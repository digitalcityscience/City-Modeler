# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Shared Blender builder: scene from the CityGML IR (T11d, part 1).

``shared/citygml_reader.py`` reads a file into the IR, ``shared/builder_payload.py``
turns the IR into per-object payloads (pure Python), and this module is the only
part that touches ``bpy``: collections, meshes, materials, custom properties and
parenting. Materials and UVs still come from ``apply_materials_uvs`` of the
reader the file belongs to, and the semantic split of a Building into surface
objects from ``explode_building_into_semantic_objects``.

This is the only importer. Content outside the add-on's scope (interior spaces, Tunnel,
WaterBody, Relief, ... see ``builder_payload.skipped_features``) is skipped and reported with
``builder_payload.scope_notice``; a file above ``LOAD_ALL_MAX_MB`` is not imported at all (Browse
first, or 3DCityDB v5, does that job).
"""

from __future__ import annotations

import os
from pathlib import Path

import bpy

from ..shared import builder_payload as bp
from ..shared import georef as gr
from .builder_support.v3.importer import _safe_store_json_chunks
from ..shared.citygml_reader import read_document
from ..shared.load_limits import (  # noqa: F401  (re-exported for callers)
    LOAD_ALL_MAX_FEATURES, LOAD_ALL_MAX_MB, ImportNotSupported, ImportTooLarge, check_feature_count, size_notice,
)
from ..shared.semantic_appearance import (
    AppearanceDiagnosticsCounters,
    DeduplicatedWarnings,
    new_unknown_subtype_summary,
)

# The builder reads a feature the way the IR holds it, like Bonsai reads an IFC entity:
# a feature's own values only, an LoD filter that really filters, the 2.0 address read.


def _scene_options(context):
    return context.scene.cgml3 if not isinstance(context, dict) else bpy.context.scene.cgml3


def import_filters(v, opts) -> bp.ImportFilters:
    """The scene's import filters, read the way the old importer reads them."""
    from .filter_utils import resolve_scene_bbox_filter
    gml_ids = None
    if getattr(opts, "use_gmlid_filter", False):
        text = str(opts.gmlid_filter).strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        gml_ids = v.imp._parse_gml_id_filter(text)
    bbox = resolve_scene_bbox_filter(opts)
    return bp.ImportFilters(gml_ids=gml_ids, feature_types=bp.feature_type_set(opts, v.schema),
                            lods=bp.lod_set(opts), bbox=tuple(bbox) if bbox else None)


def shell_polygon_ids(root) -> set:
    """gml:ids of the polygons below a ``gml:Solid`` whose ``gml:exterior`` holds a ``gml:Shell``;
    the CityGML 3 importer flags their materials ``in_shell``."""
    ids = set()
    for ns in ("http://www.opengis.net/gml/3.2", "http://www.opengis.net/gml"):
        for solid in root.iter(f"{{{ns}}}Solid"):
            if solid.find(f".//{{{ns}}}exterior//{{{ns}}}Shell") is None:
                continue
            for member in solid.iter(f"{{{ns}}}surfaceMember"):
                for el in member.iter():
                    if isinstance(el.tag, str) and el.tag.rpartition("}")[2] in ("Polygon", "Triangle", "Rectangle"):
                        gid = el.get(f"{{{ns}}}id")
                        if gid:
                            ids.add(gid)
    return ids


class _Versioned:
    """What differs between the two importers the builder stands in for."""

    def __init__(self, schema: str):
        self.schema = schema
        if schema == "2.0":
            from .builder_support.v2 import importer as imp
            from .builder_support.v2 import appearance as app
            from .builder_support.v2.materials import apply_materials_uvs
            self.collection_prefix = "CityGML2"
        else:
            from .builder_support.v3 import importer as imp
            from .builder_support.v3 import appearance as app
            from .builder_support.v3.materials import apply_materials_uvs
            self.collection_prefix = "CityGML3"
        self.imp, self.app = imp, app
        self.apply_materials_uvs = apply_materials_uvs


def _prepare_world(v: _Versioned, context, root):
    """World CRS/origin as the importer sets them; returns (ref_origin, default_srs)."""
    imp = v.imp
    default_srs = root.get("srsName")
    env_srs, _lower = imp._get_envelope_srs_and_corners(root)
    sc = context.scene if not isinstance(context, dict) else bpy.context.scene
    is_local = bool(getattr(sc.cgml3, "import_local", False))
    if not is_local:
        try:
            z_epsg = imp._extract_vertical_epsg(env_srs or default_srs or "")
            if z_epsg:
                gr.georef_world()[gr.Z_EPSG] = z_epsg
        except Exception:
            pass
    # the georeference lives only in bpy.data.worlds["World"] (every reader uses it), never in the
    # scene's own world or in other add-ons' scene keys; a world-less scene gets "World"
    world = gr.georef_world(sc)
    file_origin, file_crs = imp._determine_file_origin_and_crs(root)
    if not is_local:
        # a CRS without an origin: a building drawn in an empty scene (_ensure_crs) sets only the CRS
        if (not gr.georef_in_use(world) or (gr.CRS not in world.keys()) or world[gr.CRS] == "Unknown CRS"
                or not gr.same_crs(world[gr.CRS], file_crs)
                or gr.X_ORIGIN not in world.keys() or gr.Y_ORIGIN not in world.keys()):
            world[gr.CRS] = file_crs
            gr.set_world_float(world, gr.X_ORIGIN, file_origin[0])
            gr.set_world_float(world, gr.Y_ORIGIN, file_origin[1])
            gr.set_world_float(world, gr.Z_ORIGIN, file_origin[2])
        if gr.Z_ORIGIN not in world.keys():
            gr.set_world_float(world, gr.Z_ORIGIN, file_origin[2])
    if is_local:
        return tuple(float(c) for c in file_origin), default_srs
    return (float(world[gr.X_ORIGIN]), float(world[gr.Y_ORIGIN]),
            float(world.get(gr.Z_ORIGIN, file_origin[2]))), default_srs


class _Collections:
    def __init__(self, imp, context, name):
        self.imp = imp
        self.root = imp.ensure_collection(context, name)
        self._by_type = {}

    def of(self, feature_type: str):
        name = feature_type or "UnknownType"
        sub = self._by_type.get(name)
        if sub is None:
            sub = next((c for c in self.root.children if c.name == name), None)
            if sub is None:
                sub = bpy.data.collections.new(name)
                self.root.children.link(sub)
            self._by_type[name] = sub
        return sub


def build_feature(payload: bp.FeaturePayload, ctx: "_BuildContext"):
    """One city object -> one mesh object (plus the surface objects of a Building)."""
    geom = payload.geometry
    if not geom.faces:
        if payload.feature_type == "Building" and payload.gml_id in getattr(ctx, "part_owners", ()):
            return _build_part_container(payload, ctx)
        return None
    v = ctx.v
    feat_id = payload.gml_id

    implicit = payload.implicit
    geom_hash = None
    if ctx.geometry_cache and geom.verts and geom.faces and implicit is None:
        geom_hash = v.imp._compute_geometry_hash(geom.verts, geom.faces)
    target = ctx.collections.of(payload.feature_type)
    if implicit is not None and implicit.fingerprint in ctx.implicit_meshes:
        mesh = ctx.implicit_meshes[implicit.fingerprint]
        obj = bpy.data.objects.new(feat_id, mesh)
        is_mesh_instance = True
    elif geom_hash and geom_hash in ctx.geometry_cache:
        mesh = ctx.geometry_cache[geom_hash]
        obj = bpy.data.objects.new(feat_id, mesh)
        is_mesh_instance = True
    else:
        mesh = bpy.data.meshes.new(feat_id)
        mesh.from_pydata(geom.verts, [], geom.faces)
        obj = bpy.data.objects.new(mesh.name, mesh)
        is_mesh_instance = False
        if geom_hash and len(geom.faces) > 5:
            ctx.geometry_cache[geom_hash] = mesh
        if implicit is not None:
            ctx.implicit_meshes[implicit.fingerprint] = mesh
            mesh["ImplicitGeometry"] = True
    target.objects.link(obj)
    ctx.id_to_obj[feat_id] = obj

    crs = ctx.feature_crs(feat_id)
    if crs and crs != "Unknown CRS":
        obj["CRS"] = crs

    if not is_mesh_instance:
        if v.schema == "2.0":
            try:
                mesh["cgml3_uv_start_by_ring"] = dict(ctx.uv_start_by_ring) \
                    if isinstance(ctx.uv_start_by_ring, dict) else {}
            except Exception:
                pass
        extra = {}
        if v.schema == "3.0":
            extra = dict(poly_also_in_solid={},
                         filling_parent_surface_id_by_poly=geom.filling_parent_surface_id_by_poly)
        counters, warnings = v.apply_materials_uvs(
            obj, mesh, geom.surf_labels, ctx.matcolor_by_surface_id, ctx.ptex_by_ring,
            ctx.gtex_by_poly, ctx.poly_to_image, ctx.path, ptex_by_poly=ctx.ptex_by_poly,
            surface_attrs_by_poly=geom.surf_attrs_by_poly, surface_id_by_poly=geom.surface_id_by_poly,
            multisurface_id_by_poly=geom.multisurface_id_by_poly,
            compositesurface_id_by_poly=geom.compositesurface_id_by_poly,
            installation_semantic_type=payload.installation_type,
            installation_unknown_subtype=payload.installation_unknown_subtype,
            import_appearance_enabled=bool(ctx.import_appearance), **extra,
        )
        ctx.diag_totals.merge(counters)
        for w in warnings:
            ctx.diag_warnings.add(w)
        if payload.installation_unknown_subtype and payload.installation_unknown_value:
            ctx.unknown_subtypes.add(payload.installation_unknown_value)
        if getattr(ctx.options, "mesh_validate", False):
            mesh.validate(verbose=False)

    obj["cgml3_feature"] = payload.feature_type
    obj["gml_id"] = feat_id
    if implicit is not None:
        _place_implicit(obj, implicit, ctx)

    if payload.feature_type in ("Building", "BuildingPart") and not is_mesh_instance:
        if bool(getattr(ctx.options, "use_semantic_objects", False)):
            from .builder_support.semantic_explode import explode_building_into_semantic_objects
            if v.schema == "2.0":
                explode_building_into_semantic_objects(obj, target, schema_version="2.0")
            elif explode_building_into_semantic_objects(obj, target):
                _give_openings_depth(obj)

    _apply_payload_attributes(obj, payload)

    if payload.installation_type is not None:
        obj["cgml_semantic_type"] = payload.installation_type
        obj["cgml_subtype_origin"] = payload.installation_origin
        obj["cgml_schema_version"] = v.schema
        obj["cgml_source_role"] = payload.source_role
        obj["cgml_source_element"] = payload.source_element
    return obj


def _apply_payload_attributes(obj, payload):
    if payload.display_name:
        obj.name = payload.display_name
    for key, value in payload.properties.items():
        try:
            obj[key] = value
        except (TypeError, ValueError):
            obj[key] = str(value)
    if payload.generic_attributes:
        obj["cgml3_generic_attributes"] = dict(payload.generic_attributes)
        _safe_store_json_chunks(obj, "cgml3_generic_attributes_json", payload.generic_attributes)


def _build_part_container(payload, ctx):
    """A Building whose geometry is all in its BuildingParts (B09): an empty
    object that keeps its id and attributes and becomes the parts' parent."""
    feat_id = payload.gml_id
    obj = bpy.data.objects.new(feat_id, bpy.data.meshes.new(feat_id))
    ctx.collections.of(payload.feature_type).objects.link(obj)
    ctx.id_to_obj[feat_id] = obj
    crs = ctx.feature_crs(feat_id)
    if crs and crs != "Unknown CRS":
        obj["CRS"] = crs
    obj["cgml3_feature"] = payload.feature_type
    obj["gml_id"] = feat_id
    _apply_payload_attributes(obj, payload)
    return obj


class _BuildContext:
    pass


def _place_implicit(obj, implicit: bp.ImplicitPlacement, ctx):
    """An implicit instance shares its template's mesh and carries the transform on the object."""
    from mathutils import Matrix
    obj["cgml3_transformationMatrix"] = implicit.matrix_text
    if ctx.v.schema == "3.0" and implicit.lod is not None:
        obj["cgml3_implicit_lod"] = implicit.lod
    obj.matrix_world = Matrix(implicit.matrix)
    obj["cgml3_implicit_template_id"] = implicit.template_id


def _parent_keep_world(child_obj, parent_obj):
    """``child.parent = parent`` keeps the child's local matrix, so a child placed with ``matrix_world``
    (an implicit instance) would move if the parent has a matrix of its own."""
    child_obj.parent = parent_obj
    parent_world = parent_obj.matrix_world
    if parent_world != parent_world.Identity(4):
        child_obj.matrix_parent_inverse = parent_world.inverted_safe()


def _give_openings_depth(building_obj):
    """CityGML 3 importer: every flat opening the semantic split produced gets a 4 cm depth."""
    from ..shared.facade_placement import apply_synthetic_opening_depth
    for opening in building_obj.children_recursive:
        if opening.type != 'MESH' or opening.get("structure_part") != "opening":
            continue
        mesh = opening.data
        if mesh is None or not mesh.polygons:
            continue
        normal = (opening.matrix_world.to_3x3() @ mesh.polygons[0].normal).normalized()
        apply_synthetic_opening_depth(opening, normal, str(opening.get("cgml3_feature") or ""))


def import_with_builder(filepath: str, context, *, schema: str, import_appearance: bool = True,
                        max_mb: float | None = None, max_features: int | None = None,
                        data: bytes | None = None) -> list:
    """Build ``filepath`` from the IR. Returns the notices for the user (large file, skipped content).
    ``data``: the content of ``filepath`` when the caller already holds it; the file is not opened then.
    Raises ``ImportTooLarge`` (too many MB: nothing is read; too many features: nothing is built) or
    ``ImportNotSupported`` (nothing is built)."""
    try:
        from lxml import etree as ET
    except Exception:
        import xml.etree.ElementTree as ET

    v = _Versioned(schema)
    opts = _scene_options(context)

    # the 2.0 importer reads no filters when it is called with an options dict
    filters = bp.ImportFilters() if (schema == "2.0" and isinstance(context, dict)) else import_filters(v, opts)
    try:
        threshold = float(opts.import_streaming_threshold_mb)
    except Exception:
        threshold = 30.0
    notices = []
    size = len(data) if data is not None else os.path.getsize(filepath)
    size_text = size_notice(size / (1024 * 1024), threshold,
                            LOAD_ALL_MAX_MB if max_mb is None else max_mb)
    if size_text:
        print("[CityGML Builder] " + size_text)
        notices.append(size_text)

    try:
        parser = ET.XMLParser(huge_tree=True, remove_blank_text=False)
        root = (ET.fromstring(data, parser=parser) if data is not None
                else ET.parse(filepath, parser=parser).getroot())
    except TypeError:
        root = ET.fromstring(data) if data is not None else ET.parse(filepath).getroot()

    warnings, report = [], []
    roots = read_document(root, warnings=warnings, report=report, upgrade=False,
                          envelopes=bool(filters.bbox))
    skip, reasons = bp.skipped_features(roots, schema, filters)
    # before the scene changes (world, collections); a child kept below a filtered-out root is not counted
    check_feature_count(sum(1 for r in roots if id(r) not in skip and filters.passes(r)),
                        LOAD_ALL_MAX_FEATURES if max_features is None else max_features)
    if reasons:
        notice = bp.scope_notice(reasons)
        print("[CityGML Builder] " + notice)
        notices.append(notice)
    sc = context.scene if not isinstance(context, dict) else bpy.context.scene
    if not getattr(sc.cgml3, "import_local", False):
        gr.check_crs(v.imp._determine_file_origin_and_crs(root)[1])

    ctx = _BuildContext()
    ctx.v, ctx.context, ctx.options, ctx.path = v, context, opts, filepath
    ctx.import_appearance = import_appearance
    ctx.geometry_cache, ctx.id_to_obj = {}, {}
    ctx.implicit_meshes = {}
    ctx.diag_totals = AppearanceDiagnosticsCounters()
    ctx.diag_warnings = DeduplicatedWarnings()
    ctx.unknown_subtypes = new_unknown_subtype_summary()

    app = v.app
    has_appearance = False
    if import_appearance:
        has_appearance = any(isinstance(el.tag, str) and el.tag.endswith("Appearance")
                             for el in root.iter())
    if import_appearance and has_appearance:
        ctx.matcolor_by_surface_id = app.parse_x3d_materials(root)
        textures = app.parse_parameterized_textures(root, filepath)
        ctx.ptex_by_ring, ctx.poly_to_image, ctx.ptex_by_poly = textures[:3]
        ctx.uv_start_by_ring = textures[3] if len(textures) > 3 else {}
        ctx.gtex_by_poly = app.parse_georeferenced_textures(root, filepath)
    else:
        ctx.matcolor_by_surface_id, ctx.ptex_by_ring, ctx.poly_to_image = {}, {}, {}
        ctx.ptex_by_poly, ctx.uv_start_by_ring, ctx.gtex_by_poly = {}, {}, {}
    if import_appearance:
        try:
            (Path(filepath).parent / "Appearance").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    ctx.collections = _Collections(v.imp, context, f"{v.collection_prefix}:{Path(filepath).stem}")
    ref_origin, default_srs = _prepare_world(v, context, root)

    from ..shared.citygml_reader import document_ids
    elements = document_ids(root)

    def feature_crs(feat_id):
        el = elements.get(feat_id)
        if el is None:
            return "Unknown CRS"
        try:
            return v.imp._infer_feature_crs(el)
        except Exception:
            return "Unknown CRS"

    ctx.feature_crs = feature_crs

    plan = bp.plan_document(roots, ref_origin, default_srs or "", schema=schema,
                            filters=filters,
                            shell_ids=shell_polygon_ids(root) if schema == "3.0" else None,
                            skip=skip)
    types = {p.gml_id: p.feature_type for p in plan.features}
    ctx.part_owners = {parent for parent, child, _kind in plan.edges if types.get(child) == "BuildingPart"}
    built = 0
    for payload in plan.features:
        if build_feature(payload, ctx) is not None:
            built += 1

    # a feature without geometry (a Section, a TrafficSpace) builds no object: its children hang below
    # the nearest ancestor that has one (the old importer leaves them unparented)
    owner_of = {child_id: parent_id for parent_id, child_id, _kind in plan.edges}
    for parent_id, child_id, _kind in plan.edges:
        while parent_id not in ctx.id_to_obj and parent_id in owner_of:
            parent_id = owner_of[parent_id]
        parent_obj, child_obj = ctx.id_to_obj.get(parent_id), ctx.id_to_obj.get(child_id)
        if parent_obj is None or child_obj is None or parent_obj is child_obj:
            continue
        _parent_keep_world(child_obj, parent_obj)
        child_obj["cgml_parent_id"] = parent_id
    v.imp._disable_inline_subfeature_collections(ctx.collections.root)

    if not isinstance(context, dict) and hasattr(context.scene, "cgml3"):
        context.scene.cgml3.last_import_total = plan.total
        context.scene.cgml3.last_import_imported = built
        context.scene.cgml3.last_import_filtered = plan.total - len(plan.features)
    v.imp._finalize_appearance_diagnostics(
        ctx.diag_totals, ctx.diag_warnings, ctx.unknown_subtypes, label=f"CityGML{schema[0]}")
    print(f"[CityGML Builder] built {built} feature(s) from the IR")
    if not built and skip:
        raise ImportNotSupported(bp.scope_notice(reasons))
    return notices
