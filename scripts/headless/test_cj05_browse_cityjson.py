"""CJ-05: Browse first for CityJSON - a 300-building file as one document and as CityJSONSeq.

- boxes for all 300 buildings, 5 picked and loaded in LoD 2.2; their objects (ids, surface classes,
  properties) and coordinates (1 mm) equal the same buildings from a whole-file open
- Unload brings the boxes back; the loaded scene exports as CityGML 3.0, XSD-valid
- LoD by text: a building without the chosen LoD is skipped and counted, never loaded in another LoD
- the session origin and CRS come from the whole file, once; cgml_source_format = CityJSON
- dispatch: CityJSONSeq always browses (no Load all), above 30 buildings Browse first, up to 30 the
  document opens directly; the drop path (browse operator) previews CityJSON itself
- the main thread is not blocked while the index is built (tick loop as in test_t09 / bench_import);
  cancel closes the session
- CJ-02 leftovers: the file browser's LoD menu on a file above the threshold

The files are made here (scripts/tests/cj05_fixture.py); nothing large is in the repo.

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj05_browse_cityjson.py
"""
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402

import cj05_fixture as fx  # noqa: E402
from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
ADDON = None
N = 300
PICKS = [1, 5, 7, 11, 44]       # dotted id (B.001), room child, part, installation, installation
TOLERANCE_M = 0.001
EXCLUDED_PROPS = {"cgml3_source_kind", "cgml3_source_path", "cgml3_feature_id",
                  "cgml3_cityjson_source", "cgml3_cityjson_fingerprint"}   # CJ-06 bookkeeping
SMALL_SEQ = os.path.join(HERE, "..", "..", "test_files", "CityJSON", "cj05_seq_small.city.jsonl")


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")


def call(op, **kw):
    """Operator result; an ERROR report raises in background mode."""
    try:
        return op(**kw), ""
    except RuntimeError as exc:
        return {'CANCELLED'}, str(exc)


def browse(path, use_cache=False):
    res, _ = call(bpy.ops.cgml3.browse_gml_file, filepath=path, use_cache=use_cache)
    return res, mod("ops.browse_gml").get_session(path)


def roots():
    return [o for o in bpy.data.objects if o.get("cgml3_feature_id")]


def buildings():
    return [o for o in bpy.data.objects if o.get("cgml3_feature") == "Building"]


def proxies():
    return [o for o in bpy.data.objects if o.get("cgml3_browse_path")]


def world_georef():
    w = bpy.data.worlds.get("World")
    return {k: w.get(k) for k in ("cgml_CRS", "cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin")}


def shown_boxes(s):
    dg = bpy.context.evaluated_depsgraph_get()
    return len(s.proxy().evaluated_get(dg).data.polygons)


def signature(root):
    """Objects, custom properties (ids, feature / surface class) and world coordinates of a building."""
    objs = [root, *root.children_recursive]
    w = bpy.data.worlds["World"]
    off = tuple(float(w.get(k, 0.0)) for k in ("cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin"))
    pts, props = [], []
    for o in objs:
        props.append(tuple(sorted((k, str(v)) for k, v in o.items()
                                  if isinstance(v, (str, int, float)) and k not in EXCLUDED_PROPS)))
        if o.type == 'MESH':
            for v in o.data.vertices:
                p = o.matrix_world @ v.co
                pts.append((p.x + off[0], p.y + off[1], p.z + off[2]))
    return {"objects": len(objs), "props": sorted(props), "points": sorted(pts),
            "classes": sorted((str(o.get("gml_id")), str(o.get("cgml3_feature"))) for o in objs),
            "polygons": sum(len(o.data.polygons) for o in objs if o.type == 'MESH')}


def same(a, b):
    if a is None or b is None:
        return False, "building missing"
    if a["classes"] != b["classes"]:
        return False, f"ids / classes differ: {sorted(set(a['classes']) ^ set(b['classes']))[:4]}"
    if a["objects"] != b["objects"] or a["polygons"] != b["polygons"] or a["props"] != b["props"]:
        diff = sorted(set(a["props"]) ^ set(b["props"]))[:2]
        return False, f"objects {a['objects']}/{b['objects']} polygons {a['polygons']}/{b['polygons']} props {diff}"
    if len(a["points"]) != len(b["points"]):
        return False, f"points {len(a['points'])}/{len(b['points'])}"
    worst = max((max(abs(x - y) for x, y in zip(p, q)) for p, q in zip(a["points"], b["points"])), default=0.0)
    return worst <= TOLERANCE_M, f"max diff {worst * 1000:.3f} mm over {len(a['points'])} points"


class Recorder:
    """Layout stand-in: records ``(operator id, button text, properties set on it)``."""

    def __init__(self, ops):
        object.__setattr__(self, "ops", ops)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **kw):
        return Recorder(self.ops)

    row = column = box = _child

    def operator(self, idname, **kw):
        props = types.SimpleNamespace()
        self.ops.append((idname, kw.get("text", ""), props))
        return props

    def label(self, *a, **kw):
        pass

    separator = prop = label


# --------------------------------------------------------------------------- cases
def case_browse_load(label, path, out):
    print(f"\n[CJ-05] --- {label}: boxes, pick 5, load LoD 2.2, export, unload ---")
    fresh_scene()
    bm = mod("ops.browse_gml")
    t0 = time.perf_counter()
    res, s = browse(path)
    check(res == {'FINISHED'} and s is not None and s.complete, f"{label}: indexed", f"{res} {time.perf_counter() - t0:.2f} s")
    check(s.source_format == label and s.index.root_name == label, f"{label}: session format", s.index.root_name)
    check(len(s.entries) == N, f"{label}: 300 buildings in the index", str(len(s.entries)))
    check(s.index.skipped_kinds == {"WaterBody": 2, "BuildingRoom": 1}, f"{label}: out-of-scope types counted, not indexed",
          str(s.index.skipped_kinds))
    check(bm.not_indexed_text(s) == "not indexed: 1 BuildingRoom, 2 WaterBody", f"{label}: the count is reported",
          bm.not_indexed_text(s))
    p = proxies()
    check(len(p) == 1 and len(p[0].data.polygons) == len(s.arrays.face_feature) >= N
          and set(s.arrays.face_feature) == set(range(N)), f"{label}: one proxy object with an outline for every building",
          f"{len(p)} proxies, {len(p[0].data.polygons) if p else 0} faces")
    check(shown_boxes(s) == len(s.arrays.face_feature), f"{label}: all boxes shown")
    check(s.preview() == "Footprint (ground surface) + Bounding box", f"{label}: preview label", s.preview())
    ext = fx.extent(N)
    origin = mod("shared.import_origin").center_origin(ext[:3], ext[3:])
    geo = world_georef()
    check(geo == {"cgml_CRS": "EPSG:25832", "cgml_X-Origin": origin[0], "cgml_Y-Origin": origin[1],
                  "cgml_Z-Origin": origin[2]} and tuple(s.origin) == origin,
          f"{label}: World CRS and origin from the whole file", f"{geo} / {origin}")

    ids = [s.entries[i].gml_id for i in PICKS]
    bpy.ops.cgml3.browse_select(action='SET', ids=",".join(ids))
    n, lods, _preview, _risks = bm.selection_summary(s)
    check((n, lods) == (5, "1.2, 2.2"), f"{label}: picked 5, LoD texts of the file", f"{n} {lods}")
    ops = []
    mod("ui.browse_panel").draw_show_unshow(Recorder(ops), bpy.context, s)
    lod_buttons = [(text, getattr(pr, "lod", None)) for op, text, pr in ops if op == "cgml3.browse_load" and hasattr(pr, "lod")]
    check(lod_buttons == [("2.2", "2.2"), ("1.2", "1.2")], f"{label}: Show offers the file's LoDs, default first",
          str(lod_buttons))
    t0 = time.perf_counter()
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    secs = time.perf_counter() - t0
    got = roots()
    check(res == {'FINISHED'} and sorted(r["cgml3_feature_id"] for r in got) == sorted(ids),
          f"{label}: only the 5 picked buildings came in", f"{res} {err} {len(got)} roots in {secs:.2f} s")
    check(len(buildings()) == 5 and all(r.get("cgml3_source_kind") == "file"
                                        and os.path.samefile(r["cgml3_source_path"], path) for r in got),
          f"{label}: roots carry cgml3_source_kind/path/feature_id")
    check(any(r.get("gml_id") == "B.001" and "cgml3_cityjson_id" not in r for r in got),
          f"{label}: the file's own id is the gml_id (B.001), nothing mapped")
    check(bpy.data.worlds["World"].get("cgml_source_format") == "CityJSON", f"{label}: World cgml_source_format")
    check(world_georef() == geo, f"{label}: Load keeps the session origin", str(world_georef()))
    last = s.last_load
    check(last.get("not_loaded") == ["1 BuildingRoom", "5 LoD1.2 geometries"] and last.get("lod") == "2.2",
          f"{label}: summary counts what was not loaded (as CJ-02)", str(last.get("not_loaded")))
    check("not loaded: 1 BuildingRoom, 5 LoD1.2 geometries" in last.get("message", ""), f"{label}: the message says it",
          str(last.get("message")))
    if label == "CityJSONSeq":
        check(0 < last["bytes_read"] < os.path.getsize(path) / 20, f"{label}: only the picked lines were read",
              f"{last['bytes_read']:,} of {os.path.getsize(path):,} bytes")
    check(shown_boxes(s) == sum(1 for f in s.arrays.face_feature if f not in s.loaded) < len(s.arrays.face_feature),
          f"{label}: loaded boxes are hidden")
    sigs = {r["cgml3_feature_id"]: signature(r) for r in got}

    gml = os.path.join(out, f"cj05_{label}.gml")
    res, err = call(bpy.ops.cgml3.export_gml_file, filepath=gml, citygml_version="3.0")
    ok, msg = mod("ops.validate").validate_citygml3_xsd(gml)
    skipped = any(word in msg for word in ("Hinweis", "Warnung", "übersprungen", "skipped"))
    text = open(gml, encoding="utf-8").read() if os.path.isfile(gml) else ""
    check(res == {'FINISHED'} and ok and not skipped, f"{label}: the loaded scene exports as CityGML 3.0, XSD-valid",
          f"{res} {err} {msg[:300]}")
    check(text.count("<bldg:Building ") == 5 and "CityGML Index" not in text, f"{label}: export has the 5 buildings",
          str(text.count("<bldg:Building ")))

    res, err = call(bpy.ops.cgml3.browse_unload, all=True)
    check(res == {'FINISHED'} and not roots() and not buildings(), f"{label}: Unload removes the buildings", f"{res} {err}")
    check(shown_boxes(s) == len(s.arrays.face_feature) and not s.loaded, f"{label}: Unload brings the boxes back")

    # K6: the LoD is chosen by its text; building 3 has only LoD 1.2
    two = [s.entries[3].gml_id, s.entries[4].gml_id]
    bpy.ops.cgml3.browse_select(action='SET', ids=",".join(two))
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    check(res == {'FINISHED'} and [r["cgml3_feature_id"] for r in roots()] == two[1:]
          and "no LoD2.2: 1" in s.last_load["warnings"] and "no LoD2.2: 1" in s.last_load["message"],
          f"{label}: a building without LoD 2.2 is skipped and counted", f"{res} {err} {s.last_load.get('message')}")
    bpy.ops.cgml3.browse_unload(all=True)
    bpy.ops.cgml3.browse_select(action='SET', ids=two[0])
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    check(res == {'CANCELLED'} and not roots() and "LoD2.2" in err, f"{label}: nothing is loaded in another LoD",
          f"{res} {err}")
    res, err = call(bpy.ops.cgml3.browse_load, lod="1.2")
    check(res == {'FINISHED'} and len(roots()) == 1 and s.last_load["lod"] == "1.2", f"{label}: LoD 1.2 when chosen")
    bpy.ops.cgml3.browse_unload(all=True)
    res = bm.run_show(True)     # the S key: the default LoD of what is picked
    check(res == {'FINISHED'} and len(roots()) == 1 and s.last_load["lod"] == "1.2",
          f"{label}: Show (S) takes the picked building's own default LoD", str(s.last_load.get("lod")))
    bpy.ops.cgml3.browse_close()
    check(not proxies(), f"{label}: Close removes the preview")
    return sigs


def case_direct(doc_path, sigs_by_form):
    print("\n[CJ-05] --- whole-file open of the 300-building document (Load all, LoD 2.2) ---")
    fresh_scene()
    items = sys.modules[ADDON]._cityjson_lod_items(types.SimpleNamespace(filepath=doc_path), None)
    # the menu comes from the sniff (a token scan, CJ-02): it also sees the WaterBody's "1"; the Browse
    # path takes its LoD list from the index instead (case_browse_load)
    check([i[0] for i in items] == ["2.2", "1", "1.2"], "file browser LoD menu: LoD texts of the file, default first",
          str([i[0] for i in items]))
    t0 = time.perf_counter()
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=doc_path, load_mode='ALL', cityjson_lod="2.2")
    # LoD 2.2 of its own, or of a BuildingPart (the Building is then the parts' container)
    n22 = sum(1 for i in range(N) if fx.has_lod22(i) or fx.has_part(i))
    check(res == {'FINISHED'} and len(buildings()) == n22, "Load all above the threshold builds the LoD 2.2 buildings",
          f"{res} {err} {len(buildings())}/{n22} in {time.perf_counter() - t0:.1f} s")
    by_id = {o["gml_id"]: o for o in buildings()}
    direct = {gid: signature(o) for gid, o in by_id.items() if any(gid in s for s in sigs_by_form.values())}
    for form, sigs in sigs_by_form.items():
        check(len(sigs) == 5, f"{form}: 5 signatures", str(len(sigs)))
        for gid, sig in sigs.items():
            ok, detail = same(sig, direct.get(gid))
            check(ok, f"{form} {gid}: ids, surface classes and coordinates as from the whole-file open", detail)
    walls = sum(1 for o in by_id[next(iter(direct))].children_recursive if o.get("cgml3_feature") == "WallSurface")
    check(walls >= 4, "the compared buildings have surface classes", str(walls))


def case_lod_menu_above_threshold(out):
    print("\n[CJ-05] --- CJ-02 leftover: the LoD chosen in the file browser, file above 30 buildings ---")
    fresh_scene()
    path = fx.write_document(os.path.join(out, "cj05_b40.city.json"), 40)
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=path, load_mode='ALL', cityjson_lod="1.2")
    b = buildings()
    lods = {o.get("cgml3_feature") for o in bpy.data.objects if o.get("cgml3_feature")}
    check(res == {'FINISHED'} and len(b) == 40 and "WallSurface" not in lods,
          "LoD 1.2 chosen: all 40 buildings, without the LoD 2.2 surfaces", f"{res} {err} {len(b)} {sorted(lods)}")


def case_dispatch(doc_path, seq_path, out):
    print("\n[CJ-05] --- dispatch: Open button, Load all limits, drop path ---")
    bm_get = lambda p: mod("ops.browse_gml").get_session(p)  # noqa: E731
    fresh_scene()
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=seq_path)
    s = bm_get(seq_path)
    check(res == {'FINISHED'} and s is not None and s.complete and len(s.entries) == N and not buildings(),
          "Open on CityJSONSeq: Browse first, nothing imported", f"{res} {err}")
    check(s.from_cache, "the Seq index comes from the cache (written when it was first indexed)")
    bpy.ops.cgml3.browse_select(action='SET', ids=s.entries[7].gml_id)
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    check(res == {'FINISHED'} and len(roots()) == 1, "a cached Seq index loads a building", f"{res} {err}")
    bpy.ops.cgml3.browse_close()

    fresh_scene()
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=seq_path, load_mode='ALL')
    check(res == {'CANCELLED'} and "Browse first only" in err and not buildings() and bm_get(seq_path) is None,
          "CityJSONSeq has no Load all", f"{res} {err}")
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=doc_path, load_mode='BROWSE')
    s = bm_get(doc_path)
    check(res == {'FINISHED'} and s is not None and len(s.entries) == N and not buildings(),
          "Open with Browse first on the document", f"{res} {err}")
    bpy.ops.cgml3.browse_close()

    co = mod("shared.cityjson_open")
    sniff = mod("shared.citygml_sniff").sniff_file
    check(co.open_decision(sniff(doc_path))[0] == "browse" and co.load_all_refusal(sniff(doc_path)) == "",
          "interactive: 300 buildings ask Browse first / Load all (Load all within its limits)")
    check(co.open_decision(sniff(seq_path))[0] == "browse" and co.load_all_refusal(sniff(seq_path)) != "",
          "interactive: CityJSONSeq offers Browse first only")

    fresh_scene()
    small = fx.write_document(os.path.join(out, "cj05_b6.city.json"), 6)
    res, err = call(bpy.ops.cgml3.import_gml_file, filepath=small)
    check(res == {'FINISHED'} and len(buildings()) == 5 and bm_get(small) is None,
          "up to 30 buildings the document opens directly, as in CJ-02", f"{res} {err} {len(buildings())}")
    fresh_scene()
    check(".jsonl" in bpy.types.CGML3_FH_citygml.bl_file_extensions
          and bpy.types.CGML3_FH_citygml.bl_import_operator == "cgml3.browse_gml_file", "drop handler takes .json/.jsonl")
    res, s = browse(small)
    check(res == {'FINISHED'} and s is not None and len(s.entries) == 6 and not buildings(),
          "drop path (browse operator) previews CityJSON itself", f"{res}")
    bpy.ops.cgml3.browse_close()
    other = os.path.join(out, "cj05_other.json")
    with open(other, "w", encoding="utf-8") as f:
        f.write('{"type": "FeatureCollection", "features": []}')
    res, err = call(bpy.ops.cgml3.browse_gml_file, filepath=other)
    check(res == {'CANCELLED'} and "Not a CityJSON" in err, "other JSON is refused", f"{res} {err}")
    res, s = browse(SMALL_SEQ)
    check(s is not None and len(s.entries) == 6, "the small Seq fixture of the repo browses", str(res))
    bpy.ops.cgml3.browse_close()


def run_manual(path):
    """Index like the modal timer (50 ms ticks); returns (session, tick durations, main-thread sleep overshoots)."""
    bm, job = mod("ops.browse_gml"), mod("common.background_job")
    session, drv = bm.start_browse(bpy.context, path, use_cache=False, manual=True)
    ticks, gaps = [], []
    while True:
        t = time.perf_counter()
        done = drv.tick()
        ticks.append(time.perf_counter() - t)
        if done:
            break
        for _ in range(int(job.TICK_INTERVAL_S / 0.005)):
            t = time.perf_counter()
            time.sleep(0.005)
            gaps.append(time.perf_counter() - t - 0.005)
    return session, drv, ticks, gaps


def case_non_blocking(label, path, n=N, max_wait=0.1):
    print(f"\n[CJ-05] --- {label}, {n} buildings ({os.path.getsize(path) / 2**20:.1f} MB): "
          "the main thread while the index is built ---")
    fresh_scene()
    s, drv, ticks, gaps = run_manual(path)
    check(drv.error is None and s.complete and len(s.entries) == n, f"{label}: indexed in the worker thread",
          f"{drv.error} {len(s.entries)} in {drv.stats.get('seconds', 0):.2f} s, {len(ticks)} ticks")
    check(max(ticks) < 0.1, f"{label}: longest main-thread tick < 100 ms", f"{max(ticks) * 1000:.1f} ms over {len(ticks)} ticks")
    check(max(gaps or [0]) < max_wait, f"{label}: the worker never holds the main thread for {max_wait * 1000:.0f} ms",
          f"longest wait {max(gaps or [0]) * 1000:.1f} ms over {len(gaps)} 5 ms sleeps")
    bpy.ops.cgml3.browse_close()


def case_cancel(out):
    print("\n[CJ-05] --- cancel while indexing ---")
    fresh_scene()
    bm, job = mod("ops.browse_gml"), mod("common.background_job")
    path = fx.write_seq(os.path.join(out, "cj05_b6000.city.jsonl"), 6000)
    session, drv = bm.start_browse(bpy.context, path, use_cache=False, manual=True)
    drv.tick()
    drv.job.cancel()
    t0 = time.perf_counter()
    while not drv.tick() and time.perf_counter() - t0 < 30:
        time.sleep(job.TICK_INTERVAL_S)
    check(drv.finished and drv.job.cancelled() and bm.get_session(path) is None and not proxies(),
          "cancel stops the index and closes the session",
          f"finished {drv.finished}, {len(session.entries)} of 6000 indexed, session {bm.get_session(path)}")
    check(len(session.entries) < 6000, "the index stopped before the end of the file", str(len(session.entries)))
    check(not job.is_running(), "the next job can start")
    os.remove(path)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj05_")
    os.makedirs(out, exist_ok=True)
    cache = os.path.join(out, "index_cache")
    shutil.rmtree(cache, ignore_errors=True)
    os.environ["CGML3_INDEX_CACHE_DIR"] = cache
    doc_path = fx.write_document(os.path.join(out, "cj05_b300.city.json"), N)
    seq_path = fx.write_seq(os.path.join(out, "cj05_b300.city.jsonl"), N)
    print(f"[CJ-05] document {os.path.getsize(doc_path) / 2**20:.2f} MB, seq {os.path.getsize(seq_path) / 2**20:.2f} MB")

    sigs = {"CityJSON": case_browse_load("CityJSON", doc_path, out),
            "CityJSONSeq": case_browse_load("CityJSONSeq", seq_path, out)}
    case_direct(doc_path, sigs)
    case_lod_menu_above_threshold(out)
    case_dispatch(doc_path, seq_path, out)
    case_non_blocking("CityJSON", doc_path)
    case_non_blocking("CityJSONSeq", seq_path)
    # 300 buildings index within two ticks; a longer job shows whether the worker lets the main thread run
    big = [fx.write_document(os.path.join(out, "cj05_b6000.city.json"), 6000),
           fx.write_document(os.path.join(out, "cj05_b6000_one_line.city.json"), 6000, indent=None),
           fx.write_seq(os.path.join(out, "cj05_b6000_nb.city.jsonl"), 6000)]
    for name, path in zip(("CityJSON", "CityJSON on one line", "CityJSONSeq"), big):
        # a single document stays in memory for Load: while it grows, Python's own garbage collector stops
        # every thread for its full passes (measured 110-150 ms at 16 MB); one json.load would hold ~500 ms
        case_non_blocking(name, path, 6000, max_wait=0.1 if name == "CityJSONSeq" else 0.3)
        os.remove(path)
    case_cancel(out)

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    print("\n[ALL PASS] CJ-05 browse checks satisfied")


main()
