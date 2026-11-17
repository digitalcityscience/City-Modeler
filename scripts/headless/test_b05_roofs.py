"""B05/B11: Add Roof turns a LoD1 block into a LoD2 building for every AdV roof type.

For each of the 10 roof types: New Building (12 x 8 x 6 m, rotated) ->
Add Roof -> Ground 1, Wall 4, Roof >= 1, LoD 2 everywhere, bldg:roofType
set, gml:id kept, closed solid. Re-roofing (gable -> hip) keeps one
Building. Slanted footprints are refused.
Export 3.0 and 2.0: one Building and roofType value (with the AdV codeSpace, B87) per type, XSD valid.

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b05_roofs.py -- <out_dir>
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bmesh
import bpy

from building_harness import check, count, export_checked, finish
from addon_test_utils import footprint  # B34: explicit footprint

ADDON = "city_modeler"


def kinds(root):
    out = {}
    for o in root.children_recursive:
        out[o.get("cgml3_feature")] = out.get(o.get("cgml3_feature"), 0) + 1
    return out


def closed(root):
    bm = bmesh.new()
    for o in root.children:
        tmp = bmesh.new()
        tmp.from_mesh(o.data)
        tmp.transform(o.matrix_world)
        me = bpy.data.meshes.new("tmp")
        tmp.to_mesh(me)
        tmp.free()
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    ok = all(e.is_manifold for e in bm.edges)
    vol = bm.calc_volume(signed=True)
    bm.free()
    return ok, vol


def new_block(x):
    bpy.context.scene.cursor.location = (x, 0, 0)
    bpy.ops.cgml.building_new(points=footprint(12, 8, angle=math.radians(15)), height=6, epsg=25832)
    return bpy.context.active_object


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b05-out"
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    from city_modeler.shared.roof_shapes import ROOF_TYPES

    for i, code in enumerate(ROOF_TYPES):
        b = new_block(i * 30)
        gml_id = b["gml_id"]
        res = bpy.ops.cgml.roof_add(roof_type=code, pitch=30 if code != "1000" else 0)
        name = ROOF_TYPES[code]
        check(f"{name}: Add Roof finishes", res == {'FINISHED'}, f"result={res}")
        k = kinds(b)
        check(f"{name}: ground 1, walls 4, roofs >= 1",
              k.get("GroundSurface") == 1 and k.get("WallSurface", 0) >= 4 and k.get("RoofSurface", 0) >= 1, str(k))
        lods = {int(m["lod"]) for o in b.children for m in o.data.materials if m and "lod" in m}
        check(f"{name}: LoD 2 everywhere", lods == {2}, str(lods))
        mats = [m for o in b.children if o.get("cgml3_feature") == "RoofSurface" for m in o.data.materials if m]
        shown = {tuple(round(c, 3) for c in m.diffuse_color) for m in mats}
        base = {tuple(round(c, 3) for c in m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)
                for m in mats}
        check(f"{name}: the roof shows its semantic colour in Solid view (B87)",
              shown == base and (0.8, 0.8, 0.8, 1.0) not in shown, f"{shown} {base}")
        check(f"{name}: roofType {code}", b.get("bldg:roofType") == code)
        check(f"{name}: gml:id kept", b.get("gml_id") == gml_id)
        ok, vol = closed(b)
        check(f"{name}: closed solid, volume above the block", ok and vol >= 12 * 8 * 6 - 1e-3, f"manifold={ok} vol={vol:.2f}")

    # Re-roof: gable -> hip on the same building.
    b = new_block(400)
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=35)
    res = bpy.ops.cgml.roof_add(roof_type="3200", pitch=35)
    check("re-roofing gable -> hip finishes", res == {'FINISHED'} and b.get("bldg:roofType") == "3200")
    check("re-roofed building has 4 roof planes", kinds(b).get("RoofSurface") == 4, str(kinds(b)))
    for o in list(b.children_recursive) + [b]:
        bpy.data.objects.remove(o)

    # B87: Cone / Dome / Tower fit any convex footprint (a round tower); the gable does not.
    import json
    ring = json.dumps([[450 + 5 * math.cos(math.pi * k / 4), 5 * math.sin(math.pi * k / 4), 0.0] for k in range(8)])
    bpy.ops.cgml.building_new(points=ring, height=6, epsg=25832)
    tower = bpy.context.active_object
    for code in ("3600", "3700", "4000"):
        res = bpy.ops.cgml.roof_add(roof_type=code, pitch=45)
        k = kinds(tower)
        ok, _vol = closed(tower)
        check(f"octagon: {ROOF_TYPES[code]} roof, closed solid", res == {'FINISHED'} and ok and k.get("WallSurface") == 8
              and k.get("RoofSurface") == (32 if code == "3700" else 8), f"{res} {k}")
    try:
        res = bpy.ops.cgml.roof_add(roof_type="3100")
    except RuntimeError as e:
        res = str(e)
    check("octagon: gable is refused, the tower roof stays", res != {'FINISHED'} and tower.get("bldg:roofType") == "4000",
          str(res))
    for o in list(tower.children_recursive) + [tower]:
        bpy.data.objects.remove(o)

    # A slanted footprint is refused (L/T/U shapes are split into parts, see B09).
    mesh = bpy.data.meshes.new("trapezoid")
    mesh.from_pydata([(0, 50, 0), (10, 50, 0), (8, 56, 0), (2, 56, 0)], [], [[0, 1, 2, 3]])
    flat = bpy.data.objects.new("trapezoid", mesh)
    bpy.context.scene.collection.objects.link(flat)
    bpy.context.view_layer.objects.active = flat
    bpy.ops.cgml.building_from_footprint(height=5)
    slanted = bpy.context.active_object
    try:
        res = bpy.ops.cgml.roof_add(roof_type="3100")
    except RuntimeError as e:
        res = str(e)
    check("slanted footprint is refused", res != {'FINISHED'} and slanted.get("bldg:roofType") is None, str(res))
    for o in list(slanted.children_recursive) + [slanted, flat]:
        bpy.data.objects.remove(o)

    for o in bpy.context.selected_objects:
        o.select_set(False)
    for version in ("3.0", "2.0"):
        text = export_checked(os.path.join(out_dir, f"b05_v{version.replace('.', '')}.gml"), version)
        check(f"{version}: one Building per roof type", count(text, "bldg:Building") == len(ROOF_TYPES),
              str(count(text, "bldg:Building")))
        codes = sorted(re.findall(r"<bldg:roofType[^>]*>([^<]+)<", text))
        check(f"{version}: every roofType value", codes == sorted(ROOF_TYPES), str(codes))
        check(f"{version}: roofType carries the AdV codeSpace (B87)",
              text.count('<bldg:roofType codeSpace="http://repository.gdi-de.org/schemas/adv/citygml/Codelisten/'
                         'RoofTypeTypeAdV.xml">') == len(ROOF_TYPES))
        check(f"{version}: LoD2 geometry, no LoD1 left", "lod2MultiSurface" in text and "lod1" not in text)

    finish("B05/B11 roofs")


main()
