"""B14: File > Import and drag & drop open the preview; the preview is a flat 2D map.

- File > Import > CityGML: the first entry is the preview (browse), the whole
  file import stays as the second entry
- a dropped .gml/.xml opens the preview, not the whole file
- every preview face is flat at its building's lowest Z (no boxes, no LoD1
  look): LoD0 if the file has it, else the GroundSurface outline, else the
  bbox rectangle

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b14_preview_default_2d.py -- <buildings_dir> <hh_tile>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
FOOT = "Footprint (ground surface)"


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Recorder:
    def __init__(self, ops):
        self.ops = ops

    def operator(self, idname, text="", **kw):
        self.ops.append((idname, text))
        return type("P", (), {})()

    def __getattr__(self, name):
        return lambda *a, **kw: None


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    return addon, sys.modules[f"{addon}.ops.browse_gml"]


def roots():
    return [o for o in bpy.data.objects if o.get("cgml3_feature") == "Building" and o.parent is None]


def flat_per_feature(s):
    """(features whose faces are not all at one Z, features whose Z is not the bbox min Z)."""
    a, o = s.arrays, s.origin
    z_of = {}
    for f in range(a.face_count):
        st, n = a.loop_start[f], a.loop_total[f]
        z_of.setdefault(a.face_feature[f], set()).update(round(a.co[3 * a.loop_vertex[st + k] + 2], 4) for k in range(n))
    tilted = [fi for fi, zs in z_of.items() if len(zs) != 1]
    off = [fi for fi, zs in z_of.items() if len(zs) == 1
           and abs(next(iter(zs)) - (s.entries[fi].bbox[2] - o[2])) > 1e-3]
    return tilted, off


def test_menu_and_drop(path):
    addon, bm = fresh()
    print("\n[B14] --- File > Import and drag & drop ---")
    funcs = [f for f in bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize() if f.__module__ == addon]
    ops = []
    for f in funcs:
        f(type("M", (), {"layout": Recorder(ops)})(), bpy.context)
    ids = [o[0] for o in ops]
    check(ids[:1] == ["cgml3.browse_gml_file"], "File > Import: first CityGML entry is the preview", str(ops))
    check("cgml3.import_gml_file" in ids[1:], "File > Import: whole file stays as a second entry", str(ops))
    check(bpy.types.CGML3_FH_citygml.bl_import_operator == "cgml3.browse_gml_file",
          "drag & drop opens the preview", bpy.types.CGML3_FH_citygml.bl_import_operator)
    # what the file handler runs on a drop: the operator with the dropped path, invoked
    res = bpy.ops.cgml3.browse_gml_file('INVOKE_DEFAULT', filepath=path, use_cache=False)
    s = bm.get_session(path)
    check(res == {'FINISHED'} and s is not None and s.complete, "dropped file is browsed", str(res))
    check(not roots(), "no real building is loaded by the drop", str(len(roots())))


def test_flat(path, expect_label, label):
    _addon, bm = fresh()
    print(f"\n[B14] --- flat preview: {label} ---")
    bpy.ops.cgml3.browse_gml_file(filepath=path, use_cache=False)
    s = bm.get_session(path)
    check(s is not None and s.complete, f"{label}: indexed")
    p = s.proxy()
    tilted, off = flat_per_feature(s)
    check(not tilted, f"{label}: every building is flat (2D, no box walls)", f"{len(tilted)} not flat")
    check(not off, f"{label}: drawn at the building's lowest Z", f"{len(off)} off")
    zs = [v.co.z for v in p.data.vertices]
    tall = max(e.bbox[5] - e.bbox[2] for e in s.entries if e.bbox)
    check(len(p.data.polygons) == s.arrays.face_count, f"{label}: proxy mesh holds the flat faces only")
    check(s.preview() == expect_label, f"{label}: preview label", s.preview())
    n_feet = sum(1 for e in s.entries if e.footprint)
    print(f"      {len(s.entries)} buildings, {n_feet} with a ground footprint, {len(p.data.polygons)} faces, "
          f"{len(p.data.vertices)} vertices, tallest building {tall:.1f} m, proxy Z span "
          f"{max(zs) - min(zs):.1f} m, index {s.stats.get('index_s', s.stats.get('seconds', 0)):.2f} s")
    # the footprint stays inside the bbox (same place as the building)
    a, o = s.arrays, s.origin
    outside = 0
    for f in range(a.face_count):
        e = s.entries[a.face_feature[f]]
        st, n = a.loop_start[f], a.loop_total[f]
        for k in range(n):
            vi = a.loop_vertex[st + k]
            x, y = a.co[3 * vi] + o[0], a.co[3 * vi + 1] + o[1]
            if not (e.bbox[0] - 0.01 <= x <= e.bbox[3] + 0.01 and e.bbox[1] - 0.01 <= y <= e.bbox[4] + 0.01):
                outside += 1
    check(outside == 0, f"{label}: footprints lie inside the building's bbox", f"{outside} vertices outside")
    # the see-through fill covers the outlines exactly (a fan over a concave outline spills)
    import numpy as np
    t = bm.fill_points(s).reshape(-1, 3, 3)
    fill_area = float(np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2)
    poly_area = sum(pg.area for pg in p.data.polygons)
    check(abs(fill_area - poly_area) <= 0.001 * poly_area, f"{label}: fill area = footprint area",
          f"{fill_area:.1f} / {poly_area:.1f} m²")
    return s


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    buildings, tile = argv[0], argv[1]
    lod2 = os.path.join(buildings, "buildings_lod2_citygml_2_0.gml")
    test_menu_and_drop(lod2)
    for name in ("buildings_lod2_citygml_2_0.gml", "buildings_lod3_citygml_3_0.gml"):
        test_flat(os.path.join(buildings, name), FOOT, name)
    s = test_flat(tile, FOOT, os.path.basename(tile))
    check(all(e.footprint for e in s.entries if e.bbox), "HH tile: every building has its ground outline")

    if FAILURES:
        print(f"\n[FAIL] B14 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B14 preview by default, flat 2D")


main()
