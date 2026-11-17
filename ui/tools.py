# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/tools.py
"""Left toolbar (T) tools, Bonsai ``BimTool`` pattern (B16/B18).

Pick a tool, click in the viewport; the tool's settings sit in the tool
header. ``draw_settings`` follows the selection: nothing CityGML selected ->
create settings, a CityGML window/door selected -> edit actions. The tools
only start operators that already exist, so they keep working when the
authoring core (CAC) takes over storage.

Window / Door (decision 1, 2026-11-05): select a wall face first, then draw.
In Edit Mode a click on the selected face starts ``cgml.facade_draw`` (click =
type size, drag = exact rectangle); any other click falls through to normal
selection. In Object Mode the tool selects and offers "Edit Facade".

Object Mode (B19): Roof, Balcony, Chimney: a click on a building runs the
operator with ``from_tool`` (it finds what was clicked); a click on nothing
selects. Balcony / Chimney show the B15 blue preview under the mouse; their
settings are the tool's operator properties (header), F9 still works.
Copy / Paste (B71, was Stamp): Copy (Object Mode, right-click too) remembers a
window, door, balcony or chimney; the Paste tool (last in the Edit row) puts a
copy where the selected face is clicked (windows / doors: until Esc).
Window / Door (B71): only a drag on the selected face draws; a click places nothing.
Balcony / Chimney in Edit Mode (B70) work like Window: a blue preview of the type
picked in the header gallery follows the mouse on the selected face (B71), a
drag places it (B73: a click places nothing; width / depth / railing in the header).
Surface type brush (B20, Edit Mode): a click (or a drag across faces) selects
the faces under the mouse; on release ``cgml3_modeltyper.manual_assign``
gives them the type picked in the header, once, as one undo step.
Icons are Blender's own (decision 3).
"""

import bpy
from bpy.types import WorkSpaceTool

from ..shared import modal_hint
from . import facade_view


OPENINGS = {"WindowSurface": "Window", "Window": "Window", "DoorSurface": "Door", "Door": "Door"}

# Blender's default click / box selection (bl_keymap_data km_3d_view_tool_select*).
SELECT_KEYMAP = (
    ("view3d.select_box", {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG'}, None),
    ("view3d.select_box", {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG', "shift": True},
     {"properties": [("mode", 'ADD')]}),
    ("view3d.select_box", {"type": 'LEFTMOUSE', "value": 'CLICK_DRAG', "ctrl": True},
     {"properties": [("mode", 'SUB')]}),
    ("view3d.select", {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("deselect_all", True)]}),
    ("view3d.select", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, {"properties": [("toggle", True)]}),
)


def _feature(obj):
    return str(obj.get("cgml3_feature") or "") if obj is not None else ""


def tool_active(context, idname):
    """True when ``idname`` is the active 3D-view tool in the current mode (lets an
    operator's poll accept a tool click before anything is selected)."""
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return False
    return tool is not None and tool.idname == idname


# --- hover preview (Balcony / Chimney) -------------------------------------------
_preview = {"pts": None, "tool": ""}
_draw_handle = None
_keymaps = []  # B98: (keymap, item) of the 1-4 hotkeys


class _OpProxy:
    """The tool's operator properties seen through the operator class, so the
    class's own ``candidate_at`` / ``boxes`` compute the preview (no copy)."""

    def __init__(self, cls, props):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "_props", props)

    def __getattr__(self, name):
        if name in self._props.bl_rna.properties.keys():
            return getattr(self._props, name)
        mod, op = self._cls.bl_idname.split(".")
        rna = getattr(getattr(bpy.ops, mod), op).get_rna_type().properties
        if name in rna.keys():  # B71: the Edit Facade tool's props lack the add operator's sizes
            prop = rna[name]
            return tuple(prop.default_array) if getattr(prop, "is_array", False) else prop.default
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


class CGML_OT_tool_hover(bpy.types.Operator):
    """Blue preview of what a click would place (Balcony / Chimney tool)"""
    bl_idname = "cgml.tool_hover"
    bl_label = "Tool Preview"
    bl_options = {'INTERNAL'}

    op: bpy.props.StringProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        _preview.update(overlay=None, shift=event.shift, mouse=(event.mouse_region_x, event.mouse_region_y))
        _preview.update(pts=preview_points(context, self.op, *_ray(context, event)))
        _preview["tool"] = getattr(context.workspace.tools.from_space_view3d_mode(context.mode, create=False),
                                   "idname", "")
        if context.area is not None:
            context.area.tag_redraw()
        return {'PASS_THROUGH'}


def _ray(context, event):
    import types
    from ..ops.installation_authoring import ray_under_mouse
    # shortcut: a fresh snap cache per move (only while snapping is on); keep one per tool if big scenes lag
    return ray_under_mouse(context, event, types.SimpleNamespace())


def preview_points(context, op, origin, direction):
    """Line points of what ``op`` would place for this ray with the tool's settings, or None."""
    from ..ops import installation_authoring as ia
    if op == "cgml.semantic_stamp":
        return _paste_preview(context, origin, direction)
    cls = {"cgml.balcony_add": ia.CGML_OT_balcony_add, "cgml.chimney_add": ia.CGML_OT_chimney_add,
           "cgml.balcony_draw": ia.CGML_OT_balcony_add, "cgml.chimney_draw": ia.CGML_OT_chimney_add}.get(op)
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    if cls is None or tool is None:
        return None
    if context.mode == 'EDIT_MESH':  # B71: Edit Facade: only on the selected face (a click elsewhere selects)
        from ..ops.facade_authoring import _selected_face, ray_hits_selected_face
        obj, face_index = _selected_face(context)
        if obj is None or not ray_hits_selected_face(obj, face_index, origin, direction):
            return None
        if op == "cgml.balcony_draw":  # B81: the drag decides the balcony; a hover ghost misled (like Window / Door)
            return None
    proxy = _OpProxy(cls, tool.operator_properties(op))
    try:
        cand = proxy.candidate_at(context, origin, direction)
        return ia.box_edges(proxy.boxes(cand)) if cand else None
    except Exception as exc:  # a preview must never break the click
        print(f"[CityGML] tool preview failed: {exc}")
        return None


def _paste_preview(context, origin, direction):
    """B74: what a Paste click on the selected face would place, before the first click."""
    from mathutils import Vector
    from mathutils.geometry import intersect_line_plane
    from ..ops import installation_authoring as ia
    from ..ops import facade_authoring as fa
    src = _copied(context)
    obj, face_index = fa._selected_face(context) if context.mode == 'EDIT_MESH' else (None, -1)
    if src is None or obj is None or not fa.ray_hits_selected_face(obj, face_index, origin, direction):
        return None
    try:
        if fa.is_pasteable_installation(src):
            _op, a, _why = ia.paste_args(context, src, origin, direction)
            if a is None:
                return None
            if src.get("cgml_semantic_type") == "Balcony":  # as the _FaceDraw boxes_for
                return ia.box_edges(ia.balcony_boxes(a["point"], a["normal"], a["kind"], a["width"], a["depth"],
                                                     a["railing"], mirror=a.get("corner_side") == 'START'))
            return ia.box_edges(ia.chimney_boxes(bpy.data.objects.get(a["host"]), a["point"], a["normal"],
                                                 a["size"], a["above_ridge"], a["kind"], a.get("depth")))
        surface = fa.resolve_surface_context_for_face(obj, face_index)
        if surface.semantic_type.lower() != "wallsurface":
            return None
        definition = fa.definition_from_source_object(src)
        hit = intersect_line_plane(origin, origin + direction, Vector(surface.origin_world),
                                   Vector(surface.normal_world))
        if hit is None:
            return None
        size = (definition.width, definition.height, definition.semantic_type)
        if _preview.get("shift"):  # B91: as the click will place it
            hit = fa.paste_snap(surface, surface.object_name, hit, *size)
        # B91: the alignment guides before the first click too (the Paste modal draws them after it)
        _preview["overlay"] = fa._wall_alignment_overlay(
            surface, surface.object_name, fa.surface_uv_from_world_point(surface, hit), *size)
        h = Vector(surface.horizontal_axis_world).normalized() * (definition.width / 2)
        v = Vector(surface.vertical_axis_world).normalized() * (definition.height / 2)
        c = [hit - h - v, hit + h - v, hit + h + v, hit - h + v]  # the stamp ghost's rectangle
        return [p[:] for i in range(4) for p in (c[i], c[(i + 1) % 4])]
    except Exception as exc:  # a preview must never break the click
        print(f"[CityGML] paste preview failed: {exc}")
        return None


def _draw_preview():
    pts = _preview["pts"]
    if not pts or not tool_active(bpy.context, _preview["tool"]):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    vp = gpu.state.viewport_get()
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    shader.bind()
    shader.uniform_float("viewportSize", (vp[2], vp[3]))
    shader.uniform_float("lineWidth", 2.5)
    shader.uniform_float("color", (0.2, 0.55, 1.0, 1.0))
    batch_for_shader(shader, 'LINES', {"pos": pts}).draw(shader)
    gpu.state.blend_set('NONE')


def _draw_preview_guides():
    """B91: the Paste preview's alignment guides (screen space, as the Paste modal draws them)."""
    overlay = _preview.get("overlay")
    if overlay is None or not _preview["pts"] or not tool_active(bpy.context, _preview["tool"]):
        return
    from ..shared.facade_alignment_overlay import draw_alignment_overlay
    draw_alignment_overlay(bpy.context, overlay, _preview.get("mouse"))


def selected_face_count(obj):
    """Selected faces of ``obj`` in Edit Mode (0 outside Edit Mode); live from the edit mesh."""
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return 0
    return obj.data.total_face_sel


def draw_opening_settings(kind, context, layout):
    """Tool header for the Window / Door tool."""
    obj = context.active_object
    if obj is not None and obj.mode == 'OBJECT' and _feature(obj) in OPENINGS:
        layout.label(text=f"This {OPENINGS[_feature(obj)].lower()}:", icon='RESTRICT_SELECT_OFF')
        layout.operator("cgml.opening_array", icon='MOD_ARRAY')
        layout.operator("cgml.set_semantic_stamp_source", text="Copy", icon='COPYDOWN')
        layout.operator("cgml.align_objects", icon='SNAP_ON')
        layout.operator("cgml.delete_feature", text="Delete", icon='TRASH')
        return
    wm = context.window_manager
    layout.prop(wm, f"cgml_{kind.lower()}_type", text="")  # the type name carries its size
    if obj is None or obj.mode != 'EDIT':
        if obj is not None and _feature(obj):
            layout.operator("cgml.facade_edit_start", text="Edit Facade", icon='EDITMODE_HLT')
        else:
            layout.label(text="Select a building first", icon='INFO')
    elif selected_face_count(obj) != 1:
        layout.label(text="Select a wall face first", icon='INFO')
    else:
        layout.label(text="Drag on the selected face to draw it", icon='MOUSE_LMB')


def _draw_keymap(kind):
    op = "cgml.door_draw" if kind == "Door" else "cgml.facade_draw"  # B33: F9 / undo name = the tool
    return (
        (op, {"type": 'LEFTMOUSE', "value": 'PRESS'},
         {"properties": [("semantic_type", kind), ("from_tool", True)]}),
    ) + SELECT_KEYMAP


class CityGMLTool(WorkSpaceTool):
    """Base of the CityGML tools (Bonsai ``BimTool``)."""
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_widget = None
    bl_keymap = SELECT_KEYMAP


class WindowToolEdit(CityGMLTool):
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "cgml.window_tool"
    bl_label = "Window"
    bl_description = "Draw a window on the selected wall face: select a face, then drag on it"
    bl_icon = "ops.mesh.inset"
    bl_keymap = _draw_keymap("Window")

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_opening_settings("Window", context, layout)


class DoorToolEdit(CityGMLTool):
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "cgml.door_tool"
    bl_label = "Door"
    bl_description = "Draw a door on the selected wall face: select a face, then drag on it"
    bl_icon = "ops.mesh.extrude_region_move"
    bl_keymap = _draw_keymap("Door")

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_opening_settings("Door", context, layout)


class WindowToolObject(WindowToolEdit):
    bl_context_mode = 'OBJECT'
    bl_keymap = SELECT_KEYMAP


class DoorToolObject(DoorToolEdit):
    bl_context_mode = 'OBJECT'
    bl_keymap = SELECT_KEYMAP


def _click_keymap(op, hover=False, shift=False):
    items = ((op, {"type": 'LEFTMOUSE', "value": 'PRESS'}, {"properties": [("from_tool", True)]}),)
    if shift:  # B91 Paste: Shift+click lines the copy up (off the selected face it passes on to select)
        items += ((op, {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, {"properties": [("from_tool", True)]}),)
    if hover:  # B91: also with Shift held (any), so the preview follows it
        items += (("cgml.tool_hover", {"type": 'MOUSEMOVE', "value": 'ANY', "any": True},
                   {"properties": [("op", op)]}),)
    return items + SELECT_KEYMAP


class RoofTool(CityGMLTool):
    bl_idname = "cgml.roof_tool"
    bl_label = "Roof"
    bl_description = "Put a roof on a building: pick the shape, click the building (one made here or without a roof)"
    bl_icon = "ops.mesh.bisect"
    bl_keymap = _click_keymap("cgml.roof_add")

    @classmethod
    def draw_settings(cls, context, layout, tool):
        wm = context.window_manager
        layout.template_icon_view(wm, "cgml_roof_choice", show_labels=True, scale=1.0, scale_popup=4.0)
        from ..shared.roof_shapes import ROOF_TYPES
        layout.label(text=ROOF_TYPES.get(wm.cgml_roof_choice, wm.cgml_roof_choice))
        layout.label(text="Click a building (made here or without a roof)", icon='MOUSE_LMB')


class BalconyTool(CityGMLTool):
    bl_idname = "cgml.balcony_tool"
    bl_label = "Balcony"
    bl_description = ("Add a balcony (BuildingInstallation): click a wall where its floor goes, "
                      "or a door / window to fit it")
    bl_icon = "ops.mesh.extrude_region_shrink_fatten"
    bl_keymap = _click_keymap("cgml.balcony_add", hover=True)

    @classmethod
    def draw_settings(cls, context, layout, tool):
        props = tool.operator_properties("cgml.balcony_add")
        layout.prop(props, "kind", text="")
        layout.prop(props, "railing")  # B67: width / depth come from the clicked wall (F9 to change)
        layout.label(text="Click a wall, door or window", icon='MOUSE_LMB')


class ChimneyTool(CityGMLTool):
    bl_idname = "cgml.chimney_tool"
    bl_label = "Chimney"
    bl_description = "Add a chimney (BuildingInstallation): click a roof where it goes; it rises above the ridge"
    bl_icon = "ops.mesh.primitive_cylinder_add_gizmo"
    bl_keymap = _click_keymap("cgml.chimney_add", hover=True)

    @classmethod
    def draw_settings(cls, context, layout, tool):
        props = tool.operator_properties("cgml.chimney_add")
        layout.prop(props, "size")
        layout.prop(props, "depth")
        layout.prop(props, "above_ridge")
        layout.label(text="Click a roof", icon='MOUSE_LMB')


def _face_keymap(op):
    """B70: a press on the selected face starts ``op`` (click / drag); elsewhere it selects.
    B71: the blue preview of the type follows the mouse on the selected face."""
    return _click_keymap(op, hover=True)


class BalconyToolEdit(BalconyTool):
    """B70: in Edit Facade the same way as Window / Door (Object Mode keeps the click-a-building tool)."""
    bl_context_mode = 'EDIT_MESH'
    bl_description = ("Add a balcony: select a wall face, then click it (drag for an exact size); "
                      "a door / window face: fitted to it")
    bl_keymap = _click_keymap("cgml.balcony_draw")  # B81: no hover ghost; the drag shows the balcony

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_installation_settings("balcony", context, layout, tool)


class ChimneyToolEdit(ChimneyTool):
    bl_context_mode = 'EDIT_MESH'
    bl_description = ("Add a chimney: select a roof face, drag its width and depth on it, "
                      "then move the mouse (or type) for its height")
    bl_keymap = _face_keymap("cgml.chimney_draw")

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_installation_settings("chimney", context, layout, tool)


def _copied(context):
    """B71: the object Copy remembered (window, door, balcony, chimney), or None."""
    return bpy.data.objects.get(str(context.scene.get("cgml_semantic_stamp_source") or ""))


def _kind(obj):
    """'window' / 'door' / 'balcony' / 'chimney' for a copyable object."""
    kind = _feature(obj)
    if kind == "BuildingInstallation":
        return str(obj.get("cgml_semantic_type") or "").lower()
    return "door" if kind in ("Door", "DoorSurface") else "window"


def draw_stamp_settings(context, layout):
    obj = context.active_object
    src = _copied(context)
    if obj is not None and obj.mode == 'OBJECT' and bpy.ops.cgml.set_semantic_stamp_source.poll():
        layout.operator("cgml.set_semantic_stamp_source", text=f"Copy This {_kind(obj).title()}", icon='COPYDOWN')
    layout.label(text=f"Copied: {src.name}" if src else "Nothing copied yet", icon='DUPLICATE')
    reason = step_reason(context, "stamp")
    if src is None:
        layout.label(text="Select a window, door, balcony or chimney in Object Mode, then Copy", icon='INFO')
    elif obj is None or obj.mode != 'EDIT':
        layout.label(text="Open Edit Facade (City Modeler row), select a face, then click it", icon='INFO')
    elif reason:
        layout.label(text=reason, icon='INFO')
    else:
        layout.label(text="Click on the selected face to paste", icon='MOUSE_LMB')
        if _kind(src) in ("window", "door"):
            layout.label(text="Hold Shift: line up with the wall's windows / doors", icon='EVENT_SHIFT')


class StampToolEdit(CityGMLTool):
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "cgml.stamp_tool"
    bl_label = "Paste"  # B71: was Stamp
    bl_description = "Paste what you copied (window, door, balcony, chimney): select a face, then click it"
    bl_icon = "ops.mesh.dupli_extrude_cursor"
    bl_keymap = _click_keymap("cgml.semantic_stamp", hover=True, shift=True)  # B74: preview before the first click

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_stamp_settings(context, layout)


class StampToolObject(StampToolEdit):
    bl_context_mode = 'OBJECT'
    bl_label = "Copy"  # B71: Object Mode copies, Edit Facade pastes
    bl_keymap = SELECT_KEYMAP


def sync_brush_version(context, obj):
    """Assign with the object's own feature type and CityGML version (B25: the list in
    the header is already the object's); keep the picked type (the props' update
    resets the type to the first entry)."""
    from ..modeltyper.ui.properties import brush_target
    props = context.scene.cgml3_modeltyper_props
    feature, version = brush_target(context)
    if (props.feature_type, props.citygml_version) != (feature, version):
        picked = props.category_enum
        props.feature_type = feature
        props.citygml_version = version
        try:
            props.category_enum = picked
        except TypeError:
            pass  # that type does not exist here: first entry stays


class CGML_OT_surface_brush(bpy.types.Operator):
    """Give the faces you click or drag across the surface type picked in the tool header"""
    bl_idname = "cgml.surface_brush"
    bl_label = "Assign Surface Class"  # B33: = the tool name
    bl_options = {'UNDO'}  # no F9: redo would re-run on the old selection

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'

    def _pick(self, context, event, extend):
        loc = (event.mouse_region_x, event.mouse_region_y)
        res = bpy.ops.view3d.select(extend=extend, deselect_all=not extend, location=loc)
        return 'FINISHED' in res

    @modal_hint.clears_status
    def invoke(self, context, event):
        if not self._pick(context, event, extend=False):
            return {'PASS_THROUGH'}  # nothing under the mouse: normal selection
        context.window_manager.modal_handler_add(self)
        props = getattr(context.scene, "cgml3_modeltyper_props", None)
        modal_hint.status(context, modal_hint.hint(("Drag", "More faces"),
                                                   ("Release", f"Assign {getattr(props, 'category_enum', '')}"),
                                                   ("Esc/RMB", "Cancel")))
        return {'RUNNING_MODAL'}

    @modal_hint.clears_status
    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self._pick(context, event, extend=True)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            modal_hint.status(context)
            return self.execute(context)
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            modal_hint.status(context)
            return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        modal_hint.status(context)

    def execute(self, context):
        sync_brush_version(context, context.active_object)
        return bpy.ops.cgml3_modeltyper.manual_assign()


def draw_surface_settings(context, layout):
    """Surface type picker of Assign Surface Class: the Edit tab of the right panel (B96) and the tool header."""
    from ..modeltyper.ui.properties import brush_target, object_feature_type
    props = getattr(context.scene, "cgml3_modeltyper_props", None)
    if props is None:
        return
    feature, version = brush_target(context)
    if object_feature_type(context.active_object):  # the object decides the list
        names = {"BUILDING": "Building", "TRANSPORTATION": "Transportation", "CITYFURNITURE": "CityFurniture"}
        layout.label(text=f"{names[feature]} · CityGML {version}")
    else:  # a plain mesh: say what it is meant to be
        layout.prop(props, "feature_type", text="")
    layout.prop(props, "category_enum", text="")
    from ..modeltyper.citygml import get_surface_types
    desc = get_surface_types(feature, version).get(props.category_enum, {}).get("description")
    if desc:
        layout.label(text=desc)
    layout.label(text="Click or drag across faces", icon='MOUSE_LMB')


class SurfaceBrushTool(CityGMLTool):
    bl_context_mode = 'EDIT_MESH'
    bl_idname = "cgml.surface_brush_tool"
    bl_label = "Assign Surface Class"
    bl_description = ("Give the faces you click or drag across a boundary-surface class "
                      "(WallSurface, RoofSurface ...)")
    bl_icon = "brush.paint_texture.fill"
    bl_keymap = (("cgml.surface_brush", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),) + SELECT_KEYMAP

    @classmethod
    def draw_settings(cls, context, layout, tool):
        draw_surface_settings(context, layout)


# (tool class, separator before it); grouped below Blender's own tools.
TOOLS = (
    (WindowToolEdit, True), (DoorToolEdit, False), (BalconyToolEdit, False),
    (ChimneyToolEdit, False), (SurfaceBrushTool, False), (StampToolEdit, False),
    (WindowToolObject, True), (DoorToolObject, False), (StampToolObject, False),
    (RoofTool, False), (BalconyTool, False), (ChimneyTool, False),
)


# --- CityGML build row (B24) ------------------------------------------------------
# Blender appends add-on tools to the bottom of the toolbar, where they scroll away.
# The build steps sit in the tool-settings row instead, in LoD order, as labelled
# buttons. A step is enabled only when its target is selected (a wall face for a
# window, a roof for a chimney ...); its tooltip says what is missing.
TOOL_IDS = {"roof": "cgml.roof_tool", "window": "cgml.window_tool", "door": "cgml.door_tool",
            "stamp": "cgml.stamp_tool", "balcony": "cgml.balcony_tool", "chimney": "cgml.chimney_tool",
            "surface": "cgml.surface_brush_tool"}
_LABELS = {"roof": "Roof", "window": "Window", "door": "Door", "stamp": "Paste", "balcony": "Balcony",
           "chimney": "Chimney", "surface": "Assign Surface Class"}


def _wall_face_reason(obj):
    """'' when the Edit-Mode selection is one face of a wall (or of a merged building,
    which the draw operator splits on the click), else what to do."""
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return "Edit Facade, then select one wall face"
    if obj.data.total_face_sel != 1:
        return "Select one wall face"
    kind = _feature(obj)
    if kind in ("WallSurface", "Building", "BuildingPart"):
        return ""
    return f"That face is a {kind}; select a wall face" if kind else "Not a wall; select a wall face"


def step_reason(context, key):
    """'' when step ``key`` can start now, else the hint shown as its tooltip."""
    from ..ops.installation_authoring import building_root
    obj = context.active_object
    kind = _feature(obj)
    mode = context.mode
    if key == "surface":
        return "" if mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH' else "Edit Facade first"
    if key in ("window", "door"):
        return _wall_face_reason(obj) if mode == 'EDIT_MESH' else "Edit Facade, then select one wall face"
    if key == "stamp":  # B71: Copy in Object Mode, Paste in Edit Facade
        src = _copied(context)
        if mode == 'OBJECT':
            if bpy.ops.cgml.set_semantic_stamp_source.poll():
                return ""  # copies it (B50: a window or a ring-paired door; B71 a balcony / chimney)
            return ("Edit Facade, then select a face to paste on" if src
                    else "Select the window, door, balcony or chimney to copy")
        if src is None:
            return "Copy first: Object Mode, select a window, door, balcony or chimney, Copy"
        if _kind(src) in ("balcony", "chimney"):
            from ..ops.installation_authoring import selected_face_placement
            return selected_face_placement(context, 'ROOF' if _kind(src) == "chimney" else 'WALL')[1]
        return _wall_face_reason(obj)
    if key in ("balcony", "chimney"):  # B28: on the face selected in Edit Facade
        what = "a wall, door or window face" if key == "balcony" else "a roof face"
        if mode != 'EDIT_MESH':
            return f"Edit Facade, then select {what}"
        from ..ops.installation_authoring import selected_face_placement
        _cand, reason = selected_face_placement(context, 'WALL' if key == "balcony" else 'ROOF')
        return reason
    if mode != 'OBJECT':
        return "Back to Object Mode (Done)"
    if key == "from_footprint":  # B33: a plain mesh to turn into a building
        if obj is None or obj.type != 'MESH':
            return "Select a flat mesh (the footprint)"
        if kind:
            return "That is already a CityGML object; select a plain mesh footprint"
        return "" if obj.data.polygons else "That mesh has no faces"
    if key == "edit_facade":
        return "" if obj is not None and obj.type == 'MESH' and kind else "Select a CityGML building"
    if key == "roof":
        root = building_root(obj)
        if root is None:
            return "Select a building"
        from .workflow_panels import roof_editable
        return "" if roof_editable(root) else "This building already has its imported LoD2 roof"
    return ""


def step_description(context, key, text):
    """Tooltip: ``text`` plus "Now: <what is missing>" while step ``key`` is greyed out."""
    reason = step_reason(context, key)
    return f"{text}.\nNow: {reason}" if reason else text


class CGML_OT_tool_pick(bpy.types.Operator):
    """Start a CityGML build step"""
    bl_idname = "cgml.tool_pick"
    bl_label = "Build Step"
    bl_options = {'INTERNAL'}

    key: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, props):
        tool = next((t for t, _s in TOOLS if t.bl_idname == TOOL_IDS.get(props.key)), None)
        text = tool.bl_description if tool is not None else cls.__doc__
        if context.mode == 'EDIT_MESH' and props.key in HOTKEYS:  # B98
            text += f" [key {list(HOTKEYS).index(props.key) + 1}]"
        return step_description(context, props.key, text)

    def execute(self, context):
        reason = step_reason(context, self.key)
        if reason:
            self.report({'WARNING'}, reason)
            return {'CANCELLED'}
        obj = context.active_object
        if self.key == "stamp" and context.mode == 'OBJECT' and bpy.ops.cgml.set_semantic_stamp_source.poll():
            bpy.ops.cgml.set_semantic_stamp_source()
        bpy.ops.wm.tool_set_by_id(name=TOOL_IDS[self.key])
        if self.key in ("window", "door", "stamp", "balcony"):  # B71: not the chimney (roof)
            facade_view.auto_face(context)  # B68: the face already selected turns to you
        return {'FINISHED'}


def _step_button(layout, context, key, text=None):
    sub = layout.row(align=True)
    sub.enabled = not step_reason(context, key)
    sub.operator(CGML_OT_tool_pick.bl_idname, text=text or _LABELS[key],
                 depress=tool_active(context, TOOL_IDS[key])).key = key


def _op_button(layout, context, key, idname):
    """B33: an operator step that is not a tool, gated like ``_step_button`` (its ``description``
    adds the "Now: ..." line); the button text is the operator's own label (= F9 / undo name)."""
    sub = layout.row(align=True)
    sub.enabled = not step_reason(context, key)
    sub.operator(idname)


class CGML_PT_roof_gallery(bpy.types.Panel):
    """B26: the row's Roof button: every AdV roof type, large and named. A click puts
    that roof on the selected building at once (one undo step); another click swaps it."""
    bl_idname = "CGML_PT_roof_gallery"
    bl_label = "Roof"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 22

    def draw(self, context):
        from .gallery import _icon
        from ..ops.installation_authoring import building_root
        from ..shared.roof_shapes import ROOF_TYPES
        root = building_root(context.active_object)
        current = root.get("bldg:roofType") if root is not None else None
        layout = self.layout
        layout.label(text="Pick a roof for the selected building")
        grid = layout.grid_flow(row_major=True, columns=5, even_columns=True, align=False)
        for code, name in ROOF_TYPES.items():
            col = grid.column(align=True)
            col.template_icon(icon_value=_icon(f"roof_{code}"), scale=4.0)
            col.operator("cgml.roof_add", text=name, depress=code == current).roof_type = code
        layout.separator()
        # a redo button here is greyed inside the popup; F9 works once it closes
        layout.label(text="Pitch and ridge direction: press F9 after choosing", icon='INFO')


def _active_tool(context):
    try:
        return context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    except Exception:
        return None


class CGML_OT_tool_type_set(bpy.types.Operator):
    """Use this type for the next drag on the selected face (starts the tool when it is not active)"""
    bl_idname = "cgml.tool_type_set"
    bl_label = "Element Type"
    bl_options = {'INTERNAL'}

    op: bpy.props.StringProperty()    # the tool's draw operator (Balcony / Chimney); "" for Window / Door
    kind: bpy.props.StringProperty()
    key: bpy.props.StringProperty()   # B98: window / door / balcony / chimney; from ``op`` when empty

    def execute(self, context):
        key = self.key or self.op.split(".")[1].split("_")[0]
        wm = context.window_manager
        if not tool_active(context, TOOL_IDS[key]):  # B98: the type is picked first, the tool follows
            bpy.ops.wm.tool_set_by_id(name=TOOL_IDS[key])
        if key in ("window", "door"):
            setattr(wm, f"cgml_{key}_type", self.kind)
        else:
            tool = _active_tool(context)
            if tool is None:
                return {'CANCELLED'}
            tool.operator_properties(self.op).kind = self.kind
        setattr(wm, f"cgml_search_{key}", "")
        if key == "balcony" and facade_view.corner_balcony(context):  # B92: corner: the 3D view, not 90°
            facade_view.restore(context)
        else:  # another type again: Auto faces the selected wall as usual
            facade_view.auto_face(context)
        return {'FINISHED'}


class _TypeGallery:
    """B28 galleries, B70 the type picker of the Edit Facade tools (tool header), B98 also Window / Door,
    one dropdown per tool in the City Modeler row, with a search field (a long list of window / door assets
    is coming): a type button sets the tool's type and starts the tool; the drag on the face places it."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 16
    OP, PREFIX, TYPES_NAME, HINT = "", "", "", ""

    @property
    def TYPES(self):
        from ..ops import installation_authoring
        return getattr(installation_authoring, self.TYPES_NAME)

    def draw(self, context):
        from .gallery import _icon, has_icon
        layout = self.layout
        wm = context.window_manager
        layout.label(text=self.HINT)
        layout.activate_init = True  # typing starts at once
        layout.prop(wm, f"cgml_search_{self.PREFIX}", text="", icon='VIEWZOOM')
        if self.OP:
            tool = _active_tool(context)
            current = tool.operator_properties(self.OP).kind if tool is not None else ""
        else:
            current = getattr(wm, f"cgml_{self.PREFIX}_type")
        found = getattr(wm, f"cgml_search_{self.PREFIX}").strip().lower()
        types = [t for t in self.TYPES if found in t[1].lower() or found in t[0].lower()]
        if not types:
            layout.label(text="No match")
        icons = any(has_icon(f"{self.PREFIX}_{t[0]}") for t in types)  # Window / Door: names only, for now
        grid = layout.grid_flow(row_major=True, columns=3 if icons else 1, even_columns=True, align=False)
        for ident, name, _desc in types:
            col = grid.column(align=True)
            if icons:
                col.template_icon(icon_value=_icon(f"{self.PREFIX}_{ident}"), scale=4.0)
            op = col.operator(CGML_OT_tool_type_set.bl_idname, text=name, depress=ident == current)
            op.op, op.kind, op.key = self.OP, ident, self.PREFIX


class CGML_PT_window_gallery(_TypeGallery, bpy.types.Panel):
    bl_idname = "CGML_PT_window_gallery"
    bl_label = "Window"
    PREFIX, HINT = "window", "Window type"

    @property
    def TYPES(self):
        from ..shared import opening_types
        return opening_types.items("Window")


class CGML_PT_door_gallery(_TypeGallery, bpy.types.Panel):
    bl_idname = "CGML_PT_door_gallery"
    bl_label = "Door"
    PREFIX, HINT = "door", "Door type"

    @property
    def TYPES(self):
        from ..shared import opening_types
        return opening_types.items("Door")


class CGML_PT_balcony_gallery(_TypeGallery, bpy.types.Panel):
    bl_idname = "CGML_PT_balcony_gallery"
    bl_label = "Balcony"
    OP, PREFIX, TYPES_NAME = "cgml.balcony_draw", "balcony", "BALCONY_TYPES"
    HINT = "Balcony type"


class CGML_PT_chimney_gallery(_TypeGallery, bpy.types.Panel):
    bl_idname = "CGML_PT_chimney_gallery"
    bl_label = "Chimney"
    OP, PREFIX, TYPES_NAME = "cgml.chimney_draw", "chimney", "CHIMNEY_TYPES"
    HINT = "Chimney type"


GALLERIES = {"window": CGML_PT_window_gallery, "door": CGML_PT_door_gallery,
             "balcony": CGML_PT_balcony_gallery, "chimney": CGML_PT_chimney_gallery}
HOTKEYS = {"window": 'ONE', "door": 'TWO', "balcony": 'THREE', "chimney": 'FOUR'}


class CGML_OT_tool_key(bpy.types.Operator):
    """B98: Edit Facade hotkeys 1 Window, 2 Door, 3 Balcony, 4 Chimney start that tool"""
    bl_idname = "cgml.tool_key"
    bl_label = "Start Tool"
    bl_options = {'INTERNAL'}

    key: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):  # a plain mesh keeps Blender's 1 / 2 / 3 select modes
        return context.mode == 'EDIT_MESH' and bool(_feature(context.active_object))

    def execute(self, context):
        bpy.ops.wm.tool_set_by_id(name=TOOL_IDS[self.key])
        facade_view.auto_face(context)
        return {'FINISHED'}


def draw_installation_settings(key, context, layout, tool):
    """B70: Edit Facade Balcony / Chimney tool header, like Window / Door: type, size, what to click."""
    panel = CGML_PT_balcony_gallery if key == "balcony" else CGML_PT_chimney_gallery
    from ..ops import installation_authoring
    names = {i: n for i, n, _d in getattr(installation_authoring, panel.TYPES_NAME)}
    if tool is not None:
        props = tool.operator_properties(panel.OP)
        layout.popover(panel=panel.bl_idname, text=names.get(props.kind, props.kind))
        for name in (("width", "depth", "railing") if key == "balcony" else ("size", "depth", "above_ridge")):
            layout.prop(props, name)
    reason = step_reason(context, key)
    if reason:
        layout.label(text=reason, icon='INFO')
    else:
        text = "Drag the size on the selected face, then the height" if key == "chimney" else             "Drag on the selected face to place it"  # B73: a click places nothing; B80: chimney height after
        layout.label(text=text, icon='MOUSE_LMB')


def _lod_label(layout, n, lod):
    layout.label(text=f"LoD{n}", icon='CHECKMARK' if lod >= n else 'BLANK1')


def draw_build_row(self, context):
    """Prepended to the 3D view's tool-settings row (Object and Edit Mode)."""
    if context.mode not in ('OBJECT', 'EDIT_MESH'):
        return
    from ..ops.installation_authoring import building_root
    from ..shared.lod_helpers import top_level_lods
    root = building_root(context.active_object)
    lod = max(top_level_lods(root) or [0]) if root is not None else -1
    edit = context.mode == 'EDIT_MESH'
    layout = self.layout
    row = layout.row(align=False)
    row.label(text="City Modeler", icon='HOME')

    if edit:  # Edit Mode: only its steps, so the active tool's settings keep their room
        _lod_label(row, 3, lod)
        grp = row.row(align=True)
        grp.operator("object.mode_set", text="Done", icon='CHECKMARK').mode = 'OBJECT'
        for key in ("window", "door", "balcony", "chimney"):  # B70: all tools, one way
            _step_button(grp, context, key)
            grp.popover(panel=GALLERIES[key].bl_idname, text="", icon='DOWNARROW_HLT')  # B98: type first, with search
        face = row.row(align=True)  # B96: Assign Surface Class is in the right panel (Edit tab)  # B68: draw at 90°; Shift+F does the same
        face.operator(facade_view.CGML_OT_face_wall.bl_idname, icon='AXIS_FRONT', depress=facade_view.is_facing())
        auto = face.row(align=True)
        auto.active = not facade_view.corner_balcony(context)  # B92: grey: Auto skips a corner balcony
        auto.prop(context.window_manager, "cgml_face_wall_auto", text="Auto")
        _step_button(row, context, "stamp", "Paste")  # B71: last, what Copy remembered
        layout.separator()
        return

    _lod_label(row, 1, lod)
    grp = row.row(align=True)
    grp.operator_context = 'INVOKE_REGION_WIN'  # B27: draw in the viewport, not in the header
    _op_button(grp, context, "new_building", "cgml.building_new")
    _op_button(grp, context, "from_footprint", "cgml.building_from_footprint")

    _lod_label(row, 2, lod)
    if step_reason(context, "roof"):
        _step_button(row, context, "roof")  # grey, the tooltip says what is missing
    else:  # B26: a gallery straight on the selected building
        row.popover(panel=CGML_PT_roof_gallery.bl_idname, text="Roof")

    _lod_label(row, 3, lod)
    grp = row.row(align=True)
    _op_button(grp, context, "edit_facade", "cgml.facade_edit_start")
    for key in ("window", "door", "balcony", "chimney"):
        _step_button(grp, context, key)
    _step_button(row, context, "stamp", "Copy")  # B71: the selected window / door / balcony / chimney
    layout.separator()


def register():
    global _draw_handle
    bpy.utils.register_class(CGML_OT_tool_hover)
    bpy.utils.register_class(CGML_OT_surface_brush)
    bpy.utils.register_class(CGML_OT_tool_pick)
    bpy.utils.register_class(CGML_OT_tool_type_set)
    bpy.utils.register_class(CGML_OT_tool_key)
    bpy.utils.register_class(CGML_PT_roof_gallery)
    for cls in GALLERIES.values():
        bpy.utils.register_class(cls)
    for key in GALLERIES:  # B98: the search box of each type dropdown
        setattr(bpy.types.WindowManager, f"cgml_search_{key}",
                bpy.props.StringProperty(name="Search", description="Type part of a name", options={'TEXTEDIT_UPDATE'}))
    kc = getattr(getattr(bpy.context, "window_manager", None), "keyconfigs", None)
    if kc is not None and kc.addon is not None:  # none in --background
        km = kc.addon.keymaps.new(name='Mesh', space_type='EMPTY')
        for key, event in HOTKEYS.items():  # B98: Edit Facade: 1 Window, 2 Door, 3 Balcony, 4 Chimney
            kmi = km.keymap_items.new(CGML_OT_tool_key.bl_idname, event, 'PRESS')
            kmi.properties.key = key
            _keymaps.append((km, kmi))
    facade_view.register()
    bpy.types.VIEW3D_HT_tool_header.prepend(draw_build_row)
    _draw_handle = (bpy.types.SpaceView3D.draw_handler_add(_draw_preview, (), 'WINDOW', 'POST_VIEW'),
                    bpy.types.SpaceView3D.draw_handler_add(_draw_preview_guides, (), 'WINDOW', 'POST_PIXEL'))
    for cls, sep in TOOLS:
        try:
            bpy.utils.register_tool(cls, separator=sep)
        except Exception as exc:  # a stale registration from a reload
            print(f"[CityGML] tool {cls.bl_idname} ({cls.bl_context_mode}) not registered: {exc}")


def unregister():
    global _draw_handle
    bpy.types.VIEW3D_HT_tool_header.remove(draw_build_row)
    facade_view.unregister()
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    for key in GALLERIES:
        delattr(bpy.types.WindowManager, f"cgml_search_{key}")
    for cls in reversed(list(GALLERIES.values())):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(CGML_PT_roof_gallery)
    bpy.utils.unregister_class(CGML_OT_tool_key)
    bpy.utils.unregister_class(CGML_OT_tool_pick)
    bpy.utils.unregister_class(CGML_OT_tool_type_set)
    for cls, _sep in reversed(TOOLS):
        try:
            bpy.utils.unregister_tool(cls)
        except Exception:
            pass
    for handle in _draw_handle or ():
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _draw_handle = None
    bpy.utils.unregister_class(CGML_OT_surface_brush)
    bpy.utils.unregister_class(CGML_OT_tool_hover)
