# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/browse_panel.py
"""Sidebar panel of Browse first (T08): index state, selection, Load / Unload."""

import os

import bpy
from bpy.types import Panel

from ..common import background_job
from ..ops import browse_gml
from . import colors


class CGML3_PT_Browse(Panel):
    bl_label = "File Browse"
    bl_idname = "CGML3_PT_Browse"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Main"

    @classmethod
    def poll(cls, context):
        # The "Browse File..." entry lives in step 1 Open; this shows the session.
        return browse_gml.active_session(context) is not None or bool(browse_gml.proxy_objects("file"))

    def draw_header(self, context):
        self.layout.label(icon='MESH_CUBE')

    def draw(self, context):
        layout = self.layout
        s = browse_gml.active_session(context)
        if s is None:
            col = layout.column(align=True)
            if browse_gml.proxy_objects("file"):
                col.label(text="Index preview found in this file", icon='INFO')
                col.operator("cgml3.browse_resume", icon='FILE_REFRESH')
            col.operator("cgml3.browse_gml_file", text="Browse File...", icon='VIEWZOOM')
            return

        box = layout.box()
        box.label(text=os.path.basename(s.path), icon='FILE')
        version = s.index.version if s.index.version != "unknown" else "?"
        crs = s.crs if s.crs and s.crs != "Unknown CRS" else "CRS unknown"
        box.label(text=f"CityGML {version} · {crs} · {len(s.entries):,} features")
        box.label(text=f"Preview: {s.preview()}")

        drv = background_job.current()
        if drv is not None and drv.job.name == browse_gml.JOB_NAME and not s.complete:
            done, total, text = drv.job.progress
            pct = 100.0 * done / total if total else 0.0
            row = box.row()
            row.label(text=f"Indexing {pct:.0f}% · {text}", icon='TIME')
            row.operator("cgml3.browse_cancel", text="", icon='CANCEL')
        elif s.complete:
            note = "from cache" if s.from_cache else f"{s.stats.get('index_s', 0.0):.1f} s"
            box.label(text=f"Index complete ({note})", icon='CHECKMARK')
            risks = s.index.risk_counts()
            for name, n in sorted(risks.items()):
                box.label(text=f"{name}: {n}", icon='ERROR')

        layout.label(text="Show / Unshow: in the Open tab above", icon='INFO')
        if s.last_load:
            layout.label(text=f"Last show: {s.last_load['features']} buildings · "
                              f"{s.last_load['bytes_read'] / 1024:.0f} KB · {s.last_load['seconds']:.2f} s")


def draw_show_unshow(layout, context, s) -> None:
    """B12/B15: picked outlines -> Show (real building) -> Unshow (back to the outline).

    Drawn in "1 · Open" (open by default). Colours follow one rule
    (``ui.colors``): blue adds / shows, red removes, grey selects or views.
    """
    box = layout.box()
    picking = browse_gml.CGML3_OT_browse_pick.running
    box.label(text=f"Preview: {os.path.basename(s.path)}", icon='MESH_GRID')
    box.operator("cgml3.browse_pick", text="Picking: On (clicks pick outlines)" if picking
                 else "Picking: Off (click to pick outlines)", icon='RESTRICT_SELECT_OFF', depress=picking)
    if not s.complete:
        box.label(text="Outlines are appearing while the file is indexed...", icon='TIME')
    n, lods, preview, risks = browse_gml.selection_summary(s)
    shown = len(s.loaded)
    if n == 0:
        box.label(text="Click an outline to pick a building" if picking
                  else "Turn picking on to pick buildings", icon='INFO')
    else:
        box.label(text=f"Picked: {n} · LoD {lods}", icon='CHECKBOX_HLT')
        for e in s.selected_entries()[:3]:
            box.label(text=e.gml_id + (f"  ({e.name})" if e.name else ""), icon='HOME')
        if n > 3:
            box.label(text=f"... and {n - 3} more", icon='BLANK1')
    for name, k in sorted(risks.items()):
        box.label(text=f"{name}: {k}", icon='ERROR')
    limit = browse_gml.load_limit(context)
    if limit and n > limit:
        box.label(text=f"Pick at most {limit} buildings (preferences)", icon='CANCEL')
    row = box.row(align=True)
    row.operator("cgml3.browse_frame_picked", text="Focus  F" if n else "Focus All  F", icon='ZOOM_SELECTED')
    sub = row.row(align=True)
    sub.enabled = n > 0
    sub.operator("cgml3.browse_select", text="Pick None", icon='X').action = 'CLEAR'
    to_show = len(s.selected - s.loaded)
    row = box.row(align=True)
    row.scale_y = 1.3
    row.enabled = s.complete and to_show > 0
    colors.add(row, "cgml3.browse_load", text=f"Show ({to_show})  S", icon='HIDE_OFF').mode = 'HIGHEST'
    sub = row.row(align=True)
    sub.scale_x = 0.8
    colors.add(sub, "cgml3.browse_load", text="LoD2").mode = 'LOD2'
    row = colors.remove_row(box)
    row.enabled = shown > 0
    row.operator("cgml3.browse_unload", text="Unshow  U", icon='HIDE_ON').all = False
    row.operator("cgml3.browse_unload", text=f"Unshow All ({shown})").all = True
    colors.remove_row(box).operator("cgml3.browse_close", text="Close Preview", icon='X')


classes = (CGML3_PT_Browse,)
