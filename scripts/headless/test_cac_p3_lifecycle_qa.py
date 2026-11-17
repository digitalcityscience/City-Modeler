"""CAC-P3.6 undo/redo QA: the full record-backed Window lifecycle as one
narrative, headless (PLAN.md P3.6 / S1 steps 1, 3, 6, 8).

Each of P3.2/P3.2a/P3.4's own test files proves one step of the lifecycle in
isolation (create, stamp, delete) with undo/redo around that single step.
This file is not a new gate - every check it makes is already covered
elsewhere - it is the P3.6 evidence artifact that runs the whole chain in
one script and one Blender session, exactly as the manual QA checklist
describes it:

    create -> record -> registry -> panel -> delete (with cascade) ->
    undo -> undo -> redo -> redo -> export (v2 + v3, xmllint)

using the same real entry points a user would: ``bpy.ops.cgml.facade_draw``
(create.md, PLAN.md P3.2) and ``bpy.ops.cgml.delete_feature`` (PLAN.md P3.4),
each one Blender undo step, so two ``bpy.ops.ed.undo()`` calls walk back
through delete-then-create to the as-imported state, and two
``bpy.ops.ed.redo()`` calls walk forward again.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_lifecycle_qa.py -- <out_dir>
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


def _draw_operator(wall, face_index, position_uv, size=SIZE):
    import bpy
    import bmesh

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(wall.data)

    before = {o.name for o in bpy.data.objects}
    try:
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type="Window", width=size, height=size, depth=0.05,
            position_uv=position_uv, object_name=wall.name,
            object_gml_id=str(wall.get("gml_id") or ""), face_index=face_index,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the fill object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    fill = bpy.data.objects[new_names[0]] if new_names else None
    return result, error, fill


def _select_active(obj):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _delete_via_operator(fill):
    import bpy

    _select_active(fill)
    return bpy.ops.cgml.delete_feature('EXEC_DEFAULT', object_gml_id=str(fill.get("gml_id") or ""))


def _ring_links(wall):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    return sorted({link for _, _, link in wall_interior_ring_faces(wall) if link})


def full_lifecycle_undo_redo_export(R):
    """S1 steps 1, 3, 6, 8 as one narrative: create -> record -> registry ->
    panel -> delete (cascade) -> undo x2 -> redo x2 -> export v2/v3."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.accessor import read_feature_view
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY
    from city_modeler.ui import citygml_attributes_panel as panel

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    face_count_as_imported = len(wall.data.polygons)

    # --- create ---------------------------------------------------------
    bpy.ops.ed.undo_push(message="cac: before draw window")
    result, error, fill = _draw_operator(wall, face_index, uv)
    bpy.ops.ed.undo_push(message="cac: after draw window")
    if result != {"FINISHED"} or fill is None:
        R.check("full_lifecycle_undo_redo_export", False, f"create failed: result={result} error={error!r}")
        return
    fill_name = fill.name
    gml_id = str(fill.get("gml_id"))
    face_count_after_create = len(wall.data.polygons)
    step_create = result == {"FINISHED"} and fill is not None and face_count_after_create > face_count_as_imported

    # --- record -----------------------------------------------------------
    fill_has_record = storage.has_record(fill)
    wall_has_record = storage.has_record(wall)
    feature = storage.read_record(fill).feature(gml_id) if fill_has_record else None
    opening_link = feature.opening_link if feature is not None else None
    step_record = (
        fill_has_record and wall_has_record and feature is not None
        and feature.kind == "Window" and feature.host_id == str(wall.get("gml_id"))
        and opening_link and opening_link in _ring_links(wall)
    )

    # --- registry ---------------------------------------------------------
    resolved = REGISTRY.get(gml_id)
    step_registry = resolved is not None and resolved.name == fill_name

    # --- panel --------------------------------------------------------------
    panel_gml_id = panel.get_gml_id(fill)
    panel_feature_type = panel.get_feature_type(fill)
    panel_semantic_type, panel_semantic_origin = panel.get_semantic_type(fill)
    panel_view_origin = read_feature_view(fill).origin
    step_panel = (
        panel_gml_id == gml_id
        and panel_feature_type == "Window"
        and panel_semantic_type == "Window"
        and panel_view_origin == "record"
    )

    # --- delete (with cascade) ---------------------------------------------
    bpy.ops.ed.undo_push(message="cac: before delete window")
    delete_result = _delete_via_operator(fill)
    bpy.ops.ed.undo_push(message="cac: after delete window")
    wall_after_delete = bpy.data.objects[wall_name]
    delete_object_gone = fill_name not in bpy.data.objects
    delete_ring_gone = opening_link not in _ring_links(wall_after_delete)
    face_count_after_delete = len(wall_after_delete.data.polygons)
    delete_mesh_shrank = face_count_after_delete < face_count_after_create
    delete_registry_gone = REGISTRY.get(gml_id) is None
    step_delete = (
        delete_result == {"FINISHED"} and delete_object_gone and delete_ring_gone
        and delete_mesh_shrank and delete_registry_gone
    )

    # --- undo x2: past the delete, then past the create ----------------------
    # ``bpy.ops.ed.undo()`` reverts exactly one internal undo-stack entry per
    # call; the manual ``undo_push`` checkpoints bracketing each operator do
    # not reliably collapse into that operator's own auto-registered step
    # (observed: the create step alone can need more than one ``undo()`` call
    # to fully unwind once a second operator has since run). What the CAC-P3.6
    # checklist actually asserts is the outcome - repeatedly undoing walks
    # back through delete, then create, to the as-imported state - not a
    # fixed keystroke count, so this polls with a small bound instead of
    # hard-coding "exactly two calls".
    def _state():
        wall_now = bpy.data.objects[wall_name]
        return {
            "object_present": fill_name in bpy.data.objects,
            "ring_present": opening_link in _ring_links(wall_now),
            "face_count": len(wall_now.data.polygons),
        }

    def _matches_created():
        s = _state()
        return s["object_present"] and s["ring_present"] and s["face_count"] == face_count_after_create

    def _matches_imported():
        s = _state()
        return not s["object_present"] and not s["ring_present"] and s["face_count"] == face_count_as_imported

    def _matches_deleted():
        s = _state()
        return not s["object_present"] and not s["ring_present"] and s["face_count"] == face_count_after_delete

    MAX_STEPS = 6
    undo_reached_created = False
    undo_calls_to_created = 0
    for _ in range(MAX_STEPS):
        bpy.ops.ed.undo()
        undo_calls_to_created += 1
        if _matches_created():
            undo_reached_created = True
            break
    step_undo_1 = undo_reached_created
    undo_state_at_created = _state()

    undo_reached_imported = False
    undo_calls_to_imported = 0
    for _ in range(MAX_STEPS):
        bpy.ops.ed.undo()
        undo_calls_to_imported += 1
        if _matches_imported():
            undo_reached_imported = True
            break
    step_undo_2 = undo_reached_imported
    undo_state_at_imported = _state()

    # --- redo x2: back to the final (deleted) state --------------------------
    # Symmetry with the undo call counts above is not assumed: empirically,
    # Blender's redo direction can reapply more state per call than undo
    # unwound per call (observed here: 2 undo() calls out, 1 redo() call back
    # to the same intermediate "created" checkpoint) - the property this
    # checklist step actually needs is that redo gets there and then all the
    # way forward again, not a matching keystroke count.
    redo_reached_created = False
    redo_calls_to_created = 0
    for _ in range(MAX_STEPS):
        bpy.ops.ed.redo()
        redo_calls_to_created += 1
        if _matches_created():
            redo_reached_created = True
            break
    step_redo_1 = redo_reached_created

    redo_reached_deleted = False
    redo_calls_to_deleted = 0
    for _ in range(MAX_STEPS):
        bpy.ops.ed.redo()
        redo_calls_to_deleted += 1
        if _matches_deleted():
            redo_reached_deleted = True
            break
    step_redo_2 = redo_reached_deleted

    # --- export v2 + v3 from the final (deleted) state, xmllint (I6) --------
    export_detail = {}
    step_export = True
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p3_lifecycle_qa_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        export_detail[version] = lint
        step_export = step_export and lint["exit"] == 0 and lint["errors"] == 0 and gml_id not in text

    steps = {
        "1_create": step_create,
        "2_record": step_record,
        "3_registry": step_registry,
        "4_panel": step_panel,
        "5_delete_cascade": step_delete,
        "6_undo_past_delete": step_undo_1,
        "7_undo_past_create": step_undo_2,
        "8_redo_past_create": step_redo_1,
        "9_redo_past_delete": step_redo_2,
        "10_export_v2_v3_xmllint": step_export,
    }
    ok = all(steps.values())
    detail = {
        "steps": steps,
        "panel": {
            "gml_id": panel_gml_id, "feature_type": panel_feature_type,
            "semantic_type": panel_semantic_type, "semantic_origin": panel_semantic_origin,
            "view_origin": panel_view_origin,
        },
        "export": export_detail,
        "undo_redo_calls": {
            "undo_calls_to_created": undo_calls_to_created,
            "undo_calls_to_imported": undo_calls_to_imported,
            "redo_calls_to_created": redo_calls_to_created,
            "redo_calls_to_deleted": redo_calls_to_deleted,
        },
        "face_counts": {
            "as_imported": face_count_as_imported,
            "after_create": face_count_after_create,
            "after_delete": face_count_after_delete,
        },
        "undo_state_at_created": undo_state_at_created,
        "undo_state_at_imported": undo_state_at_imported,
    }
    R.check("full_lifecycle_undo_redo_export", ok, json.dumps(detail))


def main():
    R = h.Results(__file__)
    checks = [full_lifecycle_undo_redo_export]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
