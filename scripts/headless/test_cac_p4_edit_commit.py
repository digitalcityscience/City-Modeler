"""Explicit edit/commit lifecycle for record-backed Window features (CAC-P4.2).

    begin -> snapshot -> user edits -> validate -> commit OR cancel
             -> dual-write record + legacy mirror

``cgml.feature_edit_begin`` / ``cgml.feature_edit_commit`` /
``cgml.feature_edit_cancel`` (``ops/authoring_core_ops.py``) drive
``shared/authoring_core/blender/edit_session.py``. Dual-write only (PLAN.md
Phase 4 Goal): commit re-cuts the host wall's ring onto the Window's edited
position (``ring_opening_sync.sync_opening_to_ring``) and re-derives both
records from that post-sync legacy state, the same way ``create_feature``
(P3.2) derives its own dual-write.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p4_edit_commit.py -- <out_dir>
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


def _op(idname, gml_id):
    import bpy

    ns = bpy.ops
    for part in idname.split("."):
        ns = getattr(ns, part)
    return ns('EXEC_DEFAULT', object_gml_id=gml_id)


def _begin_op(fill):
    _select_active(fill)
    return _op("cgml.feature_edit_begin", str(fill.get("gml_id") or ""))


def _commit_op(fill):
    _select_active(fill)
    return _op("cgml.feature_edit_commit", str(fill.get("gml_id") or ""))


def _cancel_op(fill):
    _select_active(fill)
    return _op("cgml.feature_edit_cancel", str(fill.get("gml_id") or ""))


def _ring_links(wall):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    return sorted({link for _, _, link in wall_interior_ring_faces(wall) if link})


def _ring_verts(wall, link):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    for _idx, verts, l in wall_interior_ring_faces(wall):
        if l == link:
            return tuple(tuple(v) for v in verts)
    return None


def _translate(obj, dx=0.05, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _run_validation(objects=None):
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(objects)


def _is_blocking(findings):
    from city_modeler.shared.authoring_core.domain import consistency as c

    return c.is_blocking(findings)


def begin_on_record_backed_window_succeeds(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    result = _begin_op(fill)
    session = es.active_session(gml_id)
    ok = result == {"FINISHED"} and session is not None and session.checksum and session.gml_id == gml_id
    R.check("begin_on_record_backed_window_succeeds", ok, f"result={result} session={session}")
    if session is not None:
        es.cancel(gml_id)  # leave no open session behind for the next check


def begin_on_legacy_only_window_refuses(R):
    """A Window imported without ever going through ``create_feature`` -
    genuinely legacy-only (no ``_cgml_record`` at all)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    legacy_window = next(
        (o for o in bpy.data.objects
         if str(o.get("cgml3_feature") or "").strip().lower() in {"window", "windowsurface"}
         and not storage.has_record(o)),
        None,
    )
    if legacy_window is None:
        R.check("begin_on_legacy_only_window_refuses", False, "fixture has no legacy Window to test with")
        return

    refused = False
    error = ""
    try:
        es.begin(str(legacy_window.get("gml_id") or ""))
    except es.EditSessionError as e:
        refused = True
        error = str(e)
    no_session_left = es.active_session(str(legacy_window.get("gml_id") or "")) is None
    ok = refused and no_session_left
    R.check("begin_on_legacy_only_window_refuses", ok, f"refused={refused} error={error!r}")


def no_geometry_change_commit_is_noop(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    record_before = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)

    es.begin(gml_id)
    result = es.commit(gml_id)
    record_after = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)

    ok = (result.status == "no_op" and record_before == record_after
          and es.active_session(gml_id) is None)
    R.check("no_geometry_change_commit_is_noop", ok, f"result={result} same_record={record_before == record_after}")


def moved_window_wall_ring_follows_on_commit(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    ring_before = _ring_verts(wall, opening_link)

    es.begin(gml_id)
    _translate(fill, dx=0.05, dy=0.0, dz=0.0)
    result = es.commit(gml_id)

    ring_after = _ring_verts(wall, opening_link)
    ring_moved = ring_before != ring_after

    window_feature = storage.read_record(fill).feature(gml_id)
    wall_feature_after = storage.read_record(wall).feature(str(wall.get("gml_id")))
    gml_id_preserved = window_feature is not None and window_feature.gml_id == gml_id
    wall_still_has_ring = any(
        r.opening_link == opening_link for p in storage.read_record(wall).polygons_of(wall_feature_after.gml_id)
        for r in p.interiors
    )
    registry_resolves = REGISTRY.get(gml_id) is fill
    # Same scope edit_session.commit() itself validates with (the wall's own
    # ancestor chain must be included too, or its parent_id/owner_id can't
    # resolve against a legacy read that never saw the Building); registry
    # findings cover the whole scene regardless of scope (P2.4), so still
    # filter to the two features this edit actually touched.
    from city_modeler.shared.authoring_core.blender.guards import touched_objects

    relevant = {gml_id, wall_feature_after.gml_id}
    findings = [f for f in _run_validation(touched_objects([fill])) if not f.gml_id or f.gml_id in relevant]
    validator_clean = not _is_blocking(findings)

    ok = (result.status == "committed" and ring_moved and gml_id_preserved
          and wall_still_has_ring and registry_resolves and validator_clean)
    R.check("moved_window_wall_ring_follows_on_commit", ok,
            f"result={result} ring_moved={ring_moved} gml_id_preserved={gml_id_preserved} "
            f"wall_still_has_ring={wall_still_has_ring} registry_resolves={registry_resolves} "
            f"validator_clean={validator_clean} findings={findings if not validator_clean else ''}")


def invalid_edit_refused_without_mutation(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.ring_opening_sync import OPENING_LINK_KEY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    record_before = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)
    wall_record_before = json.dumps(storage.read_record(wall).to_dict(), sort_keys=True)
    ring_before = _ring_verts(wall, storage.read_record(fill).feature(gml_id).opening_link)

    es.begin(gml_id)
    _translate(fill, dx=0.05)
    # Corrupt the *legacy* opening link (not the stored record) so the fresh
    # legacy read disagrees with the record - domain/consistency.py's
    # OPENING_LINK_MISMATCH is an existing, blocking (ERROR) finding; no new
    # geometry rule is invented for this test.
    fill[OPENING_LINK_KEY] = "BOGUS-OPENING-LINK-DOES-NOT-EXIST"

    refused = False
    error = ""
    try:
        es.commit(gml_id)
    except es.EditSessionError as e:
        refused = True
        error = str(e)

    record_after = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)
    wall_record_after = json.dumps(storage.read_record(wall).to_dict(), sort_keys=True)
    ring_after = _ring_verts(wall, storage.read_record(fill).feature(gml_id).opening_link)
    session_still_open = es.active_session(gml_id) is not None

    ok = (refused and record_before == record_after and wall_record_before == wall_record_after
          and ring_before == ring_after and session_still_open)
    R.check("invalid_edit_refused_without_mutation", ok,
            f"refused={refused} error={error!r} record_unchanged={record_before == record_after} "
            f"wall_record_unchanged={wall_record_before == wall_record_after} "
            f"ring_unchanged={ring_before == ring_after} session_still_open={session_still_open}")
    if session_still_open:
        es.cancel(gml_id)


def cancel_restores_display(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    location_before = tuple(fill.location)
    mesh_before = tuple(tuple(v.co) for v in fill.data.vertices)
    ring_before = _ring_verts(wall, opening_link)
    record_before = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)

    es.begin(gml_id)
    _translate(fill, dx=0.05, dy=0.03)
    es.cancel(gml_id)

    location_after = tuple(fill.location)
    mesh_after = tuple(tuple(v.co) for v in fill.data.vertices)
    ring_after = _ring_verts(wall, opening_link)
    record_after = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)

    ok = (location_before == location_after and mesh_before == mesh_after
          and ring_before == ring_after and record_before == record_after
          and es.active_session(gml_id) is None)
    R.check("cancel_restores_display", ok,
            f"location_restored={location_before == location_after} "
            f"mesh_restored={mesh_before == mesh_after} ring_unchanged={ring_before == ring_after} "
            f"record_unchanged={record_before == record_after}")


def commit_is_one_undo_step(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    wall_name, fill_name = wall.name, fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    ring_before = _ring_verts(wall, opening_link)
    record_before = json.dumps(storage.read_record(fill).to_dict(), sort_keys=True)

    bpy.ops.ed.undo_push(message="cac: before edit begin")
    begin_result = _begin_op(fill)
    _translate(fill, dx=0.05)
    bpy.ops.ed.undo_push(message="cac: after geometry edit")
    commit_result = _commit_op(fill)
    bpy.ops.ed.undo_push(message="cac: after commit")

    ring_after_commit = _ring_verts(bpy.data.objects[wall_name], opening_link)
    committed_ok = commit_result == {"FINISHED"} and ring_after_commit != ring_before

    bpy.ops.ed.undo()  # should undo the whole commit in one step
    wall_after_undo = bpy.data.objects[wall_name]
    fill_after_undo = bpy.data.objects[fill_name]
    ring_after_undo = _ring_verts(wall_after_undo, opening_link)
    record_after_undo = json.dumps(storage.read_record(fill_after_undo).to_dict(), sort_keys=True)
    undo_restored = ring_after_undo == ring_before and record_after_undo == record_before

    bpy.ops.ed.redo()
    wall_after_redo = bpy.data.objects[wall_name]
    ring_after_redo = _ring_verts(wall_after_redo, opening_link)
    redo_reapplied = ring_after_redo == ring_after_commit

    ok = begin_result == {"FINISHED"} and committed_ok and undo_restored and redo_reapplied
    R.check("commit_is_one_undo_step", ok,
            f"begin={begin_result} committed_ok={committed_ok} undo_restored={undo_restored} "
            f"redo_reapplied={redo_reapplied}")


def flag_off_operators_unavailable(R):
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)

    _flag(False)
    _select_active(fill)
    from city_modeler.ops.authoring_core_ops import (
        CGML_OT_feature_edit_begin,
        CGML_OT_feature_edit_cancel,
        CGML_OT_feature_edit_commit,
    )

    polls = (
        CGML_OT_feature_edit_begin.poll(bpy.context),
        CGML_OT_feature_edit_commit.poll(bpy.context),
        CGML_OT_feature_edit_cancel.poll(bpy.context),
    )
    ok = polls == (False, False, False)
    R.check("flag_off_operators_unavailable", ok, f"polls={polls}")


def edit_exit_handler_unaffected_by_record_backed_pair(R):
    """R6/flag-off-parity guard: ``ops/edit_exit_validation.py`` already
    skips a record-backed pair via ``guards.refuse_if_record_backed``
    (P3.0) - this task's allowed-files list names that module as
    "guard: skip record-backed objects" because the guard already covers
    it; nothing there needed to change for P4.2. This just re-confirms the
    guard still refuses for the pairing this task authors, so a future
    change to either module cannot silently reintroduce a double-commit."""
    from city_modeler.shared.authoring_core.blender.guards import refuse_if_record_backed

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)

    message = refuse_if_record_backed([wall, fill], "Edit-exit auto-sync")
    ok = message is not None
    R.check("edit_exit_handler_unaffected_by_record_backed_pair", ok, f"message={message!r}")


def export_clean_both_versions_after_commit(R):
    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    es.begin(gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(gml_id)
    ok = result.status == "committed"
    detail = {}
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p4_edit_commit_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        detail[version] = lint
        ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id in text
    R.check("export_clean_both_versions_after_commit", ok, json.dumps(detail))


def main():
    R = h.Results(__file__)
    checks = [
        begin_on_record_backed_window_succeeds,
        begin_on_legacy_only_window_refuses,
        no_geometry_change_commit_is_noop,
        moved_window_wall_ring_follows_on_commit,
        invalid_edit_refused_without_mutation,
        cancel_restores_display,
        commit_is_one_undo_step,
        flag_off_operators_unavailable,
        edit_exit_handler_unaffected_by_record_backed_pair,
        export_clean_both_versions_after_commit,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
