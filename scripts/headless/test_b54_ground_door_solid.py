"""B54: a ground-touching door keeps the building shell closed, so lod3Solid is still written.

A ground door is a notch in the wall's exterior ring (ADR 0004 D3). Its bottom edge lies on the
GroundSurface's edge; without the door's two bottom corners in the GroundSurface ring that edge is
a T-junction, the shell is not edge-closed and the writer silently drops lod3Solid (2.0 and 3.0).
- GroundSurface ring carries both bottom corners of the door
- 3.0 and 2.0: building lod3Solid, every member resolves, the shell is edge-closed
- the wall is notched (no interior ring), the DoorSurface / Door is one polygon in the wall plane
- a window next to it still makes a hole; XSD valid

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b54_ground_door_solid.py -- <out_dir>
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
from test_b49_opening_filling import element_blocks, front_wall, rings  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b54_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    wg = import_module(f"{addon}.shared.writer_geometry")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8, 9 m
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    wall = front_wall(b)
    zs = [(wall.matrix_world @ v.co).z for v in wall.data.vertices]
    z0, hh = min(zs), max(zs) - min(zs)
    wall_y = round((wall.matrix_world @ wall.data.vertices[0].co).y, 4)

    edit_one_face(front_wall(b))
    res = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0,
                                 position_uv=(0.5, 1.05 / hh), width=1.0, height=2.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    check("ground door placed", res == {'FINISHED'}, str(res))
    door = next(o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Door", "DoorSurface"))
    check("door is a ground notch", door.get("cgml_authoring_topology") == "ground_notch",
          str(door.get("cgml_authoring_topology")))
    edit_one_face(front_wall(b))
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front_wall(b).name, face_index=0,
                             position_uv=(0.2, 1.5 / hh), width=1.2, height=1.4)
    bpy.ops.object.mode_set(mode='OBJECT')

    ground = next(o for o in b.children_recursive if o.get("cgml3_feature") == "GroundSurface")
    gpts = {tuple(round(c, 4) for c in ground.matrix_world @ v.co) for v in ground.data.vertices}
    for x in (-0.5, 0.5):
        check(f"GroundSurface has the door corner x={x}", (x, wall_y, round(z0, 4)) in gpts, str(sorted(gpts)))

    for v in ("3.0", "2.0"):
        p = os.path.join(out, f"b54_v{v[0]}.gml")
        bpy.ops.object.select_all(action='DESELECT')
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        wblk = [blk for gid, blk in element_blocks(text, "WallSurface") if gid == str(front_wall(b).get("gml_id"))][0]
        own = wblk.split("<con:fillingSurface")[0].split("<bldg:opening")[0]
        check(f"{v}: wall notched (9-point exterior) + one window hole",
              len(rings(own)[0]) == 9 and own.count("<gml:interior>") == 1,
              f"{len(rings(own)[0])} pts, {own.count('<gml:interior>')} interior")
        doors = element_blocks(wblk, "DoorSurface") + element_blocks(wblk, "Door")
        check(f"{v}: one door, one polygon in the wall plane",
              len(doors) == 1 and len(rings(doors[0][1])) == 1
              and all(y == wall_y for _x, y, _z in rings(doors[0][1])[0]), str(len(doors)))
        n_b = text.find("<bldg:buildingInstallation>")
        head = text if n_b < 0 else text[:n_b]
        solid = re.findall(r"<(?:bldg:)?lod(\d)Solid>(.*?)</(?:bldg:)?lod\1Solid>", head, re.S)
        check(f"{v}: building lod3Solid", [l for l, _ in solid] == ["3"], str([l for l, _ in solid]))
        if solid:
            members = re.findall(r'xlink:href="#([^"]+)"', solid[0][1])
            polys = dict(element_blocks(head, "Polygon"))
            check(f"{v}: 9 members (7 boundary + door + window), all resolve",
                  len(members) == 9 and set(members) <= set(polys), f"{len(members)}")
            check(f"{v}: the referenced shell is closed",
                  wg.shell_is_closed([r for m in members if m in polys for r in rings(polys[m])]))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
