# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Blender adapter for the record <-> legacy consistency validator (CAC-P3.3).

Builds the plain facts ``domain/consistency.py`` validates from real
``bpy.data.objects`` - a merged ``RecordSet`` of every record-backed object,
a fresh ``LegacyScene`` (the same oracle P1.3/P3.2 already use, via
``legacy_views.read_scene``) and a ``GeometryFact`` of live world-space
polygon/ring coordinates, keyed exactly the way ``usecases.create_feature``
already matches them (``gml_polygon_id`` material, ``opening_link`` for
rings). Read-only: nothing here writes to Blender or to a record.
"""
from __future__ import annotations

from ...object_helpers import face_material
from ...ring_opening_sync import wall_interior_ring_faces
from ..domain import consistency
from ..domain.records import RecordSet
from . import flag, legacy_views, registry, storage


def _all_objects(objects):
    if objects is not None:
        return list(objects)
    import bpy

    return list(bpy.data.objects)


def _polygon_world_coords(obj):
    """``{polygon_id: world exterior loop}`` for every non-interior face material of ``obj``.

    Mirrors ``usecases.py::_exterior_face_world_coords``'s identity
    convention (first face whose material's ``gml_polygon_id`` matches),
    generalised to every polygon id the object's faces carry at once.
    """
    mesh = obj.data
    out = {}
    if mesh is None:
        return out
    mw = obj.matrix_world
    for poly in mesh.polygons:
        mat = face_material(mesh, poly)
        if mat is None or mat.get("Interior"):
            continue
        polygon_id = str(mat.get("gml_polygon_id") or "").strip()
        if not polygon_id or polygon_id in out:
            continue
        out[polygon_id] = tuple(tuple(mw @ mesh.vertices[vi].co) for vi in poly.vertices)
    return out


def geometry_fact(objects) -> consistency.GeometryFact:
    """Live ``GeometryFact`` over every mesh object in ``objects``."""
    polygons, rings, generic_by_polygon = {}, {}, {}
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        polygons.update(_polygon_world_coords(obj))
        mesh = obj.data
        for index, verts, link in wall_interior_ring_faces(obj):
            coords = tuple(tuple(v) for v in verts)
            if link:
                rings[link] = coords
                continue
            # No opening_link (CAC-P6.1a): an ordinary interior ring (a
            # Roof hole, say), not a Window/Door pairing. Its own polygon
            # material carries ExteriorPolyId (old Cut Openings display path (removed in B17); was ops/cut_openings_display.py::
            # _material_poly_key already relies on this), the only live
            # identity available for it - matched to a RingRecord
            # positionally by face order, not by key.
            mat = face_material(mesh, mesh.polygons[index])
            host_polygon_id = str(mat.get("ExteriorPolyId") or "").strip() if mat is not None else ""
            if host_polygon_id:
                generic_by_polygon.setdefault(host_polygon_id, []).append((index, coords))
    generic_rings = {
        polygon_id: tuple(coords for _index, coords in sorted(faces))
        for polygon_id, faces in generic_by_polygon.items()
    }
    return consistency.GeometryFact(polygons=polygons, rings=rings, generic_rings=generic_rings)


def _legacy_fact(gml_id, legacy_scene):
    feature = legacy_scene.records.feature(gml_id)
    if feature is None:
        return consistency.LegacyFact(exists=False)
    return consistency.LegacyFact(exists=True, feature=feature, polygon_ids=feature.polygon_ids)


def merged_record_set(objects) -> RecordSet:
    """Every object's own stored ``RecordSet`` (ADR 0009: one document per
    object) folded into one, so cross-feature checks (ring pairing) can see
    both a Window and its host wall's record at once."""
    merged = RecordSet()
    for obj in objects:
        record_set = storage.read_record(obj)
        if record_set is None:
            continue
        for feature in record_set.features:
            merged = merged.with_feature(feature)
        for polygon in record_set.polygons:
            merged = merged.with_polygon(polygon)
    return merged


def run_validation(objects=None) -> tuple:
    """Every P3.3 finding for ``objects`` (default: the whole scene).

    Runs regardless of the experimental flag - callers that must honour
    flag-off parity (export pre-flight) check ``flag.is_enabled()``
    themselves before calling this, so a flag-off run never even builds
    these snapshots.
    """
    all_objects = _all_objects(objects)
    record_set = merged_record_set(all_objects)
    legacy_scene = legacy_views.read_scene(all_objects)
    geometry = geometry_fact(all_objects)
    registry_findings = registry.REGISTRY.findings()
    return consistency.validate_record_set(
        record_set,
        legacy_of=lambda gml_id: _legacy_fact(gml_id, legacy_scene),
        geometry_of=lambda _feature: geometry,
        registry_findings=registry_findings,
    )


def _pointer(obj):
    pointer = getattr(obj, "as_pointer", None)
    return pointer() if callable(pointer) else id(obj)


def host_validation_scope(wall, *, exclude=()) -> list:
    """Every object a record-backed commit on ``wall`` must validate (CAC-P4.10).

    Host-complete, not feature-scoped: the wall, its ancestor chain (host
    context, P3.3 ``missing_object`` checks), all its descendants (the openings
    parented under it) and every object whose ``filling_parent_surface_id``
    names it without being parented under it - plus those openings'
    descendants. ``domain/consistency.py``'s ring pairing treats the RecordSet
    it is given as complete, so a scope that drops a sibling opening reads
    that sibling's own ring as ``dangling_ring`` (AB-19).

    ``exclude``: objects left out on purpose (``initialize_duplicate``'s
    unresolved native copy, which is being replaced).
    """
    from . import guards  # lazy, like the other callers of this adapter

    found = {}
    for obj in guards.touched_objects([wall]):
        found.setdefault(_pointer(obj), obj)
    wall_id = str(wall.get("gml_id") or "").strip()
    if wall_id:
        for obj in _all_objects(None):
            if str(obj.get(guards.HOST_ID_KEY) or "").strip() != wall_id:
                continue
            for member in guards.touched_objects([obj]):
                found.setdefault(_pointer(member), member)
    skipped = {_pointer(obj) for obj in exclude or () if obj is not None}
    return [obj for key, obj in found.items() if key not in skipped]


def host_closure_findings(wall, wall_gml_id="", *, exclude=(), tolerate_duplicate_of="") -> tuple:
    """P3.3 findings that concern ``wall``'s closure (CAC-P4.10).

    Runs ``run_validation`` over ``host_validation_scope(wall)`` and keeps the
    findings about the wall itself, about any record-backed opening whose
    ``host_id`` is the wall, and scene-wide ones (``gml_id == ""``). Findings
    about unrelated features (the registry reports scene-wide) are dropped so
    they cannot block an edit of this wall.

    ``tolerate_duplicate_of``: ignore ``duplicate_gml_id``/``duplicate_record``
    for that one id - the collision ``initialize_duplicate`` is resolving.
    """
    wall_gml_id = wall_gml_id or str(wall.get("gml_id") or "").strip()
    scope = host_validation_scope(wall, exclude=exclude)
    records = merged_record_set(scope)
    relevant = {wall_gml_id} | {f.gml_id for f in records.features if f.host_id == wall_gml_id}
    duplicate_codes = (consistency.DUPLICATE_GML_ID, consistency.DUPLICATE_RECORD)
    return tuple(
        f for f in run_validation(scope)
        if (not f.gml_id or f.gml_id in relevant)
        and not (tolerate_duplicate_of and f.gml_id == tolerate_duplicate_of and f.code in duplicate_codes)
    )


def export_preflight(objects=None, *, report=None) -> bool:
    """Run the P3.3 validator before export; ``True`` means export must be blocked.

    A no-op (returns ``False``, nothing built or printed) when the
    experimental flag is off (C6: flag-off export is unchanged). ``report``,
    when given, is called as ``report({'ERROR'|'WARNING'|'INFO'}, text)``
    (a Blender operator's ``self.report``) once per finding.

    CAC-P4.5 (D-7b): on top of P3.3's own blocking findings, any record-backed
    object still carrying uncommitted native-transform drift (``coords_stale``
    - unchanged, still a WARNING in the loop above; P3.3's own severity
    classification is not touched) hard-refuses export here. This is the
    *only* place that escalates it, and it always recomputes fresh from
    ``findings`` rather than trusting ``transform_drift``'s cache (see that
    module's docstring) - export must never be wrong because a handler
    happened not to have run. Never auto-commits: the report only names the
    drifted objects and points at ``cgml.commit_transform_drift``.
    """
    if not flag.is_enabled():
        return False
    findings = run_validation(objects)
    for finding in findings:
        level = "ERROR" if finding.severity == consistency.ERROR else "WARNING"
        text = f"[CityGML][CAC-P3.3] {finding.code} ({finding.gml_id or 'scene'}): {finding.message}"
        print(text)
        if report is not None:
            report({level}, text)
    blocked = consistency.is_blocking(findings)

    drifted = sorted({f.gml_id for f in findings if f.code == consistency.COORDS_STALE and f.gml_id})
    if drifted:
        blocked = True
        names = ", ".join(
            f"{gml_id} ({obj.name})" if (obj := registry.resolve(gml_id)) is not None else gml_id
            for gml_id in drifted
        )
        text = (
            f"[CityGML][CAC-P4.5] export blocked: uncommitted transform drift on {names} - "
            "run cgml.commit_transform_drift before exporting"
        )
        print(text)
        if report is not None:
            report({"ERROR"}, text)

    if blocked and report is not None:
        report({"ERROR"}, "[CityGML][CAC-P3.3] export blocked: record/legacy consistency errors above")
    return blocked
