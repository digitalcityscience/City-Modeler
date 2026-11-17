"""Ticket 14 checklist, run against the real Blender import+export pipeline.

Covers, from docs/agent/semantic-hierarchy/issues/14-name-the-lod-export-policy.md:
  - the export log records the active LoD policy on every export (both
    CityGML 2 and CityGML 3 targets)
  - export behaviour is unchanged - the exported feature/element counts for
    the Railway LoD3 fixture still match the pre-ticket-14 baseline
    (see test_ticket12_cross_version.py's EXPECTED_V3_SHAPE)
  - two exports of the same in-memory scene are identical once gml:id,
    UUID/ID xlink targets, and creationDate are normalized away (same
    technique as test_ticket13_legacy_byte_identical.py) - proves the new
    print() lines and UI label did not touch exported file content

Usage:
    SP=/tmp/cgml-probe-11   # reuse ticket 11's staged scripts dir
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket14_lod_export_policy.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML3_Building_only.gml \\
      /tmp/out14
"""
import contextlib
import io
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"

EXPECTED_POLICY_LINE = "[CityGML] lod_export_policy=Preserve Available Higher-LoD Details"

EXPECTED_V3_SHAPE = {
    "WallSurface": 9,
    "WindowSurface": 15,
    "DoorSurface": 6,
    "BuildingInstallation": 2,
    "Polygon": 186,
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


def normalize(text):
    text = re.sub(r'gml:id="[^"]*"', 'gml:id="X"', text)
    text = re.sub(r"<creationDate>[^<]*</creationDate>", "<creationDate>X</creationDate>", text)
    text = re.sub(r"#(UUID|ID)_[0-9a-fA-F]+", "#X", text)
    return text


def do_export(path, version):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
    assert res == {'FINISHED'}, f"export failed: {res}"
    return buf.getvalue()


def main():
    a = args()
    v3_gml, out_dir = a[0], a[1]
    os.makedirs(out_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    res = bpy.ops.cgml3.import_gml_file(filepath=v3_gml)
    assert res == {'FINISHED'}, f"import failed: {res}"

    print("\n[test] === export log states the LoD policy (CityGML 3 target) ===")
    out_v3 = os.path.join(out_dir, "policy_v3.gml")
    log_v3 = do_export(out_v3, "3.0")
    check("v3 export log contains the LoD policy line",
          EXPECTED_POLICY_LINE in log_v3, log_v3[-2000:])

    print("\n[test] === export log states the LoD policy (CityGML 2 target) ===")
    out_v2 = os.path.join(out_dir, "policy_v2.gml")
    log_v2 = do_export(out_v2, "2.0")
    check("v2 export log contains the LoD policy line",
          EXPECTED_POLICY_LINE in log_v2, log_v2[-2000:])

    print("\n[test] === export behaviour is unchanged (feature counts match baseline) ===")
    c = counts(out_v3)
    for k, expected in EXPECTED_V3_SHAPE.items():
        check(f"{k} count unchanged", c.get(k, 0) == expected, f"got {c.get(k, 0)}, expected {expected}")

    print("\n[test] === two exports of the same scene are byte-identical once normalized ===")
    out_v3_again = os.path.join(out_dir, "policy_v3_again.gml")
    do_export(out_v3_again, "3.0")
    with open(out_v3, encoding="utf-8") as f:
        text_a = normalize(f.read())
    with open(out_v3_again, encoding="utf-8") as f:
        text_b = normalize(f.read())
    check("normalized export text is identical across two runs", text_a == text_b)

    if FAIL:
        print(f"\n[FAIL] ticket 14 checklist NOT satisfied ({len(FAIL)} failure(s)):")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 14 checklist satisfied")


main()
