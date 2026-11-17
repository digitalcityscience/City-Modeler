# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/db_browse_panel.py
"""Sidebar panel of DB Browse first (T09): query state, selection, Load, Load by ID."""

import bpy
from bpy.types import Panel

from ..citydb import changes
from ..common import background_job
from ..ops import browse_citydb, browse_gml, commit_citydb, update_citydb
from . import colors


class CGML3_PT_DBBrowse(Panel):
    bl_label = "3DCityDB Browse"
    bl_idname = "CGML3_PT_DBBrowse"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Main"

    @classmethod
    def poll(cls, context):
        sc = context.scene.cgml3
        return bool(sc.db_profile and sc.db_name)

    def draw_header(self, context):
        self.layout.label(icon='MESH_GRID')

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        sc = context.scene.cgml3
        if not sc.db_profile or not sc.db_name:
            layout.label(text="Pick a city model in the 3DCityDB panel", icon='INFO')
            return
        s = browse_citydb.active_session(context)

        box = layout.box()
        row = box.row()
        row.label(text=sc.db_target_label or sc.db_schema, icon='WORLD')
        if s is not None:
            row.operator("cgml3.db_browse_close", text="", icon='X')
        col = box.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.2
        drv = background_job.current()
        busy = drv is not None and drv.job.name in (browse_citydb.QUERY_JOB, browse_citydb.LOAD_JOB,
                                                     browse_citydb.FIND_JOB)
        importing = browse_citydb.import_running()
        row.operator("cgml3.db_browse_view", icon='VIEWZOOM',
                     text="Querying ..." if busy and drv.job.name == browse_citydb.QUERY_JOB else "Show Buildings in View")
        col.prop(wm, "cgml3_db_type_filter", text="Type")
        if busy:
            _done, _total, text = drv.job.progress
            r = box.row()
            r.label(text=text or drv.job.label, icon='TIME')
            r.operator("cgml3.browse_cancel", text="", icon='CANCEL')
        elif importing:
            run = browse_citydb._import_run
            box.label(text=f"Importing {len(run.steps)} / {len(run.steps) + len(run.queue)} buildings", icon='TIME')
            busy = True

        if s is None:  # write back lives in 3 · Save (B83; `_draw_update` did not exist here)
            _draw_advanced_import(layout)
            return
        q = s.last_query
        if q:
            approx = "~" if q.get("estimated") else ""
            box.label(text=f"Area {q.get('area', 0.0):.2f} km² · {approx}{q.get('count', 0):,} buildings in view")
            if "rows" in q:
                box.label(text=f"Shown: {len(s.rows):,} · preview: {s.preview()}")
                if q.get("more"):
                    sub = box.column(align=True)
                    sub.alert = True
                    sub.label(text=f"More than {q.get('limit', 0):,} - narrow the area or filter by type", icon='ERROR')
                    sub.operator("cgml3.db_browse_view", text="Next page", icon='TRIA_RIGHT').next_page = True
        if s.message and not busy:
            box.label(text=s.message[:120], icon='INFO')

        col = layout.column(align=True)
        picking = browse_gml.CGML3_OT_browse_pick.running
        col.operator("cgml3.browse_pick", text="Picking: On (clicks pick outlines)" if picking
                     else "Picking: Off (click to pick outlines)", icon='RESTRICT_SELECT_OFF', depress=picking)
        col.operator("cgml3.browse_frame_picked", text="Focus on Picked", icon='ZOOM_SELECTED')

        n, lods, preview, lines = browse_citydb.selection_summary(s)
        box = layout.box()
        box.label(text=f"Selected: {n} · LoD available: {lods} · preview: {preview}")
        for r, versions in lines:
            box.label(text=f"{r.label}  ({r.classname})", icon='HOME')
            box.label(text=versions, icon='BLANK1')
        if n > 3:
            box.label(text=f"... and {n - 3} more")
        limit = browse_citydb.load_limit(context)
        if limit and n > limit:
            box.label(text=f"Select at most {limit} buildings (preferences)", icon='CANCEL')

        row = layout.row(align=True)
        row.enabled = n > 0 and not busy
        colors.add(row, "cgml3.db_browse_load", text="Load LoD2", icon='IMPORT').mode = 'LOD2'
        colors.add(row, "cgml3.db_browse_load", text="Load Highest", icon='IMPORT').mode = 'HIGHEST'
        row = colors.remove_row(layout)
        row.enabled = bool(s.loaded)
        row.operator("cgml3.db_browse_unload", text="Unload Selected", icon='PANEL_CLOSE').all = False
        row.operator("cgml3.db_browse_unload", text=f"Unload All ({len(s.loaded)})").all = True
        layout.label(text="Unload only closes them here; the database keeps them", icon='INFO')
        if s.last_load and "features" in s.last_load:
            ll = s.last_load
            layout.label(text=f"Last load: {ll['features']} buildings · export {ll['export_s']:.1f} s · "
                              f"import {ll['import_s']:.2f} s")

        # Load by ID
        box = layout.box()
        box.label(text="Load by ID", icon='VIEWZOOM')
        row = box.row(align=True)
        row.prop(wm, "cgml3_db_find_text", text="")
        op = row.operator("cgml3.db_browse_find", text="", icon='IMPORT')
        op.text = wm.cgml3_db_find_text
        if s.id_matches:
            box.label(text=f"{len(s.id_matches)} matches for {s.find_text}:")
            for r in s.id_matches:
                levels = "/".join(f"LoD{v}" for v in r.lod_levels) or "no LoD"
                op = box.operator("cgml3.db_browse_load", text=f"{levels} (id {r.id})", icon='IMPORT')
                op.feature_id = r.id
                op.mode = 'HIGHEST'
        _draw_advanced_import(layout)


def _draw_advanced_import(layout) -> None:
    """B83: the citydb-tool dialog, last and grey; it needs an area (never the whole city model)."""
    layout.separator()
    layout.operator("cgml3.import_from_db", text="Advanced Import by Area (citydb-tool)...", icon='SETTINGS')


def _names(col, items, limit=3) -> None:
    for c in items[:limit]:
        col.label(text=c.name or c.objectid, icon='BLANK1')
    if len(items) > limit:
        col.label(text=f"... and {len(items) - limit} more", icon='BLANK1')


def draw_changes(layout, context) -> None:
    """B84: the scene is a working copy. What differs from the database, and one Commit that writes it."""
    sc = context.scene.cgml3
    box = layout.box()
    found = commit_citydb.scan_cached(context) if sc.db_profile and sc.db_name else None
    if found is None:
        box.label(text="3DCityDB: not connected (Open tab > Connect)", icon='NETWORK_DRIVE')
        return
    box.label(text=f"3DCityDB: {sc.db_target_label or sc.db_schema}", icon='NETWORK_DRIVE')
    drv = background_job.current()
    busy = drv is not None or browse_citydb.import_running()
    if drv is not None and drv.job.name in (commit_citydb.COMMIT_JOB, update_citydb.UPDATE_JOB):
        _done, _total, text = drv.job.progress
        r = box.row()
        r.label(text=text or drv.job.label, icon='TIME')
        r.operator("cgml3.browse_cancel", text="", icon='CANCEL')
        return
    box.label(text="Blender is a working copy:", icon='INFO')
    box.label(text="the database changes only on Commit.", icon='BLANK1')
    by_kind = {k: [c for c in found[1] if c.kind == k] for k in (*changes.PENDING, changes.SYNCED)}
    col = box.column(align=True)
    for kind in (*changes.PENDING, changes.SYNCED):
        items = by_kind[kind]
        if not items:
            continue
        col.label(text=f"{changes.LABELS[kind]}: {len(items)}", icon=commit_citydb._ICONS[kind])
        if kind != changes.SYNCED:
            _names(col, items)
        if kind == changes.DELETED:
            col.operator("cgml3.db_keep", text="Keep in Database", icon='LOOP_BACK')
    n = sum(len(by_kind[k]) for k in changes.PENDING)
    if not n and not by_kind[changes.SYNCED]:
        col.label(text="No city objects in the scene", icon='BLANK1')
    # the active object's state, so one building can be checked at a glance
    mine = set(commit_citydb.selected_ids(context))
    picked = [c for c in found[1] if c.objectid in mine]
    if len(picked) == 1:
        box.label(text=f"Selected: {changes.LABELS[picked[0].kind].lower()}", icon='RESTRICT_SELECT_OFF')
    col = box.column(align=True)
    col.enabled = bool(n) and not busy
    row = col.row()
    row.scale_y = 1.3
    colors.add(row, "cgml3.db_commit", icon='EXPORT',
               text=f"Commit {n} Change{'s' if n != 1 else ''} to 3DCityDB..." if n else "Nothing to Commit")
    m = sum(1 for c in picked if c.kind in changes.PENDING)
    if 0 < m < n:
        col.operator("cgml3.db_commit", text=f"Commit Selected Only ({m})...", icon='RESTRICT_SELECT_OFF'
                     ).only_selected = True
    s = browse_citydb.active_session(context)
    lu = s.last_update if s is not None else {}
    for oid, entry in (lu.get("conflicts") or {}).items():
        sub = box.column(align=True)
        sub.alert = True
        sub.label(text=f"Conflict: {oid}", icon='ERROR')
        sub.label(text=str(entry["why"])[:70], icon='BLANK1')
        sub.operator("cgml3.db_resolve", text="Keep My Version (replace on commit)").objectid = oid
    if lu.get("message"):
        r = box.row()
        r.alert = bool(lu.get("error"))
        r.label(text=str(lu["message"])[:140], icon='ERROR' if lu.get("error") else 'CHECKMARK')


classes = (CGML3_PT_DBBrowse,)
