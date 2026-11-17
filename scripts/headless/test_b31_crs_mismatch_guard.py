"""B31: a source in another CRS must not silently re-base a scene that already holds CityGML features.

- scene with features + World CRS/origin (25832): a 25833 file import is CANCELLED, World and objects untouched
- same CRS: imports, origin kept
- browse of a 25833 file is refused before the index touches the World
- 3DCityDB browse uses the same rule (pure check: crs_conflict with the scene lock)
- empty scene: a 25833 file re-bases the World (old behaviour)
- New Building (CRS only, no origin) then a same-CRS import writes the origin (B26)
- import_local: no check, World untouched
- a compound CRS (EPSG:3068+5783) in World matches the same file again

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b31_crs_mismatch_guard.py
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

GML = os.path.join(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"), "buildings_lod2_citygml_3_0.gml")


def other_crs_copy() -> str:
    with open(GML, encoding="utf-8") as fh:
        text = fh.read()
    path = os.path.join(tempfile.mkdtemp(prefix="b31_"), "buildings_25833.gml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace("EPSG::25832", "EPSG::25833"))
    return path


def reset(sc):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in list(bpy.data.worlds):
        bpy.data.worlds.remove(w)
    sc.world = bpy.data.worlds.new("World")
    sc.cgml3.import_local = False


def georef():
    w = bpy.data.worlds["World"]
    return {k: w.get(k) for k in ("cgml_CRS", "cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin")}


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    browse_gml = import_module(f"{addon}.ops.browse_gml")
    georef_mod = import_module(f"{addon}.shared.georef")
    sc = bpy.context.scene
    other = other_crs_copy()

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
        reset(sc)
        check("first import", bpy.ops.cgml3.import_gml_file(filepath=GML) == {'FINISHED'})
        before, n = georef(), len(bpy.data.objects)
        check("World is 25832", "25832" in str(before["cgml_CRS"]), str(before))

        try:
            res = bpy.ops.cgml3.import_gml_file(filepath=other)
        except RuntimeError as exc:  # an operator ERROR report raises in background mode
            res = {'CANCELLED'} if "25833" in str(exc) and "25832" in str(exc) else str(exc)
        check("25833 import refused", res == {'CANCELLED'}, str(res))
        check("World not re-based", georef() == before, f"{georef()} vs {before}")
        check("nothing imported", len(bpy.data.objects) == n, f"{len(bpy.data.objects)} vs {n}")

        msg = georef_mod.crs_conflict("EPSG:25833", georef_mod.scene_crs_lock())
        check("message names both EPSGs and the way out",
              msg and "EPSG:25833" in msg and "EPSG:25832" in msg and "new Blender file" in msg and "3DCityDB" in msg,
              str(msg))
        check("3DCityDB rule: other srid refused, same srid passes",
              georef_mod.crs_conflict("EPSG:25833", "EPSG:25832") and
              georef_mod.crs_conflict("EPSG:25832", "EPSG:25832") is None)

        try:
            browse_gml.start_browse(bpy.context, other, use_cache=False, manual=True)
            refused = False
        except georef_mod.CrsMismatch:
            refused = True
        check("browse of a 25833 file refused", refused)
        check("browse left the World alone", georef() == before, str(georef()))

        check("same-CRS import", bpy.ops.cgml3.import_gml_file(filepath=GML) == {'FINISHED'})
        check("same-CRS import keeps the origin", georef() == before, str(georef()))

        sc.cgml3.import_local = True
        check("import_local: 25833 imports", bpy.ops.cgml3.import_gml_file(filepath=other) == {'FINISHED'})
        check("import_local: World untouched", georef() == before, str(georef()))

        reset(sc)
        check("empty scene: 25833 imports", bpy.ops.cgml3.import_gml_file(filepath=other) == {'FINISHED'})
        check("empty scene: World re-based to 25833", "25833" in str(georef()["cgml_CRS"]), str(georef()))

        # a compound CRS stored in World ("EPSG:3068+5783") is the same CRS as the file's URN
        check("compound World CRS matches its URN", georef_mod.same_crs(
            "EPSG:3068+5783", "urn:ogc:def:crs,crs:EPSG::3068,crs:EPSG::5783"))
        reset(sc)
        rail = os.path.join(HERE, "..", "..", "test_files", "Railway", "Railway_citygml3.gml")
        bpy.ops.cgml3.import_gml_file(filepath=rail)
        before = georef()
        check("compound CRS: second import of the same file",
              bpy.ops.cgml3.import_gml_file(filepath=rail) == {'FINISHED'} and georef() == before, str(georef()))

        reset(sc)
        bpy.ops.cgml.building_new(points=footprint())
        check("New Building: CRS only", "cgml_CRS" in bpy.data.worlds["World"]
              and "cgml_X-Origin" not in bpy.data.worlds["World"], str(georef()))
        check("B26: same-CRS import after New Building",
              bpy.ops.cgml3.import_gml_file(filepath=GML) == {'FINISHED'})
        check("B26: origin written", georef()["cgml_X-Origin"] is not None, str(georef()))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
