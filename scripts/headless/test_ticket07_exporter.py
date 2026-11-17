"""Ticket 07 checklist, run against the real Blender import+export pipeline.

Imports the Railway LoD3 CityGML3 fixture with use_semantic_objects ON,
exports it back to CityGML 3, and asserts the ticket-07 checklist from
docs/agent/semantic-hierarchy/issues/07-citygml3-exporter-object-driven.md:
the semantic-object export must reproduce the ticket-01 baseline exactly,
and must match a legacy (flag-off) export of the same fixture byte-for-byte
in every feature count that matters for round-trip.

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for
why a private staged scripts dir + a cp313 pyproj/lxml shim are required):

    SP=/tmp/cgml-probe-07
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket07_exporter.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML3_Building_only.gml /tmp/out
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
    "WindowSurface": 15,
    "DoorSurface": 6,
    "BuildingInstallation": 2,
    # The fixture's one flat Door gets a 4 cm display prism on import
    # (window-door-upload ticket 02), but export writes the flat source
    # polygon again (facade_placement.flat_synthetic_openings): 186 = input.
    "Polygon": 186,
    "cityObjectMember": 1,
}
_LEGACY_VS_SEMANTIC_EXEMPT = set()


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
    res = bpy.ops.cgml3.export_gml_file(filepath=path)
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
    assert building.type == 'MESH' and len(building.data.polygons) == 215
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
        if k in _LEGACY_VS_SEMANTIC_EXEMPT:
            print(f"  [SKIP] {k}: legacy={a_} semantic={b_} (exempt - see EXPECTED_BASELINE comment)")
            continue
        status = "OK" if a_ == b_ else "FAIL"
        if a_ != b_:
            ok = False
        print(f"  [{status}] {k}: legacy={a_} semantic={b_}")

    # Interior rings: gml:interior elements, all 41 must survive.
    a_int = counts(out_legacy).get("interior", 0)
    b_int = counts(out_semantic).get("interior", 0)
    status = "OK" if a_int == b_int else "FAIL"
    if a_int != b_int:
        ok = False
    print(f"  [{status}] interior rings: legacy={a_int} semantic={b_int}")

    print("\n[test] === CHECK: no double export (surface/opening objects not top-level) ===")
    with open(out_semantic, encoding="utf-8") as f:
        text = f.read()
    # a naively-additive export would double WallSurface count (see README 3.1);
    # already covered above, but assert the raw regex count too for clarity
    raw_wall = len(re.findall(r"<con:WallSurface\b", text))
    status = "OK" if raw_wall == EXPECTED_BASELINE["WallSurface"] else "FAIL"
    if raw_wall != EXPECTED_BASELINE["WallSurface"]:
        ok = False
    print(f"  [{status}] raw <con:WallSurface count: {raw_wall}")

    if not ok:
        print("\n[FAIL] ticket 07 checklist NOT satisfied")
        sys.exit(1)
    print("\n[ALL PASS] ticket 07 checklist satisfied")


main()
