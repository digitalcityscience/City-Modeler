"""B67: a balcony picked from the gallery / tool gets its size from the clicked wall.

- balcony_size: width 60 % of the wall (max 4 m, never wider than the wall), depth half the
  width (0.6-1.5 m); a door / window fit keeps its width, depth follows it
- wall_extent: only the faces in the clicked wall plane count (a box: one side, not the box)
- a small building (3 x 2.1 m, the user's case): the balcony fits the 2.1 m side wall
- a click near a wall end: the balcony slides back onto the wall
- a long wall: 4 x 1.5 m; a rotated building: the same rule along the wall
- Corner: sized from the wall; door click: depth follows the fitted width
- the preview is the placed balcony; F9 (an explicit width) still wins
- drag on wall: depth follows the dragged width

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b67_wall_sized_balcony.py
"""
import math
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def fake(ia, kind):
    op = ia.CGML_OT_balcony_add
    return type("F", (), {"kind": kind, "place_from_hit": op.place_from_hit, "boxes": op.boxes, "WANT": op.WANT,
                          "MISS": op.MISS, "offset": 0.0, "railing": 1.0, "width": 3.0, "depth": 1.4,
                          "corner_side": 'END', "point": (0, 0, 0), "normal": (0, -1, 0)})()


def world(obj):
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def new_building(width, depth, angle=0.0):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.cgml.building_new(points=footprint(width, depth, angle=math.radians(angle)))
    return bpy.context.active_object


def click(ia, kind, along, z, wall_len_axis, angle=0.0):
    """Click the wall whose outward normal is -y (``wall_len_axis`` 'x') or +x ('y') of an unrotated
    or ``angle``-rotated building, at ``along`` metres from the wall centre, height ``z``."""
    a = math.radians(angle)
    rot = lambda v: Vector((v.x * math.cos(a) - v.y * math.sin(a), v.x * math.sin(a) + v.y * math.cos(a), v.z))
    if wall_len_axis == 'x':
        origin, direction = rot(Vector((along, -50, z))), rot(Vector((0, 1, 0)))
    else:
        origin, direction = rot(Vector((50, along, z))), rot(Vector((-1, 0, 0)))
    f = fake(ia, kind)
    return ia._ClickPlace.candidate_at(f, bpy.context, origin, direction), f


def along_span(pts, n):
    h = Vector((-n[1], n[0], 0.0))
    s = [p.dot(h) for p in pts]
    return min(s), max(s)


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ia = import_module(f"{addon}.ops.installation_authoring")

    # --- the rule --------------------------------------------------------------------------
    near = lambda a, b: abs(a - b) < 1e-6
    w, d = ia.balcony_size(2.1)
    check("2.1 m wall: 1.26 x 0.63 m", near(w, 1.26) and near(d, 0.63), f"{w} {d}")
    w, d = ia.balcony_size(20.0)
    check("20 m wall: capped at 4 x 1.5 m", near(w, 4.0) and near(d, 1.5), f"{w} {d}")
    w, d = ia.balcony_size(0.6)
    check("0.6 m wall: the 0.5 m minimum, depth 0.6", near(w, 0.5) and near(d, 0.6), f"{w} {d}")
    w, d = ia.balcony_size(0, 1.4)
    check("fitted width kept, depth follows", near(w, 1.4) and near(d, 0.7), f"{w} {d}")

    # --- wall_extent on a plain box: only the clicked side --------------------------------
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(1.0, 2.0, 0.5), scale=(4.0, 2.0, 1.0))
    box = bpy.context.active_object
    bpy.context.view_layer.update()
    ext = ia.wall_extent(box, Vector((1.0, 1.0, 0.5)), Vector((0, -1, 0)))  # front side y = 1, 4 m long
    check("wall_extent: the 4 m front side only", ext is not None and near(ext[1] - ext[0], 4.0), str(ext))
    check("wall_extent: none for a plane nobody clicked",
          ia.wall_extent(box, Vector((1.0, 0.0, 0.5)), Vector((0, -1, 0))) is None)

    # --- the user's case: a 3 x 2.1 m building ---------------------------------------------
    b = new_building(3.0, 2.1)
    cand, f = click(ia, 'PROJECTING', 0.0, 4.0, 'y')  # the 2.1 m side wall (x = +1.5)
    check("small side wall: 1.26 x 0.63 m", cand is not None and near(round(cand["width"], 6), 1.26)
          and near(round(cand["depth"], 6), 0.63), str(cand))
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    bal = bpy.context.active_object
    lo, hi = along_span(world(bal), cand["normal"])
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    side = max(walls, key=lambda o: min(p.x for p in world(o)))
    wlo, whi = along_span(world(side), cand["normal"])
    check("placed on the 2.1 m wall, inside its ends",
          res == {'FINISHED'} and lo >= wlo - 1e-4 and hi <= whi + 1e-4 and near(round(hi - lo, 4), 1.26),
          f"{lo:.3f}..{hi:.3f} in {wlo:.3f}..{whi:.3f}")
    xs = [p.x for p in world(bal)]
    check("sticks out 0.63 m", abs(max(xs) - 1.5 - 0.63) < 1e-3, f"{max(xs) - 1.5:.3f}")
    pv = [Vector(p) for p in ia.box_edges(f.boxes(cand))]
    bbox = lambda pts: [round(fn(p[i] for p in pts), 4) for i in range(3) for fn in (min, max)]
    check("preview = placed balcony", bbox(pv) == bbox(world(bal)), f"{bbox(pv)} vs {bbox(world(bal))}")

    # --- a click near the end slides the balcony onto the wall ---------------------------
    cand, _f = click(ia, 'PROJECTING', 1.4, 4.0, 'x')  # the 3 m front wall, 0.1 m from its end
    bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    xs = [p.x for p in world(bpy.context.active_object)]
    check("near the end: the 1.8 m balcony ends at the wall end", abs(max(xs) - 1.5) < 1e-3
          and abs(max(xs) - min(xs) - 1.8) < 1e-3, f"{min(xs):.3f}..{max(xs):.3f}")

    # --- F9: an explicit width wins ---------------------------------------------------------
    bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **{**cand, "width": 1.0, "depth": 0.8})
    xs = [p.x for p in world(bpy.context.active_object)]
    check("F9 width 1.0 kept", abs(max(xs) - min(xs) - 1.0) < 1e-3, f"{max(xs) - min(xs):.3f}")

    # --- Corner: sized from the wall ------------------------------------------------------
    cand, _f = click(ia, 'CORNER', -1.0, 4.0, 'x')  # the left half: the balconies above sit on the right
    check("Corner on the 3 m wall: width 1.8, at the nearer (left) end", cand is not None
          and near(round(cand["width"], 6), 1.8) and abs(cand["point"][0] + 1.5) < 1e-3
          and cand["corner_side"] == 'START', str(cand))

    # --- a long wall and a rotated building --------------------------------------------
    new_building(20.0, 8.0)
    cand, _f = click(ia, 'PROJECTING', 0.0, 4.0, 'x')
    check("20 m wall: 4 x 1.5 m", cand is not None and near(cand["width"], 4.0) and near(cand["depth"], 1.5), str(cand))
    new_building(5.0, 4.0, angle=30.0)
    cand, _f = click(ia, 'PROJECTING', 0.0, 4.0, 'x', angle=30.0)
    check("rotated 5 m wall: 3 x 1.5 m", cand is not None and abs(cand["width"] - 3.0) < 1e-3
          and abs(cand["depth"] - 1.5) < 1e-3, str(cand))

    # --- door click: depth follows the fitted width ------------------------------------
    b = new_building(6.0, 5.0)
    wall = min((o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"),
               key=lambda o: min(p.y for p in world(o)))
    door = bpy.data.objects.new("DoorSurface_b67", bpy.data.meshes.new("door"))
    bpy.context.scene.collection.objects.link(door)
    y = min(p.y for p in world(wall)) - 0.01
    door.data.from_pydata([(-0.5, y, 2.0), (0.5, y, 2.0), (0.5, y, 4.1), (-0.5, y, 4.1)], [], [[0, 1, 2, 3]])
    door["cgml3_feature"] = "DoorSurface"
    door.parent = wall
    door.matrix_parent_inverse = wall.matrix_world.inverted()
    bpy.context.view_layer.update()
    cand, _f = click(ia, 'PROJECTING', 0.0, 3.0, 'x')
    check("door click: width 1.0 + 0.8, depth 0.9", cand is not None and abs(cand["width"] - 1.8) < 1e-3
          and abs(cand["depth"] - 0.9) < 1e-3 and abs(cand["point"][2] - 2.0) < 1e-3, str(cand))

    # --- drag on wall ------------------------------------------------------------------------
    dc = {"host": b.name, "normal": (0, -1, 0), "plane": (0, y, 3), "lo": -3.0, "hi": 3.0}
    args = ia.drag_to_balcony(dc, (-1, y, 3), (1, y, 4), 'PROJECTING')
    check("drag: depth follows the dragged 2 m", abs(args["width"] - 2.0) < 1e-6 and abs(args["depth"] - 1.0) < 1e-6,
          str(args))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
