# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.4 (RoofSurface delete + duplicate/init
lifecycle completion).

``shared/authoring_core/domain/roof_lifecycle.py`` (pure parts covered by
``scripts/tests/unit/test_cac_p6_4_roof_lifecycle.py``) and
``shared/authoring_core/blender/roof_usecases.py`` need a real Blender scene
for everything that touches ``registry.resolve``, object/material removal,
undo/redo and save/reload - the same split every other CAC phase test
already established (``test_cac_p6_2_roof_edit_session.py``, ``test_cac_p6_3_
roof_display_regen.py``).

Primary fixture: ``test_files/Building/CitySmith/CS1_lod3.gml`` (same P6.0/
P6.1/P6.1a/P6.2/P6.3 fixture - 33 RoofSurface polygons, 2 hole-bearing),
promoted first through the unmodified ``cgml.promote_building`` operator.

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_4
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

CS1 = h.FIXTURES / "Building" / "CitySmith" / "CS1_lod3.gml"


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
    removed = []
    for obj in list(bpy.data.objects):
        kind = _kind_of(obj)
        if kind in ("Building", "BuildingPart") or kind in keep_kinds:
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        removed.append(obj.name)
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return removed


def _prepare_promoted_cs1():
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as _rs

    _rs._SESSIONS.clear()

    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    result, error = _promote(building)
    if result != {'FINISHED'}:
        raise RuntimeError(f"CS1 promotion did not succeed: result={result} error={error!r}")
    from city_modeler.shared.authoring_core.blender import storage

    roofs = [
        o for o in bpy.data.objects
        if _kind_of(o) == "RoofSurface" and storage.has_record(o)
    ]
    return building, roofs


def _hole_bearing(roofs):
    from city_modeler.shared.authoring_core.blender import storage

    for roof in roofs:
        record_set = storage.read_record(roof)
        feature = record_set.feature(str(roof.get("gml_id")))
        if feature is None:
            continue
        for p in record_set.polygons_of(feature.gml_id):
            if p.interiors and p.geometry_state == "committed":
                return roof
    return None


def _native_duplicate(roof):
    """Blender's own Shift+D-equivalent: a new object + new independent
    mesh datablock, custom properties (including ``_cgml_record``) copied
    verbatim, material slots left linked to the exact same datablocks."""
    import bpy

    _select([roof])
    bpy.ops.object.duplicate(linked=False)
    return bpy.context.view_layer.objects.active


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(obj)
    return json.dumps(record.to_dict(), sort_keys=True) if record is not None else ""


# --------------------------------------------------------------------------
# duplicate/init
# --------------------------------------------------------------------------

def duplicate_mints_fresh_ids_and_preserves_geometry(R):
    from city_modeler.shared.authoring_core.blender import registry, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = next((r for r in roofs if r is not _hole_bearing(roofs)), roofs[0] if roofs else None)
    if roof is None:
        R.check("duplicate_mints_fresh_ids_and_preserves_geometry", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))
    before_polys = storage.read_record(roof).polygons_of(old_gml_id)
    before_coords = [p.exterior.coords for p in before_polys]

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)

    new_gml_id = str(replacement.get("gml_id"))
    new_record = storage.read_record(replacement)
    new_feature = new_record.feature(new_gml_id)
    new_polys = new_record.polygons_of(new_gml_id)

    source_record = storage.read_record(roof)
    source_feature = source_record.feature(old_gml_id)

    ok = (
        new_gml_id != old_gml_id
        and new_feature is not None
        and new_feature.kind == "RoofSurface"
        and set(p.polygon_id for p in new_polys).isdisjoint(set(p.polygon_id for p in before_polys))
        and [p.exterior.coords for p in new_polys] == before_coords
        and source_feature.gml_id == old_gml_id  # source untouched
        and registry.resolve(new_gml_id) is replacement
        and registry.resolve(old_gml_id) is roof
    )
    R.check("duplicate_mints_fresh_ids_and_preserves_geometry", ok,
            f"old={old_gml_id} new={new_gml_id} source_untouched={source_feature.gml_id == old_gml_id}")


def duplicate_hole_bearing_preserves_topology(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = _hole_bearing(roofs)
    if roof is None:
        R.check("duplicate_hole_bearing_preserves_topology", False, "no committed hole-bearing RoofSurface in CS1")
        return
    old_gml_id = str(roof.get("gml_id"))
    before_polys = storage.read_record(roof).polygons_of(old_gml_id)
    before_hole_coords = [tuple(r.coords for r in p.interiors) for p in before_polys]

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))
    new_polys = storage.read_record(replacement).polygons_of(new_gml_id)

    same_hole_count = [len(p.interiors) for p in new_polys] == [len(p.interiors) for p in before_polys]
    same_hole_coords = [tuple(r.coords for r in p.interiors) for p in new_polys] == before_hole_coords
    new_ring_ids = {r.ring_id for p in new_polys for r in p.interiors}
    old_ring_ids = {r.ring_id for p in before_polys for r in p.interiors}
    all_committed = all(p.geometry_state == "committed" for p in new_polys)

    ok = same_hole_count and same_hole_coords and new_ring_ids.isdisjoint(old_ring_ids) and all_committed
    R.check("duplicate_hole_bearing_preserves_topology", ok,
            f"hole_count_match={same_hole_count} hole_coords_match={same_hole_coords} "
            f"ring_ids_disjoint={new_ring_ids.isdisjoint(old_ring_ids)} all_committed={all_committed}")


def duplicate_edit_isolation_both_directions(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = next((r for r in roofs if r is not _hole_bearing(roofs)), roofs[0] if roofs else None)
    if roof is None:
        R.check("duplicate_edit_isolation_both_directions", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))
    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    source_record_before = _record_json(roof)
    dup_record_before = _record_json(replacement)

    # Edit the duplicate; the source record must not change.
    rs.begin(new_gml_id)
    for v in replacement.data.vertices:
        v.co.x += 0.3
    rs.commit(new_gml_id)
    source_unaffected_by_dup_edit = _record_json(roof) == source_record_before

    # Edit the source; the duplicate record must not change.
    dup_record_after_dup_edit = _record_json(replacement)
    rs.begin(old_gml_id)
    for v in roof.data.vertices:
        v.co.y += 0.3
    rs.commit(old_gml_id)
    dup_unaffected_by_source_edit = _record_json(replacement) == dup_record_after_dup_edit

    ok = (
        source_unaffected_by_dup_edit and dup_unaffected_by_source_edit
        and dup_record_after_dup_edit != dup_record_before
    )
    R.check("duplicate_edit_isolation_both_directions", ok,
            f"source_unaffected={source_unaffected_by_dup_edit} "
            f"dup_unaffected={dup_unaffected_by_source_edit}")


def duplicate_display_independent_and_fresh(R):
    """Promotion alone never creates a display object (CAC-P6.3: display is
    derived, only (re)built on commit) - so this forces the source's own
    display into existence first (a no-op transform-drift commit), then
    proves the duplicate gets its own, distinct, fresh one, never the
    source's."""
    from city_modeler.shared.authoring_core.blender import display_regen, roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("duplicate_display_independent_and_fresh", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))
    import bpy
    roof.location.x += 0.0001
    bpy.context.view_layer.update()
    rs.commit_transform_drift(old_gml_id)

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    source_display = display_regen._existing_display_object(roof, old_gml_id)
    dup_display = display_regen._existing_display_object(replacement, new_gml_id)
    record_set = storage.read_record(replacement)
    fresh = display_regen.roof_display_freshness(dup_display, record_set, new_gml_id) == "fresh"

    ok = (
        source_display is not None and dup_display is not None
        and source_display is not dup_display and source_display.name != dup_display.name
        and fresh
    )
    R.check("duplicate_display_independent_and_fresh", ok,
            f"source_display_exists={source_display is not None} dup_display_exists={dup_display is not None} "
            f"distinct_objects={source_display is not dup_display if source_display and dup_display else False} "
            f"fresh={fresh}")


def duplicate_sibling_isolation(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    if len(roofs) < 3:
        R.check("duplicate_sibling_isolation", False, f"need >=3 promoted Roofs, found {len(roofs)}")
        return
    target, sibling_b, sibling_c = roofs[0], roofs[1], roofs[2]
    before_b = _record_json(sibling_b)
    before_c = _record_json(sibling_c)

    duplicate = _native_duplicate(target)
    roof_usecases.initialize_roof_duplicate(duplicate)

    ok = _record_json(sibling_b) == before_b and _record_json(sibling_c) == before_c
    R.check("duplicate_sibling_isolation", ok, f"siblings_untouched={ok}")


def duplicate_requires_exactly_one_sibling(R):
    """A second ``initialize_roof_duplicate`` call on an already-initialized
    duplicate must refuse cleanly: the object's own ``gml_id`` has already
    been re-stamped to its own independent id, so it has no sibling left to
    pair with (found 0, not 1) - not a silent no-op, not a crash."""
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("duplicate_requires_exactly_one_sibling", False, "no promoted RoofSurface found")
        return
    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    record_before_retry = _record_json(replacement)

    refused_second_call = False
    try:
        roof_usecases.initialize_roof_duplicate(replacement)
    except roof_usecases.RoofLifecycleError as exc:
        refused_second_call = "exactly one" in str(exc)

    unchanged = _record_json(replacement) == record_before_retry
    ok = refused_second_call and unchanged
    R.check("duplicate_requires_exactly_one_sibling", ok,
            f"refused_second_call={refused_second_call} unchanged={unchanged}")


def duplicate_rollback_on_display_failure(R):
    from city_modeler.shared.authoring_core.blender import display_regen, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("duplicate_rollback_on_display_failure", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))
    duplicate = _native_duplicate(roof)

    real = display_regen.regenerate_or_restore_roof_display

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected display failure")

    display_regen.regenerate_or_restore_roof_display = _boom
    refused = False
    try:
        roof_usecases.initialize_roof_duplicate(duplicate)
    except roof_usecases.RoofLifecycleError:
        refused = True
    finally:
        display_regen.regenerate_or_restore_roof_display = real

    record_after = storage.read_record(duplicate)
    gml_id_after = str(duplicate.get("gml_id") or "")
    no_half_init = (
        record_after is not None and record_after.feature(old_gml_id) is not None
        and gml_id_after == old_gml_id
    )
    ok = refused and no_half_init
    R.check("duplicate_rollback_on_display_failure", ok,
            f"refused={refused} no_half_init={no_half_init}")


def duplicate_undo_redo(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("duplicate_undo_redo", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))

    bpy.ops.ed.undo_push(message="cac: before roof duplicate")
    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))
    replacement_name = replacement.name
    bpy.ops.ed.undo_push(message="cac: after roof duplicate init")

    bpy.ops.ed.undo()
    after_undo = registry.resolve(new_gml_id)
    undo_removed_duplicate = after_undo is None and registry.resolve(old_gml_id) is not None

    bpy.ops.ed.redo()
    after_redo = registry.resolve(new_gml_id)
    redo_restored_duplicate = after_redo is not None and str(after_redo.get("gml_id")) == new_gml_id

    ok = undo_removed_duplicate and redo_restored_duplicate
    R.check("duplicate_undo_redo", ok,
            f"undo_removed={undo_removed_duplicate} redo_restored={redo_restored_duplicate}")


def duplicate_save_reload(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("duplicate_save_reload", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))
    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    path = h.out_dir() / "p6_4_roof_duplicate_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))

    source_after = registry.resolve(old_gml_id)
    dup_after = registry.resolve(new_gml_id)
    both_committed = (
        source_after is not None and dup_after is not None
        and all(p.geometry_state == "committed" for p in storage.read_record(source_after).polygons_of(old_gml_id))
        and all(p.geometry_state == "committed" for p in storage.read_record(dup_after).polygons_of(new_gml_id))
    )
    ok = source_after is not None and dup_after is not None and old_gml_id != new_gml_id and both_committed
    R.check("duplicate_save_reload", ok,
            f"source_resolved={source_after is not None} dup_resolved={dup_after is not None} "
            f"both_committed={both_committed}")


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------

def delete_removes_object_record_display_registry(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen, registry, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("delete_removes_object_record_display_registry", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    roof_name = roof.name
    display_before = display_regen._existing_display_object(roof, gml_id)

    roof_usecases.delete_roof(gml_id)

    object_gone = bpy.data.objects.get(roof_name) is None
    registry_gone = registry.resolve(gml_id) is None
    display_gone = (
        display_before is None
        or bpy.data.objects.get(display_before.name) is None
    )
    ok = object_gone and registry_gone and display_gone
    R.check("delete_removes_object_record_display_registry", ok,
            f"object_gone={object_gone} registry_gone={registry_gone} display_gone={display_gone}")


def delete_sibling_isolation(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases

    _, roofs = _prepare_promoted_cs1()
    if len(roofs) < 3:
        R.check("delete_sibling_isolation", False, f"need >=3 promoted Roofs, found {len(roofs)}")
        return
    target, sibling_b, sibling_c = roofs[0], roofs[1], roofs[2]
    before_b = _record_json(sibling_b)
    before_c = _record_json(sibling_c)
    target_gml_id = str(target.get("gml_id"))

    roof_usecases.delete_roof(target_gml_id)

    ok = _record_json(sibling_b) == before_b and _record_json(sibling_c) == before_c
    R.check("delete_sibling_isolation", ok, f"siblings_untouched={ok}")


def delete_rollback_on_display_failure(R):
    from city_modeler.shared.authoring_core.blender import display_regen, registry, roof_usecases, storage

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("delete_rollback_on_display_failure", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    roof_name = roof.name
    record_before = _record_json(roof)

    real = display_regen.remove_display_object

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected display-removal failure")

    display_regen.remove_display_object = _boom
    refused = False
    try:
        roof_usecases.delete_roof(gml_id)
    except roof_usecases.RoofLifecycleError:
        refused = True
    finally:
        display_regen.remove_display_object = real

    import bpy

    object_survived = bpy.data.objects.get(roof_name) is not None
    record_unchanged = object_survived and _record_json(bpy.data.objects[roof_name]) == record_before
    registry_unchanged = object_survived and registry.resolve(gml_id) is not None
    ok = refused and object_survived and record_unchanged and registry_unchanged
    R.check("delete_rollback_on_display_failure", ok,
            f"refused={refused} object_survived={object_survived} record_unchanged={record_unchanged} "
            f"registry_unchanged={registry_unchanged}")


def delete_undo_redo(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, roof_usecases

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("delete_undo_redo", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))

    bpy.ops.ed.undo_push(message="cac: before roof delete")
    roof_usecases.delete_roof(gml_id)
    bpy.ops.ed.undo_push(message="cac: after roof delete")

    bpy.ops.ed.undo()
    after_undo = registry.resolve(gml_id)
    undo_restored = after_undo is not None and str(after_undo.get("gml_id")) == gml_id

    bpy.ops.ed.redo()
    after_redo = registry.resolve(gml_id)
    redo_removed_again = after_redo is None

    ok = undo_restored and redo_removed_again
    R.check("delete_undo_redo", ok, f"undo_restored={undo_restored} redo_removed_again={redo_removed_again}")


def delete_save_reload(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, roof_usecases

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("delete_save_reload", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    roof_usecases.delete_roof(gml_id)

    path = h.out_dir() / "p6_4_roof_delete_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))

    ok = registry.resolve(gml_id) is None
    R.check("delete_save_reload", ok, f"stays_absent_after_reload={ok}")


def guarded_native_delete_routes_record_backed_roof(R):
    """``cgml.guarded_delete`` (native X/Delete override, CAC-P4.4/D-7a)
    must route a record-backed RoofSurface through its own delete use case
    (CAC-P6.4), not block the whole selection the way an unrouted kind
    previously did."""
    import bpy

    from city_modeler.shared.authoring_core.blender import registry

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("guarded_native_delete_routes_record_backed_roof", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    _select([roof])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')

    ok = result == {'FINISHED'} and registry.resolve(gml_id) is None
    R.check("guarded_native_delete_routes_record_backed_roof", ok, f"result={result}")


def native_split_and_join_refuse_on_record_backed_roof(R):
    """CAC-P6.4 S21: Split/Join migration is explicitly deferred, but the
    already-generic ``guards.refuse_if_record_backed`` guard (wired into
    both ``CGML_OT_split_surfaces`` and ``ops/join_object_parts.py``) must
    already refuse to run on a record-backed RoofSurface, not silently
    bypass its record."""
    import bpy

    _, roofs = _prepare_promoted_cs1()
    roof = roofs[0] if roofs else None
    if roof is None:
        R.check("native_split_and_join_refuse_on_record_backed_roof", False, "no promoted RoofSurface found")
        return
    _select([roof])
    try:
        split_result = bpy.ops.cgml.split_surfaces('EXEC_DEFAULT')
    except RuntimeError:
        split_result = {'CANCELLED'}

    from city_modeler.shared.authoring_core.blender import storage

    record_unchanged = storage.has_record(roof)
    ok = split_result != {'FINISHED'} and record_unchanged
    R.check("native_split_and_join_refuse_on_record_backed_roof", ok,
            f"split_result={split_result} record_unchanged={record_unchanged}")


def main():
    R = h.Results(__file__)
    checks = [
        duplicate_mints_fresh_ids_and_preserves_geometry,
        duplicate_hole_bearing_preserves_topology,
        duplicate_edit_isolation_both_directions,
        duplicate_display_independent_and_fresh,
        duplicate_sibling_isolation,
        duplicate_requires_exactly_one_sibling,
        duplicate_rollback_on_display_failure,
        duplicate_undo_redo,
        duplicate_save_reload,
        delete_removes_object_record_display_registry,
        delete_sibling_isolation,
        delete_rollback_on_display_failure,
        delete_undo_redo,
        delete_save_reload,
        guarded_native_delete_routes_record_backed_roof,
        native_split_and_join_refuse_on_record_backed_roof,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
