"""B04: New Building and Building from Footprint make exportable LoD1 buildings.

1. Empty scene, no CRS: New Building (12 x 8 x 9 m, 3 storeys, EPSG 25833)
   -> Building + 1 Ground + 1 Roof + 4 Walls, LoD 1 everywhere, CRS set.
2. An L-shaped flat mesh (two quads) -> Building from Footprint -> 6 walls.
3. Edit Mode on a box, only its bottom face selected -> 4 walls.
4. Export 3.0 and 2.0: three Buildings, no OtherConstruction, XSD valid,
   storeysAboveGround written; re-import gives three Buildings back.

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b04_new_building.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bmesh
import bpy

from building_harness import check, count, export_checked, finish
from addon_test_utils import footprint  # B34: explicit footprint

ADDON = "city_modeler"


def surfaces(building):
    kinds = {}
    for o in building.children_recursive:
        kinds[o.get("cgml3_feature")] = kinds.get(o.get("cgml3_feature"), 0) + 1
    return kinds


def lods(building):
    return {int(m["lod"]) for o in [building, *building.children_recursive] if o.type == 'MESH'
            for m in o.data.materials if m is not None and "lod" in m}


def flat_mesh(name, verts, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b04-out"
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    scene = bpy.context.scene

    # 1. New Building in an empty scene.
    res = bpy.ops.cgml.building_new(points=footprint(12.0, 8.0), height=9.0, storeys=3, epsg=25833)
    check("New Building finishes", res == {'FINISHED'}, f"result={res}")
    b1 = bpy.context.active_object
    check("it is a Building", b1.get("cgml3_feature") == "Building", f"{b1.get('cgml3_feature')!r}")
    check("Ground 1, Roof 1, Wall 4", surfaces(b1) == {"GroundSurface": 1, "RoofSurface": 1, "WallSurface": 4},
          str(surfaces(b1)))
    check("all surfaces are LoD1", lods(b1) == {1}, str(lods(b1)))
    check("CRS set from the operator", scene.world is not None and scene.world.get("cgml_CRS") == "EPSG:25833",
          str(scene.world.get("cgml_CRS") if scene.world else None))
    top = max((o.matrix_world @ v.co).z for o in b1.children for v in o.data.vertices)
    check("height is 9 m", abs(top - 9.0) < 1e-4, f"top={top}")

    # 2. L-shaped footprint from a flat mesh.
    verts = [(20, 0, 0), (30, 0, 0), (30, 5, 0), (25, 5, 0), (25, 12, 0), (20, 12, 0), (20, 5, 0)]
    flat_mesh("L_footprint", verts, [[0, 1, 2, 3, 6], [6, 3, 4, 5]])
    res = bpy.ops.cgml.building_from_footprint(height=6.0)
    check("Building from Footprint (L shape) finishes", res == {'FINISHED'}, f"result={res}")
    b2 = bpy.context.active_object
    check("L shape gives 6 walls", surfaces(b2).get("WallSurface") == 6, str(surfaces(b2)))

    # 3. A box in Edit Mode with only the bottom face selected.
    box = flat_mesh("box", [(40, 0, 0), (46, 0, 0), (46, 6, 0), (40, 6, 0),
                            (40, 0, 3), (46, 0, 3), (46, 6, 3), (40, 6, 3)],
                    [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]])
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(box.data)
    for f in bm.faces:
        f.select_set(f.index == 0)
    bmesh.update_edit_mesh(box.data)
    res = bpy.ops.cgml.building_from_footprint(height=4.0)
    check("Building from the selected bottom face finishes", res == {'FINISHED'}, f"result={res}")
    b3 = bpy.context.active_object
    check("box footprint gives 4 walls", surfaces(b3).get("WallSurface") == 4, str(surfaces(b3)))
    check("back in Object Mode", bpy.context.mode == 'OBJECT')
    bpy.data.objects.remove(box)
    bpy.data.objects.remove(bpy.data.objects["L_footprint"])

    # 4. Export and re-import (nothing selected: export writes the whole scene).
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for version in ("3.0", "2.0"):
        path = os.path.join(out_dir, f"b04_v{version.replace('.', '')}.gml")
        text = export_checked(path, version)
        check(f"{version}: three Buildings", count(text, "bldg:Building") == 3, str(count(text, "bldg:Building")))
        check(f"{version}: no OtherConstruction", "OtherConstruction" not in text)
        check(f"{version}: storeysAboveGround written", "storeysAboveGround>3<" in text)
        check(f"{version}: LoD1 geometry written", "lod1" in text)

    path = os.path.join(out_dir, "b04_v30.gml")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.ops.cgml3.import_gml_file(filepath=path)
    n = sum(1 for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    check("re-import gives three Buildings", n == 3, f"{n}")

    # 5. An imported LoD1 file round-trips to a valid CityGML 2.0 file (was
    #    bldg:boundedBy/lod1MultiSurface before B04).
    from pathlib import Path
    fixture = Path(__file__).resolve().parents[2] / "test_files" / "Building" / "CitySmith"
    for name in ("CS1_lod1.gml", "CS1_lod0.gml"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.preferences.addon_enable(module=ADDON)
        bpy.ops.cgml3.import_gml_file(filepath=str(fixture / name))
        text = export_checked(os.path.join(out_dir, f"roundtrip_{name}"), "2.0")
        check(f"{name}: no LoD0/1 boundary surface in 2.0",
              "boundedBy" not in text or "lod1MultiSurface><gml" not in text.split("boundedBy")[1])

    finish("B04 new building")


main()
