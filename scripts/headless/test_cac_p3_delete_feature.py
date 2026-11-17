"""Record-backed Window delete lifecycle (CAC-P3.4, PLAN.md P3.4).

``delete_feature`` (``shared/authoring_core/blender/usecases.py``) removes a
record-backed Window's complete semantic feature - its own record, the host
WallSurface's ring record and legacy ring face, and the Window object - in
one undo step. ``CGML_OT_delete_feature`` (``ops/facade_authoring.py``) is
the explicit, narrow authoring-core delete action that drives it; native
Blender X/Delete on a record-backed Window is *not* routed through here
(AB-02's native-X bypass stays open for P4 - see
``native_x_bypass_still_leaves_a_dangling_ring`` below, which keeps that
distinction honest).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_delete_feature.py -- <out_dir>
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


def _place_window(wall, face_index, ctx, avoid=(), generator=None):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    kwargs = {"generator": generator} if generator else {}
    fill = create_feature("Window", ctx, definition, placement, **kwargs)
    placed_rect = (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)
    return fill, placed_rect


def _run(objects=None):
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(objects)


def _is_blocking(findings):
    from city_modeler.shared.authoring_core.domain import consistency as c

    return c.is_blocking(findings)


def _ring_links(wall):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    return sorted({link for _, _, link in wall_interior_ring_faces(wall) if link})


def _select_active(obj):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _delete_via_operator(fill):
    """Drive the real ``cgml.delete_feature`` operator (poll + execute),
    the same undo-carrying path a user's context-menu click takes -
    ``shared/authoring_core/blender/usecases.py::delete_feature`` directly
    is exercised by the error/no-op checks below instead, since those want
    the raw exception rather than an operator {'CANCELLED'}."""
    import bpy

    _select_active(fill)
    result = bpy.ops.cgml.delete_feature('EXEC_DEFAULT', object_gml_id=str(fill.get("gml_id") or ""))
    return result


def clean_draw_window_delete_removes_everything(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link
    face_count_before = len(wall.data.polygons)

    result = _delete_via_operator(fill)

    object_gone = fill_name not in bpy.data.objects
    ring_gone = opening_link not in _ring_links(wall)
    mesh_shrank = len(wall.data.polygons) < face_count_before
    registry_gone = REGISTRY.get(gml_id) is None
    findings = _run()
    validator_clean = not _is_blocking(findings)
    ok = (result == {"FINISHED"} and object_gone and ring_gone and mesh_shrank
          and registry_gone and validator_clean)
    R.check("clean_draw_window_delete_removes_everything", ok,
            f"result={result} object_gone={object_gone} ring_gone={ring_gone} "
            f"mesh_shrank={mesh_shrank} registry_gone={registry_gone} findings={findings}")


def clean_stamp_window_delete_removes_everything(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, placed = _place_window(wall, face_index, ctx, generator="semantic_stamp")
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name
    feature = storage.read_record(fill).feature(gml_id)
    is_stamp_generated = feature.generator == "semantic_stamp"
    opening_link = feature.opening_link

    result = _delete_via_operator(fill)

    object_gone = fill_name not in bpy.data.objects
    ring_gone = opening_link not in _ring_links(wall)
    findings = _run()
    validator_clean = not _is_blocking(findings)
    ok = (is_stamp_generated and result == {"FINISHED"} and object_gone and ring_gone and validator_clean)
    R.check("clean_stamp_window_delete_removes_everything", ok,
            f"is_stamp_generated={is_stamp_generated} result={result} object_gone={object_gone} "
            f"ring_gone={ring_gone} findings={findings}")


def second_window_on_same_wall_survives_delete(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill_a, rect_a = _place_window(wall, face_index, ctx)
    fill_b, _ = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    gml_a, gml_b = str(fill_a.get("gml_id")), str(fill_b.get("gml_id"))
    name_a, name_b = fill_a.name, fill_b.name
    link_a = storage.read_record(fill_a).feature(gml_a).opening_link
    link_b = storage.read_record(fill_b).feature(gml_b).opening_link

    result = _delete_via_operator(fill_a)

    a_gone = name_a not in bpy.data.objects
    b_intact = (name_b in bpy.data.objects and storage.has_record(bpy.data.objects[name_b])
                and storage.read_record(bpy.data.objects[name_b]).feature(gml_b) is not None)
    links_after = _ring_links(wall)
    ok = (result == {"FINISHED"} and a_gone and b_intact
          and link_a not in links_after and link_b in links_after
          and not _is_blocking(_run()))
    R.check("second_window_on_same_wall_survives_delete", ok,
            f"result={result} a_gone={a_gone} b_intact={b_intact} "
            f"link_a_gone={link_a not in links_after} link_b_present={link_b in links_after}")


def export_clean_both_versions_after_delete(R):
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill_a, rect_a = _place_window(wall, face_index, ctx)
    fill_b, _ = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    gml_a, gml_b = str(fill_a.get("gml_id")), str(fill_b.get("gml_id"))

    result = _delete_via_operator(fill_a)
    ok = result == {"FINISHED"}
    detail = {}
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p3_delete_feature_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        detail[version] = lint
        ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_a not in text and gml_b in text
    R.check("export_clean_both_versions_after_delete", ok, json.dumps(detail))


def undo_redo_restores_and_reremoves(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    fill, _ = _place_window(wall, face_index, ctx)
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(str(fill.get("gml_id"))).opening_link
    face_count_before_delete = len(wall.data.polygons)

    bpy.ops.ed.undo_push(message="cac: before delete window")
    result = _delete_via_operator(fill)
    bpy.ops.ed.undo_push(message="cac: after delete window")
    if result != {"FINISHED"}:
        R.check("undo_redo_restores_and_reremoves", False, f"setup failed: result={result}")
        return

    bpy.ops.ed.undo()
    wall_after_undo = bpy.data.objects[wall_name]
    undo_object_back = fill_name in bpy.data.objects
    undo_ring_back = opening_link in _ring_links(wall_after_undo)
    undo_face_count_restored = len(wall_after_undo.data.polygons) == face_count_before_delete

    bpy.ops.ed.redo()
    wall_after_redo = bpy.data.objects[wall_name]
    redo_object_gone = fill_name not in bpy.data.objects
    redo_ring_gone = opening_link not in _ring_links(wall_after_redo)

    ok = (undo_object_back and undo_ring_back and undo_face_count_restored
          and redo_object_gone and redo_ring_gone)
    R.check("undo_redo_restores_and_reremoves", ok,
            f"undo: object_back={undo_object_back} ring_back={undo_ring_back} "
            f"face_count_restored={undo_face_count_restored}; "
            f"redo: object_gone={redo_object_gone} ring_gone={redo_ring_gone}")


def flag_off_delete_operator_is_unavailable(R):
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    assert storage.has_record(fill)

    _flag(False)
    _select_active(fill)
    import bpy

    from city_modeler.ops.facade_authoring import CGML_OT_delete_feature
    poll_ok = CGML_OT_delete_feature.poll(bpy.context)
    ok = poll_ok is False
    R.check("flag_off_delete_operator_is_unavailable", ok, f"poll={poll_ok}")


def unknown_gml_id_refused_without_mutation(R):
    from city_modeler.shared.authoring_core.blender.usecases import DeleteFeatureError, delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    import bpy

    objects_before = {o.name for o in bpy.data.objects}
    refused = False
    error = ""
    try:
        delete_feature("NOT_A_REAL_GML_ID")
    except (DeleteFeatureError, ValueError) as e:
        refused = True
        error = str(e)
    unchanged = {o.name for o in bpy.data.objects} == objects_before
    ok = refused and unchanged
    R.check("unknown_gml_id_refused_without_mutation", ok, f"refused={refused} error={error!r} unchanged={unchanged}")


def non_window_kind_refused_without_mutation(R):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.usecases import DeleteFeatureError, delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)  # gives the wall its own record too
    wall_gml_id = str(wall.get("gml_id"))
    face_count_before = len(wall.data.polygons)

    refused = False
    error = ""
    try:
        delete_feature(wall_gml_id)
    except (DeleteFeatureError, ValueError) as e:
        refused = True
        error = str(e)
    unchanged = len(wall.data.polygons) == face_count_before and storage.has_record(wall)
    ok = refused and unchanged and "Window only" in error
    R.check("non_window_kind_refused_without_mutation", ok, f"refused={refused} error={error!r} unchanged={unchanged}")


def already_deleted_window_is_refused_cleanly(R):
    from city_modeler.shared.authoring_core.blender.usecases import DeleteFeatureError, delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    first = _delete_via_operator(fill)
    refused = False
    error = ""
    try:
        delete_feature(gml_id)
    except (DeleteFeatureError, ValueError) as e:
        refused = True
        error = str(e)
    ok = first == {"FINISHED"} and refused
    R.check("already_deleted_window_is_refused_cleanly", ok, f"first={first} refused={refused} error={error!r}")


def native_x_bypass_still_leaves_a_dangling_ring(R):
    """AB-02's native-X bypass is explicitly out of CAC-P3.4's scope
    (PLAN.md P3.4 NATIVE X DELETE): a raw ``bpy.data.objects.remove`` never
    goes through ``delete_feature``, so the wall's RingRecord is still left
    dangling and the P3.3 validator must keep catching it - proving the
    supported path (above) and the native bypass (still open) are two
    different things, not the same bug closed twice."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    mesh = fill.data
    bpy.data.objects.remove(fill, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)

    from city_modeler.shared.authoring_core.domain import consistency as c

    findings = _run()
    ok = any(f.code == c.DANGLING_RING for f in findings) and _is_blocking(findings)
    R.check("native_x_bypass_still_leaves_a_dangling_ring", ok, f"findings={findings}")


def main():
    R = h.Results(__file__)
    checks = [
        clean_draw_window_delete_removes_everything,
        clean_stamp_window_delete_removes_everything,
        second_window_on_same_wall_survives_delete,
        export_clean_both_versions_after_delete,
        undo_redo_restores_and_reremoves,
        flag_off_delete_operator_is_unavailable,
        unknown_gml_id_refused_without_mutation,
        non_window_kind_refused_without_mutation,
        already_deleted_window_is_refused_cleanly,
        native_x_bypass_still_leaves_a_dangling_ring,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
