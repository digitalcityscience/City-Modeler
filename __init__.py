# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# __init__.py
# Identity, version and dependencies (lxml, pyproj) are declared in blender_manifest.toml.
import json
import os

import bpy
from bpy.types import Panel, Operator, AddonPreferences, PropertyGroup
from bpy.props import StringProperty, BoolProperty, EnumProperty, IntProperty, FloatProperty, FloatVectorProperty, PointerProperty, CollectionProperty
import datetime
import sys, importlib
import traceback
from pathlib import Path
from types import SimpleNamespace
from .ops.validate import validate_citygml3_xsd
from .common import import_citygml_auto
from .common.filter_utils import format_bbox_coords_string, parse_bbox_coords_string
from .shared import georef as gr
from .shared.load_limits import LOAD_ALL_MAX_FEATURES, LOAD_ALL_MAX_MB, ImportTooLarge
from .ops import drag_drop
from .ops import join_object_parts
from .ops import assign_object_part
from .ops import ring_opening_sync
from .ops import facade_authoring
from .ops import edit_exit_validation
from .ops import authoring_core_ops
from .ops import planarity
from .ops import validation
from .ops import citydb_connection
from .ops import browse_gml
from .ops import browse_citydb
from .ops import update_citydb
from .ops import commit_citydb
from .ops import building_authoring
from .ops import roof_authoring
from .ops import opening_authoring
from .ops import installation_authoring
from .common import background_job
from .ui import citydb_panel
from .ui import browse_panel
from .ui import db_browse_panel
from .ui import header_panel
from .ui import workflow_panels
from .ui import colors
from . import modeltyper
from .shared.authoring_core.blender import props as authoring_core_props
from .shared.authoring_core.blender import registry as authoring_core_registry
from .shared.authoring_core.blender import transform_drift as authoring_core_transform_drift

from .ops.citydb_cli import CityDBTool
from . import ui as cgml3_ui
from . import presets as cgml3_presets


HEADLESS_PREFS_KEY = "cgml3_headless_prefs"


def _default_citydb_prefs():
    return SimpleNamespace(
        citydb_exe="citydb-tool-1.3.0\\citydb.bat" if os.name == "nt" else "/opt/citydb-tool/citydb",
        use_docker=False,
        docker_image="ghcr.io/3dcitydb/citydb-tool:latest",
    )


def _get_citydb_prefs(context=None):
    ctx = context or bpy.context
    addon_keys = []
    module_name = (__package__ or __name__).split('.')[0]
    for key in (module_name, __name__, "CityGML-3_Importer-Exporter"):
        if key and key not in addon_keys:
            addon_keys.append(key)

    try:
        addons = ctx.preferences.addons
    except Exception:
        addons = None

    if addons is not None:
        for key in addon_keys:
            entry = addons.get(key)
            if entry is not None and hasattr(entry, "preferences"):
                return entry.preferences

    prefs = _default_citydb_prefs()
    overrides = getattr(bpy.app, "driver_namespace", {}).get(HEADLESS_PREFS_KEY, {}) or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if hasattr(prefs, key):
                setattr(prefs, key, value)
    return prefs


def _frame_view_after_import(context=None) -> bool:
    return bool(getattr(_get_citydb_prefs(context), "frame_view_after_import", True))


def _detect_citygml_version_from_scene(scene=None):
    """
    Erkennt CityGML-Version anhand des Namens der obersten Collection im Blender-Szene.
    Gibt '3.0', '2.0' oder None zurück.
    """
    import bpy
    root_coll = (scene if scene is not None else bpy.context.scene).collection
    for col in root_coll.children:
        name_up = col.name.upper()
        if 'CITYGML3' in name_up:
            return '3.0'
        if 'CITYGML2' in name_up:
            return '2.0'
    return None

def _citydb_tool_unsupported_message(log_text: str):
    from .citydb.errors import citydb_tool_unsupported_message
    return citydb_tool_unsupported_message(log_text)


# XSD messages "Tolerant (3DCityDB)" lets through: known quirks of citydb-tool exports.
_TOLERATED_XSD_ERRORS = ("xs:ID", "textureParameterization", "isFront")


def _validate_citygml(path: str, xsd: bool, lenient: bool):
    """``(error, warning, xsd_log)`` of the structure check or, with ``xsd``, of the XSD check (which
    starts with the structure check). No bpy: the DB jobs run it in their worker thread."""
    from .ops.validate_auto import well_formed_citygml_auto, validate_citygml_auto

    if not xsd:
        ok, msg = well_formed_citygml_auto(path)
        return (None if ok else f"Validator: {msg}"), None, None
    ok, msg = validate_citygml_auto(path)
    if ok:
        return None, None, msg
    if lenient and any(s in msg for s in _TOLERATED_XSD_ERRORS):
        return None, msg, msg
    return f"XSD: {msg}", None, msg


# Filter Options feature-type checkboxes -> citydb-tool type names (DB import and export)
_DB_TYPE_NAMES = {
    "import_buildings":       ["bldg:Building", "bldg:BuildingPart"],
    "import_bridges":         ["brid:Bridge", "brid:BridgePart"],
    "import_tunnels":         ["tun:Tunnel", "tun:TunnelPart"],
    "import_vegetation":      ["veg:SolitaryVegetationObject", "veg:PlantCover"],
    "import_water":           ["wtr:WaterBody", "wtr:WaterSurface"],
    "import_transportation":  ["tran:Road", "tran:Railway", "tran:Track", "tran:Square", "tran:Waterway"],
    "import_cityfurniture":   ["frn:CityFurniture"],
    "import_landuse":         ["luse:LandUse"],
    "import_relief":          ["dem:TINRelief", "dem:RasterRelief", "dem:MassPointRelief", "dem:BreaklineRelief"],
    "import_generics":        ["gen:GenericOccupiedSpace", "gen:GenericLogicalSpace",
                               "gen:GenericUnoccupiedSpace", "gen:GenericThematicSurface",
                               "gen:GenericCityObject"],
}


def _scene_db_type_names(scene):
    """citydb-tool type names of the Filter Options checkboxes when that filter is on, else None."""
    sc = scene.cgml3
    if not sc.use_feature_type_filter:
        return None
    names = [n for prop, group in _DB_TYPE_NAMES.items() if getattr(sc, prop, True) for n in group]
    return names or None


def _export_types(op) -> list[str]:
    """CityGML feature types selected by the export checkboxes (file and DB export)."""
    v2 = op.citygml_version == '2.0'
    types = []
    if op.export_buildings:
        types += ["bldg:Building", "bldg:BuildingPart", "bldg:BuildingUnit", "bldg:Storey", "bldg:BuildingRoom",
                  "bldg:BuildingConstructiveElement", "bldg:BuildingInstallation", "bldg:BuildingFurniture"]
        if v2:
            types += ["bldg:IntBuildingInstallation"]
    if op.export_bridges:
        types += ["brid:Bridge", "brid:BridgePart", "brid:BridgeRoom", "brid:BridgeFurniture",
                  "brid:BridgeInstallation"]
        if v2:
            types += ["brid:IntBridgeInstallation", "brid:BridgeConstructionElement"]
    if op.export_tunnels:
        types += ["tun:Tunnel", "tun:TunnelPart", "tun:HollowSpace", "tun:TunnelFurniture",
                  "tun:TunnelInstallation", "tun:TunnelConstructiveElement"]
    if op.export_vegetation:
        types += ["veg:SolitaryVegetationObject", "veg:PlantCover"]
    if op.export_waterbodies:
        types += ["wtr:WaterBody", "wtr:WaterSurface", "wtr:WaterGroundSurface"]
        if v2:
            types += ["wtr:WaterClosureSurface"]
    if op.export_construction and not v2:
        types += ["con:OtherConstruction", "con:Window", "con:Door"]
    if op.export_transportation:
        types += ["tran:Road", "tran:Railway", "tran:Track", "tran:Square"]
        if v2:
            types += ["tran:TrafficArea", "tran:AuxiliaryTrafficArea"]
        else:
            types += ["tran:Waterway", "tran:Section", "tran:Intersection", "tran:TrafficSpace",
                      "tran:AuxiliaryTrafficSpace", "tran:TrafficArea", "tran:AuxiliaryTrafficArea",
                      "tran:ClearanceSpace", "tran:Marking", "tran:Hole"]
    if op.export_cityfurniture:
        types += ["frn:CityFurniture"]
    if op.export_landuse:
        types += ["luse:LandUse"]
    if op.export_relief:
        types += ["dem:ReliefFeature", "dem:TINRelief", "dem:RasterRelief", "dem:MassPointRelief",
                  "dem:BreaklineRelief"]
    if op.export_generics:
        types += ["grp:CityObjectGroup"]
        if v2:
            types += ["gen:GenericCityObject"]
        else:
            types += ["gen:GenericOccupiedSpace", "gen:GenericLogicalSpace", "gen:GenericUnoccupiedSpace",
                      "gen:GenericThematicSurface"]
    return types


def _write_scene_gml(op, ctx, out_path: str, target: str = "file") -> dict:
    """Write ``op.scope`` of the scene to ``out_path`` with the export operator's options (file and DB
    export). Returns the written/skipped record (also in ``scene["cgml_last_export"]``, B56)."""
    from .shared import export_scope
    with export_scope.export_selection(ctx.view_layer, ctx.scene, op.scope, op.scope_gml_ids):
        _write_scene_gml_scoped(op, ctx, out_path)
    rec = export_scope.record_export(ctx.scene, out_path, op.citygml_version, op.scope, target)
    print(f"[CityGML] {export_scope.summary(rec)}")
    return rec


def _write_scene_gml_scoped(op, ctx, out_path: str) -> None:
    if op.citygml_version == '2.0':
        from .io_v2 import export_citygml2_from_blender as export_func
    else:
        from .io.gml3_writer import export_blender_to_citygml3 as export_func
    print(f"[CityGML] exporter={export_func.__module__}.{export_func.__name__}")
    kwargs = dict(
        use_inner_outer_script=op.use_inner_outer_script,
        autofill_new_surfaces=op.autofill_new_surfaces,
        split_wall_roof_surfaces=op.split_wall_roof_surfaces,
        write_lod_solid_refs=op.write_lod_solid_refs,
        export_types=_export_types(op),
    )
    if hasattr(op, "unclassified_surface_type"):  # file export only
        kwargs["unclassified_surface_type"] = op.unclassified_surface_type
    from .shared.attribute_utils import features_without_function
    from .shared.facade_placement import flat_synthetic_openings
    with flat_synthetic_openings(list(ctx.scene.objects)):
        if op.write_offset_back:
            export_func(out_path, ctx, op.srs_name, op.feature_type_prop, **kwargs)
        else:
            offset = ctx.scene.cgml3_offset
            old = tuple(offset)
            try:
                offset[0] = offset[1] = offset[2] = 0.0
                export_func(out_path, ctx, op.srs_name, op.feature_type_prop, **kwargs)
            finally:
                offset[0], offset[1], offset[2] = old
    # B47: no fabricated bldg:function; say which buildings went out without one
    missing = features_without_function(o for o in ctx.scene.objects if o.visible_get())
    if missing:
        op.report({'WARNING'}, f"{len(missing)} building(s) without bldg:function (set it on the Attributes card): "
                               + ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))


def _job_report(op):
    """``report(level, text)`` for a job's ``on_done``: the operator itself in background mode (the job
    ends before ``execute`` returns), else a popup (the operator is gone by then)."""
    if bpy.app.background:
        return op.report
    from .ops.browse_gml import _notify
    return lambda level, text: _notify(text, next(iter(level)))


# ---------------- Reload UI ----------------
class CGML3_PT_Reload(Panel):
    bl_label = "Plugin Reload"
    bl_idname = "CGML3_PT_Reload"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = header_panel.ROOT_ID
    bl_order = 999
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        # B97: the bottom of the Open step (not only with Developer Extras): code edits come in
        # without restarting Blender.
        return header_panel.on_tab(context, "OPEN") and not header_panel.in_sidebar(context)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.separator()
        col.operator("cgml3.reload_addon", text="Reload Extension", icon='FILE_REFRESH')


class CGML3_OT_ReloadAddon(Operator):
    """Reload the extension's code from disk without restarting Blender (shows the newest source file's time)"""
    bl_idname = "cgml3.reload_addon"
    bl_label = "Reload City Modeler"

    def execute(self, context):
        def _reload_addon_safe():
            try:
                # The addon's own dotted module name - NOT its first path
                # segment. A legacy single-folder addon's __package__ has no
                # dots, so this was harmless there, but a Blender Extension
                # is loaded as "bl_ext.<repo>.<extension_id>": splitting on
                # "." and keeping [0] left just "bl_ext" - Blender's *shared*
                # namespace package for every installed extension, not this
                # addon. disable()/enable() then operated on "bl_ext" itself,
                # and the sys.modules cleanup below deleted every extension's
                # loaded modules from that namespace, not just this addon's.
                module_name = __package__ or __name__

                # Never addon_utils.disable()/enable(): disable() refreshes the
                # extension wheels while this add-on is off, Blender then deletes
                # its pyproj/lxml/psycopg wheels and, with their DLLs loaded,
                # stops halfway (see common/wheel_repair.py).
                sys.modules[module_name].unregister()
                to_delete = [m for m in list(sys.modules)
                             if m == module_name or m.startswith(module_name + ".")]
                for m in to_delete:
                    del sys.modules[m]
                importlib.invalidate_caches()
                importlib.import_module(module_name).register()

                # B97: proof the new code is in: how many modules came back and the newest source file
                skip = {"scripts", "wheels", "docs", "test_files", "cac-out", "tickets", "__pycache__"}
                newest = (0.0, "")
                for base, dirs, files in os.walk(os.path.dirname(os.path.abspath(__file__))):
                    dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
                    for name in files:
                        if name.endswith(".py"):
                            path = os.path.join(base, name)
                            newest = max(newest, (os.path.getmtime(path), os.path.relpath(path, os.path.dirname(__file__))))
                loaded = sum(1 for m in sys.modules if m == module_name or m.startswith(module_name + "."))
                stamp = datetime.datetime.fromtimestamp(newest[0]).strftime("%H:%M:%S")
                text = f"Reloaded {loaded} modules. Newest file: {newest[1]} ({stamp})"
                print(f"✅ {text}")
                if not bpy.app.background:
                    bpy.context.window_manager.popup_menu(
                        lambda self, ctx: self.layout.label(text=text), title="Extension reloaded", icon='CHECKMARK')
            except Exception as e:
                print(f"❌ Fehler beim Reload: {e}")
            return None

        bpy.app.timers.register(_reload_addon_safe, first_interval=0.1)
        return {'FINISHED'}

# ---------------- Filter Helper Operators ----------------
class CGML3_OT_SelectAllLODs(Operator):
    """Select all LOD levels"""
    bl_idname = "cgml3.select_all_lods"
    bl_label = "Select All LODs"
    
    def execute(self, context):
        cgml3 = context.scene.cgml3
        cgml3.lod_0 = True
        cgml3.lod_1 = True
        cgml3.lod_2 = True
        cgml3.lod_3 = True
        cgml3.lod_4 = True
        return {'FINISHED'}

class CGML3_OT_DeselectAllLODs(Operator):
    """Deselect all LOD levels"""
    bl_idname = "cgml3.deselect_all_lods"
    bl_label = "Deselect All LODs"
    
    def execute(self, context):
        cgml3 = context.scene.cgml3
        cgml3.lod_0 = False
        cgml3.lod_1 = False
        cgml3.lod_2 = False
        cgml3.lod_3 = False
        cgml3.lod_4 = False
        return {'FINISHED'}

class CGML3_OT_SelectAllFeatureTypes(Operator):
    """Select all feature types"""
    bl_idname = "cgml3.select_all_feature_types"
    bl_label = "Select All Types"
    
    def execute(self, context):
        cgml3 = context.scene.cgml3
        cgml3.import_buildings = True
        cgml3.import_bridges = True
        cgml3.import_tunnels = True
        cgml3.import_vegetation = True
        cgml3.import_water = True
        cgml3.import_transportation = True
        cgml3.import_cityfurniture = True
        cgml3.import_landuse = True
        cgml3.import_relief = True
        cgml3.import_generics = True
        return {'FINISHED'}

class CGML3_OT_DeselectAllFeatureTypes(Operator):
    """Deselect all feature types"""
    bl_idname = "cgml3.deselect_all_feature_types"
    bl_label = "Deselect All Types"
    
    def execute(self, context):
        cgml3 = context.scene.cgml3
        cgml3.import_buildings = False
        cgml3.import_bridges = False
        cgml3.import_tunnels = False
        cgml3.import_vegetation = False
        cgml3.import_water = False
        cgml3.import_transportation = False
        cgml3.import_cityfurniture = False
        cgml3.import_landuse = False
        cgml3.import_relief = False
        cgml3.import_generics = False
        return {'FINISHED'}

# ---------------- Preferences ----------------
class CGML3_Prefs(AddonPreferences):
    bl_idname = __name__

    citydb_exe: StringProperty(
        name="citydb executable",
        subtype="FILE_PATH",
        description=(
            "Path to citydb-tool. "
            "Windows: z.B. citydb-tool-1.3.0\\citydb.bat | "
            "Linux: z.B. /opt/citydb-tool/citydb"
        ),
        default="citydb-tool-1.3.0\\citydb.bat" if os.name == "nt" else "/opt/citydb-tool/citydb"
    )
    use_docker: BoolProperty(
        name="use Docker",
        default=False,
        description="citydb-tool start via Container"
    )
    docker_image: StringProperty(
        name="Docker image",
        default="ghcr.io/3dcitydb/citydb-tool:latest",
    )

    city_modeler_view: BoolProperty(
        name="City Modeler view",
        default=True,
        description="Open Blender in the City Modeler workspace: outliner left, the City Modeler panels in Properties > Tool on the right",
        update=lambda self, ctx: cgml3_ui.city_modeler_view.on_pref_update(self, ctx),
    )

    frame_view_after_import: BoolProperty(
        name="Frame view after import",
        default=True,
        description="After an import, frame the new objects in every 3D view and widen the clip range to fit them",
    )

    # Browse first (T08)
    browse_threshold_mb: FloatProperty(
        name="Browse first from (MB)",
        default=20.0, min=0.0, soft_max=1000.0,
        description="Files at least this large open with 'Browse first' (boxes, then load the selection)",
    )
    browse_threshold_features: IntProperty(
        name="Browse first above (features)",
        default=30, min=0,
        description="Files with more than this many top-level features (estimated for big files) open with 'Browse first'",
    )
    browse_load_limit: IntProperty(
        name="Load at most (buildings)",
        default=10, min=1, soft_max=200,
        description="Largest number of selected buildings that 'Load' imports in one go",
    )
    index_cache_max_mb: FloatProperty(
        name="Index cache limit (MB)",
        default=500.0, min=0.0, soft_max=10000.0,
        description="Cached file indexes beyond this size are deleted, oldest first",
    )
    # 3DCityDB Browse first (T09)
    db_browse_limit: IntProperty(
        name="Boxes per query",
        default=1000, min=1, max=20000,
        description="Largest number of buildings one 'Show Buildings in View' query shows (hard limit 20,000); "
                    "more are reached with 'Next page'",
    )
    db_preview_footprint: BoolProperty(
        name="Footprint from ground surfaces",
        default=False,
        description="Show buildings without real LoD0 as the 2D union of their GroundSurface geometry "
                    "(computed by the database for the shown area only; slower than boxes)",
    )

    # 3DCityDB servers (no passwords; see citydb/credentials.py)
    db_profiles: CollectionProperty(type=citydb_connection.CGML3_DBProfile)
    db_active_profile: StringProperty(
        name="Server",
        description="3DCityDB server to browse",
        update=citydb_connection._on_active_profile_changed,
    )

    experimental_authoring_core: BoolProperty(
        name="Experimental: authoring core",
        default=False,
        description=(
            "Off by default; with the flag off nothing writes authoring "
            "records and behaviour is unchanged (PLAN.md §4.8, R6). Can "
            "also be enabled headless with CGML_AUTHORING_CORE=1."
        ),
    )

    # B60 (agent plan A5): CityDoctor is installed by the user, never bundled
    citydoctor_validator: StringProperty(
        name="CityDoctor validator",
        subtype="FILE_PATH",
        description=(
            "The start script of the unzipped CityDoctorValidation release (or its folder). "
            "Windows: C:\\CityDoctor\\start.bat | Linux and macOS: ~/CityDoctor/start.sh. "
            "Also accepted: your own command that takes a CityGML file and writes "
            "<file>.citydoctor-report.xml. Empty: the CITYDOCTOR_VALIDATE_SH environment variable"
        ),
        default="",
    )

    experimental_gs_surface_dispatch: BoolProperty(
        name="Experimental: per-surface IR export (WallSurface + Windows)",
        default=False,
        description=(
            "Supported opt-in for the WallSurface + N record-backed Window "
            "slice (P3b.6 disposition; not merely a prototype, but still "
            "off by default pending broader real-fixture evidence). "
            "Building-level IR dispatch always wins first and is unchanged "
            "by this flag. With it off, per-Building IR dispatch behaves "
            "exactly as without this flag. With it on, a Building that is "
            "not itself IR-eligible may still export one or more of its own "
            "eligible record-backed WallSurfaces (each with any number of "
            "hosted rectangular Windows) through the record/IR path while "
            "the rest of that Building - any unsupported surface, Door, "
            "RoofSurface, depth-authored Window, or a surface whose legacy "
            "mirror fails the record/legacy consistency check - stays on "
            "the legacy mesh/material export path. Can also be enabled "
            "headless with CGML_GS_SURFACE_DISPATCH=1."
        ),
    )

    def draw(self, ctx):
        col = self.layout.column()
        col.prop(self, "frame_view_after_import")
        col.separator()
        box = col.box()
        box.label(text="Browse first (large files)", icon='VIEWZOOM')
        box.prop(self, "browse_threshold_mb")
        box.prop(self, "browse_threshold_features")
        box.prop(self, "browse_load_limit")
        row = box.row()
        row.prop(self, "index_cache_max_mb")
        row.operator("cgml3.browse_clear_cache", text="", icon='TRASH')
        box.prop(self, "db_browse_limit")
        box.prop(self, "db_preview_footprint")
        col.separator()
        box = col.box()
        box.label(text=f"3DCityDB servers: {len(self.db_profiles)}", icon='NETWORK_DRIVE')
        for p in self.db_profiles:
            box.label(text=f"{p.name}  -  {p.user}@{p.host}:{p.port}", icon='DOT')
        box.label(text="Manage them in Properties > Tool tab > Open", icon='INFO')
        col.separator()
        col.label(text="Requires 3DCityDB 5.x (citydb-tool).")

        col.prop(self, "use_docker")
        if self.use_docker:
            col.prop(self, "docker_image")
            col.label(text="With Docker, input/output files and configuration")
            col.label(text="must be in the same mounted folder (/data).")
        else:
            col.prop(self, "citydb_exe")

        col.separator()
        from .ops.validation import draw_citydoctor_setup
        draw_citydoctor_setup(col.box(), self)  # B60 / B86: the same text as the ? button in 3 · Save
        col.separator()
        col.prop(self, "experimental_authoring_core")
        col.prop(self, "experimental_gs_surface_dispatch")
        col.separator()
        row = col.row()
        row.label(text="HafenCity University Hamburg · NGCN")
        row.operator('wm.url_open', text="HCU Hamburg", icon='URL').url = "https://www.hcu-hamburg.de/"

# ---------------- Runtime / Scene Props ----------------
class CGML3_Runtime(PropertyGroup):
    tmp_dir: StringProperty(name="Temp Dir", subtype='DIR_PATH', default="")
    xsd_validate: BoolProperty(
        name="XSD-Validierung on",
        default=False,
        description=(
            "Validate against CityGML-3 XSD after import/export. "
            "Uses local schemas, falls back to OGC online schemas if needed."
        ),
    )
    xsd_lenient_validation: BoolProperty(
        name="Lenient Validation (3DCityDB)",
        default=False,
        description=(
            "For XSD errors from 3DCityDB exports (invalid gml:id values, incorrect element order, "
            "textureParameterization placement, etc.), only warn instead of aborting import. "
            "Useful for data from 3DCityDB version 5."
        ),
    )

    # Filter Options
    use_bbox_filter: BoolProperty(
        name="Bounding Box Filter",
        default=False,
        description="Limit import/export to a specific geographic area"
    )
    bbox_min_x: FloatProperty(name="Min X", default=0.0)
    bbox_min_y: FloatProperty(name="Min Y", default=0.0)
    bbox_max_x: FloatProperty(name="Max X", default=0.0)
    bbox_max_y: FloatProperty(name="Max Y", default=0.0)
    bbox_coords_string: StringProperty(
        name="BBox Coordinates",
        description="Format: MinX, MinY, MaxX, MaxY (e.g., 24487802, 6820388.5, 24488468, 6820804.5)",
        default=""
    )
    
    use_gmlid_filter: BoolProperty(
        name="GML ID Filter",
        default=False,
        description="Limit import/export to specific CityObjects"
    )
    gmlid_filter: StringProperty(
        name="GML IDs",
        description="Comma-separated list of GML IDs (e.g., 'DENW11AL0000h5UU_,DENW11AL0000h5UV_')",
        default="",
        maxlen=16384
    )
    
    # LOD Filter (for direct GML imports)
    use_lod_filter: BoolProperty(
        name="LOD Filter",
        default=False,
        description="Only import specific LODs (Level of Detail)"
    )
    lod_0: BoolProperty(name="LOD 0", default=True, description="Import LOD 0 geometries")
    lod_1: BoolProperty(name="LOD 1", default=True, description="Import LOD 1 geometries")
    lod_2: BoolProperty(name="LOD 2", default=True, description="Import LOD 2 geometries")
    lod_3: BoolProperty(name="LOD 3", default=True, description="Import LOD 3 geometries")
    lod_4: BoolProperty(name="LOD 4", default=True, description="Import LOD 4 geometries (CityGML 2.0 only)")
    
    # Feature-Type Filter (for direct GML imports)
    use_feature_type_filter: BoolProperty(
        name="Feature-Typ Filter",
        default=False,
        description="import only specific Feature-Typs"
    )
    import_buildings: BoolProperty(name="Buildings", default=True)
    import_bridges: BoolProperty(name="Bridges", default=True)
    import_tunnels: BoolProperty(name="Tunnels", default=True)
    import_vegetation: BoolProperty(name="Vegetation", default=True)
    import_water: BoolProperty(name="Water Bodies", default=True)
    import_transportation: BoolProperty(name="Transportation", default=True)
    import_cityfurniture: BoolProperty(name="City Furniture", default=True)
    import_landuse: BoolProperty(name="Land Use", default=True)
    import_relief: BoolProperty(name="Relief", default=True)
    import_generics: BoolProperty(name="Generic Objects", default=True)
    
    # Import statistics (read-only, set by importer)
    last_import_total: IntProperty(name="Total Features Found", default=0)
    last_import_imported: IntProperty(name="Features Imported", default=0)
    last_import_filtered: IntProperty(name="Features Filtered", default=0)
    
    # Import transformation options
    import_local: BoolProperty(
        name="Local Import (without Georeference)",
        default=False,
        description="Import objects locally at the Blender origin (0,0,0) without CRS transformation"
    )

    mesh_validate: BoolProperty(
        name="Run mesh.validate()",
        default=False,
        description=(
            "Runs mesh.validate() after import. Can remove faces for special geometries; "
            "disable for debugging/if faces are missing."
        ),
    )

    use_semantic_objects: BoolProperty(
        name="Semantic Hierarchy (Building > WallSurface > Window/Door)",
        default=True,
        description=(
            "CityGML 2 and 3: explode each Building's boundary surfaces and openings "
            "(WallSurface, Window/Door, Balcony/Chimney installations) into their own "
            "selectable, editable objects instead of one flat mesh. Round-trips through "
            "export/re-import with source semantics, appearance and gml:ids preserved "
            "(see docs/agent/semantic-hierarchy/issues/13-roundtrip-matrix-and-flag-default.md). "
            "Disable to import as a single flat mesh instead."
        ),
    )

    export_debug: BoolProperty(
        name="Export Debug (Console)",
        default=False,
        description="Writes additional debug info during export to the Blender console",
    )

    import_streaming_threshold_mb: FloatProperty(
        name="Large File Warning (MB)",
        default=30.0,
        min=1.0,
        max=10000.0,
        description=(
            "File size (in MB) from which Load all warns that the whole file is read into memory. "
            "Files above 200 MB are not loaded at all: use Browse first or 3DCityDB v5"
        ),
    )

    export_schema_location_mode: EnumProperty(
        name="schemaLocation",
        description="How xsi:schemaLocation should be written (for validators like FME)",
        items=[
            ('REMOTE', 'Remote (schemas.opengis.net)', 'Uses online XSD URLs in schemaLocation'),
            ('LOCAL', 'Local (Schema/... in the addon)', 'Uses local XSD paths from the addon (file:// URIs)'),
            ('NONE', 'Without schemaLocation', 'Does not write xsi:schemaLocation'),
        ],
        default='REMOTE',
    )
    
    # 3DCityDB import/export target of this scene, set by picking a city model in
    # the 3DCityDB panel. Saved with the .blend; the password never is.
    db_profile: StringProperty(name="Server Profile", default="")
    db_host: StringProperty(name="Host", default="localhost")
    db_port: StringProperty(name="Port", default="5432")
    db_name: StringProperty(name="Database", default="citydb")
    db_user: StringProperty(name="User", default="postgres")
    db_schema: StringProperty(name="Schema", default="citydb")
    db_sslmode: EnumProperty(name="SSL", items=citydb_connection.SSL_ITEMS, default="prefer")
    db_target_label: StringProperty(name="City Model", default="")
    db_target_srid: IntProperty(name="SRID", default=0)

    db_import_ui_show_connection: BoolProperty(name="DB-Import Connection", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_expert: BoolProperty(name="DB-Import Expert", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_crs: BoolProperty(name="DB-Import CRS", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_filters: BoolProperty(name="DB-Import Filter", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_validity: BoolProperty(name="DB-Import Validity", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_format: BoolProperty(name="DB-Import Appearance", default=False, options={'SKIP_SAVE'})
    db_import_ui_show_config: BoolProperty(name="DB-Import Config", default=False, options={'SKIP_SAVE'})

    db_export_ui_show_connection: BoolProperty(name="DB-Export Connection", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_expert: BoolProperty(name="DB-Export Expert", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_export: BoolProperty(name="DB-Export CityGML", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_config: BoolProperty(name="DB-Export Config", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_import_options: BoolProperty(name="DB-Export Import Options", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_import_filters: BoolProperty(name="DB-Export Import Filters", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_import_appearance: BoolProperty(name="DB-Export Import Appearance", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_import_citygml: BoolProperty(name="DB-Export Import CityGML", default=False, options={'SKIP_SAVE'})
    db_export_ui_show_upgrade: BoolProperty(name="DB-Export Upgrade", default=False, options={'SKIP_SAVE'})
    filter_ui_show_advanced: BoolProperty(name="File Import Advanced", default=False, options={'SKIP_SAVE'})

def _ensure_scene_props():
    sc = bpy.types.Scene

    if not hasattr(sc, "cgml3_offset"):
        sc.cgml3_offset = FloatVectorProperty(
            name="CityModel-Offset", size=3, default=(0.0, 0.0, 0.0)
        )


def _draw_foldout_section(layout, data, prop_name, label):
    box = layout.box()
    header = box.row(align=True)
    is_open = bool(getattr(data, prop_name))
    header.prop(
        data,
        prop_name,
        text=label,
        icon='TRIA_DOWN' if is_open else 'TRIA_RIGHT',
        emboss=False,
    )
    if not is_open:
        return None
    return box.column(align=True)

# ---------------- Panels ----------------
class CGML3_PT_Main(Panel):
    """Step 1 Open (B02): get buildings from a file or 3DCityDB, a few at a time."""
    bl_label = "Open"
    bl_idname = "CGML3_PT_Main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = header_panel.ROOT_ID  # B90: steps sit under the status strip, in this order
    bl_order = 1
    bl_options = {'HIDE_HEADER'}  # B96: the tab above already names the step

    @classmethod
    def poll(cls, ctx):
        return header_panel.on_tab(ctx, "OPEN")

    def draw(self, ctx):
        layout = self.layout
        sc = ctx.scene.cgml3

        s = browse_gml.active_session(ctx)
        if s is not None:  # B12: the picked boxes' next step comes first
            browse_panel.draw_show_unshow(layout, ctx, s)

        box = layout.box()
        box.label(text="From a file (CityGML 2.0 / 3.0)", icon='FILE')
        row = box.row()
        row.scale_y = 1.3
        colors.add(row, "cgml3.browse_gml_file", text="Preview File (pick buildings)...", icon='VIEWZOOM')
        colors.add(box, "cgml3.import_gml_file", text="Open Whole File (small files)...", icon='IMPORT')
        box.label(text="or drop a .gml file into the viewport", icon='BLANK1')

        box = layout.box()
        box.label(text="From 3DCityDB", icon='NETWORK_DRIVE')
        # B83: one way in (Connect, then Browse); the citydb-tool dialog sits at the end of Browse
        if sc.db_profile and sc.db_name:
            box.label(text=f"City model: {sc.db_target_label or sc.db_schema}", icon='WORLD')
            box.label(text="Pick buildings in 3DCityDB Browse below", icon='BLANK1')
        else:
            box.label(text="Connect below, then pick buildings", icon='BLANK1')

class CGML3_PT_Filter(Panel):
    """B83: options of Open Whole File and the citydb-tool dialog; rarely needed ones fold away."""
    bl_label = "File Import Options"
    bl_idname = "CGML3_PT_Filter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, ctx):
        layout = self.layout
        cgml3 = ctx.scene.cgml3

        # Semantic hierarchy (ticket 06, default flipped on in ticket 13)
        layout.prop(cgml3, "use_semantic_objects")

        # LOD Filter
        box = layout.box()
        row = box.row()
        row.prop(cgml3, "use_lod_filter")
        col = box.column()
        col.enabled = cgml3.use_lod_filter
        row = col.row(align=True)
        row.operator("cgml3.select_all_lods", text="All", icon='CHECKBOX_HLT')
        row.operator("cgml3.deselect_all_lods", text="None", icon='CHECKBOX_DEHLT')
        grid = col.grid_flow(row_major=True, columns=3, even_columns=True, even_rows=True, align=True)
        for i in range(5):
            grid.prop(cgml3, f"lod_{i}")

        # Feature-type filter: the add-on's modules first (scope decision), the rest under Advanced
        box = layout.box()
        box.prop(cgml3, "use_feature_type_filter")
        col = box.column(align=True)
        col.enabled = cgml3.use_feature_type_filter
        row = col.row(align=True)
        row.operator("cgml3.select_all_feature_types", text="All", icon='CHECKBOX_HLT')
        row.operator("cgml3.deselect_all_feature_types", text="None", icon='CHECKBOX_DEHLT')
        col.prop(cgml3, "import_buildings")
        col.prop(cgml3, "import_transportation")
        col.prop(cgml3, "import_cityfurniture")

        # GML ID Filter
        box = layout.box()
        box.prop(cgml3, "use_gmlid_filter")
        col = box.column()
        col.enabled = cgml3.use_gmlid_filter
        col.prop(cgml3, "gmlid_filter")

        # Bounding Box Filter
        box = layout.box()
        row = box.row()
        row.prop(cgml3, "use_bbox_filter")

        col = box.column()
        col.enabled = cgml3.use_bbox_filter
        
        current_epsg = header_panel.scene_epsg(ctx.scene) or "Unknown"
        col.label(text=f"Bounding Box ({current_epsg}):")
        
        # Info-Text
        if current_epsg == "Unknown":
            info_row = col.row()
            info_row.label(text="No CRS loaded.", icon='INFO')
        else:
            info_row = col.row()
            info_row.label(text="Coordinates in the CRS of the file to be imported", icon='INFO')
        
        # Quick input: comma-separated coordinates
        col.separator()
        col.label(text="Quick input (MinX, MinY, MaxX, MaxY):")
        row = col.row(align=True)
        row.prop(cgml3, "bbox_coords_string", text="")
        row.operator("cgml3.parse_bbox_string", text="", icon='IMPORT')
        
        col.separator()
        col.label(text="Individual values:")
        
        # Min X/Y
        row = col.row()
        row.prop(cgml3, "bbox_min_x")
        row.prop(cgml3, "bbox_min_y")
        
        # Max X/Y
        row = col.row()
        row.prop(cgml3, "bbox_max_x")
        row.prop(cgml3, "bbox_max_y")
        
        # Copy to String button
        row = col.row()
        row.operator("cgml3.bbox_to_string", text="Copy values to string", icon='COPYDOWN')

        adv = _draw_foldout_section(layout, cgml3, "filter_ui_show_advanced", "Advanced")
        if adv:
            self._draw_advanced(adv, cgml3)

        # Import Statistics
        if cgml3.last_import_total > 0:
            box = layout.box()
            box.label(text="Last Import Statistics:", icon='INFO')
            col = box.column(align=True)
            col.label(text=f"  Total Features Found: {cgml3.last_import_total}")
            col.label(text=f"  Features Imported: {cgml3.last_import_imported}")
            if cgml3.last_import_filtered > 0:
                col.label(text=f"  Features Filtered: {cgml3.last_import_filtered}", icon='FILTER')

    @staticmethod
    def _draw_advanced(layout, cgml3):
        col = layout.column(align=True)
        col.enabled = cgml3.use_feature_type_filter
        col.label(text="Other feature types (3DCityDB work):")
        for name in ("import_bridges", "import_tunnels", "import_vegetation", "import_water",
                     "import_landuse", "import_relief", "import_generics"):
            col.prop(cgml3, name)

        # Local Import
        box = layout.box()
        box.prop(cgml3, "import_local", text="Local Import (without georeference)")
        if cgml3.import_local:
            box.label(text="Objects will be imported at the origin (0,0,0)", icon='INFO')

        # Mesh validate option with copy-data-path button
        box = layout.box()
        row = box.row(align=True)
        row.prop(cgml3, "mesh_validate", text="Run mesh validate()")
        op = row.operator("ui.copy_data_path_button", text="", icon='COPYDOWN')
        try:
            op.full_path = True
        except Exception:
            pass
        box.label(text="Disable for testing if faces are missing", icon='INFO')

        # Large-file warning
        box = layout.box()
        box.label(text="Large files", icon='SETTINGS')
        box.prop(cgml3, "import_streaming_threshold_mb", text="warn starting at (MB)")
        box.label(text="Load all above the threshold: warning; above 200 MB: refused", icon='INFO')

        # Export debug
        box = layout.box()
        box.label(text="Debug", icon='INFO')
        box.prop(cgml3, "export_debug", text="Export Debug (Console)")

def _draw_db_target_section(layout, op, ui_state, flag_name):
    """Connection part of the DB dialogs: the chosen city model, editable on request."""
    box = layout.box()
    label = ui_state.db_target_label if (
        ui_state.db_name == op.db_name and ui_state.db_schema == op.db_schema) else ""
    box.label(text=f"3DCityDB: {label or op.db_schema or 'citydb'}", icon='NETWORK_DRIVE')
    box.label(text=f"{op.db_name} / {op.db_schema or 'citydb'} @ {op.db_host}:{op.db_port}  ({op.db_user})")
    box.label(text="Choose another city model in the 3DCityDB panel; password: Password > Set", icon='INFO')
    sub = _draw_foldout_section(layout, ui_state, flag_name, "Edit connection")
    if sub:
        sub.prop(op, "db_host"); sub.prop(op, "db_port")
        sub.prop(op, "db_name"); sub.prop(op, "db_schema")
        sub.prop(op, "db_user"); sub.prop(op, "db_sslmode")


def _draw_db_area_section(layout, ctx):
    """Area part of the DB dialogs: the scene's bounding box filter (Filter Options panel)."""
    cgml3 = ctx.scene.cgml3
    layout.prop(cgml3, "use_bbox_filter", text="Limit to an area")
    col = layout.column(align=True)
    # the buttons switch the area on themselves; the manual fields only matter when it is on
    row = col.row(align=True)
    row.label(text="Area:")
    row.operator("cgml3.db_area_fill", text="Current view", icon='VIEW3D').source = 'VIEW'
    row.operator("cgml3.db_area_fill", text="Scene extent", icon='SCENE_DATA').source = 'SCENE'
    col = col.column(align=True)
    col.enabled = cgml3.use_bbox_filter
    row = col.row(align=True)
    row.prop(cgml3, "bbox_min_x"); row.prop(cgml3, "bbox_min_y")
    row = col.row(align=True)
    row.prop(cgml3, "bbox_max_x"); row.prop(cgml3, "bbox_max_y")
    if not cgml3.use_bbox_filter:
        layout.label(text="Off: the whole city model", icon='INFO')


class CGML3_OT_DBAreaFill(Operator):
    """Set the area of the DB dialogs (scene bounding box filter) from the 3D view or the scene"""
    bl_idname = "cgml3.db_area_fill"
    bl_label = "Set Area"
    bl_options = {'INTERNAL', 'UNDO'}

    source: EnumProperty(
        name="Source",
        items=[('VIEW', "Current view", "Ground area seen by the 3D view"),
               ('SCENE', "Scene extent", "Bounding box of the mesh objects in the scene")],
        default='VIEW',
    )

    def execute(self, ctx):
        from mathutils import Vector
        from .shared.browse.view_extent import ground_extent, to_crs, format_extent

        if self.source == 'VIEW':
            from .ops.browse_citydb import view_rays
            rays = view_rays(ctx)
            local = ground_extent(rays, 0.0) if rays else None
            if local is None:
                self.report({'ERROR'}, "No 3D view to take the area from")
                return {'CANCELLED'}
        else:
            pts = [o.matrix_world @ Vector(c) for o in ctx.scene.objects
                   if o.type == 'MESH' and len(o.data.vertices) for c in o.bound_box]
            if not pts:
                self.report({'ERROR'}, "The scene has no mesh objects")
                return {'CANCELLED'}
            local = (min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
        # Blender coordinates are relative to the World origin (same rule as the importers)
        world = gr.world()
        origin = (float(world.get(gr.X_ORIGIN, 0.0)), float(world.get(gr.Y_ORIGIN, 0.0))) if world else (0.0, 0.0)
        extent = to_crs(local, origin)
        cgml3 = ctx.scene.cgml3
        cgml3.bbox_min_x, cgml3.bbox_min_y, cgml3.bbox_max_x, cgml3.bbox_max_y = extent
        cgml3.use_bbox_filter = True
        self.report({'INFO'}, f"Area: {format_extent(extent)}")
        return {'FINISHED'}


def _db_target_from_scene(op, ctx):
    """Fill empty connection fields of a DB operator from the scene target."""
    d = ctx.scene.cgml3
    if not op.db_host: op.db_host = d.db_host
    if not op.db_port: op.db_port = d.db_port
    if not op.db_name: op.db_name = d.db_name
    if not op.db_user: op.db_user = d.db_user
    if not op.db_schema: op.db_schema = d.db_schema
    if op.db_sslmode == 'prefer': op.db_sslmode = d.db_sslmode


def _resolve_db_target(op, ctx):
    """(password, sslmode) for a DB operator, or None after reporting the error."""
    from .citydb.errors import CityDBError

    try:
        target, password, info = citydb_connection.resolve_target(
            op.db_host, op.db_port, op.db_name, op.db_user, op.db_schema, op.db_sslmode,
        )
    except CityDBError as exc:
        op.report({'ERROR'}, citydb_connection.describe_error(exc))
        if exc.detail:
            print(f"[3DCityDB] {exc.message} - {exc.detail}")
        return None
    if info is not None:
        print(f"[3DCityDB] Target verified: {target.label} - 3DCityDB {info.version}, EPSG:{info.srid}")
    return password, target.server.sslmode

# ---------------- Operator: BBox String Parser ----------------
class CGML3_OT_ParseBBoxString(Operator):
    bl_idname = "cgml3.parse_bbox_string"
    bl_label = "Parse BBox"
    bl_description = "Parse Bounding Box coordinates from string (MinX, MinY, MaxX, MaxY)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, ctx):
        cgml3 = ctx.scene.cgml3
        bbox_coords = parse_bbox_coords_string(cgml3.bbox_coords_string)
        
        if bbox_coords is None:
            if not str(cgml3.bbox_coords_string or "").strip():
                self.report({'WARNING'}, "No coordinates entered")
            else:
                self.report({'ERROR'}, "Invalid format. Expected: MinX, MinY, MaxX, MaxY")
            return {'CANCELLED'}

        min_x, min_y, max_x, max_y = bbox_coords
        cgml3.bbox_min_x = min_x
        cgml3.bbox_min_y = min_y
        cgml3.bbox_max_x = max_x
        cgml3.bbox_max_y = max_y

        self.report({'INFO'}, f"BBox set: ({min_x}, {min_y}) - ({max_x}, {max_y})")
        return {'FINISHED'}

class CGML3_OT_BBoxToString(Operator):
    bl_idname = "cgml3.bbox_to_string"
    bl_label = "Copy to String"
    bl_description = "Copy the individual values to the string field"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, ctx):
        cgml3 = ctx.scene.cgml3
        
        cgml3.bbox_coords_string = format_bbox_coords_string((
            cgml3.bbox_min_x,
            cgml3.bbox_min_y,
            cgml3.bbox_max_x,
            cgml3.bbox_max_y,
        ))
        
        self.report({'INFO'}, "Coordinates copied to string field")
        return {'FINISHED'}

# ---------------- Operator: File Import ----------------
class CGML3_OT_ImportGMLFile(Operator):
    bl_idname = "cgml3.import_gml_file"
    bl_label = "Import CityGML (File)"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Imports CityGML 2.0/3.0, optionally with Appearance/Textures"

    filepath: StringProperty(name="CityGML", subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.gml;*.xml", options={'HIDDEN'})
    filename_ext = ".gml"
    import_appearance: BoolProperty(
        name="Import Appearance",
        description="Imports textures and material colors from Appearance data. Semantic material slots and custom properties are preserved",
        default=True,
    )
    load_mode: EnumProperty(
        name="Open",
        items=[
            ('AUTO', "Automatic", "Large files ask: Browse first (recommended) or Load all; small files load completely"),
            ('ALL', "Load all", "Import the whole file"),
            ('BROWSE', "Browse first", "Show one box per building, then load only the selected ones"),
        ],
        default='AUTO',
        options={'SKIP_SAVE'},
    )

    _sniff_cache = {}

    def invoke(self, ctx, evt):
        # Drag & drop (FileHandler) and scripted calls pass a path: no file browser.
        if self.filepath:
            return self.execute(ctx)
        ctx.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    @classmethod
    def description(cls, context, props):
        # Only static text. No access to props.filepath, no parser calls.
        return "Imports CityGML files optionally with Appearance, UV, and textures"

    def _log_text(self, name_prefix, content):
        from .shared.export_helpers import log_text_to_blender
        log_text_to_blender(name_prefix, content)

    @classmethod
    def _sniff(cls, path):
        """Sniff summary of ``path``, cached by size and mtime (draw() runs often)."""
        from .shared.citygml_sniff import summary_or_none
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            return None
        key = (path, st.st_size, st.st_mtime_ns)
        if key not in cls._sniff_cache:
            if len(cls._sniff_cache) > 64:
                cls._sniff_cache.clear()
            cls._sniff_cache[key] = summary_or_none(path)
        return cls._sniff_cache[key]

    def draw(self, ctx):
        summary = self._sniff(self.filepath) if self.filepath else None
        if summary is not None and summary.is_citygml:
            box = self.layout.box()
            box.label(text=summary.label(), icon='FILE')
            if self._browse_recommended(ctx, summary):
                box.label(text="Large file: Browse first is recommended", icon='VIEWZOOM')
        self.layout.prop(self, "load_mode")
        box = self.layout.box()
        box.label(text="Appearance")
        box.prop(self, "import_appearance")

    @staticmethod
    def _browse_recommended(ctx, summary):
        from .shared.citygml_sniff import browse_recommended
        prefs = _get_citydb_prefs(ctx)
        return browse_recommended(
            summary,
            float(getattr(prefs, "browse_threshold_mb", 20.0)),
            int(getattr(prefs, "browse_threshold_features", 30)),
        )

    def _ask_browse_or_load(self, ctx, summary):
        path = self.filepath
        appearance = self.import_appearance
        n = summary.features_estimate
        threshold = int(getattr(_get_citydb_prefs(ctx), "browse_threshold_features", 30))
        from .shared.load_limits import LOAD_ALL_MAX_FEATURES, LOAD_ALL_MAX_MB
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
        except OSError:
            size_mb = 0.0
        if size_mb > LOAD_ALL_MAX_MB:
            too_large = f"{size_mb:,.0f} MB, limit {LOAD_ALL_MAX_MB:,.0f} MB"
        elif n > LOAD_ALL_MAX_FEATURES:
            too_large = f"{'~' if not summary.exact else ''}{n:,} features, limit {LOAD_ALL_MAX_FEATURES:,}"
        else:
            too_large = ""

        def draw_menu(menu, _context):
            col = menu.layout.column()
            col.label(text=f"{'~' if not summary.exact else ''}{n:,} features, Browse first above {threshold}")
            op = col.operator("cgml3.browse_gml_file", text="Browse first (recommended)", icon='VIEWZOOM')
            op.filepath = path
            if too_large:
                col.label(text=f"Too large for Load all ({too_large}): use Browse first or 3DCityDB v5", icon='ERROR')
            else:
                op = col.operator("cgml3.import_gml_file",
                                  text=f"Load all ({'~' if not summary.exact else ''}{n:,} features) - slow, needs much memory",
                                  icon='ERROR')
                op.filepath = path
                op.load_mode = 'ALL'
                op.import_appearance = appearance

        ctx.window_manager.popup_menu(draw_menu, title=summary.label(), icon='QUESTION')

    def execute(self, ctx):
        from .common.import_landing import ImportSnapshot
        from .shared.citygml_sniff import looks_like_citygml

        if not self.filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}
        if not os.path.isfile(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}
        # .xml is shared with many formats (drag & drop accepts it): require a CityGML namespace.
        if self.filepath.lower().endswith(".xml") and not looks_like_citygml(self.filepath):
            self.report({'ERROR'}, f"Not a CityGML file (no CityGML namespace in header): {Path(self.filepath).name}")
            return {'CANCELLED'}

        summary = self._sniff(self.filepath)
        if self.load_mode == 'BROWSE':
            return bpy.ops.cgml3.browse_gml_file(filepath=self.filepath)
        # Large files ask first; background runs (scripts, tests) have no popup and load all.
        if (self.load_mode == 'AUTO' and summary is not None and not bpy.app.background
                and self._browse_recommended(ctx, summary)):
            self._ask_browse_or_load(ctx, summary)
            return {'FINISHED'}

        # The Load-all size limit before anything reads the whole file (1.0 copy, XSD); the builder checks again.
        from .shared.load_limits import size_notice
        try:
            size_notice(os.path.getsize(self.filepath) / (1024 * 1024), float("inf"))
        except ImportTooLarge as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        warnings = []
        snapshot = ImportSnapshot()

        # CityGML 1.0 is read by the 2.0 importer after a namespace rewrite (T08).
        source = self.filepath
        translated = None
        if summary is not None and summary.version == '1.0':
            import tempfile
            from .shared.browse.fragment import write_translated_copy
            fd, translated = tempfile.mkstemp(prefix=Path(self.filepath).stem + "_v2_", suffix=".gml")
            os.close(fd)
            missing_ns = write_translated_copy(self.filepath, translated)
            source = translated
            print("[CityGML Import] CityGML 1.0: namespaces rewritten to 2.0 in a temporary copy")
            if missing_ns:
                warnings.append("CityGML 1.0 namespaces without 2.0 mapping: " + ", ".join(missing_ns))
        try:
            return self._import(ctx, source, snapshot, warnings)
        finally:
            if translated:
                try:
                    os.remove(translated)
                except OSError:
                    pass

    def _import(self, ctx, source, snapshot, warnings):
        from .common import import_citygml_auto
        from .common.import_landing import finish_import, report_import_error

        # Validation only if enabled
        if ctx.scene.cgml3.xsd_validate:
            problem, warning, xsd_log = _validate_citygml(source, True, ctx.scene.cgml3.xsd_lenient_validation)
            if xsd_log:
                self._log_text("citygml_xsd_check", xsd_log)
            if problem:
                self.report({'ERROR'}, problem)
                return {'CANCELLED'}
            if warning:
                self.report({'WARNING'}, f"XSD warning (3DCityDB): {warning[:200]}... Import will continue.")
                print(f"[CityGML Import] XSD warning (lenient): {warning}")
                warnings.append("XSD (lenient)")

        # the import itself (BBOX / gml:id filters are applied by the builder)
        try:
            warnings.extend(import_citygml_auto(source, ctx, import_appearance=self.import_appearance))
        except Exception as exc:
            report_import_error(self, exc)
            return {'CANCELLED'}

        level, message, _summary = finish_import(
            ctx, snapshot, frame=_frame_view_after_import(ctx), warnings=warnings,
        )
        self.report({level}, message)
        return {'FINISHED'}

# ---------------- Operator: Datei-Export ----------------
class CGML3_OT_ExportGMLFile(Operator):
    bl_idname = "cgml3.export_gml_file"
    bl_label = "Export CityGML (File)"
    bl_options = {'REGISTER'}
    bl_description = "Export CityGML 2.0/3.0, including Appearance/Textures"

    filepath: StringProperty(name="Output File", subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.gml;*.xml", options={'HIDDEN'})
    filename_ext = ".gml"
    
    citygml_version: EnumProperty(
        name="CityGML Version",
        description="Select the CityGML version for export",
        items=[
            ('3.0', 'CityGML 3.0', 'Export as CityGML 3.0'),
            ('2.0', 'CityGML 2.0', 'Export as CityGML 2.0'),
        ],
        default='3.0'
    )
    
    srs_name: StringProperty(
        name="srsName",
        description="Target CRS (e.g., 'EPSG:25832'). Leave empty to use World['cgml_CRS'].",
        default=""
    )
    feature_type_prop: StringProperty(name="Feature Type Property", default="ModelType")
    write_offset_back: BoolProperty(
        name="Write Origin Offset",
        description="Add the stored CityModel offset back during export",
        default=True
    )
    use_inner_outer_script: BoolProperty(
        name="Automatically Detect Interior Surfaces",
        description="Uses get_inner_and_outer_rings.py to automatically detect interior surfaces.",
        default=False,
    )

    autofill_new_surfaces: BoolProperty(
        name="Automatically Process New Surfaces",
        description="Only for newly modeled faces without CityGML metadata: detects interior faces, adopts appearance from the corresponding exterior, and assigns surface type for new roof/wall faces without modifying existing imported interior structures.",
        default=False,
    )

    unclassified_surface_type: EnumProperty(
        name="Standard Surface Type",
        description="Surface type for faces without existing material or face semantics",
        items=[
            ('WallSurface', 'WallSurface', 'Export unclassified faces as WallSurface'),
            ('RoofSurface', 'RoofSurface', 'Export unclassified faces as RoofSurface'),
            ('GroundSurface', 'GroundSurface', 'Export unclassified faces as GroundSurface'),
            ('ClosureSurface', 'ClosureSurface', 'Export unclassified faces as ClosureSurface'),
        ],
        default='WallSurface',
    )

    split_wall_roof_surfaces: BoolProperty(
        name="Export Roof-/WallSurfaces Individually",
        description="Only for RoofSurface and WallSurface: instead of bundling as MultiSurface/MultiPolygon, write each polygon as a separate surface (better for individual selection in FME).",
        default=False,
    )

    write_lod_solid_refs: BoolProperty(
        name="Write LoD Solid from Surface References",
        description="Writes a lod{n}Solid referencing the boundary polygons via xlink:href (no installation geometry) when the shell is closed or the source file carried that Solid (B48). Authored buildings always get one when closed.",
        default=True,
    )

    # B56 (agent plan A1): what is written; a forgotten selection no longer exports one building silently
    scope: EnumProperty(
        name="Scope",
        description="Which features are written",
        items=[
            ('SCENE', 'Whole scene', 'Every visible feature of the scene, whatever is selected'),
            ('SELECTION', 'Selection', 'The selected features with their parts, installations and openings'),
            ('IDS', 'GML ids', 'The top-level features listed in GML ids'),
        ],
        default='SCENE',
    )
    scope_gml_ids: StringProperty(
        name="GML ids", default="",
        description="Comma-separated GML ids of top-level features (scope GML ids)")

    normalize_mixed_lods: BoolProperty(
        name="Normalize Mixed LoDs",
        description="Before export, detect top-level objects with mixed LoDs and raise all contained surfaces and parts to the highest LoD used in that top-level object.",
        default=False,
    )
    
    # Export Feature-Type Filter
    export_buildings: BoolProperty(
        name="Buildings export",
        description="Export Building and BuildingPart",
        default=True,
    )
    export_bridges: BoolProperty(
        name="Bridges export",
        description="Export Bridge and BridgePart",
        default=True,
    )
    export_tunnels: BoolProperty(
        name="Tunnels export",
        description="Export Tunnel and TunnelPart",
        default=True,
    )
    export_vegetation: BoolProperty(
        name="Vegetation export",
        description="Export SolitaryVegetationObject and PlantCover",
        default=True,
    )
    export_waterbodies: BoolProperty(
        name="WaterBodies export",
        description="Export WaterBody and Water-Surfaces",
        default=True,
    )
    export_construction: BoolProperty(
        name="Construction export",
        description="Export OtherConstruction, Window, Door",
        default=True,
    )
    export_cityfurniture: BoolProperty(
        name="CityFurniture export",
        description="Export CityFurniture",
        default=True,
    )
    export_landuse: BoolProperty(
        name="LandUse export",
        description="Export LandUse",
        default=True,
    )
    export_transportation: BoolProperty(
        name="Transportation export",
        description="Export Transportation features and surfaces",
        default=True,
    )
    export_relief: BoolProperty(
        name="Relief export",
        description="Export TINRelief and RasterRelief",
        default=True,
    )
    export_generics: BoolProperty(
        name="Generics export",
        description="Export Generics and CityObjectGroup",
        default=True,
    )

    def draw(self, ctx):
        from .shared.lod_helpers import LOD_EXPORT_POLICY_NAME

        layout = self.layout

        box = layout.box()
        box.label(text="CityGML / CRS")
        box.prop(self, "citygml_version")
        box.prop(self, "srs_name")
        box.prop(self, "scope")
        if self.scope == 'IDS':
            box.prop(self, "scope_gml_ids")

        box = layout.box()
        box.label(text="Geometry")
        box.prop(self, "write_offset_back")
        box.prop(self, "use_inner_outer_script")
        box.prop(self, "autofill_new_surfaces")
        box.prop(self, "unclassified_surface_type")
        box.prop(self, "split_wall_roof_surfaces")
        box.prop(self, "write_lod_solid_refs")
        box.prop(self, "normalize_mixed_lods")
        box.separator()
        box.label(text=f"LoD Export Policy: {LOD_EXPORT_POLICY_NAME}", icon='INFO')

        box = layout.box()
        box.label(text="Feature Types")
        box.prop(self, "export_buildings")
        box.prop(self, "export_bridges")
        box.prop(self, "export_tunnels")
        box.prop(self, "export_vegetation")
        box.prop(self, "export_waterbodies")
        box.prop(self, "export_construction")
        box.prop(self, "export_cityfurniture")
        box.prop(self, "export_landuse")
        box.prop(self, "export_transportation")
        box.prop(self, "export_relief")
        box.prop(self, "export_generics")

    def invoke(self, ctx, evt):
        # Set default filename if not already set
        if not self.filepath:
            self.filepath = "export.gml"

        # Automatically detect CityGML version from the top-level collection
        detected_version = _detect_citygml_version_from_scene(ctx.scene)
        if detected_version:
            self.citygml_version = detected_version
        # B56: the dialog shows the scope; the selection preselects SELECTION, scripts default to SCENE
        self.scope = 'SELECTION' if ctx.selected_objects else 'SCENE'

        # Automatically prefill srsName from World["cgml_CRS"], if available
        if not self.srs_name:
            try:
                w = gr.world()
                if w and gr.CRS in w:
                    val = str(w[gr.CRS]).strip()
                    if val:
                        self.srs_name = val
            except Exception:
                pass

        ctx.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def _log_text(self, name_prefix, content):
        from .shared.export_helpers import log_text_to_blender
        log_text_to_blender(name_prefix, content)

    def execute(self, ctx):
        from .shared.lod_helpers import normalize_scene_top_level_lods, LOD_EXPORT_POLICY_NAME

        props = ctx.scene.cgml3

        # Ensure the file extension is .gml
        filepath = self.filepath
        if not filepath.lower().endswith('.gml'):
            filepath = filepath.rstrip('.') + '.gml'

        # IMPORTANT: Convert Blender-relative paths (//) to absolute paths
        filepath = bpy.path.abspath(filepath)

        # Ensure the output directory exists
        out_dir = os.path.dirname(filepath)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # Ticket 12 (docs/agent/semantic-hierarchy): cross-version export is a
        # deliberate, supported gesture (a v2-imported scene can target v3 on
        # export and vice versa) - so a detected/selected mismatch here is not
        # an error, but it must be visible in the log rather than silently
        # resolved one way or the other.
        _detected_version_now = _detect_citygml_version_from_scene(ctx.scene)
        if _detected_version_now is None:
            _version_source = "user-selected (no CityGML2/CityGML3 collection naming convention found in scene)"
        elif _detected_version_now == self.citygml_version:
            _version_source = "auto-detected"
        else:
            _version_source = f"user-selected (overrides scene, detected as CityGML {_detected_version_now})"

        print(f"[CityGML] Export target: {filepath}")
        print(f"[CityGML] citygml_version={self.citygml_version} ({_version_source})")
        # Ticket 14 (docs/agent/semantic-hierarchy): name the active LoD
        # export policy in the log on every export. No filtering exists
        # today, so this is always the same value - see shared/lod_helpers.py.
        print(f"[CityGML] lod_export_policy={LOD_EXPORT_POLICY_NAME}")
        if _detected_version_now and _detected_version_now != self.citygml_version:
            self.report(
                {'INFO'},
                f"Cross-version export: writing CityGML {self.citygml_version}; "
                f"scene detected as CityGML {_detected_version_now}"
            )

        if self.normalize_mixed_lods:
            normalization = normalize_scene_top_level_lods(ctx.scene)
            if normalization["normalized"]:
                count = len(normalization["normalized"])
                self.report({'INFO'}, f"Normalized mixed LoDs for {count} top-level object(s) before export")

        try:
            if getattr(ctx.scene, "cgml3", None) and ctx.scene.cgml3.export_debug:
                sample = bpy.context.object
                if sample:
                    d = None
                    try:
                        d = sample.get("cgml3_generic_attributes", None)
                    except Exception:
                        d = None
                    print(
                        "[CityGML3][DIAG] export sample obj="
                        f"{getattr(sample, 'name', '?')} gml_id={sample.get('gml_id')} "
                        f"generic_dict_len={(len(d) if isinstance(d, dict) else 0)} "
                        f"has_json={bool(sample.get('cgml3_generic_attributes_json') or sample.get('cgml3_generic_attributes_json_0'))}"
                    )
                try:
                    from .io.writer.document import create_citymodel_root
                    r = create_citymodel_root("0000")
                    print(f"[CityGML3][DIAG] writer root tag={r.tag}")
                except Exception as e:
                    print(f"[CityGML3][DIAG] writer root tag check failed: {e}")
        except Exception:
            pass

        # CAC-P3.3: record <-> legacy consistency pre-flight. No-op with the
        # experimental authoring core off (flag-off export unchanged).
        from .shared.authoring_core.blender.consistency_view import export_preflight

        if export_preflight(report=self.report):
            self.report({'ERROR'}, "CityGML Export cancelled: see the consistency errors above")
            return {'CANCELLED'}

        try:
            rec = _write_scene_gml(self, ctx, filepath)
        except ValueError as e:  # scope (B56): nothing written
            self.report({'ERROR'}, f"CityGML Export cancelled: {e}")
            return {'CANCELLED'}
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"CityGML Export FEHLGESCHLAGEN: {e}")
            return {'CANCELLED'}

        # After export: file must exist
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, f"Export completed, but file was NOT created: {filepath} (see system console)")
            return {'CANCELLED'}

        if os.path.getsize(filepath) == 0:
            self.report({'ERROR'}, f"Export file is empty (0 bytes): {filepath} (see system console)")
            return {'CANCELLED'}

        # Optional: XSD validation after export (a failure is a warning: the file is written)
        if props.xsd_validate:
            try:
                problem, _warning, xsd_log = _validate_citygml(filepath, True, False)
            except Exception as e:
                problem, xsd_log = f"Validation failed: {e}", None
            print(f"[CityGML {self.citygml_version}] Validation: {problem or xsd_log}")
            if xsd_log:
                self._log_text("citygml_xsd_check", xsd_log)
            if problem:
                self.report({'WARNING'}, f"Validation FAILED - {problem}")
            else:
                # B57 (A2): the XSD says nothing about planarity, closed shells or self-intersections
                self.report({'INFO'}, f"XSD valid (CityGML {self.citygml_version}); geometry not checked")
            rec["xsd"] = "invalid" if problem else "valid"
            ctx.scene["cgml_last_export"] = json.dumps(rec)

        from .shared.export_scope import summary
        self.report({'WARNING'} if rec["skipped"] or rec.get("not_citygml") else {'INFO'}, summary(rec))
        self.report({'INFO'}, f"CityGML Export finished: {filepath}")
        return {'FINISHED'}

# ---------------- Operator: DB-Import (Export->Datei->lesen) ----------------
class CGML3_OT_ImportFromDB(Operator):
    bl_idname = "cgml3.import_from_db"
    bl_label = "Import from 3DCityDB"
    bl_options = {'REGISTER', 'UNDO'}

    # Connection (defaults: the scene's 3DCityDB target; no password property)
    db_host: StringProperty(name="Host", default="")
    db_port: StringProperty(name="Port", default="")
    db_name: StringProperty(name="Database", default="")
    db_user: StringProperty(name="User", default="")
    db_schema: StringProperty(name="Schema", default="")
    db_sslmode: EnumProperty(name="SSL", items=citydb_connection.SSL_ITEMS, default="prefer")

    # CRS/Transform
    crs: StringProperty(
        name="Output CRS", default="",
        description="Reproject the features to this CRS, e.g. EPSG:25832. Empty: the CRS of the city model "
                    "(citydb-tool --crs)")
    crs_name: StringProperty(
        name="CRS name", default="",
        description="srsName written into the GML, e.g. a URN. Empty: derived from the CRS (citydb-tool --crs-name)")
    transform: StringProperty(
        name="Coordinate transform", default="",
        description="Affine transformation: swap-xy or 12 comma-separated matrix values (citydb-tool --transform)")

    # Filter
    type_names: StringProperty(
        name="Feature types", default="",
        description="Comma-separated feature types to load, e.g. bldg:Building,brid:Bridge. Empty: the feature "
                    "types of the Filter Options panel when enabled, else all (citydb-tool --type-name)")
    lod: StringProperty(
        name="LoD", default="",
        description="Comma-separated LoDs to load, e.g. 1,2. Empty: the LoD filter of the Filter Options panel "
                    "when enabled, else all (citydb-tool --lod)")
    lod_mode: EnumProperty(
        name="LoD match",
        description="How the listed LoDs select the geometry (citydb-tool --lod-mode)",
        items=[('NONE', 'Default', 'citydb-tool default'),
               ('or', 'Any listed LoD', 'Features that have at least one of the listed LoDs'),
               ('and', 'All listed LoDs', 'Features that have all of the listed LoDs'),
               ('minimum', 'Lowest listed LoD', 'Only the lowest of the listed LoDs a feature has'),
               ('maximum', 'Highest listed LoD', 'Only the highest of the listed LoDs a feature has')],
        default='NONE'
    )
    lod_search_depth: StringProperty(
        name="LoD search depth", default="",
        description="How many levels of nested features are searched for the LoDs: a number or all "
                    "(citydb-tool --lod-search-depth)")
    cql2_filter: StringProperty(
        name="CQL2 filter", default="",
        description="CQL2 text expression, e.g. bldg:storeysAboveGround > 3. Combined with the area by AND "
                    "(citydb-tool --filter)")
    filter_crs: StringProperty(
        name="Filter CRS", default="",
        description="CRS of the coordinates in the CQL2 filter and the area. Empty: the output CRS "
                    "(citydb-tool --filter-crs)")
    sql_filter: StringProperty(
        name="SQL filter", default="",
        description="SQL query used as filter (citydb-tool --sql-filter)")
    sort_by: StringProperty(
        name="Sort by", default="",
        description="Comma-separated properties, each optionally followed by +asc or +desc (citydb-tool --sort-by)")
    limit: IntProperty(
        name="Max. features", default=0, min=0,
        description=f"Load at most this many features. 0: up to the Load-all limit of "
                    f"{LOAD_ALL_MAX_FEATURES:,}; more are not imported (citydb-tool --limit)")
    start_index: IntProperty(
        name="Skip first", default=0, min=0,
        description="Skip this many features before loading. 0: start at the first (citydb-tool --start-index)")

    # Temporal validity
    validity: EnumProperty(
        name="Validity",
        description="Which feature versions to load (citydb-tool --validity)",
        items=[('NONE', 'Default', 'citydb-tool default'),
               ('latest', 'Latest version', 'The latest valid version'),
               ('at', 'Valid at a time', 'Versions valid at the time below'),
               ('between', 'Valid in a period', 'Versions valid in the period below'),
               ('terminated', 'Terminated', 'Terminated versions'),
               ('terminated_at', 'Terminated at a time', 'Versions terminated at the time below'),
               ('all', 'All versions', 'Every version')],
        default='NONE'
    )
    validity_at: StringProperty(
        name="Valid at", default="",
        description="YYYY-MM-DD or an ISO date and time (citydb-tool --validity-at)")
    validity_between: StringProperty(
        name="Valid between", default="",
        description="Start and end as ISO date/time, comma-separated (citydb-tool --validity-between)")
    validity_reference: EnumProperty(
        name="Time reference",
        description="Which time the validity refers to (citydb-tool --validity-reference)",
        items=[('NONE', 'Default', 'citydb-tool default'),
               ('transaction', 'Database time', 'When the version was written to the database'),
               ('validity', 'Real-world time', 'When the version was valid in reality')],
        default='NONE'
    )
    lenient_validity: BoolProperty(
        name="Lenient validity", default=False,
        description="Tolerate incomplete validity information (citydb-tool --lenient-validity)")

    # Appearance
    no_appearances: BoolProperty(
        name="Skip appearances", default=False,
        description="Load no textures and colours (citydb-tool --no-appearances)")
    appearance_themes: StringProperty(
        name="Appearance themes", default="",
        description="Comma-separated appearance themes to load. Empty: all (citydb-tool --appearance-theme)")

    # Output formatting
    no_pretty_print: BoolProperty(
        name="Compact GML", default=False,
        description="Write the intermediate GML without indentation (citydb-tool --no-pretty-print)")
    xsl_transform: StringProperty(
        name="XSL transformation", subtype='FILE_PATH', default="",
        description="XSLT stylesheet applied to the exported GML (citydb-tool --xsl-transform)")

    # Config
    export_config: StringProperty(
        name="Config file (JSON)", subtype='FILE_PATH', default="",
        description="citydb-tool JSON configuration (citydb-tool --config-file)")
    # B83 scale decision: from the UI never the whole city model; scripts (EXEC) keep full control
    require_area: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=False)

    def _missing_area(self, ctx):
        return self.require_area and not (ctx.scene.cgml3.use_bbox_filter or self.cql2_filter.strip()
                                          or self.sql_filter.strip())

    def invoke(self, ctx, evt):
        _db_target_from_scene(self, ctx)
        self.require_area = True
        ctx.window_manager.invoke_props_dialog(self, width=600)
        return {'RUNNING_MODAL'}

    def draw(self, ctx):
        ui_state = ctx.scene.cgml3
        col = self.layout.column(align=True)

        _draw_db_target_section(col, self, ui_state, "db_import_ui_show_connection")

        box = col.box()
        box.label(text="Basic", icon='PREFERENCES')
        box.prop(self, "type_names")
        row = box.row(align=True)
        row.prop(self, "lod"); row.prop(self, "lod_mode", text="")
        _draw_db_area_section(box, ctx)
        if self._missing_area(ctx):
            sub = box.column(align=True)
            sub.alert = True
            sub.label(text="Pick an area (Current view): the whole city model is not loaded", icon='ERROR')

        expert = _draw_foldout_section(col, ui_state, "db_import_ui_show_expert", "Expert")
        if not expert:
            return

        box = _draw_foldout_section(expert, ui_state, "db_import_ui_show_crs", "Coordinates")
        if box:
            box.prop(self, "crs"); box.prop(self, "crs_name"); box.prop(self, "transform")

        box = _draw_foldout_section(expert, ui_state, "db_import_ui_show_filters", "Filter and paging")
        if box:
            box.prop(self, "lod_search_depth")
            box.prop(self, "cql2_filter"); box.prop(self, "sql_filter"); box.prop(self, "filter_crs")
            box.prop(self, "sort_by"); box.prop(self, "limit"); box.prop(self, "start_index")

        box = _draw_foldout_section(expert, ui_state, "db_import_ui_show_validity", "Versions (temporal validity)")
        if box:
            box.prop(self, "validity"); box.prop(self, "validity_at"); box.prop(self, "validity_between")
            box.prop(self, "validity_reference"); box.prop(self, "lenient_validity")

        box = _draw_foldout_section(expert, ui_state, "db_import_ui_show_format", "Appearance and output")
        if box:
            box.prop(self, "no_appearances"); box.prop(self, "appearance_themes")
            box.prop(self, "no_pretty_print"); box.prop(self, "xsl_transform")

        box = _draw_foldout_section(expert, ui_state, "db_import_ui_show_config", "Configuration file")
        if box:
            box.prop(self, "export_config")

    def _log_text(self, name_prefix, content):
        from .shared.export_helpers import log_text_to_blender
        log_text_to_blender(name_prefix, content)

    def execute(self, ctx):
        import shutil, tempfile, time
        from .ops.citydb_cli import CityDBTool
        from .shared.export_helpers import log_text_to_blender

        if self._missing_area(ctx):
            self.report({'ERROR'}, "Pick an area first (Area: Current view) or use 3DCityDB Browse; "
                                   "the whole city model is not loaded")
            return {'CANCELLED'}
        _db_target_from_scene(self, ctx)
        resolved = _resolve_db_target(self, ctx)
        if resolved is None:
            return {'CANCELLED'}
        db_password, db_sslmode = resolved
        if browse_citydb.import_running():
            self.report({'ERROR'}, "A 3DCityDB Browse load is running; wait for it or press Esc")
            return {'CANCELLED'}

        type_names = [s.strip() for s in self.type_names.split(",") if s.strip()] or None
        lod = [s.strip() for s in self.lod.split(",") if s.strip()] or None
        sort_by = [s.strip() for s in self.sort_by.split(",") if s.strip()] or None
        appearance_themes = [s.strip() for s in self.appearance_themes.split(",") if s.strip()] or None
        schema = self.db_schema.strip() or None
        lod_mode_val = None if self.lod_mode == 'NONE' else self.lod_mode
        validity_val = None if self.validity == 'NONE' else self.validity
        validity_ref_val = None if self.validity_reference == 'NONE' else self.validity_reference

        # ── Use scene defaults if fields are empty ──
        # Feature type filter from Scene Panel → type_names (if empty in Operator)
        if not type_names:
            type_names = _scene_db_type_names(ctx.scene)
            if type_names:
                print(f"[CityGML DB-Import] Feature type filter (Scene): {len(type_names)} types")

        # LOD filter from Scene Panel → lod (if empty in Operator)
        if not lod and ctx.scene.cgml3.use_lod_filter:
            _scene_lods = []
            for lvl in range(5):  # LOD 0–4
                if getattr(ctx.scene.cgml3, f"lod_{lvl}", True):
                    _scene_lods.append(str(lvl))
            if _scene_lods:
                lod = _scene_lods
                print(f"[CityGML DB-Import] LOD filter (Scene): {','.join(lod)}")

        # GML-ID filter from Scene Properties -> database-side export filters.
        gml_ids = []
        if ctx.scene.cgml3.use_gmlid_filter:
            gml_ids_str = ctx.scene.cgml3.gmlid_filter.strip()
            if gml_ids_str:
                gml_ids = [gid.strip() for gid in gml_ids_str.replace(";", ",").split(",") if gid.strip()]
                if gml_ids:
                    print(f"[CityGML DB-Import] GML-ID filter active: {len(gml_ids)} IDs")

        effective_sql_filter = self.sql_filter.strip() if self.sql_filter else ""

        # Read BBox filter from Scene Properties and convert to CQL2 filter
        # Note: --bbox is only available for import, for export we need to use CQL2
        # Syntax: s_intersects(core:envelope, bbox(xmin,ymin,xmax,ymax))
        bbox_cql2 = None
        bbox_filter_crs = None
        if ctx.scene.cgml3.use_bbox_filter:
            min_x = ctx.scene.cgml3.bbox_min_x
            min_y = ctx.scene.cgml3.bbox_min_y
            max_x = ctx.scene.cgml3.bbox_max_x
            max_y = ctx.scene.cgml3.bbox_max_y
            # Use citydb CQL2 syntax: s_intersects(core:envelope, bbox(...))
            bbox_cql2 = f"s_intersects(core:envelope, bbox({min_x},{min_y},{max_x},{max_y}))"
            # If filter_crs is set, use it, otherwise use the CRS from the field
            bbox_filter_crs = self.filter_crs or self.crs or None
            print(f"[CityGML DB-Import] BBox filter active: {bbox_cql2}")

        # Combine BBox filter with existing CQL2 filter if present
        combined_cql2 = None
        combined_filter_crs = self.filter_crs or bbox_filter_crs
        if bbox_cql2 and self.cql2_filter:
            combined_cql2 = f"({self.cql2_filter}) AND ({bbox_cql2})"
        elif bbox_cql2:
            combined_cql2 = bbox_cql2
        elif self.cql2_filter:
            combined_cql2 = self.cql2_filter

        tmp = tempfile.mkdtemp(prefix="cgml3_")
        out_gml = os.path.join(tmp, "export.gml")
        export_kw = dict(
            output=out_gml,
            host=self.db_host, port=self.db_port, db=self.db_name,
            user=self.db_user, password=db_password, sslmode=db_sslmode,
            db_schema=schema,
            citygml_version="3.0",
            config_file=(self.export_config or None),
            crs=(self.crs or None),
            crs_name=(self.crs_name or None),
            transform=(self.transform or None),
            cql2_filter=combined_cql2,
            filter_crs=combined_filter_crs,
            sql_filter=(effective_sql_filter or None),
            feature_ids=(gml_ids or None),
            type_names=type_names,
            lod=lod,
            lod_mode=lod_mode_val,
            lod_search_depth=(self.lod_search_depth or None),
            sort_by=sort_by,
            # one more than the Load-all limit: the importer refuses that export instead of the DB sending all of it
            limit=min(self.limit or LOAD_ALL_MAX_FEATURES + 1, LOAD_ALL_MAX_FEATURES + 1),
            start_index=(self.start_index or None) if self.start_index > 0 else None,
            # temporal validity
            validity=validity_val,
            validity_reference=validity_ref_val,
            validity_at=(self.validity_at or None),
            validity_between=(self.validity_between or None),
            lenient_validity=self.lenient_validity,
            # appearance
            no_appearances=self.no_appearances,
            appearance_themes=appearance_themes,
            # formatting
            no_pretty_print=self.no_pretty_print,
            xsl_transform=(self.xsl_transform or None)
        )
        tool = CityDBTool(_get_citydb_prefs(ctx))
        xsd, lenient = ctx.scene.cgml3.xsd_validate, ctx.scene.cgml3.xsd_lenient_validation
        report = _job_report(self)
        outcome = {}
        t0 = time.time()

        # citydb-tool and the XML checks in a thread; only the scene build runs on the main thread
        def worker(job):
            job.set_progress(0, 0, "citydb-tool: export")
            ok, log = tool.export_db_to_citygml(
                **export_kw, cancelled=job.cancelled,
                on_output=lambda line: job.set_progress(0, 0, f"citydb-tool: {line[-60:]}"))
            if job.cancelled():
                raise background_job.JobCancelled()
            if not ok:
                return ok, log, None, None, None
            job.set_progress(0, 0, "XSD validation" if xsd else "XML check")
            return (ok, log) + _validate_citygml(out_gml, xsd, lenient)

        def build(result, error, cancelled) -> bool:
            from .common.import_landing import ImportSnapshot, finish_import, report_import_error

            if cancelled:
                report({'WARNING'}, "DB import cancelled, nothing imported")
                return False
            if error is not None:
                report({'ERROR'}, f"DB import failed: {error}")
                return False
            ok, log, problem, warning, xsd_log = result
            log_text_to_blender("citydb_export", log)
            if not ok:
                report({'ERROR'}, _citydb_tool_unsupported_message(log)
                       or "citydb export citygml failed (see text log)")
                return False
            print(f"[CityGML DB-Import] DB export completed ({time.time() - t0:.1f}s, 3DCityDB v5)")
            if xsd_log:
                log_text_to_blender("citygml_xsd_check", xsd_log)
            if problem:
                report({'ERROR'}, problem)
                return False
            if warning:
                print(f"[CityGML Export] XSD warning (tolerated): {warning}")
                report({'WARNING'}, f"XSD warning (3DCityDB): DB export will continue despite: {warning[:100]}...")

            context = bpy.context
            snapshot = ImportSnapshot()
            try:
                warnings = import_citygml_auto(out_gml, context)
            except ImportTooLarge:
                report({'ERROR'}, f"The export is too large to import at once (at most {LOAD_ALL_MAX_FEATURES:,} "
                                  f"features, {LOAD_ALL_MAX_MB:,.0f} MB) and nothing was imported: add a filter, or use 'Show Buildings "
                                  "in View' (3DCityDB Browse) and load the buildings you need")
                return False
            except Exception as exc:
                report_import_error(SimpleNamespace(report=report), exc, "citydb_import_error")
                return False
            print(f"[CityGML DB-Import] DB import completed ({time.time() - t0:.1f}s)")
            level, message, _summary = finish_import(context, snapshot, frame=_frame_view_after_import(context),
                                                     warnings=warnings)
            report({level}, f"DB: {message}")
            return True

        def on_done(result, error, cancelled):
            try:
                outcome["ok"] = build(result, error, cancelled)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        print("[CityGML DB-Import] Start DB export (3DCityDB v5)...")
        job = background_job.Job("cgml3_db_import", worker, on_done=on_done, label="Import from 3DCityDB")
        if background_job.start(job, ctx) is None:
            shutil.rmtree(tmp, ignore_errors=True)
            self.report({'ERROR'}, "Another background job is running; wait for it or press Esc")
            return {'CANCELLED'}
        # background mode ran the job already; in the UI it reports when it ends
        if bpy.app.background and not outcome.get("ok"):
            return {'CANCELLED'}
        return {'FINISHED'}

# ---------------- Operator: DB-Export (File->Import in DB) ----------------
class CGML3_OT_ExportToDB(Operator):
    bl_idname = "cgml3.export_to_db"
    bl_label = "Export to 3DCityDB"
    bl_options = {'REGISTER'}

    autofill_new_surfaces: BoolProperty(
        name="Automatically process new surfaces",
        description="Only for newly modeled faces without CityGML metadata: detects interior faces, adopts appearance from the corresponding exterior, and assigns surface type for new roof/wall faces without modifying existing imported interior structures.",
        default=False,
    )

    # Connection (defaults: the scene's 3DCityDB target; no password property)
    db_host: StringProperty(name="Host", default="")
    db_port: StringProperty(name="Port", default="")
    db_name: StringProperty(name="Database", default="")
    db_user: StringProperty(name="User", default="")
    db_schema: StringProperty(name="Schema", default="")
    db_sslmode: EnumProperty(name="SSL", items=citydb_connection.SSL_ITEMS, default="prefer")

    # File export (before import into DB)
    citygml_version: EnumProperty(
        name="CityGML Version",
        description="Select the CityGML version for export",
        items=[
            ('3.0', 'CityGML 3.0', 'Export as CityGML 3.0'),
            ('2.0', 'CityGML 2.0', 'Export as CityGML 2.0'),
        ],
        default='3.0'
    )
    
    srs_name: StringProperty(
        name="CRS",
        description="CRS written into the uploaded file, e.g. EPSG:25832. Empty: World['cgml_CRS']",
        default=""
    )
    feature_type_prop: StringProperty(
        name="Feature type property", default="ModelType",
        description="Object property that holds the feature type")
    write_offset_back: BoolProperty(
        name="Rewrite the origin offset",
        description="When exporting, add the saved CityModel offset back into the geometry coordinates (only for export, does not modify the scene's offset permanently).",
        default=True
    )
    use_inner_outer_script: BoolProperty(
        name="Automatically detect interior surfaces",
        description="Uses get_inner_and_outer_rings.py to automatically detect interior surfaces.",
        default=False,
    )

    split_wall_roof_surfaces: BoolProperty(
        name="Export roof/wall surfaces individually",
        description="Only for RoofSurface and WallSurface: instead of bundling as MultiSurface/MultiPolygon, write each polygon as a separate surface.",
        default=False,
    )

    write_lod_solid_refs: BoolProperty(
        name="Write LoD Solid from surface references",
        description="Writes a lod{n}Solid referencing the boundary polygons via xlink:href (no installation geometry) when the shell is closed or the source file carried that Solid (B48).",
        default=True,
    )

    # B56 (agent plan A1): what is written; a forgotten selection no longer exports one building silently
    scope: EnumProperty(
        name="Scope",
        description="Which features are written",
        items=[
            ('SCENE', 'Whole scene', 'Every visible feature of the scene, whatever is selected'),
            ('SELECTION', 'Selection', 'The selected features with their parts, installations and openings'),
            ('IDS', 'GML ids', 'The top-level features listed in GML ids'),
        ],
        default='SCENE',
    )
    scope_gml_ids: StringProperty(
        name="GML ids", default="",
        description="Comma-separated GML ids of top-level features (scope GML ids)")

    normalize_mixed_lods: BoolProperty(
        name="Normalize Mixed LoDs",
        description="Before export, detect top-level objects with mixed LoDs and raise all contained surfaces and parts to the highest LoD used in that top-level object.",
        default=False,
    )

    # Export Feature-Type Filter (match file-export UI)
    export_buildings: BoolProperty(name="Buildings", default=True, description="Upload buildings and their parts")
    export_bridges: BoolProperty(name="Bridges", default=True, description="Upload bridges and their parts")
    export_tunnels: BoolProperty(name="Tunnels", default=True, description="Upload tunnels and their parts")
    export_vegetation: BoolProperty(name="Vegetation", default=True, description="Upload vegetation objects")
    export_waterbodies: BoolProperty(name="Water bodies", default=True, description="Upload water bodies")
    export_construction: BoolProperty(name="Constructions", default=True,
                                      description="Upload other constructions, windows and doors (CityGML 3.0)")
    export_cityfurniture: BoolProperty(name="City furniture", default=True, description="Upload city furniture")
    export_landuse: BoolProperty(name="Land use", default=True, description="Upload land use areas")
    export_transportation: BoolProperty(name="Transportation", default=True,
                                        description="Upload roads, railways, tracks and squares")
    export_relief: BoolProperty(name="Relief", default=True, description="Upload terrain (relief) features")
    export_generics: BoolProperty(name="Generics and groups", default=True,
                                  description="Upload generic city objects and city object groups")

    # Config
    import_config: StringProperty(
        name="Config file (JSON)", subtype='FILE_PATH', default="",
        description="citydb-tool JSON configuration (citydb-tool --config-file)")

    # ── DB-Import Filter (citydb import citygml) ──
    import_mode: EnumProperty(
        name="Existing features",
        description="What citydb-tool does with features already in the database (citydb-tool --import-mode)",
        items=[
            ('import_all', 'Import all', 'Import every feature, existing ones stay (default)'),
            ('skip', 'Skip existing', 'Skip features that already exist'),
            ('delete', 'Delete and replace', 'Delete existing features and import the new ones'),
            ('terminate', 'Terminate and replace', 'Terminate existing features and import the new ones'),
        ],
        default='import_all'
    )
    pre_delete_existing_features: BoolProperty(
        name="Remove old versions first",
        description="Before the upload, delete or terminate the database features with the GML ids of the scene "
                    "(separate citydb-tool delete run)",
           default=True,
    )
    pre_delete_mode: EnumProperty(
        name="Removal",
        description="How the old versions are removed (citydb-tool delete --delete-mode)",
        items=[
            ('delete', 'Delete', 'Delete the old versions'),
            ('terminate', 'Terminate', 'Terminate the old versions (history kept)'),
        ],
        default='delete'
    )
    import_threads: IntProperty(name="Threads", default=0, min=0,
                                description="Number of import threads. 0: automatic (citydb-tool --threads)")
    import_compute_extent: BoolProperty(
        name="Compute envelopes", default=False,
        description="Compute the feature envelopes from the geometry instead of the file (citydb-tool --compute-extent)")
    import_transform: StringProperty(
        name="Coordinate transform", default="",
        description="Affine transformation: swap-xy or 12 comma-separated matrix values (citydb-tool --transform)")

    # Filter for DB import
    import_type_names: StringProperty(
        name="Feature types",
        description="Comma-separated feature types to write, e.g. bldg:Building,brid:Bridge. Empty: the feature "
                    "types of the Filter Options panel when enabled, else all (citydb-tool --type-name)",
        default=""
    )
    import_feature_ids: StringProperty(
        name="GML ids",
        description="Comma-separated GML ids to write, e.g. ID_001,ID_002. Empty: the GML id filter of the "
                    "Filter Options panel when enabled (citydb-tool --id)",
        default=""
    )
    import_bbox: StringProperty(
        name="Area override",
        description="xmin,ymin,xmax,ymax[,srid]. Replaces the area of the Basic section (citydb-tool --bbox)",
        default=""
    )
    import_bbox_mode: EnumProperty(
        name="Area match",
        description="Which features count as inside the area (citydb-tool --bbox-mode)",
        items=[
            ('NONE', 'Default (intersects)', 'citydb-tool default'),
            ('intersects', 'Intersects', 'Features that touch the area'),
            ('contains', 'Inside', 'Features completely inside the area'),
            ('on_tile', 'On tile', 'Features whose centre lies in the area'),
        ],
        default='NONE'
    )
    import_limit: IntProperty(name="Max. features", default=0, min=0,
                              description="Write at most this many features. 0: no limit (citydb-tool --limit)")
    import_start_index: IntProperty(
        name="Skip first", default=0, min=0,
        description="Skip this many features of the file. 0: start at the first (citydb-tool --start-index)")

    # Appearance
    import_no_appearances: BoolProperty(
        name="Skip appearances", default=False,
        description="Write no textures and colours (citydb-tool --no-appearances)")
    import_appearance_themes: StringProperty(
        name="Appearance themes", default="",
        description="Comma-separated appearance themes to write. Empty: all (citydb-tool --appearance-theme)")

    # CityGML-specific
    import_xsl_transform: StringProperty(
        name="XSL transformation", subtype='FILE_PATH', default="",
        description="XSLT stylesheet applied to the file before the import (citydb-tool --xsl-transform)")
    import_xal_source: BoolProperty(
        name="Keep xAL address source", default=False,
        description="Store the xAL source of addresses (citydb-tool --import-xal-source)")

    # Upgrade options (CityGML 2.0/1.0 → 3.0)
    use_lod4_as_lod3: BoolProperty(
        name="Use LoD4 as LoD3", default=False,
        description="Store CityGML 2.0/1.0 LoD4 geometry as LoD3 (citydb-tool --use-lod4-as-lod3)")
    map_lod0_roof_edge: BoolProperty(
        name="Map LoD0 roof edge", default=False,
        description="Map CityGML 2.0/1.0 LoD0 roof edges onto roof surfaces (citydb-tool --map-lod0-roof-edge)")
    map_lod1_surface: BoolProperty(
        name="Map LoD1 surface", default=False,
        description="Map CityGML 2.0/1.0 LoD1 multi-surfaces onto generic thematic surfaces "
                    "(citydb-tool --map-lod1-surface)")

    def invoke(self, ctx, evt):
        _db_target_from_scene(self, ctx)

        # Automatically detect CityGML version from the top-level collection
        detected_version = _detect_citygml_version_from_scene(ctx.scene)
        if detected_version:
            self.citygml_version = detected_version
        # B56: the dialog shows the scope; the selection preselects SELECTION, scripts default to SCENE
        self.scope = 'SELECTION' if ctx.selected_objects else 'SCENE'

        # Automatically prefill srsName from World["cgml_CRS"], if available
        if not self.srs_name:
            try:
                w = gr.world()
                if w and gr.CRS in w:
                    val = str(w[gr.CRS]).strip()
                    if val:
                        self.srs_name = val
            except Exception:
                pass

        ctx.window_manager.invoke_props_dialog(self, width=500)
        return {'RUNNING_MODAL'}

    def draw(self, ctx):
        from .shared.lod_helpers import LOD_EXPORT_POLICY_NAME

        ui_state = ctx.scene.cgml3
        col = self.layout.column(align=True)

        _draw_db_target_section(col, self, ui_state, "db_export_ui_show_connection")

        box = col.box()
        box.label(text="Basic", icon='PREFERENCES')
        row = box.row(align=True)
        row.prop(self, "citygml_version", text="")
        row.prop(self, "srs_name")
        box.prop(self, "scope")
        if self.scope == 'IDS':
            box.prop(self, "scope_gml_ids")
        box.label(text="Feature types:")
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for name in ("export_buildings", "export_bridges", "export_tunnels", "export_vegetation",
                     "export_waterbodies", "export_construction", "export_transportation",
                     "export_cityfurniture", "export_landuse", "export_relief", "export_generics"):
            grid.prop(self, name)
        box.label(text=f"LoD: {LOD_EXPORT_POLICY_NAME}", icon='INFO')
        box.prop(self, "normalize_mixed_lods")
        _draw_db_area_section(box, ctx)
        box.prop(self, "pre_delete_existing_features")
        row = box.row()
        row.enabled = bool(self.pre_delete_existing_features)
        row.prop(self, "pre_delete_mode")
        box.prop(self, "import_mode")

        expert = _draw_foldout_section(col, ui_state, "db_export_ui_show_expert", "Expert")
        if not expert:
            return

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_export", "File export (geometry)")
        if box:
            box.prop(self, "feature_type_prop")
            box.prop(self, "write_offset_back")
            box.prop(self, "use_inner_outer_script")
            box.prop(self, "autofill_new_surfaces")
            box.prop(self, "split_wall_roof_surfaces")
            box.prop(self, "write_lod_solid_refs")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_import_options", "Database import")
        if box:
            box.prop(self, "import_threads")
            box.prop(self, "import_compute_extent")
            box.prop(self, "import_transform")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_import_filters", "Filter and paging")
        if box:
            box.label(text="Empty fields use the Filter Options panel", icon='INFO')
            box.prop(self, "import_type_names")
            box.prop(self, "import_feature_ids")
            box.prop(self, "import_bbox")
            box.prop(self, "import_bbox_mode")
            box.prop(self, "import_limit")
            box.prop(self, "import_start_index")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_import_appearance", "Appearance")
        if box:
            box.prop(self, "import_no_appearances")
            box.prop(self, "import_appearance_themes")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_import_citygml", "CityGML")
        if box:
            box.prop(self, "import_xsl_transform")
            box.prop(self, "import_xal_source")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_config", "Configuration file")
        if box:
            box.prop(self, "import_config")

        box = _draw_foldout_section(expert, ui_state, "db_export_ui_show_upgrade", "Upgrade from CityGML 2.0/1.0")
        if box:
            box.prop(self, "use_lod4_as_lod3")
            box.prop(self, "map_lod0_roof_edge")
            box.prop(self, "map_lod1_surface")

    def _log_text(self, name_prefix, content):
        from .shared.export_helpers import log_text_to_blender
        log_text_to_blender(name_prefix, content)

    def execute(self, ctx):
        import shutil, tempfile
        from .ops.citydb_cli import CityDBTool
        from .shared.export_helpers import log_text_to_blender
        from .shared.lod_helpers import normalize_scene_top_level_lods, LOD_EXPORT_POLICY_NAME

        # Check the target and the options before spending time on the GML export.
        _db_target_from_scene(self, ctx)
        resolved = _resolve_db_target(self, ctx)
        if resolved is None:
            return {'CANCELLED'}
        db_password, db_sslmode = resolved

        if self.pre_delete_existing_features and self.import_mode in {'delete', 'terminate'}:
            msg = (
                "Pre-delete is already active. Please set the DB import mode to 'import_all' or 'skip', "
                "so that existing features are not deleted or terminated twice."
            )
            self._log_text("citydb_delete", msg)
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        if self.import_mode == 'delete' and not self.import_no_appearances:
            msg = (
                "DB-Import aborted: citydb-tool v5 does not import appearance data in 'delete' mode. "
                "Please delete or terminate existing features separately first "
                "and then export without delete mode."
            )
            self._log_text("citydb_import", msg)
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        # ── Prepare Scene Filters for DB Import ──

        # Feature Type Filter: Operator Field → Scene Panel Fallback
        _db_type_names = [s.strip() for s in self.import_type_names.split(",") if s.strip()] or None
        if not _db_type_names:
            _db_type_names = _scene_db_type_names(ctx.scene)
            if _db_type_names:
                print(f"[CityGML DB-Import] Feature Type Filter (Scene): {len(_db_type_names)} types")

        # GML-ID Filter: Operator Field → Scene Panel Fallback
        # citydb import citygml supports --id directly (no SQL needed)!
        _db_feature_ids = [s.strip() for s in self.import_feature_ids.split(",") if s.strip()] or None
        if not _db_feature_ids and ctx.scene.cgml3.use_gmlid_filter:
            gml_ids_str = ctx.scene.cgml3.gmlid_filter.strip()
            if gml_ids_str:
                _db_feature_ids = [gid.strip() for gid in gml_ids_str.replace(";", ",").split(",") if gid.strip()]
                if _db_feature_ids:
                    print(f"[CityGML DB-Import] GML-ID-Filter (Scene): {len(_db_feature_ids)} IDs")

        # BBox-Filter: Operator-Feld → Scene-Panel-Fallback
        # citydb import citygml support --bbox=xmin,ymin,xmax,ymax[,srid]
        _db_bbox = self.import_bbox.strip() or None
        if not _db_bbox and ctx.scene.cgml3.use_bbox_filter:
            min_x = ctx.scene.cgml3.bbox_min_x
            min_y = ctx.scene.cgml3.bbox_min_y
            max_x = ctx.scene.cgml3.bbox_max_x
            max_y = ctx.scene.cgml3.bbox_max_y
            _db_bbox = f"{min_x},{min_y},{max_x},{max_y}"
            print(f"[CityGML DB-Import] BBox-Filter (Scene): {_db_bbox}")

        # Appearance-Themes
        _db_appearance_themes = [s.strip() for s in self.import_appearance_themes.split(",") if s.strip()] or None

        _import_feature_ids = _db_feature_ids
        # B56: without explicit ids the pre-delete takes the ids written to the upload file (below),
        # not every feature of the scene: a scoped export must not delete what it does not re-import.
        _pre_delete_from_file = self.pre_delete_existing_features and not _db_feature_ids
        if self.pre_delete_existing_features and not self.import_feature_ids.strip():
            # The IDs are only needed for the delete; the import GML already contains the
            # correct feature set. Passing them through to the import command would exceed
            # the Windows command-line length limit (WinError 206) for large scenes.
            _import_feature_ids = None

        conn = dict(host=self.db_host, port=self.db_port, db=self.db_name, user=self.db_user,
                    password=db_password, sslmode=db_sslmode, db_schema=(self.db_schema.strip() or None))
        delete_kw = None
        if self.pre_delete_existing_features:
            delete_kw = dict(conn, delete_mode=self.pre_delete_mode, type_names=_db_type_names,
                             feature_ids=_db_feature_ids)
        import_kw = dict(
            conn,
            config_file=(self.import_config or None),
            # Import-Optionen
            import_mode=self.import_mode if self.import_mode != 'import_all' else None,
            threads=self.import_threads if self.import_threads > 0 else None,
            compute_extent=self.import_compute_extent,
            transform=(self.import_transform.strip() or None),
            # Filter
            type_names=_db_type_names,
            feature_ids=_import_feature_ids,
            bbox=_db_bbox,
            bbox_mode=None if self.import_bbox_mode == 'NONE' else self.import_bbox_mode,
            limit=self.import_limit if self.import_limit > 0 else None,
            start_index=self.import_start_index if self.import_start_index > 0 else None,
            # Appearance
            no_appearances=self.import_no_appearances,
            appearance_themes=_db_appearance_themes,
            # CityGML-spezifisch
            xsl_transform=(self.import_xsl_transform.strip() or None),
            import_xal_source=self.import_xal_source,
            # Upgrade
            use_lod4_as_lod3=self.use_lod4_as_lod3,
            map_lod0_roof_edge=self.map_lod0_roof_edge,
            map_lod1_surface=self.map_lod1_surface,
        )

        # Ticket 14 (docs/agent/semantic-hierarchy): name the active LoD
        # export policy in the log on every export, same as the file exporter.
        print(f"[CityGML] citygml_version={self.citygml_version}")
        print(f"[CityGML] lod_export_policy={LOD_EXPORT_POLICY_NAME}")

        if self.normalize_mixed_lods:
            normalization = normalize_scene_top_level_lods(ctx.scene)
            if normalization["normalized"]:
                count = len(normalization["normalized"])
                self.report({'INFO'}, f"Normalized mixed LoDs for {count} top-level object(s) before export")

        tmp = tempfile.mkdtemp(prefix="cgml3_")
        gml = os.path.join(tmp, "upload.gml")
        try:
            rec = _write_scene_gml(self, ctx, gml, target="db")  # bpy: main thread
        except ValueError as e:  # scope (B56): nothing written
            shutil.rmtree(tmp, ignore_errors=True)
            self.report({'ERROR'}, f"Export to DB cancelled: {e}")
            return {'CANCELLED'}
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        from .shared.export_scope import summary
        self.report({'WARNING'} if rec["skipped"] or rec.get("not_citygml") else {'INFO'}, summary(rec))
        if _pre_delete_from_file:
            if not rec["written"]:
                shutil.rmtree(tmp, ignore_errors=True)
                msg = ("Pre-delete by GML-ID could not derive any IDs: no feature with a gml:id was written. "
                       "Please check if the objects have valid gml_id properties.")
                self._log_text("citydb_delete", msg)
                self.report({'ERROR'}, msg)
                return {'CANCELLED'}
            delete_kw["feature_ids"] = list(rec["written"])
            print(f"[3DCityDB] Pre-Delete: {len(rec['written'])} IDs = the features written to the upload file")

        tool = CityDBTool(_get_citydb_prefs(ctx))
        xsd, lenient = ctx.scene.cgml3.xsd_validate, ctx.scene.cgml3.xsd_lenient_validation
        report = _job_report(self)
        outcome = {}

        # XML checks and citydb-tool in a thread; nothing here touches bpy
        def worker(job):
            def progress(line):
                job.set_progress(0, 0, f"citydb-tool: {line[-60:]}")

            result = {"logs": []}
            job.set_progress(0, 0, "XSD validation" if xsd else "XML check")
            result["problem"], result["warning"], result["xsd_log"] = _validate_citygml(gml, xsd, lenient)
            if result["problem"]:
                return result
            if job.cancelled():
                raise background_job.JobCancelled()
            if delete_kw is not None:
                print(f"[3DCityDB] Pre-Delete: mode={delete_kw['delete_mode']}, ids={len(delete_kw['feature_ids'] or [])}")
                job.set_progress(0, 0, "citydb-tool: delete")
                ok, log = tool.delete_features_from_db(**delete_kw, on_output=progress)
                result["logs"].append(("citydb_delete", log))
                if not ok:
                    result["failed"] = log
                    result["failed_text"] = "Pre-delete failed (see text log)."
                    return result
            # not cancelled half way once the delete ran: the old rows would be gone without the new ones
            job.set_progress(0, 0, "citydb-tool: import")
            ok, log = tool.import_citygml_to_db(gml, **import_kw, on_output=progress,
                                                cancelled=None if delete_kw is not None else job.cancelled)
            result["logs"].append(("citydb_import", log))
            if not ok:
                result["failed"] = log
                result["failed_text"] = "CityGML import to DB failed (see text log)."
            return result

        def finish(result, error, cancelled) -> bool:
            if cancelled:
                report({'WARNING'}, "Export to DB cancelled")
                return False
            if error is not None:
                report({'ERROR'}, f"Export to DB failed: {error}")
                return False
            if result["xsd_log"]:
                log_text_to_blender("citygml_xsd_check", result["xsd_log"])
            for prefix, log in result["logs"]:
                log_text_to_blender(prefix, log)
            if result["problem"]:
                report({'ERROR'}, result["problem"])
                return False
            if result["warning"]:
                print(f"[CityGML Export] XSD-Warnung (toleriert): {result['warning']}")
                report({'WARNING'}, f"XSD-Warnung (3DCityDB): {result['warning'][:100]}... Export wird fortgesetzt.")
            if "failed" in result:
                report({'ERROR'}, _citydb_tool_unsupported_message(result["failed"]) or result["failed_text"])
                return False
            report({'INFO'}, "Export to DB finished")
            return True

        def on_done(result, error, cancelled):
            try:
                outcome["ok"] = finish(result, error, cancelled)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

        job = background_job.Job("cgml3_db_export", worker, on_done=on_done, label="Export to 3DCityDB")
        if background_job.start(job, ctx) is None:
            shutil.rmtree(tmp, ignore_errors=True)
            self.report({'ERROR'}, "Another background job is running; wait for it or press Esc")
            return {'CANCELLED'}
        # background mode ran the job already; in the UI it reports when it ends
        if bpy.app.background and not outcome.get("ok"):
            return {'CANCELLED'}
        return {'FINISHED'}

# ---------------- File menu entries ----------------
def menu_func_import(self, context):
    # preview first (B14): boxes on the ground, Show only the picked buildings
    self.layout.operator(browse_gml.CGML3_OT_browse_gml_file.bl_idname, text="CityGML 2/3 Preview (.gml/.xml)")
    self.layout.operator(CGML3_OT_ImportGMLFile.bl_idname, text="CityGML 2/3 Whole File (.gml/.xml)")

def menu_func_export(self, context):
    self.layout.operator(CGML3_OT_ExportGMLFile.bl_idname, text="CityGML 2/3 (.gml)")

# ---------------- Register ----------------
classes = (
    # Property groups used by the preferences and the window manager come first.
    *citydb_connection.property_classes,
    *commit_citydb.property_classes,
    CGML3_Prefs, CGML3_Runtime,
    # Sidebar: status strip (ui.register), 1 Open, 2 Edit, 3 Save, More Tools.
    # Sub-panels follow their parent in registration order.
    CGML3_PT_Main,
    *building_authoring.operator_classes,
    *roof_authoring.operator_classes,
    *opening_authoring.operator_classes,
    *installation_authoring.operator_classes,
    *workflow_panels.operator_classes,
    *workflow_panels.panel_classes,
    *citydb_connection.operator_classes,
    *citydb_panel.classes,
    *background_job.classes,
    *browse_gml.operator_classes,
    *browse_panel.classes,
    *browse_citydb.operator_classes,
    *update_citydb.operator_classes,
    *commit_citydb.operator_classes,
    *db_browse_panel.classes,
    CGML3_PT_Filter,
    CGML3_OT_ParseBBoxString, CGML3_OT_BBoxToString, CGML3_OT_DBAreaFill,
    CGML3_OT_ImportGMLFile, CGML3_OT_ExportGMLFile,
    CGML3_OT_ImportFromDB, CGML3_OT_ExportToDB,
    CGML3_PT_Reload, CGML3_OT_ReloadAddon,
    CGML3_OT_SelectAllLODs, CGML3_OT_DeselectAllLODs,
    CGML3_OT_SelectAllFeatureTypes, CGML3_OT_DeselectAllFeatureTypes,
    join_object_parts.CGML3_OT_JoinObjectParts,
    join_object_parts.CGML3_MT_JoinObjectPartsContext,
    assign_object_part.CGML3_OT_AssignObjectPart,
    assign_object_part.CGML3_OT_DetachObjectPart,
    assign_object_part.CGML3_MT_AssignObjectPartContext,
    ring_opening_sync.CGML3_OT_SyncRingToOpening,
    ring_opening_sync.CGML3_OT_SyncOpeningToRing,
    ring_opening_sync.CGML3_PT_RingOpeningSync,
    facade_authoring.CGML_OT_split_surfaces,
    facade_authoring.CGML_OT_facade_draw,
    facade_authoring.CGML_OT_door_draw,
    facade_authoring.CGML_OT_set_semantic_stamp_source,
    facade_authoring.SEMANTIC_OT_stamp,
    facade_authoring.CGML_OT_align_objects,
    facade_authoring.CGML_OT_delete_feature,
    facade_authoring.CGML_MT_facade_context,
    edit_exit_validation.CGML_OT_resolve_ambiguous_sync,
    edit_exit_validation.CGML_OT_validate_building_openings,
    edit_exit_validation.CGML_OT_validate_scene_openings,
    authoring_core_ops.CGML_OT_feature_edit_begin,
    authoring_core_ops.CGML_OT_feature_edit_commit,
    authoring_core_ops.CGML_OT_feature_edit_cancel,
    authoring_core_ops.CGML_OT_guarded_delete,
    authoring_core_ops.CGML_OT_initialize_duplicate,
    authoring_core_ops.CGML_OT_delete_roof,
    authoring_core_ops.CGML_OT_initialize_roof_duplicate,
    authoring_core_ops.CGML_OT_commit_transform_drift,
    authoring_core_ops.CGML_OT_promote_building,
    *planarity.classes,
    *validation.classes,
)

def register():
    # Put back bundled wheels Blender's wheel sync half-deleted (common/wheel_repair.py).
    if __package__.startswith("bl_ext."):
        try:
            from .common import wheel_repair
            site = wheel_repair.extension_site_packages(bpy.utils.user_resource('EXTENSIONS'))
            fixed = wheel_repair.repair(os.path.join(os.path.dirname(__file__), "wheels"), site)
            if fixed:
                importlib.invalidate_caches()
                print(f"[CityGML] Restored missing wheel files: {', '.join(fixed)}")
        except Exception as e:
            print(f"[CityGML] Wheel check failed: {e}")

    # Check Cython acceleration status
    try:
        from .cython_ext import check_cython_support
        cython_ok, modules, msg = check_cython_support()
        if cython_ok:
            print(f"[CityGML] 🚀 {msg}")
        else:
            print(f"[CityGML] ⚠️ {msg}")
    except ImportError:
        print("[CityGML] ⚠️ Cython modules not found. Using pure Python (slower).")
        print("[CityGML] To enable 10-50× speedup, run: python setup.py build_ext --inplace")
    
    header_panel.register()  # B90: the parent of the step panels below
    for c in classes:
        bpy.utils.register_class(c)
        # Presets is the first sub-panel of More Tools.
        if c is workflow_panels.CGML3_PT_Tools:
            try:
                cgml3_presets.register()
            except Exception as e:
                print(f"[CityGML] Presets register failed: {e}")
    try:
        cgml3_ui.register()
    except Exception as e:
        print(f"[CityGML] UI register failed: {e}")
    try:
        modeltyper.register()
    except Exception as e:
        print(f"[CityGML] ModelTyper register failed: {e}")
        traceback.print_exc()
    try:
        authoring_core_props.register()
        authoring_core_registry.register()
    except Exception as e:
        print(f"[CityGML] Authoring-core register failed: {e}")
        traceback.print_exc()
    bpy.types.Scene.cgml3 = PointerProperty(type=CGML3_Runtime)
    _ensure_scene_props()
    citydb_connection.register_window_manager_props()
    try:
        browse_gml.register()
        browse_citydb.register()
        commit_citydb.register()
    except Exception as e:
        print(f"[CityGML] Browse overlay register failed: {e}")
    try:
        gr.register()  # B32: legacy World keys -> cgml_*
    except Exception as e:
        print(f"[CityGML] World key migration register failed: {e}")
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.VIEW3D_MT_object_context_menu.append(workflow_panels.draw_object_context_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(
        facade_authoring.draw_facade_context_menu
    )
    try:
        edit_exit_validation.register()
    except Exception as e:
        print(f"[CityGML] Edit-exit validation handler register failed: {e}")
    try:
        authoring_core_transform_drift.register()
    except Exception as e:
        print(f"[CityGML] Transform-drift handler register failed: {e}")
    try:
        authoring_core_ops.register_keymaps()
    except Exception as e:
        print(f"[CityGML] Guarded-delete keymap register failed: {e}")
    bpy.types.Scene.cgml3_offset = FloatVectorProperty(
    name="CityModel-Offset", size=3, default=(0.0, 0.0, 0.0)
    )
    # Register drag & drop handler
    try:
        drag_drop.register()
        print("[CityGML] Drag & Drop enabled for .gml/.xml files")
    except Exception as e:
        print(f"[CityGML] Drag & Drop registration failed: {e}")
    # __pycache__ is kept: Python recompiles a module whose source mtime/size
    # changed, and the bundle excludes __pycache__ (blender_manifest.toml).
    # Deleting it on every register made each session recompile all modules;
    # one compile() holds the GIL for the whole module (T09 follow-up).

def unregister():
    try:
        authoring_core_ops.unregister_keymaps()
    except Exception:
        pass
    try:
        authoring_core_transform_drift.unregister()
    except Exception:
        pass
    try:
        edit_exit_validation.unregister()
    except Exception:
        pass
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    try:
        bpy.types.VIEW3D_MT_object_context_menu.remove(
            workflow_panels.draw_object_context_menu
        )
    except Exception:
        pass
    try:
        bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(
            facade_authoring.draw_facade_context_menu
        )
    except Exception:
        pass
    try:
        drag_drop.unregister()
    except Exception:
        pass
    try:
        cgml3_presets.unregister()
    except Exception:
        pass
    try:
        cgml3_ui.unregister()
    except Exception:
        pass
    try:
        modeltyper.unregister()
    except Exception:
        traceback.print_exc()
    try:
        authoring_core_registry.unregister()
        authoring_core_props.unregister()
    except Exception:
        traceback.print_exc()
    try:
        citydb_connection.unregister_window_manager_props()
    except Exception:
        traceback.print_exc()
    try:
        commit_citydb.unregister()
        browse_citydb.unregister()
        browse_gml.unregister()
    except Exception:
        traceback.print_exc()
    try:
        gr.unregister()
    except Exception:
        traceback.print_exc()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    header_panel.unregister()

if __name__ == "__main__":
    register()
