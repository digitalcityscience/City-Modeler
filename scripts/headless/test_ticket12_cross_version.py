"""Ticket 12 checklist, run against the real Blender import+export pipeline.

Cross-version export: a Building imported as semantic objects from one
CityGML schema version exports correctly to the *other* version. This does
not add a new capability - citygml_version is already a user-selectable
export enum (see __init__.py) - it makes that gesture work for the
semantic-object hierarchy (tickets 06-11).

Covers, from docs/agent/semantic-hierarchy/issues/12-cross-version-export.md:
  - v2 fixture -> v3 export produces the v3 baseline shape (con:WindowSurface/
    DoorSurface, bldg:buildingInstallation, no outer/interior split)
  - v3 fixture -> v2 export produces the v2 baseline shape (bldg:Window/Door
    under bldg:opening, bldg:outerBuildingInstallation)
  - outer/interior role survives a v2 -> v3 -> v2 export sequence on the
    same in-memory scene (the real fixture has no interiorBuildingInstallation,
    so the "interior" case is exercised on one installation object with its
    role flipped by hand - same gap ticket 03 documented for Balcony/Chimney)
  - no bldg:/con: prefixed value leaks into a decision - checked by grepping
    both outputs for the wrong schema's element vocabulary
  - cgml_source_element (verbatim provenance) never appears in either output
  - gml:id is preserved across both directions
  - feature counts survive both directions

Usage:
    SP=/tmp/cgml-probe-11   # reuse ticket 11's staged scripts dir
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket12_cross_version.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml \\
      test_files/Building/Railway_Scene_LoD3_CityGML3_Building_only.gml \\
      /tmp/out12
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"

BLDG_NS = "http://www.opengis.net/citygml/building/2.0"
CON_NS_HINTS = ("construction",)  # v3 con: namespace URI contains "construction"

EXPECTED_V3_SHAPE = {
    "WallSurface": 9,
    "WindowSurface": 15,
    "DoorSurface": 6,
    "BuildingInstallation": 2,
    "Polygon": 186,
    "interior": 41,
}

EXPECTED_V2_SHAPE = {
    "WallSurface": 9,
    "Window": 15,
    "Door": 6,
    "BuildingInstallation": 2,
    "Polygon": 186,
    "cityObjectMember": 1,
    "interior": 41,
}

FAIL = []


def check(label, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


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
        c[_local_name(str(elem.tag))] = c.get(_local_name(str(elem.tag)), 0) + 1
    return c


def gml_ids(path):
    root = ET.parse(path).getroot()
    ids = set()
    for elem in root.iter():
        for k, v in elem.attrib.items():
            if _local_name(k) == "id":
                ids.add(v)
    return ids


def fresh_import(gml, use_semantic_objects=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, f"import failed: {res}"


def do_export(path, version):
    res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
    assert res == {'FINISHED'}, f"export failed: {res}"


def building_obj():
    return next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")


def main():
    a = args()
    v2_gml, v3_gml, out_dir = a[0], a[1], a[2]
    os.makedirs(out_dir, exist_ok=True)

    # ------------------------------------------------------------------
    print("\n[test] === v2 fixture -> v3 export ===")
    fresh_import(v2_gml, True)
    b = building_obj()
    assert b.get("cgml_hierarchy_model") == "semantic_objects"
    src_ids_v2 = {o.get("gml_id") for o in bpy.data.objects if o.get("gml_id")}

    out_v2_to_v3 = os.path.join(out_dir, "v2_to_v3.gml")
    do_export(out_v2_to_v3, "3.0")
    c = counts(out_v2_to_v3)
    for k, expected in EXPECTED_V3_SHAPE.items():
        check(f"v2->v3 {k} count", c.get(k, 0) == expected, f"got {c.get(k, 0)}, expected {expected}")

    with open(out_v2_to_v3, encoding="utf-8") as f:
        text_v2_to_v3 = f.read()
    check("v2->v3 output has no bare bldg:Window (v2 opening vocabulary)",
          "<bldg:Window" not in text_v2_to_v3)
    check("v2->v3 output has no bldg:opening (v2 opening wrapper)",
          "<bldg:opening" not in text_v2_to_v3)
    check("v2->v3 output has no outerBuildingInstallation/interiorBuildingInstallation (v2-only)",
          "outerBuildingInstallation" not in text_v2_to_v3 and "interiorBuildingInstallation" not in text_v2_to_v3)
    check("v2->v3 output has con:WindowSurface/con:fillingSurface (v3 vocabulary present)",
          "WindowSurface" in text_v2_to_v3 and "fillingSurface" in text_v2_to_v3)
    check("v2->v3 output never renders cgml_source_element",
          "cgml_source_element" not in text_v2_to_v3)

    dst_ids_v3 = gml_ids(out_v2_to_v3)
    check("v2->v3 gml:ids preserved (subset survives)",
          src_ids_v2 and src_ids_v2.issubset(dst_ids_v3),
          f"missing: {sorted(src_ids_v2 - dst_ids_v3)[:5]}")

    # ------------------------------------------------------------------
    print("\n[test] === v3 fixture -> v2 export ===")
    fresh_import(v3_gml, True)
    b = building_obj()
    assert b.get("cgml_hierarchy_model") == "semantic_objects"
    src_ids_v3 = {o.get("gml_id") for o in bpy.data.objects if o.get("gml_id")}

    out_v3_to_v2 = os.path.join(out_dir, "v3_to_v2.gml")
    do_export(out_v3_to_v2, "2.0")
    c = counts(out_v3_to_v2)
    for k, expected in EXPECTED_V2_SHAPE.items():
        check(f"v3->v2 {k} count", c.get(k, 0) == expected, f"got {c.get(k, 0)}, expected {expected}")

    with open(out_v3_to_v2, encoding="utf-8") as f:
        text_v3_to_v2 = f.read()
    check("v3->v2 output has no con:WindowSurface/DoorSurface (v3-only vocabulary)",
          "con:WindowSurface" not in text_v3_to_v2 and "con:DoorSurface" not in text_v3_to_v2)
    check("v3->v2 output has no con:fillingSurface (v3-only wrapper)",
          "con:fillingSurface" not in text_v3_to_v2)
    check("v3->v2 output has bldg:opening/bldg:Window (v2 vocabulary present)",
          "bldg:opening" in text_v3_to_v2 and "<bldg:Window" in text_v3_to_v2)
    check("v3->v2 output has bldg:outerBuildingInstallation (v3 imports always resolve outer)",
          "bldg:outerBuildingInstallation" in text_v3_to_v2)
    check("v3->v2 output never renders cgml_source_element",
          "cgml_source_element" not in text_v3_to_v2)

    dst_ids_v2 = gml_ids(out_v3_to_v2)
    check("v3->v2 gml:ids preserved (subset survives)",
          src_ids_v3 and src_ids_v3.issubset(dst_ids_v2),
          f"missing: {sorted(src_ids_v3 - dst_ids_v2)[:5]}")

    # ------------------------------------------------------------------
    print("\n[test] === outer/interior role survives v2 -> v3 -> v2 on the same scene ===")
    # The real fixture has zero interiorBuildingInstallation (verified by
    # inspection - see memory), so flip one installation's role by hand to
    # exercise the case the source data can't, mirroring ticket 03's synthetic
    # Balcony/Chimney fixtures for the same kind of coverage gap.
    fresh_import(v2_gml, True)
    installations = [o for o in bpy.data.objects if o.get("cgml3_feature") == "BuildingInstallation"]
    check("v2 fixture has >=1 BuildingInstallation to flip", len(installations) >= 1)
    target = installations[0]
    target_id = target.get("gml_id")
    check("picked installation has source_role == outer before flip",
          target.get("cgml_source_role") == "outer")
    target["cgml_source_role"] = "interior"
    target["cgml_source_element"] = "bldg:interiorBuildingInstallation"

    rt_v3 = os.path.join(out_dir, "roundtrip_role_v3.gml")
    do_export(rt_v3, "3.0")
    with open(rt_v3, encoding="utf-8") as f:
        rt_v3_text = f.read()
    check("v3 export has no outer/interior split even with an interior-flagged object",
          "outerBuildingInstallation" not in rt_v3_text and "interiorBuildingInstallation" not in rt_v3_text)
    check("flipped object's cgml_source_role still 'interior' after v3 export (same scene, no re-import)",
          target.get("cgml_source_role") == "interior")

    rt_v2 = os.path.join(out_dir, "roundtrip_role_v2_after_v3.gml")
    do_export(rt_v2, "2.0")
    root_rt_v2 = ET.parse(rt_v2).getroot()

    def _gml_id_of(el):
        for k, v in el.attrib.items():
            if _local_name(k) == "id":
                return v
        return None

    interior_ids = {
        _gml_id_of(el) for el in root_rt_v2.iter(f"{{{BLDG_NS}}}IntBuildingInstallation")
    }
    check("flipped installation renders as IntBuildingInstallation on re-export to v2",
          target_id in interior_ids, f"interior ids found: {interior_ids}")
    outer_count = len(root_rt_v2.findall(f".//{{{BLDG_NS}}}outerBuildingInstallation"))
    check("the other installation still renders as outerBuildingInstallation",
          outer_count == len(installations) - 1, f"got {outer_count}")

    # ------------------------------------------------------------------
    print("\n[test] === opening mapping both directions (Window/Door element identity) ===")
    root_v2v3 = ET.parse(out_v2_to_v3).getroot()
    windows_v3 = [e for e in root_v2v3.iter() if _local_name(e.tag) == "WindowSurface"]
    doors_v3 = [e for e in root_v2v3.iter() if _local_name(e.tag) == "DoorSurface"]
    check("v2->v3: 15 WindowSurface elements", len(windows_v3) == 15, str(len(windows_v3)))
    check("v2->v3: 6 DoorSurface elements", len(doors_v3) == 6, str(len(doors_v3)))

    root_v3v2 = ET.parse(out_v3_to_v2).getroot()
    windows_v2 = root_v3v2.findall(f".//{{{BLDG_NS}}}opening/{{{BLDG_NS}}}Window")
    doors_v2 = root_v3v2.findall(f".//{{{BLDG_NS}}}opening/{{{BLDG_NS}}}Door")
    check("v3->v2: 15 bldg:opening/bldg:Window elements", len(windows_v2) == 15, str(len(windows_v2)))
    check("v3->v2: 6 bldg:opening/bldg:Door elements", len(doors_v2) == 6, str(len(doors_v2)))

    # ------------------------------------------------------------------
    if FAIL:
        print(f"\n[FAIL] ticket 12 checklist NOT satisfied ({len(FAIL)} failure(s)):")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 12 checklist satisfied")


main()
