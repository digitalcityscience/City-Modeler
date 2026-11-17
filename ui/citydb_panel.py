# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/citydb_panel.py
"""3DCityDB panel: server, password, discovered city models, active target.

Drawing only; connections, SQL and subprocesses live in ops/citydb_connection.py
and citydb/.
"""

import textwrap

import bpy
from bpy.types import Panel, UIList

from ..citydb import credentials, models
from ..common import background
from ..ops import citydb_connection as dbc

_STATUS_ICONS = {
    models.OK: 'WORLD',
    models.EMPTY: 'OUTLINER_COLLECTION',
    models.NO_ACCESS: 'LOCKED',
    models.UNSUPPORTED: 'ERROR',
}

# Approximate width of one UI character in pixels at UI scale 1.0.
_CHAR_PX = 7.0


def _region_chars(context, margin_px: int = 40) -> int:
    region = getattr(context, "region", None)
    width = getattr(region, "width", 300) or 300
    scale = context.preferences.system.ui_scale or 1.0
    return max(int((width - margin_px) / (_CHAR_PX * scale)), 16)


def _label_wrapped(layout, context, text: str, icon: str = 'NONE', indent_icon: str = 'BLANK1'):
    """Label that wraps to the sidebar width instead of being cut with '...'."""
    lines = textwrap.wrap(text, width=_region_chars(context, 60)) or [""]
    col = layout.column(align=True)
    for i, line in enumerate(lines):
        col.label(text=line, icon=icon if i == 0 else (indent_icon if icon != 'NONE' else 'NONE'))
    return col


def _foldout(layout, data, prop_name: str, label: str):
    """Disclosure row; returns a column to draw into when open, else None."""
    is_open = bool(getattr(data, prop_name))
    layout.prop(data, prop_name, text=label, icon='TRIA_DOWN' if is_open else 'TRIA_RIGHT', emboss=False)
    return layout.column(align=True) if is_open else None


class CGML3_UL_DBModels(UIList):
    """City models: label (double-click to rename) | database / schema | EPSG.

    Columns appear as the sidebar gets wider; the label always stays.
    """

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.active = item.usable
        chars = _region_chars(context)
        status_icon = _STATUS_ICONS.get(item.status, 'QUESTION')
        if chars < 44:
            row.prop(item, "label", text="", emboss=False, icon=status_icon)
            return
        split = row.split(factor=0.5 if chars >= 60 else 0.55, align=True)
        split.prop(item, "label", text="", emboss=False, icon=status_icon)
        if chars >= 60:
            right = split.split(factor=0.62, align=True)
            right.label(text=f"{item.database} / {item.schema}")
            right.label(text=f"EPSG:{item.srid}" if item.srid else "")
        else:
            split.label(text=f"{item.database} / {item.schema}")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if self.filter_name:
            needle = self.filter_name.lower()
            for i, it in enumerate(items):
                text = f"{it.label} {it.database} {it.schema} {it.comment}".lower()
                if needle not in text:
                    flags[i] = 0
        return flags, []


def _draw_status(layout, context, wm):
    state = wm.cgml3_db_state
    if state == "RUNNING" or background.is_running(dbc.DISCOVER_JOB):
        _label_wrapped(layout, context, wm.cgml3_db_message or "Checking ...", icon='SORTTIME')
    elif state == "OK":
        col = _label_wrapped(layout, context, wm.cgml3_db_message, icon='CHECKMARK')
        server = f"PostgreSQL {wm.cgml3_db_server_version} · " if wm.cgml3_db_server_version else ""
        col.label(text=f"{server}checked {wm.cgml3_db_checked_at}", icon='BLANK1')
    elif state == "ERROR":
        layout.alert = True
        _label_wrapped(layout, context, wm.cgml3_db_message, icon='ERROR')
        layout.alert = False
    else:
        layout.label(text="Not connected in this session", icon='RADIOBUT_OFF')


def _draw_password(layout, wm, needs_password: bool):
    row = layout.row(align=True)
    source = wm.cgml3_db_password_source
    if source == credentials.SOURCE_KEYCHAIN:
        row.label(text="Password: keychain", icon='LOCKED')
    elif source == credentials.SOURCE_SESSION:
        row.label(text="Password: this session", icon='LOCKED')
    else:
        row.label(text="Password: not set", icon='UNLOCKED')
    sub = row.row(align=True)
    sub.alert = needs_password
    sub.operator("cgml3.db_set_password", text="" if source else "Set", icon='KEY_HLT')
    if source:
        row.operator("cgml3.db_forget_password", text="", icon='X')


def _draw_details(layout, context, item):
    col = layout.column(align=True)
    col.prop(item, "label", text="Label")
    col.separator()
    col.label(text=f"{item.database} / {item.schema}", icon='DISK_DRIVE')
    if item.version:
        col.label(text=f"3DCityDB {item.version}", icon='BLANK1')
    if item.srid:
        _label_wrapped(col, context, f"EPSG:{item.srid} – {item.crs_name}" if item.crs_name else f"EPSG:{item.srid}",
                       icon='ORIENTATION_GLOBAL')
    if item.counts_text:
        _label_wrapped(col, context, item.counts_text, icon='HOME')
    if item.extent_text:
        col.label(text=item.extent_text, icon='SNAP_VOLUME')
    if item.lonlat_text:
        col.label(text=item.lonlat_text, icon='WORLD')
    if item.usable:
        if item.last_modified:
            col.label(text=f"Changed {item.last_modified}", icon='TIME')
        col.label(text="Read & write" if item.can_write else "Read only",
                  icon='GREASEPENCIL' if item.can_write else 'HIDE_OFF')
    if item.message:
        _label_wrapped(col, context, item.message, icon='ERROR')
    if item.comment and item.comment != item.label:
        _label_wrapped(col, context, f"Comment: {item.comment}", icon='TEXT')


def _draw_unverified_target(layout, context):
    """Target saved in the .blend but not listed yet (no Connect in this session)."""
    sc = context.scene.cgml3
    if not sc.db_profile:
        return
    col = layout.column(align=True)
    col.label(text=f"Last used: {sc.db_target_label or sc.db_schema}", icon='LINKED')
    col.label(text=f"{sc.db_name} / {sc.db_schema} - press Connect to check it", icon='BLANK1')


def connection_status(context):
    """``(text, colour icon)`` of the 3DCityDB target, for the panel header and the status strip."""
    wm = context.window_manager
    sc = context.scene.cgml3
    if wm.cgml3_db_state == "ERROR":
        return "Error", 'COLLECTION_COLOR_01'
    if not sc.db_profile:
        return "Not connected", 'COLLECTION_COLOR_08'
    label = sc.db_target_label or sc.db_schema
    if wm.cgml3_db_state == "OK":
        return label, 'COLLECTION_COLOR_04'
    return f"{label} (not verified)", 'COLLECTION_COLOR_03'


class CGML3_PT_CityDB(Panel):
    """B83: server -> Connect -> city model (= Load and Save target); settings and details fold away."""
    bl_label = "Connect to 3DCityDB"
    bl_idname = "CGML3_PT_CityDB"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Main"

    def draw_header(self, context):
        self.layout.label(icon='NETWORK_DRIVE')

    def draw_header_preset(self, context):
        # Icon only: the text overlapped bl_label in a narrow sidebar; the status strip shows it in full.
        _, icon = connection_status(context)
        self.layout.label(icon=icon)

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        prefs = dbc.addon_prefs(context)
        if prefs is None or not hasattr(prefs, "db_profiles"):
            layout.label(text="Add-on preferences not available", icon='ERROR')
            return

        # 1) Server: one row; its settings only while not connected or on request (gear)
        box = layout.box()
        row = box.row(align=True)
        row.prop_search(prefs, "db_active_profile", prefs, "db_profiles", text="", icon='NETWORK_DRIVE')
        row.prop(wm, "cgml3_db_show_advanced", text="", icon='PREFERENCES')
        row.operator("cgml3.db_profile_add", text="", icon='ADD')
        row.operator("cgml3.db_profile_remove", text="", icon='REMOVE')

        profile = dbc.active_profile(context)
        if profile is None:
            box.label(text="Add a server (+) to see its city models", icon='INFO')
            return

        connected = wm.cgml3_db_state == "OK"
        if wm.cgml3_db_show_advanced or not connected:
            col = box.column(align=True)
            row = col.row(align=True)
            row.prop(profile, "host")
            row.prop(profile, "port", text="")
            col.prop(profile, "user")
            col.prop(profile, "sslmode")
            col.prop(profile, "only_databases")
            col.prop(profile, "maintenance_db")
            _draw_password(box, wm, wm.cgml3_db_error_kind in ("password_required", "auth"))

        # 2) Connect
        row = box.row()
        row.scale_y = 1.3
        running = background.is_running(dbc.DISCOVER_JOB)
        row.operator("cgml3.db_discover", icon='FILE_REFRESH',
                     text="Connecting ..." if running else ("Reconnect" if connected else "Connect"))
        _draw_status(box.column(), context, wm)

        # 3) City model: the selected row is where Load reads and Save writes
        if not len(wm.cgml3_db_models):
            _draw_unverified_target(layout, context)
            return
        box = layout.box()
        box.label(text="City model (Load and Save use it)", icon='WORLD')
        box.template_list("CGML3_UL_DBModels", "", wm, "cgml3_db_models", wm, "cgml3_db_models_index",
                          rows=3, maxrows=8)
        sub = _foldout(box, wm, "cgml3_db_show_details", "Details")
        if sub:
            idx = wm.cgml3_db_models_index
            if 0 <= idx < len(wm.cgml3_db_models):
                _draw_details(sub, context, wm.cgml3_db_models[idx])
            if wm.cgml3_db_other:
                col = sub.column(align=True)
                col.label(text="Other databases", icon='INFO')
                for entry in wm.cgml3_db_other.split(", "):
                    col.label(text=entry, icon='BLANK1')


classes = (CGML3_UL_DBModels, CGML3_PT_CityDB)
