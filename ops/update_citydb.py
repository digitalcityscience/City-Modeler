# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/update_citydb.py
"""Write the selected loaded feature back to 3DCityDB (T10).

Only a root object that DB Browse (T09) loaded can be written back: it carries
``cgml3_source_kind=citydb``, ``cgml3_db_feature_id`` and ``cgml3_objectid``
and comes from the scene's 3DCityDB target.

1. Main thread: the root and its children are exported with the add-on's
   exporter to a temporary CityGML 3.0 file (``gml:id`` = objectid). The file
   must hold exactly that one feature and no add-on properties.
2. Background job (``common.background_job``): the active rows with that
   objectid are read, then ``citydb import citygml --preview`` runs. A dry run
   stops here; nothing is written.
3. A real run (terminate / delete need an explicit confirmation listing the
   rows) starts only when the database still has the confirmed rows and the
   preview replaces exactly those; then the same import runs without
   ``--preview`` and the new active row is read back.
4. Main thread: the root gets the new feature.id, the boxes of the replaced
   rows are swapped for the new row and the feature is loaded again from the
   database (``ops.browse_citydb.start_load``).

citydb-tool behaviour and options: ``citydb.update``.
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..citydb import catalog
from ..citydb import update
from ..citydb.catalog import CatalogRow
from ..common import background_job
from . import browse_citydb as bc
from .browse_gml import SOURCE_KIND_PROP, _notify, _redraw_views

UPDATE_JOB = "citydb-update"
UPDATE_TIMEOUT_S = 600


# --------------------------------------------------------------------------- target object
def feature_root(obj):
    """The outermost ancestor (or ``obj``) that carries a 3DCityDB feature id."""
    root, p = None, obj
    while p is not None:
        if p.get(bc.DB_FEATURE_ID_PROP) is not None:
            root = p
        p = p.parent
    return root


def _scene_key(context) -> str:
    try:
        target, *_ = bc.scene_target(context)
    except Exception:
        return ""
    return bc.target_key(target)


def status(context, obj=None):
    """``(root, ok, reason)`` for the active object (panel, poll and operator)."""
    obj = obj if obj is not None else getattr(context, "active_object", None)
    if obj is None:
        return None, False, "Select a building loaded from 3DCityDB"
    root = feature_root(obj)
    if root is None:
        kind = obj.get(SOURCE_KIND_PROP)
        return None, False, ("Not a loaded 3DCityDB feature" if kind == "citydb"
                             else "Not loaded from 3DCityDB (load it with DB Browse first)")
    key = _scene_key(context)
    if not key:
        return root, False, "Pick the city model in the 3DCityDB panel"
    ok, reason = update.eligibility({k: root.get(k) for k in root.keys()}, key)
    return root, ok, reason


def _feature_id(root) -> int:
    return int(root[bc.DB_FEATURE_ID_PROP])


def load_mode(root) -> str:
    """Reload with the LoD the root was loaded with."""
    try:
        return 'LOD2' if int(root.get(bc.LOADED_LOD_PROP, -1)) == 2 else 'HIGHEST'
    except (TypeError, ValueError):
        return 'HIGHEST'


def known_versions(session, root) -> List[CatalogRow]:
    """Active rows of the root's objectid as far as this session knows them (dialog text)."""
    objectid = str(root.get(bc.OBJECTID_PROP) or "")
    plan = session.update_plan if session is not None else {}
    if plan.get("objectid") == objectid and plan.get("rows"):
        return list(plan["rows"])
    if session is not None:
        rows = [r for r in session.rows if r.objectid == objectid]
        if rows:
            return rows
    return [CatalogRow(id=_feature_id(root), objectid=objectid, classname="")]


# --------------------------------------------------------------------------- export
def export_feature(context, root, path: str, srid: int) -> Optional[str]:
    """Export ``root`` and its children to ``path``; returns an error text or None."""
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError as exc:
            return f"Leave {context.mode} mode first ({exc})"
    view_layer = context.view_layer
    objs = [root, *root.children_recursive]
    hidden = [o.name for o in objs if o.name not in view_layer.objects or o.hide_get(view_layer=view_layer)]
    if root.name in hidden:
        return "The feature is hidden or excluded from the view layer; show it first"
    before = [o for o in context.selected_objects]
    active = view_layer.objects.active
    try:
        for o in before:
            o.select_set(False)
        for o in objs:
            if o.name not in hidden:
                o.select_set(True)
        view_layer.objects.active = root
        # scope: the default (SCENE, B56) wrote every loaded building into the one-feature file
        res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version='3.0', scope='SELECTION',
                                            srs_name=f"EPSG:{srid}" if srid else "")
    except Exception as exc:
        return f"Export failed: {exc}"
    finally:
        for o in objs:
            try:
                o.select_set(False)
            except RuntimeError:
                pass
        for o in before:
            try:
                o.select_set(True)
            except (RuntimeError, ReferenceError):
                pass
        view_layer.objects.active = active
    if 'FINISHED' not in res:
        return "Export failed (see the Info log)"
    return None


# --------------------------------------------------------------------------- job
def start_update(context, root, mode: str = 'TERMINATE', *, dry_run: bool = True,
                 expected_ids: Sequence[int] = (), reload: bool = True, manual: bool = False):
    """Export ``root`` and run the preview (and, unless ``dry_run``, the import) in the background.

    Returns ``(session, driver)``; raises ``RuntimeError`` / ``InvalidId`` /
    ``CityDBError`` before anything is written.
    """
    from ..citydb import client

    if mode not in update.TOOL_MODES:
        raise RuntimeError(f"Unknown update mode {mode}")
    if background_job.is_running() or bc.import_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    _root, ok, reason = status(context, root)
    if not ok:
        raise RuntimeError(reason)
    if not dry_run and mode in update.CONFIRM_MODES and not expected_ids:
        raise RuntimeError(f"{mode.title()} needs an explicit confirmation of the rows it replaces")
    objectid = update.validate_objectid(str(root[bc.OBJECTID_PROP]))
    old_fid = _feature_id(root)
    root_name = root.name
    session = bc._session_for_scene(context)
    if session.origin is None and session.srid:
        # a new session (preview closed): the reload must use the World origin of the loaded model
        origin = session.fixed_origin or bc.existing_origin(context, f"EPSG:{session.srid}")
        if origin is not None:
            bc._apply_origin(context, session, tuple(origin), f"EPSG:{session.srid}")
    target = session.target
    password = bc._password(target)
    prefs = bc._tool_prefs(context)
    expected = [int(i) for i in expected_ids]
    reload_mode = load_mode(root)

    path = bc._temp_export_path()
    t_start = time.perf_counter()
    problem = export_feature(context, root, path, session.srid)
    if problem is None:
        problem = update.export_problem(path, objectid)
    if problem is not None:
        bc._remove_file(path)
        raise RuntimeError(problem)
    export_s = time.perf_counter() - t_start
    run = "dry run" if dry_run else update.TOOL_MODES[mode]
    print(f"[CityDB Update] {objectid} (id {old_fid}): {run} with --import-mode={update.TOOL_MODES[mode]}")

    def worker(job):
        from .citydb_cli import CityDBTool

        def on_output(line):
            job.set_progress(0, 0, f"citydb-tool: {line[-60:]}")

        conn_kw = dict(host=target.server.host, port=str(target.server.port), db=target.database,
                       user=target.server.user, password=password, db_schema=target.schema,
                       sslmode=target.server.sslmode)
        _info, used = client.verify_target(target, password)
        with client.connect(target.server, target.database, used) as conn:
            active = catalog.active_versions(conn, target.schema, objectid)
        job.set_progress(0, 0, "citydb-tool: preview")
        tool = CityDBTool(prefs)
        ok, log = tool.import_citygml_to_db(path, **conn_kw, **update.import_options(mode, preview=True),
                                            on_output=on_output, cancelled=job.cancelled)
        if job.cancelled():
            raise background_job.JobCancelled()
        preview = update.parse_import_log(log)
        result = {"ok": ok, "log": log, "preview": preview, "active": active, "written": False}
        if not ok or dry_run:
            return result
        problem = update.plan_problem(mode, expected, active, preview)
        if problem is not None:
            result["problem"] = problem
            return result
        # the real run is not cancelled half way: citydb-tool writes one transaction
        job.set_progress(0, 0, f"citydb-tool: {update.TOOL_MODES[mode]}")
        ok, log2 = tool.import_citygml_to_db(path, **conn_kw, **update.import_options(mode, preview=False),
                                             on_output=on_output)
        result.update(ok=ok, log=log + "\n" + log2, report=update.parse_import_log(log2), written=ok)
        with client.connect(target.server, target.database, used) as conn:
            result["after"] = catalog.active_versions(conn, target.schema, objectid)
        return result

    def on_done(result, error, cancelled):
        bc._remove_file(path)
        session.last_update = {"objectid": objectid, "old_id": old_fid, "mode": mode, "dry_run": dry_run,
                               "export_s": export_s, "seconds": time.perf_counter() - t_start}
        if cancelled:
            _finish(session, "Update cancelled, nothing written", 'WARNING')
            return
        if error is not None:
            _finish(session, bc._error_text(error), 'ERROR', error=True)
            return
        if result.get("log"):
            from ..shared.export_helpers import log_text_to_blender
            log_text_to_blender("citydb_update", result["log"])
        active: List[CatalogRow] = result["active"]
        preview: update.ImportReport = result["preview"]
        session.update_plan = {"objectid": objectid, "rows": list(active), "ids": [r.id for r in active],
                               "duplicates": preview.duplicates, "mode": mode}
        session.last_update.update(active=[r.id for r in active], duplicates=preview.duplicates,
                                   counts=dict(preview.counts))
        if not result["ok"]:
            _finish(session, "citydb import citygml failed (see text log citydb_update)", 'ERROR', error=True)
            return
        if dry_run:
            _finish(session, dry_run_message(mode, objectid, active, preview), 'INFO')
            return
        if result.get("problem"):
            _finish(session, result["problem"], 'ERROR', error=True)
            return
        after: List[CatalogRow] = result.get("after", [])
        new = update.new_active_row([r.id for r in active], after)
        if new is None:
            _finish(session, f"Nothing written for {objectid}: "
                    + ("the feature exists (skip)" if result["report"].skipped_file else "no new row"),
                    'WARNING')
            return
        obj = bpy.data.objects.get(root_name)
        if obj is not None and obj.get(bc.OBJECTID_PROP) == objectid:
            from . import commit_citydb  # B84: the written state is the new base of the pending changes
            commit_citydb.stamp(bpy.context.scene, session, {objectid: (obj, new.id)})
        bc.replace_rows(session, [r.id for r in active], after)
        session.update_plan = {}
        session.last_update.update(new_id=new.id, written=True)
        verb = "terminated" if mode == 'TERMINATE' else "deleted"
        _finish(session, f"Updated {objectid}: id {', '.join(str(r.id) for r in active)} {verb}, "
                         f"new id {new.id}", 'INFO')
        if reload:
            bc.set_selection_fids(session, [new.id])
            bc._defer(lambda: bc._start_load_safe(session, [new.id], reload_mode))

    job = background_job.Job(UPDATE_JOB, worker, on_done=on_done,
                             label=f"{'Dry run' if dry_run else 'Updating'} {objectid}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        bc._remove_file(path)
        raise RuntimeError("Another background job is running")
    return session, driver


def dry_run_message(mode: str, objectid: str, active: Sequence[CatalogRow], preview: update.ImportReport) -> str:
    if not active:
        return f"Dry run: {objectid} has no active row in the database; nothing would be replaced"
    if mode == 'SKIP':
        if preview.skipped_file:
            return f"Dry run (skip): {objectid} exists, skip would write nothing"
        return f"Dry run (skip): would insert {preview.counts_text()}"
    text = (f"Dry run ({update.TOOL_MODES[mode]}): would replace {preview.duplicates} row(s) of {objectid} "
            f"({catalog.versions_label(active)}) with {preview.counts_text()} · nothing written")
    if len(active) > 1:
        text += f" · WARNING: all {len(active)} versions would be replaced"
    return text


def _finish(session, message: str, level: str, *, error: bool = False) -> None:
    session.message = message
    if error:
        session.last_update["error"] = message
    session.last_update["message"] = message
    _redraw_views()
    _notify(message, level)


# --------------------------------------------------------------------------- operator
_MODE_ITEMS = [
    ('TERMINATE', "Terminate", "Keep the current row as history (termination_date) and insert the edited "
                               "feature as a new row (recommended)"),
    ('DELETE', "Delete", "Delete the current row and insert the edited feature as a new row"),
    ('SKIP', "Skip", "Insert only if no active row with this objectid exists (never changes an existing feature)"),
]


class CGML3_OT_db_update_feature(bpy.types.Operator):
    """Write the selected building back to the 3DCityDB it was loaded from"""
    bl_idname = "cgml3.db_update_feature"
    bl_label = "Update Feature in 3DCityDB"
    bl_options = {'REGISTER'}

    mode: EnumProperty(name="Mode", items=_MODE_ITEMS, default='TERMINATE')
    dry_run: BoolProperty(name="Dry run", default=True, options={'SKIP_SAVE'},
                          description="Only run citydb-tool --preview: show what would change, write nothing")
    confirmed: BoolProperty(name="Confirm", default=False, options={'SKIP_SAVE'},
                            description="Required for terminate and delete")
    expected_ids: StringProperty(name="Rows", default="", options={'SKIP_SAVE'},
                                 description="Comma-separated feature ids the confirmation covers")
    reload: BoolProperty(name="Reload", default=True, options={'SKIP_SAVE'},
                         description="Load the written feature again from the database")

    @classmethod
    def poll(cls, context):
        _root, ok, reason = status(context)
        if not ok:
            cls.poll_message_set(reason)
        return ok

    @classmethod
    def description(cls, context, props):
        if props.dry_run:
            return "Dry run: export the selected feature and let citydb-tool preview the import (writes nothing)"
        return dict((k, d) for k, _n, d in _MODE_ITEMS).get(props.mode, "")

    def _needs_confirmation(self) -> bool:
        return not self.dry_run and self.mode in update.CONFIRM_MODES

    def invoke(self, context, event):
        if not self._needs_confirmation():
            return self.execute(context)
        root, ok, reason = status(context)
        if not ok:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        rows = known_versions(bc.active_session(context), root)
        self.expected_ids = ",".join(str(r.id) for r in rows)
        self.confirmed = False
        self._lines = update.confirm_lines(self.mode, str(root[bc.OBJECTID_PROP]), rows,
                                           _where(context))
        return context.window_manager.invoke_props_dialog(self, width=560, title="Write back to 3DCityDB",
                                                          confirm_text=self.mode.title())

    def draw(self, context):
        layout = self.layout
        for i, line in enumerate(getattr(self, "_lines", ()) or ()):
            layout.label(text=line, icon='ERROR' if i == 0 else 'BLANK1')
        n = len([t for t in self.expected_ids.split(",") if t.strip()])
        verb = "terminate" if self.mode == 'TERMINATE' else "delete"
        layout.prop(self, "confirmed", text=f"Yes, {verb} {n} row(s)")
        layout.prop(self, "reload", text="Load the written feature again")

    def execute(self, context):
        root, ok, reason = status(context)
        if not ok:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        expected: List[int] = []
        if self._needs_confirmation():
            try:
                expected = catalog.validate_feature_ids(t for t in self.expected_ids.split(",") if t.strip())
            except catalog.InvalidId as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            if not (self.confirmed and expected):
                self.report({'ERROR'}, f"{self.mode.title()} not confirmed: nothing written")
                return {'CANCELLED'}
        try:
            session, driver = start_update(context, root, self.mode, dry_run=self.dry_run,
                                           expected_ids=expected, reload=self.reload)
        except Exception as exc:
            return bc._report_start_error(self, exc)
        if driver.finished:
            err = session.last_update.get("error")
            if err:
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            self.report({'INFO'}, session.message)
        return {'FINISHED'}


def _where(context) -> str:
    try:
        target, *_ = bc.scene_target(context)
    except Exception:
        return "the scene's 3DCityDB"
    s = target.server
    return f"{s.user}@{s.host}:{s.port}/{target.database}/{target.schema}"


operator_classes = (CGML3_OT_db_update_feature,)
