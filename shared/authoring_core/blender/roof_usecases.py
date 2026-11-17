# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Delete + duplicate/init lifecycle for a record-backed RoofSurface (CAC-P6.4).

Mirrors ``usecases.py``'s Window delete/duplicate shape, deliberately not
reused unchanged - a RoofSurface has no host wall, no ``opening_link``, no
ring to re-cut (``roof_edit_session.py``'s own docstring already makes this
call for the edit/commit seam; this module makes the same call for delete
and duplicate):

- ``delete_roof`` removes the Roof's own object, record (same object,
  ADR 0009) and derived display - no sibling record to rewrite, unlike
  ``usecases.delete_feature``'s host-wall ring removal.
- ``initialize_roof_duplicate`` turns one native Shift+D copy into an
  independent RoofSurface by minting fresh feature/polygon/ring ids
  directly onto a copy of its own already-committed geometry
  (``domain/roof_lifecycle.py::plan_roof_duplicate``, pure). There is no
  Roof creation/placement kernel to re-run the way Window's duplicate reuses
  ``create_feature`` - P6.4 does not add one (scope boundary S22) - so the
  duplicate's own mesh/materials are reused in place, not replaced.

Both entry points resolve by ``gml_id`` (R2), validate before mutating
anything (R4), and mark the registry dirty on success - the same shape
``usecases.py`` already established.
"""
from __future__ import annotations

from ..domain.roof_lifecycle import ROOF_KIND, RoofLifecycleError, plan_roof_delete, plan_roof_duplicate
from . import display_regen, registry, storage

__all__ = ["RoofLifecycleError", "delete_roof", "initialize_roof_duplicate"]


def delete_roof(gml_id):
    """Delete a record-backed RoofSurface and its complete semantic feature.

    1. resolve + validate (``plan_roof_delete``) - refuses, nothing
       touched, if the object/record/kind do not match;
    2. snapshot the Roof's own display, remove it (CAC-P3b.3/P6.3 shape) -
       any failure here restores the snapshot and refuses before the
       semantic object is touched;
    3. remove the Roof object (and its mesh datablock, if now unused) -
       the same deliberately-irreversible last step
       ``usecases.delete_feature`` already treats as final: the record
       lives on the object itself (ADR 0009), so removing the object is
       what removes the record, with nothing left to roll back after it.

    Raises ``RoofLifecycleError`` on refusal. One call, one undo step, when
    driven from an operator with ``bl_options = {'REGISTER', 'UNDO'}``.
    """
    gml_id = str(gml_id or "").strip()
    roof = registry.resolve(gml_id) if gml_id else None
    if roof is None or roof.type != "MESH":
        raise RoofLifecycleError(f"{gml_id!r}: no such object (already deleted?)")

    record_set = storage.read_record(roof)
    plan_roof_delete(gml_id, record_set)  # refuses before anything below mutates

    display_before = display_regen.snapshot_single_display(roof, gml_id)
    try:
        display_regen.remove_display_object(roof, gml_id)
    except Exception as exc:
        display_regen.restore_single_display_snapshot(roof, gml_id, display_before)
        raise RoofLifecycleError(f"{gml_id!r}: delete refused: {exc}") from exc

    import bpy

    mesh = roof.data
    bpy.data.objects.remove(roof, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)

    registry.REGISTRY.mark_dirty()
    return gml_id


def _single_user_materials(mesh):
    """Give ``mesh`` its own, independent copy of every material slot it
    uses, so writing a fresh ``gml_polygon_id`` onto one never also rewrites
    the source RoofSurface's identically-named material (Blender's native
    Shift+D duplicates mesh data but, by default, leaves material slots
    linked to the exact same Material datablock as the original - CAC-P6.4
    S9/S18's "no duplicated semantic object may reuse another feature's
    identity" applies to this per-face identity channel too, not only to
    the display object)."""
    for index, material in enumerate(mesh.materials):
        if material is not None:
            mesh.materials[index] = material.copy()


def _remap_duplicate_identity_materials(mesh, old_gml_id, new_gml_id, polygon_id_map, ring_id_map):
    """Re-tag every already-single-user material's legacy identity fields
    from the source's old ids to the duplicate's new ones (CAC-P6.5).

    ``domain/legacy_reader.py::read_legacy_scene`` attributes a polygon to
    its owning feature via the material's own ``con_surface_id`` - not via
    the owning *object*'s ``gml_id`` - and an interior ring's host via
    ``ExteriorPolyId``/its own identity via ``gml_ring_id`` (or
    ``gml_polygon_id`` as that field's own fallback for a ring-only
    material). Before this fix, only ``gml_polygon_id`` was re-tagged:
    every other field still named the source, so the legacy/consistency
    view kept attributing the duplicate's own geometry back to the
    source's ``gml_id`` (via ``con_surface_id``) - the real cause behind
    every export after a Roof duplicate being blocked by a spurious
    ``polygon_ids_mismatch``/``foreign_record`` consistency finding, not a
    deliberate refusal. ``polygon_id_map``/``ring_id_map`` are
    ``{old_id: new_id}`` for this duplicate's own polygons/rings only."""
    for material in mesh.materials:
        if material is None:
            continue
        if str(material.get("con_surface_id") or "").strip() == old_gml_id:
            material["con_surface_id"] = new_gml_id
        for key in ("gml_polygon_id", "ExteriorPolyId", "gml_ring_id"):
            old_value = str(material.get(key) or "").strip()
            if not old_value:
                continue
            if old_value in polygon_id_map:
                material[key] = polygon_id_map[old_value]
            elif old_value in ring_id_map:
                material[key] = ring_id_map[old_value]


def initialize_roof_duplicate(duplicate):
    """Turn one native Shift+D RoofSurface copy into a new, independent
    semantic RoofSurface (D-4).

    Blender's native duplicate carries the source's own ``gml_id``,
    ``_cgml_record`` and (by default) shared material datablocks - all
    deliberately invalid: two objects answering to the same semantic and
    polygon identity. This call mints a fresh record
    (``domain/roof_lifecycle.py::plan_roof_duplicate``, pure - validated
    before anything here is mutated), makes the duplicate's materials
    single-user and re-tags their ``gml_polygon_id``, writes the fresh
    record and ``gml_id`` onto the duplicate, then regenerates its own,
    independent derived display (CAC-P6.3 shape) - never touching the
    source object/record/display.

    A failure after the record/``gml_id`` write restores the duplicate's
    pre-init (shared-id) record/``gml_id`` exactly - the physical duplicate
    object itself already existed before this call (Blender's own Shift+D),
    so there is no object-creation step to roll back, only this call's own
    record/identity write.

    Raises ``RoofLifecycleError`` on refusal, leaving the duplicate and the
    source exactly as they were.
    """
    if duplicate is None or getattr(duplicate, "type", None) != "MESH":
        raise RoofLifecycleError("select a duplicated record-backed RoofSurface")

    record_set = storage.read_record(duplicate)
    old_gml_id = str(duplicate.get("gml_id") or "").strip()
    feature = record_set.feature(old_gml_id) if record_set is not None and old_gml_id else None
    if feature is None or feature.kind != ROOF_KIND:
        raise RoofLifecycleError("selected object is not a record-backed RoofSurface duplicate")

    siblings = [
        obj for obj in registry.REGISTRY.get_all(old_gml_id)
        if obj is not duplicate and storage.has_record(obj)
    ]
    if len(siblings) != 1:
        raise RoofLifecycleError(
            f"'{old_gml_id}' must have exactly one original RoofSurface before initialization; "
            f"found {len(siblings)}"
        )

    # Pure planning step: fully derived and validated before anything below
    # mutates the duplicate object.
    new_record_set, new_gml_id, polygon_id_map = plan_roof_duplicate(old_gml_id, record_set)

    ring_id_map = {}
    for old_polygon, new_polygon in zip(record_set.polygons_of(old_gml_id), new_record_set.polygons):
        ring_id_map[old_polygon.exterior.ring_id] = new_polygon.exterior.ring_id
        for old_ring, new_ring in zip(old_polygon.interiors, new_polygon.interiors):
            ring_id_map[old_ring.ring_id] = new_ring.ring_id

    mesh = duplicate.data
    _single_user_materials(mesh)
    _remap_duplicate_identity_materials(mesh, old_gml_id, new_gml_id, polygon_id_map, ring_id_map)

    try:
        storage.write_record(duplicate, new_record_set)
        duplicate["gml_id"] = new_gml_id
        new_feature = new_record_set.feature(new_gml_id)
        display_regen.regenerate_or_restore_roof_display(duplicate, new_feature)
    except Exception as exc:
        storage.write_record(duplicate, record_set)
        duplicate["gml_id"] = old_gml_id
        raise RoofLifecycleError(f"{old_gml_id!r}: duplicate initialization refused: {exc}") from exc

    registry.REGISTRY.mark_dirty()
    return duplicate
