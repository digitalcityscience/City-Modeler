# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/browse_gml.py
"""Browse first (T08): see where the buildings are, load only what you pick.

1. ``cgml3.browse_gml_file`` indexes the file in a background job
   (``common.background_job``; the bpy-free worker is
   ``shared.browse.browse_worker``) and shows every feature as a box - or as
   its real LoD0 footprint - in ONE mesh object of the ``CityGML Index``
   collection. Boxes appear while the index grows.
2. ``cgml3.browse_pick`` (modal) selects features by clicking a box or by
   dragging a rectangle; ``cgml3.browse_select`` does the same from scripts.
3. ``cgml3.browse_load`` cuts the selected members out of the file by byte
   range into a small temporary GML and hands it to the normal importer; the
   loaded feature's box is hidden. ``cgml3.browse_unload`` deletes the
   detail objects and shows the box again.

CityJSON and CityJSONSeq (CJ-05) go the same way: ``shared.browse.cityjson_index``
gives the same index, and Load builds a small CityJSON document of the picked
buildings for the builder (no temporary file). The LoD is chosen by its text.

The proxy mesh carries the face attributes ``feature_index`` and
``load_state``; a geometry nodes modifier hides faces flagged
``cgml3_hidden`` (loaded features). Selection is drawn as an overlay.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Set

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..common import background_job
from ..shared import cityjson_open as co
from ..shared import georef as gr
from ..shared.browse import cityjson_index as cji
from ..shared.browse import index_cache
from ..shared.browse.browse_worker import run_browse_index
from ..shared.browse.fragment import build_fragment
from ..shared.browse.gml_index import FileIndex, GmlIndexError, IndexCancelled
from ..shared.browse.proxy_geometry import (
    STATE_ERROR,
    STATE_INDEXED,
    STATE_LOADED,
    STATE_LOADING,
    STATE_RISK,
    ProxyArrays,
    bounds_center,
    outline_edges,
    preview_label,
)
from ..shared.browse.selection import (
    DEFAULT_LOAD_LIMIT,
    features_in_rect,
    format_levels,
    limit_message,
    lod_summary,
    plan_load,
)
from ..shared.citygml_sniff import sniff_file

ADDON_PACKAGE = __package__.rsplit(".", 1)[0]
INDEX_COLLECTION = "CityGML Index"
PROXY_PATH_PROP = "cgml3_browse_path"
PROXY_ORIGIN_PROP = "cgml3_browse_origin"
SOURCE_KIND_PROP = "cgml3_source_kind"
SOURCE_PATH_PROP = "cgml3_source_path"
FEATURE_ID_PROP = "cgml3_feature_id"
HIDE_NODE_GROUP = "CityGML Index: hide loaded"
HIDDEN_ATTR = "cgml3_hidden"
PROXY_MATERIAL = "CityGML Index"
PROXY_WRITE_INTERVAL_S = 1.0
JOB_NAME = "browse-index"

# B12: boxes are a see-through tint, so they never pass for real buildings
PROXY_TINT = (0.2, 0.5, 1.0, 0.18)
# overlay colours (RGBA)
COLOR_SELECTED = (1.0, 0.55, 0.05, 1.0)
COLOR_RISK = (0.9, 0.15, 0.1, 1.0)
STATE_COLORS = {
    STATE_INDEXED: (0.55, 0.65, 0.8, 1.0),
    STATE_LOADING: (0.95, 0.85, 0.2, 1.0),
    STATE_LOADED: (0.3, 0.8, 0.35, 1.0),
    STATE_ERROR: (0.85, 0.1, 0.1, 1.0),
    STATE_RISK: (0.95, 0.45, 0.2, 1.0),
}


# --------------------------------------------------------------------------- preferences
def addon_prefs(context=None):
    ctx = context or bpy.context
    try:
        entry = ctx.preferences.addons.get(ADDON_PACKAGE)
    except Exception:
        entry = None
    return getattr(entry, "preferences", None)


def pref(name: str, default, context=None):
    return getattr(addon_prefs(context), name, default)


def cache_max_mb(context=None) -> float:
    return float(pref("index_cache_max_mb", 500.0, context))


def load_limit(context=None) -> int:
    return int(pref("browse_load_limit", DEFAULT_LOAD_LIMIT, context))


# --------------------------------------------------------------------------- session
DB_KEY_PREFIX = "citydb://"


def _key(path: str) -> str:
    if path.startswith(DB_KEY_PREFIX):  # DB browse sessions (ops/browse_citydb.py)
        return path
    return os.path.normcase(os.path.abspath(path))


class BrowseSession:
    """State of one browsed file (main thread only).

    ``ops.browse_citydb.DBBrowseSession`` derives from it, so the proxy mesh,
    overlay and picking work for files and 3DCityDB alike.
    """

    kind = "file"
    collection_name = INDEX_COLLECTION

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.index = FileIndex(path=self.path)
        self.complete = False
        self.from_cache = False
        self.error = ""
        self.origin = None
        self.crs = ""
        self.fixed_origin = None
        self.proxy_name = ""
        self.arrays = ProxyArrays()
        self.selected: Set[int] = set()
        self.loaded: Set[int] = set()
        self.failed: Set[int] = set()
        self.id_to_index: Dict[str, int] = {}
        self.dirty = False
        self.last_write = 0.0
        self.overlay_dirty = True
        self.overlay = None
        self.fill_dirty = True  # see-through box fill: rebuilt on mesh/shown changes only
        self.fill = None
        self.estimate = 0
        self.stats: Dict[str, float] = {}
        self.last_load: Dict[str, object] = {}
        self.framed = ""  # "", "partial" (while indexing) or "complete"
        self.source_format = "CityGML"  # or "CityJSON" / "CityJSONSeq" (CJ-05), from the sniff
        self.t0 = time.perf_counter()

    # convenience
    @property
    def cityjson(self) -> bool:
        return self.source_format in cji.FORMATS

    @property
    def entries(self):
        return self.index.entries

    @property
    def stem(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def proxy_title(self) -> str:
        return f"CityGML Index: {self.stem}"

    def proxy(self):
        return bpy.data.objects.get(self.proxy_name) if self.proxy_name else None

    def preview(self) -> str:
        return preview_label(self.entries)

    def selected_entries(self):
        return [self.entries[i] for i in sorted(self.selected) if i < len(self.entries)]

    def add_entries(self, entries):
        n = len(self.index.entries)
        for k, e in enumerate(entries):
            self.id_to_index[e.gml_id] = n + k
        self.index.entries.extend(entries)

    def set_index(self, idx: FileIndex):
        self.index = idx
        self.id_to_index = {e.gml_id: i for i, e in enumerate(idx.entries)}

    def face_states(self):
        ff = self.arrays.face_feature
        base = self.arrays.face_state
        if not (self.loaded or self.failed):
            return base  # array buffer, no copy
        out = list(base)
        for i, fi in enumerate(ff):
            if fi in self.loaded:
                out[i] = STATE_LOADED
            elif fi in self.failed:
                out[i] = STATE_ERROR
        return out


_sessions: Dict[str, BrowseSession] = {}
_active_key: Optional[str] = None


def sessions() -> List[BrowseSession]:
    return list(_sessions.values())


def get_session(path: str) -> Optional[BrowseSession]:
    return _sessions.get(_key(path))


def active_session(context=None, kind: str = "file") -> Optional[BrowseSession]:
    ctx = context or bpy.context
    obj = getattr(ctx, "active_object", None)
    if obj is not None:
        try:
            path = obj.get(PROXY_PATH_PROP) or obj.get(SOURCE_PATH_PROP)
        except Exception:
            path = None
        if path and _key(path) in _sessions and _sessions[_key(path)].kind == kind:
            return _sessions[_key(path)]
    if _active_key and _active_key in _sessions and _sessions[_active_key].kind == kind:
        return _sessions[_active_key]
    return next((s for s in _sessions.values() if s.kind == kind), None)


def proxy_objects(kind: Optional[str] = None) -> List[bpy.types.Object]:
    """Preview objects; ``kind`` 'file' or 'citydb' keeps only those."""
    out = []
    for o in bpy.data.objects:
        path = o.get(PROXY_PATH_PROP)
        if not path:
            continue
        if kind is not None and (str(path).startswith(DB_KEY_PREFIX)) != (kind == "citydb"):
            continue
        out.append(o)
    return out


# --------------------------------------------------------------------------- world origin
def _normalize_crs(srs: str) -> str:
    from ..shared.materials_common import normalize_crs_to_epsg
    return normalize_crs_to_epsg(srs) if srs else "Unknown CRS"


def _scene_world(context):
    return gr.georef_world(context.scene)


def _local_import(context) -> bool:
    return bool(getattr(context.scene.cgml3, "import_local", False))


def existing_origin(context, srs_name: str):
    """World origin the importers would keep for a file with ``srs_name``, else None.

    Same rule as the importers: an origin is kept when the World CRS matches the file CRS.
    """
    if _local_import(context):
        return None
    world = _scene_world(context)
    crs = _normalize_crs(srs_name)
    if crs == "Unknown CRS" or gr.X_ORIGIN not in world or gr.Y_ORIGIN not in world:
        return None
    if not gr.same_crs(world.get(gr.CRS), crs):
        return None
    if gr.Z_ORIGIN not in world:
        return None
    return (float(world[gr.X_ORIGIN]), float(world[gr.Y_ORIGIN]), float(world[gr.Z_ORIGIN]))


def _apply_origin(context, session: BrowseSession, origin, srs_name: str):
    session.origin = tuple(float(v) for v in origin)
    session.crs = _normalize_crs(srs_name)
    if _local_import(context) or session.fixed_origin is not None:
        return
    world = _scene_world(context)
    world[gr.CRS] = session.crs
    gr.set_world_float(world, gr.X_ORIGIN, session.origin[0])
    gr.set_world_float(world, gr.Y_ORIGIN, session.origin[1])
    gr.set_world_float(world, gr.Z_ORIGIN, session.origin[2])


# --------------------------------------------------------------------------- proxy object
def _index_collection(context, name: str = INDEX_COLLECTION):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll


def _proxy_material():
    mat = bpy.data.materials.get(PROXY_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(PROXY_MATERIAL)
    mat.diffuse_color = STATE_COLORS[STATE_INDEXED]
    try:
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_type = 'GEOMETRY'
        attr.attribute_name = "load_state"
        div = nt.nodes.new("ShaderNodeMath")
        div.operation = 'DIVIDE'
        div.inputs[1].default_value = 4.0
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.interpolation = 'CONSTANT'
        els = ramp.color_ramp.elements
        els[0].position = 0.0
        els[0].color = STATE_COLORS[STATE_INDEXED]
        els[1].position = 0.2
        els[1].color = STATE_COLORS[STATE_LOADING]
        for pos, state in ((0.45, STATE_LOADED), (0.7, STATE_ERROR), (0.95, STATE_RISK)):
            el = els.new(pos)
            el.color = STATE_COLORS[state]
        nt.links.new(attr.outputs["Fac"], div.inputs[0])
        nt.links.new(div.outputs[0], ramp.inputs["Fac"])
        if bsdf is not None:
            nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    except Exception as exc:  # display only
        print(f"[CityGML Browse] proxy material without state colours: {exc}")
    return mat


def _hide_node_group():
    ng = bpy.data.node_groups.get(HIDE_NODE_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(HIDE_NODE_GROUP, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gin = ng.nodes.new("NodeGroupInput")
    gout = ng.nodes.new("NodeGroupOutput")
    named = ng.nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = 'BOOLEAN'
    named.inputs["Name"].default_value = HIDDEN_ATTR
    delete = ng.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = 'FACE'
    ng.links.new(gin.outputs[0], delete.inputs["Geometry"])
    ng.links.new(named.outputs["Attribute"], delete.inputs["Selection"])
    ng.links.new(delete.outputs["Geometry"], gout.inputs[0])
    return ng


def _ensure_proxy(context, session: BrowseSession):
    obj = session.proxy()
    if obj is None:
        for o in proxy_objects():
            if _key(o[PROXY_PATH_PROP]) == _key(session.path):
                obj = o
                break
    if obj is None:
        name = session.proxy_title
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        _index_collection(context, session.collection_name).objects.link(obj)
    obj[PROXY_PATH_PROP] = session.path
    obj[PROXY_ORIGIN_PROP] = list(session.origin)
    obj.hide_render = True
    obj.hide_select = True
    obj.lock_location = obj.lock_rotation = obj.lock_scale = (True, True, True)
    # B12: wireframe + the overlay's see-through fill; a shaded box looked like a real building
    obj.display_type = 'WIRE'
    obj.color = PROXY_TINT
    mat = _proxy_material()
    if not obj.data.materials:
        obj.data.materials.append(mat)
    if not any(m.type == 'NODES' and m.node_group and m.node_group.name == HIDE_NODE_GROUP for m in obj.modifiers):
        try:
            mod = obj.modifiers.new("Hide loaded", 'NODES')
            mod.node_group = _hide_node_group()
        except Exception as exc:
            print(f"[CityGML Browse] hide modifier unavailable: {exc}")
    session.proxy_name = obj.name
    return obj


def _attr(mesh, name, dtype):
    a = mesh.attributes.get(name)
    if a is not None and (a.data_type != dtype or a.domain != 'FACE'):
        mesh.attributes.remove(a)
        a = None
    if a is None:
        a = mesh.attributes.new(name, dtype, 'FACE')
    return a


@contextmanager
def _keep_gil():
    """Keep the GIL on the main thread for a short burst of bpy calls.

    Between two calls Python would hand the GIL to the indexing thread for up
    to one switch interval (5 ms) each time; over the ~10 calls of a proxy
    write that nearly doubled the main-thread block (L: 65 -> 108 ms).
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(1.0)
    try:
        yield
    finally:
        sys.setswitchinterval(old)


def write_proxy_mesh(session: BrowseSession) -> None:
    """Copy the session arrays into the proxy mesh (C-speed buffer copies)."""
    obj = session.proxy()
    if obj is None:
        return
    with _keep_gil():
        _write_proxy_mesh(session, obj)


def _write_proxy_mesh(session: BrowseSession, obj) -> None:
    t0 = time.perf_counter()
    mesh = obj.data
    a = session.arrays
    mesh.clear_geometry()
    mesh.vertices.add(a.vertex_count)
    mesh.loops.add(a.loop_count)
    mesh.polygons.add(a.face_count)
    mesh.vertices.foreach_set("co", a.co)
    mesh.loops.foreach_set("vertex_index", a.loop_vertex)
    mesh.polygons.foreach_set("loop_start", a.loop_start)
    mesh.update(calc_edges=True)
    _attr(mesh, "feature_index", 'INT').data.foreach_set("value", a.face_feature)
    write_proxy_states(session, update=False)
    session.dirty = False
    session.last_write = time.perf_counter()
    session.stats["max_proxy_write_s"] = max(session.stats.get("max_proxy_write_s", 0.0), session.last_write - t0)
    if "first_boxes_s" not in session.stats and a.face_count:
        session.stats["first_boxes_s"] = time.perf_counter() - session.t0


def write_proxy_states(session: BrowseSession, update: bool = True) -> None:
    obj = session.proxy()
    if obj is None:
        return
    mesh = obj.data
    if len(mesh.polygons) != session.arrays.face_count:
        return
    _attr(mesh, "load_state", 'INT').data.foreach_set("value", session.face_states())
    hidden = _attr(mesh, HIDDEN_ATTR, 'BOOLEAN')
    if session.loaded:
        loaded = session.loaded
        hidden.data.foreach_set("value", [fi in loaded for fi in session.arrays.face_feature])
    else:
        hidden.data.foreach_set("value", memoryview(bytearray(session.arrays.face_count)).cast("?"))
    if update:
        mesh.update()
    session.overlay_dirty = True
    session.fill_dirty = True


# --------------------------------------------------------------------------- overlay
_draw_handle = None


def _build_overlay(session: BrowseSession, obj):
    import gpu
    from gpu_extras.batch import batch_for_shader

    sel_pts = outline_edges(session.arrays, set(session.selected) - set(session.loaded))
    risky = {fi for fi, e in enumerate(session.entries) if e.risky}
    risk_pts = outline_edges(session.arrays, risky - set(session.loaded) - set(session.selected)) if risky else []
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    out = []
    if sel_pts:
        out.append((batch_for_shader(shader, 'LINES', {"pos": sel_pts}), COLOR_SELECTED, 3.0))
    if risk_pts:
        out.append((batch_for_shader(shader, 'LINES', {"pos": risk_pts}), COLOR_RISK, 1.5))
    session.overlay = (shader, out)
    session.overlay_dirty = False


def fill_points(session, obj=None):
    """Triangle corners of the unshown footprints (local), from Blender's own
    triangulation of the proxy mesh, so concave outlines do not spill."""
    import numpy as np
    obj = obj or session.proxy()
    mesh = obj.data if obj is not None else None
    if mesh is None or not mesh.polygons:
        return np.empty((0, 3), np.float32)
    mesh.calc_loop_triangles()
    n = len(mesh.loop_triangles)
    tri = np.empty(n * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri)
    face = np.empty(n, np.int32)
    mesh.loop_triangles.foreach_get("polygon_index", face)
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", co)
    tri = tri.reshape(-1, 3)
    if session.loaded:
        feature = np.frombuffer(session.arrays.face_feature, np.int32)[face]
        tri = tri[~np.isin(feature, np.fromiter(session.loaded, np.int32))]
    return co.reshape(-1, 3)[tri.ravel()]


def _draw_fill(session, obj):
    """See-through tint of the unshown footprints; real buildings hide it by depth."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    if session.fill_dirty or session.fill is None:
        session.fill_dirty = False
        pts = fill_points(session, obj)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        session.fill = (shader, batch_for_shader(shader, 'TRIS', {"pos": pts})) if len(pts) else (None, None)
    shader, batch = session.fill
    if batch is None:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.depth_mask_set(False)
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(obj.matrix_world)
    shader.bind()
    shader.uniform_float("color", PROXY_TINT)
    batch.draw(shader)
    gpu.matrix.pop()
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _draw_overlay():
    import gpu
    for session in list(_sessions.values()):
        obj = session.proxy()
        if obj is None or not obj.visible_get():
            continue
        try:
            _draw_fill(session, obj)
        except Exception as exc:
            session.fill, session.fill_dirty = (None, None), False
            print(f"[CityGML Browse] box fill not drawn: {exc}")
        if session.overlay_dirty or session.overlay is None:
            try:
                _build_overlay(session, obj)
            except Exception:
                session.overlay = None
                session.overlay_dirty = False
                continue
        shader, batches = session.overlay
        if not batches:
            continue
        vp = gpu.state.viewport_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(obj.matrix_world)
        shader.bind()
        shader.uniform_float("viewportSize", (vp[2], vp[3]))
        for batch, color, width in batches:
            shader.uniform_float("lineWidth", width)
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.matrix.pop()
        gpu.state.blend_set('NONE')


def _redraw_views():
    background_job.tag_redraw_all()


def frame_proxy(context, session: BrowseSession) -> None:
    """B12: bring the boxes into view (clip range + view_selected), like an import does.

    Called on the first boxes and once more when the index completes, so the
    screen is never left empty while the boxes lie outside the view.
    """
    from ..shared.view_landing import land_on_objects
    obj = session.proxy()
    if obj is None or not len(obj.data.polygons):
        return
    obj.hide_select = False  # view_selected skips unselectable objects
    try:
        land_on_objects(context, [obj])
    except Exception as exc:
        print(f"[CityGML Browse] framing the boxes failed: {exc}")
    finally:
        obj.hide_select = True
    session.framed = "complete" if session.complete else "partial"
    session.stats.setdefault("framed_s", time.perf_counter() - session.t0)


# --------------------------------------------------------------------------- browse job
def _notify(message: str, level: str = 'INFO') -> None:
    print(f"[CityGML Browse] {message}")
    if bpy.app.background:
        return
    icon = {'INFO': 'INFO', 'WARNING': 'ERROR', 'ERROR': 'CANCEL'}.get(level, 'INFO')
    try:
        bpy.context.window_manager.popup_menu(lambda self, ctx: self.layout.label(text=message),
                                              title="CityGML Browse", icon=icon)
    except Exception:
        pass


def _start_pick_mode():
    if bpy.app.background or CGML3_OT_browse_pick.running:
        return
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.cgml3.browse_pick('INVOKE_DEFAULT')
            except Exception as exc:
                print(f"[CityGML Browse] pick mode not started: {exc}")
            return


def start_browse(context, path: str, *, use_cache: bool = True, manual: bool = False):
    """Start indexing ``path``; returns ``(session, driver)`` or raises RuntimeError.

    ``manual`` (benchmarks): the worker runs in its thread and the caller
    ticks the returned driver, as the modal timer would.
    """
    global _active_key
    if background_job.is_running():
        raise RuntimeError("Another background job is running; wait for it or press Esc")
    old = _sessions.get(_key(path))
    session = BrowseSession(path)
    if old is not None:
        session.proxy_name = old.proxy_name
        session.loaded = set(old.loaded)
    summary = sniff_file(path)
    session.estimate = summary.features_estimate
    if summary.format in cji.FORMATS:
        session.source_format = summary.format
    # a single CityJSON document is parsed for Load anyway: no cached index for it
    cached = use_cache and session.source_format != cji.FORMAT_DOCUMENT
    # an existing proxy (same file, saved .blend) keeps its origin
    for o in proxy_objects():
        if _key(o[PROXY_PATH_PROP]) == _key(path) and o.get(PROXY_ORIGIN_PROP):
            session.proxy_name = o.name
            session.fixed_origin = tuple(float(v) for v in o[PROXY_ORIGIN_PROP])
    if session.fixed_origin is None:
        session.fixed_origin = existing_origin(context, summary.srs_name)
    if session.fixed_origin is None and not _local_import(context):
        gr.check_crs(summary.srs_name)  # the index would re-base the World (B31)
    _sessions[_key(path)] = session
    _active_key = _key(path)
    _refresh_loaded(session)

    def worker(job):
        def progress(done, total, n):
            est = max(session.estimate, n)
            job.set_progress(done, total, f"{n:,} / ~{est:,}")
        try:
            return run_browse_index(
                path, job.post, fixed_origin=session.fixed_origin, progress=progress,
                cancelled=job.cancelled, cache_max_mb=cache_max_mb(), use_cache=cached,
                **({"indexer": cji.index_cityjson} if session.cityjson else {}))
        except IndexCancelled:
            raise background_job.JobCancelled()

    def on_message(msg):
        kind = msg[0]
        if kind == "origin":
            _apply_origin(bpy.context, session, msg[1], msg[3])
            session.index.version, session.index.srs_name = msg[2], msg[3]
            _ensure_proxy(bpy.context, session)
            _start_pick_mode()
        elif kind == "batch":
            session.add_entries(msg[1])
            session.arrays.extend(msg[2])
            session.dirty = True
        elif kind == "final":
            idx, arrays, from_cache = msg[1], msg[2], msg[3]
            session.set_index(idx)
            session.from_cache = from_cache
            if arrays is not None:
                session.arrays = arrays
            session.dirty = True

    def on_tick():
        if session.dirty and time.perf_counter() - session.last_write >= PROXY_WRITE_INTERVAL_S:
            write_proxy_mesh(session)
            if not session.framed:
                frame_proxy(bpy.context, session)
            _redraw_views()
            return True
        return False

    def on_done(result, error, cancelled):
        session.stats["index_s"] = time.perf_counter() - session.t0
        if cancelled:
            close_session(session, remove_proxy=True)
            _notify("Indexing cancelled", 'WARNING')
            return
        if error is not None:
            session.error = str(error)
            msg = str(error) if isinstance(error, GmlIndexError) else f"Indexing failed: {error}"
            close_session(session, remove_proxy=True)
            _notify(msg, 'ERROR')
            return
        session.complete = True
        if session.dirty or session.last_write == 0.0:
            write_proxy_mesh(session)
        _refresh_loaded(session)
        write_proxy_states(session)
        if session.framed != "complete":
            frame_proxy(bpy.context, session)
        _redraw_views()
        n = len(session.entries)
        risks = session.index.risk_counts()
        risk_txt = "; ".join(f"{k}: {v}" for k, v in sorted(risks.items()))
        skipped = not_indexed_text(session)
        if session.cityjson:    # count and LoDs from the index, not from the sniff's token scan
            skipped = " · ".join(t for t in ("LoD " + ", ".join(cji.lod_texts(session.entries)), skipped) if t)
        _notify(
            f"{'Index from cache' if session.from_cache else 'Indexed'}: {n:,} features "
            f"in {session.stats['index_s']:.1f} s · preview: {session.preview()}"
            + (f" · {risk_txt}" if risk_txt else "") + (f" · {skipped}" if skipped else ""),
            'WARNING' if risks else 'INFO',
        )

    job = background_job.Job(JOB_NAME, worker, on_message=on_message, on_done=on_done,
                             on_tick=on_tick, label=f"Indexing {session.stem}")
    driver = background_job.start(job, context, manual=manual)
    if driver is None:
        _sessions.pop(_key(path), None)
        raise RuntimeError("Another background job is running")
    return session, driver


def not_indexed_text(session: BrowseSession) -> str:
    """CityJSON: what the file has and the index does not ("not indexed: 2 WaterBody"), else ''."""
    kinds = session.index.skipped_kinds
    return "not indexed: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) if kinds else ""


def close_session(session: BrowseSession, *, remove_proxy: bool = True) -> None:
    global _active_key
    if remove_proxy:
        obj = session.proxy()
        if obj is not None:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        coll = bpy.data.collections.get(session.collection_name)
        if coll is not None and not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)
    _sessions.pop(_key(session.path), None)
    if _active_key == _key(session.path):
        _active_key = next(iter(_sessions), None)
    _redraw_views()


# --------------------------------------------------------------------------- selection
def set_selection(session: BrowseSession, indices: Iterable[int], action: str = 'SET') -> int:
    indices = {i for i in indices if 0 <= i < len(session.entries)}
    if action == 'SET':
        session.selected = indices
    elif action == 'ADD':
        session.selected |= indices
    elif action == 'REMOVE':
        session.selected -= indices
    elif action == 'TOGGLE':
        session.selected ^= indices
    elif action == 'CLEAR':
        session.selected = set()
    elif action == 'ALL':
        session.selected = set(range(len(session.entries)))
    session.overlay_dirty = True
    _redraw_views()
    return len(session.selected)


def _view_rays(region, rv3d, coord):
    from bpy_extras import view3d_utils
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    return origin, direction


def feature_at(context, region, rv3d, coord):
    """``(session, feature_index)`` of the nearest proxy face under ``coord``, or (None, None)."""
    origin, direction = _view_rays(region, rv3d, coord)
    return feature_on_ray(context, origin, direction)


def feature_on_ray(context, origin, direction):
    """``(session, feature_index)`` of the first box the world ray hits, unless real geometry is in front."""
    depsgraph = context.evaluated_depsgraph_get()
    best = (None, None, float("inf"))
    for session in _sessions.values():
        obj = session.proxy()
        if obj is None or not obj.visible_get():
            continue
        ev = obj.evaluated_get(depsgraph)
        inv = ev.matrix_world.inverted()
        lo = inv @ origin
        ld = (inv.to_3x3() @ direction).normalized()
        hit, loc, _nor, face = ev.ray_cast(lo, ld)
        if not hit:
            continue
        dist = (ev.matrix_world @ loc - origin).length
        attr = ev.data.attributes.get("feature_index")
        if attr is None or face >= len(attr.data):
            continue
        if dist < best[2]:
            best = (session, attr.data[face].value, dist)
    if best[0] is not None:
        # B13: a shown building in front of a box gets the click, not the box behind it
        hit, loc, _nor, _idx, hit_obj, _mat = context.scene.ray_cast(depsgraph, origin, direction)
        if hit and not hit_obj.original.get(PROXY_PATH_PROP) and (loc - origin).length < best[2] - 1e-4:
            return None, None
    return best[0], best[1]


def features_in_region_rect(session: BrowseSession, region, rv3d, rect) -> List[int]:
    obj = session.proxy()
    if obj is None or session.origin is None:
        return []
    m = rv3d.perspective_matrix @ obj.matrix_world
    matrix = [list(row) for row in m]
    ox, oy, oz = session.origin
    centers = {}
    for fi, e in enumerate(session.entries):
        if e.bbox and fi not in session.loaded:
            c = bounds_center(e.bbox)
            centers[fi] = (c[0] - ox, c[1] - oy, c[2] - oz)
    return features_in_rect(centers, matrix, region.width, region.height, rect)


# --------------------------------------------------------------------------- load / unload
@contextmanager
def _browse_import_filters(scene, level: Optional[int]):
    """Only LoD ``level``; no bbox / id / type filter (the fragment is the filter)."""
    props = scene.cgml3
    names = ("use_lod_filter", "lod_0", "lod_1", "lod_2", "lod_3", "lod_4",
             "use_bbox_filter", "use_gmlid_filter", "use_feature_type_filter")
    saved = {n: getattr(props, n) for n in names if hasattr(props, n)}
    try:
        props.use_bbox_filter = False
        props.use_gmlid_filter = False
        props.use_feature_type_filter = False
        props.use_lod_filter = level is not None
        if level is not None:
            for k in range(5):
                setattr(props, f"lod_{k}", k == level)
        yield
    finally:
        for n, v in saved.items():
            setattr(props, n, v)


def _file_of(o):
    """The browsed file of a root. A commit to 3DCityDB re-tags the root "citydb" (B84); a CityJSON
    root still names its source file (CJ-04)."""
    return o.get(SOURCE_PATH_PROP) if o.get(SOURCE_KIND_PROP) == "file" else o.get(co.SOURCE_PROP)


def _feature_objects(session: BrowseSession, gml_ids: Optional[Set[str]] = None):
    """Root objects loaded by browse for this file (optionally only ``gml_ids``)."""
    key = _key(session.path)
    out = []
    for o in bpy.data.objects:
        try:
            if not o.get(FEATURE_ID_PROP):
                continue
            src = _file_of(o)
        except Exception:
            continue
        if not src or _key(src) != key:
            continue
        if gml_ids is not None and o[FEATURE_ID_PROP] not in gml_ids:
            continue
        out.append(o)
    return out


def _refresh_loaded(session: BrowseSession) -> None:
    ids = {o[FEATURE_ID_PROP] for o in _feature_objects(session)}
    session.loaded = {session.id_to_index[i] for i in ids if i in session.id_to_index}


def shown_targets(context, session: BrowseSession) -> Set[int]:
    """Shown features to Unshow: picked boxes plus buildings selected in the viewport.

    After Show the box is hidden, so the user selects the real building (any
    surface of it); its root carries the feature id.
    """
    out = set(session.selected)
    key = _key(session.path)
    for o in context.view_layer.objects:
        if not o.select_get():
            continue
        p = o
        while p is not None:
            gid = p.get(FEATURE_ID_PROP)
            if gid and _file_of(p) and _key(str(_file_of(p))) == key:
                if gid in session.id_to_index:
                    out.add(session.id_to_index[gid])
                break
            p = p.parent
    return out & session.loaded


def _remove_objects(roots) -> int:
    objs = []
    seen = set()
    for r in roots:
        for o in [r, *r.children_recursive]:
            if o.as_pointer() not in seen:
                seen.add(o.as_pointer())
                objs.append(o)
    datas = [o.data for o in objs if o.data is not None]
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for d in datas:
        try:
            if d.users == 0:
                if isinstance(d, bpy.types.Mesh):
                    bpy.data.meshes.remove(d)
                elif isinstance(d, bpy.types.Curve):
                    bpy.data.curves.remove(d)
        except ReferenceError:
            pass
    return len(objs)


def _remove_empty_collections(names: Iterable[str]) -> None:
    for name in names:
        coll = bpy.data.collections.get(name)
        if coll is not None and not coll.all_objects and not coll.children:
            bpy.data.collections.remove(coll)


def unload_features(session: BrowseSession, indices: Iterable[int]) -> int:
    """Delete the detail objects of ``indices``; their boxes show again."""
    ids = {session.entries[i].gml_id for i in indices if 0 <= i < len(session.entries)}
    roots = _feature_objects(session, ids)
    from . import commit_citydb   # CJ-06 / B84: unloaded is not deleted, neither in the source nor in the DB
    others = commit_citydb.forget(roots)
    colls = {c.name for r in roots for c in r.users_collection}
    removed = _remove_objects(roots)
    _remove_empty_collections(colls)
    _refresh_loaded(session)
    session.failed -= set(indices)
    write_proxy_states(session)
    commit_citydb.refresh_boxes(others - {session.path})
    _redraw_views()
    return removed


def _temp_fragment_path(session: BrowseSession) -> str:
    folder = os.path.join(tempfile.gettempdir(), "cgml3_browse")
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f"{session.stem}_", suffix=".gml", dir=folder)
    os.close(fd)
    return path


def load_features(context, session: BrowseSession, indices: Iterable[int], mode: str = 'LOD2', lod: str = ""):
    """Import ``indices`` from their byte ranges; returns ``(level, message)``.

    CityJSON (CJ-05): ``lod`` is the LoD text to load ("2.2"; empty: the default among the LoDs of the
    picked buildings, ``cityjson_open.lod_choices``) and ``mode`` is not used. A building without that
    LoD is left out and counted, never loaded in another LoD."""
    from ..common import import_citygml_auto
    from ..common.import_landing import ImportSnapshot, finish_import
    from ..shared.load_limits import ImportNotSupported

    indices = sorted({i for i in indices if 0 <= i < len(session.entries)})
    if not session.complete:
        return 'ERROR', "Wait until the index is complete"
    if session.cityjson:
        lod = lod or (co.lod_choices(cji.lod_texts(session.entries[i] for i in indices)) or [""])[0]
        loading = [i for i in indices if lod in session.entries[i].lod_texts]
        groups, missing = {}, [i for i in indices if lod not in session.entries[i].lod_texts]
        what = f"LoD{lod}" if lod else "geometry"
    else:
        groups, missing = plan_load({i: session.entries[i] for i in indices}, mode)
        loading = [i for g in groups.values() for i in g]
        what = 'LoD2' if mode == 'LOD2' else 'geometry'
    if not loading:
        return 'ERROR', f"None of the {len(indices)} selected features has {what}"
    replaced = [i for i in indices if i in session.loaded]
    if replaced:
        unload_features(session, replaced)
    warnings: List[str] = []
    left_out: List[str] = []    # import summary "not loaded" texts (CityJSON)
    t0 = time.perf_counter()
    snapshot = ImportSnapshot()
    bytes_read = 0
    if session.cityjson:
        from ..common.unified_builder import import_cityjson_with_builder
        doc, bytes_read = cji.selection_document(session.index, loading)
        with _browse_import_filters(context.scene, None):
            try:
                notices, left_out = import_cityjson_with_builder(
                    session.path, context, lod=lod, doc=doc, origin=session.origin)
                warnings.extend(notices)
            except ImportNotSupported as exc:
                warnings.append(str(exc))
    for level, fis in sorted(groups.items()):
        frag = build_fragment(session.index, fis, origin=session.origin)
        bytes_read += frag.bytes_read
        if frag.untranslated_namespaces:
            warnings.append("CityGML 1.0 namespaces without 2.0 mapping: " + ", ".join(frag.untranslated_namespaces)
                            + " (consider 'citygml-tools upgrade')")
        tmp = _temp_fragment_path(session)
        try:
            with open(tmp, "wb") as fh:
                fh.write(frag.data)
            with _browse_import_filters(context.scene, level):
                try:
                    warnings.extend(import_citygml_auto(tmp, context, import_appearance=True, data=frag.data))
                except ImportNotSupported as exc:
                    warnings.append(str(exc))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    # tag the roots of the loaded features
    wanted = {session.entries[i].gml_id: i for i in loading}
    tagged: Set[int] = set()
    for o in snapshot.new_objects():
        p, root = o, None
        while p is not None:
            if p.get("gml_id") in wanted:
                root = p
            p = p.parent
        if root is not None and root.get(FEATURE_ID_PROP) is None:
            root[SOURCE_KIND_PROP] = "file"
            root[SOURCE_PATH_PROP] = session.path
            root[FEATURE_ID_PROP] = root["gml_id"]
            tagged.add(wanted[root["gml_id"]])
    not_loaded = [i for i in loading if i not in tagged]
    session.failed = (session.failed - set(tagged)) | set(not_loaded)
    if not_loaded:
        warnings.append(f"not imported: {len(not_loaded)}")
    if missing:
        warnings.append(f"no {what}: {len(missing)}")
        if session.cityjson:
            left_out.append(warnings[-1])
    ext = sum(1 for i in loading if session.entries[i].external_xlink)
    if ext:
        warnings.append(f"geometry referenced outside the feature: {ext}")
    imp = sum(1 for i in loading if session.entries[i].implicit_geometry)
    if imp:
        warnings.append(f"implicit geometry: {imp}")
    _refresh_loaded(session)
    write_proxy_states(session)
    level, message, summary = finish_import(context, snapshot, frame=False, warnings=warnings,
                                            not_loaded=left_out)
    seconds = time.perf_counter() - t0
    session.last_load = {
        "features": len(tagged), "requested": len(indices), "bytes_read": bytes_read,
        "seconds": seconds, "objects": len(snapshot.new_objects()), "warnings": list(warnings),
        "message": message, "mode": mode, "lod": lod, "not_loaded": list(left_out),
    }
    print(f"[CityGML Browse] read {bytes_read:,} bytes for {len(loading)} features in {seconds:.2f} s")
    if warnings:
        print("[CityGML Browse] " + "; ".join(warnings))
    _redraw_views()
    return level, f"{message} · {bytes_read / 1024:.0f} KB read" if bytes_read else message


# --------------------------------------------------------------------------- show / unshow
# B12: one gesture for file and DB previews; the existing Load/Unload operators do the work.
SHOW_OPS = {"file": ("cgml3.browse_load", "cgml3.browse_unload"),
            "citydb": ("cgml3.db_browse_load", "cgml3.db_browse_unload")}
PICK_HINT = ("Pick: click an outline · Shift+click add/remove · drag or B box select · "
             "F focus · S / Enter Show · U Unshow · right-click menu · Picking button (Open tab) stops it")


def picked_session() -> Optional[BrowseSession]:
    """The preview the user picked in last (file or 3DCityDB)."""
    if _active_key in _sessions:
        return _sessions[_active_key]
    return next(iter(_sessions.values()), None)


def show_op_ids(session: Optional[BrowseSession] = None):
    """``(show_id, unshow_id)`` for ``session`` (default: the picked one), or None."""
    s = session or picked_session()
    return SHOW_OPS.get(s.kind) if s is not None else None


def run_show(show: bool = True, mode: str = 'HIGHEST'):
    ids = show_op_ids()
    if ids is None:
        return {'CANCELLED'}
    cat, name = ids[0 if show else 1].split(".")
    op = getattr(getattr(bpy.ops, cat), name)
    if show:
        return op(mode=mode)
    return op() if bpy.app.background else op('INVOKE_DEFAULT')  # interactive: confirm first


def confirm_unshow(op, context, event):
    """Unshow deletes the real geometry and edits on it, and undo does not bring it back."""
    if bpy.app.background:
        return op.execute(context)
    return context.window_manager.invoke_confirm(
        op, event, title="Unshow", confirm_text="Unshow", icon='WARNING',
        message="Deletes the shown buildings and any edits on them (no undo). Save first to keep edits.")


def draw_context_menu(self, context):
    """Viewport right-click menu: Show / Unshow while a preview is open."""
    s = picked_session()
    ids = show_op_ids(s)
    if ids is None:
        return
    layout = self.layout
    layout.separator()
    n = len(s.selected - s.loaded)
    row = layout.row()
    row.enabled = n > 0
    row.operator(ids[0], text=f"Show Picked Buildings ({n})", icon='HIDE_OFF').mode = 'HIGHEST'
    layout.operator(ids[1], text="Unshow (back to outline)", icon='HIDE_ON')
    layout.operator("cgml3.browse_frame_picked", icon='ZOOM_SELECTED')


def frame_picked(context, session) -> int:
    """Centre every 3D view on the picked buildings (all of them when none is picked).

    The proxy is not selectable, so view_selected cannot do it; the bboxes are
    known, so the view is set directly. Returns the number of buildings framed.
    """
    from ..shared.view_landing import _apply_clip, _iter_view3d_spaces
    picked = sorted(session.selected) or range(len(session.entries))
    boxes = [session.entries[fi].bbox for fi in picked if fi < len(session.entries) and session.entries[fi].bbox]
    if not boxes:
        return 0
    o = session.origin or (0.0, 0.0, 0.0)
    lo = [min(b[k] for b in boxes) - o[k] for k in range(3)]
    hi = [max(b[k + 3] for b in boxes) - o[k] for k in range(3)]
    size = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 10.0)
    _apply_clip(size)
    for space in _iter_view3d_spaces():
        r3d = space.region_3d
        r3d.view_location = [(lo[k] + hi[k]) / 2 for k in range(3)]
        r3d.view_distance = size * 1.6
    return len(boxes)


# --------------------------------------------------------------------------- operators
def _session_or_report(op, context) -> Optional[BrowseSession]:
    s = active_session(context)
    if s is None:
        op.report({'ERROR'}, "No file is being browsed (File > Import > CityGML, Browse first)")
    return s


class CGML3_OT_browse_gml_file(bpy.types.Operator):
    """Preview the file: one flat outline per building on the ground; Show only the buildings you pick"""
    bl_idname = "cgml3.browse_gml_file"
    bl_label = "Preview CityGML (outlines first)"
    bl_options = {'REGISTER'}

    filepath: StringProperty(name="CityGML", subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.gml;*.xml;*.json;*.jsonl", options={'HIDDEN'})
    use_cache: BoolProperty(name="Use index cache", default=True,
                            description="Reuse the cached index of an unchanged file; off re-indexes and refreshes the cache")

    def invoke(self, context, event):
        if self.filepath:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        path = self.filepath
        if not path or not os.path.isfile(path):
            self.report({'ERROR'}, f"File not found: {path}")
            return {'CANCELLED'}
        low = path.lower()
        if low.endswith((".zip", ".gz", ".gzip")):
            self.report({'ERROR'}, "Extract the archive first: compressed files cannot be browsed")
            return {'CANCELLED'}
        summary = sniff_file(path)
        if summary.format in ("CityJSON", "CityJSONSeq", "JSON"):
            # CJ-05: previewed here like GML; only what cannot be indexed is refused
            kind, text = co.open_decision(summary)
            if kind == 'error':
                self.report({'ERROR'}, text)
                return {'CANCELLED'}
        elif not summary.is_citygml:
            self.report({'ERROR'}, f"Not a CityGML file (no CityGML namespace in header): {os.path.basename(path)}")
            return {'CANCELLED'}
        try:
            session, driver = start_browse(context, path, use_cache=self.use_cache)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if driver.finished and session.error:
            self.report({'ERROR'}, session.error)
            return {'CANCELLED'}
        if not session.complete and driver.finished:
            return {'CANCELLED'}
        # CityJSON: the sniff's count and LoDs are a token scan; the index reports the real ones when done
        self.report({'INFO'}, f"Browsing {summary.format} {os.path.basename(path)}" if session.cityjson
                    else f"Browsing {summary.label()}")
        return {'FINISHED'}


class CGML3_OT_browse_select(bpy.types.Operator):
    """Select browsed features by gml:id or index"""
    bl_idname = "cgml3.browse_select"
    bl_label = "Select Buildings"
    bl_options = {'REGISTER'}

    action: EnumProperty(items=[
        ('SET', "Set", ""), ('ADD', "Add", ""), ('REMOVE', "Remove", ""),
        ('TOGGLE', "Toggle", ""), ('CLEAR', "None", ""), ('ALL', "All", ""),
    ], default='SET')
    ids: StringProperty(name="gml:ids", description="Comma-separated gml:ids", default="")
    indices: StringProperty(name="Indices", description="Comma-separated feature indices", default="")

    def execute(self, context):
        s = _session_or_report(self, context)
        if s is None:
            return {'CANCELLED'}
        chosen = set()
        unknown = []
        for gid in (t.strip() for t in self.ids.split(",")):
            if gid:
                if gid in s.id_to_index:
                    chosen.add(s.id_to_index[gid])
                else:
                    unknown.append(gid)
        for t in self.indices.split(","):
            t = t.strip()
            if t.isdigit():
                chosen.add(int(t))
        n = set_selection(s, chosen, self.action)
        if unknown:
            self.report({'WARNING'}, f"Selected: {n} · unknown ids: {', '.join(unknown[:5])}")
        return {'FINISHED'}


class CGML3_OT_browse_load(bpy.types.Operator):
    """Show the real geometry of the picked outlines (only their bytes are read from the file)"""
    bl_idname = "cgml3.browse_load"
    bl_label = "Show"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="LoD", items=[
        ('LOD2', "LoD2", "Load the LoD2 geometry of the selected buildings"),
        ('HIGHEST', "Highest", "Load the highest LoD of every selected building"),
    ], default='LOD2')
    lod: StringProperty(
        name="CityJSON LoD", default="", options={'SKIP_SAVE'},
        description="CityJSON preview: the LoD to load, as written in the file (2.2); "
                    "empty loads the default LoD of the picked buildings. Buildings without it are skipped")

    @classmethod
    def description(cls, context, props):
        if props.lod:
            return f"Show the picked buildings in LoD {props.lod}; buildings without it are skipped and counted"
        if props.mode == 'HIGHEST':
            return "Show the picked buildings in their highest LoD (only their bytes are read)"
        return "Show the picked buildings in LoD2 (only their bytes are read)"

    def execute(self, context):
        s = _session_or_report(self, context)
        if s is None:
            return {'CANCELLED'}
        msg = limit_message(len(s.selected), load_limit(context))
        if msg:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        try:
            level, message = load_features(context, s, s.selected, self.mode, self.lod)
        except Exception as exc:
            from ..common.import_landing import report_import_error
            report_import_error(self, exc, log_prefix="citygml_browse_error")
            s.failed |= set(s.selected)
            write_proxy_states(s)
            return {'CANCELLED'}
        self.report({level}, message)
        return {'FINISHED'} if level != 'ERROR' else {'CANCELLED'}


class CGML3_OT_browse_unload(bpy.types.Operator):
    """Unshow: delete the real geometry of the picked (or selected) buildings and show their outlines again"""
    bl_idname = "cgml3.browse_unload"
    bl_label = "Unshow"
    bl_options = {'REGISTER', 'UNDO'}

    all: BoolProperty(name="All", description="Unshow every shown building of this file", default=False)

    def invoke(self, context, event):
        return confirm_unshow(self, context, event)

    def execute(self, context):
        s = _session_or_report(self, context)
        if s is None:
            return {'CANCELLED'}
        targets = set(s.loaded) if self.all else shown_targets(context, s)
        if not targets:
            self.report({'WARNING'}, "Select a shown building (or pick its outline) to unshow it")
            return {'CANCELLED'}
        n = unload_features(s, targets)
        self.report({'INFO'}, f"Unshown {len(targets)} building(s), {n} objects removed; outlines are back")
        return {'FINISHED'}


class CGML3_OT_browse_close(bpy.types.Operator):
    """Close the outline preview of this file (shown buildings stay)"""
    bl_idname = "cgml3.browse_close"
    bl_label = "Close Browse"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _session_or_report(self, context)
        if s is None:
            return {'CANCELLED'}
        if background_job.is_running():
            background_job.cancel_current()
        close_session(s, remove_proxy=True)
        return {'FINISHED'}


class CGML3_OT_browse_resume(bpy.types.Operator):
    """Index this preview's file again (from the cache when unchanged)"""
    bl_idname = "cgml3.browse_resume"
    bl_label = "Resume Browsing"
    bl_options = {'REGISTER'}

    def execute(self, context):
        obj = context.active_object
        path = obj.get(PROXY_PATH_PROP) if obj is not None else None
        if path and str(path).startswith(DB_KEY_PREFIX):
            path = None
        if not path:
            objs = proxy_objects("file")
            path = objs[0][PROXY_PATH_PROP] if objs else None
        if not path:
            self.report({'ERROR'}, "No CityGML index preview in this file")
            return {'CANCELLED'}
        return bpy.ops.cgml3.browse_gml_file(filepath=path)


class CGML3_OT_browse_cancel(bpy.types.Operator):
    """Stop the running indexing job"""
    bl_idname = "cgml3.browse_cancel"
    bl_label = "Cancel Indexing"

    def execute(self, context):
        background_job.cancel_current()
        return {'FINISHED'}


class CGML3_OT_browse_clear_cache(bpy.types.Operator):
    """Delete all cached CityGML indexes"""
    bl_idname = "cgml3.browse_clear_cache"
    bl_label = "Clear Index Cache"

    def execute(self, context):
        n = index_cache.clear()
        self.report({'INFO'}, f"Removed {n} cached index(es)")
        return {'FINISHED'}


class CGML3_OT_browse_frame_picked(bpy.types.Operator):
    """Zoom the view to the picked buildings (to all outlines when none is picked)"""
    bl_idname = "cgml3.browse_frame_picked"
    bl_label = "Focus on Picked"

    @classmethod
    def poll(cls, context):
        return picked_session() is not None

    def execute(self, context):
        n = frame_picked(context, picked_session())
        if not n:
            self.report({'WARNING'}, "Nothing to focus on")
            return {'CANCELLED'}
        return {'FINISHED'}


class CGML3_OT_browse_pick(bpy.types.Operator):
    """On: clicks in the viewport pick preview outlines (Shift+click add/remove, drag or B box select). Off: clicks select objects as usual"""
    bl_idname = "cgml3.browse_pick"
    bl_label = "Pick Buildings"

    running = False
    DRAG_PX = 5
    SHOW_KEYS = {'S', 'RET', 'NUMPAD_ENTER', 'U'}
    FOCUS_KEYS = {'F', 'NUMPAD_PERIOD'}

    def invoke(self, context, event):
        if CGML3_OT_browse_pick.running:
            CGML3_OT_browse_pick.running = False  # second press stops it
            return {'CANCELLED'}
        if not _sessions:
            self.report({'ERROR'}, "No file is being browsed")
            return {'CANCELLED'}
        self._press = None
        self._rect_mode = False
        self._rect = None
        self._px_handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_rect, (), 'WINDOW', 'POST_PIXEL')
        CGML3_OT_browse_pick.running = True
        context.window_manager.modal_handler_add(self)
        self._header(context, True)
        return {'RUNNING_MODAL'}

    def _header(self, context, on):
        # B24: status bar, like Blender's own modal hints; the header rows stay usable
        try:
            context.workspace.status_text_set(PICK_HINT if on else None)
        except Exception:
            pass

    def _finish(self, context):
        CGML3_OT_browse_pick.running = False
        if self._px_handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._px_handle, 'WINDOW')
            self._px_handle = None
        self._header(context, False)
        _redraw_views()

    def cancel(self, context):
        self._finish(context)

    def _draw_rect(self):
        if not self._rect:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        if bpy.context.region != self._rect[0]:
            return
        x0, y0, x1, y1 = self._rect[1]
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]})
        shader.bind()
        shader.uniform_float("color", COLOR_SELECTED)
        batch.draw(shader)

    @staticmethod
    def _view_under_mouse(context, event):
        for area in context.window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type != 'WINDOW':
                    continue
                if region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height:
                    rv3d = region.data
                    return area, region, rv3d, (event.mouse_x - region.x, event.mouse_y - region.y)
        return None

    def modal(self, context, event):
        if not CGML3_OT_browse_pick.running or not _sessions:
            self._finish(context)
            return {'CANCELLED'}
        # B73: Esc no longer stops picking; every tool's Esc (cancel a window, a balcony ...) silently
        # turned it off and the outlines could not be clicked any more. The Picking button stops it.
        if (event.type in self.SHOW_KEYS and event.value == 'PRESS'
                and not (event.shift or event.ctrl or event.alt or event.oskey)
                and self._view_under_mouse(context, event)):
            try:
                run_show(event.type != 'U')
            except RuntimeError as exc:  # the operator's own report (e.g. nothing picked)
                self.report({'WARNING'}, str(exc).splitlines()[-1].removeprefix("Error: "))
            return {'RUNNING_MODAL'}
        if (event.type in self.FOCUS_KEYS and event.value == 'PRESS'
                and not (event.shift or event.ctrl or event.alt or event.oskey)
                and self._view_under_mouse(context, event)):
            s = picked_session()
            if s is not None:
                frame_picked(context, s)
            return {'RUNNING_MODAL'}
        if event.type == 'B' and event.value == 'PRESS' and self._view_under_mouse(context, event):
            self._rect_mode = True
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and self._press is not None:
            region, start = self._press[1], self._press[2]
            cur = (event.mouse_x - region.x, event.mouse_y - region.y)
            if abs(cur[0] - start[0]) > self.DRAG_PX or abs(cur[1] - start[1]) > self.DRAG_PX:
                self._rect = (region, (start[0], start[1], cur[0], cur[1]))
                self._press[0].tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit = self._view_under_mouse(context, event)
            if hit is None:
                return {'PASS_THROUGH'}
            area, region, rv3d, coord = hit
            session, fi = feature_at(context, region, rv3d, coord)
            if session is None and not self._rect_mode:
                return {'PASS_THROUGH'}
            self._press = (area, region, coord, rv3d, session, fi)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._press is not None:
            area, region, coord, rv3d, session, fi = self._press
            self._press = None
            action = 'REMOVE' if event.ctrl else ('ADD' if event.shift else 'SET')
            if self._rect is not None:
                rect = self._rect[1]
                self._rect = None
                self._rect_mode = False
                for s in _sessions.values():
                    picked = features_in_region_rect(s, region, rv3d, rect)
                    set_selection(s, picked, action)
            elif session is not None:
                if event.shift and fi in session.selected:
                    action = 'REMOVE'
                set_selection(session, [fi], action)
                global _active_key
                _active_key = _key(session.path)
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


operator_classes = (
    CGML3_OT_browse_gml_file,
    CGML3_OT_browse_select,
    CGML3_OT_browse_load,
    CGML3_OT_browse_unload,
    CGML3_OT_browse_close,
    CGML3_OT_browse_resume,
    CGML3_OT_browse_cancel,
    CGML3_OT_browse_clear_cache,
    CGML3_OT_browse_pick,
    CGML3_OT_browse_frame_picked,
)


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    _sessions.clear()
    global _active_key
    _active_key = None
    CGML3_OT_browse_pick.running = False


def register():
    global _draw_handle
    if not bpy.app.background and _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_VIEW')
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    bpy.types.VIEW3D_MT_object_context_menu.append(draw_context_menu)


def unregister():
    global _draw_handle
    try:
        bpy.types.VIEW3D_MT_object_context_menu.remove(draw_context_menu)
    except Exception:
        pass
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    CGML3_OT_browse_pick.running = False
    _sessions.clear()


def selection_summary(session: BrowseSession):
    """Panel text: ``(count, common LoDs, preview label, risk lines)``."""
    ents = session.selected_entries()
    common, _union = lod_summary(ents)
    lods = format_levels(common)
    if session.cityjson and ents:   # the LoDs as written in the file, those every picked building has
        shared = set.intersection(*(set(e.lod_texts) for e in ents))
        lods = ", ".join(sorted(shared, key=cji.lod_order)) or "none"
    risks: Dict[str, int] = {}
    for e in ents:
        for r in e.risks():
            risks[r] = risks.get(r, 0) + 1
    return len(ents), lods, preview_label(ents) if ents else session.preview(), risks
