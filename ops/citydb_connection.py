# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/citydb_connection.py
"""3DCityDB servers, city-model discovery and the import/export target.

- Server profiles (host, port, user, SSL) live in the add-on preferences,
  together with user labels per database/schema. No password.
- "Connect & Discover" lists every 3DCityDB v5 schema on the server, across
  databases, in a background thread (window-manager data, not saved).
- Picking a city model makes it the scene's import/export target
  (``scene.cgml3.db_*``, saved with the .blend so a file knows its source).
- Passwords: ``citydb.credentials`` (session / OS keychain), entered through
  an operator popup whose property is never saved.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty,
)
from bpy.types import Operator, PropertyGroup

from ..citydb import credentials, errors as db_errors, models
from ..citydb.connection import (
    DEFAULT_MAINTENANCE_DB, DEFAULT_SCHEMA, SSL_MODES, CityDBTarget, ServerProfile, parse_port,
)
from ..common import background

ADDON_PACKAGE = __package__.rsplit(".", 1)[0]
DISCOVER_JOB = "citydb-discover"

SSL_ITEMS = [
    ("disable", "Disable", "No SSL"),
    ("allow", "Allow", "Try without SSL first, then with SSL"),
    ("prefer", "Prefer", "Try SSL first, then without (PostgreSQL default)"),
    ("require", "Require", "SSL only, no certificate check"),
    ("verify-ca", "Verify CA", "SSL with a trusted certificate authority"),
    ("verify-full", "Verify Full", "SSL, trusted authority and matching host name"),
]
assert tuple(item[0] for item in SSL_ITEMS) == SSL_MODES

STATE_ITEMS = [
    ("IDLE", "Not verified", ""),
    ("RUNNING", "Checking", ""),
    ("OK", "Verified", ""),
    ("ERROR", "Error", ""),
]

# Set while discovery fills the list, so label updates do not write aliases back.
_filling = False


# ---------------------------------------------------------------------------
# Preferences access
# ---------------------------------------------------------------------------
def addon_prefs(context=None):
    ctx = context or bpy.context
    try:
        entry = ctx.preferences.addons.get(ADDON_PACKAGE)
    except Exception:
        return None
    return getattr(entry, "preferences", None) if entry else None


def active_profile(context=None):
    prefs = addon_prefs(context)
    if prefs is None or not hasattr(prefs, "db_profiles"):
        return None
    return prefs.db_profiles.get(prefs.db_active_profile)


def _unique_profile_name(prefs, base: str) -> str:
    name, n = base, 2
    while name in prefs.db_profiles:
        name = f"{base} {n}"
        n += 1
    return name


# ---------------------------------------------------------------------------
# Property groups
# ---------------------------------------------------------------------------
def _reset_discovery(context) -> None:
    wm = getattr(context, "window_manager", None) or bpy.context.window_manager
    if wm is None or background.is_running(DISCOVER_JOB):
        return
    wm.cgml3_db_models.clear()
    wm.cgml3_db_models_index = -1
    wm.cgml3_db_state = "IDLE"
    wm.cgml3_db_message = ""
    wm.cgml3_db_other = ""


def _on_server_changed(self, context):
    _reset_discovery(context)
    refresh_password_state(context)


def _on_active_profile_changed(self, context):
    _reset_discovery(context)
    refresh_password_state(context)


class CGML3_DBSchemaAlias(PropertyGroup):
    database: StringProperty(name="Database")
    schema: StringProperty(name="Schema")
    label: StringProperty(name="Label")


class CGML3_DBProfile(PropertyGroup):
    """A PostgreSQL server holding one or more 3DCityDB city models."""

    host: StringProperty(name="Host", default="localhost", update=_on_server_changed)
    port: IntProperty(name="Port", default=5432, min=1, max=65535, update=_on_server_changed)
    user: StringProperty(name="User", default="postgres", update=_on_server_changed)
    sslmode: EnumProperty(name="SSL", items=SSL_ITEMS, default="prefer", update=_on_server_changed)
    maintenance_db: StringProperty(
        name="Maintenance Database",
        default=DEFAULT_MAINTENANCE_DB,
        description="Database used to list the other databases on the server",
        update=_on_server_changed,
    )
    only_databases: StringProperty(
        name="Only Databases",
        default="",
        description=(
            "Comma-separated databases to search. Empty searches every database you may "
            "connect to (needs access to the maintenance database)"
        ),
        update=_on_server_changed,
    )
    aliases: CollectionProperty(type=CGML3_DBSchemaAlias)

    def server(self) -> ServerProfile:
        return ServerProfile(
            host=self.host.strip(),
            port=int(self.port),
            user=self.user.strip(),
            sslmode=self.sslmode,
            maintenance_db=self.maintenance_db.strip() or DEFAULT_MAINTENANCE_DB,
        )

    def database_filter(self):
        return [d.strip() for d in self.only_databases.split(",") if d.strip()]

    def alias(self, database: str, schema: str) -> str:
        for a in self.aliases:
            if a.database == database and a.schema == schema:
                return a.label
        return ""

    def set_alias(self, database: str, schema: str, label: str) -> None:
        for i, a in enumerate(self.aliases):
            if a.database == database and a.schema == schema:
                if label.strip():
                    a.label = label.strip()
                else:
                    self.aliases.remove(i)
                return
        if label.strip():
            a = self.aliases.add()
            a.database, a.schema, a.label = database, schema, label.strip()


def _on_label_changed(self, context):
    if _filling:
        return
    profile = active_profile(context)
    if profile is not None:
        profile.set_alias(self.database, self.schema, self.label)
    sc = context.scene.cgml3
    if sc.db_profile == getattr(profile, "name", None) and sc.db_name == self.database \
            and sc.db_schema == self.schema:
        sc.db_target_label = self.label or self.schema


class CGML3_DBModelItem(PropertyGroup):
    """One discovered 3DCityDB schema (window-manager data, rebuilt by discovery)."""

    label: StringProperty(name="Label", description="Your name for this city model", update=_on_label_changed)
    database: StringProperty(name="Database")
    schema: StringProperty(name="Schema")
    comment: StringProperty(name="Comment")
    status: StringProperty(name="Status")
    version: StringProperty(name="3DCityDB Version")
    srid: IntProperty(name="SRID")
    crs_name: StringProperty(name="CRS")
    counts_text: StringProperty(name="Features")
    extent_text: StringProperty(name="Extent")
    lonlat_text: StringProperty(name="Location")
    last_modified: StringProperty(name="Last Change")
    can_write: BoolProperty(name="Writable")
    message: StringProperty(name="Message")

    @property
    def usable(self) -> bool:
        return self.status in (models.OK, models.EMPTY)


def _on_model_selected(self, context):
    if _filling:
        return
    wm = context.window_manager
    idx = wm.cgml3_db_models_index
    if not 0 <= idx < len(wm.cgml3_db_models):
        return
    item = wm.cgml3_db_models[idx]
    if item.usable:
        set_scene_target(context, active_profile(context), item)


def set_scene_target(context, profile, item) -> None:
    """Make a discovered city model the scene's import/export target."""
    if profile is None:
        return
    sc = context.scene.cgml3
    sc.db_profile = profile.name
    sc.db_host = profile.host.strip()
    sc.db_port = str(profile.port)
    sc.db_user = profile.user.strip()
    sc.db_sslmode = profile.sslmode
    sc.db_name = item.database
    sc.db_schema = item.schema
    sc.db_target_label = item.label or item.schema
    sc.db_target_srid = item.srid


# ---------------------------------------------------------------------------
# Password state (cached for drawing; the keychain is not queried per redraw)
# ---------------------------------------------------------------------------
def refresh_password_state(context=None) -> None:
    ctx = context or bpy.context
    wm = getattr(ctx, "window_manager", None)
    if wm is None:
        return
    profile = active_profile(ctx)
    wm.cgml3_db_password_source = (credentials.has_password(profile.server().key) or "") if profile else ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _fill_models(wm, profile, result: models.DiscoveryResult, scene) -> None:
    global _filling
    _filling = True
    try:
        wm.cgml3_db_models.clear()
        select = -1
        for info in result.schemas:
            item = wm.cgml3_db_models.add()
            item.database = info.database
            item.schema = info.schema
            item.comment = info.comment
            item.status = info.status
            item.version = info.version
            item.srid = int(info.srid or 0)
            item.crs_name = info.crs_name
            item.counts_text = models.format_counts(info) if info.status == models.OK else (
                "no features" if info.status == models.EMPTY else models.format_status(info))
            item.extent_text = models.format_extent_size(info)
            item.lonlat_text = models.format_lonlat(info.center_lonlat)
            item.last_modified = info.last_modified
            item.can_write = info.can_write
            item.message = info.message
            item.label = info.display_label(profile.alias(info.database, info.schema))
            sc = scene.cgml3
            if sc.db_profile == profile.name and sc.db_name == info.database and sc.db_schema == info.schema:
                select = len(wm.cgml3_db_models) - 1
        wm.cgml3_db_models_index = select
    finally:
        _filling = False

    sc = scene.cgml3
    if select >= 0:
        # Keep the label and CRS of the current target in sync.
        set_scene_target(bpy.context, profile, wm.cgml3_db_models[select])
    elif not sc.db_profile:
        first = next((i for i, it in enumerate(wm.cgml3_db_models) if it.usable), -1)
        if first >= 0:
            wm.cgml3_db_models_index = first  # triggers set_scene_target


def discover_sync(context, profile, password: Optional[str] = None):
    """Discovery on the calling thread (headless scripts); returns (result, error)."""
    from ..citydb import client

    server = profile.server()
    pw = password or credentials.lookup(server.key)[0]
    try:
        result, used = client.discover(server, pw, databases=profile.database_filter() or None)
    except db_errors.CityDBError as exc:
        return None, exc
    _apply_discovery(context.window_manager, profile, context.scene, result, None)
    return result, None


def _apply_discovery(wm, profile, scene, result, error) -> None:
    wm.cgml3_db_checked_at = time.strftime("%H:%M")
    if error is not None:
        wm.cgml3_db_state = "ERROR"
        if isinstance(error, db_errors.CityDBError):
            wm.cgml3_db_message = error.message
            wm.cgml3_db_error_kind = error.kind
            if error.detail:
                print(f"[3DCityDB] {error.message} - {error.detail}")
        else:
            wm.cgml3_db_message = f"Unexpected error: {error.__class__.__name__}"
            wm.cgml3_db_error_kind = db_errors.OTHER
            print(f"[3DCityDB] Discovery failed: {credentials.redact(repr(error))}")
        return
    wm.cgml3_db_state = "OK"
    wm.cgml3_db_error_kind = ""
    wm.cgml3_db_message = models.discovery_summary(result, with_server=False)
    wm.cgml3_db_server_version = models.server_version_short(result)
    wm.cgml3_db_other = models.other_databases_line(result)
    wm.cgml3_db_profile_key = profile.server().key
    _fill_models(wm, profile, result, scene)
    print(f"[3DCityDB] {models.discovery_summary(result)} ({result.seconds:.2f} s)")


class CGML3_OT_DBDiscover(Operator):
    bl_idname = "cgml3.db_discover"
    bl_label = "Connect & Discover"
    bl_description = (
        "Connect to the server and list every 3DCityDB v5 city model "
        "(database / schema) with its CRS, content and extent"
    )

    @classmethod
    def poll(cls, context):
        if background.is_running(DISCOVER_JOB):
            cls.poll_message_set("Discovery is running")
            return False
        if active_profile(context) is None:
            cls.poll_message_set("Add a server first")
            return False
        return True

    def execute(self, context):
        from ..citydb import client

        profile = active_profile(context)
        server = profile.server()
        if not server.host:
            self.report({'ERROR'}, "Enter a host name")
            return {'CANCELLED'}
        if not client.available():
            self.report({'ERROR'}, db_errors.user_message(db_errors.DRIVER_MISSING))
            return {'CANCELLED'}

        wm = context.window_manager
        password, _source = credentials.lookup(server.key)

        if bpy.app.background:
            _result, error = discover_sync(context, profile, password)
            if error:
                self.report({'ERROR'}, error.message)
                return {'CANCELLED'}
            self.report({'INFO'}, wm.cgml3_db_message)
            return {'FINISHED'}

        wm.cgml3_db_state = "RUNNING"
        wm.cgml3_db_message = f"Connecting to {server.host}:{server.port} ..."
        databases = profile.database_filter() or None
        profile_name = profile.name
        scene_name = context.scene.name

        def _work():
            return client.discover(server, password, databases=databases)

        def _done(result, error):
            wm_now = bpy.context.window_manager
            prefs = addon_prefs()
            prof = prefs.db_profiles.get(profile_name) if prefs else None
            scene = bpy.data.scenes.get(scene_name) or bpy.context.scene
            if prof is None:
                wm_now.cgml3_db_state = "IDLE"
                return
            _apply_discovery(wm_now, prof, scene, result[0] if result else None, error)
            refresh_password_state()

        background.run(DISCOVER_JOB, _work, _done)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Password and profile operators
# ---------------------------------------------------------------------------
class CGML3_OT_DBSetPassword(Operator):
    bl_idname = "cgml3.db_set_password"
    bl_label = "Database Password"
    bl_description = "Enter the password for this server. It is never saved in the .blend file"

    password: StringProperty(name="Password", subtype='PASSWORD', options={'SKIP_SAVE', 'HIDDEN'})
    remember: BoolProperty(
        name="Remember in system keychain",
        description="Store the password in the operating system's keychain instead of only for this session",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        return active_profile(context) is not None

    def invoke(self, context, event):
        self.password = ""
        self.remember = credentials.has_password(active_profile(context).server().key) == credentials.SOURCE_KEYCHAIN
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        profile = active_profile(context)
        layout = self.layout
        layout.label(text=f"{profile.user}@{profile.host}:{profile.port}", icon='USER')
        layout.prop(self, "password")
        usable, backend = credentials.keychain_status()
        row = layout.row()
        row.enabled = usable
        row.prop(self, "remember")
        if not usable:
            layout.label(text=f"Keychain unavailable ({backend}) - kept for this session", icon='INFO')

    def execute(self, context):
        profile = active_profile(context)
        key = profile.server().key
        if not self.password:
            credentials.forget(key)
            self.report({'INFO'}, "Password removed")
        else:
            where = credentials.store(key, self.password, remember=self.remember)
            self.report({'INFO'}, "Password stored in the system keychain" if where == credentials.SOURCE_KEYCHAIN
                        else "Password kept for this session")
        has_password = bool(self.password)
        self.password = ""
        refresh_password_state(context)
        if has_password:
            # Usually the password was asked for because discovery needed it.
            bpy.ops.cgml3.db_discover()
        return {'FINISHED'}


class CGML3_OT_DBForgetPassword(Operator):
    bl_idname = "cgml3.db_forget_password"
    bl_label = "Forget Password"
    bl_description = "Remove the password from this session and from the system keychain"

    @classmethod
    def poll(cls, context):
        return active_profile(context) is not None

    def execute(self, context):
        credentials.forget(active_profile(context).server().key)
        refresh_password_state(context)
        self.report({'INFO'}, "Password forgotten")
        return {'FINISHED'}


class CGML3_OT_DBProfileAdd(Operator):
    bl_idname = "cgml3.db_profile_add"
    bl_label = "Add Server"
    bl_description = "Add a PostgreSQL / 3DCityDB server"

    name: StringProperty(name="Name", default="")
    host: StringProperty(name="Host", default="localhost")
    port: IntProperty(name="Port", default=5432, min=1, max=65535)
    user: StringProperty(name="User", default="postgres")

    def invoke(self, context, event):
        sc = context.scene.cgml3
        # Start from the scene's last connection (older files) when there is one.
        self.host = sc.db_host or "localhost"
        try:
            self.port = parse_port(sc.db_port)
        except ValueError:
            self.port = 5432
        self.user = sc.db_user or "postgres"
        self.name = ""
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        prefs = addon_prefs(context)
        if prefs is None:
            self.report({'ERROR'}, "Add-on preferences not available")
            return {'CANCELLED'}
        p = prefs.db_profiles.add()
        p.name = _unique_profile_name(prefs, self.name.strip() or f"{self.host}:{self.port}")
        p.host, p.port, p.user = self.host.strip(), self.port, self.user.strip()
        prefs.db_active_profile = p.name
        _save_prefs_soon()
        return {'FINISHED'}


class CGML3_OT_DBProfileRemove(Operator):
    bl_idname = "cgml3.db_profile_remove"
    bl_label = "Remove Server"
    bl_description = "Remove this server profile and forget its password"

    @classmethod
    def poll(cls, context):
        return active_profile(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        prefs = addon_prefs(context)
        profile = active_profile(context)
        credentials.forget(profile.server().key)
        idx = prefs.db_profiles.find(profile.name)
        prefs.db_profiles.remove(idx)
        prefs.db_active_profile = prefs.db_profiles[0].name if len(prefs.db_profiles) else ""
        _save_prefs_soon()
        return {'FINISHED'}


def _save_prefs_soon():
    """Persist profile changes even when auto-save of preferences is off."""
    def _save():
        try:
            if not bpy.app.background and bpy.context.preferences.is_dirty:
                bpy.ops.wm.save_userpref()
        except Exception:
            pass
        return None
    try:
        bpy.app.timers.register(_save, first_interval=0.5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Import/export: resolve and verify the scene target
# ---------------------------------------------------------------------------
def resolve_target(
    host: str, port, database: str, user: str, schema: str = "", sslmode: str = "prefer",
    *, verify: bool = True,
) -> Tuple[CityDBTarget, Optional[str], Optional[models.SchemaInfo]]:
    """Target, password and (when verified) schema facts for a DB operation.

    The password comes from this session or the OS keychain; libpq may add one
    from ~/.pgpass. Verification is a quick read-only check that the schema is
    a reachable 3DCityDB v5 schema, so citydb-tool never starts against the
    wrong database. Raises ``CityDBError``.
    """
    from ..citydb import client

    try:
        server = ServerProfile(host=(host or "").strip(), port=parse_port(port), user=(user or "").strip(),
                               sslmode=sslmode or "prefer")
    except ValueError:
        raise db_errors.CityDBError(db_errors.OTHER, f"Invalid port: {port}") from None
    if not server.host or not (database or "").strip():
        raise db_errors.CityDBError(
            db_errors.OTHER, "No 3DCityDB target - pick a city model in the 3DCityDB panel")
    target = CityDBTarget(server, database.strip(), (schema or "").strip() or DEFAULT_SCHEMA)
    password, _ = credentials.lookup(server.key)
    if verify and client.available():
        info, used = client.verify_target(target, password)
        return target, used, info
    return target, password, None


def describe_error(exc: db_errors.CityDBError) -> str:
    """Operator report text, with the fix when the password is the problem."""
    if exc.needs_password:
        return f"{exc.message} - set it in the 3DCityDB panel (Password)"
    return exc.message


# ---------------------------------------------------------------------------
# Legacy cleanup: older files stored the DB password in scene.cgml3.db_pass
# ---------------------------------------------------------------------------
LEGACY_PASSWORD_KEY = "db_pass"


def purge_legacy_passwords() -> int:
    """Remove ``db_pass`` left in scenes by older versions; returns how many were removed."""
    removed = 0
    for scene in bpy.data.scenes:
        for group in _cgml3_property_groups(scene):
            try:
                if LEGACY_PASSWORD_KEY in group.keys():
                    del group[LEGACY_PASSWORD_KEY]
                    removed += 1
            except Exception:
                pass
    return removed


def _cgml3_property_groups(scene):
    """IDProperty groups that may hold scene.cgml3 data (user and system storage)."""
    groups = []
    try:
        g = scene.get("cgml3")
        if g is not None and hasattr(g, "keys"):
            groups.append(g)
    except Exception:
        pass
    getter = getattr(scene, "bl_system_properties_get", None)
    if getter is not None:
        try:
            system = getter()
            g = system.get("cgml3") if system is not None else None
            if g is not None and hasattr(g, "keys"):
                groups.append(g)
        except Exception:
            pass
    return groups


@persistent
def _on_load_post(*_args):
    n = purge_legacy_passwords()
    if n:
        print(f"[3DCityDB] Removed {n} stored database password(s) from this file "
              "(passwords are no longer saved in .blend files)")
    refresh_password_state()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
property_classes = (CGML3_DBSchemaAlias, CGML3_DBProfile, CGML3_DBModelItem)
operator_classes = (
    CGML3_OT_DBDiscover,
    CGML3_OT_DBSetPassword,
    CGML3_OT_DBForgetPassword,
    CGML3_OT_DBProfileAdd,
    CGML3_OT_DBProfileRemove,
)


def register_window_manager_props():
    wm = bpy.types.WindowManager
    wm.cgml3_db_models = CollectionProperty(type=CGML3_DBModelItem)
    wm.cgml3_db_models_index = IntProperty(default=-1, update=_on_model_selected)
    wm.cgml3_db_state = EnumProperty(items=STATE_ITEMS, default="IDLE")
    wm.cgml3_db_message = StringProperty(default="")
    wm.cgml3_db_error_kind = StringProperty(default="")
    wm.cgml3_db_checked_at = StringProperty(default="")
    wm.cgml3_db_server_version = StringProperty(default="")
    wm.cgml3_db_other = StringProperty(default="")
    wm.cgml3_db_profile_key = StringProperty(default="")
    wm.cgml3_db_password_source = StringProperty(default="")
    wm.cgml3_db_show_advanced = BoolProperty(name="Server settings", default=False,
                                             description="Show host, port, user, SSL and password")
    wm.cgml3_db_show_details = BoolProperty(name="Details", default=False)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister_window_manager_props():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    wm = bpy.types.WindowManager
    for name in (
        "cgml3_db_models", "cgml3_db_models_index", "cgml3_db_state", "cgml3_db_message",
        "cgml3_db_error_kind", "cgml3_db_checked_at", "cgml3_db_server_version", "cgml3_db_other",
        "cgml3_db_profile_key",
        "cgml3_db_password_source", "cgml3_db_show_advanced", "cgml3_db_show_details",
    ):
        if hasattr(wm, name):
            delattr(wm, name)
