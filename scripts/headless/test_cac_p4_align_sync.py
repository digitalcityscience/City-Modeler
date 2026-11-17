"""CAC-P4.6: record-backed Window Align and manual Ring/Opening Sync.

This intentionally exercises the public shared-commit functions directly:
the Blender operators are thin dispatchers and the assertions here prove the
important invariant -- no Align/Sync-specific record writer exists.
"""
import os
import sys
import traceback
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402


RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on=True):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    return wall, face_index, h.surface_context(wall, face_index)


def _place(wall, face_index, ctx, avoid=()):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    feature = create_feature(
        "Window", ctx,
        FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05),
        PlacementRequest(position_uv=uv, source="manual_draw"),
    )
    return feature, (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)


def _consistent(window, wall):
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender import transform_drift
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    gml_id = str(window.get("gml_id"))
    record = storage.read_record(window).feature(gml_id)
    ring_count = sum(1 for _idx, _verts, link in wall_interior_ring_faces(wall) if link == record.opening_link)
    # The fixture carries unrelated historical hierarchy findings (its source
    # Building is intentionally not an exploded semantic object).  P4.6 must
    # instead prove its own invariant: this Window has one paired ring and no
    # uncommitted coordinate/transform drift after the explicit operation.
    findings = [f for f in run_validation([window, wall]) if f.gml_id == gml_id]
    stale = [f for f in findings if f.code == "coords_stale"]
    dirty = transform_drift.is_dirty(gml_id)
    return ring_count == 1 and not stale and not dirty, ring_count, stale


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage
    records = storage.read_record(obj)
    return json.dumps(records.to_dict(), sort_keys=True) if records else ""


def _ring_coords(wall, link):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces
    return next((tuple(tuple(v) for v in verts) for _idx, verts, key in wall_interior_ring_faces(wall)
                 if key == link), None)


def _select_active(active, selected):
    import bpy
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in selected:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active


def align_record_backed_window(R):
    from city_modeler.shared.authoring_core.blender.edit_session import align_existing_window
    from city_modeler.shared.authoring_core.blender.registry import resolve

    _flag()
    wall, face_index, ctx = _prepare()
    source, source_rect = _place(wall, face_index, ctx)
    target, _target_rect = _place(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))
    result = align_existing_window(gml_id, target)
    consistent, rings, errors = _consistent(source, wall)
    ok = result.status == "committed" and str(source.get("gml_id")) == gml_id and resolve(gml_id, "") is source and consistent
    R.check("record_backed_align_commits_identity_and_ring", ok,
            f"status={result.status} resolved={resolve(gml_id, '') is source} rings={rings} errors={errors}")


def align_operator_routes_and_undo_redo(R):
    import bpy
    from city_modeler.shared.authoring_core.blender import storage

    _flag()
    wall, face_index, ctx = _prepare()
    source, source_rect = _place(wall, face_index, ctx)
    target, _ = _place(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))
    link = storage.read_record(source).feature(gml_id).opening_link
    record_before, ring_before = _record_json(source), _ring_coords(wall, link)
    matrix_before = tuple(tuple(row) for row in source.matrix_world)
    source_name, wall_name = source.name, wall.name
    _select_active(target, (source, target))
    # Background Blender needs an explicit initial checkpoint before its undo
    # stack is available (the P0.0 capability probe and P4.2 tests use this
    # same headless convention).
    bpy.ops.ed.undo_push(message="cac: before align")
    result = bpy.ops.cgml.align_objects('EXEC_DEFAULT')
    bpy.ops.ed.undo_push(message="cac: after align")
    source_after, wall_after = bpy.data.objects[source_name], bpy.data.objects[wall_name]
    record_after = _record_json(source_after)
    ring_after = _ring_coords(wall_after, link)
    matrix_after = tuple(tuple(row) for row in source_after.matrix_world)
    bpy.ops.ed.undo()
    source_undo, wall_undo = bpy.data.objects[source_name], bpy.data.objects[wall_name]
    undo_ok = _record_json(source_undo) == record_before and _ring_coords(wall_undo, link) == ring_before
    bpy.ops.ed.redo()
    source_redo, wall_redo = bpy.data.objects[source_name], bpy.data.objects[wall_name]
    redo_ok = _record_json(source_redo) == record_after and _ring_coords(wall_redo, link) == ring_after
    clean, rings, stale = _consistent(source_redo, wall_redo)
    geometry_changed = ring_after != ring_before or matrix_after != matrix_before
    ok = result == {'FINISHED'} and geometry_changed and undo_ok and redo_ok and clean
    R.check("align_operator_no_refusal_and_undo_redo", ok,
            f"result={result} geometry_changed={geometry_changed} record_changed={record_after != record_before} "
            f"undo={undo_ok} redo={redo_ok} rings={rings} stale={stale}")


def invalid_align_refuses_atomically(R):
    from city_modeler.shared.authoring_core.blender.edit_session import EditSessionError, align_existing_window
    from city_modeler.shared.authoring_core.blender import storage

    _flag()
    wall, face_index, ctx = _prepare()
    source, _ = _place(wall, face_index, ctx)
    gml_id = str(source.get("gml_id"))
    link = storage.read_record(source).feature(gml_id).opening_link
    before = (_record_json(source), _record_json(wall), tuple(source.matrix_world), _ring_coords(wall, link))
    try:
        # A source cannot be aligned to itself.  The legacy geometry helper
        # rejects this after the shared seam has taken its rollback snapshot.
        align_existing_window(gml_id, source)
        refused = False
    except EditSessionError:
        refused = True
    after = (_record_json(source), _record_json(wall), tuple(source.matrix_world), _ring_coords(wall, link))
    R.check("invalid_align_refuses_without_partial_mutation", refused and before == after,
            f"refused={refused} state_unchanged={before == after}")


def manual_sync_both_directions_commit(R):
    from city_modeler.shared.authoring_core.blender.edit_session import sync_existing_feature

    _flag()
    wall, face_index, ctx = _prepare()
    window, _ = _place(wall, face_index, ctx)
    gml_id = str(window.get("gml_id"))

    # Opening -> Ring: move the working Window, then explicitly recut + commit.
    window.location.x += 0.04
    first = sync_existing_feature(gml_id, "opening_to_ring")
    first_ok, first_rings, first_errors = _consistent(window, wall)

    # Ring -> Opening: make the opening stale again, then keep the committed
    # ring where it is and move the Window back through the same commit seam.
    window.location.x += 0.04
    second = sync_existing_feature(gml_id, "ring_to_opening")
    second_ok, second_rings, second_errors = _consistent(window, wall)
    ok = (first.status == "committed" and second.status == "committed" and first_ok and second_ok
          and str(window.get("gml_id")) == gml_id)
    R.check("manual_sync_both_directions_commit_cleanly", ok,
            f"first={first.status}/{first_rings}/{first_errors} second={second.status}/{second_rings}/{second_errors}")


def main():
    R = h.Results(__file__)
    for check in (
        align_record_backed_window,
        align_operator_routes_and_undo_redo,
        invalid_align_refuses_atomically,
        manual_sync_both_directions_commit,
    ):
        try:
            check(R)
        except Exception:
            R.check(check.__name__, False, traceback.format_exc())
    R.finish()


if __name__ == "__main__":
    main()
