"""B51: a flat roof goes on a footprint without right angles; pitched roofs still refuse it.

- building drawn on a skewed quadrilateral: Roof (3100 gable) is refused with the message
  naming the Flat roof as the way out; Roof (1000 flat) makes it LoD2 with bldg:roofType 1000
- 2.0 and 3.0 export: lod2Solid (closed), one RoofSurface, XSD-valid

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b51_flat_roof_skewed.py -- <out_dir>
"""
import json
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

SKEWED = [(0, 0, 0), (10, 0, 0), (12, 7, 0), (-1, 6, 0)]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b51_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=json.dumps(SKEWED))
    b = bpy.context.active_object
    try:  # an ERROR report raises in background mode
        res = str(bpy.ops.cgml.roof_add(roof_type="3100"))
    except RuntimeError as exc:
        res = str(exc)
    check("gable on a skewed footprint is refused, pointing at the Flat roof", "Flat roof" in res, res[:200])
    check("still LoD1 after the refusal", str(b.get("lod")) == "1", str(b.get("lod")))
    res = bpy.ops.cgml.roof_add(roof_type="1000")
    check("flat roof accepted", res == {'FINISHED'}, str(res))
    check("LoD2 + roofType 1000", str(b.get("lod")) == "2" and b.get("bldg:roofType") == "1000",
          f"{b.get('lod')} {b.get('bldg:roofType')}")
    roofs = [o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface"]
    check("one RoofSurface at the eave", len(roofs) == 1 and all(abs((roofs[0].matrix_world @ v.co).z - 9.0) < 1e-3
                                                                 for v in roofs[0].data.vertices), str(len(roofs)))
    for v in ("3.0", "2.0"):
        p = os.path.join(out, f"b51_v{v[0]}.gml")
        bpy.ops.object.select_all(action='DESELECT')
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        check(f"{v}: lod2Solid with 6 members", len(re.findall(r'xlink:href="#', text)) == 6 and "lod2Solid>" in text,
              str(len(re.findall(r'xlink:href="#', text))))
        check(f"{v}: roofType 1000", re.search(r"<bldg:roofType[^>]*>1000<", text) is not None)
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
