"""T09 end to end in Blender: DB Browse first against the local 3DCityDB v5.

Scratch schemas (created here, dropped at the end; T02 fixtures untouched):
- ``t09_browse``: the T03 ``S`` set (300 buildings) - query time, main-thread
  blocks, panning without duplicates, limit + paging, 6 buildings -> 6
  top-level features, replace, selection limit.
- ``t09_sample``: buildings_lod2_citygml_3_0.gml + buildings_lod3_citygml_3_0.gml
  (same objectids as LoD2 and LoD3 rows) - grouping, Load by ID, LoD3 picks
  the right feature.id, "Feature not found", rejected input, and equivalence
  with the T08 file browse (1 mm, same tree and semantics).
The database is checked to be unchanged by every step (checksums).

Usage (isolated profile, password only in the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python-exit-code 1 --python scripts/headless/test_t09_db_browse.py -- \
      <S.gml> <buildings_dir> <citydb_compose_dir> [--keep-schemas] [--reuse-schemas]
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
RESULTS = {}
ADDON = None
CONTAINER = "citydb-v5-dev"
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"
BROWSE, SAMPLE = "t09_browse", "t09_sample"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label + (f" - {detail}" if detail else ""))
    return cond


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


# --------------------------------------------------------------------------- scratch schemas
def _run(cmd, env=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    rc, out = _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])
    return out.strip()


def drop_schema(schema):
    _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
          "-v", f"schema_name={schema}", "-f", "drop-schema.sql"])


def create_schema(schema):
    drop_schema(schema)
    rc, out = _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                    "-v", f"schema_name={schema}", "-f", "create-schema.sql"])
    if rc != 0:
        raise RuntimeError(f"create-schema {schema} failed: {out[-400:]}")


def import_file(compose_dir, schema, path):
    """citydb-tool import (docker compose; the password comes from compose / the environment)."""
    folder, name = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    env = dict(os.environ, CITYDB_SCHEMA=schema, MSYS_NO_PATHCONV="1")
    rc, out = _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "-v", f"{folder}:/t09in",
                    "citydb-tool", "import", "citygml", f"/t09in/{name}"], env=env, cwd=compose_dir)
    if rc != 0 or "successfully completed" not in out:
        raise RuntimeError(f"citydb import {name} failed: {out[-600:]}")


def db_checksum():
    """Row counts, id sums and last change of the tables a browse could touch, per schema."""
    parts = []
    for s in (BROWSE, SAMPLE, "citydb"):
        parts.append(psql(
            f"SELECT '{s}', (SELECT count(*) || ':' || coalesce(sum(id), 0) || ':' || "
            f"coalesce(max(last_modification_date)::text, '') FROM {s}.feature), "
            f"(SELECT count(*) || ':' || coalesce(sum(id), 0) FROM {s}.property), "
            f"(SELECT count(*) || ':' || coalesce(sum(id), 0) FROM {s}.geometry_data)"))
    return "\n".join(parts)


# --------------------------------------------------------------------------- scene / target
def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False


def set_target(schema, label):
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("T09 local")
    if p is None:
        p = prefs.db_profiles.add()
        p.name = "T09 local"
    p.host, p.port, p.user = HOST, PORT, USER
    prefs.db_active_profile = p.name
    mod("citydb.credentials").store(p.server().key, SECRET)
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", schema
    sc.db_target_label, sc.db_target_srid = label, 25832
    return prefs


GAPS = []  # main-thread gaps of the last run_manual (5 ms sleeps between ticks)


def run_manual(start):
    """Run a job the way the modal timer does (50 ms ticks); returns (result, tick durations).

    Between ticks the main thread sleeps in 5 ms slices; how much longer a slice
    took (the worker holding the GIL) goes to ``GAPS``.
    """
    drv = start()
    if isinstance(drv, tuple):
        drv = drv[1]
    ticks = []
    GAPS.clear()
    while True:
        t = time.perf_counter()
        done = drv.tick()
        ticks.append(time.perf_counter() - t)
        if done:
            break
        for _ in range(10):
            t = time.perf_counter()
            time.sleep(0.005)
            GAPS.append(time.perf_counter() - t)
    return drv, ticks


def remove_pycache():
    """Cold start: no .pyc of the add-on (first session after install or update)."""
    import shutil
    root = os.path.dirname(sys.modules[ADDON].__file__)
    for d, _sub, _files in list(os.walk(root)):
        if os.path.basename(d) == "__pycache__":
            shutil.rmtree(d, ignore_errors=True)


def proxy_objects(collection):
    coll = bpy.data.collections.get(collection)
    return list(coll.objects) if coll else []


def db_roots():
    return [o for o in bpy.data.objects if o.get("cgml3_source_kind") == "citydb"]


# --------------------------------------------------------------------------- equivalence helpers
VOLATILE = ("cgml3_source", "cgml3_db_", "cgml3_objectid", "cgml3_loaded_lod", "cgml3_feature_id",
            "cgml3_browse", "gml_id", "_RNA_UI", "cycles", "cgml3_import", "cgml3_file", "CRS")


def _props(o):
    out = []
    for k in sorted(o.keys()):
        if any(k.startswith(v) for v in VOLATILE):
            continue
        v = o[k]
        if isinstance(v, (int, float, str, bool)):
            out.append((k, round(v, 6) if isinstance(v, float) else v))
    return tuple(out)


def tree_signature(root):
    world = bpy.context.scene.world
    off = (float(world.get("cgml_X-Origin", 0.0)), float(world.get("cgml_Y-Origin", 0.0)), float(world.get("cgml_Z-Origin", 0.0)))
    pts, polys, nodes = set(), 0, []
    for o in (root, *root.children_recursive):
        n_poly = len(o.data.polygons) if o.type == 'MESH' else 0
        nodes.append((o.type, _props(o), n_poly, len(o.children)))
        if o.type != 'MESH':
            continue
        polys += n_poly
        mw = o.matrix_world
        for v in o.data.vertices:
            w = mw @ v.co
            pts.add((round(w.x + off[0], 3), round(w.y + off[1], 3), round(w.z + off[2], 3)))
    return {"points": pts, "polygons": polys, "nodes": sorted(nodes, key=repr)}


def roots_by_gml_id():
    return {str(o["gml_id"]): o for o in bpy.data.objects
            if o.get("gml_id") and (o.parent is None or not o.parent.get("gml_id"))
            and not o.get("cgml3_browse_path")}


# --------------------------------------------------------------------------- cases
def case_browse_m(prefs):
    print("\n[T09] --- S set (300 buildings): query, pan, paging ---")
    fresh_scene()
    prefs = set_target(BROWSE, "T09 S")
    bc = mod("ops.browse_citydb")
    prefs.db_browse_limit = 100
    # 400 m x 300 m at the start of the S grid
    ext1 = (564820.0, 5934290.0, 565220.0, 5934590.0)
    drv, ticks = run_manual(lambda: bc.start_query(bpy.context, crs_extent=ext1, manual=True))
    s = bc.active_session(bpy.context)
    q = s.last_query
    st = q.get("stats", {})
    RESULTS["query_s"] = st.get("count_s", 0) + st.get("query_s", 0)
    RESULTS["query_total_s"] = st.get("seconds", 0)
    RESULTS["query_max_tick_ms"] = max(ticks) * 1000
    check(drv.error is None, "query finished", str(drv.error))
    check(q["count"] > 0 and q["new"] == q["count"] <= 100, "boxes for the buildings in the extent",
          f"count {q['count']}, new {q['new']}, area {q['area']:.3f} km²")
    check(RESULTS["query_s"] < 1.0, "count + page query < 1 s",
          f"count {st.get('count_s', 0) * 1000:.0f} ms + page {st.get('query_s', 0) * 1000:.0f} ms; "
          f"job {st.get('seconds', 0):.2f} s incl. connect/verify")
    check(max(ticks) < 0.1, "longest main-thread tick < 100 ms", f"{max(ticks) * 1000:.1f} ms over {len(ticks)} ticks")
    proxies = proxy_objects("CityDB Index")
    check(len(proxies) == 1, "one proxy object in 'CityDB Index'", str(len(proxies)))
    obj = proxies[0]
    check(obj.get("cgml3_browse_path", "").startswith("citydb://"), "proxy marks itself as an export helper")
    w = bpy.context.scene.world
    origin = (w["cgml_X-Origin"], w["cgml_Y-Origin"], w["cgml_Z-Origin"])
    check(origin[0] % 100 == 0 and origin[1] % 100 == 0 and w.get("cgml_CRS") == "EPSG:25832",
          "T01 origin (rounded 100 m) and CRS on the World", f"{origin} {w.get('cgml_CRS')}")
    # boxes aligned: proxy vertices + origin == the DB envelopes
    xs = [v.co.x + origin[0] for v in obj.data.vertices]
    ys = [v.co.y + origin[1] for v in obj.data.vertices]
    bx0 = min(r.bbox[0] for r in s.rows)
    by1 = max(r.bbox[4] for r in s.rows)
    check(abs(min(xs) - bx0) < 0.01 and abs(max(ys) - by1) < 0.01, "boxes sit on the DB envelopes (1 cm)",
          f"dx {abs(min(xs) - bx0) * 1000:.1f} mm, dy {abs(max(ys) - by1) * 1000:.1f} mm")
    check(len(obj.data.polygons) == len(s.rows) and s.preview() == "Bounding box",
          "one flat bbox rectangle per building, preview 'Bounding box' (no LoD0 in S)", s.preview())

    print("\n[T09] --- pan: only new boxes ---")
    n1 = len(s.rows)
    ext2 = (565020.0, 5934290.0, 565420.0, 5934590.0)  # half overlaps ext1
    run_manual(lambda: bc.start_query(bpy.context, crs_extent=ext2, manual=True))
    q2 = s.last_query
    ids = [r.id for r in s.rows]
    check(len(ids) == len(set(ids)), "no feature shown twice", f"{len(ids)} rows")
    check(0 < q2["new"] < q2["count"] and len(s.rows) == n1 + q2["new"], "second area adds only the new boxes",
          f"in view {q2['count']}, new {q2['new']}, total {len(s.rows)}")
    check(len(proxy_objects("CityDB Index")) == 1 and len(obj.data.polygons) == len(s.rows),
          "still one proxy, mesh grew")

    prefs.db_preview_footprint = True
    t = time.perf_counter()
    run_manual(lambda: bc.start_query(bpy.context, crs_extent=(564800.0, 5934850.0, 565400.0, 5935100.0),
                                      manual=True))
    st = s.last_query["stats"]
    feet = sum(1 for r in s.rows if r.footprint)
    RESULTS["footprint_s"] = st.get("footprint_s", 0.0)
    check(feet == s.last_query["new"] > 0 and "Footprint (ground surface)" in s.preview(),
          "optional ground-surface footprint (timed)",
          f"{feet} footprints in {st.get('footprint_s', 0) * 1000:.0f} ms, preview: {s.preview()}")
    prefs.db_preview_footprint = False

    print("\n[T09] --- limit + paging ---")
    prefs.db_browse_limit = 150
    full = (564000.0, 5933000.0, 568000.0, 5938000.0)
    before = len(s.rows)
    run_manual(lambda: bc.start_query(bpy.context, crs_extent=full, manual=True))
    q3 = s.last_query
    check(q3["count"] == 300 and q3["rows"] == 150 and q3["more"], "count over the limit, one page of 150",
          f"count {q3['count']}, rows {q3['rows']}, more {q3['more']}")
    check("Narrow the area or filter by type" in s.message, "limit message", s.message)
    res = bpy.ops.cgml3.db_browse_view(next_page=True)
    q4 = s.last_query
    check(res == {'FINISHED'} and q4["after_id"] == q3["last_id"] and q4["rows"] == 150,
          "Next page continues after the last id", f"after {q4['after_id']}, rows {q4['rows']}")
    ids = [r.id for r in s.rows]
    check(len(ids) == len(set(ids)) and len(s.rows) > before, "paging adds rows without duplicates",
          f"{before} -> {len(s.rows)}")
    prefs.db_browse_limit = 100
    return s


def case_load_six(s):
    print("\n[T09] --- select 6, Load LoD2 ---")
    bc = mod("ops.browse_citydb")
    pick = [r.id for r in s.rows[:6]]
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(map(str, pick)))
    check(len(s.selected) == 6, "6 selected")
    cold = f"{ADDON}.common.builder_support.v3.materials" not in sys.modules
    remove_pycache()
    drv, ticks = run_manual(lambda: bc.start_load(bpy.context, s, pick, 'LOD2', manual=True))
    RESULTS["first_load_max_gap_ms"] = max(GAPS or [0]) * 1000
    check(cold and RESULTS["first_load_max_gap_ms"] < 100,
          "first load, cold start (no __pycache__): main-thread gap < 100 ms",
          f"importer not loaded before: {cold}; largest gap {RESULTS['first_load_max_gap_ms']:.1f} ms "
          f"over {len(GAPS)} 5 ms sleeps")
    # the separate process compiles the whole common package, also modules the import never loads
    io_dir = os.path.join(os.path.dirname(sys.modules[ADDON].__file__), "common", "builder_support")
    io_src = [os.path.join(d, f) for d, _sub, files in os.walk(io_dir) for f in files if f.endswith(".py")]
    stale = [p for p in io_src if not bc._pyc_current(p)]
    check(io_src and not stale, "the importer's .pyc were written by the precompile process",
          f"{len(io_src) - len(stale)}/{len(io_src)} in common/builder_support/")
    ll = s.last_load
    RESULTS["export_s"] = ll.get("export_s", 0.0)
    RESULTS["import_s"] = ll.get("import_s", 0.0)
    RESULTS["load_wait_max_tick_ms"] = max(ticks[:-1] or [0]) * 1000
    check(ll.get("members") == 6, "export GML holds 6 top-level features", str(ll.get("members")))
    check(ll.get("features") == 6, "6 buildings imported and tagged", str(ll))
    check(max(ticks[:-1] or [0]) < 0.1, "main thread free while citydb-tool exports (< 100 ms ticks)",
          f"{RESULTS['load_wait_max_tick_ms']:.1f} ms over {len(ticks) - 1} ticks; export {ll.get('export_s', 0):.1f} s")
    RESULTS["import_max_step_ms"] = ll.get("max_step_s", 1.0) * 1000
    check(ll.get("max_step_s", 1.0) < 0.1 and ll.get("steps") == 7,
          "import runs one building per main-thread step (< 100 ms each)",
          f"{ll.get('steps')} steps (6 buildings + finish), longest {ll.get('max_step_s', 0) * 1000:.0f} ms, "
          f"total {ll.get('import_s', 0) * 1000:.0f} ms")
    roots = db_roots()
    check(sorted(int(o["cgml3_db_feature_id"]) for o in roots) == sorted(pick), "roots carry their feature.id")
    r0 = roots[0]
    check(r0.get("cgml3_source_kind") == "citydb" and r0.get("cgml3_source_profile") == "T09 local"
          and r0.get("cgml3_objectid") == r0.get("gml_id") and r0.get("cgml3_loaded_lod") == 2,
          "source kind / profile / objectid / loaded LoD on the root",
          f"{r0.get('cgml3_source_kind')} {r0.get('cgml3_source_profile')} {r0.get('cgml3_loaded_lod')}")
    loaded = bpy.data.collections.get("CityDB Loaded")
    check(loaded is not None and all(r0.name in loaded.all_objects for r0 in roots),
          "loaded buildings live in 'CityDB Loaded'")
    kids = [c.name for c in loaded.children] if loaded else []
    check(len(kids) == 1 and kids[0].startswith("CityDB sel_"), "one collection per load (steps merged)", str(kids))
    check(len(s.loaded) == 6, "boxes of loaded buildings marked loaded (hidden)", str(len(s.loaded)))
    check(not any(f.startswith("sel_") for f in os.listdir(bc._temp_dir or ".")), "temporary export removed")

    print("\n[T09] --- reload replaces, limit, unload ---")
    n_objs = len(bpy.data.objects)
    run_manual(lambda: bc.start_load(bpy.context, s, pick[:2], 'LOD2', manual=True))
    check(len(db_roots()) == 6 and len(bpy.data.objects) == n_objs, "loading again replaces, no duplicates",
          f"{len(db_roots())} roots, {n_objs} -> {len(bpy.data.objects)} objects")
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(str(r.id) for r in s.rows[:11]))
    try:
        res = bpy.ops.cgml3.db_browse_load(mode='LOD2')
    except RuntimeError as exc:
        res = str(exc)
    check("Select at most 10 buildings" in str(res), "selection limit 10 (preferences)", str(res)[:80])
    res = bpy.ops.cgml3.db_browse_unload(all=True)
    check(res == {'FINISHED'} and not db_roots() and not s.loaded, "Unload All removes them, boxes return")


def case_sample(buildings_dir):
    print("\n[T09] --- LoD2/LoD3 versions, Load by ID ---")
    fresh_scene()
    set_target(SAMPLE, "T09 sample")
    bc = mod("ops.browse_citydb")
    res = bpy.ops.cgml3.db_browse_view(use_extent=True, extent=(564700.0, 5934100.0, 565000.0, 5934500.0))
    s = bc.active_session(bpy.context)
    check(res == {'FINISHED'} and len(s.rows) == 6, "6 rows (3 buildings x LoD2/LoD3)", str(len(s.rows)))
    groups = mod("citydb.catalog").group_versions(s.rows)
    yaz = groups.get("DEHHALKAJ0000yAZ", [])
    label = mod("citydb.catalog").versions_label(yaz)
    check(len(yaz) == 2 and label.startswith("2 versions: LoD2 (id ") and ", LoD3 (id " in label,
          "DEHHALKAJ0000yAZ grouped", label)
    lod3_id = max(r.id for r in yaz if 3 in r.lod_levels)
    # both boxes selected (they overlap), Load Highest -> the LoD3 row
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(str(r.id) for r in yaz))
    res = bpy.ops.cgml3.db_browse_load(mode='HIGHEST')
    roots = db_roots()
    check(res == {'FINISHED'} and len(roots) == 1 and int(roots[0]["cgml3_db_feature_id"]) == lod3_id
          and roots[0]["cgml3_loaded_lod"] == 3, "LoD3 asked: the LoD3 feature.id is loaded",
          f"{[(o['cgml3_db_feature_id'], o['cgml3_loaded_lod']) for o in roots]}, wanted {lod3_id}")
    check(s.last_load.get("skipped") == [min(r.id for r in yaz)], "other version reported, not loaded",
          str(s.last_load.get("warnings")))
    lod2_id = min(r.id for r in yaz)
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=str(lod2_id))
    bpy.ops.cgml3.db_browse_load(mode='LOD2')
    roots = db_roots()
    check(len(roots) == 1 and int(roots[0]["cgml3_db_feature_id"]) == lod2_id, "LoD2 version replaces the LoD3 one",
          str([o["cgml3_db_feature_id"] for o in roots]))

    res = bpy.ops.cgml3.db_browse_find(text="DEHHALKAJ0000yAZ")
    check(res == {'FINISHED'} and len(s.id_matches) == 2 and "2 versions" in s.message,
          "Load by objectid with 2 rows lists them", s.message)
    res = bpy.ops.cgml3.db_browse_find(text=str(lod3_id))
    roots = db_roots()
    check(res == {'FINISHED'} and [int(o["cgml3_db_feature_id"]) for o in roots] == [lod3_id],
          "Load by feature.id loads exactly that row", str([o["cgml3_db_feature_id"] for o in roots]))
    try:
        res = bpy.ops.cgml3.db_browse_find(text="999999")
    except RuntimeError as exc:
        res = str(exc)
    check("Feature not found" in s.message, "unknown id: 'Feature not found'", s.message)
    try:
        res = bpy.ops.cgml3.db_browse_find(text="1; DROP TABLE feature")
        msg = str(res)
    except RuntimeError as exc:
        msg = str(exc)
    check("Invalid ID" in msg, "'1; DROP TABLE feature' rejected before any query", msg.strip()[:90])
    try:
        res = bpy.ops.cgml3.db_browse_select(feature_ids="1; DROP TABLE feature")
        msg = str(res)
    except RuntimeError as exc:
        msg = str(exc)
    check("Not a feature id" in msg, "select by id rejects it too", msg.strip()[:90])
    try:
        bc.start_load(bpy.context, s, ["13); DELETE FROM feature; --"], 'LOD2')
        msg = "accepted"
    except Exception as exc:
        msg = str(exc)
    check("Not a feature id" in msg, "load rejects non-integer ids", msg)
    bpy.context.window_manager.cgml3_db_type_filter = "Building; DROP"
    try:
        res = bpy.ops.cgml3.db_browse_view(use_extent=True, extent=(564700.0, 5934100.0, 565000.0, 5934500.0))
        msg = str(res)
    except RuntimeError as exc:
        msg = str(exc)
    check("Invalid feature type" in msg, "type filter validated", msg.strip()[:90])
    bpy.context.window_manager.cgml3_db_type_filter = ""

    print("\n[T09] --- equivalence: same buildings from the GML file (T08) and the DB ---")
    src = os.path.join(buildings_dir, "buildings_lod2_citygml_3_0.gml")
    fresh_scene()
    os.environ["CGML3_INDEX_CACHE_DIR"] = os.path.join(bpy.app.tempdir or os.environ.get("TEMP", "."), "t09_idx")
    bpy.ops.cgml3.browse_gml_file(filepath=src, use_cache=False)
    bpy.ops.cgml3.browse_select(action='ALL')
    res = bpy.ops.cgml3.browse_load(mode='LOD2')
    ref = {k: tree_signature(v) for k, v in roots_by_gml_id().items()}
    check(res == {'FINISHED'} and len(ref) == 3, "GML: 3 buildings via file browse", str(len(ref)))
    nodes = [n for sig in ref.values() for n in sig["nodes"]]
    with_props = [n for n in nodes if n[1]]
    keys = sorted({k for n in with_props for k, _v in n[1]})
    check(len(with_props) >= len(nodes) // 2 and keys, "semantic properties are part of the comparison",
          f"{len(with_props)}/{len(nodes)} objects; keys {', '.join(keys[:8])}")

    fresh_scene()
    set_target(SAMPLE, "T09 sample")
    bpy.ops.cgml3.db_browse_view(use_extent=True, extent=(564700.0, 5934100.0, 565000.0, 5934500.0))
    s = bc.active_session(bpy.context)
    lod2_rows = [r.id for r in s.rows if r.lod_levels == (2,)]
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(map(str, lod2_rows)))
    res = bpy.ops.cgml3.db_browse_load(mode='LOD2')
    got = {k: tree_signature(v) for k, v in roots_by_gml_id().items()}
    check(res == {'FINISHED'} and sorted(got) == sorted(ref), "DB: the same 3 buildings", f"{sorted(got)}")
    worst = []
    for gid, a in ref.items():
        b = got.get(gid)
        if b is None:
            continue
        check(a["polygons"] == b["polygons"], f"{gid}: polygon count", f"{a['polygons']} vs {b['polygons']}")
        diff = (a["points"] - b["points"]) | (b["points"] - a["points"])
        check(not diff, f"{gid}: vertex coordinates within 1 mm", f"{len(diff)} differ of {len(a['points'])}")
        check(a["nodes"] == b["nodes"], f"{gid}: object tree and semantic properties",
              _first_diff(a["nodes"], b["nodes"]))
        worst.append(len(diff))
    RESULTS["equivalence_points_differing"] = sum(worst)


def _first_diff(a, b):
    for x, y in zip(a, b):
        if x != y:
            return f"{x} != {y}"[:300]
    return f"{len(a)} vs {len(b)} nodes" if len(a) != len(b) else ""


def main():
    a = args()
    s_gml, buildings_dir, compose_dir = a[0], a[1], a[2]
    if not SECRET:
        print("Set CGML3_TEST_DB_PASSWORD")
        sys.exit(2)
    t0 = time.perf_counter()
    if "--reuse-schemas" not in a:
        print("[T09] creating scratch schemas ...")
        create_schema(BROWSE)
        import_file(compose_dir, BROWSE, s_gml)
        create_schema(SAMPLE)
        for f in ("buildings_lod2_citygml_3_0.gml", "buildings_lod3_citygml_3_0.gml"):
            import_file(compose_dir, SAMPLE, os.path.join(buildings_dir, f))
        print(f"[T09] schemas ready in {time.perf_counter() - t0:.0f} s")
    before = db_checksum()
    try:
        s = case_browse_m(None)
        case_load_six(s)
        case_sample(buildings_dir)
        after = db_checksum()
        check(before == after, "database unchanged by browsing and loading", after if before != after else "")
        texts = "\n".join(t.as_string() for t in bpy.data.texts)
        check(SECRET not in texts, "no password in Blender text logs")
    finally:
        if "--keep-schemas" not in a:
            drop_schema(BROWSE)
            drop_schema(SAMPLE)
            left = psql(f"SELECT count(*) FROM pg_namespace WHERE nspname IN ('{BROWSE}', '{SAMPLE}')")
            print(f"\n[T09] scratch schemas dropped: {left == '0'}")
    print(f"\n[T09] results: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                          for k, v in RESULTS.items()))
    print(f"[T09] {time.perf_counter() - t0:.0f} s")
    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] T09 DB browse checks satisfied")


try:
    main()
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
