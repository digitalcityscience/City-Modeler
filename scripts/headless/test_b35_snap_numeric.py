"""B35: snapping and typed numbers.

- one snap code path (Openings_Cutter, Window / Door's) now also snaps to an edge's midpoint
  (not just any point on the edge) and to Blender's Increment grid, kept on the surface plane
- Balcony / Chimney (click and T tool), Drag on Wall and the New Building footprint go through it
- New Building: after a corner, typed digits give the next edge's length (the mouse gives the
  direction), Backspace edits, Enter places the corner; the status bar shows the typed number.
  Driven through the operator's modal with key events.

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b35_snap_numeric.py
"""
import inspect
import os
import sys
import types
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def snap_ctx(*elements, grid=1.0):
    ts = types.SimpleNamespace(use_snap=True, snap_elements=set(elements))
    return types.SimpleNamespace(scene=types.SimpleNamespace(tool_settings=ts), region=None,
                                 space_data=types.SimpleNamespace(region_3d=None,
                                                                  overlay=types.SimpleNamespace(grid_scale=grid)))


class Ev:
    def __init__(self, type_, value='PRESS'):
        self.type, self.value = type_, value
        self.ctrl = self.alt = self.shift = False
        self.mouse_region_x = self.mouse_region_y = 0


def close(a, b, tol=1e-4):
    return a is not None and all(abs(x - y) < tol for x, y in zip(a, b))


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    oc = import_module(f"{addon}.Openings_Cutter.Openings_Cutter")
    ia = import_module(f"{addon}.ops.installation_authoring")
    ba = import_module(f"{addon}.ops.building_authoring")
    fd = import_module(f"{addon}.shared.footprint_draw")

    # --- the shared snap path -------------------------------------------------------------
    cache = [{"vertices_world": [Vector((0, 0, 0)), Vector((1, 0, 0))], "edge_indices": [(0, 1)],
              "vertices_screen": [Vector((0.0, 0.0)), Vector((100.0, 0.0))], "bbox_screen_rect": (0, 100, 0, 0)}]
    xy, _c, snapped = oc.resolve_opening_cutter_snap_mouse(snap_ctx('EDGE_MIDPOINT'), (45.0, 5.0), cache)
    check("edge midpoint: the midpoint itself", snapped and close(xy, (50.0, 0.0)), str(xy))
    xy, _c, snapped = oc.resolve_opening_cutter_snap_mouse(snap_ctx('EDGE'), (45.0, 5.0), cache)
    check("edge: the nearest point on it", snapped and close(xy, (45.0, 0.0)), str(xy))
    xy, _c, snapped = oc.resolve_opening_cutter_snap_mouse(snap_ctx('VERTEX'), (3.0, 4.0), cache)
    check("vertex", snapped and close(xy, (0.0, 0.0)), str(xy))
    inc = getattr(oc, "snap_point_to_increment", None)
    check("increment helper exists", inc is not None)
    if inc is not None:
        ctx = snap_ctx('INCREMENT', grid=0.5)
        check("increment on a wall: along the wall and up, stays on the wall",
              close(inc(ctx, (1.3, -4.0, 2.6), (0, -1, 0)), (1.5, -4.0, 2.5)), str(inc(ctx, (1.3, -4.0, 2.6), (0, -1, 0))))
        n = Vector((0, -0.5, 0.866)).normalized()
        p = inc(ctx, Vector((0.3, -1.2, 6.0)), n)
        check("increment on a roof: x, y on the grid, z back on the roof plane",
              close(p[:2], (0.5, -1.0)) and abs((p - Vector((0.3, -1.2, 6.0))).dot(n)) < 1e-6, str(p))
        check("increment off: unchanged", close(inc(snap_ctx('VERTEX'), (1.3, -4.0, 2.6), (0, -1, 0)),
                                                 (1.3, -4.0, 2.6)))

    check("Balcony / Chimney click and T tool snap the mouse",
          "resolve_opening_cutter_snap_mouse" in inspect.getsource(ia.ray_under_mouse)
          and inspect.getsource(ia._ClickPlace.modal).count("ray_under_mouse(context, event, self)") == 2
          and "ray_under_mouse(context, event, self)" in inspect.getsource(ia._ClickPlace.invoke))
    check("Balcony / Chimney: increment on the clicked surface",
          "snap_point_to_increment" in inspect.getsource(ia._ClickPlace.candidate_at))
    check("Drag on Wall snaps", "ray_under_mouse(context, event, self)" in inspect.getsource(ia.CGML_OT_balcony_draw._on_wall)
          and "snap_point_to_increment" in inspect.getsource(ia.CGML_OT_balcony_draw._on_wall))
    gp = inspect.getsource(ba._ground_point)
    check("New Building footprint snaps", "resolve_opening_cutter_snap_mouse" in gp and "snap_point_to_increment" in gp)

    # --- typed edge length, driven through the modal ---------------------------------------
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    state = fd.FootprintDraw()
    state.press((0.0, 0.0, 0.0), (100, 100))
    state.release((0.0, 0.0, 0.0), (100, 100))
    state.move((3.0, 4.0, 0.0), (130, 140))  # the mouse gives the direction
    ba._drawing["state"] = state
    op = types.SimpleNamespace(report=lambda *a: None, execute=lambda ctx: {"FINISHED"})
    with bpy.context.temp_override(window=win, area=area, region=region):
        for key in ("ONE", "ZERO"):
            ba.CGML_OT_building_new.modal(op, bpy.context, Ev(key))
        check("typing 10: the next corner 10 m towards the mouse", close(state.hover, (6.0, 8.0, 0.0)), str(state.hover))
        check("status bar shows the typed length", "Edge length 10" in state.status(), state.status())
        ba.CGML_OT_building_new.modal(op, bpy.context, Ev('BACK_SPACE'))
        check("Backspace edits the number (1 m)", close(state.hover, (0.6, 0.8, 0.0)), str(state.hover))
        for key in ("TWO", "PERIOD", "FIVE", "RET"):
            ba.CGML_OT_building_new.modal(op, bpy.context, Ev(key))
        check("Enter places the corner at 12.5 m", len(state.points) == 2 and close(state.points[-1], (7.5, 10.0, 0.0))
              and state.typed == "", str(state.points))
        state.move((7.5, 0.0, 0.0), (200, 100))
        for key in ("FOUR", "RET"):
            ba.CGML_OT_building_new.modal(op, bpy.context, Ev(key))
        check("next edge: 4 m straight down towards the mouse", close(state.points[-1], (7.5, 6.0, 0.0)), str(state.points))
        ba.CGML_OT_building_new.modal(op, bpy.context, Ev('RET'))
        check("Enter without a number still closes (3 corners)", state.phase == "HEIGHT", state.phase)
        for key in ("SIX", "RET"):
            ba.CGML_OT_building_new.modal(op, bpy.context, Ev(key))
    ba._stop_drawing(bpy.context)
    check("height still typed (6 m) after typed edges", state.height == 6.0, str(state.height))
    fresh = fd.FootprintDraw()
    check("no typing before the first corner", fresh.key("5") is False and fresh.typed == "")

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
