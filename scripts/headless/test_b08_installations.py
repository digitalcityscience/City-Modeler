"""B08: Add Balcony / Add Chimney make outer BuildingInstallations.

New Building 12 x 8 x 7 m + gable roof (35 deg). Balcony on the front wall
(3.0 x 1.4 m at 3.0 m), chimney on a roof plane. Checks: both are
BuildingInstallations under the Building, the chimney reaches 0.8 m above
the ridge and starts inside the roof, the balcony sticks out of the wall;
Add Roof now refuses (it would rebuild walls under the installations);
export 3.0 and 2.0: one Building, two installations with SIG3D function codes 1000/1030 (B44),
XSD valid.

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b08_installations.py -- <out_dir>
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bpy

from building_harness import check, count, export_checked, finish
from addon_test_utils import footprint  # B34: explicit footprint

ADDON = "city_modeler"


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def zs(obj):
    return [(obj.matrix_world @ v.co).z for v in obj.data.vertices]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b08-out"
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    bpy.ops.cgml.building_new(points=footprint(12, 8), height=7, epsg=25832)
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=35)
    front = next(o for o in b.children if o.get("cgml3_feature") == "WallSurface"
                 and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.9)
    roof = next(o for o in b.children if o.get("cgml3_feature") == "RoofSurface")
    ridge = max(z for o in b.children if o.get("cgml3_feature") == "RoofSurface" for z in zs(o))

    activate(front)
    # B34: the click's spot is explicit (it was the wall centre + 3 m by default)
    fpts = [front.matrix_world @ v.co for v in front.data.vertices]
    fx = (min(p.x for p in fpts) + max(p.x for p in fpts)) / 2
    res = bpy.ops.cgml.balcony_add(host=b.name, point=(fx, min(p.y for p in fpts), 3.0), normal=(0, -1, 0))
    balcony = bpy.context.active_object
    check("Add Balcony finishes", res == {'FINISHED'}, f"result={res}")
    check("balcony is a Balcony installation under the Building",
          balcony.get("cgml3_feature") == "BuildingInstallation" and balcony.get("cgml_semantic_type") == "Balcony"
          and balcony.parent is b)
    ys = [(balcony.matrix_world @ v.co).y for v in balcony.data.vertices]
    wall_y = (front.matrix_world @ front.data.vertices[0].co).y
    check("balcony sticks out 1.4 m from the wall", abs((wall_y - min(ys)) - 1.4) < 1e-4, f"{wall_y - min(ys)}")
    check("balcony floor at 3.0 m", abs(min(zs(balcony)) - 2.8) < 1e-4 and abs(max(zs(balcony)) - 4.0) < 1e-4,
          f"{min(zs(balcony))}..{max(zs(balcony))}")

    activate(roof)
    rpts = [roof.matrix_world @ v.co for v in roof.data.vertices]
    rn = (roof.matrix_world.to_3x3() @ roof.data.polygons[0].normal).normalized()
    rc = sum(rpts, rpts[0] * 0) / len(rpts)
    res = bpy.ops.cgml.chimney_add(host=b.name, point=rc[:], normal=rn[:])  # B34: explicit spot
    chimney = bpy.context.active_object
    check("Add Chimney finishes", res == {'FINISHED'}, f"result={res}")
    check("chimney is a Chimney installation", chimney.get("cgml_semantic_type") == "Chimney" and chimney.parent is b)
    check("chimney top 0.8 m above the ridge", abs(max(zs(chimney)) - (ridge + 0.8)) < 1e-4,
          f"top {max(zs(chimney))}, ridge {ridge}")
    check("chimney starts inside the roof", min(zs(chimney)) < ridge and min(zs(chimney)) > 7.0 - 1e-6,
          f"bottom {min(zs(chimney))}")

    activate(b)
    check("Add Roof refuses a building with installations", not bpy.ops.cgml.roof_add.poll())

    for o in bpy.context.selected_objects:
        o.select_set(False)
    for version in ("3.0", "2.0"):
        text = export_checked(os.path.join(out_dir, f"b08_v{version.replace('.', '')}.gml"), version)
        check(f"{version}: one Building", count(text, "bldg:Building") == 1)
        check(f"{version}: two installations", count(text, "bldg:BuildingInstallation") == 2,
              str(count(text, "bldg:BuildingInstallation")))
        codes = sorted(re.findall(r"<bldg:function[^>]*>(10[03]0)<", text))
        check(f"{version}: codes 1000 balcony and 1030 chimney (SIG3D)", codes == ["1000", "1030"], str(codes))

    finish("B08 installations")


main()
