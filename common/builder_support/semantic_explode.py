# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Explode a flat CityGML Building/BuildingPart mesh into real
WallSurface/RoofSurface/GroundSurface/... objects with Window/Door openings
parented under their owning surface (ticket 06 of
docs/agent/semantic-hierarchy for CityGML 3, ticket 10 for CityGML 2 - both
importers call the same function here).

Called only when the `use_semantic_objects` import option is on (default
off - see ``__init__.py``'s ``CGML3_Runtime``). With the option off, nothing
in this module runs and import behaves exactly as it does today.

No new XML parsing happens here. By the time this runs, every polygon's
owning surface is already fully resolved and sitting on that polygon's
Blender material as custom properties (``io/reader/materials.py:
apply_materials_uvs``):

- ``con_surface_id`` - the owning surface's own gml:id (WallSurface,
  RoofSurface, GroundSurface, or - for an opening's own faces - the
  WindowSurface/DoorSurface's own id).
- ``SurfaceTyp`` - that surface's feature type, verbatim from the source.
- ``filling_parent_surface_id`` - only present on an opening's faces; the
  WallSurface gml:id it fills.

Grouping every face by ``con_surface_id`` is therefore sufficient to
recover the whole hierarchy, including rings: an interior ring (a wall's
window cutout, a roof's chimney penetration, a window's own frame ring)
is stored on the same source ``gml:Polygon`` as its exterior counterpart
and so already carries the *same* ``con_surface_id`` - it lands in the
same group as its owner with no ring-specific logic at all. Verified
against the Railway LoD3 fixture: grouping by ``con_surface_id`` yields
exactly 9 WallSurface / 3 RoofSurface / 1 GroundSurface / 15 WindowSurface
/ 6 DoorSurface groups, matching the source file exactly.

Geometry must live in exactly one place (README decision 1): faces are
*moved* out of the Building's own mesh into the new child objects, never
copied alongside them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy
import bmesh

from ...shared.semantic_tree import BOUNDARY_SURFACE_TYPES, OPENING_FEATURE_TYPES
from ...shared.schema_version import is_citygml2_0

_MAX_NAME_LEN = 63  # Blender object/mesh datablock name limit

# Verbatim source-element templates per schema version (ticket 10), for
# provenance/debugging only - never rendered by the exporter (README §2.1).
# "{type}" is filled with the surface's already-normalized SurfaceTyp
# (e.g. "WallSurface", "Window" for v2 / "WindowSurface" for v3), which
# matches the XML local name verbatim for both schemas.
_SOURCE_ELEMENT_TEMPLATES = {
    "2.0": {"boundary": "bldg:boundedBy/bldg:{type}", "opening": "bldg:opening/bldg:{type}"},
    "3.0": {"boundary": "boundary/con:{type}", "opening": "con:fillingSurface/con:{type}"},
}


@dataclass
class _SurfaceGroup:
    """One explode group: every face sharing a surface's ``con_surface_id``
    (or, lacking one, the fallback key built from its polygon id) - the
    grouping unit ticket 06's docstring above describes."""

    surface_type: str
    gml_id: str
    filling_parent: str
    faces: list[int] = field(default_factory=list)


def _object_name(surface_type: str, gml_id: str, fallback_key: str) -> str:
    base = gml_id or fallback_key
    name = f"{surface_type}_{base}" if surface_type else base
    return name[:_MAX_NAME_LEN]


def _compact_material_slots(mesh) -> None:
    """Drop material slots unused by the (already face-filtered) mesh,
    remapping ``polygon.material_index`` to the compacted list.

    Not purely cosmetic: ticket 07's export reads every per-polygon fact
    (``gml_polygon_id``, ``con_surface_id``, ring ids, ...) off the
    material a face's ``material_index`` points at, so a wrong index here
    silently collapses every face of an exploded object onto one
    material's identity.

    ``mesh.materials.clear()`` resets every polygon's ``material_index``
    to 0 as a side effect (an out-of-range index would otherwise dangle
    once the list is cleared) - so the original, pre-clear indices must be
    snapshotted into a plain list first and remapped from that snapshot,
    not re-read from the (by-then-zeroed) polygons.
    """
    original_indices = [p.material_index for p in mesh.polygons]
    used = sorted(set(original_indices))
    if not used:
        return
    remap = {old: new for new, old in enumerate(used)}
    kept = [mesh.materials[i] for i in used]
    mesh.materials.clear()
    for m in kept:
        mesh.materials.append(m)
    for p, orig_idx in zip(mesh.polygons, original_indices):
        p.material_index = remap[orig_idx]


def _extract_face_subset(source_obj, face_indices, name):
    """Return a new, not-yet-linked object holding only ``face_indices`` of
    ``source_obj``'s mesh (by position, matching ``mesh.polygons`` order).

    Implemented as copy-then-prune rather than rebuilding vertices/UVs by
    hand: ``Mesh.copy()`` duplicates the full datablock (verts, loops, UV
    layers, per-face material assignment) referencing the *same* Material
    datablocks, then ``bmesh.ops.delete`` removes everything outside the
    subset. This is what guarantees exact geometry/UV/appearance fidelity
    for the kept faces with no reconstruction logic to get subtly wrong.
    """
    new_mesh = source_obj.data.copy()
    new_mesh.name = name

    bm = bmesh.new()
    bm.from_mesh(new_mesh)
    faces = list(bm.faces)  # positional order == mesh.polygons order
    keep = set(face_indices)
    to_delete = [f for i, f in enumerate(faces) if i not in keep]
    bmesh.ops.delete(bm, geom=to_delete, context='FACES')
    bm.to_mesh(new_mesh)
    bm.free()
    new_mesh.update()

    _compact_material_slots(new_mesh)

    new_obj = bpy.data.objects.new(name, new_mesh)
    new_obj.matrix_world = source_obj.matrix_world.copy()
    return new_obj


def explode_building_into_semantic_objects(obj, collection, schema_version: str = "3.0") -> bool:
    """Split ``obj``'s flat mesh into boundary-surface/opening child objects.

    ``obj`` must be a flat (non-hierarchical, non-mesh-instance) Building or
    BuildingPart MESH object whose materials/UVs have already been assigned
    (i.e. called after ``apply_materials_uvs``). ``collection`` is the
    Blender collection new child objects are linked into - the same one
    ``obj`` itself lives in. ``schema_version`` ("2.0" or "3.0") is only used
    to stamp the new objects' ``cgml_schema_version``/``cgml_source_element``
    (ticket 10) - the grouping logic itself is schema-neutral, keyed purely
    off material custom properties already written identically by both
    readers' ``apply_materials_uvs``.

    Returns True and marks ``obj["cgml_hierarchy_model"] = "semantic_objects"``
    if anything was exploded; returns False (mesh left untouched) if there
    was nothing recognizable to split, e.g. an empty mesh or one whose
    materials carry no ``SurfaceTyp``.
    """
    # Preserve the historical fallback: only known CityGML 2 input uses the
    # v2 template; unrecognized versions continue to receive the v3 template.
    source_elements = (
        _SOURCE_ELEMENT_TEMPLATES["2.0"]
        if is_citygml2_0(schema_version)
        else _SOURCE_ELEMENT_TEMPLATES["3.0"]
    )
    mesh = obj.data
    if mesh is None or not mesh.polygons:
        return False

    materials = mesh.materials

    groups: dict[str, _SurfaceGroup] = {}
    for poly in mesh.polygons:
        mat_index = poly.material_index
        mat = materials[mat_index] if 0 <= mat_index < len(materials) else None
        if mat is None:
            continue
        surface_type = str(mat.get("SurfaceTyp") or mat.get("surface_type") or "").strip()
        if not surface_type:
            continue
        con_surface_id = str(mat.get("con_surface_id") or "").strip()
        filling_parent = str(
            mat.get("filling_parent_surface_id") or mat.get("opening_surface_id") or ""
        ).strip()
        # Fallback key for a surface with no gml:id of its own: groups by
        # polygon id instead, which means each of its rings would land in
        # its own object rather than staying together. Not exercised by
        # any known fixture (every thematic surface there carries a
        # gml:id) - a conservative degradation, not a crash, if it ever is.
        key = con_surface_id or f"{surface_type}::{mat.get('gml_polygon_id', poly.index)}"
        grp = groups.setdefault(
            key,
            _SurfaceGroup(
                surface_type=surface_type,
                gml_id=con_surface_id,
                filling_parent=filling_parent,
            ),
        )
        grp.faces.append(poly.index)

    if not groups:
        return False

    def _is_opening(surface_type: str) -> bool:
        return surface_type.lower() in OPENING_FEATURE_TYPES

    def _is_boundary_surface(surface_type: str) -> bool:
        return surface_type.lower() in BOUNDARY_SURFACE_TYPES

    surface_groups = []
    opening_groups = []
    classified = set()
    for key, grp in groups.items():
        if _is_opening(grp.surface_type):
            opening_groups.append((key, grp))
            classified.update(grp.faces)
        elif _is_boundary_surface(grp.surface_type):
            surface_groups.append((key, grp))
            classified.update(grp.faces)
        # else: unrecognized surface type - leave its faces on the Building
        # rather than guessing where they belong.

    if not surface_groups and not opening_groups:
        return False

    surface_objs_by_gml_id = {}

    for key, grp in surface_groups:
        name = _object_name(grp.surface_type, grp.gml_id, key)
        new_obj = _extract_face_subset(obj, grp.faces, name)
        new_obj["cgml3_feature"] = grp.surface_type
        new_obj["gml_id"] = grp.gml_id or key
        new_obj["cgml_schema_version"] = schema_version
        new_obj["cgml_source_element"] = source_elements["boundary"].format(type=grp.surface_type)
        collection.objects.link(new_obj)
        new_obj.parent = obj
        # B76: the faces are in obj's local space; a copied world matrix applied obj's transform twice
        new_obj.matrix_basis.identity()
        if grp.gml_id:
            surface_objs_by_gml_id[grp.gml_id] = new_obj

    for key, grp in opening_groups:
        name = _object_name(grp.surface_type, grp.gml_id, key)
        new_obj = _extract_face_subset(obj, grp.faces, name)
        new_obj["cgml3_feature"] = grp.surface_type
        new_obj["gml_id"] = grp.gml_id or key
        new_obj["structure_part"] = "opening"
        new_obj["cgml_schema_version"] = schema_version
        new_obj["cgml_source_element"] = source_elements["opening"].format(type=grp.surface_type)
        if grp.filling_parent:
            new_obj["filling_parent_surface_id"] = grp.filling_parent
            new_obj["opening_surface_id"] = grp.filling_parent
        collection.objects.link(new_obj)
        parent_obj = surface_objs_by_gml_id.get(grp.filling_parent)
        new_obj.parent = parent_obj if parent_obj is not None else obj
        new_obj.matrix_basis.identity()  # B76: parents sit at obj's frame, the faces are obj-local

    # Geometry must live in exactly one place: remove the exploded faces
    # from the Building's own mesh now that every one of them owns a new
    # child object. Any face whose surface type went unrecognized above
    # stays behind, untouched.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    faces = list(bm.faces)
    to_delete = [f for i, f in enumerate(faces) if i in classified]
    bmesh.ops.delete(bm, geom=to_delete, context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    _compact_material_slots(mesh)

    obj["cgml_hierarchy_model"] = "semantic_objects"
    return True
