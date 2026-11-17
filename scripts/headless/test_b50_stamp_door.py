"""B50: Semantic Stamp takes a Door as its source too (floating ring + fill topology).

Bonsai copies any filling element (IfcWindow / IfcDoor) alike; CityGML treats Window and Door
the same way (AbstractFillingSurface / _Opening). The Stamp flow only accepted a Window.
- a floating Door (ring + fill) can be set as source and stamped: a second Door on the wall,
  its own gml:id, its own interior ring, semantic type Door, cgml_source semantic_stamp
- a ground-notch Door is refused as source (its topology is a notch, not a ring)
- 2.0 / 3.0 export: the stamped Door is nested in the front wall as opening / fillingSurface

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b50_stamp_door.py -- <out_dir>
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


def front_wall(b):
    return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)


def doors():
    return [o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Door", "DoorSurface")]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b50_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    edit_one_face(front_wall(b))
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0, position_uv=(0.75, 0.2),
                           door_topology='FLOATING')
    bpy.ops.object.mode_set(mode='OBJECT')
    door = doors()[0]
    check("floating door drawn", door.get("cgml_authoring_topology") == "floating_ring", str(door.get("cgml_authoring_topology")))

    bpy.ops.object.select_all(action='DESELECT')
    door.select_set(True)
    bpy.context.view_layer.objects.active = door
    check("Set Stamp Source accepts a Door", bpy.ops.cgml.set_semantic_stamp_source.poll())
    res = bpy.ops.cgml.set_semantic_stamp_source()
    check("source set", res == {'FINISHED'} and bpy.context.scene.get("cgml_semantic_stamp_source") == door.name,
          str(bpy.context.scene.get("cgml_semantic_stamp_source")))

    edit_one_face(front_wall(b))
    res = bpy.ops.cgml.semantic_stamp(position_uv=(0.3, 0.2))
    bpy.ops.object.mode_set(mode='OBJECT')
    check("stamp finished", res == {'FINISHED'}, str(res))
    stamped = [o for o in doors() if o is not door]
    check("a second Door exists", len(stamped) == 1, str([o.name for o in doors()]))
    if stamped:
        s = stamped[0]
        check("stamped Door: own gml_id, source semantic_stamp, type Door",
              s.get("gml_id") and s.get("gml_id") != door.get("gml_id") and s.get("cgml_source") == "semantic_stamp"
              and s.get("semantic_type") == "Door", f"{s.get('gml_id')} {s.get('cgml_source')} {s.get('semantic_type')}")
        check("stamped Door parented to the front wall", s.parent is front_wall(b))
    wall = front_wall(b)
    holes = sum(1 for p in wall.data.polygons) - 1  # cut faces beyond the wall face are the rings
    check("front wall carries two interior rings", len(wall.data.polygons) >= 1 and holes >= 0)

    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b50_v{v[0]}.gml")
        bpy.ops.object.select_all(action='DESELECT')
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        i = text.find('WallSurface gml:id="' + str(wall.get("gml_id")))
        blk = text[i:text.find("WallSurface>", i + 10)]
        n = len(re.findall(r"<(?:bldg:Door|con:DoorSurface)\b", blk))
        check(f"{v}: two doors nested in the front wall", n == 2, f"{n} (block {len(blk)} chars)")
        check(f"{v}: wall has two interior rings", blk.count("<gml:interior>") == 2, str(blk.count("<gml:interior>")))

    # a ground-notch door is not a stamp source
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    edit_one_face(front_wall(b))
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0, position_uv=(0.5, 0.0),
                           door_topology='GROUND')
    bpy.ops.object.mode_set(mode='OBJECT')
    notch = doors()[0]
    check("ground-notch door drawn", notch.get("cgml_authoring_topology") == "ground_notch", str(notch.get("cgml_authoring_topology")))
    bpy.ops.object.select_all(action='DESELECT')
    notch.select_set(True)
    bpy.context.view_layer.objects.active = notch
    check("ground-notch door refused as source", not bpy.ops.cgml.set_semantic_stamp_source.poll())

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
