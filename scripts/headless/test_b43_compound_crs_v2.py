"""B43: the 2.0 writer keeps the vertical CRS (compound CRS URN in srsName).

Railway_Scene_LoD3_CityGML2 carries srsName="urn:ogc:def:crs,crs:EPSG::3068,crs:EPSG::5783".
The 2.0 export wrote "urn:ogc:def:crs:EPSG::3068" (vertical CRS dropped); the 3.0 export kept
the compound URN. Now both do; every srsName in the 2.0 file is the compound URN and the file
is XSD-valid. (3DCityDB v5 citydb-tool --preview and CityDoctor accept that URN: ticket B43.)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b43_compound_crs_v2.py -- <out_dir>
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402

SRC = os.path.join(HERE, "..", "..", "test_files", "Building", "Railway_Scene_LoD3_CityGML2_Building_only.gml")
COMPOUND = "urn:ogc:def:crs,crs:EPSG::3068,crs:EPSG::5783"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b43_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    res = bpy.ops.cgml3.import_gml_file(filepath=os.path.abspath(SRC))
    check("import finished", res == {'FINISHED'}, str(res))
    w = bpy.data.worlds.get("World")
    check("World keeps the vertical EPSG", w is not None and str(w.get("cgml_Z-EPSG")) == "5783", str(dict(w.items()) if w else None))
    for o in bpy.context.view_layer.objects:
        o.select_set(True)
    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"railway_to_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export finished", res == {'FINISHED'}, str(res))
        names = sorted(set(re.findall(r'srsName="([^"]*)"', open(p, encoding="utf-8").read())))
        check(f"{v}: every srsName is the compound CRS URN", names == [COMPOUND], str(names))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD-valid", valid, msg)

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
