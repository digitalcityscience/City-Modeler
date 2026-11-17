"""B76: a roof on a building away from the origin stays on its footprint.

The surface objects split from the building copied its world matrix and were parented without a
parent inverse, so the building's offset was applied twice (found with user story US17: footprint
at x 60..70 m, walls at 125..135 m after Roof). Every older test built at the origin.

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b76_roof_keeps_position.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def box(root):
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ v.co for o in root.children_recursive if o.type == 'MESH' for v in o.data.vertices]
    return ([round(min(p[i] for p in pts), 3) for i in range(3)], [round(max(p[i] for p in pts), 3) for i in range(3)])


def main():
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    for roof in ("3100", "3200", "1000"):
        bpy.ops.cgml.building_new(points=footprint(10, 8, centre=(65.0, -56.0, 0.0)), height=9.0)
        b = bpy.context.view_layer.objects.active
        check(f"{roof}: LoD1 on its footprint", box(b)[0][:2] == [60.0, -60.0], str(box(b)))
        bpy.ops.cgml.roof_add(roof_type=roof)
        lo, hi = box(b)
        check(f"{roof}: LoD2 still on its footprint", lo[:2] == [60.0, -60.0] and hi[:2] == [70.0, -52.0], f"{lo} {hi}")
        check(f"{roof}: surfaces have no own offset", all(c.location.length < 1e-6 for c in b.children))
    if FAILURES:
        raise SystemExit(f"FAILED: {FAILURES}")
    print("ALL OK")


main()
