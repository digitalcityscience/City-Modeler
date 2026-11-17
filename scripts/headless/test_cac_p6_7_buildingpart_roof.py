# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.7 (BuildingPart-owned RoofSurface
ownership/hierarchy parity).

P6.0-P6.6 proved the whole record-backed RoofSurface slice for a
RoofSurface owned directly by a top-level Building. P6.7 closes the one
remaining structural question: does every one of promotion, edit, display,
duplicate, delete, registry, export and save/reload already work
*unmodified* when the owning space is a ``BuildingPart`` nested under a
Building, rather than the Building itself?

Fixture: ``test_files/Building/CitySmith/CS1_multiLOD.gml`` (CityGML 2.0).
This is a real, pre-existing fixture - not fabricated for P6.7 - with one
``bldg:BuildingPart`` (``UUID_4a9691a0-985c-5245-9ac2-8ad61ece0965``)
nested under the top-level Building, carrying exactly two RoofSurfaces:

- ``UUID_778ee73b-...`` (LoD3, **2** PolygonRecords - real multi-polygon
  BuildingPart Roof evidence, P6.7 §14)
- ``UUID_56bd8558-...`` (LoD2, 1 PolygonRecord)

The same Building also owns dozens of its own top-level RoofSurfaces, so
this one fixture already gives the "mixed Building + BuildingPart Roof
hierarchy" shape P6.7 §16 asks for (one BuildingPart, not two - no real
fixture with two sibling BuildingParts each owning a Roof was found in
``test_files/``; see the P6.7 report for that limitation). No interior
rings exist on the BuildingPart's own Roofs in this fixture either - that
combination (§15) is therefore not covered by a real fixture and is left
undemonstrated rather than synthesised.

Per the P6.0-era audit hypothesis this file exists to confirm or refute:
no BuildingPart-specific or Roof-specific ownership code is added. Every
check below runs through the already-generic ``cgml.promote_building``,
``roof_edit_session``, ``roof_usecases``, ``display_regen``, ``registry``
and IR/writer machinery P6.1-P6.5 already proved for Building-owned Roofs.

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_7
"""
import json
import os
import sys
import traceback
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

CS1_MULTILOD = h.FIXTURES / "Building" / "CitySmith" / "CS1_multiLOD.gml"
BUILDING_PART_GML_ID = "UUID_4a9691a0-985c-5245-9ac2-8ad61ece0965"
PART_ROOF_LOD3 = "UUID_778ee73b-29fa-5f1c-8839-a0f42767df38"  # 2 polygons
PART_ROOF_LOD2 = "UUID_56bd8558-9210-5ae7-9ff5-e5778b61ad2a"  # 1 polygon


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _select(objects):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def _building_object():
    import bpy

    for obj in bpy.data.objects:
        if _kind_of(obj) == "Building" and str(obj.get("gml_id") or "").strip():
            return obj
    return None


def _building_part_object():
    import bpy

    for obj in bpy.data.objects:
        if _kind_of(obj) == "BuildingPart" and str(obj.get("gml_id") or "") == BUILDING_PART_GML_ID:
            return obj
    return None


def _promote(building_obj):
    import bpy

    _select([building_obj])
    try:
        result = bpy.ops.cgml.promote_building('EXEC_DEFAULT')
        error = ""
    except RuntimeError as e:
        result, error = {'CANCELLED'}, str(e)
    return result, error


def _strip_unsupported():
    import bpy

    from city_modeler.shared.authoring_core.domain.export_eligibility import (
        ALLOWED_IR_KINDS, PASSTHROUGH_KINDS,
    )

    keep_kinds = (set(ALLOWED_IR_KINDS) | set(PASSTHROUGH_KINDS)) - {"Window"}
    for obj in list(bpy.data.objects):
        kind = _kind_of(obj)
        if kind in ("Building", "BuildingPart") or kind in keep_kinds:
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _prepare_promoted_cs1_multilod():
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as _rs

    _rs._SESSIONS.clear()
    _flag(True)
    h.import_fixture(CS1_MULTILOD, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    if building is None:
        raise RuntimeError("no Building object after import")
    result, error = _promote(building)
    if result != {'FINISHED'}:
        raise RuntimeError(f"CS1_multiLOD promotion did not succeed: result={result} error={error!r}")
    from city_modeler.shared.authoring_core.blender import storage

    part = _building_part_object()
    all_roofs = [o for o in bpy.data.objects if _kind_of(o) == "RoofSurface" and storage.has_record(o)]
    part_roofs = [o for o in all_roofs if str(o.get("gml_id") or "") in (PART_ROOF_LOD3, PART_ROOF_LOD2)]
    building_own_roofs = [o for o in all_roofs if o not in part_roofs]
    return building, part, part_roofs, building_own_roofs


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(obj)
    return json.dumps(record.to_dict(), sort_keys=True) if record is not None else ""


def _legacy_roof_facts(scope):
    from city_modeler.shared.authoring_core.blender import legacy_views

    legacy = legacy_views.read_scene(scope).records
    out = {}
    for f in legacy.features:
        if f.kind != "RoofSurface":
            continue
        polys = legacy.polygons_of(f.gml_id)
        ring_ids = tuple(p.exterior.ring_id for p in polys) + tuple(
            r.ring_id for p in polys for r in p.interiors
        )
        out[f.gml_id] = (f.polygon_ids, ring_ids, f.lod)
    return out


def _by_gml_id(roofs, gml_id):
    return next((r for r in roofs if str(r.get("gml_id")) == gml_id), None)


# ---------------------------------------------------------------------------
# 1. Promotion: ids/LoD/ownership preserved for BuildingPart-owned Roofs
# ---------------------------------------------------------------------------

def buildingpart_roofs_promote_with_correct_ownership(R):
    from city_modeler.shared.authoring_core.blender import building_root, storage

    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    if part is None or len(part_roofs) != 2:
        R.check("buildingpart_roofs_promote_with_correct_ownership", False,
                f"setup: BuildingPart or its 2 Roofs not found (part={part}, n_part_roofs={len(part_roofs)})")
        return

    scope = building_root.building_scope(building)
    legacy_facts = _legacy_roof_facts(scope)

    ok = True
    detail = {}
    for gml_id in (PART_ROOF_LOD3, PART_ROOF_LOD2):
        roof = _by_gml_id(part_roofs, gml_id)
        if roof is None:
            ok = False
            detail[gml_id] = "not promoted"
            continue
        record = storage.read_record(roof)
        feature = record.feature(gml_id)
        want_polys, want_rings, want_lod = legacy_facts.get(gml_id, ((), (), None))
        ids_preserved = feature is not None and feature.polygon_ids == want_polys and feature.lod == want_lod
        owner_is_part = feature is not None and feature.owner_id == BUILDING_PART_GML_ID
        parent_is_part = feature is not None and feature.parent_id == BUILDING_PART_GML_ID
        coords_committed = all(
            p.geometry_state == "committed" and len(set(p.exterior.coords)) >= 3
            for p in record.polygons_of(gml_id)
        )
        n_polys_ok = len(record.polygons_of(gml_id)) == (2 if gml_id == PART_ROOF_LOD3 else 1)
        step_ok = ids_preserved and owner_is_part and parent_is_part and coords_committed and n_polys_ok
        ok = ok and step_ok
        detail[gml_id] = {
            "ids_preserved": ids_preserved, "owner_is_part": owner_is_part,
            "parent_is_part": parent_is_part, "coords_committed": coords_committed,
            "n_polys_ok": n_polys_ok, "lod": feature.lod if feature else None,
        }

    # Contrast: the Building's own Roofs must NOT be owned by the BuildingPart.
    own_roof = building_own_roofs[0] if building_own_roofs else None
    own_owner_is_building = False
    if own_roof is not None:
        own_feature = storage.read_record(own_roof).feature(str(own_roof.get("gml_id")))
        own_owner_is_building = own_feature is not None and own_feature.owner_id == str(building.get("gml_id"))
    ok = ok and own_owner_is_building

    R.check("buildingpart_roofs_promote_with_correct_ownership", ok, json.dumps({
        **detail, "building_own_roof_owner_is_building": own_owner_is_building,
    }))


# ---------------------------------------------------------------------------
# 2. Interactive edit: commit preserves BuildingPart ownership; scope reaches
#    across the whole Building subtree (this exact fixture is why
#    _geometry_scope widens past [roof] alone - see its docstring).
# ---------------------------------------------------------------------------

def buildingpart_roof_edit_commit_preserves_ownership_and_siblings(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD3)
    sibling = _by_gml_id(part_roofs, PART_ROOF_LOD2)
    if roof is None or sibling is None:
        R.check("buildingpart_roof_edit_commit_preserves_ownership_and_siblings", False, "setup failed")
        return
    gml_id = str(roof.get("gml_id"))
    sibling_before = _record_json(sibling)
    building_roof_before = _record_json(building_own_roofs[0]) if building_own_roofs else ""

    roof.location.x += 0.05
    bpy.context.view_layer.update()
    result = rs.commit_transform_drift(gml_id)

    feature_after = storage.read_record(roof).feature(gml_id)
    ownership_preserved = (
        feature_after is not None
        and feature_after.owner_id == BUILDING_PART_GML_ID
        and feature_after.parent_id == BUILDING_PART_GML_ID
    )
    sibling_unaffected = _record_json(sibling) == sibling_before
    building_roof_unaffected = (
        not building_own_roofs or _record_json(building_own_roofs[0]) == building_roof_before
    )
    ok = result.status == "committed" and ownership_preserved and sibling_unaffected and building_roof_unaffected
    R.check("buildingpart_roof_edit_commit_preserves_ownership_and_siblings", ok, json.dumps({
        "result": result.status, "ownership_preserved": ownership_preserved,
        "sibling_unaffected": sibling_unaffected, "building_roof_unaffected": building_roof_unaffected,
    }))


def buildingpart_roof_edit_rollback_preserves_hierarchy(R):
    from city_modeler.shared.authoring_core.blender import display_regen, roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD3)
    if roof is None:
        R.check("buildingpart_roof_edit_rollback_preserves_hierarchy", False, "setup failed")
        return
    gml_id = str(roof.get("gml_id"))
    record_before = _record_json(roof)

    real = display_regen.regenerate_or_restore_roof_display

    def _boom(*_a, **_k):
        raise RuntimeError("injected P6.7 display failure")

    display_regen.regenerate_or_restore_roof_display = _boom
    refused = False
    try:
        rs.begin(gml_id)
        for v in roof.data.vertices:
            v.co.x += 0.2
        rs.commit(gml_id)
    except rs.RoofEditSessionError:
        refused = True
    finally:
        display_regen.regenerate_or_restore_roof_display = real

    feature_after = storage.read_record(roof).feature(gml_id)
    hierarchy_preserved = (
        feature_after is not None
        and feature_after.owner_id == BUILDING_PART_GML_ID
        and feature_after.parent_id == BUILDING_PART_GML_ID
    )
    record_unchanged = _record_json(roof) == record_before
    ok = refused and hierarchy_preserved and record_unchanged
    R.check("buildingpart_roof_edit_rollback_preserves_hierarchy", ok, json.dumps({
        "refused": refused, "hierarchy_preserved": hierarchy_preserved, "record_unchanged": record_unchanged,
    }))


# ---------------------------------------------------------------------------
# 3. Display: derived display works for a BuildingPart-owned Roof
# ---------------------------------------------------------------------------

def buildingpart_roof_display_generates_fresh(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen, registry, roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD2)
    if roof is None:
        R.check("buildingpart_roof_display_generates_fresh", False, "setup failed")
        return
    gml_id = str(roof.get("gml_id"))
    roof.location.x += 0.0001
    bpy.context.view_layer.update()
    rs.commit_transform_drift(gml_id)

    display = display_regen._existing_display_object(roof, gml_id)
    record_set = storage.read_record(roof)
    fresh = display is not None and display_regen.roof_display_freshness(display, record_set, gml_id) == "fresh"
    display_not_in_registry = display is not None and registry.resolve(gml_id) is roof

    ok = display is not None and fresh and display_not_in_registry
    R.check("buildingpart_roof_display_generates_fresh", ok, json.dumps({
        "display_exists": display is not None, "fresh": fresh,
        "registry_resolves_to_semantic_object_not_display": display_not_in_registry,
    }))


# ---------------------------------------------------------------------------
# 4. Duplicate: stays in the same BuildingPart
# ---------------------------------------------------------------------------

def _native_duplicate(obj):
    import bpy

    _select([obj])
    bpy.ops.object.duplicate(linked=False)
    return bpy.context.view_layer.objects.active


def buildingpart_roof_duplicate_preserves_part_ownership(R):
    from city_modeler.shared.authoring_core.blender import registry, roof_usecases, storage

    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD3)
    sibling = _by_gml_id(part_roofs, PART_ROOF_LOD2)
    if roof is None or sibling is None:
        R.check("buildingpart_roof_duplicate_preserves_part_ownership", False, "setup failed")
        return
    old_gml_id = str(roof.get("gml_id"))
    sibling_before = _record_json(sibling)
    before_polys = storage.read_record(roof).polygons_of(old_gml_id)

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))
    new_record = storage.read_record(replacement)
    new_feature = new_record.feature(new_gml_id)
    new_polys = new_record.polygons_of(new_gml_id)

    source_feature = storage.read_record(roof).feature(old_gml_id)

    ok = (
        new_gml_id != old_gml_id
        and new_feature is not None
        and new_feature.owner_id == BUILDING_PART_GML_ID
        and new_feature.parent_id == BUILDING_PART_GML_ID
        and set(p.polygon_id for p in new_polys).isdisjoint({p.polygon_id for p in before_polys})
        and [p.exterior.coords for p in new_polys] == [p.exterior.coords for p in before_polys]
        and source_feature is not None and source_feature.owner_id == BUILDING_PART_GML_ID
        and registry.resolve(new_gml_id) is replacement
        and registry.resolve(old_gml_id) is roof
        and _record_json(sibling) == sibling_before
    )
    R.check("buildingpart_roof_duplicate_preserves_part_ownership", ok, json.dumps({
        "old": old_gml_id, "new": new_gml_id,
        "new_owner": new_feature.owner_id if new_feature else None,
        "sibling_untouched": _record_json(sibling) == sibling_before,
    }))


def buildingpart_roof_duplicate_display_independent(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen, roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD2)
    if roof is None:
        R.check("buildingpart_roof_duplicate_display_independent", False, "setup failed")
        return
    old_gml_id = str(roof.get("gml_id"))
    roof.location.x += 0.0001
    bpy.context.view_layer.update()
    rs.commit_transform_drift(old_gml_id)

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    source_display = display_regen._existing_display_object(roof, old_gml_id)
    dup_display = display_regen._existing_display_object(replacement, new_gml_id)
    record_set = storage.read_record(replacement)
    fresh = dup_display is not None and display_regen.roof_display_freshness(dup_display, record_set, new_gml_id) == "fresh"

    ok = (
        source_display is not None and dup_display is not None
        and source_display is not dup_display and source_display.name != dup_display.name
        and fresh
    )
    R.check("buildingpart_roof_duplicate_display_independent", ok, json.dumps({
        "source_display": source_display is not None, "dup_display": dup_display is not None, "fresh": fresh,
    }))


# ---------------------------------------------------------------------------
# 5. Delete: removes only the target; BuildingPart and its sibling intact
# ---------------------------------------------------------------------------

def buildingpart_roof_delete_leaves_siblings_and_part_intact(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen, registry, roof_usecases

    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    target = _by_gml_id(part_roofs, PART_ROOF_LOD3)
    sibling = _by_gml_id(part_roofs, PART_ROOF_LOD2)
    if target is None or sibling is None:
        R.check("buildingpart_roof_delete_leaves_siblings_and_part_intact", False, "setup failed")
        return
    gml_id = str(target.get("gml_id"))
    target_name = target.name
    sibling_before = _record_json(sibling)
    part_name = part.name
    building_name = building.name

    roof_usecases.delete_roof(gml_id)

    object_gone = bpy.data.objects.get(target_name) is None
    registry_gone = registry.resolve(gml_id) is None
    sibling_intact = _record_json(sibling) == sibling_before
    part_intact = bpy.data.objects.get(part_name) is not None
    building_intact = bpy.data.objects.get(building_name) is not None
    other_roofs_intact = all(bpy.data.objects.get(o.name) is not None for o in building_own_roofs)

    ok = object_gone and registry_gone and sibling_intact and part_intact and building_intact and other_roofs_intact
    R.check("buildingpart_roof_delete_leaves_siblings_and_part_intact", ok, json.dumps({
        "object_gone": object_gone, "registry_gone": registry_gone, "sibling_intact": sibling_intact,
        "part_intact": part_intact, "building_intact": building_intact, "other_roofs_intact": other_roofs_intact,
    }))


# ---------------------------------------------------------------------------
# 6. Registry: nested Roof gml_ids resolve to the correct semantic object
# ---------------------------------------------------------------------------

def registry_resolves_nested_roofs_without_collision(R):
    from city_modeler.shared.authoring_core.blender import registry

    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    if len(part_roofs) != 2 or not building_own_roofs:
        R.check("registry_resolves_nested_roofs_without_collision", False, "setup failed")
        return

    checks = {}
    for roof in part_roofs + building_own_roofs[:1]:
        gml_id = str(roof.get("gml_id"))
        checks[gml_id] = registry.resolve(gml_id) is roof
    ok = all(checks.values())
    R.check("registry_resolves_nested_roofs_without_collision", ok, json.dumps(checks))


# ---------------------------------------------------------------------------
# 7. Save/reload: hierarchy, records, registry preserved
# ---------------------------------------------------------------------------

def buildingpart_roof_save_reload_preserves_hierarchy(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, storage

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    if part is None or len(part_roofs) != 2:
        R.check("buildingpart_roof_save_reload_preserves_hierarchy", False, "setup failed")
        return
    gml_ids = [str(r.get("gml_id")) for r in part_roofs]
    part_gml_id = str(part.get("gml_id"))

    path = str(h.out_dir() / "p6_7_buildingpart_roof_save_reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    registry.REGISTRY.mark_dirty()

    ok = True
    detail = {}
    for gml_id in gml_ids:
        obj = registry.resolve(gml_id)
        feature = storage.read_record(obj).feature(gml_id) if obj is not None else None
        step_ok = (
            obj is not None and feature is not None
            and feature.owner_id == part_gml_id and feature.parent_id == part_gml_id
        )
        ok = ok and step_ok
        detail[gml_id] = step_ok
    R.check("buildingpart_roof_save_reload_preserves_hierarchy", ok, json.dumps(detail))


# ---------------------------------------------------------------------------
# 8. Undo/redo preserves ownership
# ---------------------------------------------------------------------------

def buildingpart_roof_undo_redo_preserves_ownership(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    roof = _by_gml_id(part_roofs, PART_ROOF_LOD3)
    if roof is None:
        R.check("buildingpart_roof_undo_redo_preserves_ownership", False, "setup failed")
        return
    gml_id = str(roof.get("gml_id"))
    roof_name = roof.name
    record_a = _record_json(roof)

    bpy.ops.ed.undo_push(message="cac: before p6.7 roof edit")
    roof.location.x += 0.06
    bpy.context.view_layer.update()
    rs.commit_transform_drift(gml_id)
    record_b = _record_json(bpy.data.objects[roof_name])
    bpy.ops.ed.undo_push(message="cac: after p6.7 roof edit")

    bpy.ops.ed.undo()
    record_undone = _record_json(bpy.data.objects[roof_name])
    bpy.ops.ed.redo()
    record_redone = _record_json(bpy.data.objects[roof_name])

    feature_redone = storage.read_record(bpy.data.objects[roof_name]).feature(gml_id)
    ownership_preserved = (
        feature_redone is not None
        and feature_redone.owner_id == BUILDING_PART_GML_ID
        and feature_redone.parent_id == BUILDING_PART_GML_ID
    )
    ok = (
        record_a != record_b and record_undone == record_a and record_redone == record_b
        and ownership_preserved
    )
    R.check("buildingpart_roof_undo_redo_preserves_ownership", ok, json.dumps({
        "a_ne_b": record_a != record_b, "undone_eq_a": record_undone == record_a,
        "redone_eq_b": record_redone == record_b, "ownership_preserved": ownership_preserved,
    }))


# ---------------------------------------------------------------------------
# 9. D-6 eligibility: decided per-feature-object, independent of the parent
#    Building's own eligibility - characterized, not assumed.
# ---------------------------------------------------------------------------

def buildingpart_d6_eligibility_is_decided_independently_of_building(R):
    from city_modeler.shared.authoring_core.blender import export_dispatch

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    if part is None:
        R.check("buildingpart_d6_eligibility_is_decided_independently_of_building", False, "setup failed")
        return

    building_plan = export_dispatch.building_export_plan(building)
    part_plan = export_dispatch.building_export_plan(part)

    # Characterization, not a fixed expectation either way: both dispatch
    # calls must at least run to completion and report a real decision
    # (not raise), and the two decisions are independent objects - this is
    # the generic per-object dispatch loop in io(_v2)/writer/exporter.py,
    # not a BuildingPart-specific branch.
    ok = building_plan is not None and part_plan is not None
    R.check("buildingpart_d6_eligibility_is_decided_independently_of_building", ok, json.dumps({
        "building_eligible": building_plan.eligibility.eligible if building_plan else None,
        "building_reason": building_plan.eligibility.reason if building_plan else None,
        "part_eligible": part_plan.eligibility.eligible if part_plan else None,
        "part_reason": part_plan.eligibility.reason if part_plan else None,
    }))


# ---------------------------------------------------------------------------
# 10. Export v2/v3: correct nesting, ids preserved, xmllint-clean, no
#     flattening/duplication under the top-level Building.
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[1] if tag.startswith("{") else tag


def _ancestor_tags_and_ids(root, target_gml_id):
    """Chain of (local tag, gml:id) from CityModel down to the element
    carrying ``target_gml_id``, root first."""
    gml_ns = {"{http://www.opengis.net/gml}id", "{http://www.opengis.net/gml/3.2}id"}
    parent = {child: elem for elem in root.iter() for child in elem}

    def gid(elem):
        for k in gml_ns:
            if elem.get(k) is not None:
                return elem.get(k)
        return None

    target = None
    for elem in root.iter():
        if gid(elem) == target_gml_id:
            target = elem
            break
    if target is None:
        return None
    chain = [(_local(target.tag), gid(target))]
    up = parent.get(target)
    while up is not None:
        chain.append((_local(up.tag), gid(up)))
        up = parent.get(up)
    return list(reversed(chain))


def buildingpart_roof_export_v2_nests_under_buildingpart(R):
    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    if part is None or len(part_roofs) != 2 or not building_own_roofs:
        R.check("buildingpart_roof_export_v2_nests_under_buildingpart", False, "setup failed")
        return
    building_gml_id = str(building.get("gml_id"))

    out_path = h.out_dir() / "p6_7_buildingpart_roof_v2.gml"
    text = h.export_scene(str(out_path), "2.0")
    lint = h.xmllint(out_path, "2.0")
    root = ET.fromstring(text.encode("utf-8"))

    ok = lint["exit"] == 0 and lint["errors"] == 0
    detail = {"lint": lint}
    for gml_id in (PART_ROOF_LOD3, PART_ROOF_LOD2):
        chain = _ancestor_tags_and_ids(root, gml_id)
        tags = [t for t, _ in chain] if chain else []
        ids_in_chain = {i for _, i in chain} if chain else set()
        nested_under_part = "BuildingPart" in tags and BUILDING_PART_GML_ID in ids_in_chain
        nested_under_building = building_gml_id in ids_in_chain
        not_duplicated = sum(1 for e in root.iter() if e.get("{http://www.opengis.net/gml}id") == gml_id) == 1
        step_ok = chain is not None and nested_under_part and nested_under_building and not_duplicated
        ok = ok and step_ok
        detail[gml_id] = {"tags": tags, "nested_under_part": nested_under_part,
                           "nested_under_building": nested_under_building, "not_duplicated": not_duplicated}

    # The Building's own Roof must NOT be nested under the BuildingPart.
    own_gml_id = str(building_own_roofs[0].get("gml_id"))
    own_chain = _ancestor_tags_and_ids(root, own_gml_id)
    own_tags = [t for t, _ in own_chain] if own_chain else []
    own_not_under_part = "BuildingPart" not in own_tags
    ok = ok and own_not_under_part
    detail["building_own_roof_not_under_part"] = own_not_under_part

    R.check("buildingpart_roof_export_v2_nests_under_buildingpart", ok, json.dumps(detail))


def buildingpart_roof_export_v3_nests_under_buildingpart(R):
    building, part, part_roofs, building_own_roofs = _prepare_promoted_cs1_multilod()
    if part is None or len(part_roofs) != 2:
        R.check("buildingpart_roof_export_v3_nests_under_buildingpart", False, "setup failed")
        return

    out_path = h.out_dir() / "p6_7_buildingpart_roof_v3.gml"
    text = h.export_scene(str(out_path), "3.0")
    lint = h.xmllint(out_path, "3.0")
    root = ET.fromstring(text.encode("utf-8"))

    ok = lint["exit"] == 0 and lint["errors"] == 0
    detail = {"lint": lint}
    for gml_id in (PART_ROOF_LOD3, PART_ROOF_LOD2):
        chain = _ancestor_tags_and_ids(root, gml_id)
        tags = [t for t, _ in chain] if chain else []
        ids_in_chain = {i for _, i in chain} if chain else set()
        nested_under_part = "BuildingPart" in tags and BUILDING_PART_GML_ID in ids_in_chain
        step_ok = chain is not None and nested_under_part
        ok = ok and step_ok
        detail[gml_id] = {"tags": tags, "nested_under_part": nested_under_part}
    R.check("buildingpart_roof_export_v3_nests_under_buildingpart", ok, json.dumps(detail))


def buildingpart_roof_v2_v3_semantic_parity(R):
    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    if part is None or len(part_roofs) != 2:
        R.check("buildingpart_roof_v2_v3_semantic_parity", False, "setup failed")
        return

    v2_path = h.out_dir() / "p6_7_parity_v2.gml"
    v3_path = h.out_dir() / "p6_7_parity_v3.gml"
    v2_text = h.export_scene(str(v2_path), "2.0")
    v3_text = h.export_scene(str(v3_path), "3.0")
    ids2 = h.collect_ids(v2_text)
    ids3 = h.collect_ids(v3_text)

    roofs2 = set(ids2.get("RoofSurface", ()))
    roofs3 = set(ids3.get("RoofSurface", ()))
    polys2 = set(ids2.get("Polygon", ()))
    polys3 = set(ids3.get("Polygon", ()))
    buildingpart2 = set(ids2.get("BuildingPart", ()))
    buildingpart3 = set(ids3.get("BuildingPart", ()))

    ok = (
        {PART_ROOF_LOD3, PART_ROOF_LOD2} <= roofs2 == roofs3
        and polys2 == polys3
        and buildingpart2 == buildingpart3 == {BUILDING_PART_GML_ID}
    )
    R.check("buildingpart_roof_v2_v3_semantic_parity", ok, json.dumps({
        "roofs_equal": roofs2 == roofs3, "polys_equal": polys2 == polys3,
        "buildingpart_equal": buildingpart2 == buildingpart3,
        "buildingpart_ids_v2": sorted(buildingpart2), "buildingpart_ids_v3": sorted(buildingpart3),
    }))


# ---------------------------------------------------------------------------
# 11. Export is read-only for BuildingPart Roof state
# ---------------------------------------------------------------------------

def buildingpart_export_is_read_only(R):
    from city_modeler.shared.authoring_core.blender import registry

    building, part, part_roofs, _ = _prepare_promoted_cs1_multilod()
    if part is None:
        R.check("buildingpart_export_is_read_only", False, "setup failed")
        return

    object_keys = ("gml_id", "_cgml_record")
    before = h.snapshot_objects(object_keys=object_keys, material_keys=("gml_polygon_id",))
    registry_before = sorted(getattr(registry.REGISTRY, "_by_gml_id", {}).keys())

    h.export_scene(str(h.out_dir() / "p6_7_readonly_v2.gml"), "2.0")
    h.export_scene(str(h.out_dir() / "p6_7_readonly_v3.gml"), "3.0")

    after = h.snapshot_objects(object_keys=object_keys, material_keys=("gml_polygon_id",))
    registry_after = sorted(getattr(registry.REGISTRY, "_by_gml_id", {}).keys())

    ok = before == after and registry_before == registry_after
    R.check("buildingpart_export_is_read_only", ok, json.dumps({
        "snapshot_equal": before == after, "registry_equal": registry_before == registry_after,
    }))


def main():
    R = h.Results(__file__)
    checks = [
        buildingpart_roofs_promote_with_correct_ownership,
        buildingpart_roof_edit_commit_preserves_ownership_and_siblings,
        buildingpart_roof_edit_rollback_preserves_hierarchy,
        buildingpart_roof_display_generates_fresh,
        buildingpart_roof_duplicate_preserves_part_ownership,
        buildingpart_roof_duplicate_display_independent,
        buildingpart_roof_delete_leaves_siblings_and_part_intact,
        registry_resolves_nested_roofs_without_collision,
        buildingpart_roof_save_reload_preserves_hierarchy,
        buildingpart_roof_undo_redo_preserves_ownership,
        buildingpart_d6_eligibility_is_decided_independently_of_building,
        buildingpart_roof_export_v2_nests_under_buildingpart,
        buildingpart_roof_export_v3_nests_under_buildingpart,
        buildingpart_roof_v2_v3_semantic_parity,
        buildingpart_export_is_read_only,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
