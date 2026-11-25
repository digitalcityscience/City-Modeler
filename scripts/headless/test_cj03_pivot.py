"""CJ-03 step 0: is the pivot (scene -> CityGML 3.0 writer -> citygml_reader -> IR) lossless on the
Building fixtures? Per fixture: open it, export CityGML 3.0, read the source and the export into IR
and compare their fingerprints (scripts/tests/cj_fingerprint.py): ids, surface classes, rings, 1 mm
coordinates, parent/host links, attributes and addresses (attribute-card view).

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj03_pivot.py
"""
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))
sys.path.insert(0, ROOT)   # pure modules as top-level ``shared`` (fingerprint, readers)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from cj_fingerprint import diff, fingerprint  # noqa: E402
from shared import citygml_reader, cityjson_open, cityjson_reader  # noqa: E402

SAMPLES = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
TF = os.path.join(ROOT, "test_files")
FIXTURES = [os.path.join(SAMPLES, f) for f in (
    "buildings_lod2_citygml_3_0.gml", "buildings_lod3_citygml_3_0.gml",
    "buildings_lod2_cityjson_2_0.city.json", "buildings_lod3_cityjson_2_0.city.json")] + [
    os.path.join(TF, "Building", f) for f in (
        "Railway_Scene_LoD3_CityGML3_Building_only.gml", "SemanticHierarchy_Installations_LoD3_CityGML3.gml",
        "SemanticHierarchy_Appearance_LoD3_CityGML3.gml", "AltOpeningSpelling_LoD3_CityGML3.gml")] + [
    os.path.join(TF, "CityJSON", "cj02_multi_lod.city.json")]
FAILURES = []


def check(label, condition, detail=""):
    print(("[PASS] " if condition else "[FAIL] ") + label + ("" if condition else f" - {detail}"))
    if not condition:
        FAILURES.append(label)


def clean():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in bpy.data.worlds:
        for k in [k for k in w.keys() if k.startswith("cgml")]:
            del w[k]


def read_ir(path, lod=None):
    if path.endswith(".json"):
        # a building without the chosen LoD is not built (CJ-02 "no LoD2.2: n"), so not exported
        return cityjson_open.with_geometry(cityjson_reader.read_document(cityjson_reader.load(path), lod=lod))[0]
    return [r for r in citygml_reader.read_document(ET.parse(path).getroot())
            if r.feature_type in ("Building", "BuildingPart")]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj03p_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    report = {}
    for src in FIXTURES:
        name = os.path.basename(src)
        if not os.path.isfile(src):
            print(f"[SKIP] {name}: missing")
            continue
        clean()
        res = bpy.ops.cgml3.import_gml_file(filepath=src)
        gml = os.path.join(out, name.split(".")[0] + "_pivot.gml")
        res2 = bpy.ops.cgml3.export_gml_file(filepath=gml, citygml_version="3.0")
        check(f"{name}: open + 3.0 export", res == {'FINISHED'} and res2 == {'FINISHED'}, f"{res} {res2}")
        lod = "2.2" if "multi_lod" in name else None
        # mapped ids (K2): the originals stay in the scene and reach the IR in scene_to_ir, not via GML
        a, b = fingerprint(read_ir(src, lod), original_ids=False), fingerprint(read_ir(gml), original_ids=False)
        d = diff(a, b)
        report[name] = {"features": a["features"], "surfaces": a["surfaces"], "diff": d}
        check(f"{name}: pivot fingerprint equal", not d, json.dumps(d, default=str)[:1500])
    with open(os.path.join(out, "cj03_pivot_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
