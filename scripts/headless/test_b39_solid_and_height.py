"""B39: an authored building is written as a solid and with its height.

Hamburg's LoD2 buildings carry lod2Solid (xlink:href to the boundary polygons) and the
height (2.0 bldg:measuredHeight, 3.0 con:height highestRoofEdge / lowestGroundPoint).
New Building and Roof wrote neither: lodNSolid only came with the export option
"Write LoD Solid from surface references" (default off), and in 2.0 it said lod2Solid for
a LoD1 block (XSD-invalid next to lod1MultiSurface).

- New Building (LoD1, 9 m): 2.0 and 3.0 have one lod1Solid whose members all resolve to
  polygons of the file; height 9 m (3.0 con:height, status estimated; 2.0 measuredHeight)
- gable roof: lod2Solid with the 7 boundary polygons, height = ridge (11.801 m)
- window + door: lod3Solid with the fillings (B49: they close the holes), height unchanged
- every file XSD-valid; a chimney does not count as height

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b39_solid_and_height.py -- <out_dir>
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402

RIDGE = 11.801  # 9 m eaves + 4 m * tan(35 deg) gable


def export(out, tag):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    paths = {}
    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b39_{tag}_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{tag}: export {v}", res == {'FINISHED'}, str(res))
        valid, msg = xsd_valid(v, p)
        check(f"{tag} {v}: XSD valid", valid, msg[:300])
        paths[v] = p
    return paths


def solids(text):
    """{lod: [href targets]} of the lodNSolid elements."""
    out = {}
    for lod, body in re.findall(r"<(?:bldg:)?lod(\d)Solid>(.*?)</(?:bldg:)?lod\1Solid>", text, re.S):
        out.setdefault(lod, []).extend(re.findall(r'xlink:href="#([^"]+)"', body))
    return out


def height(text, version):
    if version == "2.0":
        m = re.search(r"<bldg:measuredHeight[^>]*>([^<]+)<", text)
        return float(m.group(1)) if m else None, ""
    m = re.search(r"<con:height>(.*?)</con:height>", text, re.S)
    if not m:
        return None, ""
    val = re.search(r"<con:value[^>]*>([^<]+)<", m.group(1))
    refs = "/".join(re.findall(r"<con:(?:highReference|lowReference|status)>([^<]+)<", m.group(1)))
    return (float(val.group(1)) if val else None), refs


def check_stage(paths, tag, lod, members, h):
    for v, p in paths.items():
        text = open(p, encoding="utf-8").read()
        s = solids(text)
        polys = set(re.findall(r'<gml:Polygon gml:id="([^"]+)"', text))
        if lod is None:
            check(f"{tag} {v}: no lodNSolid", not s, str(s.keys()))
        else:
            got = s.get(lod, [])
            check(f"{tag} {v}: one lod{lod}Solid only", list(s) == [lod] and text.count(f"lod{lod}Solid>") == 2,
                  str({k: len(x) for k, x in s.items()}))
            check(f"{tag} {v}: {members} solid members, all polygons of this file",
                  len(got) == members and set(got) <= polys, f"{len(got)} members, missing {set(got) - polys}")
        val, refs = height(text, v)
        check(f"{tag} {v}: height {h} m", val is not None and abs(val - h) < 1e-3, str(val))
        if v == "3.0":
            check(f"{tag} 3.0: height references as Hamburg", refs == "highestRoofEdge/lowestGroundPoint/estimated",
                  refs)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b39_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    if not ok:
        sys.exit(1)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m
    b = bpy.context.active_object
    check_stage(export(out, "lod1"), "LoD1", "1", 6, 9.0)

    bpy.ops.cgml.roof_add(roof_type="3100")
    check_stage(export(out, "roof"), "gable", "2", 7, RIDGE)

    def front():
        return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                    and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)

    edit_one_face(front())
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front().name, face_index=0,
                             position_uv=(0.25, 0.6))
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front())
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front().name, face_index=0, position_uv=(0.75, 0.15))
    # B49: the flat fillings close the holes, so the LoD3 shell is a solid (7 + window + door)
    check_stage(export(out, "open"), "window+door", "3", 9, RIDGE)

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
