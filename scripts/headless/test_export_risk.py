"""Decisive test: does adding a child mesh under a Building corrupt the export?

Baseline export vs. export with one extra child mesh marked structure_part=semantic_surface
(a WallSurface without a material). The child must be added as one more WallSurface polygon,
and every polygon of the baseline must come back with the same gml:id and coordinates.

Usage (no arguments: the repo's Railway LoD3 CityGML 3 Building and a temp folder):
  blender --background --factory-startup --python-exit-code 1 \\
      --python scripts/headless/test_export_risk.py [-- <gml> <out_base.gml> <out_mod.gml>]
"""
import os
import re
import sys
import tempfile

# Blender 5.2 ships Python 3.13; the repo's vendored pyproj is cp311, so
# bundled_deps.py rejects it and export dies. Use a cp313 install for this probe.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def counts(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        t = f.read()
    return {
        "Polygon": len(re.findall(r"<gml:Polygon", t)),
        "cityObjectMember": len(re.findall(r"cityObjectMember", t)),
        "Building": len(re.findall(r"<bldg:Building[ >]", t)),
        "WallSurface": len(re.findall(r"<con:WallSurface", t)),
        "WindowSurface": len(re.findall(r"<con:WindowSurface", t)),
        "DoorSurface": len(re.findall(r"<con:DoorSurface", t)),
        "BuildingInstallation": len(re.findall(r"<bldg:BuildingInstallation", t)),
    }


def polygons(path):
    """gml:id -> posList text of every gml:Polygon exterior/interior ring, in order."""
    import xml.etree.ElementTree as ET
    out = {}
    for el in ET.parse(path).iter():
        if el.tag.endswith("}Polygon"):
            gid = next((v for k, v in el.attrib.items() if k.endswith("}id")), "")
            out[gid] = [p.text for p in el.iter() if p.tag.endswith("}posList")]
    return out


def do_export(path):
    bpy.ops.cgml3.export_gml_file(filepath=path)


def main():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if a:
        gml, out_base, out_mod = a[0], a[1], a[2]
    else:
        gml = os.path.join(REPO, "test_files", "Building", "Railway_Scene_LoD3_CityGML3_Building_only.gml")
        tmp = tempfile.mkdtemp(prefix="cgml_export_risk_")
        out_base, out_mod = os.path.join(tmp, "base.gml"), os.path.join(tmp, "mod.gml")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.ops.cgml3.import_gml_file(filepath=gml)

    print("\n[test] --- BASELINE EXPORT ---")
    do_export(out_base)
    base = counts(out_base)
    for k, v in base.items():
        print(f"  {k}: {v}")

    # Now simulate Strategy A: add a child mesh under the Building
    bld = bpy.data.objects.get("Station KIT/KHH-1")
    print(f"\n[test] adding child mesh under {bld.name!r}")

    me = bpy.data.meshes.new("FakeWallSurface")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    child = bpy.data.objects.new("FakeWallSurface", me)
    child.parent = bld
    child["structure_part"] = "semantic_surface"
    child["cgml3_feature"] = "WallSurface"
    child["gml_id"] = "FAKE_WALL_1"
    bld.users_collection[0].objects.link(child)

    print("[test] --- EXPORT WITH EXTRA CHILD ---")
    do_export(out_mod)
    mod = counts(out_mod)
    for k, v in mod.items():
        print(f"  {k}: {v}")

    print("\n[test] === DIFF (baseline -> with child) ===")
    failures = []
    expected = dict(base, Polygon=base["Polygon"] + 1, WallSurface=base["WallSurface"] + 1)
    for k in base:
        if mod[k] != expected[k]:
            failures.append(f"{k}: {base[k]} -> {mod[k]}, expected {expected[k]}")
    base_polys, mod_polys = polygons(out_base), polygons(out_mod)
    changed = [gid for gid, rings in base_polys.items() if mod_polys.get(gid) != rings]
    if changed:
        failures.append(f"{len(changed)} baseline polygon(s) missing or with other coordinates: {changed[:5]}")
    for f in failures:
        print(f"  [FAIL] {f}")
    if failures:
        sys.exit(1)
    print("  [PASS] the child is one more WallSurface polygon; every baseline polygon is unchanged")


main()
