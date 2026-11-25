# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/workflow_panels.py
"""Sidebar steps 2 Edit and 3 Save, and the collapsed "More Tools" group (B02).

Drawing only, plus two thin operators that chain existing ones: enter
facade editing (the "Tab, 3" gesture) and make the selected mesh CityGML
(ModelTyper auto assign). Step 1 Open is CGML3_PT_Main in the package
__init__.
"""

import os

import bpy
from bpy.types import Operator, Panel

from ..ops import browse_gml
from ..shared.lod_helpers import lod_promotion_root, top_level_lods
from ..shared.object_helpers import breadth_first_descendants
from . import colors, db_browse_panel
from .header_panel import ROOT_ID, on_tab

_SURFACES = ("WallSurface", "RoofSurface", "GroundSurface")
_OPENINGS = {"WindowSurface": "Window", "Window": "Window", "DoorSurface": "Door", "Door": "Door"}


def _space(context):
    return getattr(context, "space_data", None)


def _is_part(obj) -> bool:
    """A detachable part: BuildingPart / installation, or a child of a hierarchy empty."""
    parent = obj.parent
    return _feature(obj) in ("BuildingPart", "BuildingInstallation") or (
        parent is not None and parent.type == 'EMPTY' and parent.get("structure_type") == "hierarchical"
        and obj.get("structure_part") != "outer_shell")


def _feature(obj) -> str:
    return str(obj.get("cgml3_feature") or "") if obj is not None else ""


def _has_roof(root) -> bool:
    """A real (LoD2+) roof; the flat top of a LoD1 block does not count."""
    return max(top_level_lods(root) or [0]) >= 2 and any(
        _feature(o) == "RoofSurface" for o in breadth_first_descendants(root))


def roof_editable(root) -> bool:
    """B15: offer the roof gallery only where it makes sense: a building made here
    (it changes the roof) or one without a LoD2 roof yet. An imported roof stays."""
    return root.get("cgml_source") == "authored" or not _has_roof(root)


def _summary(root) -> list:
    """Two short lines: surfaces, then openings/installations (sidebar width)."""
    counts = {}
    for o in breadth_first_descendants(root):
        kind = _feature(o)
        kind = _OPENINGS.get(kind, kind)
        counts[kind] = counts.get(kind, 0) + 1
    groups = ((("BuildingPart", "part"),),
              (("WallSurface", "wall"), ("RoofSurface", "roof"), ("GroundSurface", "ground")),
              (("Window", "window"), ("Door", "door"), ("BuildingInstallation", "installation")))
    lines = [" · ".join(f"{counts[k]} {name}{'s' if counts[k] != 1 else ''}" for k, name in group if counts.get(k))
             for group in groups]
    return [line for line in lines if line] or ["one mesh, not split into surfaces"]


class CGML_OT_facade_edit_start(Operator):
    """Enter Edit Mode on the whole building with face select, ready for Window/Door (the 'Tab, 3' gesture)"""
    bl_idname = "cgml.facade_edit_start"
    bl_label = "Edit Facade"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = obj is not None and obj.type == 'MESH' and obj.mode == 'OBJECT' and bool(_feature(obj))
        if not ok:
            cls.poll_message_set("Select a CityGML building")
        return ok

    @classmethod
    def description(cls, context, props):
        from .tools import step_description
        return step_description(context, "edit_facade", cls.__doc__)  # B33

    def execute(self, context):
        root = lod_promotion_root(context.active_object)
        meshes = [o for o in [root, *breadth_first_descendants(root)]
                  if o.type == 'MESH' and o.visible_get()]
        for o in context.selected_objects:
            o.select_set(False)
        for o in meshes:
            o.select_set(True)
        context.view_layer.objects.active = root
        context.window_manager.cgml_panel_tab = 'EDIT'  # B96: Assign Surface Class lives there
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        try:  # B24: no CityGML tool before a target face is picked
            bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        except RuntimeError:
            pass  # no 3D view (scripted call)
        self.report({'INFO'}, "Click a wall face, then Window or Door in the City Modeler row")
        return {'FINISHED'}


class CGML_OT_make_citygml(Operator):
    """Give the selected plain meshes CityGML semantics (ModelTyper auto assign: wall/roof/ground from geometry)"""
    bl_idname = "cgml.make_citygml"
    bl_label = "Make City Object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        context.scene.cgml3_modeltyper_props.assignment_mode = 'SELECTION'
        return bpy.ops.cgml3_modeltyper.auto_assign()


class CGML3_PT_Edit(Panel):
    bl_label = "Edit"
    bl_idname = "CGML3_PT_Edit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = ROOT_ID
    bl_order = 2
    bl_options = {'HIDE_HEADER'}  # B96: the tab above already names the step

    @classmethod
    def poll(cls, context):
        return on_tab(context, "EDIT")

    def draw(self, context):
        """B21: an identity card of the selection and the one or two buttons that
        help right now. B24: making things (new building, roof, facade, window ...)
        is the City Modeler row above the viewport, in LoD order."""
        layout = self.layout
        obj = context.active_object
        if obj is None:
            layout.label(text="Select a building to edit it", icon='RESTRICT_SELECT_OFF')
            self._draw_show_picked(layout)
        elif not _feature(obj):
            self._draw_plain(layout, context, obj)
        else:
            root = lod_promotion_root(obj)
            self._draw_card(layout, obj, root)
            if obj.mode != 'EDIT':
                self._draw_actions(layout, context, obj, root)
        layout.label(text="Build: City Modeler row above the viewport", icon='TOOL_SETTINGS')

    def _draw_show_picked(self, layout):
        """B13: picked preview boxes are not editable yet; Show them first."""
        s = browse_gml.picked_session()
        ids = browse_gml.show_op_ids(s)
        n = len(s.selected - s.loaded) if s is not None else 0
        if ids and n:
            row = layout.row()
            row.scale_y = 1.3
            colors.add(row, ids[0], text=f"Show {n} Picked to Edit (S)", icon='HIDE_OFF').mode = 'HIGHEST'

    def _draw_plain(self, layout, context, obj):
        box = layout.box()
        box.label(text=f"{obj.name}: not a city object yet", icon='COLLECTION_COLOR_03')
        if obj.type != 'MESH':
            box.label(text="Select a mesh to turn it into a city object", icon='INFO')
            return
        props = getattr(context.scene, "cgml3_modeltyper_props", None)
        if props is not None:
            box.prop(props, "feature_type", text="Type")
        row = box.row()
        row.scale_y = 1.3
        colors.add(row, "cgml.make_citygml", icon='MOD_BUILD')

    def _draw_card(self, layout, obj, root):
        box = layout.box()
        col = box.column(align=True)
        if root.get("cgml_source") == "authored":
            source = "drawn here"
        else:
            source = {"citydb": "3DCityDB", "file": "file preview"}.get(root.get(browse_gml.SOURCE_KIND_PROP), "file")
        col.label(text=f"{_feature(root)}: {root.name}", icon='COLLECTION_COLOR_04')
        col.label(text=f"ID: {root.get('gml_id') or '(none yet)'}", icon='BLANK1')
        lods = top_level_lods(root)
        lod_text = "/".join(f"LoD{v}" for v in lods) if lods else "LoD unknown"
        col.label(text=f"{lod_text} · from {source}", icon='BLANK1')
        for line in _summary(root):
            col.label(text=line, icon='BLANK1')
        if obj is not root:
            col.separator()
            col.label(text=f"Selected: {_feature(obj)} {obj.name}", icon='RESTRICT_SELECT_OFF')
            col.label(text=f"Part of: {root.name}", icon='BLANK1')

    def _draw_actions(self, layout, context, obj, root):
        """At most two buttons. A window / door has none here: right-click and the tool header."""
        if _feature(obj) in _OPENINGS:
            return
        if len(context.selected_objects) >= 2:
            layout.menu("CGML3_MT_assign_object_part_context", text=f"Make Part of {obj.name}", icon='LINKED')
        if obj is not root and _is_part(obj):
            layout.operator("cgml3.detach_object_part", text="Detach (make independent)", icon='UNLINKED')
            return
        layout.operator("cgml.validate_building_openings", text="Check Openings", icon='CHECKMARK')


class CGML3_PT_AssignSurface(Panel):
    """B96: semantics are set here (right panel); the top row keeps the geometry operations."""
    bl_label = "Assign Surface Class"
    bl_idname = "CGML3_PT_AssignSurface"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Edit"
    bl_order = 5  # after the Attributes card

    def draw(self, context):
        from .tools import _step_button, draw_surface_settings
        layout = self.layout
        draw_surface_settings(context, layout)
        row = layout.row()
        row.scale_y = 1.3
        _step_button(row, context, "surface")


class CGML3_PT_Save(Panel):
    bl_label = "Save"
    bl_idname = "CGML3_PT_Save"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = ROOT_ID
    bl_order = 3
    bl_options = {'HIDE_HEADER'}  # B96: the tab above already names the step

    @classmethod
    def poll(cls, context):
        return on_tab(context, "SAVE")

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        if hasattr(wm, "cgml_export_format"):   # CJ-03: CityGML (3.0/2.0 in the dialog) or CityJSON 2.0
            layout.row(align=True).prop(wm, "cgml_export_format", expand=True)
        row = layout.row()
        row.scale_y = 1.3
        if getattr(wm, "cgml_export_format", 'CITYGML') == 'CITYJSON':
            colors.add(row, "cgml3.export_cityjson_file", text="Export CityJSON File...", icon='EXPORT')
        else:
            colors.add(row, "cgml3.export_gml_file", text="Export CityGML File...", icon='EXPORT')
        # CJ-06: one button per CityJSON file the scene came from; writes the changes back into it
        from ..shared import cityjson_open as cjo
        from ..shared import georef as gr
        for path in cjo.sources(gr.world()):
            colors.add(layout, "cgml3.save_cityjson_source", text=f"Save into {os.path.basename(path)}...",
                       icon='FILE_REFRESH').filepath = path
        n = len(context.selected_objects)
        layout.label(text=f"Writes the {n} selected objects (with their parts)" if n
                     else "Writes the whole scene (nothing selected)", icon='INFO')
        row = layout.row(align=True)  # B55: CityGML polygons must be planar
        row.operator("cgml.check_planarity", icon='VIEW_ORTHO')
        row.operator("cgml.fix_planarity", icon='MOD_SMOOTH')
        row = layout.row(align=True)  # B60: closed shell, rings, fillings; CityDoctor when installed
        row.operator("cgml.check_geometry", icon='MESH_ICOSPHERE')
        row.operator("cgml.citydoctor_validate", icon='CHECKMARK')
        row.operator("cgml.citydoctor_help", text="", icon='QUESTION')  # B86: setup steps for this OS
        row = layout.row(align=True)  # B61: repairs only when pressed
        row.operator("cgml.repair_geometry", text="Split Bent Surfaces", icon='MOD_EDGESPLIT').code = 'SE_BS_UNFRAGMENTED'
        row.operator("cgml.repair_geometry", text="Weld T-Junctions", icon='AUTOMERGE_ON').code = 'GE_S_NOT_CLOSED'
        # B84: one box for the database: pending changes of the scene and one Commit that writes them
        db_browse_panel.draw_changes(layout, context)
        # expert path (citydb-tool options, permanent delete): writes at once and replaces, red
        colors.remove_row(layout).operator("cgml3.export_to_db", icon='SETTINGS',
                                           text="Advanced: replace same IDs...")


class CGML3_PT_ExportOptions(Panel):
    bl_label = "Export Options"
    bl_idname = "CGML3_PT_ExportOptions"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Save"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        cg = context.scene.cgml3
        col = layout.column()
        col.prop(cg, "xsd_validate", text="XSD Validation")
        sub = col.row()
        sub.enabled = cg.xsd_validate
        sub.prop(cg, "xsd_lenient_validation", text="Tolerant (3DCityDB)")
        col.prop(cg, "export_schema_location_mode", text="schemaLocation")
        if context.scene.get("cgml3_last_export_streaming", False):
            col.label(text="Validation is skipped during streaming export", icon='INFO')


class CGML3_PT_Tools(Panel):
    bl_label = "More Tools"
    bl_idname = "CGML3_PT_Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = ROOT_ID
    bl_order = 10
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='TOOL_SETTINGS')

    def draw(self, context):
        self.layout.label(text="Specialist tools; most work needs only steps 1-3.", icon='INFO')


class CGML_MT_object_context(bpy.types.Menu):
    """B21: every CityGML object action in one right-click submenu."""
    bl_label = "City Modeler"
    bl_idname = "CGML_MT_object_context"

    def draw(self, context):
        from ..ops import assign_object_part, facade_authoring, join_object_parts
        if _feature(context.active_object) in _OPENINGS:
            self.layout.operator("cgml.opening_array", icon='MOD_ARRAY')
        for fn in (facade_authoring.draw_semantic_stamp_source_context_menu,
                   facade_authoring.draw_align_objects_context_menu,
                   facade_authoring.draw_delete_feature_context_menu,
                   join_object_parts.draw_join_object_parts_context_menu,
                   assign_object_part.draw_assign_object_part_context_menu):
            fn(self, context)


def draw_object_context_menu(self, context):
    """Object right-click: 'CityGML >' when a CityGML object (or meshes to join) is selected."""
    from ..ops import join_object_parts
    if context.mode != 'OBJECT':
        return
    if not (_feature(context.active_object) or join_object_parts._selected_source_meshes(context)):
        return
    self.layout.separator()
    self.layout.menu(CGML_MT_object_context.bl_idname, icon='HOME')


operator_classes = (CGML_OT_facade_edit_start, CGML_OT_make_citygml, CGML_MT_object_context)
# Parents before children: CGML3_PT_Tools must be registered before the panels that attach to it.
panel_classes = (CGML3_PT_Edit, CGML3_PT_AssignSurface, CGML3_PT_Save, CGML3_PT_ExportOptions, CGML3_PT_Tools)
