"""B81: an entrance door is not a balcony door, semantically or geometrically; balcony drag is honest.

CityGML gives a door an address (2.0 bldg:Door/bldg:address, 3.0 con:DoorSurface/con:address), and an
entrance stands on the ground (ADR 0004 D3: a notch in the wall's outline). A balcony door floats
(ring + filling) and has no address.
- Draw Door with entrance=True, drawn 0.4 m above the ground: its bottom goes down to the wall's lower
  edge, its top stays, it is a ground notch and flagged as the entrance
- a balcony door (no entrance) on the upper floor stays a floating ring, no flag
- 3.0: the entrance DoorSurface carries con:address with the building's street; the balcony door none
- 2.0: the entrance bldg:Door carries bldg:address; the balcony door none; no internal key leaks; XSD valid
- the "Entrance door" type is the entrance role (the tool sets the operator's entrance from it)
- B81 soft snap: a door drawn within 10 cm of a balcony floor in front of it sits on that floor (top kept);
  30 cm above it or beside the balcony it stays where drawn
- drag_to_balcony: a tall drag sets the railing (the preview's top follows the mouse); a flat one keeps
  the header's

Usage: blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
    --python scripts/headless/test_b81_entrance_door.py -- <out_dir>
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
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402
from test_b49_opening_filling import element_blocks, front_wall  # noqa: E402


def zrange(obj):
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
    return min(zs), max(zs)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b81_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ot = import_module(f"{addon}.shared.opening_types")
    ia = import_module(f"{addon}.ops.installation_authoring")

    check("the Entrance door type is a door type", ot.size(ot.ENTRANCE, "Door") is not None)
    check("its label says it stands on the ground with the address", "address" in ot.OPENING_TYPES[ot.ENTRANCE][1])

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8, 9 m
    b = bpy.context.active_object
    b["bldg:address:street"] = "Pilatuspool"
    b["bldg:address:streetNumber"] = "11"
    b["bldg:address:locality"] = "Hamburg"
    bpy.ops.cgml.roof_add(roof_type="3100")
    z0, z1 = zrange(front_wall(b))
    hh = z1 - z0

    # entrance drawn floating (bottom 0.4 m up, top 2.5 m): it goes down to the ground
    edit_one_face(front_wall(b))
    res = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0,
                                 position_uv=(0.3, 1.45 / hh), width=1.1, height=2.1, entrance=True)
    wall_now = front_wall(b)
    check("B97 entrance (notch): the wall face stays selected in Edit Mode, like a window",
          wall_now.mode == 'EDIT' and wall_now.data.total_face_sel == 1, f"{wall_now.mode} {wall_now.data.total_face_sel}")
    bpy.ops.object.mode_set(mode='OBJECT')
    check("entrance door placed", res == {'FINISHED'}, str(res))
    doors = [o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Door", "DoorSurface")]
    entrance = doors[-1]
    check("entrance: ground notch", entrance.get("cgml_authoring_topology") == "ground_notch",
          str(entrance.get("cgml_authoring_topology")))
    check("entrance: flagged", bool(entrance.get("cgml_entrance_door")))
    lo, hi = zrange(entrance)
    check("entrance: bottom on the wall's lower edge, top where drawn",
          abs(lo - z0) < 0.01 and abs(hi - (z0 + 2.5)) < 0.01, f"{lo:.3f}..{hi:.3f} (wall {z0:.3f})")

    # balcony door: upper floor, no entrance
    edit_one_face(front_wall(b))
    res = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0,
                                 position_uv=(0.7, 4.05 / hh), width=0.9, height=2.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    check("balcony door placed", res == {'FINISHED'}, str(res))
    balcony_door = [o for o in bpy.data.objects
                    if str(o.get("cgml3_feature")) in ("Door", "DoorSurface") and o is not entrance][0]
    check("balcony door: floating ring, not the entrance",
          balcony_door.get("cgml_authoring_topology") == "floating_ring"
          and not balcony_door.get("cgml_entrance_door"), str(balcony_door.get("cgml_authoring_topology")))
    check("address owners: building for the entrance, none for the balcony door",
          ot.door_address_owner(entrance) is b and ot.door_address_owner(balcony_door) is None)

    for v, tag, addr in (("3.0", "DoorSurface", "<con:address>"), ("2.0", "Door", "<bldg:address>")):
        p = os.path.join(out, f"b81_v{v[0]}.gml")
        bpy.ops.object.select_all(action='DESELECT')
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        blocks = dict(element_blocks(text, tag))
        eb, bb = blocks.get(str(entrance["gml_id"]), ""), blocks.get(str(balcony_door["gml_id"]), "")
        check(f"{v}: entrance {tag} carries {addr} with the street", addr in eb and "Pilatuspool" in eb, eb[-600:])
        check(f"{v}: balcony door has no address", bool(bb) and "address" not in bb, bb[-300:])
        check(f"{v}: no internal key in the file", "cgml_entrance" not in text)
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    # soft snap: a door drawn a few cm off a balcony floor in front of it sits on that floor
    fw = front_wall(b)
    pts = [fw.matrix_world @ v.co for v in fw.data.vertices]
    cx, wy = sum(p.x for p in pts) / len(pts), min(p.y for p in pts)
    ia.make_balcony(bpy.context, b, (cx, wy, z0 + 3.0), (0.0, -1.0, 0.0), width=6.0, depth=1.2)

    def door_at(u, bottom):
        edit_one_face(front_wall(b))
        r = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0,
                                   position_uv=(u, (bottom + 1.05) / hh), width=0.9, height=2.1)
        bpy.ops.object.mode_set(mode='OBJECT')
        new = [o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Door", "DoorSurface")
               and o not in seen]
        seen.extend(new)
        return r, (zrange(new[0]) if new else (None, None))

    seen = [o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Door", "DoorSurface")]
    r, (lo, hi) = door_at(0.6, 3.06)
    check("6 cm above the balcony floor: on the floor, top kept",
          r == {'FINISHED'} and abs(lo - (z0 + 3.0)) < 1e-3 and abs(hi - (z0 + 5.16)) < 1e-3, f"{r} {lo}..{hi}")
    r, (lo, hi) = door_at(0.35, 3.3)
    check("30 cm above: left where drawn", r == {'FINISHED'} and abs(lo - (z0 + 3.3)) < 1e-3, f"{r} {lo}")
    r, (lo, hi) = door_at(0.1, 3.05)
    check("beside the balcony: left where drawn", r == {'FINISHED'} and abs(lo - (z0 + 3.05)) < 1e-3, f"{r} {lo}")

    wall = {"host": "B", "normal": (0, -1, 0), "plane": (0, -4, 4), "lo": -5.0, "hi": 5.0,
            "zmin": 0.0, "zmax": 9.0, "opening": False}
    a = ia.drag_to_balcony(wall, (-1, -4, 4.2), (2, -4, 3), 'PROJECTING', 2.5, 1.2, 1.0)
    check("tall drag: its height is the railing, its bottom the floor",
          abs(a["railing"] - 1.2) < 1e-6 and abs(a["point"][2] - 3) < 1e-6, str(a))
    a = ia.drag_to_balcony(wall, (-1, -4, 3), (2, -4, 3.1), 'PROJECTING', 2.5, 1.2, 1.1)
    check("flat drag: the header's railing", abs(a["railing"] - 1.1) < 1e-6, str(a))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
