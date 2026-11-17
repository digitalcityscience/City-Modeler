"""B79: plain Blender objects (the startup Cube, a helper Plane) are not exported as Buildings.

Before, both writers guessed "Building" for any mesh without a CityGML type, so the startup Cube
became <bldg:Building gml:id="Cube"> (found with the user stories, 2026-11-09). Now they are skipped
and the export summary names them; Make CityGML still turns a mesh into an exported building.

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b79_skip_plain_objects.py
"""
import json
import os
import re
import sys
import tempfile
from importlib import import_module

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
    es = import_module(f"{addon}.shared.export_scope")
    check("factory scene has the startup Cube", "Cube" in bpy.data.objects)
    bpy.ops.mesh.primitive_plane_add(size=4, location=(40, 0, 0))
    bpy.context.active_object.name = "HelperPlane"
    bpy.ops.mesh.primitive_cube_add(size=6, location=(-40, 0, 3))
    made = bpy.context.active_object
    made.name = "MadeHouse"
    bpy.ops.cgml.make_citygml()
    check("Make CityGML marks the mesh", not es.is_plain_blender_object(made))
    bpy.ops.cgml.building_new(points=footprint(10, 8, centre=(0.0, 30.0, 0.0)), height=9.0)
    authored = bpy.context.view_layer.objects.active
    check("Cube / HelperPlane are plain", es.is_plain_blender_object(bpy.data.objects["Cube"])
          and es.is_plain_blender_object(bpy.data.objects["HelperPlane"]))
    for o in bpy.context.scene.objects:
        o.select_set(False)
    out = tempfile.mkdtemp()
    for v in ("3.0", "2.0"):
        path = os.path.join(out, f"b79_{v}.gml")
        check(f"{v}: export finished", bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=v) == {'FINISHED'})
        txt = open(path, encoding="utf-8").read()
        ids = re.findall(r"<bldg:Building gml:id=\"([^\"]+)\"", txt)
        check(f"{v}: no Cube / HelperPlane building", "Cube" not in ids and "HelperPlane" not in ids, str(ids))
        check(f"{v}: authored and Make CityGML buildings written",
              authored["gml_id"] in ids and made["gml_id"] in ids, str(ids))
        rec = json.loads(bpy.context.scene[es.LAST_EXPORT_KEY])
        check(f"{v}: record names the plain objects", sorted(rec["not_citygml"]) == ["Cube", "HelperPlane"],
              str(rec["not_citygml"]))
        check(f"{v}: summary says so", "without a CityGML type not written: Cube" in es.summary(rec), es.summary(rec))
    if FAILURES:
        raise SystemExit(f"FAILED: {FAILURES}")
    print("ALL OK")


main()
