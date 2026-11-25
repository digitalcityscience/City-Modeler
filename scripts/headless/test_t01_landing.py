"""T01: import landing - origin at the data centre, stable origin for further
imports, georeferenced scenes untouched, clip range, export round trip.

Usage:
  blender -b --python scripts/headless/test_t01_landing.py -- <buildings_dir> <out_dir>

<buildings_dir> holds buildings_lod{2,3}_citygml_{2_0,3_0}.gml (Hamburg,
EPSG:25832). Exits 1 when any check fails.
"""
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bpy
from mathutils import Vector

from addon_test_utils import ensure_addon_enabled

LOD2_V3 = "buildings_lod2_citygml_3_0.gml"
LOD3_V3 = "buildings_lod3_citygml_3_0.gml"
LOD2_V2 = "buildings_lod2_citygml_2_0.gml"
# Envelope centre of the LoD2 files rounded to 100 m, Z = envelope min Z
EXPECTED_ORIGIN = (564800.0, 5934300.0, 17.23)
TOLERANCE_MM = 0.001

FAILURES = []
ADDON = None


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")


def import_file(path):
    before = {o.as_pointer() for o in bpy.data.objects}
    res = bpy.ops.cgml3.import_gml_file(filepath=path)
    assert res == {'FINISHED'}, res
    return [o for o in bpy.data.objects if o.as_pointer() not in before]


def header_epsg():
    return __import__(f"{ADDON}.ui.header_panel", fromlist=["scene_epsg"]).scene_epsg(bpy.context.scene)


def shifted_copy(path, out_dir, dx):
    """The same file moved ``dx`` metres east (EPSG:25832 X values start with 56)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"\b56\d{4}(?:\.\d+)?(?= 59\d{5})", lambda m: repr(float(m.group()) + dx), text)
    out = os.path.join(out_dir, "t01_shifted_" + os.path.basename(path))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return out


def world_origin():
    w = bpy.context.scene.world
    return (float(w["cgml_X-Origin"]), float(w["cgml_Y-Origin"]), float(w["cgml_Z-Origin"])), str(w.get("cgml_CRS", ""))


def data_bounds(objects):
    mins = [math.inf] * 3
    maxs = [-math.inf] * 3
    for o in objects:
        if o.type != 'MESH' or not o.data.vertices:
            continue
        for v in o.data.vertices:
            w = o.matrix_world @ v.co
            for k in range(3):
                mins[k] = min(mins[k], w[k])
                maxs[k] = max(maxs[k], w[k])
    return mins, maxs


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def max_clip_end():
    ends = [s.clip_end for scr in bpy.data.screens for a in scr.areas if a.type == 'VIEW_3D'
            for s in a.spaces if s.type == 'VIEW_3D']
    return max(ends) if ends else None


def pos_list_points(path):
    """Surface coordinates of a file.

    TerrainIntersectionCurves are skipped: the exporter does not write them
    (pre-existing gap, independent of the import origin).
    """
    pts = []

    def walk(el):
        tag = str(el.tag)
        if "TerrainIntersection" in tag:
            return
        if tag.endswith("}posList") and el.text:
            vals = [float(v) for v in el.text.split()]
            dim = int(el.get("srsDimension") or 3)
            pts.extend(tuple(vals[i:i + dim]) for i in range(0, len(vals) - dim + 1, dim))
        for child in el:
            walk(child)

    walk(ET.parse(path).getroot())
    return pts


def unmatched(points, reference, tol):
    """Points with no reference point within ``tol`` (grid lookup)."""
    cell = tol * 10
    grid = {}
    for p in reference:
        grid.setdefault(tuple(math.floor(c / cell) for c in p), []).append(p)
    missing = []
    for p in points:
        key = tuple(math.floor(c / cell) for c in p)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in grid.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if max(abs(a - b) for a, b in zip(p, q)) <= tol:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if not found:
            missing.append(p)
    return missing


def main():
    src_dir, out_dir = args()[0], args()[1]
    os.makedirs(out_dir, exist_ok=True)
    lod2_v3 = os.path.join(src_dir, LOD2_V3)

    print("\n[T01] --- CityGML 3: origin = data centre, data near (0,0) ---")
    fresh_scene()
    objs = import_file(lod2_v3)
    origin, crs = world_origin()
    check(close(origin, EXPECTED_ORIGIN, 1e-6), "World origin is the rounded envelope centre", f"{origin}")
    check(crs == "EPSG:25832", "World CRS kept", crs)
    mins, maxs = data_bounds(objs)
    centre = ((mins[0] + maxs[0]) / 2, (mins[1] + maxs[1]) / 2)
    check(abs(centre[0]) <= 100 and abs(centre[1]) <= 100, "Data bbox centre within 100 m of (0,0)", f"{centre}")
    extent = max(maxs[k] - mins[k] for k in range(3))
    clip_end = max_clip_end()
    check(clip_end is None or clip_end >= 5 * extent, "Clip end covers 5x data extent", f"clip_end={clip_end} extent={extent:.1f}")

    print("\n[T01] --- second file keeps origin and aligns ---")
    objs2 = import_file(os.path.join(src_dir, LOD3_V3))
    origin2, _ = world_origin()
    check(close(origin2, origin, 1e-9), "Origin unchanged after second import", f"{origin2}")
    mins2, maxs2 = data_bounds(objs2)
    check(close(mins2[:2], mins[:2], 1.0) and close(maxs2[:2], maxs[:2], 1.0),
          "LoD3 model overlaps LoD2 model (XY bounds within 1 m)", f"{mins2[:2]} / {mins[:2]}")

    print("\n[T01] --- CityGML 2: same origin ---")
    fresh_scene()
    import_file(os.path.join(src_dir, LOD2_V2))
    origin_v2, _ = world_origin()
    check(close(origin_v2, EXPECTED_ORIGIN, 1e-6), "CityGML 2 origin equals CityGML 3 origin", f"{origin_v2}")

    print("\n[T01] --- georeferenced scene is not changed ---")
    fresh_scene()
    res = bpy.ops.cgml3.set_scene_origin(crs="EPSG:25832", origin="564000 5934000 0")
    check(res == {'FINISHED'}, "Set Origin Before Opening", str(res))
    objs = import_file(lod2_v3)
    origin_g, _ = world_origin()
    check(close(origin_g, (564000.0, 5934000.0, 0.0)), "Existing World origin kept", f"{origin_g}")
    mins_g, _ = data_bounds(objs)
    check(close(mins_g[:2], (mins[0] + 800.0, mins[1] + 300.0), 1e-2), "Data placed relative to existing origin", f"{mins_g[:2]}")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    check(header_epsg() == "EPSG:25832", "Hand-set origin still shown with nothing open", str(header_epsg()))

    print("\n[T01] --- data closed: a same-CRS file 100 km away gets its own origin ---")
    fresh_scene()
    import_file(lod2_v3)
    for o in list(bpy.data.objects):  # the user deletes everything; World keeps the old keys
        bpy.data.objects.remove(o)
    check(header_epsg() is None, "Left-over CRS not shown with nothing open", str(header_epsg()))
    browse_gml = __import__(f"{ADDON}.ops.browse_gml", fromlist=["existing_origin"])
    check(browse_gml.existing_origin(bpy.context, "EPSG:25832") is None, "Preview ignores the left-over origin")
    objs = import_file(shifted_copy(lod2_v3, out_dir, 100000.0))
    origin_s, crs_s = world_origin()
    want = (EXPECTED_ORIGIN[0] + 100000.0,) + EXPECTED_ORIGIN[1:]
    check(close(origin_s, want, 1e-6) and crs_s == "EPSG:25832", "Origin re-based to the new file", f"{origin_s}")
    mins_s, maxs_s = data_bounds(objs)
    check(abs((mins_s[0] + maxs_s[0]) / 2) <= 100, "New data near (0,0)", f"{mins_s[:2]}")
    check(header_epsg() == "EPSG:25832", "CRS shown again with data open", str(header_epsg()))

    print("\n[T01] --- export round trip keeps coordinates (1 mm) ---")
    fresh_scene()
    import_file(lod2_v3)
    out = os.path.join(out_dir, "t01_roundtrip.gml")
    res = bpy.ops.cgml3.export_gml_file(filepath=out)
    check(res == {'FINISHED'}, "Export finished", str(res))
    src_pts = pos_list_points(lod2_v3)
    out_pts = pos_list_points(out)
    miss_out = unmatched(out_pts, src_pts, TOLERANCE_MM)
    miss_src = unmatched(src_pts, out_pts, TOLERANCE_MM)
    check(bool(out_pts) and not miss_out, "Every exported point exists in the source",
          f"{len(miss_out)}/{len(out_pts)} unmatched, e.g. {miss_out[:2]}")
    check(not miss_src, "Every source point exists in the export",
          f"{len(miss_src)}/{len(src_pts)} unmatched, e.g. {miss_src[:2]}")

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    print("\n[ALL PASS] T01 landing checks satisfied")


main()
