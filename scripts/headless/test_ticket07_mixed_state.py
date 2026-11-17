"""Ticket 07: a mixed cgml_hierarchy_model state must halt the export and
name the offending building - never silently drop or guess.

Two cases:
1. Marker missing/legacy_mesh but semantic surface/opening children exist.
2. Marker semantic_objects but no semantic children exist.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def fresh_import(gml):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, res


def expect_export_error(path, label):
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=path)
    except Exception as e:
        print(f"[PASS] {label}: export raised as expected: {e}")
        return
    if res == {'CANCELLED'}:
        print(f"[PASS] {label}: export cancelled as expected")
        return
    print(f"[FAIL] {label}: export did NOT halt, result={res}")
    sys.exit(1)


def main():
    gml, out_dir = args()[0], args()[1]

    print("\n[test] --- Case 1: marker missing but semantic children exist ---")
    fresh_import(gml)
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    assert building.get("cgml_hierarchy_model") == "semantic_objects"
    del building["cgml_hierarchy_model"]
    expect_export_error(f"{out_dir}/mixed1.gml", "marker missing, children present")

    print("\n[test] --- Case 2: marker semantic_objects but no semantic children ---")
    fresh_import(gml)
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    semantic_types = {"WallSurface", "RoofSurface", "GroundSurface", "WindowSurface", "DoorSurface"}
    to_remove = [o for o in bpy.data.objects if o.get("cgml3_feature") in semantic_types]
    for child in to_remove:
        bpy.data.objects.remove(child, do_unlink=True)
    expect_export_error(f"{out_dir}/mixed2.gml", "marker semantic_objects, no children")

    print("\n[ALL PASS] mixed-state halt checklist satisfied")


main()
