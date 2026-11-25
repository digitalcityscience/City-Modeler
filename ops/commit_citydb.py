# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/commit_citydb.py
"""Commit the scene's pending changes to 3DCityDB (B84).

The scene is a working copy of the city model picked in 1 · Open. Loading,
editing, adding and deleting objects in Blender never changes the database.
``scan`` compares the scene with what was loaded / committed last
(``citydb.changes``): new, changed, deleted, synced. One commit writes them:

1. Main thread: the new and changed top-level features are exported to one
   temporary CityGML 3.0 file (export scope IDS).
2. Background job: the active rows of every objectid are read. A new feature
   that already exists, or a changed / deleted one whose row is no longer the
   loaded one, is a conflict: nothing is written. Otherwise ``citydb import
   citygml --import-mode=terminate`` runs (preview first; it must replace
   exactly the changed rows), then ``citydb delete --delete-mode=terminate``
   for the deleted features. Old versions stay in the database as history.
3. Main thread: the written roots get their new feature.id and fingerprint,
   deleted features are no longer tracked, the browse boxes follow.

State: ``scene["cgml3_db_tracked"]`` (JSON ``{city model key: {objectid:
feature id}}``) and ``root["cgml3_db_fingerprint"]``. Unload (browse panel)
removes a feature from the scene *and* from the tracked set, so it is not a
pending delete; deleting the object in Blender is.
"""

from __future__ import annotations

import hashlib
import json
import time
from array import array
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty

from ..citydb import catalog, changes, update
from ..citydb.changes import CHANGED, DELETED, NEW, Change
from ..common import background_job
from ..shared import export_scope
from ..shared import georef as gr
from . import browse_citydb as bc
from . import update_citydb
from .browse_gml import FEATURE_ID_PROP, SOURCE_KIND_PROP, SOURCE_PATH_PROP, _redraw_views
from ..shared import cityjson_open as co

COMMIT_JOB = "citydb-commit"
TRACKED_PROP = "cgml3_db_tracked"
FINGERPRINT_PROP = "cgml3_db_fingerprint"
# bookkeeping of the add-on: never part of what a feature "is"
_SKIP_KEYS = frozenset({SOURCE_KIND_PROP, SOURCE_PATH_PROP, bc.SOURCE_PROFILE_PROP, bc.DB_FEATURE_ID_PROP,
                        bc.OBJECTID_PROP, bc.LOADED_LOD_PROP, FINGERPRINT_PROP,
                        co.SOURCE_PROP, co.FINGERPRINT_PROP})
# CJ-06: the browse preview tags the root (feature id) after the build
_CJ_SKIP_KEYS = _SKIP_KEYS | {FEATURE_ID_PROP}


# --------------------------------------------------------------------------- fingerprint
def _plain(value):
    if hasattr(value, "to_dict"):
        return json.dumps(value.to_dict(), sort_keys=True, default=str)
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def _props(idblock, skip=_SKIP_KEYS) -> bytes:
    return repr(sorted((k, _plain(v)) for k, v in idblock.items() if k not in skip)).encode()


def cityjson_fingerprint(root) -> str:
    """``fingerprint`` for save into a CityJSON source (CJ-06)."""
    return fingerprint(root, _CJ_SKIP_KEYS)


def fingerprint(root, skip=_SKIP_KEYS) -> str:
    """Hash of what the exporter reads from ``root`` and its children: names, transforms,
    custom properties, mesh geometry, materials.

    shortcut: UV layers and image textures are not hashed, a texture-only edit is not seen
    as a change; add them when textures are edited in Blender.
    """
    h = hashlib.sha1()
    for o in sorted([root, *root.children_recursive], key=lambda o: o.name):
        h.update(o.name.encode())
        h.update(repr([round(v, 5) for row in o.matrix_world for v in row]).encode())
        h.update(_props(o, skip))
        me = o.data if o.type == 'MESH' else None
        if me is None:
            continue
        for seq, attr, code, width in ((me.vertices, "co", 'f', 3), (me.loops, "vertex_index", 'i', 1),
                                       (me.polygons, "loop_total", 'i', 1),
                                       (me.polygons, "material_index", 'i', 1)):
            buf = array(code, bytes(array(code).itemsize * len(seq) * width))
            seq.foreach_get(attr, buf)
            h.update(buf.tobytes())
        h.update(_props(me, skip))
        for m in me.materials:
            if m is not None:
                h.update(m.name.encode())
                h.update(repr([round(c, 4) for c in m.diffuse_color]).encode())
                h.update(_props(m, skip))
    return h.hexdigest()


# --------------------------------------------------------------------------- tracked set
def _all_tracked(scene) -> Dict[str, Dict[str, int]]:
    try:
        data = json.loads(scene.get(TRACKED_PROP) or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def tracked(scene, path: str) -> Dict[str, int]:
    return {str(k): int(v) for k, v in _all_tracked(scene).get(path, {}).items()}


def track(scene, path: str, add: Mapping[str, int] = (), remove: Iterable[str] = ()) -> None:
    data = _all_tracked(scene)
    entry = dict(data.get(path, {}))
    for oid in remove:
        entry.pop(oid, None)
    entry.update({str(k): int(v) for k, v in dict(add).items()})
    if entry:
        data[path] = entry
    else:
        data.pop(path, None)
    scene[TRACKED_PROP] = json.dumps(data, sort_keys=True)
    _cache.clear()


def forget(roots) -> set:
    """Unload (file or 3DCityDB browse): the roots leave every working copy they are in, so neither the
    commit nor save into the CityJSON source counts them as deleted. A CityJSON building that was
    committed is in both (CJ-04). Returns the session paths whose boxes must follow."""
    scene, paths = bpy.context.scene, set()
    for r in roots:
        if r.get(SOURCE_KIND_PROP) == "citydb":
            paths.add(str(r.get(SOURCE_PATH_PROP)))
            track(scene, str(r.get(SOURCE_PATH_PROP)), remove=[str(r.get(bc.OBJECTID_PROP))])
        if r.get(co.SOURCE_PROP):
            paths.add(str(r[co.SOURCE_PROP]))
            co.track_loaded(gr.world(), str(r[co.SOURCE_PROP]), remove=[str(r.get("gml_id"))])
    return paths


def refresh_boxes(paths) -> None:
    """Browse boxes of the other working copy after an Unload: the building shows as not loaded."""
    from . import browse_gml
    for p in paths:
        s = browse_gml.get_session(p)
        if s is not None:
            (bc._refresh_loaded if s.kind == "citydb" else browse_gml._refresh_loaded)(s)
            browse_gml.write_proxy_states(s)


def stamp(scene, session, roots: Mapping[str, Tuple[object, int]]) -> None:
    """``{objectid: (root object or None, feature id)}`` is in the database as it is in the scene now."""
    bpy.context.view_layer.update()  # matrix_world of freshly built objects
    for oid, (obj, fid) in roots.items():
        if obj is None:
            continue
        obj[SOURCE_KIND_PROP] = "citydb"
        obj[SOURCE_PATH_PROP] = session.path
        obj[bc.SOURCE_PROFILE_PROP] = session.profile
        obj[bc.DB_FEATURE_ID_PROP] = fid if fid < 2 ** 31 else str(fid)
        obj[bc.OBJECTID_PROP] = oid
        obj[FINGERPRINT_PROP] = fingerprint(obj)
    track(scene, session.path, add={oid: fid for oid, (_obj, fid) in roots.items()})


# --------------------------------------------------------------------------- scan
_cache: Dict[str, object] = {}


def is_shell(root) -> bool:
    """Every surface was deleted in Blender: only the invisible root is left. That is a deleted
    building (the writers refuse to export it)."""
    return not any(o.type == 'MESH' and len(o.data.polygons) for o in (root, *root.children_recursive))


def remove_shells(scene, objectids: Iterable[str]) -> None:
    roots = export_scope.top_level_feature_objects(scene)
    for oid in objectids:
        root = roots.get(oid)
        if root is not None and is_shell(root):
            for o in (*root.children_recursive, root):
                bpy.data.objects.remove(o)


def scan(context) -> Optional[Tuple[str, List[Change]]]:
    """``(city model key, changes)`` of the scene, or None when no city model is picked."""
    path = update_citydb._scene_key(context)
    if not path:
        return None
    scene = context.scene
    known = tracked(scene, path)
    feats: Dict[str, Tuple[str, bool]] = {}
    for gid, obj in export_scope.top_level_feature_objects(scene).items():
        if (gid not in known and obj.get(SOURCE_KIND_PROP) == "citydb" and obj.get(SOURCE_PATH_PROP) == path
                and obj.get(bc.OBJECTID_PROP) == gid):
            # loaded before B84: the feature id is on the object, no fingerprint -> "changed"
            try:
                known[gid] = int(obj.get(bc.DB_FEATURE_ID_PROP) or 0)
            except (TypeError, ValueError):
                known[gid] = 0
        if is_shell(obj):
            continue
        feats[gid] = (obj.name, gid in known and obj.get(FINGERPRINT_PROP) == fingerprint(obj))
    return path, changes.classify(feats, known)


def scan_cached(context, max_age: float = 0.5):
    """``scan`` for panel drawing: at most one scan per ``max_age`` seconds and scene."""
    key, now = context.scene.name, time.monotonic()
    if _cache.get("key") != key or now - float(_cache.get("t", 0.0)) > max_age:
        _cache.update(key=key, t=now, value=scan(context))
    return _cache["value"]


def selected_ids(context) -> List[str]:
    """gml:ids of the top-level features the selection belongs to."""
    by_name = {o.name: gid for gid, o in export_scope.top_level_feature_objects(context.scene).items()}
    out = set()
    for o in context.selected_objects:
        while o is not None and o.name not in by_name:
            o = o.parent
        if o is not None:
            out.add(by_name[o.name])
    return sorted(out)


# --------------------------------------------------------------------------- job
def start_commit(context, objectids: Optional[Iterable[str]] = None, *, manual: bool = False):
    """Write the pending changes (all, or those of ``objectids``); returns ``(session, driver)``.

    Raises ``RuntimeError`` / ``InvalidId`` / ``CityDBError`` before anything is written.
    """
    from ..citydb import client

    if background_job.is_running() or bc.import_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError as exc:
            raise RuntimeError(f"Leave {context.mode} mode first ({exc})")
    found = scan(context)
    if found is None:
        raise RuntimeError("Connect and pick a city model first (Open tab)")
    only = None if objectids is None else set(objectids)
    todo = [c for c in changes.pending(found[1]) if only is None or c.objectid in only]
    if not todo:
        raise RuntimeError("Nothing to commit: the scene matches the database")
    for c in todo:
        update.validate_objectid(c.objectid)
    session = bc._session_for_scene(context)
    world = gr.world()
    crs = world.get(gr.CRS) if world is not None else None
    if session.srid and crs and not gr.same_crs(crs, f"EPSG:{session.srid}"):
        # citydb-tool does not reproject on import
        raise RuntimeError(f"The scene is in {crs}, the city model in EPSG:{session.srid}: nothing written")
    target = session.target
    password = bc._password(target)
    prefs = bc._tool_prefs(context)
    scene_name = context.scene.name
    write = [c for c in todo if c.kind in (NEW, CHANGED)]
    write_ids = [c.objectid for c in write]
    replaced = sum(1 for c in write if c.kind == CHANGED)

    path = ""
    if write:
        path = bc._temp_export_path()
        try:
            res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version='3.0', scope='IDS',
                                                scope_gml_ids=",".join(write_ids),
                                                srs_name=f"EPSG:{session.srid}" if session.srid else "")
            problem = None if 'FINISHED' in res else "Export failed (see the Info log)"
        except RuntimeError as exc:  # the operator error carries the traceback: keep its last line
            problem = "Export failed: " + (str(exc).strip().splitlines() or ["?"])[-1][:300]
        problem = problem or changes.export_problem(path, write_ids)
        if problem is not None:
            bc._remove_file(path)
            raise RuntimeError(problem)
    print(f"[CityDB Commit] {session.label}: {changes.summary(todo)}")

    def worker(job):
        from .citydb_cli import CityDBTool

        def on_output(line):
            job.set_progress(0, 0, f"citydb-tool: {line[-60:]}")

        def versions(used):
            with client.connect(target.server, target.database, used) as conn:
                return {c.objectid: catalog.active_versions(conn, target.schema, c.objectid) for c in todo}

        conn_kw = dict(host=target.server.host, port=str(target.server.port), db=target.database,
                       user=target.server.user, password=password, db_schema=target.schema,
                       sslmode=target.server.sslmode)
        _info, used = client.verify_target(target, password)
        before = versions(used)
        result = {"before": before, "log": "", "written": False, "removed": False,
                  "conflicts": changes.conflicts(todo, {oid: [r.id for r in rows] for oid, rows in before.items()})}
        if result["conflicts"]:
            return result
        tool = CityDBTool(prefs)
        if write:
            job.set_progress(0, 0, "citydb-tool: preview")
            ok, log = tool.import_citygml_to_db(path, **conn_kw, **update.import_options('TERMINATE', preview=True),
                                                on_output=on_output, cancelled=job.cancelled)
            if job.cancelled():
                raise background_job.JobCancelled()
            result["log"] = log
            preview = update.parse_import_log(log)
            if not ok:
                result["problem"] = "citydb-tool preview failed (see text log citydb_commit); nothing written"
            elif preview.duplicates != replaced:
                result["problem"] = (f"citydb-tool would replace {preview.duplicates} row(s), the commit changes "
                                     f"{replaced}; nothing written")
            elif preview.problems:
                result["problem"] = f"citydb-tool preview reported: {preview.problems[0]}; nothing written"
            if result.get("problem"):
                return result
            # not cancelled half way: citydb-tool writes one transaction
            job.set_progress(0, 0, "citydb-tool: import")
            ok, log = tool.import_citygml_to_db(path, **conn_kw, **update.import_options('TERMINATE', preview=False),
                                                on_output=on_output)
            result["log"] += "\n" + log
            if not ok:
                result["problem"] = "citydb import citygml failed (see text log citydb_commit); nothing written"
                return result
            result["written"] = True
        gone = [c.objectid for c in todo if c.kind == DELETED and before[c.objectid]]
        if gone:
            job.set_progress(0, 0, "citydb-tool: delete")
            ok, log = tool.delete_features_from_db(**conn_kw, delete_mode="terminate", feature_ids=gone,
                                                   on_output=on_output)
            result["log"] += "\n" + log
            result["removed"] = ok
            if not ok:
                result["problem"] = ("citydb delete failed (see text log citydb_commit)"
                                     + ("; new and changed features were written" if write else ""))
        result["after"] = versions(used)
        return result

    def on_done(result, error, cancelled):
        if path:
            bc._remove_file(path)
        _cache.clear()
        session.last_update = {"commit": True}
        finish = update_citydb._finish
        if cancelled:
            finish(session, "Commit cancelled, nothing written", 'WARNING')
            return
        if error is not None:
            finish(session, bc._error_text(error), 'ERROR', error=True)
            return
        if result.get("log"):
            from ..shared.export_helpers import log_text_to_blender
            log_text_to_blender("citydb_commit", result["log"])
        if result["conflicts"]:
            session.last_update["conflicts"] = {
                oid: {"why": why, "ids": [r.id for r in result["before"][oid]]}
                for oid, why in result["conflicts"].items()}
            finish(session, f"Nothing written: {len(result['conflicts'])} conflict(s) with the database "
                            "(listed in the Save tab)", 'ERROR', error=True)
            return
        if "after" not in result:
            finish(session, result["problem"], 'ERROR', error=True)
            return
        scene = bpy.data.scenes.get(scene_name) or bpy.context.scene
        roots = export_scope.top_level_feature_objects(scene)
        done = {NEW: 0, CHANGED: 0, DELETED: 0}
        stamps: Dict[str, Tuple[object, int]] = {}
        untrack: List[str] = []
        old_ids: List[int] = []
        new_rows = []
        for c in todo:
            rows = result["after"].get(c.objectid, [])
            was = [r.id for r in result["before"][c.objectid]]
            if c.kind == DELETED:
                if not rows:
                    untrack.append(c.objectid)
                    old_ids += was
                    done[DELETED] += 1
            elif result["written"] and rows:
                row = max(rows, key=lambda r: r.id)
                stamps[c.objectid] = (roots.get(c.objectid), row.id)
                old_ids += was
                new_rows.append(row)
                done[c.kind] += 1
        stamp(scene, session, stamps)
        track(scene, session.path, remove=untrack)
        remove_shells(scene, untrack)
        if session.rows:
            bc.replace_rows(session, old_ids, new_rows)
        session.last_update.update(done=dict(done), written=True)
        text = (f"Committed to {session.label}: {done[NEW]} added, {done[CHANGED]} updated, "
                f"{done[DELETED]} removed")
        if result.get("problem"):
            finish(session, f"{text} · {result['problem']}", 'ERROR', error=True)
        else:
            finish(session, text, 'INFO')

    job = background_job.Job(COMMIT_JOB, worker, on_done=on_done, label=f"Committing {changes.summary(todo)}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        if path:
            bc._remove_file(path)
        raise RuntimeError("Another background job is running")
    return session, driver


# --------------------------------------------------------------------------- operators
class CGML3_DBChangeItem(bpy.types.PropertyGroup):
    objectid: StringProperty()
    kind: StringProperty()
    label: StringProperty()
    include: BoolProperty(name="Commit", default=True, description="Write this change to the database")


_ICONS = {NEW: 'ADD', CHANGED: 'GREASEPENCIL', DELETED: 'TRASH', changes.SYNCED: 'CHECKMARK'}


class CGML3_OT_db_commit(bpy.types.Operator):
    """Write the pending changes of the scene to the 3DCityDB city model. Nothing reaches the database before"""
    bl_idname = "cgml3.db_commit"
    bl_label = "Commit to 3DCityDB"
    bl_options = {'REGISTER'}

    objectids: StringProperty(name="Object ids", default="", options={'SKIP_SAVE'},
                              description="Comma-separated gml:ids to commit (empty: every pending change)")
    only_selected: BoolProperty(name="Selected only", default=False, options={'SKIP_SAVE'},
                                description="Commit only the changes of the selected buildings")
    from_dialog: BoolProperty(default=False, options={'SKIP_SAVE', 'HIDDEN'})

    def _ids(self, context) -> Optional[List[str]]:
        if self.from_dialog:
            return [i.objectid for i in context.window_manager.cgml3_db_commit_items if i.include]
        if self.only_selected:
            return selected_ids(context)
        return export_scope.parse_ids(self.objectids) or None

    def invoke(self, context, event):
        found = scan(context)
        if found is None:
            self.report({'ERROR'}, "Connect and pick a city model first (Open tab)")
            return {'CANCELLED'}
        ids = self._ids(context)
        todo = [c for c in changes.pending(found[1]) if ids is None or c.objectid in ids]
        if not todo:
            self.report({'INFO'}, "Nothing to commit: the scene matches the database")
            return {'CANCELLED'}
        items = context.window_manager.cgml3_db_commit_items
        items.clear()
        for c in sorted(todo, key=lambda c: (changes.PENDING.index(c.kind), c.name or c.objectid)):
            item = items.add()
            item.objectid, item.kind = c.objectid, c.kind
            item.label = c.objectid if c.name in ("", c.objectid) else f"{c.name}  ({c.objectid})"
        self.from_dialog = True
        return context.window_manager.invoke_props_dialog(self, width=520, title="Commit to 3DCityDB",
                                                          confirm_text="Commit")

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"To: {update_citydb._where(context)}", icon='NETWORK_DRIVE')
        col = layout.column(align=True)
        kind = None
        for item in context.window_manager.cgml3_db_commit_items:
            if item.kind != kind:
                kind = item.kind
                col.separator()
                col.label(text={NEW: "Add to the database", CHANGED: "Replace in the database",
                                DELETED: "Remove from the database"}[kind], icon=_ICONS[kind])
            col.prop(item, "include", text=item.label)
        layout.label(text="Replaced and removed versions stay in the database as history (terminated).",
                     icon='INFO')

    def execute(self, context):
        ids = self._ids(context)
        if ids is not None and not ids:
            self.report({'WARNING'}, "Nothing chosen: nothing written")
            return {'CANCELLED'}
        try:
            session, driver = start_commit(context, ids)
        except Exception as exc:
            return bc._report_start_error(self, exc)
        if driver.finished:
            err = session.last_update.get("error")
            if err:
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            self.report({'INFO'}, session.message)
        return {'FINISHED'}


class CGML3_OT_db_keep(bpy.types.Operator):
    """Keep the features deleted in Blender in the database: they are no longer a pending delete"""
    bl_idname = "cgml3.db_keep"
    bl_label = "Keep in Database"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        found = scan(context)
        gone = [c.objectid for c in (found[1] if found else ()) if c.kind == DELETED]
        if not gone:
            self.report({'INFO'}, "No deleted features")
            return {'CANCELLED'}
        track(context.scene, found[0], remove=gone)
        remove_shells(context.scene, gone)
        self.report({'INFO'}, f"{len(gone)} feature(s) stay in the database (load them again in 3DCityDB Browse)")
        return {'FINISHED'}


class CGML3_OT_db_resolve(bpy.types.Operator):
    """Keep my version: the next commit replaces the database row of this feature with the scene's"""
    bl_idname = "cgml3.db_resolve"
    bl_label = "Keep My Version"
    bl_options = {'REGISTER', 'UNDO'}

    objectid: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        session = bc.active_session(context)
        conflicts = session.last_update.get("conflicts", {}) if session is not None else {}
        entry = conflicts.get(self.objectid)
        if entry is None:
            self.report({'ERROR'}, "No conflict known for this feature; commit again")
            return {'CANCELLED'}
        ids = entry["ids"]
        if len(ids) > 1:
            self.report({'ERROR'}, f"{len(ids)} active versions in the database: use Advanced Write (replace same IDs)")
            return {'CANCELLED'}
        root = export_scope.top_level_feature_objects(context.scene).get(self.objectid)
        if ids:  # the database row becomes the base; no fingerprint -> "changed"
            track(context.scene, session.path, add={self.objectid: ids[0]})
            if root is not None and FINGERPRINT_PROP in root:
                del root[FINGERPRINT_PROP]
        else:  # gone in the database: commit it as new, or forget the delete
            track(context.scene, session.path, remove=[self.objectid])
            if root is not None and root.get(SOURCE_KIND_PROP) == "citydb":
                del root[SOURCE_KIND_PROP]
        del conflicts[self.objectid]
        _redraw_views()
        return {'FINISHED'}


property_classes = (CGML3_DBChangeItem,)
operator_classes = (CGML3_OT_db_commit, CGML3_OT_db_keep, CGML3_OT_db_resolve)


def register():
    bpy.types.WindowManager.cgml3_db_commit_items = CollectionProperty(type=CGML3_DBChangeItem,
                                                                       options={'SKIP_SAVE'})


def unregister():
    try:
        del bpy.types.WindowManager.cgml3_db_commit_items
    except AttributeError:
        pass
