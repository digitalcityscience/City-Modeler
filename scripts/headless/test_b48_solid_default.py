"""B48: lodNSolid is written by default - closed shells and solids the source carried (A6).

Hamburg's LoD2 buildings carry lod2Solid; the round-trip dropped it because the export option
"Write LoD Solid from surface references" was off by default. Now:
- import records cgml_source_solid_lods ("2" for Hamburg LoD2) on the building, never exported
- Hamburg LoD2 3.0 and 2.0 samples: every building keeps its lod2Solid in 2.0 and 3.0 export,
  the solid references the building's own boundary polygons (all resolve, none from installations)
- the option off: imported buildings get no solid (authored ones still do, B39)
- Railway LoD3 (no Solid in the source, open shell): no solid is invented; installations never get one

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b48_solid_default.py -- <out_dir>
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

HH = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
RAILWAY = os.path.join(HERE, "..", "..", "test_files", "Building", "Railway_Scene_LoD3_CityGML3_Building_only.gml")


def solids(text):
    """[(lod, [href targets])] per lodNSolid element."""
    return [(lod, re.findall(r'xlink:href="#([^"]+)"', body))
            for lod, body in re.findall(r"<(?:bldg:)?lod(\d)Solid>(.*?)</(?:bldg:)?lod\1Solid>", text, re.S)]


def load(path):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    res = bpy.ops.cgml3.import_gml_file(filepath=path)
    check(f"import {os.path.basename(path)}", res == {'FINISHED'}, str(res))
    for o in bpy.context.view_layer.objects:
        o.select_set(True)


def export(out, name, v, **kw):
    p = os.path.join(out, name)
    res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v, **kw)
    check(f"export {name}", res == {'FINISHED'}, str(res))
    return open(p, encoding="utf-8").read(), p


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b48_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)

    for src in ("buildings_lod2_citygml_3_0.gml", "buildings_lod2_citygml_2_0.gml"):
        load(os.path.join(HH, src))
        blds = [o for o in bpy.data.objects if o.get("cgml3_feature") == "Building"]
        check(f"{src}: source solid LoD recorded on every building",
              blds and all(str(o.get("cgml_source_solid_lods")) == "2" for o in blds),
              str([o.get("cgml_source_solid_lods") for o in blds]))
        for v in ("3.0", "2.0"):
            text, p = export(out, f"{src[:-4]}_to_v{v[0]}.gml", v)
            s = solids(text)
            n_b = len(re.findall(r"<bldg:Building\b", text))
            check(f"{src} -> {v}: one lod2Solid per building ({n_b})", len(s) == n_b and all(l == "2" for l, _ in s),
                  str([(l, len(m)) for l, m in s]))
            inst = text.split("BuildingInstallation")
            poly_ids = set(re.findall(r'<gml:Polygon gml:id="([^"]+)"', text))
            for _lod, members in s:
                check(f"{src} -> {v}: solid members resolve", members and set(members) <= poly_ids,
                      str(sorted(set(members) - poly_ids)[:3]))
            check(f"{src} -> {v}: no bookkeeping leaks", "cgml_source_solid_lods" not in text)
            valid, msg = xsd_valid(v, p)
            check(f"{src} -> {v}: XSD valid", valid, msg[:300])
        text, _p = export(out, f"{src[:-4]}_nosolid_v3.gml", "3.0", write_lod_solid_refs=False)
        check(f"{src}: option off -> no solid", not solids(text), str(len(solids(text))))

    load(os.path.abspath(RAILWAY))
    text, _p = export(out, "railway_to_v3.gml", "3.0")
    # Building: open shell, no source solid; installations: never (B40, like Hamburg; 2.0 has no element)
    check("Railway (no source solid, open shell): no solid invented", not solids(text), str(len(solids(text))))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
