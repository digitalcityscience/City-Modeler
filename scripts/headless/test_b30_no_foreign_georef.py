"""B30: the georeference is ours alone (World["cgml_CRS"] / cgml_X/Y/Z-Origin in bpy.data.worlds["World"]).

- another add-on's scene keys (BlenderGIS: Scene["SRID"], ["crs x"], ["crs y"]) are ignored:
  import takes the CRS and origin from the file, the sidebar shows the file's EPSG
- a scene whose own world is not "World" (e.g. World.001) still gets the georeference in "World",
  which is what the exporter reads; the scene's world is left alone
- New Building in such a scene writes its CRS to "World" too

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b30_no_foreign_georef.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

GML = os.path.join(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"), "buildings_lod2_citygml_3_0.gml")


def reset(sc):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in list(bpy.data.worlds):
        bpy.data.worlds.remove(w)
    bpy.data.worlds.new("World")
    sc.world = bpy.data.worlds.new("Other")  # the scene's own world is not "World"
    for k in ("SRID", "crs x", "crs y"):
        sc.pop(k, None)


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    header = import_module(f"{addon}.ui.header_panel")
    sc = bpy.context.scene

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
        reset(sc)
        # what BlenderGIS leaves behind after a basemap / OSM import
        sc["SRID"] = "EPSG:3857"
        sc["crs x"] = 1000000.0
        sc["crs y"] = 7000000.0
        res = bpy.ops.cgml3.import_gml_file(filepath=GML)
        w = bpy.data.worlds["World"]
        check("import finished", res == {'FINISHED'}, str(res))
        check("CRS from the file, not Scene['SRID']", "25832" in str(w.get("cgml_CRS")), str(dict(w.items())))
        check("origin from the file, not Scene['crs x/y']",
              564000 < float(w.get("cgml_X-Origin", 0)) < 565000 and 5934000 < float(w.get("cgml_Y-Origin", 0)) < 5935000,
              str(dict(w.items())))
        check("scene's own world untouched", sc.world.name == "Other" and "cgml_CRS" not in sc.world,
              str(sc.world.name))
        check("sidebar shows the file EPSG", header.scene_epsg(sc) == "EPSG:25832", str(header.scene_epsg(sc)))

        reset(sc)
        bpy.ops.cgml.building_new(points=footprint())
        check("New Building: CRS in 'World'", "cgml_CRS" in bpy.data.worlds["World"] and "cgml_CRS" not in sc.world,
              str(dict(bpy.data.worlds["World"].items())))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
