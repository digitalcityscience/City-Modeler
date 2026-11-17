"""B46: height status is consistent (A4): computed = estimated, the card says so too.

CityGML 3.0 con:HeightStatusValue is "estimated" or "measured" (construction.xsd). A height the
add-on computes from the model is not surveyed: estimated (B39). The attribute card defaulted a
typed height to "measured" and offered no way to see or change the status.
- New Building: con:height status estimated, written to 3.0; 2.0 writes bldg:measuredHeight
- card: typing a height keeps "estimated"; the card's Status field switches it to "measured"
- a measured height is not overwritten by Roof (B39 rule), an estimated one is recomputed

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b46_height_status.py -- <out_dir>
"""
import os
import re
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def status_in(path):
    return re.findall(r"<con:status>([^<]*)</con:status>", open(path, encoding="utf-8").read())


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b46_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    card = import_module(f"{addon}.ui.attribute_card")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    check("New Building: status estimated", b.get("con:height:status") == "estimated", str(b.get("con:height:status")))
    check("card default status is estimated", card.HEIGHT_DEFAULTS.get("con:height:status") == "estimated",
          str(card.HEIGHT_DEFAULTS))
    wm = bpy.context.window_manager
    check("card shows the status", wm.cgml_attr_height_status == 'estimated', str(wm.cgml_attr_height_status))

    wm.cgml_attr_height = 12.5  # typed on the card: still the add-on's claim level
    check("typed height keeps estimated", b.get("con:height:status") == "estimated" and b.get("con:height:value") == 12.5,
          f"{b.get('con:height:status')} {b.get('con:height:value')}")
    wm.cgml_attr_height_status = 'measured'
    check("card switches to measured", b.get("con:height:status") == "measured", str(b.get("con:height:status")))
    p = os.path.join(out, "measured_v3.gml")
    bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    check("3.0: measured written", status_in(p) == ["measured"], str(status_in(p)))
    bpy.ops.cgml.roof_add(roof_type="3100")
    check("Roof keeps a measured height", b.get("con:height:value") == 12.5 and b.get("con:height:status") == "measured",
          f"{b.get('con:height:value')} {b.get('con:height:status')}")

    wm.cgml_attr_height_status = 'estimated'
    bpy.ops.cgml.roof_add(roof_type="1000")
    check("Roof recomputes an estimated height", b.get("con:height:status") == "estimated"
          and abs(float(b.get("con:height:value")) - 9.0) < 1e-3, f"{b.get('con:height:value')} {b.get('con:height:status')}")
    p = os.path.join(out, "estimated_v3.gml")
    bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    check("3.0: estimated written", status_in(p) == ["estimated"], str(status_in(p)))
    p2 = os.path.join(out, "estimated_v2.gml")
    bpy.ops.cgml3.export_gml_file(filepath=p2, citygml_version="2.0")
    check("2.0: measuredHeight written (2.0 has no status)",
          re.search(r"<bldg:measuredHeight[^>]*>9(\.0)?<", open(p2, encoding="utf-8").read()) is not None)

    # a fresh card height on a building without any height: estimated, never a fabricated "measured"
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    for k in [k for k in b.keys() if k.startswith("con:height")]:
        del b[k]
    wm.cgml_attr_height = 7.0
    check("card height on a bare building: estimated", b.get("con:height:status") == "estimated", str(b.get("con:height:status")))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
