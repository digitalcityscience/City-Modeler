"""T08: Browse first - index, one proxy object, load / unload the selection,
equivalence with Load all, LoD0 vs bounding box, CityGML 1.0, cache, limit.

Usage (isolated profile, see tickets/README.md):
  blender -b --python-exit-code 1 --python scripts/headless/test_t08_browse.py -- \
      <perf_data_dir> <buildings_dir> <out_dir> [--skip-large]

<perf_data_dir> holds S/S-noenv/S-lod0.gml (scripts/perf/make_perf_sets.py)
and real/hh2026/tiles/LoD2_32_564_5934_1_HH.gml (CityGML 1.0). The index
cache goes to <out_dir>/index_cache. Exits 1 when any check fails.
"""
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
ADDON = None
TOLERANCE_M = 0.001
HH_TILE = os.path.join("real", "hh2026", "tiles", "LoD2_32_564_5934_1_HH.gml")
EXCLUDED_PROPS = {"cgml3_source_kind", "cgml3_source_path", "cgml3_feature_id"}


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def browse_module():
    return sys.modules[f"{ADDON}.ops.browse_gml"]


def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")


def browse(path, use_cache=True):
    t0 = time.perf_counter()
    res = bpy.ops.cgml3.browse_gml_file(filepath=path, use_cache=use_cache)
    s = browse_module().get_session(path)
    return res, s, time.perf_counter() - t0


def select_ids(ids):
    return bpy.ops.cgml3.browse_select(action='SET', ids=",".join(ids))


def proxies():
    return [o for o in bpy.data.objects if o.get("cgml3_browse_path")]


def loaded_roots():
    return [o for o in bpy.data.objects if o.get("cgml3_feature_id")]


def feature_roots_by_gml_id(ids):
    """Top-level object of each feature after a full import."""
    wanted = set(ids)
    out = {}
    for o in bpy.data.objects:
        gid = o.get("gml_id")
        if gid in wanted:
            p = o.parent
            while p is not None and p.get("gml_id") not in wanted:
                p = p.parent
            if p is None:
                out[gid] = o
    return out


def signature(root):
    """World coordinates, object count and custom properties of a feature tree."""
    objs = [root, *root.children_recursive]
    pts = []
    props = []
    for o in objs:
        props.append(tuple(sorted((k, str(v)) for k, v in o.items()
                                  if isinstance(v, (str, int, float)) and k not in EXCLUDED_PROPS)))
        if o.type == 'MESH':
            mw = o.matrix_world
            for v in o.data.vertices:
                w = mw @ v.co
                pts.append((w.x, w.y, w.z))
    world = bpy.context.scene.world
    off = (float(world.get("cgml_X-Origin", 0.0)), float(world.get("cgml_Y-Origin", 0.0)), float(world.get("cgml_Z-Origin", 0.0)))
    pts = sorted((round(p[0] + off[0], 2), round(p[1] + off[1], 2), round(p[2] + off[2], 2), p[0] + off[0],
                  p[1] + off[1], p[2] + off[2]) for p in pts)
    return {"objects": len(objs), "props": sorted(props), "points": [p[3:] for p in pts],
            "polygons": sum(len(o.data.polygons) for o in objs if o.type == 'MESH')}


def compare_signatures(a, b):
    if a["objects"] != b["objects"] or a["polygons"] != b["polygons"] or a["props"] != b["props"]:
        return False, f"objects {a['objects']}/{b['objects']} polygons {a['polygons']}/{b['polygons']}"
    if len(a["points"]) != len(b["points"]):
        return False, f"points {len(a['points'])}/{len(b['points'])}"
    worst = max((max(abs(x - y) for x, y in zip(p, q)) for p, q in zip(a["points"], b["points"])), default=0.0)
    return worst <= TOLERANCE_M, f"max diff {worst * 1000:.3f} mm over {len(a['points'])} points"


# --------------------------------------------------------------------------- checks
def test_large_index(perf_dir):
    print("\n[T08] --- L: index, one proxy object, load 6, cache ---")
    fresh_scene()
    path = os.path.join(perf_dir, "S.gml")
    res, s, secs = browse(path, use_cache=False)
    check(res == {'FINISHED'} and s is not None and s.complete, "S indexed", f"{res} {secs:.1f} s")
    check(len(s.entries) == 300, "300 features in the index", str(len(s.entries)))
    check(secs <= 30.0, "Index of S within 30 s", f"{secs:.1f} s")
    check(len(proxies()) == 1, "Exactly one proxy object", str(len(proxies())))
    coll = bpy.data.collections.get("CityGML Index")
    check(coll is not None and len(coll.objects) == 1, "'CityGML Index' collection holds one object")
    p = proxies()[0]
    n_faces = len(s.arrays.face_feature)
    check(len(p.data.polygons) == n_faces and set(s.arrays.face_feature) == set(range(300)),
          "Proxy mesh: flat faces of every feature", str(len(p.data.polygons)))
    check(p.hide_render and p.hide_select, "Proxy hidden in render and not selectable")
    check(s.preview() == "Footprint (ground surface)", "S preview is the ground footprint (B14)", s.preview())

    ids = [e.gml_id for e in random.Random(8).sample(s.entries, 6)]
    select_ids(ids)
    t0 = time.perf_counter()
    res = bpy.ops.cgml3.browse_load(mode='LOD2')
    secs = time.perf_counter() - t0
    roots = loaded_roots()
    check(res == {'FINISHED'}, "Load LoD2 finished", str(res))
    check(sorted(r["cgml3_feature_id"] for r in roots) == sorted(ids), "Only the 6 selected buildings came in",
          f"{len(roots)} roots")
    check(all(r.get("cgml3_source_kind") == "file" and os.path.samefile(r["cgml3_source_path"], path) for r in roots),
          "Roots carry cgml3_source_kind/path/feature_id")
    check(s.last_load["bytes_read"] < 1 << 20, "Read < 1 MB", f"{s.last_load['bytes_read']:,} bytes")
    check(secs < 1.0, "6 buildings loaded in < 1 s", f"{secs:.2f} s")
    dg = bpy.context.evaluated_depsgraph_get()
    shown = sum(1 for f in s.arrays.face_feature if f not in s.loaded)
    check(len(p.evaluated_get(dg).data.polygons) == shown < n_faces, "Loaded boxes are hidden")

    print("\n[T08] --- L: second open comes from the cache ---")
    res, s2, secs2 = browse(path)
    check(s2 is not None and s2.from_cache, "Second browse uses the cache", f"{secs2:.1f} s")
    check(len(s2.entries) == 300 and len(proxies()) == 1, "Cache keeps 300 features, still one proxy")
    check(s2.loaded == {s2.id_to_index[i] for i in ids}, "Reopened session knows the loaded buildings")


def test_load_unload_replace(perf_dir):
    print("\n[T08] --- S: load, replace, unload, limit ---")
    fresh_scene()
    path = os.path.join(perf_dir, "S.gml")
    res, s, _ = browse(path, use_cache=False)
    bm = browse_module()
    ids = [s.entries[i].gml_id for i in (0, 5, 10)]
    select_ids(ids)
    bpy.ops.cgml3.browse_load(mode='LOD2')
    n_objects = len(bpy.data.objects)
    bpy.ops.cgml3.browse_load(mode='LOD2')
    check(len(bpy.data.objects) == n_objects and len(loaded_roots()) == 3,
          "Loading the same buildings again replaces them", f"{len(bpy.data.objects)} vs {n_objects}")
    select_ids(ids[:1])
    res = bpy.ops.cgml3.browse_unload()
    check(res == {'FINISHED'} and sorted(r["cgml3_feature_id"] for r in loaded_roots()) == sorted(ids[1:]),
          "Unload removes the selected building only")
    p = s.proxy()
    dg = bpy.context.evaluated_depsgraph_get()
    shown = sum(1 for f in s.arrays.face_feature if f not in s.loaded)
    check(len(p.evaluated_get(dg).data.polygons) == shown, "Its box is shown again")
    bpy.ops.cgml3.browse_unload(all=True)
    check(not loaded_roots(), "Unload all removes every loaded building")

    many = [e.gml_id for e in s.entries[:bm.load_limit() + 1]]
    select_ids(many)
    try:
        res = bpy.ops.cgml3.browse_load(mode='LOD2')
    except RuntimeError as exc:  # operator ERROR reports raise in background mode
        res = {'CANCELLED'}
        print(f"    report: {exc}")
    check(res == {'CANCELLED'} and not loaded_roots(),
          f"Selecting {len(many)} > limit {bm.load_limit()} loads nothing")


def test_equivalence(perf_dir):
    print("\n[T08] --- S: browse load equals Load all (1 mm) ---")
    path = os.path.join(perf_dir, "S.gml")
    fresh_scene()
    res, s, _ = browse(path, use_cache=False)
    ids = [e.gml_id for e in random.Random(3).sample(s.entries, 6)]
    select_ids(ids)
    bpy.ops.cgml3.browse_load(mode='HIGHEST')
    sig_browse = {r["cgml3_feature_id"]: signature(r) for r in loaded_roots()}

    fresh_scene()
    res = bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
    check(res == {'FINISHED'}, "Load all of S finished", str(res))
    roots = feature_roots_by_gml_id(ids)
    check(len(roots) == 6, "Load all has the 6 buildings", str(len(roots)))
    for gid in ids:
        ok, detail = compare_signatures(sig_browse.get(gid, {"objects": 0, "polygons": 0, "props": [], "points": []}),
                                        signature(roots[gid]) if gid in roots else {"objects": -1})
        check(ok, f"{gid}: objects, semantics and coordinates equal", detail)


def test_noenv_lod0_sample(perf_dir, buildings_dir):
    print("\n[T08] --- S-noenv / S-lod0 / sample data: preview labels ---")
    fresh_scene()
    res, s, _ = browse(os.path.join(perf_dir, "S-noenv.gml"), use_cache=False)
    check(s is not None and len(s.entries) == 300, "S-noenv indexed")
    check(all(not e.has_envelope and e.bbox for e in s.entries), "S-noenv: bbox from geometry (no envelopes)")
    check(s.preview() == "Footprint (ground surface)", "S-noenv preview is the ground footprint", s.preview())
    bpy.ops.cgml3.browse_close()
    check(not proxies(), "Close removes the proxy")

    res, s, _ = browse(os.path.join(perf_dir, "S-lod0.gml"), use_cache=False)
    check(s.preview() == "LoD0", "S-lod0 preview is the real LoD0", s.preview())
    rings = sum(len(e.lod0) for e in s.entries)
    p = s.proxy()
    check(len(p.data.polygons) == rings, "S-lod0 proxy faces are the LoD0 rings", f"{len(p.data.polygons)} / {rings}")
    zs = {round(v.co.z, 3) for v in p.data.vertices[:200]}
    check(len(zs) >= 1, "LoD0 footprints are flat polygons")
    bpy.ops.cgml3.browse_close()

    for name in ("buildings_lod2_citygml_3_0.gml", "buildings_lod2_citygml_2_0.gml"):
        res, s, _ = browse(os.path.join(buildings_dir, name), use_cache=False)
        check(len(s.entries) == 3 and s.preview() == "Footprint (ground surface)", f"{name}: 3 features, ground footprint",
              f"{len(s.entries)} {s.preview()}")
        select_ids([e.gml_id for e in s.entries])
        res = bpy.ops.cgml3.browse_load(mode='HIGHEST')
        check(res == {'FINISHED'} and len([r for r in loaded_roots()
                                           if os.path.samefile(r["cgml3_source_path"], s.path)]) == 3,
              f"{name}: 3 buildings loaded")
        bpy.ops.cgml3.browse_close()


def test_hamburg_1_0(perf_dir, out_dir):
    print("\n[T08] --- Hamburg CityGML 1.0 tile: browse, risks, 6 buildings ---")
    fresh_scene()
    path = os.path.join(perf_dir, HH_TILE)
    res, s, secs = browse(path, use_cache=False)
    check(s is not None and s.index.version == "1.0" and len(s.entries) == 460, "1.0 tile indexed",
          f"{len(s.entries) if s else 0} features in {secs:.1f} s")
    print(f"    risks: {s.index.risk_counts() or 'none'}")
    check(s.crs == "EPSG:25832", "CRS from the ADV URN", s.crs)
    ids = [e.gml_id for e in random.Random(564).sample(s.entries, 6)]
    select_ids(ids)
    res = bpy.ops.cgml3.browse_load(mode='LOD2')
    check(res == {'FINISHED'} and sorted(r["cgml3_feature_id"] for r in loaded_roots()) == sorted(ids),
          "6 buildings of the 1.0 tile loaded")
    check("EPSG:25832" in s.last_load.get("message", ""), "Report line shows the EPSG", s.last_load.get("message"))

    print("\n[T08] --- small CityGML 1.0 file: Load all gives features, not 0 ---")
    small = os.path.join(out_dir, "t08_small_1_0.gml")
    frag = sys.modules[f"{ADDON}.shared.browse.fragment"].build_fragment(s.index, [0, 1, 2], translate=False)
    with open(small, "wb") as fh:
        fh.write(frag.data)
    fresh_scene()
    res = bpy.ops.cgml3.import_gml_file(filepath=small)
    tops = [o for o in bpy.data.objects if o.get("gml_id") in {e.gml_id for e in s.entries[:3]} and o.parent is None]
    check(res == {'FINISHED'} and len(tops) == 3, "1.0 file via File > Import: 3 buildings", f"{res} {len(tops)}")


def test_cache_invalidation(buildings_dir, out_dir):
    print("\n[T08] --- cache: hit on reopen, miss after the file changed ---")
    fresh_scene()
    src = os.path.join(buildings_dir, "buildings_lod2_citygml_3_0.gml")
    path = os.path.join(out_dir, "t08_cache_probe.gml")
    shutil.copyfile(src, path)
    _, s, _ = browse(path)
    check(s is not None and not s.from_cache, "First open indexes the file")
    bpy.ops.cgml3.browse_close()
    _, s, _ = browse(path)
    check(s is not None and s.from_cache, "Second open comes from the cache")
    bpy.ops.cgml3.browse_close()
    with open(path, "ab") as fh:
        fh.write(b"\n<!-- changed -->\n")
    _, s, _ = browse(path)
    check(s is not None and not s.from_cache and len(s.entries) == 3, "Changed file is indexed again")
    bpy.ops.cgml3.browse_close()


def main():
    a = args()
    perf_dir, buildings_dir, out_dir = a[0], a[1], a[2]
    skip_large = "--skip-large" in a
    os.makedirs(out_dir, exist_ok=True)
    cache = os.path.join(out_dir, "index_cache")
    shutil.rmtree(cache, ignore_errors=True)
    os.environ["CGML3_INDEX_CACHE_DIR"] = cache

    test_load_unload_replace(perf_dir)
    test_equivalence(perf_dir)
    test_noenv_lod0_sample(perf_dir, buildings_dir)
    test_hamburg_1_0(perf_dir, out_dir)
    test_cache_invalidation(buildings_dir, out_dir)
    if not skip_large:
        test_large_index(perf_dir)

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    print("\n[ALL PASS] T08 browse checks satisfied")


main()
