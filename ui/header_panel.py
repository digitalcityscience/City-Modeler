"""Top of the CityGML panels in the Properties editor's Tool tab (B02, B89, B90).

- ``draw_title``: the "City Modeler" title, prepended to Blender's own active-tool panel so it
  is the first thing in the tab, with a caption that says what the tool name below it is.
  That panel is hidden while the Copy / Paste tool has nothing copied (``copy_panel_hidden``);
  the status strip then draws the title itself.
- ``VCS3DCityDB_PT_HeaderPanel``: the status strip (scene features, CRS, 3DCityDB target) and
  the parent of the steps 1 Open, 2 Edit, 3 Save, More Tools. It has no header: Blender lays
  header-less panels out first, so the steps stay above Blender's own Options and Workspace
  panels. It hides in the 3D view's sidebar: the panels live in one place only.
"""

import re

import bpy
from ..shared import georef as gr

ROOT_ID = "VCS3DCITYDB_PT_header"


def scene_epsg(scene):
    """``"EPSG:<code>"`` from World["cgml_CRS"], or ``None``."""
    world = gr.world()
    match = re.search(r"EPSG[:\s]*(\d+)", str(world.get(gr.CRS, "")), re.IGNORECASE) if world else None
    return f"EPSG:{match.group(1)}" if match else None


def top_level_feature_count(scene) -> int:
    return sum(1 for o in scene.objects
               if o.get("cgml3_feature") and (o.parent is None or not o.parent.get("cgml3_feature")))


TABS = (("OPEN", "Open", "Get buildings from a file or 3DCityDB"),
        ("EDIT", "Edit", "The selection, its semantics and attributes"),
        ("SAVE", "Save", "Check, export and send to 3DCityDB"))


def on_tab(context, tab) -> bool:
    """B96: poll of a step panel; True while its tab is the picked one."""
    return getattr(context.window_manager, "cgml_panel_tab", tab) == tab


def in_sidebar(context) -> bool:
    """True when the panel is asked for in the 3D view's sidebar (N), not in the Properties editor."""
    return context.area is not None and context.area.type == 'VIEW_3D'


def _draw_title(layout):
    """Plain title: the icon and name, the subtitle greyed out below, a gap before what follows."""
    row = layout.row()
    row.scale_y = 1.3
    row.label(text="City Modeler", icon='HOME')
    row = layout.row()
    row.active = False
    row.label(text="3D city models · CityGML · 3DCityDB")
    layout.separator()


def copy_panel_hidden(context):
    """True while the active tool is Copy / Paste and nothing is copied: Blender's active-tool panel
    would then only say "Nothing copied yet", so it stays away until there is a copy to show."""
    try:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        if tool is None or tool.idname != "cgml.stamp_tool":
            return False
        from .tools import _copied
        return _copied(context) is None
    except Exception:  # noqa: BLE001 - the panel must never fail to draw; show it
        return False


def draw_title(self, context):
    if in_sidebar(context):
        return
    _draw_title(self.layout)
    self.layout.label(text="Active tool (pick one in the City Modeler row above the viewport):")


class VCS3DCityDB_PT_HeaderPanel(bpy.types.Panel):
    bl_label = "City Modeler"
    bl_idname = ROOT_ID
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_order = 0
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return not in_sidebar(context)

    def draw(self, context):
        from .citydb_panel import connection_status

        if copy_panel_hidden(context):  # the title normally sits in the active-tool panel, which is hidden now
            _draw_title(self.layout)
        col = self.layout.column(align=True)
        n = top_level_feature_count(context.scene)
        if n:
            col.label(text=f"Scene: {n} city object{'s' if n != 1 else ''}", icon='COLLECTION_COLOR_05')
        else:
            col.label(text="Scene: nothing open yet", icon='COLLECTION_COLOR_08')
        epsg = scene_epsg(context.scene)
        col.label(text=f"CRS: {epsg}" if epsg else "CRS: not set yet",
                  icon='COLLECTION_COLOR_04' if epsg else 'COLLECTION_COLOR_03')
        text, icon = connection_status(context)
        col.label(text=f"3DCityDB: {text}", icon=icon)
        from .colors import LEGEND
        col.label(text=LEGEND, icon='INFO')
        row = self.layout.row(align=True)  # B96: the steps are tabs; only the picked one draws
        row.scale_y = 1.2
        row.prop(context.window_manager, "cgml_panel_tab", expand=True)


# The header-less copy of the active-tool panel is the one the Properties editor shows first.
TOOL_PANEL = "VIEW3D_PT_active_tool_duplicate"


_blender_poll = []  # Blender's own poll of that panel, kept to put it back


def _poll_without_empty_copy(cls, context):
    return not copy_panel_hidden(context) and _blender_poll[0].__func__(cls, context)


def register():
    bpy.types.WindowManager.cgml_panel_tab = bpy.props.EnumProperty(
        name="Step", items=[(k, label, tip) for k, label, tip in TABS], default="OPEN")
    bpy.utils.register_class(VCS3DCityDB_PT_HeaderPanel)
    tool_panel = getattr(bpy.types, TOOL_PANEL, None)
    if tool_panel is not None:
        tool_panel.prepend(draw_title)
        if not _blender_poll and isinstance(tool_panel.__dict__.get("poll"), classmethod):
            _blender_poll.append(tool_panel.__dict__["poll"])
            tool_panel.poll = classmethod(_poll_without_empty_copy)


def unregister():
    tool_panel = getattr(bpy.types, TOOL_PANEL, None)
    if tool_panel is not None:
        tool_panel.remove(draw_title)
        if _blender_poll:
            tool_panel.poll = _blender_poll.pop()
    bpy.utils.unregister_class(VCS3DCityDB_PT_HeaderPanel)
    del bpy.types.WindowManager.cgml_panel_tab
