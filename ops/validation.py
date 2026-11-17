# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""B60 (agent plan A5): Check Geometry and CityDoctor buttons.

Both check what an export writes (a temporary file): the selected objects' Buildings, else every
Building of the scene. Nothing in the scene changes. CityDoctor runs only when the user set up a
validator (Preferences); the button is greyed out otherwise."""
from __future__ import annotations

import json

import bpy
from bpy.types import Operator

from ..common import background_job
from ..shared.ring_opening_sync import building_ancestor
from .planarity import in_object_mode


def _top_building(obj):
    root = building_ancestor(obj)
    while root is not None and root.parent is not None and building_ancestor(root.parent) is not None:
        root = building_ancestor(root.parent)  # a BuildingPart: its Building
    return root


def _building_ids(context):
    roots = {_top_building(o) for o in context.selected_objects}
    return sorted(str(r.get("gml_id")) for r in roots if r is not None and r.get("gml_id"))


def _version(context):
    from .. import _detect_citygml_version_from_scene
    return _detect_citygml_version_from_scene(context.scene) or "3.0"


def _report_findings(report, buildings, summary_codes, cd_global):
    from ..shared.export_helpers import log_text_to_blender
    log_text_to_blender("citygml_geometry_check", json.dumps(buildings, indent=1, default=str))
    bad = {gid: b for gid, b in buildings.items() if b["checks"] or b["citydoctor"]}
    if not bad:
        report({'INFO'}, f"{len(buildings)} building(s): no geometry findings")
        return
    codes = dict(summary_codes)
    for k, v in (cd_global or {}).items():
        codes[f"CityDoctor {k}"] = v
    text = ", ".join(f"{k} {v}" for k, v in sorted(codes.items()))
    report({'WARNING'}, f"{len(bad)} of {len(buildings)} building(s) with findings: {text} "
                        "(details in the citygml_geometry_check text block)")


class CGML_OT_check_geometry(Operator):
    """Check what an export writes: closed shell, rings crossing themselves, non-planar polygons, windows/doors without a hole (selected Buildings, else the scene)"""
    bl_idname = "cgml.check_geometry"
    bl_label = "Check Geometry"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..shared import agent_api
        with in_object_mode(context):
            res = agent_api.validate(_building_ids(context) or None, _version(context))
        if not res["ok"]:
            self.report({'ERROR'}, f"{res['error_code']}: {res['message']}")
            return {'CANCELLED'}
        _report_findings(self.report, res["buildings"], res["summary"], None)
        return {'FINISHED'}


class CGML_OT_citydoctor_validate(Operator):
    """Validate what an export writes with CityDoctor (installed separately; set its validator in Preferences)"""
    bl_idname = "cgml.citydoctor_validate"
    bl_label = "CityDoctor"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        from ..shared import agent_api, citydoctor
        if agent_api.citydoctor_validator() is None:
            cls.poll_message_set("CityDoctor is not set up: press the ? button beside it "
                                 f"(path e.g. {citydoctor.path_hint()})")
            return False
        return not background_job.is_running()

    def execute(self, context):
        import os
        import tempfile
        from ..shared import agent_api, citydoctor, geometry_checks
        validator = agent_api.citydoctor_validator()
        ids = _building_ids(context)
        path = os.path.join(tempfile.mkdtemp(prefix="cgml_citydoctor_"), "validate.gml")
        with in_object_mode(context):
            res = agent_api.export(path, _version(context), scope="IDS" if ids else "SCENE", gml_ids=ids,
                                   xsd=False)
        if not res["ok"]:
            self.report({'ERROR'}, f"{res['error_code']}: {res['message']}")
            return {'CANCELLED'}
        scene = context.scene
        report = self.report if bpy.app.background else None

        def worker(job):
            job.set_progress(0, 0, "CityDoctor (Java)")
            cd = citydoctor.parse_report(citydoctor.run(validator, path))
            return cd, geometry_checks.check_file(path)

        def on_done(result, error, cancelled):
            say = report or (lambda level, text: background_job_notify(text, next(iter(level))))
            if cancelled:
                say({'WARNING'}, "CityDoctor cancelled")
                return
            if error is not None:
                say({'ERROR'}, f"CityDoctor failed: {error}")
                return
            cd, checks = result
            buildings = agent_api.merge_validation(checks, cd)
            scene[agent_api.LAST_VALIDATION_KEY] = json.dumps(
                {"path": path, "buildings": buildings, "summary": geometry_checks.summary(checks),
                 "citydoctor": cd["global"]})
            _report_findings(say, buildings, geometry_checks.summary(checks), cd["global"])

        job = background_job.Job("citydoctor", worker, on_done=on_done, label="CityDoctor")
        if background_job.start(job, context) is None:
            self.report({'ERROR'}, "Another background job is running")
            return {'CANCELLED'}
        return {'FINISHED'}


def background_job_notify(text, level):
    from .browse_gml import _notify
    _notify(text, level)


def draw_citydoctor_setup(layout, prefs):
    """B86: the setup steps for this OS, the download link and the path field (popup and Preferences)."""
    from ..shared import citydoctor
    layout.label(text=f"CityDoctor on {citydoctor.SETUP[citydoctor.os_key()][0]} (optional validator)",
                 icon='CHECKMARK')
    col = layout.column(align=True)
    for line in citydoctor.setup_lines():
        col.label(text=line)
    layout.operator("wm.url_open", text="Open the CityDoctor Download Page",
                    icon='URL').url = citydoctor.DOWNLOAD_URL
    layout.prop(prefs, "citydoctor_validator", text="Path")
    found = citydoctor.validator_path(prefs.citydoctor_validator)
    layout.label(text=f"Found: {found}" if found else "Not found yet: the CityDoctor button stays grey",
                 icon='CHECKMARK' if found else 'ERROR')


class CGML_OT_citydoctor_help(Operator):
    """How to set up CityDoctor on this computer: download, unzip, set the path"""
    bl_idname = "cgml.citydoctor_help"
    bl_label = "CityDoctor Setup"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=480)

    def draw(self, context):
        from .browse_gml import addon_prefs
        draw_citydoctor_setup(self.layout, addon_prefs(context))

    def execute(self, context):
        return {'FINISHED'}


classes = (CGML_OT_check_geometry, CGML_OT_citydoctor_validate, CGML_OT_citydoctor_help)


class CGML_OT_repair_geometry(Operator):
    """Repair one kind of geometry finding on the selected Buildings (else every Building); only when pressed"""
    bl_idname = "cgml.repair_geometry"
    bl_label = "Repair Geometry"
    bl_options = {'REGISTER', 'UNDO'}

    code: bpy.props.EnumProperty(name="Repair", items=(
        ('SE_BS_UNFRAGMENTED', "Split Bent Surfaces",
         "A surface whose polygons point in different directions becomes one surface per direction "
         "(CityDoctor SE_BS_UNFRAGMENTED)"),
        ('GE_S_NOT_CLOSED', "Weld T-Junctions",
         "Insert a neighbour's corner into the edge it lies on, so the shell closes (GE_S_NOT_CLOSED)"),
        ('GE_S_SELF_INTERSECTION', "Find Self-Intersections",
         "List the polygons that cross each other (GE_S_SELF_INTERSECTION); nothing is changed"),
    ))

    def execute(self, context):
        with in_object_mode(context):
            return self.run(context)

    def run(self, context):
        from ..shared import agent_api
        from ..shared.export_scope import top_level_features
        ids = _building_ids(context) or [g for g, t in top_level_features(context.scene).items()
                                         if t in ("Building", "BuildingPart")]
        before = after = 0
        notes = []
        for gid in ids:
            res = agent_api.repair(gid, self.code)
            if not res["ok"]:
                self.report({'ERROR'}, f"{gid}: {res['error_code']} {res['message']}")
                return {'CANCELLED'}
            before += res["before"]
            after += res["after"]
            notes += [f"{p['a']['object']} / {p['b']['object']}" for p in res.get("pairs", [])]
        if self.code == 'GE_S_SELF_INTERSECTION':
            self.report({'WARNING'} if notes else {'INFO'},
                        f"{len(notes)} crossing polygon pair(s)" + (": " + "; ".join(notes[:3]) if notes else ""))
        else:
            self.report({'WARNING'} if after else {'INFO'}, f"{self.code}: {before} -> {after} in {len(ids)} building(s)")
        return {'FINISHED'}


classes = (*classes, CGML_OT_repair_geometry)
