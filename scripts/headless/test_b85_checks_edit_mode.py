"""B85: the check / repair buttons of 3 · Save work from Edit Mode.

Adding a window or a chimney leaves the user in Edit Mode, where Check Planarity, Fix Planarity,
Check Geometry and the repairs were greyed out (poll: Object Mode only) without saying why.

- in Edit Mode on a wall of an authored building with a window: all four polls are true;
- each button finishes and Blender is back in Edit Mode on the same wall;
- a vertex pulled off its plane *in Edit Mode* (never written to the object by hand) is found and
  flattened by Fix Planarity: the buttons see the edit mesh.

Old code: every poll is false in Edit Mode.

Usage:
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b85_checks_edit_mode.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bmesh  # noqa: E402
import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def off_plane(wall):
    """Largest distance of an edit-mesh vertex of ``wall`` from the plane of its first face."""
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    f = max(bm.faces, key=lambda f: f.calc_area())
    return max(abs((v.co - f.verts[0].co).dot(f.normal)) for v in f.verts)


def main():
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    api = import_module(f"{addon}.shared.agent_api")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint(width=12.0, depth=8.0))
    bid = str(bpy.context.active_object["gml_id"])
    api.add_roof(bid, "3100")
    b = api.inspect(bid)["buildings"][0]
    surf, face = next((s, f) for s in b["surfaces"] if s["type"] == "WallSurface" for f in s["faces"]
                      if f["can_host"])
    api.add_opening(surf["gml_id"], face["index"], 0.25, 0.5, 1.2, 1.4)
    # a wall without the window: its big face keeps four corners
    wall = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "WallSurface"
                and o.get("gml_id") != surf["gml_id"] and len(o.data.polygons) == 1)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode='EDIT')

    print("\n[B85] --- buttons in Edit Mode ---")
    ops = {"check_planarity": {}, "fix_planarity": {}, "check_geometry": {},
           "repair_geometry": {"code": 'SE_BS_UNFRAGMENTED'}}
    for name in ops:
        check(f"{name}: enabled in Edit Mode", getattr(bpy.ops.cgml, name).poll())
    for name, kw in ops.items():
        try:
            r = getattr(bpy.ops.cgml, name)(**kw)
        except RuntimeError as exc:
            r = str(exc)
        check(f"{name}: finishes, back in Edit Mode on the wall",
              r == {'FINISHED'} and bpy.context.mode == 'EDIT_MESH' and bpy.context.active_object == wall,
              f"{r} {bpy.context.mode}")

    print("\n[B85] --- the buttons see the edit mesh ---")
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    f = bm.faces[0]
    f.verts[0].co += f.normal * 0.5
    bmesh.update_edit_mesh(wall.data)
    check("vertex pulled 0.5 m off the plane in Edit Mode", off_plane(wall) > 0.1, f"{off_plane(wall):.3f}")
    r = bpy.ops.cgml.fix_planarity()
    check("Fix Planarity flattens it without leaving Edit Mode by hand",
          r == {'FINISHED'} and bpy.context.mode == 'EDIT_MESH' and off_plane(wall) < 0.011,
          f"{r} {bpy.context.mode} {off_plane(wall):.4f}")

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] B85 checks from Edit Mode")


try:
    main()
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
