"""Ticket 11 checklist, run against the real Blender import+export pipeline.

Imports the Railway LoD3 CityGML2 fixture with use_semantic_objects ON,
exports it back to CityGML 2, and asserts the ticket-11 checklist from
docs/agent/semantic-hierarchy/issues/11-citygml2-exporter-object-driven.md:
the semantic-object export must reproduce the ticket-01 baseline exactly,
and must match a legacy (flag-off) export of the same fixture byte-for-byte
in every feature count that matters for round-trip.

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for
why a private staged scripts dir + a cp313 pyproj/lxml shim are required):

    SP=/tmp/cgml-probe-11
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket11_v2_exporter.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml /tmp/out
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"

EXPECTED_BASELINE = {
    "WallSurface": 9,
    "Window": 15,
    "Door": 6,
    "BuildingInstallation": 2,
    "Polygon": 186,
    "cityObjectMember": 1,
    "interior": 41,
}


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _local_name(tag):
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def counts(path):
    root = ET.parse(path).getroot()
    c = {}
    for elem in root.iter():
        local = _local_name(str(elem.tag))
        c[local] = c.get(local, 0) + 1
    # cityObjectMember is a real top-level count (not the container's own
    # end/start tag artifact) - counting elements directly, not regex, so
    # this is correct per README section 1.5.
    return c


def fresh_import(gml, use_semantic_objects):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, f"import failed: {res}"


def do_export(path):
    # bpy.ops.cgml3.export_gml_file's citygml_version auto-detect only runs
    # from the operator's invoke() (UI path); a scripted execute() call
    # defaults to '3.0' unless passed explicitly - always pass '2.0' here.
    res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version='2.0')
    assert res == {'FINISHED'}, f"export failed: {res}"


def main():
    a = args()
    gml, out_dir = a[0], a[1]
    os.makedirs(out_dir, exist_ok=True)

    out_legacy = os.path.join(out_dir, "legacy.gml")
    out_semantic = os.path.join(out_dir, "semantic.gml")

    print("\n[test] --- LEGACY EXPORT (flag off) ---")
    fresh_import(gml, False)
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    assert building.type == 'MESH' and building.get("cgml_hierarchy_model") is None
    do_export(out_legacy)
    legacy_counts = counts(out_legacy)
    for k, v in sorted(legacy_counts.items()):
        print(f"  {k}: {v}")

    print("\n[test] --- SEMANTIC-OBJECT EXPORT (flag on) ---")
    fresh_import(gml, True)
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    assert building.get("cgml_hierarchy_model") == "semantic_objects"
    assert len(building.data.polygons) == 0, "sanity: Building's own mesh should be emptied by the explode"
    do_export(out_semantic)
    semantic_counts = counts(out_semantic)
    for k, v in sorted(semantic_counts.items()):
        print(f"  {k}: {v}")

    print("\n[test] === CHECK: semantic export matches the ticket-01 baseline ===")
    ok = True
    for k, expected in EXPECTED_BASELINE.items():
        got = semantic_counts.get(k, 0)
        status = "OK" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{status}] {k}: expected {expected}, got {got}")

    print("\n[test] === CHECK: semantic export matches legacy export (no regression) ===")
    for k in EXPECTED_BASELINE:
        a_, b_ = legacy_counts.get(k, 0), semantic_counts.get(k, 0)
        status = "OK" if a_ == b_ else "FAIL"
        if a_ != b_:
            ok = False
        print(f"  [{status}] {k}: legacy={a_} semantic={b_}")

    print("\n[test] === CHECK: no double export (surface/opening objects not top-level) ===")
    with open(out_semantic, encoding="utf-8") as f:
        text = f.read()
    # a naively-additive export would double WallSurface count (see README 3.1);
    # already covered above, but assert the raw regex count too for clarity
    raw_wall = len(re.findall(r"<bldg:WallSurface\b", text))
    status = "OK" if raw_wall == EXPECTED_BASELINE["WallSurface"] else "FAIL"
    if raw_wall != EXPECTED_BASELINE["WallSurface"]:
        ok = False
    print(f"  [{status}] raw <bldg:WallSurface count: {raw_wall}")

    print("\n[test] === CHECK: openings render as bldg:opening/bldg:Window under bldg:WallSurface ===")
    ns = {"bldg": "http://www.opengis.net/citygml/building/2.0"}
    root_semantic = ET.parse(out_semantic).getroot()
    nested_windows = root_semantic.findall(
        f".//{{{ns['bldg']}}}WallSurface/{{{ns['bldg']}}}boundedBy"
        f"/{{{ns['bldg']}}}Opening/../{{{ns['bldg']}}}opening/{{{ns['bldg']}}}Window"
    )
    # boundedBy element name in CityGML2 for openings is bldg:opening (property),
    # containing bldg:Window/bldg:Door directly - search flexibly for that shape.
    opening_windows = root_semantic.findall(f".//{{{ns['bldg']}}}opening/{{{ns['bldg']}}}Window")
    status = "OK" if len(opening_windows) == EXPECTED_BASELINE["Window"] else "FAIL"
    if len(opening_windows) != EXPECTED_BASELINE["Window"]:
        ok = False
    print(f"  [{status}] bldg:opening/bldg:Window count: {len(opening_windows)} (expected {EXPECTED_BASELINE['Window']})")

    print("\n[test] === CHECK: BuildingInstallation renders as bldg:outerBuildingInstallation ===")
    outer_installations = root_semantic.findall(f".//{{{ns['bldg']}}}outerBuildingInstallation")
    status = "OK" if len(outer_installations) == EXPECTED_BASELINE["BuildingInstallation"] else "FAIL"
    if len(outer_installations) != EXPECTED_BASELINE["BuildingInstallation"]:
        ok = False
    print(f"  [{status}] bldg:outerBuildingInstallation count: {len(outer_installations)} "
          f"(expected {EXPECTED_BASELINE['BuildingInstallation']})")

    if not ok:
        print("\n[FAIL] ticket 11 checklist NOT satisfied")
        sys.exit(1)
    print("\n[ALL PASS] ticket 11 checklist satisfied")


main()
