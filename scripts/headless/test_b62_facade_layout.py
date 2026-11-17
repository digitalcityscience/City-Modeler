"""B62 (agent plan A8): facade layout document applied in one go, idempotent.

Hamburg LoD3 CityGML 3.0, the largest rectangular wall face: a cgml-facade-layout/1 document with
a ground door, a window, a 2 x 2 window grid (5 windows + 1 door, 2 storeys) and a balcony under
the window:
- first apply: 7 placed, 0 skipped; the export is XSD valid; with CityDoctor set up no error class
  the source did not have
- second apply (as a JSON string): 0 placed, 7 skipped "already there"
- an invalid document is refused as a whole (BAD_ARGUMENT naming the problems), nothing placed

Old code: no agent_api.apply_facade_layout (AttributeError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b62_facade_layout.py -- <out_dir> [<buildings_dir>]
"""
import json
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

BD = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
SOURCE_CODES = {"GE_S_SELF_INTERSECTION", "GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE", "SE_BS_UNFRAGMENTED",
                "SE_POLYGON_WITHOUT_SURFACE"}  # the last: CityDoctor counts every window/door polygon (B49)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b62_")
    bd = argv[1] if len(argv) > 1 else BD
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    api = import_module(addon + ".shared.agent_api")
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(bd, "buildings_lod3_citygml_3_0.gml"))

    cands = []
    for b in api.inspect()["buildings"]:
        for s in b["surfaces"]:
            for f in s["faces"]:
                fr = f.get("frame")
                if f["can_host"] and fr and fr["width"] >= 8 and fr["height"] >= 5 \
                        and f["area"] >= 0.98 * fr["width"] * fr["height"]:
                    cands.append((f["area"], b["gml_id"], s["gml_id"], f["index"], fr["width"], fr["height"]))
    check("a rectangular wall face >= 8 x 5 m exists", bool(cands))
    _a, bid, wid, face, W, H = max(cands)
    ww, wh = min(1.0, 0.12 * W), min(1.2, 0.2 * H)
    dw, dh = min(0.9, 0.1 * W), min(2.1, 0.4 * H)
    layout = {
        "schema": "cgml-facade-layout/1", "building_gml_id": bid, "wall_gml_id": wid, "face_index": face,
        "units": "m", "storeys": 2,
        "source": {"model_name": "test-vlm", "model_version": "0", "confidence": 0.8, "image_ref": "street/123.jpg"},
        "openings": [
            {"kind": "Door", "u": 0.6, "v": (dh / 2) / H, "width": dw, "height": dh, "topology": "AUTO"},
            {"kind": "Window", "u": 0.8, "v": 0.3, "width": ww, "height": wh},
        ],
        "grid": {"kind": "Window", "columns": 2, "rows": 2, "width": ww, "height": wh,
                 "step_x": 0.2 * W, "step_z": 0.35 * H, "origin_uv": [0.15, 0.3]},
        "installations": [{"kind": "Balcony", "type": "PROJECTING", "at_opening": 1}],
    }
    with open(os.path.join(out, "facade_layout.json"), "w", encoding="utf-8") as fh:
        json.dump(layout, fh, indent=1)

    r = api.apply_facade_layout(layout)
    check("first apply: 7 placed, 0 skipped", r["ok"] and r["placed"] == 7 and not r["skipped"],
          f"{r['message']} {r.get('skipped')}")
    kinds = [api.find_object(g) for g in r["created"]]
    names = [o.name.split("_")[0] for o in kinds]
    check("5 windows, 1 door, 1 balcony", (names.count("WindowSurface"), names.count("DoorSurface"),
                                            names.count("Balcony")) == (5, 1, 1), str(names))
    door = api.find_object(r["created"][0])
    check("door is a ground notch", door.get("cgml_authoring_topology") == "ground_notch",
          str(door.get("cgml_authoring_topology")))
    check("provenance stored", door.get("cgml_model_name") == "test-vlm", str(door.get("cgml_model_name")))

    res = api.validate(bid, citydoctor=True)
    check("export XSD valid", res["ok"] and res["xsd"] == "valid", res["message"])
    if res["citydoctor"] is not None:
        check("CityDoctor: no new error class", set(res["citydoctor"]) <= SOURCE_CODES, str(res["citydoctor"]))
        print("  CityDoctor after layout:", res["citydoctor"])
    else:
        print("  [SKIP] CityDoctor not set up")

    r2 = api.apply_facade_layout(json.dumps(layout))
    check("second apply: 0 placed, 7 skipped as already there",
          r2["ok"] and r2["placed"] == 0 and len(r2["skipped"]) == 7
          and all("already there" in s["reason"] for s in r2["skipped"]), f"{r2['message']} {r2.get('skipped')}")

    bad = dict(layout, openings=[{"kind": "Garage", "u": 1.5, "v": 0.2, "width": 1, "height": 1}])
    n = len(bpy.data.objects)
    r3 = api.apply_facade_layout(bad)
    check("invalid layout refused as a whole", not r3["ok"] and r3["error_code"] == "BAD_ARGUMENT"
          and "kind" in r3["message"] and "u" in r3["message"] and len(bpy.data.objects) == n, r3["message"])

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
