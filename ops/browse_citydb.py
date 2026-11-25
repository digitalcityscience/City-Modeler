# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/browse_citydb.py
"""DB Browse first (T09): boxes of the buildings in view, load only the selection.

1. ``cgml3.db_browse_view`` turns the ground area seen by the 3D view into CRS
   coordinates and asks the scene's 3DCityDB target (T02) for the top-level
   features whose ``feature.envelope`` intersects it - first the count, then
   one page of rows (``citydb.catalog``). Everything runs in a background job
   (``common.background_job``); the database is only read. The boxes (or real
   LoD0, or optionally a ground-surface footprint) go into ONE proxy mesh in
   the ``CityDB Index`` collection, the same proxy, overlay and picking as the
   file browse (``ops.browse_gml``). Asking again after panning adds only the
   features not shown yet.
2. Selection: ``cgml3.browse_pick`` (click / box) or ``cgml3.db_browse_select``.
3. ``cgml3.db_browse_load`` exports the selected feature ids with ONE
   ``citydb export citygml`` call in the background job and hands the file to
   the common importer (T01 origin kept). Roots get ``cgml3_source_kind=citydb``,
   profile, ``cgml3_db_feature_id``, ``cgml3_objectid`` and ``cgml3_loaded_lod``
   and live in the ``CityDB Loaded`` collection. ``cgml3.db_browse_find``
   loads by objectid or feature id.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Sequence, Set

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty

from ..citydb import catalog
from ..citydb import errors as db_errors
from ..citydb.catalog import CatalogRow
from ..citydb.connection import CityDBTarget
from ..common import background_job
from ..shared.browse.gml_index import FeatureIndexEntry
from ..shared.browse.proxy_geometry import build_proxy_arrays
from ..shared.browse.selection import DEFAULT_LOAD_LIMIT, format_levels, limit_message, lod_summary
from ..shared.browse.view_extent import area_km2, ground_extent, ground_level, regrounded_extent, to_crs
from ..shared.import_origin import center_origin
from ..shared.georef import CrsMismatch, crs_conflict, scene_crs_lock
from . import browse_gml
from .browse_gml import (
    SOURCE_KIND_PROP,
    SOURCE_PATH_PROP,
    BrowseSession,
    _apply_origin,
    _browse_import_filters,
    _ensure_proxy,
    _notify,
    _redraw_views,
    _remove_empty_collections,
    _remove_objects,
    existing_origin,
    write_proxy_mesh,
    write_proxy_states,
)

DB_INDEX_COLLECTION = "CityDB Index"
LOADED_COLLECTION = "CityDB Loaded"
SOURCE_PROFILE_PROP = "cgml3_source_profile"
DB_FEATURE_ID_PROP = "cgml3_db_feature_id"
OBJECTID_PROP = "cgml3_objectid"
LOADED_LOD_PROP = "cgml3_loaded_lod"
QUERY_JOB = "citydb-browse"
LOAD_JOB = "citydb-load"
FIND_JOB = "citydb-find"
EXPORT_TIMEOUT_S = 600

_PROGRESS_LINE = re.compile(r"(\d[\d,]*) features? (?:processed|exported)", re.IGNORECASE)


# --------------------------------------------------------------------------- preferences
def query_limit(context=None) -> int:
    return catalog.clamp_limit(browse_gml.pref("db_browse_limit", catalog.DEFAULT_LIMIT, context))


def footprint_preview(context=None) -> bool:
    return bool(browse_gml.pref("db_preview_footprint", False, context))


def load_limit(context=None) -> int:
    return int(browse_gml.pref("browse_load_limit", DEFAULT_LOAD_LIMIT, context))


def _tool_prefs(context=None):
    """citydb-tool settings copied on the main thread (worker threads never read bpy)."""
    from .. import _get_citydb_prefs
    p = _get_citydb_prefs(context)
    return SimpleNamespace(
        citydb_exe=str(getattr(p, "citydb_exe", "") or ""),
        use_docker=bool(getattr(p, "use_docker", False)),
        docker_image=str(getattr(p, "docker_image", "") or ""),
    )


# --------------------------------------------------------------------------- temp files
_temp_dir: Optional[str] = None
_temp_counter = 0


def _temp_export_path() -> str:
    """``<session temp>/sel_<n>.gml``; the folder is removed when Blender quits."""
    global _temp_dir, _temp_counter
    if _temp_dir is None or not os.path.isdir(_temp_dir):
        _temp_dir = tempfile.mkdtemp(prefix="cgml3_citydb_")
        atexit.register(shutil.rmtree, _temp_dir, True)
    _temp_counter += 1
    return os.path.join(_temp_dir, f"sel_{_temp_counter}.gml")


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
    # citydb-tool may write textures next to the file
    folder = os.path.splitext(path)[0] + "_appearance"
    shutil.rmtree(folder, ignore_errors=True)


# --------------------------------------------------------------------------- target
def target_key(target: CityDBTarget) -> str:
    s = target.server
    return f"{browse_gml.DB_KEY_PREFIX}{s.user}@{s.host}:{s.port}/{target.database}/{target.schema}"


def scene_target(context):
    """``(target, profile name, label, srid)`` of the scene's 3DCityDB target, or raises CityDBError."""
    from . import citydb_connection as dbc
    sc = context.scene.cgml3
    target, _password, _info = dbc.resolve_target(
        sc.db_host, sc.db_port, sc.db_name, sc.db_user, sc.db_schema, sc.db_sslmode, verify=False)
    return target, sc.db_profile, (sc.db_target_label or target.schema), int(sc.db_target_srid or 0)


def _password(target: CityDBTarget):
    from ..citydb import credentials
    password, _source = credentials.lookup(target.server.key)
    return password


# --------------------------------------------------------------------------- session
class DBBrowseSession(BrowseSession):
    """State of one browsed 3DCityDB schema (main thread only)."""

    kind = "citydb"
    collection_name = DB_INDEX_COLLECTION

    def __init__(self, target: CityDBTarget, profile: str = "", label: str = "", srid: int = 0):
        super().__init__(target_key(target))
        self.path = target_key(target)  # not a file path
        self.index.path = self.path
        self.target = target
        self.profile = profile
        self.label = label or target.schema
        self.srid = int(srid or 0)
        self.complete = True
        self.rows: List[CatalogRow] = []
        self.id_to_index: Dict[str, int] = {}
        self.fid_to_index: Dict[int, int] = {}
        self.last_query: Dict[str, object] = {}
        self.id_matches: List[CatalogRow] = []
        self.find_text = ""
        self.message = ""
        # T10 write back: last dry run / update of a loaded feature (ops.update_citydb)
        self.update_plan: Dict[str, object] = {}
        self.last_update: Dict[str, object] = {}

    @property
    def stem(self) -> str:
        return self.label

    @property
    def proxy_title(self) -> str:
        return f"CityDB Index: {self.label}"

    def preview(self) -> str:
        return catalog.preview_label(self.rows)

    def add_rows(self, rows: Sequence[CatalogRow], entries: Sequence[FeatureIndexEntry]) -> None:
        n = len(self.rows)
        for k, r in enumerate(rows):
            self.fid_to_index[r.id] = n + k
        self.rows.extend(rows)
        self.index.entries.extend(entries)

    def selected_rows(self) -> List[CatalogRow]:
        return [self.rows[i] for i in sorted(self.selected) if i < len(self.rows)]

    def versions_of(self, row: CatalogRow) -> List[CatalogRow]:
        return [r for r in self.rows if r.objectid == row.objectid] if row.objectid else [row]


def row_entry(row: CatalogRow) -> FeatureIndexEntry:
    return FeatureIndexEntry(gml_id=row.label, feature_type=row.classname, name=f"id {row.id}",
                             bbox=row.bbox, lods=row.lods, lod0=list(row.rings))


def sessions() -> List[DBBrowseSession]:
    return [s for s in browse_gml._sessions.values() if s.kind == "citydb"]


def get_session(target: CityDBTarget) -> Optional[DBBrowseSession]:
    s = browse_gml._sessions.get(target_key(target))
    return s if s is not None and s.kind == "citydb" else None


def active_session(context=None) -> Optional[DBBrowseSession]:
    s = browse_gml.active_session(context, kind="citydb")
    if s is not None:
        return s
    try:
        target, *_ = scene_target(context or bpy.context)
    except Exception:
        return None
    return get_session(target)


def _session_for_scene(context) -> DBBrowseSession:
    target, profile, label, srid = scene_target(context)
    s = get_session(target)
    if s is None:
        s = DBBrowseSession(target, profile, label, srid)
        for o in browse_gml.proxy_objects("citydb"):
            if o.get(browse_gml.PROXY_PATH_PROP) == s.path and o.get(browse_gml.PROXY_ORIGIN_PROP):
                s.proxy_name = o.name
                s.fixed_origin = tuple(float(v) for v in o[browse_gml.PROXY_ORIGIN_PROP])
        browse_gml._sessions[s.path] = s
    browse_gml._active_key = s.path
    return s


# --------------------------------------------------------------------------- view extent
def view_rays(context):
    """Rays through the corners and centre of the 3D view (local coordinates), or None."""
    from bpy_extras import view3d_utils
    area = context.area if context.area is not None and context.area.type == 'VIEW_3D' else None
    if area is None:
        screen = context.window.screen if context.window else None
        area = next((a for a in screen.areas if a.type == 'VIEW_3D'), None) if screen else None
    if area is None:
        return None
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None or region.data is None:
        return None
    rv3d = region.data
    w, h = region.width, region.height
    rays = []
    for x, y in ((0, 0), (w, 0), (w, h), (0, h), (w / 2, h / 2)):
        o = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
        d = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
        rays.append((tuple(o), tuple(d)))
    return rays


# --------------------------------------------------------------------------- query job
def start_query(context, *, local_extent=None, crs_extent=None, classname: str = "",
                after_id: int = 0, manual: bool = False, rays=None):
    """Count and page the features in an extent; returns ``(session, driver)`` or raises.

    ``local_extent`` is in Blender coordinates (the view), ``crs_extent`` in
    the DB CRS (scripts). With the view ``rays`` (first query, no boxes to
    take the ground from) the worker re-cuts the view at the median base of
    the features in the area. Raises ``RuntimeError`` / ``CityDBError`` /
    ``InvalidId`` before anything starts.
    """
    from ..citydb import client

    if background_job.is_running() or import_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    if not client.available():
        raise db_errors.CityDBError(db_errors.DRIVER_MISSING, db_errors.user_message(db_errors.DRIVER_MISSING))
    classname = catalog.validate_classname(classname)
    if local_extent is None and crs_extent is None:
        raise RuntimeError("No 3D view to take the area from")
    session = _session_for_scene(context)
    target = session.target
    password = _password(target)
    # origin: the session's, else one the World already has for this CRS
    origin_hint = session.origin or session.fixed_origin
    if origin_hint is None and session.srid:
        origin_hint = existing_origin(context, f"EPSG:{session.srid}")
        if origin_hint is not None:
            session.fixed_origin = origin_hint
    known = frozenset(session.fid_to_index)
    n_entries = len(session.rows)
    v0, l0 = session.arrays.vertex_count, session.arrays.loop_count
    limit = query_limit(context)
    use_footprint = footprint_preview(context)
    hint_srid = session.srid
    lock = None if browse_gml._local_import(context) else scene_crs_lock()

    def worker(job):
        t0 = time.perf_counter()
        info, used = client.verify_target(target, password)
        srid = int(info.srid or 0)
        if not srid:
            raise db_errors.CityDBError(db_errors.OTHER, "The city model has no CRS (database_srs)")
        mismatch = crs_conflict(f"EPSG:{srid}", lock)
        if mismatch:  # the origin message would re-base the World (B31)
            raise CrsMismatch(mismatch)
        conn = client.connect(target.server, target.database, used)
        stats: Dict[str, float] = {}
        with conn:
            origin = tuple(origin_hint) if (origin_hint is not None and srid == hint_srid) else None
            if origin is None:
                ext = catalog.schema_extent(conn, target.schema)
                origin = center_origin(ext[:3], ext[3:]) if ext else (0.0, 0.0, 0.0)
            job.post(("origin", origin, srid))
            extent = tuple(crs_extent) if crs_extent is not None else to_crs(local_extent, origin)
            if rays is not None and crs_extent is None:
                # first query: no boxes yet, so take the view plane from the DB
                t = time.perf_counter()
                base_z = catalog.base_z_in_extent(conn, target.schema, extent, srid, classname=classname)
                extent = to_crs(regrounded_extent(rays, local_extent, origin, base_z), origin)
                stats["base_z_s"] = time.perf_counter() - t
            area = area_km2(extent)
            job.set_progress(0, 0, f"{area:.2f} km² · counting")
            t = time.perf_counter()
            count, estimated = catalog.count_in_extent(conn, target.schema, extent, srid, classname=classname)
            stats["count_s"] = time.perf_counter() - t
            job.post(("count", extent, area, count, estimated))
            if job.cancelled():
                raise background_job.JobCancelled()
            job.set_progress(0, 0, f"{area:.2f} km² · {'~' if estimated else ''}{count:,} buildings · loading boxes")
            t = time.perf_counter()
            rows = catalog.query_extent(conn, target.schema, extent, srid, limit=limit,
                                        after_id=after_id, classname=classname)
            stats["query_s"] = time.perf_counter() - t
            more = len(rows) >= limit
            last_id = rows[-1].id if rows else after_id
            new = [r for r in rows if r.id not in known]
            lod0_ids = [r.id for r in new if r.has_lod0_geometry]
            if lod0_ids:
                t = time.perf_counter()
                rings = catalog.lod0_rings(conn, target.schema, lod0_ids)
                for r in new:
                    r.rings = rings.get(r.id, [])
                stats["lod0_s"] = time.perf_counter() - t
            if use_footprint:
                todo = [r.id for r in new if not r.rings]
                rings, secs, complete = catalog.ground_footprints(conn, target.schema, todo)
                for r in new:
                    if r.id in rings:
                        r.rings, r.footprint = rings[r.id], True
                stats["footprint_s"] = secs
                stats["footprint_complete"] = complete
        entries = [row_entry(r) for r in new]
        arrays = build_proxy_arrays(entries, origin, first_feature=n_entries, vertex_offset=v0, loop_offset=l0)
        stats["seconds"] = time.perf_counter() - t0
        job.post(("rows", new, entries, arrays))
        return {"extent": extent, "area": area, "count": count, "estimated": estimated, "rows": len(rows),
                "new": len(new), "more": more, "last_id": last_id, "after_id": after_id,
                "classname": classname, "limit": limit, "stats": stats}

    def on_message(msg):
        kind = msg[0]
        if kind == "origin":
            session.srid = int(msg[2])
            _apply_origin(bpy.context, session, msg[1], f"EPSG:{msg[2]}")
            _ensure_proxy(bpy.context, session)
            browse_gml._start_pick_mode()
        elif kind == "count":
            _extent, area, count, estimated = msg[1:]
            session.last_query = {"area": area, "count": count, "estimated": estimated, "pending": True}
            session.message = f"{area:.2f} km² · {'~' if estimated else ''}{count:,} buildings in view"
            _redraw_views()
        elif kind == "rows":
            rows, entries, arrays = msg[1], msg[2], msg[3]
            session.add_rows(rows, entries)
            session.arrays.extend(arrays)
            session.dirty = True

    def on_tick():
        if session.dirty:
            write_proxy_mesh(session)
            _redraw_views()
            return True
        return False

    def on_done(result, error, cancelled):
        if cancelled:
            session.message = "Query cancelled"
            _notify("Query cancelled", 'WARNING')
            return
        if error is not None:
            session.message = _error_text(error)
            _notify(session.message, 'ERROR')
            return
        if session.dirty or session.proxy() is None:
            _ensure_proxy(bpy.context, session)
            write_proxy_mesh(session)
        _refresh_loaded(session)
        write_proxy_states(session)
        session.last_query = dict(result)
        session.last_query["shown"] = len(session.rows)
        session.message = query_message(result, session)
        _redraw_views()
        level = 'WARNING' if result["count"] > result["rows"] and result["more"] else 'INFO'
        _notify(session.message, level)

    job = background_job.Job(QUERY_JOB, worker, on_message=on_message, on_done=on_done, on_tick=on_tick,
                             label=f"3DCityDB {session.label}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        raise RuntimeError("Another background job is running")
    return session, driver


def query_message(result, session: DBBrowseSession) -> str:
    approx = "~" if result["estimated"] else ""
    text = (f"{result['area']:.2f} km² · {approx}{result['count']:,} buildings in view · "
            f"{result['new']:,} new boxes · preview: {session.preview()}")
    if result["more"]:
        text += f" · more than {result['limit']:,}: {catalog.NARROW_MESSAGE} (or Next page)"
    if result["stats"].get("footprint_complete") is False:
        text += " · footprint query stopped (timeout), boxes shown instead"
    return text


def _error_text(error) -> str:
    if isinstance(error, CrsMismatch):
        return str(error)
    if isinstance(error, db_errors.CityDBError):
        from .citydb_connection import describe_error
        return describe_error(error)
    if isinstance(error, catalog.InvalidId):
        return str(error)
    from ..citydb.credentials import redact
    return redact(f"3DCityDB query failed: {error}")


# --------------------------------------------------------------------------- loaded objects
def _feature_objects(session: DBBrowseSession, fids: Optional[Set[int]] = None, objectids: Optional[Set[str]] = None):
    out = []
    for o in bpy.data.objects:
        try:
            if o.get(SOURCE_KIND_PROP) != "citydb" or o.get(SOURCE_PATH_PROP) != session.path:
                continue
            fid = int(o.get(DB_FEATURE_ID_PROP))
        except Exception:
            continue
        if fids is None and objectids is None:
            out.append(o)
        elif (fids is not None and fid in fids) or (objectids is not None and o.get(OBJECTID_PROP) in objectids):
            out.append(o)
    return out


def _refresh_loaded(session: DBBrowseSession) -> None:
    fids = {int(o[DB_FEATURE_ID_PROP]) for o in _feature_objects(session)}
    session.loaded = {session.fid_to_index[f] for f in fids if f in session.fid_to_index}


def unload(session: DBBrowseSession, *, fids: Optional[Set[int]] = None, objectids: Optional[Set[str]] = None) -> int:
    roots = _feature_objects(session, fids, objectids)
    colls = {c.name for r in roots for c in r.users_collection}
    # B84: unloading is not deleting: the feature leaves the scene and the pending changes
    from . import commit_citydb
    others = commit_citydb.forget(roots)
    removed = _remove_objects(roots)
    _remove_empty_collections(colls)
    _remove_empty_loaded_children()
    _refresh_loaded(session)
    write_proxy_states(session)
    commit_citydb.refresh_boxes(others - {session.path})
    _redraw_views()
    return removed


def _loaded_collection(context):
    coll = bpy.data.collections.get(LOADED_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(LOADED_COLLECTION)
    if coll.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll


def _remove_empty_loaded_children() -> None:
    coll = bpy.data.collections.get(LOADED_COLLECTION)
    if coll is None:
        return
    for child in list(coll.children):
        if not child.all_objects and not child.children:
            bpy.data.collections.remove(child)


_NAME_SUFFIX = re.compile(r"\.\d{3}$")


def _merge_into(dst, src) -> None:
    """Move objects and child collections of ``src`` into ``dst``; same-named children merge."""
    for o in list(src.objects):
        if o.name not in dst.objects:
            dst.objects.link(o)
        src.objects.unlink(o)
    for child in list(src.children):
        base = _NAME_SUFFIX.sub("", child.name)
        same = next((c for c in dst.children if _NAME_SUFFIX.sub("", c.name) == base), None)
        src.children.unlink(child)
        if same is not None:
            _merge_into(same, child)
            bpy.data.collections.remove(child)
        else:
            dst.children.link(child)


def _merge_new_collections(context, colls_before: Set[int], name: str) -> None:
    """One collection per load: the per-building imports each made their own."""
    new = [c for c in context.scene.collection.children if c.as_pointer() not in colls_before
           and c.name != LOADED_COLLECTION]
    if not new:
        return
    first = new[0]
    for other in new[1:]:
        _merge_into(first, other)
        context.scene.collection.children.unlink(other)
        bpy.data.collections.remove(other)
    first.name = name


def _move_to_loaded(context, new_objects, colls_before: Set[int]) -> None:
    """Collections the import created go under ``CityDB Loaded``; loose objects are linked there."""
    target = _loaded_collection(context)
    scene_coll = context.scene.collection
    for coll in list(scene_coll.children):
        if coll.as_pointer() in colls_before or coll == target:
            continue
        scene_coll.children.unlink(coll)
        if coll.name not in target.children:
            target.children.link(coll)
    for o in new_objects:
        if scene_coll in o.users_collection or not o.users_collection:
            if o.name not in target.objects:
                target.objects.link(o)
            if o.name in scene_coll.objects:
                scene_coll.objects.unlink(o)


# --------------------------------------------------------------------------- load job
def start_load(context, session: DBBrowseSession, fids: Iterable[int], mode: str = 'LOD2', *,
               manual: bool = False):
    """Export ``fids`` with one citydb-tool call in the background and import the file.

    Returns the driver; raises ``RuntimeError`` / ``InvalidId`` before anything starts.
    """
    if background_job.is_running() or import_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    fids = catalog.validate_feature_ids(fids)
    rows = {f: session.rows[session.fid_to_index[f]] for f in fids if f in session.fid_to_index}
    load_ids, missing, skipped = catalog.plan_groups(rows, mode)
    if not load_ids:
        raise RuntimeError(f"None of the {len(fids)} selected features has {'LoD2' if mode == 'LOD2' else 'geometry'}")
    options = catalog.export_options(load_ids, mode)
    target = session.target
    password = _password(target)
    prefs = _tool_prefs(context)
    out = _temp_export_path()
    sslmode = target.server.sslmode
    loading = {session.fid_to_index[f] for f in load_ids}
    session.failed -= loading
    t_start = time.perf_counter()
    print(f"[CityDB Browse] export {len(load_ids)} feature(s): {catalog.sql_filter(load_ids)}"
          f" (as --filter \"{options['cql2_filter']}\"), --lod {','.join(options['lod'])} --lod-mode maximum")

    def worker(job):
        from .citydb_cli import CityDBTool

        def on_output(line):
            m = _PROGRESS_LINE.search(line)
            job.set_progress(0, 0, f"citydb-tool: {m.group(0) if m else line[-60:]}")

        # .pyc are written next to the export, then the import loads them
        precompile = start_precompile()
        tool = CityDBTool(prefs)
        t = time.perf_counter()
        ok, log = tool.export_db_to_citygml(
            out, host=target.server.host, port=str(target.server.port), db=target.database,
            user=target.server.user, password=password, db_schema=target.schema, sslmode=sslmode,
            on_output=on_output, cancelled=job.cancelled, **options)
        export_s = time.perf_counter() - t
        wait_precompile(precompile, job.cancelled)
        if job.cancelled():
            raise background_job.JobCancelled()
        _preload_importer()
        members = catalog.count_top_level_members(out) if ok and os.path.isfile(out) else 0
        index = None
        if members:
            # byte ranges per building: the main thread imports one building per step
            from ..shared.browse.gml_index import index_file
            index = index_file(out, cancel=job.cancelled)
        return {"ok": ok, "log": log, "members": members, "export_s": export_s, "index": index}

    def on_done(result, error, cancelled):
        if cancelled:
            _remove_file(out)
            session.message = "Load cancelled"
            _notify(session.message, 'WARNING')
            return
        if error is not None or not result or not result["ok"]:
            _remove_file(out)
            session.failed |= loading
            write_proxy_states(session)
            log = (result or {}).get("log", "") if error is None else ""
            if log:
                from ..shared.export_helpers import log_text_to_blender
                log_text_to_blender("citydb_export", log)
            session.message = _error_text(error) if error is not None else "citydb export citygml failed (see text log)"
            session.last_load = {"error": session.message}
            _notify(session.message, 'ERROR')
            return
        if result["members"] == 0:
            _remove_file(out)
            session.message = "Feature not found"
            session.last_load = {"error": session.message, "export_s": result["export_s"], "members": 0}
            _notify(session.message, 'WARNING')
            return
        ImportRun(bpy.context, session, out, rows, load_ids, missing, skipped, mode, result, t_start).start()

    job = background_job.Job(LOAD_JOB, worker, on_done=on_done, label=f"Loading {len(load_ids)} from {session.label}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        raise RuntimeError("Another background job is running")
    return driver


_import_run: Optional["ImportRun"] = None
IMPORT_STEP_INTERVAL_S = 0.02
# CityGML 3.0 reader modules (citydb-tool exports 3.0). Their first import pulls
# lxml/numpy and took ~250 ms; done in the worker it never blocks the UI.
# [1] is the heavy module whose presence means "already loaded" (existing_origin loads the small importer ones)
_IMPORTER_MODULES = ("common.unified_builder", "common.builder_support.v3.materials",
                     "common.builder_support.v3.importer", "common.builder_support.v2.materials",
                     "common.builder_support.semantic_explode")
# Packages those modules come from; their .pyc are written by a separate process.
_IMPORTER_DIRS = ("common", "shared")
PRECOMPILE_TIMEOUT_S = 60.0


def start_precompile() -> Optional[subprocess.Popen]:
    """Write the importer's missing .pyc files in a separate Python process.

    One ``compile()`` holds the GIL for a whole module (the old ``importer.py``
    took ~53 ms), so compiling in a worker thread still stalls the UI that long
    on a cold start (no ``__pycache__`` after install or update). The bundled
    interpreter writes the same .pyc the import then loads; up-to-date ones are
    skipped. Returns None when the modules are loaded or no interpreter is found.
    """
    base = __package__.rsplit(".", 1)[0]
    if f"{base}.{_IMPORTER_MODULES[1]}" in sys.modules or sys.dont_write_bytecode:
        return None
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if all(_pyc_current(os.path.join(root, *m.split(".")) + ".py") for m in _IMPORTER_MODULES):
        return None
    exe = sys.executable or ""
    if not os.path.basename(exe).lower().startswith("python") or not os.path.isfile(exe):
        return None
    cmd = [exe, "-I"]
    if sys.pycache_prefix:
        cmd += ["-X", f"pycache_prefix={sys.pycache_prefix}"]
    cmd += ["-m", "compileall", "-q", "--invalidation-mode", "timestamp", "-o", str(sys.flags.optimize),
            *(os.path.join(root, d) for d in _IMPORTER_DIRS)]
    try:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        print(f"[CityDB Browse] precompile not started: {exc}")
        return None


def _pyc_current(source: str) -> bool:
    import importlib.util
    try:
        return os.path.getmtime(importlib.util.cache_from_source(source)) >= os.path.getmtime(source)
    except (OSError, NotImplementedError, ValueError):
        return False


def wait_precompile(proc: Optional[subprocess.Popen], cancelled=lambda: False) -> None:
    """Wait (in the worker, GIL released) for ``start_precompile``; kill it on cancel or timeout."""
    if proc is None:
        return
    deadline = time.perf_counter() + PRECOMPILE_TIMEOUT_S
    while proc.poll() is None:
        if cancelled() or time.perf_counter() > deadline:
            proc.kill()
            proc.wait()
            return
        time.sleep(0.05)


def _preload_importer() -> None:
    import importlib
    base = __package__.rsplit(".", 1)[0]
    for name in _IMPORTER_MODULES:
        try:
            importlib.import_module(f"{base}.{name}")
        except Exception as exc:  # the main thread imports it again and reports
            print(f"[CityDB Browse] preload {name}: {exc}")


def import_running() -> bool:
    return _import_run is not None and not _import_run.done


class ImportRun:
    """Imports the citydb-tool export one building per main-thread step.

    Each step cuts one member out of the export (T08 fragment, byte range from
    the index the worker built) and hands it to the common importer, so the
    UI gets control back between buildings. In background mode all steps run
    at once.
    """

    def __init__(self, context, session: DBBrowseSession, path: str, rows: Dict[int, CatalogRow],
                 load_ids: List[int], missing: List[int], skipped: List[int], mode: str, result, t_start: float):
        self.session, self.path, self.rows = session, path, rows
        self.load_ids, self.missing, self.skipped, self.mode = load_ids, missing, skipped, mode
        self.result, self.t_start = result, t_start
        self.index = result.get("index")
        self.queue = list(range(len(self.index.entries))) if self.index is not None else []
        self.done = False
        self.steps: List[float] = []
        self.import_s = 0.0
        self.scene_name = context.scene.name
        self.untranslated: Set[str] = set()

    def _context(self):
        return bpy.context

    def start(self) -> None:
        global _import_run
        context = self._context()
        # the same feature (or another version of the same building) is replaced
        objectids = {self.rows[f].objectid for f in self.load_ids if self.rows[f].objectid}
        unload(self.session, fids=set(self.load_ids), objectids=objectids)
        from ..common.import_landing import ImportSnapshot
        self.colls_before = {c.as_pointer() for c in context.scene.collection.children}
        self.snapshot = ImportSnapshot()
        loading = {self.session.fid_to_index[f] for f in self.load_ids}
        self.session.failed -= loading
        _import_run = self
        if bpy.app.background:
            while self.step():
                pass
            self.finish()
            return
        bpy.app.timers.register(self._timer, first_interval=0.0)

    def _timer(self):
        try:
            more = self.step()
        except Exception:
            more = False
            import traceback
            traceback.print_exc()
        if more:
            return IMPORT_STEP_INTERVAL_S
        self.finish()
        return None

    def step(self) -> bool:
        """Import one building; False when nothing is left."""
        if not self.queue:
            return False
        from ..common import import_citygml_auto
        from ..shared.browse.fragment import build_fragment

        fi = self.queue.pop(0)
        t = time.perf_counter()
        frag = build_fragment(self.index, [fi], origin=self.session.origin)
        self.untranslated |= set(frag.untranslated_namespaces or ())
        tmp = f"{os.path.splitext(self.path)[0]}_{fi}.gml"
        try:
            with open(tmp, "wb") as fh:
                fh.write(frag.data)
            context = self._context()
            with _browse_import_filters(context.scene, 2 if self.mode == 'LOD2' else None):
                import_citygml_auto(tmp, context, import_appearance=True, data=frag.data)
        except Exception as exc:
            print(f"[CityDB Browse] import of {self.index.entries[fi].gml_id} failed: {exc}")
        finally:
            _remove_file(tmp)
        dt = time.perf_counter() - t
        self.steps.append(dt)
        self.import_s += dt
        _redraw_views()
        return bool(self.queue)

    def finish(self) -> None:
        from ..common.import_landing import finish_import

        context = self._context()
        session, rows = self.session, self.rows
        t = time.perf_counter()
        new_objects = self.snapshot.new_objects()
        _merge_new_collections(context, self.colls_before,
                               "CityDB " + os.path.splitext(os.path.basename(self.path))[0])
        _move_to_loaded(context, new_objects, self.colls_before)
        by_objectid: Dict[str, int] = {rows[f].objectid: f for f in self.load_ids}
        tagged: Set[int] = set()
        for o in new_objects:
            p, root = o, None
            while p is not None:
                if p.get("gml_id") in by_objectid:
                    root = p
                p = p.parent
            if root is None or root.get(DB_FEATURE_ID_PROP) is not None:
                continue
            fid = by_objectid[root["gml_id"]]
            root[SOURCE_KIND_PROP] = "citydb"
            root[SOURCE_PATH_PROP] = session.path
            root[SOURCE_PROFILE_PROP] = session.profile
            root[DB_FEATURE_ID_PROP] = fid if fid < 2 ** 31 else str(fid)
            root[OBJECTID_PROP] = rows[fid].objectid
            root[LOADED_LOD_PROP] = catalog.loaded_level(rows[fid], self.mode)
            tagged.add(fid)
        not_loaded = [f for f in self.load_ids if f not in tagged]
        session.failed = (session.failed - {session.fid_to_index[f] for f in tagged}) | {
            session.fid_to_index[f] for f in not_loaded}
        warnings: List[str] = []
        if not_loaded:
            warnings.append(f"not imported: {len(not_loaded)}")
        if self.missing:
            warnings.append(f"no {'LoD2' if self.mode == 'LOD2' else 'geometry'}: {len(self.missing)}")
        if self.skipped:
            warnings.append(f"other versions of the same building left out: {len(self.skipped)}")
        if self.untranslated:
            warnings.append("namespaces without mapping: " + ", ".join(sorted(self.untranslated)))
        _refresh_loaded(session)
        write_proxy_states(session)
        level, message, _summary = finish_import(context, self.snapshot, frame=False, warnings=warnings)
        # B84: what was loaded is the base of the pending changes (after the landing moved it)
        from . import commit_citydb
        loaded_roots = {o[OBJECTID_PROP]: (o, int(o[DB_FEATURE_ID_PROP])) for o in new_objects
                        if o.get(OBJECTID_PROP) in by_objectid and by_objectid[o[OBJECTID_PROP]] in tagged}
        commit_citydb.stamp(context.scene, session, loaded_roots)
        self.steps.append(time.perf_counter() - t)
        _remove_file(self.path)
        session.last_load = {
            "features": len(tagged), "requested": len(self.load_ids), "members": self.result["members"],
            "export_s": self.result["export_s"], "import_s": self.import_s, "objects": len(new_objects),
            "max_step_s": max(self.steps), "steps": len(self.steps), "warnings": warnings, "message": message,
            "mode": self.mode, "ids": list(self.load_ids), "skipped": list(self.skipped),
            "missing": list(self.missing), "seconds": time.perf_counter() - self.t_start,
        }
        print(f"[CityDB Browse] {self.result['members']} feature(s) exported in {self.result['export_s']:.2f} s, "
              f"imported in {self.import_s:.2f} s ({len(self.steps)} steps, longest {max(self.steps) * 1000:.0f} ms)")
        self.done = True
        session.message = f"DB: {message}"
        _redraw_views()
        _notify(session.message, level)


# --------------------------------------------------------------------------- find job
def start_find(context, text: str, mode: str = 'HIGHEST', *, manual: bool = False):
    """Look up ``text`` (objectid or feature id); one match is loaded, several are listed."""
    from ..citydb import client

    if background_job.is_running() or import_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    catalog.parse_id_token(text)  # raises InvalidId before any connection
    session = _session_for_scene(context)
    session.find_text = text
    session.id_matches = []
    target = session.target
    password = _password(target)
    origin_hint = session.origin or session.fixed_origin
    if origin_hint is None and session.srid:
        origin_hint = existing_origin(context, f"EPSG:{session.srid}")
        if origin_hint is not None:
            session.fixed_origin = origin_hint

    def worker(job):
        info, used = client.verify_target(target, password)
        conn = client.connect(target.server, target.database, used)
        with conn:
            rows = catalog.find_by_id(conn, target.schema, text)
            ids = [r.id for r in rows if r.has_lod0_geometry]
            if ids:
                rings = catalog.lod0_rings(conn, target.schema, ids)
                for r in rows:
                    r.rings = rings.get(r.id, [])
        return {"rows": rows, "srid": int(info.srid or 0)}

    def on_done(result, error, cancelled):
        if cancelled:
            return
        if error is not None:
            session.message = _error_text(error)
            _notify(session.message, 'ERROR')
            return
        rows = result["rows"]
        if not rows:
            session.message = f"Feature not found: {text}"
            _notify(session.message, 'WARNING')
            return
        session.srid = session.srid or result["srid"]
        if session.origin is None and not browse_gml._local_import(bpy.context):
            mismatch = crs_conflict(f"EPSG:{session.srid}", scene_crs_lock())
            if mismatch:
                session.message = mismatch
                _notify(mismatch, 'ERROR')
                return
        if session.origin is None:
            origin = origin_hint
            if origin is None:
                boxes = [r.bbox for r in rows if r.bbox]
                origin = center_origin(boxes[0][:3], boxes[0][3:]) if boxes else (0.0, 0.0, 0.0)
            _apply_origin(bpy.context, session, origin, f"EPSG:{session.srid}")
        add_rows(session, rows)
        if len(rows) > 1:
            session.id_matches = rows
            session.message = f"{text}: {catalog.versions_label(rows)} - pick one to load"
            set_selection_fids(session, [r.id for r in rows])
            _notify(session.message, 'INFO')
            return
        set_selection_fids(session, [rows[0].id])
        _defer(lambda: _start_load_safe(session, [rows[0].id], mode))

    job = background_job.Job(FIND_JOB, worker, on_done=on_done, label=f"Finding {text}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        raise RuntimeError("Another background job is running")
    return session, driver


def add_rows(session: DBBrowseSession, rows: Sequence[CatalogRow]) -> List[int]:
    """Add rows not shown yet to the proxy (main thread, small lists); returns their feature indices."""
    new = [r for r in rows if r.id not in session.fid_to_index]
    if new:
        entries = [row_entry(r) for r in new]
        arrays = build_proxy_arrays(entries, session.origin, first_feature=len(session.rows),
                                    vertex_offset=session.arrays.vertex_count,
                                    loop_offset=session.arrays.loop_count)
        session.add_rows(new, entries)
        session.arrays.extend(arrays)
        _ensure_proxy(bpy.context, session)
        write_proxy_mesh(session)
        _refresh_loaded(session)
        write_proxy_states(session)
    return [session.fid_to_index[r.id] for r in rows]


def replace_rows(session: DBBrowseSession, remove_ids: Iterable[int], rows: Sequence[CatalogRow]) -> None:
    """Drop the boxes of ``remove_ids`` and show ``rows`` instead (T10: after a write back).

    Feature indices shift, so the proxy is rebuilt from the remaining rows and
    the selection, loaded and failed states are carried over by feature id.
    """
    remove = set(int(i) for i in remove_ids)
    by_index = lambda idx: {session.rows[i].id for i in idx if i < len(session.rows)}  # noqa: E731
    selected, failed = by_index(session.selected), by_index(session.failed)
    keep = [r for r in session.rows if r.id not in remove]
    known = {r.id for r in keep}
    keep += [r for r in rows if r.id not in known]
    entries = [row_entry(r) for r in keep]
    session.rows, session.index.entries, session.fid_to_index = [], [], {}
    origin = session.origin or session.fixed_origin or (0.0, 0.0, 0.0)
    session.arrays = build_proxy_arrays(entries, origin, first_feature=0, vertex_offset=0, loop_offset=0)
    session.add_rows(keep, entries)
    session.selected = {session.fid_to_index[f] for f in selected if f in session.fid_to_index}
    session.failed = {session.fid_to_index[f] for f in failed if f in session.fid_to_index}
    _ensure_proxy(bpy.context, session)
    write_proxy_mesh(session)
    _refresh_loaded(session)
    write_proxy_states(session)
    session.overlay_dirty = True
    _redraw_views()


def _defer(fn) -> None:
    """Run ``fn`` after the current job finished (synchronously in background mode)."""
    if bpy.app.background:
        fn()
        return

    def _run():
        fn()
        return None
    bpy.app.timers.register(_run, first_interval=0.01)


def _start_load_safe(session, fids, mode):
    try:
        start_load(bpy.context, session, fids, mode)
    except Exception as exc:
        session.message = str(exc)
        _notify(session.message, 'ERROR')


def set_selection_fids(session: DBBrowseSession, fids: Iterable[int], action: str = 'SET') -> int:
    return browse_gml.set_selection(session, [session.fid_to_index[f] for f in fids if f in session.fid_to_index],
                                    action)


def close(session: DBBrowseSession) -> None:
    browse_gml.close_session(session, remove_proxy=True)


def selection_summary(session: DBBrowseSession):
    """Panel text: ``(count, common LoDs, preview label, [(row, versions label)])``."""
    rows = session.selected_rows()
    ents = [session.entries[i] for i in sorted(session.selected) if i < len(session.entries)]
    common, _union = lod_summary(ents)
    lines = []
    for r in rows[:3]:
        versions = session.versions_of(r)
        lines.append((r, catalog.versions_label(versions)))
    return len(rows), format_levels(common), catalog.preview_label(rows) if rows else session.preview(), lines


# --------------------------------------------------------------------------- operators
def _report_start_error(op, exc) -> set:
    op.report({'ERROR'}, _error_text(exc) if not isinstance(exc, RuntimeError) else str(exc))
    return {'CANCELLED'}


class CGML3_OT_db_browse_view(bpy.types.Operator):
    """Show the buildings of the 3DCityDB target inside the area seen by the 3D view as boxes (read only)"""
    bl_idname = "cgml3.db_browse_view"
    bl_label = "Show Buildings in View"
    bl_options = {'REGISTER'}

    use_extent: BoolProperty(name="Use extent", default=False, options={'SKIP_SAVE'},
                             description="Query the given CRS extent instead of the 3D view (scripts)")
    extent: FloatVectorProperty(name="Extent (CRS)", size=4, default=(0.0, 0.0, 0.0, 0.0), options={'SKIP_SAVE'},
                                description="xmin, ymin, xmax, ymax in the city model's CRS")
    next_page: BoolProperty(name="Next page", default=False, options={'SKIP_SAVE'},
                            description="Continue the last query after its last feature")

    def execute(self, context):
        s = active_session(context)
        local, crs, after, rays = None, None, 0, None
        classname = context.window_manager.cgml3_db_type_filter
        if self.next_page:
            if s is None or not s.last_query.get("extent"):
                self.report({'ERROR'}, "Show buildings in view first")
                return {'CANCELLED'}
            crs, after = s.last_query["extent"], int(s.last_query.get("last_id", 0))
            classname = s.last_query.get("classname", "")
        elif self.use_extent:
            crs = tuple(self.extent)
        else:
            rays = view_rays(context)
            if rays is None:
                self.report({'ERROR'}, "No 3D view to take the area from")
                return {'CANCELLED'}
            local = ground_extent(rays, 0.0)
            # cut the rays at the height of the buildings already shown, not at the model's lowest point
            origin = (s.origin or s.fixed_origin) if s is not None else None
            if origin is not None and s.rows:
                z = ground_level((r.bbox for r in s.rows), origin, local)
                if z:
                    local = ground_extent(rays, z)
                rays = None  # the worker only looks the plane up when no boxes are shown
        try:
            session, driver = start_query(context, local_extent=local, crs_extent=crs, classname=classname,
                                          after_id=after, rays=rays)
        except Exception as exc:
            return _report_start_error(self, exc)
        if driver.finished and driver.error is not None:
            self.report({'ERROR'}, session.message)
            return {'CANCELLED'}
        return {'FINISHED'}


class CGML3_OT_db_browse_select(bpy.types.Operator):
    """Select browsed 3DCityDB features by feature id"""
    bl_idname = "cgml3.db_browse_select"
    bl_label = "Select Features"
    bl_options = {'REGISTER'}

    action: EnumProperty(items=[
        ('SET', "Set", ""), ('ADD', "Add", ""), ('REMOVE', "Remove", ""),
        ('TOGGLE', "Toggle", ""), ('CLEAR', "None", ""), ('ALL', "All", ""),
    ], default='SET')
    feature_ids: StringProperty(name="Feature ids", description="Comma-separated feature.id values", default="")

    def execute(self, context):
        s = active_session(context)
        if s is None:
            self.report({'ERROR'}, "No 3DCityDB features shown")
            return {'CANCELLED'}
        try:
            fids = catalog.validate_feature_ids(t for t in self.feature_ids.split(",") if t.strip())
        except catalog.InvalidId as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        unknown = [f for f in fids if f not in s.fid_to_index]
        n = set_selection_fids(s, fids, self.action)
        if unknown:
            self.report({'WARNING'}, f"Selected: {n} · not shown: {', '.join(map(str, unknown[:5]))}")
        return {'FINISHED'}


class CGML3_OT_db_browse_load(bpy.types.Operator):
    """Load the selected buildings from 3DCityDB (one citydb-tool export of only these ids)"""
    bl_idname = "cgml3.db_browse_load"
    bl_label = "Load Selected"
    bl_options = {'REGISTER'}

    mode: EnumProperty(name="LoD", items=[
        ('LOD2', "LoD2", "Load the LoD2 geometry of the selected buildings"),
        ('HIGHEST', "Highest", "Load the highest LoD of every selected building"),
    ], default='LOD2')
    feature_id: IntProperty(name="Feature id", default=0, min=0, options={'SKIP_SAVE'},
                            description="Load this feature.id instead of the selection")

    @classmethod
    def description(cls, context, props):
        if props.feature_id:
            return f"Load feature id {props.feature_id}"
        if props.mode == 'HIGHEST':
            return "Load the highest LoD of the selected buildings from 3DCityDB"
        return "Load the LoD2 geometry of the selected buildings from 3DCityDB"

    def execute(self, context):
        s = active_session(context)
        if s is None:
            self.report({'ERROR'}, "No 3DCityDB features shown (Show Buildings in View)")
            return {'CANCELLED'}
        if self.feature_id:
            fids = [self.feature_id]
            set_selection_fids(s, fids)
        else:
            fids = [r.id for r in s.selected_rows()]
            msg = limit_message(len(fids), load_limit(context))
            if msg:
                self.report({'ERROR'}, msg)
                return {'CANCELLED'}
        try:
            driver = start_load(context, s, fids, self.mode)
        except Exception as exc:
            return _report_start_error(self, exc)
        if driver.finished:
            err = s.last_load.get("error")
            if err:
                self.report({'ERROR' if err != "Feature not found" else 'WARNING'}, err)
                return {'CANCELLED'}
            self.report({'INFO'}, s.message)
        return {'FINISHED'}


class CGML3_OT_db_browse_find(bpy.types.Operator):
    """Load a feature by objectid or feature id; several matches are listed"""
    bl_idname = "cgml3.db_browse_find"
    bl_label = "Load by ID"
    bl_options = {'REGISTER'}

    text: StringProperty(name="ID", description="objectid (gml:id) or feature.id", default="", options={'SKIP_SAVE'})
    mode: EnumProperty(name="LoD", items=[
        ('LOD2', "LoD2", ""), ('HIGHEST', "Highest", ""),
    ], default='HIGHEST')

    def execute(self, context):
        text = self.text or context.window_manager.cgml3_db_find_text
        try:
            session, driver = start_find(context, text, self.mode)
        except Exception as exc:
            return _report_start_error(self, exc)
        if driver.finished:
            level = 'WARNING' if session.message.startswith("Feature not found") else 'INFO'
            self.report({level}, session.message)
            if level == 'WARNING':
                return {'CANCELLED'}
        return {'FINISHED'}


class CGML3_OT_db_browse_unload(bpy.types.Operator):
    """Remove the loaded buildings from the scene and show their boxes again. The database is not changed"""
    bl_idname = "cgml3.db_browse_unload"
    bl_label = "Unload"
    bl_options = {'REGISTER', 'UNDO'}

    all: BoolProperty(name="All", default=False, description="Unload every building loaded from this city model")

    def invoke(self, context, event):
        return browse_gml.confirm_unshow(self, context, event)

    def execute(self, context):
        s = active_session(context)
        if s is None:
            self.report({'ERROR'}, "No 3DCityDB features shown")
            return {'CANCELLED'}
        targets = set(s.loaded) if self.all else (s.selected & s.loaded)
        if not targets:
            self.report({'WARNING'}, "No loaded building selected")
            return {'CANCELLED'}
        n = unload(s, fids={s.rows[i].id for i in targets})
        self.report({'INFO'}, f"Unloaded {len(targets)} building(s), {n} objects removed")
        return {'FINISHED'}


class CGML3_OT_db_browse_close(bpy.types.Operator):
    """Remove the box preview of this city model (loaded buildings stay)"""
    bl_idname = "cgml3.db_browse_close"
    bl_label = "Close DB Browse"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = active_session(context)
        if s is None:
            return {'CANCELLED'}
        drv = background_job.current()
        if drv is not None and drv.job.name in (QUERY_JOB, LOAD_JOB, FIND_JOB):
            background_job.cancel_current()
        close(s)
        return {'FINISHED'}


operator_classes = (
    CGML3_OT_db_browse_view,
    CGML3_OT_db_browse_select,
    CGML3_OT_db_browse_load,
    CGML3_OT_db_browse_find,
    CGML3_OT_db_browse_unload,
    CGML3_OT_db_browse_close,
)


def register():
    wm = bpy.types.WindowManager
    wm.cgml3_db_find_text = StringProperty(
        name="ID", description="objectid (gml:id) or feature.id to load", default="", options={'SKIP_SAVE'})
    wm.cgml3_db_type_filter = StringProperty(
        name="Type", description="Only this feature class, e.g. Building (empty: all top-level features)",
        default="", options={'SKIP_SAVE'})


def unregister():
    for name in ("cgml3_db_find_text", "cgml3_db_type_filter"):
        try:
            delattr(bpy.types.WindowManager, name)
        except AttributeError:
            pass
