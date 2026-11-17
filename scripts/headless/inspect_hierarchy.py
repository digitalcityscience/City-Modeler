"""Headless probe: what does a CityGML import actually produce in the Outliner?

Prints root collections, object counts by feature type, parent-child edges,
and the custom properties that matter for export round-trip.
"""
import sys
from collections import Counter, defaultdict

import bpy

ADDON = "city_modeler"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def main():
    gml = args()[0]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    print(f"[probe] importing {gml}")
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    print(f"[probe] operator result: {res}")

    print("\n=== ROOT COLLECTIONS ===")
    for c in bpy.context.scene.collection.children:
        print(f"  {c.name}: {len(c.objects)} direct objects, {len(c.children)} child colls")
        for sub in c.children:
            print(f"    └─ {sub.name}: {len(sub.objects)} objects")

    objs = list(bpy.data.objects)
    print(f"\n=== TOTAL OBJECTS: {len(objs)} ===")

    by_feature = Counter()
    by_part = Counter()
    for o in objs:
        by_feature[o.get("cgml3_feature") or o.get("feature_type") or "<none>"] += 1
        by_part[o.get("structure_part") or "<none>"] += 1

    print("\n=== OBJECT COUNT BY cgml3_feature ===")
    for k, v in by_feature.most_common():
        print(f"  {k}: {v}")

    print("\n=== OBJECT COUNT BY structure_part ===")
    for k, v in by_part.most_common():
        print(f"  {k}: {v}")

    print("\n=== PARENT-CHILD EDGES ===")
    edges = [(o.parent.name, o.name, o.type) for o in objs if o.parent]
    if not edges:
        print("  (none - flat scene)")
    for p, c, t in edges:
        print(f"  {p} --> {c} [{t}]")

    print("\n=== SEMANTIC SURFACE / OPENING OBJECTS ===")
    for want in ("WallSurface", "RoofSurface", "GroundSurface", "Window", "Door",
                 "WindowSurface", "DoorSurface", "BuildingInstallation"):
        n = sum(1 for o in objs
                if want in str(o.get("cgml3_feature") or "")
                or want in str(o.get("feature_type") or ""))
        print(f"  {want} objects: {n}")

    print("\n=== EXPORT-RELEVANT CUSTOM PROPS (per object) ===")
    keys_of_interest = ("cgml3_feature", "feature_type", "gml_id", "structure_type",
                        "structure_part", "surface_id_map", "surfaces_info",
                        "opening_surface_id", "filling_parent_surface_id",
                        "cgml_parent_id", "opening_type")
    for o in objs:
        props = {}
        for k in keys_of_interest:
            v = o.get(k)
            if v not in (None, ""):
                props[k] = (str(v)[:70] + "...") if len(str(v)) > 70 else v
        print(f"  [{o.type}] {o.name}")
        for k, v in props.items():
            print(f"        {k} = {v}")

    print("\n=== MESH MATERIAL SLOTS (where semantics may hide) ===")
    for o in objs:
        if o.type == 'MESH' and o.data.materials:
            names = [m.name if m else "<empty>" for m in o.data.materials]
            print(f"  {o.name}: {len(o.data.polygons)} faces, materials={names}")


main()
