"""Ticket 11: re-importing a CityGML 2 semantic-object export must reproduce
the same hierarchy/counts, and editing a Window object's vertices must
change the exported Window's coordinates."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"


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
    return c


def fresh_import(gml, use_semantic_objects):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, res


def main():
    gml, out_dir = args()[0], args()[1]
    os.makedirs(out_dir, exist_ok=True)
    out1 = os.path.join(out_dir, "roundtrip1.gml")
    out2 = os.path.join(out_dir, "roundtrip2_edited.gml")

    print("\n[test] --- export semantic-object model ---")
    fresh_import(gml, True)
    res = bpy.ops.cgml3.export_gml_file(filepath=out1, citygml_version='2.0')
    assert res == {'FINISHED'}, res

    print("\n[test] --- re-import the export, verify same hierarchy/counts ---")
    fresh_import(out1, True)
    objs = list(bpy.data.objects)
    by_feature = {}
    for o in objs:
        by_feature.setdefault(o.get("cgml3_feature"), []).append(o)
    reimport_counts = {k: len(v) for k, v in by_feature.items()}
    print("reimport object counts by feature:", reimport_counts)
    expected = {"WallSurface": 9, "RoofSurface": 3, "GroundSurface": 1,
                "Window": 15, "Door": 6, "BuildingInstallation": 2}
    ok = True
    for k, v in expected.items():
        got = reimport_counts.get(k, 0)
        status = "OK" if got == v else "FAIL"
        if got != v:
            ok = False
        print(f"  [{status}] {k}: expected {v}, got {got}")

    print("\n[test] --- move a Window object's vertices, re-export, expect changed coords ---")
    fresh_import(gml, True)
    window = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Window")
    for v in window.data.vertices:
        v.co.z += 2.5
    # Ticket 09-equivalent guard (reused verbatim, see io_v2/writer/exporter.py
    # _assert_ring_opening_sync): export now halts if a moved Window no longer
    # lines up with its wall's opening - resync the wall to the Window's new
    # position (Sync Opening -> Ring, the Window is the source of truth here)
    # before re-exporting, same as a real user would.
    for o in bpy.data.objects:
        o.select_set(False)
    window.select_set(True)
    bpy.context.view_layer.objects.active = window
    sync_res = bpy.ops.cgml3.sync_opening_to_ring()
    assert sync_res == {'FINISHED'}, sync_res
    res = bpy.ops.cgml3.export_gml_file(filepath=out2, citygml_version='2.0')
    assert res == {'FINISHED'}, res

    with open(out1, encoding="utf-8") as f:
        text1 = f.read()
    with open(out2, encoding="utf-8") as f:
        text2 = f.read()
    status = "OK" if text1 != text2 else "FAIL"
    if text1 == text2:
        ok = False
    print(f"  [{status}] export changed after moving a Window's vertices: {text1 != text2}")

    if not ok:
        print("\n[FAIL] roundtrip checklist NOT satisfied")
        sys.exit(1)
    print("\n[ALL PASS] roundtrip checklist satisfied")


main()
