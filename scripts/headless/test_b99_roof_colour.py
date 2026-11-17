import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import bpy
from importlib import import_module
from addon_test_utils import ensure_addon_enabled, footprint
ok, addon = ensure_addon_enabled()
pal = import_module(f"{addon}.shared.semantic_appearance").SEMANTIC_PALETTE["RoofSurface"].color
tbl = import_module(f"{addon}.modeltyper.citygml")
bpy.ops.cgml.building_new(points=footprint())
bpy.ops.cgml.roof_add(roof_type="3100")
b = bpy.context.active_object
cols = {tuple(round(c,3) for c in s.material.diffuse_color) for o in b.children_recursive if o.get("cgml3_feature")=="RoofSurface" for s in o.material_slots if s.material}
exp = tuple(round(c,3) for c in pal)
print("drawn roof colours:", cols, "palette:", exp, "v2:", tbl.BUILDING_SURFACES_V2["RoofSurface"]["color"] == pal, "v3:", tbl.BUILDING_SURFACES_V3["RoofSurface"]["color"] == pal)
sys.exit(0 if cols == {exp} else 1)
