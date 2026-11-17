"""B27: New Building draws its footprint in the viewport.

- from a button (INVOKE in a 3D view; live Blender only) New Building starts drawing: modal, the
  blue outline + length labels are registered, the status bar says what to do
- Esc/cancel removes the drawing (no handler left, status bar cleared)
- in -b (no event loop) the button builds nothing and says why (B34)
- a drawn footprint (``points``) builds a LoD1 block of that shape: an L of six
  corners -> 6 walls, 1 roof, 1 ground, the typed height; F9 hides
  Width/Depth/Rotation for a drawn one
- a script call (EXEC) without ``points`` builds nothing (B34; was 10 x 8 m at the 3D cursor)
- the new building is framed (its own mesh is empty, its surfaces are not)
- the build row starts New Building in the viewport region (INVOKE_REGION_WIN)
(The mouse steps themselves are unit-tested: scripts/tests/unit/test_b27_footprint_draw.py.)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b27_draw_building.py
"""
import json
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

L_SHAPE = [[0, 0, 0], [10, 0, 0], [10, 5, 0], [5, 5, 0], [5, 10, 0], [0, 10, 0]]


def kinds(obj):
    out = {}
    for o in obj.children_recursive:
        k = str(o.get("cgml3_feature"))
        out[k] = out.get(k, 0) + 1
    return out


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ba = import_module(f"{addon}.ops.building_authoring")
    tools = import_module(f"{addon}.ui.tools")

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)

        try:
            res = bpy.ops.cgml.building_new('INVOKE_DEFAULT')
        except RuntimeError as e:  # B34: its ERROR report
            res = str(e)
        if bpy.app.background:  # -b: no drawing possible; B34: nothing is placed by itself
            check("background: the button builds nothing, says why",
                  "needs a footprint" in str(res) and not bpy.data.objects, str(res))
        else:
            check("button starts drawing (modal)", res == {'RUNNING_MODAL'}, str(res))
            check("outline + labels drawn", len(ba._draw_handles) == 2 and ba._drawing["state"] is not None)
            check("status bar: how to draw", "Drag a rectangle" in ba._drawing["state"].status())
            check("nothing built yet", not bpy.data.objects)
            ba._stop_drawing(bpy.context)
            check("cancel removes the drawing", not ba._draw_handles and ba._drawing["state"] is None)

        res = bpy.ops.cgml.building_new('EXEC_DEFAULT', points=json.dumps(L_SHAPE), height=7.5)
        b = bpy.context.active_object
        k = kinds(b)
        check("L footprint: 6 walls, 1 roof, 1 ground", res == {'FINISHED'}
              and k.get("WallSurface") == 6 and k.get("RoofSurface") == 1 and k.get("GroundSurface") == 1, str(k))
        top = max((o.matrix_world @ v.co).z for o in b.children_recursive for v in o.data.vertices)
        check("typed height", abs(top - 7.5) < 1e-4, str(top))
        xs = sorted({round((o.matrix_world @ v.co).x, 3) for o in b.children_recursive for v in o.data.vertices})
        check("drawn corners kept", xs == [0.0, 5.0, 10.0], str(xs))

        class Lay:
            def __init__(self):
                self.props = []

            def column(self):
                return self

            def prop(self, data, name, **k):
                self.props.append(name)
        lay = Lay()
        holder = type("H", (), {"layout": lay, "points": "x", "_draw_common": ba._Lod1Props._draw_common})()
        ba.CGML_OT_building_new.draw(holder, bpy.context)
        check("F9 of a drawn building: no Width/Depth/Rotation",
              "width" not in lay.props and "height" in lay.props, str(lay.props))

        ba._frame_new_building(bpy.context)
        check("framing keeps only the building selected",
              bpy.context.selected_objects == [b] and bpy.context.active_object == b)

        bpy.context.scene.cursor.location = (100, 50, 0)
        try:
            res = bpy.ops.cgml.building_new()
        except RuntimeError as e:
            res = str(e)
        check("script call without a footprint: nothing at the cursor (B34)", "needs a footprint" in str(res), str(res))

    src = open(tools.__file__, encoding="utf-8").read()
    check("row starts New Building in the viewport", "grp.operator_context = 'INVOKE_REGION_WIN'" in src)

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
