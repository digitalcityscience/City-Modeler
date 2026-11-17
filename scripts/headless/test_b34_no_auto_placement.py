"""B34: no computed auto-placement on the script path either.

- New Building without a footprint (EXEC, or INVOKE without a 3D view) builds nothing and says
  why; there is no 10 x 8 m rectangle at the 3D cursor any more (no Width / Depth / Rotation)
- Balcony without a click (no host / point / normal): nothing, a clear message; also not on the
  selected wall (was: wall centre + 3 m) nor on the Edit-Mode face (was: face centre + 3 m)
- Chimney the same (was: roof face centre / 0.3 along the selected roof)
- explicit input still works: points for New Building, host + point + normal for the others
- the UI keeps starting the interactive placement (INVOKE_REGION_WIN; checked in B27 / B29)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b34_no_auto_placement.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES, activate  # noqa: E402
from test_b28_balcony_chimney_gallery import edit_face  # noqa: E402


def run(op, *args, **kw):
    """Operator result, or the ERROR text it raised."""
    try:
        return op(*args, **kw)
    except RuntimeError as e:
        return str(e)


def count():
    return len(bpy.data.objects)


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    # --- New Building --------------------------------------------------------------
    bpy.context.scene.cursor.location = (100, 50, 0)
    res = run(bpy.ops.cgml.building_new)
    check("New Building without a footprint: nothing, says why",
          "needs a footprint" in str(res) and count() == 0, f"{res} / {count()} objects")
    props = bpy.ops.cgml.building_new.get_rna_type().properties.keys()
    check("no Width / Depth / Rotation (they only served the cursor rectangle)",
          not {"width", "depth", "angle"} & set(props), str(props))
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type != 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area):
        res = run(bpy.ops.cgml.building_new, 'INVOKE_DEFAULT')
    check("INVOKE without a 3D view: nothing either", count() == 0 and res != {'FINISHED'}, str(res))
    res = run(bpy.ops.cgml.building_new, points=footprint(10, 8), height=9.0)
    b = bpy.context.active_object
    check("an explicit footprint builds where it says", res == {'FINISHED'} and b is not None
          and abs(min((o.matrix_world @ v.co).x for o in b.children_recursive for v in o.data.vertices) + 5) < 1e-4,
          str(res))
    bpy.ops.cgml.roof_add(roof_type="3100")
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    roof = next(o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface")
    front = min(walls, key=lambda o: min((o.matrix_world @ v.co).y for v in o.data.vertices))

    # --- Balcony / Chimney ------------------------------------------------------------
    n = count()
    activate(front)
    res = run(bpy.ops.cgml.balcony_add)
    check("Balcony on the selected wall without a click: nothing, says why",
          "Click a wall, door or window where it goes" in str(res) and count() == n, str(res))
    edit_face(front, lambda nn, c: -nn.y)
    res = run(bpy.ops.cgml.balcony_add, 'EXEC_DEFAULT', kind='PROJECTING')
    check("Balcony on the Edit-Mode face without a click: nothing", "where it goes" in str(res) and count() == n,
          str(res))
    res = run(bpy.ops.cgml.balcony_add, 'EXEC_DEFAULT', host=b.name, normal=(0, -1, 0))
    check("Balcony with a host but no point: nothing", "where it goes" in str(res) and count() == n, str(res))
    bpy.ops.object.mode_set(mode='OBJECT')
    activate(roof)
    res = run(bpy.ops.cgml.chimney_add)
    check("Chimney on the selected roof without a click: nothing, says why",
          "Click a roof where it goes" in str(res) and count() == n, str(res))
    edit_face(roof, lambda nn, c: nn.z)
    res = run(bpy.ops.cgml.chimney_add, 'EXEC_DEFAULT')
    check("Chimney on the Edit-Mode face without a click: nothing", "where it goes" in str(res) and count() == n,
          str(res))
    bpy.ops.object.mode_set(mode='OBJECT')
    props = bpy.ops.cgml.chimney_add.get_rna_type().properties.keys()
    check("no scripted 'position along roof' / 'floor height'", "position" not in props
          and "floor_height" not in bpy.ops.cgml.balcony_add.get_rna_type().properties.keys())

    wall_y = min((front.matrix_world @ v.co).y for v in front.data.vertices)
    res = run(bpy.ops.cgml.balcony_add, host=b.name, point=(1.0, wall_y, 2.5), normal=(0, -1, 0))
    bal = bpy.context.active_object
    zs = [(bal.matrix_world @ v.co).z for v in bal.data.vertices]
    check("explicit host + point + normal: balcony exactly there", res == {'FINISHED'} and abs(min(zs) - 2.3) < 1e-4,
          f"{res} {min(zs) if zs else None}")
    rz = max((roof.matrix_world @ v.co).z for v in roof.data.vertices)
    res = run(bpy.ops.cgml.chimney_add, host=b.name, point=(-2.0, -1.0, rz - 1.0), normal=(0, -0.5, 0.86))
    ch = bpy.context.active_object
    xs = [(ch.matrix_world @ v.co).x for v in ch.data.vertices]
    check("explicit chimney: exactly there", res == {'FINISHED'} and abs((min(xs) + max(xs)) / 2 + 2.0) < 1e-4,
          str(res))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
