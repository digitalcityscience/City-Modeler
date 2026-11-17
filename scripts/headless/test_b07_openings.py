"""B07: window/door types and Array on Wall.

1. The sidebar type pickers exist and give DIN 18100 door sizes.
2. New Building (16 x 8 x 7 m) + gable roof; Draw Window (1.20 x 1.40) on
   the long front wall; Array on Wall 4 columns x 2 rows, 3.0 m / 3.0 m
   -> 8 windows on that wall, all with the source size.
3. An array step that runs off the wall is skipped, not forced.
4. Export 3.0 and 2.0: one Building, 8 windows (3.0 WindowSurface fillings,
   2.0 bldg:Window), XSD valid.

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b07_openings.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bpy
from mathutils import Vector

from building_harness import check, count, export_checked, finish
from addon_test_utils import footprint  # B34: explicit footprint

ADDON = "city_modeler"


def windows(building):
    return [o for o in building.children_recursive if o.get("cgml3_feature") in ("WindowSurface", "Window")]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b07-out"
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    from city_modeler.shared import opening_types

    wm = bpy.context.window_manager
    check("window and door type pickers exist", hasattr(wm, "cgml_window_type") and hasattr(wm, "cgml_door_type"))
    check("DIN 18100 door 1.01 x 2.135", opening_types.size("D_101x2135", "Door") == (1.01, 2.135))
    check("a door key is not a window size", opening_types.size("D_101x2135", "Window") is None)

    bpy.ops.cgml.building_new(points=footprint(16, 8), height=7, epsg=25832)
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=35)
    # The long front wall: normal pointing to -Y.
    front = next(o for o in b.children if o.get("cgml3_feature") == "WallSurface"
                 and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.9)
    bpy.context.view_layer.objects.active = front
    for o in bpy.context.selected_objects:
        o.select_set(False)
    front.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    # Lower-left window: u=0.2 of 16 m -> 3.2 m, v=0.2 of 7 m -> 1.4 m.
    res = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front.name, face_index=0,
                                   position_uv=(0.2, 0.2), width=1.2, height=1.4)
    bpy.ops.object.mode_set(mode='OBJECT')
    check("first window placed", res == {'FINISHED'}, f"result={res}")
    first = windows(b)[0]
    for o in bpy.context.selected_objects:
        o.select_set(False)
    first.select_set(True)
    bpy.context.view_layer.objects.active = first
    res = bpy.ops.cgml.opening_array(columns=4, rows=2, step_x=3.0, step_z=3.0)
    check("Array on Wall finishes", res == {'FINISHED'}, f"result={res}")
    ws = windows(b)
    check("8 windows on the building", len(ws) == 8, f"{len(ws)}")
    sizes = set()
    for w in ws:
        pts = [w.matrix_world @ v.co for v in w.data.vertices]
        sizes.add((round(max(p.x for p in pts) - min(p.x for p in pts), 3), round(max(p.z for p in pts) - min(p.z for p in pts), 3)))
    check("all copies have the source size 1.20 x 1.40", sizes == {(1.2, 1.4)}, str(sizes))

    # 3. Step off the wall: 6 columns of 3 m from x=3.2 cannot all fit on 16 m.
    for o in bpy.context.selected_objects:
        o.select_set(False)
    first.select_set(True)
    bpy.context.view_layer.objects.active = first
    before = len(windows(b))
    bpy.ops.cgml.opening_array(columns=6, rows=1, step_x=3.0, step_z=3.0)
    added = len(windows(b)) - before
    check("copies that overlap or run off the wall are skipped", added == 1, f"added {added}")

    for o in bpy.context.selected_objects:
        o.select_set(False)
    n = len(windows(b))
    for version, tag in (("3.0", "con:WindowSurface"), ("2.0", "bldg:Window")):
        text = export_checked(os.path.join(out_dir, f"b07_v{version.replace('.', '')}.gml"), version)
        check(f"{version}: one Building", count(text, "bldg:Building") == 1)
        check(f"{version}: {n} windows", count(text, tag) == n, f"{count(text, tag)} vs {n}")

    finish("B07 openings")


main()
