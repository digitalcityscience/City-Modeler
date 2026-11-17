"""AB-16: the edit-exit auto-sync handler is not @persistent (CAC-P3.0.2).

Registers, as a strict xfail, the pre-existing bug that
``ops/edit_exit_validation.py::register()`` appends ``_on_depsgraph_update``
to ``bpy.app.handlers.depsgraph_update_post`` without ``@persistent``
(l.460-463). Blender clears non-persistent handlers on ``wm.open_mainfile``
(File > Open, reload), so after a file load, edit-exit auto-sync for legacy
(no-record) Windows is silently off until the add-on is disabled and
re-enabled. Found during CAC-P3.0; registered here, not fixed - it is
likely superseded by the Phase 4 explicit edit/commit lifecycle.

Desired behaviour (what this test asserts, currently false):
after ``wm.open_mainfile``, ``_on_depsgraph_update`` is still registered and
an Edit-Mode move of a legacy Window still auto-syncs its wall's interior
ring on exiting Edit Mode.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_edit_exit_persistence.py -- <out_dir>
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _draw_window_via_operator():
    import bpy

    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )
    # AB-17 fix: (0.5, 0.5) lands outside this fixture's real solid area
    # (Railway arched cut-out) - find a position that actually places.
    position_uv = h.solid_area_position_uv(wall, face_index, ctx, 0.2, 0.2)
    fill = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.4, height=0.4, depth=0.05),
        ctx, PlacementRequest(position_uv=position_uv, source="manual_draw"),
    )
    bpy.context.view_layer.update()
    return wall, fill


def _ring_world_center(wall, opening):
    from mathutils import Vector
    from city_modeler.shared.ring_opening_sync import pairs_for_wall_children

    pair = next((p for p in pairs_for_wall_children(wall) if p.opening is opening), None)
    assert pair is not None, "expected a ring/opening pair for this wall/opening"
    face = wall.data.polygons[pair.ring_face_index]
    mw = wall.matrix_world
    total = Vector((0.0, 0.0, 0.0))
    for vi in face.vertices:
        total += mw @ wall.data.vertices[vi].co
    return total / len(face.vertices)


def _world_center(obj):
    from mathutils import Vector

    mw = obj.matrix_world
    total = Vector((0.0, 0.0, 0.0))
    for v in obj.data.vertices:
        total += mw @ v.co
    return total / len(obj.data.vertices)


def _edit_translate(obj, delta):
    import bmesh
    import bpy

    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.co += delta
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")


def handler_survives_reload():
    """After a real file load, is _on_depsgraph_update still registered?"""
    import bpy
    from city_modeler.ops.edit_exit_validation import _on_depsgraph_update

    out = h.out_dir()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, fill = _draw_window_via_operator()
    blend_path = str(out / "cac_p3_edit_exit_persistence.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    registered_before = _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    registered_after = _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post

    ok = registered_before and registered_after
    return ok, f"registered before open_mainfile={registered_before}, after={registered_after}"


def edit_exit_sync_survives_reload():
    """After a real file load, does moving a legacy Window in Edit Mode
    still recut its wall's interior ring on exiting Edit Mode?"""
    import bpy
    from mathutils import Vector
    from city_modeler.ops import edit_exit_validation as eev

    out = h.out_dir()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall_name_marker, fill = _draw_window_via_operator()
    wall_gml_id = str(wall_name_marker.get("gml_id") or "")
    blend_path = str(out / "cac_p3_edit_exit_persistence_sync.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    bpy.ops.wm.open_mainfile(filepath=blend_path)
    eev.reset_state()

    wall = next(o for o in bpy.data.objects
                if o.type == "MESH" and str(o.get("gml_id") or "") == wall_gml_id)
    fill = next(o for o in bpy.data.objects if o.parent is wall and o.type == "MESH")

    ring_before = _ring_world_center(wall, fill)
    fill_before = _world_center(fill)
    delta = Vector((0.3, 0.0, 0.0))
    _edit_translate(fill, delta)
    fill_after = _world_center(fill)
    moved = (fill_after - (fill_before + delta)).length < 1e-6

    ring_after = _ring_world_center(wall, fill)
    ring_followed = (ring_after - (ring_before + delta)).length < 1e-4

    ok = moved and ring_followed
    return ok, (f"window moved={moved}; ring followed={ring_followed} "
                f"(ring_before={tuple(ring_before)}, ring_after={tuple(ring_after)}, delta={tuple(delta)})")


def main():
    R = h.Results(__file__)
    try:
        h.load_addon()
    except Exception:
        R.check("setup", False, "setup exception:\n" + "".join(traceback.format_exc().splitlines(True)[-8:]))
        R.finish()

    for name, fn in (
        ("handler_survives_reload", handler_survives_reload),
        ("edit_exit_sync_survives_reload", edit_exit_sync_survives_reload),
    ):
        try:
            ok, detail = fn()
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-8:])
        R.check(name, ok, detail)
    R.finish()


main()
