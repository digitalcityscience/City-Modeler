"""B09: Add Roof on L/T/U footprints splits the building into BuildingParts.

1. L-shaped LoD1 building (from a flat L footprint) -> Put Gable Roof ->
   one Building holding 2 BuildingParts, each LoD2 with a gable roof;
   gml:id of the Building kept; footprint area kept.
2. U shape -> 3 parts. Re-roofing the Building (hip) re-roofs every part.
3. Selecting one part's wall and putting a flat roof changes only that part.
4. Export 3.0 and 2.0: 2 Buildings, 5 BuildingParts, XSD valid, re-import
   gives 2 Buildings with 5 parts.

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b09_parts.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

from building_harness import check, count, export_checked, finish

ADDON = "city_modeler"


def flat_building(name, verts, height):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [list(range(len(verts)))])
    src = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(src)
    bpy.context.view_layer.objects.active = src
    bpy.ops.cgml.building_from_footprint(height=height, epsg=25832)
    b = bpy.context.active_object
    bpy.data.objects.remove(src)
    return b


def parts(b):
    return [c for c in b.children if c.get("cgml3_feature") == "BuildingPart"]


def ground_area(b):
    total = 0.0
    for o in b.children_recursive:
        if o.get("cgml3_feature") == "GroundSurface":
            total += sum(p.area for p in o.data.polygons)
    return total


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b09-out"
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    lb = flat_building("L", [(0, 0, 0), (10, 0, 0), (10, 5, 0), (5, 5, 0), (5, 12, 0), (0, 12, 0)], 6)
    gml_id, area = lb["gml_id"], ground_area(lb)
    activate(lb)
    res = bpy.ops.cgml.roof_add(roof_type="3100", pitch=35)
    check("L shape: Put Gable Roof finishes", res == {'FINISHED'}, f"result={res}")
    ps = parts(lb)
    check("L shape: 2 BuildingParts", len(ps) == 2, str(len(ps)))
    check("Building keeps its gml:id and has no own surfaces",
          lb["gml_id"] == gml_id and not [c for c in lb.children if c.get("cgml3_feature") != "BuildingPart"])
    check("every part has a gable roof (2 roof planes, LoD2)",
          all(p.get("bldg:roofType") == "3100" and
              sum(1 for c in p.children if c.get("cgml3_feature") == "RoofSurface") == 2 and
              {int(m["lod"]) for c in p.children for m in c.data.materials if m and "lod" in m} == {2}
              for p in ps))
    check("footprint area kept (85 m2)", abs(ground_area(lb) - area) < 1e-4 and abs(area - 85) < 1e-4,
          f"{ground_area(lb)} vs {area}")

    ub = flat_building("U", [(30, 0, 0), (42, 0, 0), (42, 9, 0), (38, 9, 0), (38, 4, 0), (34, 4, 0),
                             (34, 9, 0), (30, 9, 0)], 5)
    activate(ub)
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=30)
    check("U shape: 3 parts", len(parts(ub)) == 3, str(len(parts(ub))))
    activate(ub)
    res = bpy.ops.cgml.roof_add(roof_type="3200", pitch=30)
    check("re-roofing the Building re-roofs every part",
          res == {'FINISHED'} and len(parts(ub)) == 3 and all(p.get("bldg:roofType") == "3200" for p in parts(ub)))

    one = parts(ub)[0]
    wall = next(c for c in one.children if c.get("cgml3_feature") == "WallSurface")
    activate(wall)
    bpy.ops.cgml.roof_add(roof_type="1000")
    types = sorted(p.get("bldg:roofType") for p in parts(ub))
    check("a part's wall selected: only that part changes", types == ["1000", "3200", "3200"], str(types))

    for o in bpy.context.selected_objects:
        o.select_set(False)
    for version in ("3.0", "2.0"):
        path = os.path.join(out_dir, f"b09_v{version.replace('.', '')}.gml")
        text = export_checked(path, version)
        check(f"{version}: 2 Buildings", count(text, "bldg:Building") == 2, str(count(text, "bldg:Building")))
        check(f"{version}: 5 BuildingParts", count(text, "bldg:BuildingPart") == 5, str(count(text, "bldg:BuildingPart")))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(out_dir, "b09_v30.gml"))
    nb = sum(1 for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    np = sum(1 for o in bpy.data.objects if o.get("cgml3_feature") == "BuildingPart")
    check("re-import: 2 Buildings, 5 parts", (nb, np) == (2, 5), f"{nb}, {np}")

    finish("B09 parts")


main()
