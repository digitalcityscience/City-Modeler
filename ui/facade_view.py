"""B68: look straight at the selected wall face, so windows and doors are drawn at 90°.

Face Wall turns the 3D view orthographic, upright (world Z up), looking at the
selected face from outside, framed, with textures shown in Solid shading. Run
it again (Shift+F, or the build row's Face Wall button) to get your own view
back; leaving Edit Mode (Done) brings it back too.

Auto (build row; B81: on by default again, the user wants window / door / balcony at 90° at once): with a facade tool (Window, Door, Paste, B70 Balcony) active,
selecting one wall face turns the view to it at once (B71: never a roof; Shift+F still does). A face in the plane already
faced does not move the view (a drawn window must not re-zoom it).
"""

import bmesh
import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

FACADE_TOOLS = ("cgml.window_tool", "cgml.door_tool", "cgml.stamp_tool",  # B70: same for every facade element
                "cgml.balcony_tool")  # B71: not the chimney: a roof is not turned to (Shift+F still does)
# saved: the view before facing (area pointer + view + shading); plane: the plane faced last
_state = {"saved": None, "plane": None}
_keymaps = []


def face_rotation(normal):
    """View rotation looking at a face with outward ``normal`` from outside, world Z up
    (world Y up for a flat face, e.g. a roof seen from above)."""
    z = Vector(normal).normalized()
    up = Vector((0.0, 0.0, 1.0)) if abs(z.z) < 0.99 else Vector((0.0, 1.0, 0.0))
    x = up.cross(z).normalized()
    return Matrix((x, z.cross(x), z)).transposed().to_quaternion()


def selected_face(obj):
    """(world normal, plane key) of the one selected face of ``obj`` in Edit Mode, else None.
    The key is the same for coplanar faces. (A BMFace dies with its BMesh wrapper, so no face out.)"""
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT' or obj.data.total_face_sel != 1:
        return None
    bm = bmesh.from_edit_mesh(obj.data)
    face = bm.faces.active
    if face is None or not face.select:
        face = next((f for f in bm.faces if f.select), None)
    if face is None:
        return None
    n = (obj.matrix_world.inverted_safe().transposed().to_3x3() @ face.normal).normalized()
    c = obj.matrix_world @ face.calc_center_median()
    return n, (round(n.x, 2), round(n.y, 2), round(n.z, 2), round(n.dot(c), 1))


def is_facing():
    return _state["saved"] is not None


def _view3d(context):
    """(window, area, WINDOW region) to turn: the context's own 3D view, else the largest one."""
    if context.area is not None and context.area.type == 'VIEW_3D':
        region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        return context.window, context.area, region
    best = None
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D' and (best is None or area.width * area.height > best[1].width * best[1].height):
                best = (win, area, next((r for r in area.regions if r.type == 'WINDOW'), None))
    return best or (None, None, None)


def face_selected(context):
    """Turn the 3D view to the selected face. False (nothing changed) without one face or a 3D view."""
    face = selected_face(context.view_layer.objects.active)
    win, area, region = _view3d(context)
    if face is None or region is None:
        return False
    normal, plane = face
    space = area.spaces.active
    rv3d = space.region_3d
    if _state["saved"] is None:  # facing another face keeps the view from before the first one
        _state["saved"] = {"area": area.as_pointer(), "rot": rv3d.view_rotation.copy(),
                           "loc": rv3d.view_location.copy(), "dist": rv3d.view_distance,
                           "persp": rv3d.view_perspective, "color": space.shading.color_type}
    _state["plane"] = plane
    rv3d.view_rotation = face_rotation(normal)
    rv3d.view_perspective = 'ORTHO'
    if space.shading.type == 'SOLID':
        space.shading.color_type = 'TEXTURE'  # the facade photo to draw on
    with context.temp_override(window=win, area=area, region=region):
        bpy.ops.view3d.view_selected()
    return True


def restore(context):
    """Back to the view (and shading colour) from before facing. False when not facing."""
    saved, _state["saved"], _state["plane"] = _state["saved"], None, None
    if saved is None:
        return False
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.as_pointer() == saved["area"]:  # the area may be gone; never keep its RNA
                space = area.spaces.active
                rv3d = space.region_3d
                rv3d.view_rotation = saved["rot"]
                rv3d.view_location = saved["loc"]
                rv3d.view_distance = saved["dist"]
                rv3d.view_perspective = saved["persp"]
                space.shading.color_type = saved["color"]
                area.tag_redraw()
    return True


class CGML_OT_face_wall(bpy.types.Operator):
    """Look straight at the selected wall face (orthographic, upright) to draw windows and doors at 90°.
Again: back to your own view"""
    bl_idname = "cgml.face_wall"
    bl_label = "Face Wall"

    @classmethod
    def poll(cls, context):
        if is_facing() or selected_face(context.view_layer.objects.active) is not None:
            return True
        cls.poll_message_set("Edit Facade, then select one wall face")
        return False

    def execute(self, context):
        if restore(context) or face_selected(context):
            return {'FINISHED'}
        self.report({'WARNING'}, "No 3D view to turn")
        return {'CANCELLED'}


def _facade_tool_active(context):
    for win in context.window_manager.windows:
        tool = win.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
        if tool is not None and tool.idname in FACADE_TOOLS:
            return True
    return False


def corner_balcony(context):
    """B92: the Balcony tool is active with the Corner type: it wraps around the building corner,
    which cannot be drawn looking straight at one wall, so Auto does not turn the view for it."""
    for win in context.window_manager.windows:
        tool = win.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
        if tool is not None and tool.idname == "cgml.balcony_tool":
            return tool.operator_properties("cgml.balcony_draw").kind == 'CORNER'
    return False


def _auto_wanted(context):
    """Auto is on, a facade tool is active and one face off the faced plane is selected."""
    if (not getattr(context.window_manager, "cgml_face_wall_auto", False) or not _facade_tool_active(context)
            or corner_balcony(context)):
        return False
    face = selected_face(context.view_layer.objects.active)
    return face is not None and abs(face[0].z) < 0.7 and face[1] != _state["plane"]  # B71: walls only


def auto_face(context):
    """Face a newly selected face when Auto is on and a facade tool is active."""
    return _auto_wanted(context) and face_selected(context)


def _deferred_auto():
    try:
        auto_face(bpy.context)
    except Exception as exc:  # a view nicety must never break editing
        print(f"[CityGML] face wall failed: {exc}")


def _deferred_restore():
    try:
        if bpy.context.mode != 'EDIT_MESH':  # still out of Edit Mode: it was Done / Tab, not a placement
            restore(bpy.context)
    except Exception as exc:  # a view nicety must never break editing
        print(f"[CityGML] face wall restore failed: {exc}")


@persistent
def _on_depsgraph_update(scene, depsgraph):
    context = bpy.context
    if context.mode != 'EDIT_MESH':
        # Balcony / entrance door placement dips into Object Mode and back inside one operator; restoring
        # now zoomed out in the middle of it. A timer runs after the operator, when Edit Mode is back.
        if is_facing() and not bpy.app.timers.is_registered(_deferred_restore):
            bpy.app.timers.register(_deferred_restore, first_interval=0.1)
        return
    if _auto_wanted(context) and not bpy.app.timers.is_registered(_deferred_auto):
        bpy.app.timers.register(_deferred_auto, first_interval=0.0)  # no operators inside a depsgraph handler


def _remove_handler():
    lst = bpy.app.handlers.depsgraph_update_post
    for fn in [f for f in lst if getattr(f, "__name__", "") == "_on_depsgraph_update"
               and getattr(f, "__module__", "") == __name__]:
        lst.remove(fn)  # also a stale copy from a module reload


def register():
    bpy.utils.register_class(CGML_OT_face_wall)
    bpy.types.WindowManager.cgml_face_wall_auto = bpy.props.BoolProperty(
        name="Auto Face Wall", default=True,  # B81: on (B73 had it off); Shift+F / Face Wall still toggle
        description="With a facade tool (Window, Door, Balcony, Paste), selecting one wall face turns "
                    "the view to face it")
    _remove_handler()
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    kc = getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None)
    if kc is not None and kc.addon is not None:  # none in --background
        km = kc.addon.keymaps.new(name='Mesh', space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new(CGML_OT_face_wall.bl_idname, 'F', 'PRESS', shift=True)))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    _remove_handler()
    del bpy.types.WindowManager.cgml_face_wall_auto
    bpy.utils.unregister_class(CGML_OT_face_wall)
