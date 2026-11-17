"""B49: a window / door filling is one polygon in the wall plane; the LoD3 shell closes (A1).

CityGML models an opening as a hole (interior ring) in the wall and a filling surface
(3.0 con:fillingSurface / 2.0 bldg:opening) that closes it, so the building's Solid is closed
(OGC 12-019 10.3.3 / 10.3.6; OGC 20-010 Construction module). The authored prism (±2 cm) is
viewport-only now:
- 3.0 WindowSurface / DoorSurface and 2.0 Window / Door carry exactly one polygon, in the wall
  plane, with the same vertices as the wall's interior ring (reversed orientation)
- the host wall's own MultiSurface has one polygon with two interior rings, no filling copy
- the building gets lod3Solid in 3.0 and 2.0; its members are the 7 boundary polygons plus the 2
  fillings; every member resolves; the shell is edge-closed
- the viewport still shows the prism (6 faces), an F9 depth change does not reach the file
- a Stamp copy of the window exports flat as well

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b49_opening_filling.py -- <out_dir>
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
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402


def front_wall(b):
    return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)


def rings(block):
    """[[(x, y, z), ...]] of every posList in ``block``."""
    out = []
    for pl in re.findall(r"<gml:posList[^>]*>([^<]*)<", block):
        v = [float(t) for t in pl.split()]
        out.append([tuple(round(c, 4) for c in v[i:i + 3]) for i in range(0, len(v), 3)])
    return out


def element_blocks(text, tag):
    """(gml:id, inner text) of every <tag ...>...</tag> (prefix-agnostic)."""
    out = []
    for m in re.finditer(rf"<(?:\w+:)?{tag}\b[^>]*gml:id=\"([^\"]+)\"", text):
        end = text.find(f"{tag}>", m.end())
        out.append((m.group(1), text[m.start():end]))
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b49_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    wg = import_module(f"{addon}.shared.writer_geometry")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    edit_one_face(front_wall(b))
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front_wall(b).name, face_index=0,
                             position_uv=(0.25, 0.6), depth=0.1)  # F9-style depth: viewport only
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front_wall(b))
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0, position_uv=(0.75, 0.15))
    bpy.ops.object.mode_set(mode='OBJECT')
    win = next(o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Window", "WindowSurface"))
    check("viewport keeps the prism (6 faces)", len(win.data.polygons) == 6, str(len(win.data.polygons)))
    wall_y = round((front_wall(b).matrix_world @ front_wall(b).data.vertices[0].co).y, 4)

    for v in ("3.0", "2.0"):
        p = os.path.join(out, f"b49_v{v[0]}.gml")
        bpy.ops.object.select_all(action='DESELECT')
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        walls = [(gid, blk) for gid, blk in element_blocks(text, "WallSurface") if gid == str(front_wall(b).get("gml_id"))]
        check(f"{v}: front wall found", len(walls) == 1, str(len(walls)))
        if not walls:
            continue
        wblk = walls[0][1]
        own = wblk.split("<con:fillingSurface")[0].split("<bldg:opening")[0]
        check(f"{v}: wall MultiSurface = one polygon with two interior rings",
              own.count("<gml:Polygon") == 1 and own.count("<gml:interior>") == 2,
              f"{own.count('<gml:Polygon')} polys, {own.count('<gml:interior>')} interior")
        holes = [set(r) for r in rings(own)[1:]]
        fills = element_blocks(wblk, "WindowSurface") + element_blocks(wblk, "DoorSurface") \
            + element_blocks(wblk, "Window") + element_blocks(wblk, "Door")
        check(f"{v}: two fillings nested in the wall", len(fills) == 2, str(len(fills)))
        for gid, fblk in fills:
            fr = rings(fblk)
            check(f"{v}: filling {gid[:12]} is one polygon", len(fr) == 1, str(len(fr)))
            check(f"{v}: filling {gid[:12]} lies in the wall plane", all(y == wall_y for _x, y, _z in fr[0]),
                  str(sorted({y for _x, y, _z in fr[0]})))
            check(f"{v}: filling {gid[:12]} has the hole's vertices", set(fr[0]) in holes, str(fr[0][:2]))
        n_b = text.find("<bldg:buildingInstallation>")
        head = text if n_b < 0 else text[:n_b]
        solid = re.findall(r"<(?:bldg:)?lod(\d)Solid>(.*?)</(?:bldg:)?lod\1Solid>", head, re.S)
        check(f"{v}: building lod3Solid", [l for l, _ in solid] == ["3"], str([l for l, _ in solid]))
        if solid:
            members = re.findall(r'xlink:href="#([^"]+)"', solid[0][1])
            polys = {gid: blk for gid, blk in element_blocks(head, "Polygon")}
            check(f"{v}: 9 members (7 boundary + window + door), all resolve", len(members) == 9 and set(members) <= set(polys),
                  f"{len(members)} {sorted(set(members) - set(polys))[:2]}")
            shell = [r for m in members if m in polys for r in rings(polys[m])]
            check(f"{v}: the referenced shell is closed", wg.shell_is_closed(shell))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    # Stamp copy exports flat too
    bpy.ops.object.select_all(action='DESELECT')
    win.select_set(True)
    bpy.context.view_layer.objects.active = win
    bpy.ops.cgml.set_semantic_stamp_source()
    edit_one_face(front_wall(b))
    res = bpy.ops.cgml.semantic_stamp(position_uv=(0.5, 0.6))
    bpy.ops.object.mode_set(mode='OBJECT')
    check("stamp placed", res == {'FINISHED'}, str(res))
    p = os.path.join(out, "b49_stamp_v3.gml")
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    text = open(p, encoding="utf-8").read()
    ws = element_blocks(text, "WindowSurface")
    check("stamp: two WindowSurfaces, one polygon each in the wall plane",
          len(ws) == 2 and all(len(rings(blk)) == 1 and all(y == wall_y for _x, y, _z in rings(blk)[0]) for _g, blk in ws),
          str([(g[:10], len(rings(blk))) for g, blk in ws]))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
