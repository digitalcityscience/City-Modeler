"""G/R/S transform-drift detection + explicit commit (CAC-P4.5, D-7b).

    Blender G/R/S -> transform changes -> semantic state becomes dirty
    -> handler detects/marks only (R4) -> explicit commit use case
    -> validate -> dual-write commit -> clean state

Detection is P3.3's existing ``coords_stale`` finding (unchanged - it already
compares committed world-space coordinates against live ``matrix_world``-
derived geometry, so ancestor-only moves are covered for free);
``shared/authoring_core/blender/transform_drift.py`` only mirrors that
finding into a small in-memory cache for UI convenience, never authoritative
(export and the commit use case both recompute fresh).
``cgml.commit_transform_drift`` (``ops/authoring_core_ops.py``) is the only
code allowed to resolve it, reusing P4.2's commit primitives
(``shared/authoring_core/blender/edit_session.py::commit_transform_drift``).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p4_transform_commit.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _place_window(wall, face_index, ctx, avoid=()):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    fill = create_feature("Window", ctx, definition, placement)
    placed_rect = (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)
    return fill, placed_rect


def _select_active(obj):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _op(idname, **kwargs):
    import bpy

    ns = bpy.ops
    for part in idname.split("."):
        ns = getattr(ns, part)
    return ns('EXEC_DEFAULT', **kwargs)


def _commit_drift_op(gml_id="", target='ACTIVE'):
    return _op("cgml.commit_transform_drift", object_gml_id=gml_id, target=target)


def _translate(obj, dx=0.0, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _rotate_z(obj, radians):
    import bpy

    obj.rotation_euler.z += radians
    bpy.context.view_layer.update()


def _scale(obj, factor):
    import bpy

    obj.scale = tuple(c * factor for c in obj.scale)
    bpy.context.view_layer.update()


def _ring_verts(wall, link):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    for _idx, verts, l in wall_interior_ring_faces(wall):
        if l == link:
            return tuple(tuple(v) for v in verts)
    return None


def _run_validation(objects=None):
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(objects)


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record_set = storage.read_record(obj)
    return json.dumps(record_set.to_dict(), sort_keys=True) if record_set is not None else ""


def _fire_handler():
    """Invoke the depsgraph handler's own logic directly rather than trust
    real ``bpy.app.handlers.depsgraph_update_post`` firing timing in
    ``--background`` mode (same approach the P3 edit-exit suite uses for its
    own mode-change handler) - this exercises exactly the marking code the
    real handler calls, see ``depsgraph_handler_registered_and_marks_only``
    below for the registration-presence check."""
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    td._on_depsgraph_update(None, None)


def clean_record_backed_window_reports_no_drift(R):
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _fire_handler()
    ok = not td.is_dirty(gml_id)
    R.check("clean_record_backed_window_reports_no_drift", ok, f"dirty={td.is_dirty(gml_id)}")


def translate_window_marks_dirty(R):
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _translate(fill, dx=0.05)
    _fire_handler()
    ok = td.is_dirty(gml_id)
    R.check("translate_window_marks_dirty", ok, f"dirty={td.is_dirty(gml_id)}")


def rotate_window_marks_dirty(R):
    import math

    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _rotate_z(fill, math.radians(5))
    _fire_handler()
    ok = td.is_dirty(gml_id)
    R.check("rotate_window_marks_dirty", ok, f"dirty={td.is_dirty(gml_id)}")


def scale_window_marks_dirty(R):
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _scale(fill, 1.2)
    _fire_handler()
    ok = td.is_dirty(gml_id)
    R.check("scale_window_marks_dirty", ok, f"dirty={td.is_dirty(gml_id)}")


def ancestor_building_move_marks_window_dirty(R):
    """Moving the Window's parent Building (not the Window itself, not even
    the WallSurface's own local transform) must still be detected - P4.1's
    ancestor-aware, ``matrix_world``-based comparison is the whole reason
    this works with zero extra code (see module docstring)."""
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    building = wall.parent
    if building is None:
        R.check("ancestor_building_move_marks_window_dirty", False, "fixture wall has no parent to move")
        return

    wall_local_before = tuple(wall.matrix_basis)
    window_local_before = tuple(fill.matrix_basis)

    _translate(building, dx=0.2, dy=0.1)
    _fire_handler()

    wall_local_unchanged = tuple(wall.matrix_basis) == wall_local_before
    window_local_unchanged = tuple(fill.matrix_basis) == window_local_before
    ok = td.is_dirty(gml_id) and wall_local_unchanged and window_local_unchanged
    R.check("ancestor_building_move_marks_window_dirty", ok,
            f"dirty={td.is_dirty(gml_id)} wall_local_unchanged={wall_local_unchanged} "
            f"window_local_unchanged={window_local_unchanged}")


def wall_move_marks_wall_and_window_dirty(R):
    """Acceptance: moving a record-backed WallSurface (G) leaves both the
    wall's own committed exterior coordinates and its Window child's
    coordinates stale."""
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id") or "")

    _translate(wall, dx=0.1)
    _fire_handler()

    ok = td.is_dirty(gml_id) and (not wall_gml_id or td.is_dirty(wall_gml_id))
    R.check("wall_move_marks_wall_and_window_dirty", ok,
            f"window_dirty={td.is_dirty(gml_id)} wall_dirty={td.is_dirty(wall_gml_id)}")


def transform_back_to_committed_is_clean(R):
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _translate(fill, dx=0.05)
    _fire_handler()
    dirty_after_move = td.is_dirty(gml_id)

    _translate(fill, dx=-0.05)
    _fire_handler()
    ok = dirty_after_move and not td.is_dirty(gml_id)
    R.check("transform_back_to_committed_is_clean", ok,
            f"dirty_after_move={dirty_after_move} dirty_after_return={td.is_dirty(gml_id)}")


def commit_transform_drift_preserves_identity_and_updates_ring(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    ring_before = _ring_verts(wall, opening_link)

    _translate(fill, dx=0.05, dy=0.02)
    result = es.commit_transform_drift(gml_id)

    ring_after = _ring_verts(wall, opening_link)
    window_feature = storage.read_record(fill).feature(gml_id)
    wall_feature_after = storage.read_record(wall).feature(str(wall.get("gml_id")))
    wall_still_has_ring = any(
        r.opening_link == opening_link
        for p in storage.read_record(wall).polygons_of(wall_feature_after.gml_id)
        for r in p.interiors
    )

    ok = (result.status == "committed" and ring_before != ring_after
          and window_feature is not None and window_feature.gml_id == gml_id
          and wall_still_has_ring and REGISTRY.get(gml_id) is fill)
    R.check("commit_transform_drift_preserves_identity_and_updates_ring", ok,
            f"result={result} ring_moved={ring_before != ring_after} "
            f"gml_id_preserved={window_feature is not None and window_feature.gml_id == gml_id} "
            f"wall_still_has_ring={wall_still_has_ring}")


def commit_clears_dirty_state(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _translate(fill, dx=0.05)
    _fire_handler()
    dirty_before = td.is_dirty(gml_id)

    result = es.commit_transform_drift(gml_id)
    _fire_handler()

    ok = dirty_before and result.status == "committed" and not td.is_dirty(gml_id)
    R.check("commit_clears_dirty_state", ok,
            f"dirty_before={dirty_before} result={result} dirty_after={td.is_dirty(gml_id)}")


def no_op_when_checksum_unchanged(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    record_before = _record_json(fill)

    result = es.commit_transform_drift(gml_id)
    record_after = _record_json(fill)

    ok = result.status == "no_op" and record_before == record_after
    R.check("no_op_when_checksum_unchanged", ok, f"result={result} same_record={record_before == record_after}")


def invalid_transform_refused_without_mutation(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.ring_opening_sync import OPENING_LINK_KEY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    ring_before = _ring_verts(wall, opening_link)

    _translate(fill, dx=0.05)
    # Corrupt the *legacy* opening link (not the stored record), same trick
    # test_cac_p4_edit_commit.py uses: a fresh legacy read now disagrees with
    # the record, which is an existing blocking (ERROR) finding
    # (OPENING_LINK_MISMATCH) - no new geometry rule invented for this test.
    fill[OPENING_LINK_KEY] = "BOGUS-OPENING-LINK-DOES-NOT-EXIST"

    refused = False
    error = ""
    try:
        es.commit_transform_drift(gml_id)
    except es.EditSessionError as e:
        refused = True
        error = str(e)

    record_after = _record_json(fill)
    wall_record_after = _record_json(wall)
    ring_after = _ring_verts(wall, opening_link)

    ok = (refused and record_before == record_after and wall_record_before == wall_record_after
          and ring_before == ring_after)
    R.check("invalid_transform_refused_without_mutation", ok,
            f"refused={refused} error={error!r} record_unchanged={record_before == record_after} "
            f"wall_record_unchanged={wall_record_before == wall_record_after} "
            f"ring_unchanged={ring_before == ring_after}")


def export_refuses_while_drift_uncommitted(R):
    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _translate(fill, dx=0.05)

    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight

    messages = []
    blocked = export_preflight(report=lambda level, text: messages.append((level, text)))
    named = any(gml_id in text for _level, text in messages)
    points_at_commit = any("commit_transform_drift" in text for _level, text in messages)

    ok = blocked and named and points_at_commit
    R.check("export_refuses_while_drift_uncommitted", ok,
            f"blocked={blocked} named={named} points_at_commit={points_at_commit} messages={messages}")


def export_succeeds_after_commit(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    _translate(fill, dx=0.05)
    result = es.commit_transform_drift(gml_id)

    detail = {"commit": result.status}
    ok = result.status == "committed"
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p4_transform_commit_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        detail[version] = lint
        ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id in text
    R.check("export_succeeds_after_commit", ok, json.dumps(detail))


def commit_transform_drift_is_one_undo_step(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    wall_name, fill_name = wall.name, fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    ring_before = _ring_verts(wall, opening_link)
    record_before = _record_json(fill)

    bpy.ops.ed.undo_push(message="cac: before transform")
    _translate(fill, dx=0.05)
    bpy.ops.ed.undo_push(message="cac: after transform")
    _select_active(fill)
    commit_result = _commit_drift_op(gml_id)
    bpy.ops.ed.undo_push(message="cac: after commit_transform_drift")

    ring_after_commit = _ring_verts(bpy.data.objects[wall_name], opening_link)
    committed_ok = commit_result == {"FINISHED"} and ring_after_commit != ring_before

    bpy.ops.ed.undo()  # undo the whole commit in one step
    wall_after_undo = bpy.data.objects[wall_name]
    fill_after_undo = bpy.data.objects[fill_name]
    ring_after_undo = _ring_verts(wall_after_undo, opening_link)
    record_after_undo = _record_json(fill_after_undo)
    undo_restored = ring_after_undo == ring_before and record_after_undo == record_before

    bpy.ops.ed.redo()
    wall_after_redo = bpy.data.objects[wall_name]
    ring_after_redo = _ring_verts(wall_after_redo, opening_link)
    redo_reapplied = ring_after_redo == ring_after_commit

    ok = committed_ok and undo_restored and redo_reapplied
    R.check("commit_transform_drift_is_one_undo_step", ok,
            f"committed_ok={committed_ok} undo_restored={undo_restored} redo_reapplied={redo_reapplied}")


def raw_transform_undo_returns_to_clean(R):
    """G/R/S -> dirty -> Blender undo (of the raw transform, before any
    commit) -> the detector is clean again, purely because the live state
    once more matches the committed record - no special-cased "undo of a
    transform" handling exists or is needed."""
    import bpy

    from city_modeler.shared.authoring_core.blender import transform_drift as td

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name

    bpy.ops.ed.undo_push(message="cac: before raw transform")
    _translate(fill, dx=0.05)
    bpy.ops.ed.undo_push(message="cac: after raw transform")
    _fire_handler()
    dirty_after_move = td.is_dirty(gml_id)

    bpy.ops.ed.undo()
    _fire_handler()
    dirty_after_undo = td.is_dirty(str(bpy.data.objects[fill_name].get("gml_id") or ""))

    ok = dirty_after_move and not dirty_after_undo
    R.check("raw_transform_undo_returns_to_clean", ok,
            f"dirty_after_move={dirty_after_move} dirty_after_undo={dirty_after_undo}")


def depsgraph_handler_registered_and_marks_only(R):
    """``register()``/``unregister()`` (CAC-P4.5) - matches the pattern
    ``test_cac_p3_edit_exit_persistence.py`` uses for the P3 edit-exit
    handler. Also re-confirms by inspection that the handler module carries
    no write path into storage/records - R4 grep target for review."""
    import ast
    import inspect

    import bpy

    from city_modeler.shared.authoring_core.blender import transform_drift as td

    registered_before = td._on_depsgraph_update in bpy.app.handlers.depsgraph_update_post
    td.unregister()
    registered_after_unregister = td._on_depsgraph_update in bpy.app.handlers.depsgraph_update_post
    td.register()
    registered_after_register = td._on_depsgraph_update in bpy.app.handlers.depsgraph_update_post

    source = inspect.getsource(td)
    tree = ast.parse(source)
    write_calls = {"write_record", "sync_opening_to_ring", "sync_ring_to_opening", "delete_feature"}
    calls_found = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in write_calls
    }

    ok = (registered_after_unregister is False and registered_after_register
          and not calls_found)
    R.check("depsgraph_handler_registered_and_marks_only", ok,
            f"registered_before={registered_before} after_unregister={registered_after_unregister} "
            f"after_register={registered_after_register} write_calls_found={calls_found}")


def flag_off_operator_unavailable(R):
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)

    _flag(False)
    _select_active(fill)
    from city_modeler.ops.authoring_core_ops import CGML_OT_commit_transform_drift

    ok = CGML_OT_commit_transform_drift.poll(bpy.context) is False
    R.check("flag_off_operator_unavailable", ok, f"poll={CGML_OT_commit_transform_drift.poll(bpy.context)}")


def main():
    R = h.Results(__file__)
    checks = [
        clean_record_backed_window_reports_no_drift,
        translate_window_marks_dirty,
        rotate_window_marks_dirty,
        scale_window_marks_dirty,
        ancestor_building_move_marks_window_dirty,
        wall_move_marks_wall_and_window_dirty,
        transform_back_to_committed_is_clean,
        commit_transform_drift_preserves_identity_and_updates_ring,
        commit_clears_dirty_state,
        no_op_when_checksum_unchanged,
        invalid_transform_refused_without_mutation,
        export_refuses_while_drift_uncommitted,
        export_succeeds_after_commit,
        commit_transform_drift_is_one_undo_step,
        raw_transform_undo_returns_to_clean,
        depsgraph_handler_registered_and_marks_only,
        flag_off_operator_unavailable,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
