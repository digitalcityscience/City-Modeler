"""B77: deleting a building (X / Delete) removes its surfaces, openings and installations too.

Before, only the building object went; its surfaces stayed visible as loose objects and the
export wrote each of them as a con:OtherConstruction feature (found with user story US24).

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b77_delete_takes_surfaces.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def main():
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    bpy.data.batch_remove(list(bpy.context.scene.objects))  # the factory Cube would be exported as a Building
    bpy.ops.cgml.building_new(points=footprint(10, 8, centre=(0.0, 0.0, 0.0)), height=9.0)
    keep = bpy.context.view_layer.objects.active
    bpy.ops.cgml.building_new(points=footprint(10, 8, centre=(30.0, 0.0, 0.0)), height=9.0)
    gone = bpy.context.view_layer.objects.active
    bpy.ops.cgml.roof_add(roof_type="3100")
    names = [gone.name] + [c.name for c in gone.children_recursive]
    gone_id = gone["gml_id"]
    check("building has surfaces", len(names) > 5, str(len(names)))
    for o in bpy.context.scene.objects:
        o.select_set(False)
    gone.select_set(True)
    bpy.context.view_layer.objects.active = gone
    check("delete finished", bpy.ops.cgml.guarded_delete() == {'FINISHED'})
    check("building and all its surfaces removed", not any(n in bpy.data.objects for n in names))
    check("the other building untouched", keep.name in bpy.data.objects and len(keep.children) >= 6)
    path = os.path.join(tempfile.mkdtemp(), "b77.gml")
    bpy.ops.cgml3.export_gml_file(filepath=path)
    txt = open(path, encoding="utf-8").read()
    check("export has no stray OtherConstruction", "OtherConstruction" not in txt)
    import re
    ids = re.findall(r"<bldg:Building gml:id=\"([^\"]+)\"", txt)
    check("export has only the kept building", ids == [keep["gml_id"]], f"{ids} keep={keep['gml_id']} gone={gone_id}")
    if FAILURES:
        raise SystemExit(f"FAILED: {FAILURES}")
    print("ALL OK")


main()
