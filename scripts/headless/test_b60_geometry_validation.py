"""B60 (agent plan A5): geometry checks on the written file, CityDoctor button.

- Hamburg LoD3 CityGML 3.0: validate() finds the 3 non-planar polygons (= CityDoctor
  GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE 3), every building closed, no filling without a hole;
  with CityDoctor set up (CITYDOCTOR_VALIDATE_SH) its per-building counts come back and its
  non-planar count matches ours
- authored building with a window, a floating door and a ground door: no findings, closed
- a wall face removed: GE_S_NOT_CLOSED with the open edges listed
- two corners of a wall face swapped (bow-tie): GE_R_SELF_INTERSECTION on that polygon
- a window moved 2 m along its wall: CGML_FILLING_WITHOUT_HOLE
- the CityDoctor button is greyed out (poll message) without a validator; Check Geometry runs

Old code: no shared.geometry_checks / agent_api.validate (ImportError / AttributeError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b60_geometry_validation.py -- <out_dir> [<buildings_dir>]
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

BD = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
API = None
ADDON = None


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def authored():
    clear()
    bpy.ops.cgml.building_new(points=footprint(width=12.0, depth=8.0))
    bid = str(bpy.context.active_object["gml_id"])
    API.add_roof(bid, "3100")
    b = API.inspect(bid)["buildings"][0]
    walls = [(s, f) for s in b["surfaces"] if s["type"] == "WallSurface" for f in s["faces"] if f["can_host"]]
    (s0, f0), (s1, f1) = walls[:2]
    win = API.add_opening(s0["gml_id"], f0["index"], 0.25, 0.5, 1.2, 1.4)["created"][0]
    API.add_opening(s1["gml_id"], f1["index"], 0.3, 0.5, 1.0, 2.0, kind="Door", topology="FLOATING")
    door_v = (2.1 / 2) / f1["frame"]["height"]
    r = API.add_opening(s1["gml_id"], f1["index"], 0.75, door_v, 1.0, 2.1, kind="Door")
    check("ground door placed", r["ok"] and r["topology"] == "ground_notch", r["message"])
    return bid, s0["gml_id"], s1["gml_id"], win


def findings(res, bid):
    return res["buildings"].get(bid, {}).get("checks", {})


def main():
    global API, ADDON
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b60_")
    bd = argv[1] if len(argv) > 1 else BD
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    ADDON = addon
    if not ok:
        raise SystemExit(addon)
    API = import_module(addon + ".shared.agent_api")

    # authored, clean
    bid, w0, w1, win = authored()
    res = API.validate()
    check("authored: validate ok, XSD valid", res["ok"] and res["xsd"] == "valid", res["message"])
    check("authored: no findings, closed", findings(res, bid) == {} and res["buildings"][bid]["closed"] is True,
          str(res["buildings"].get(bid)))

    # window moved along the wall: the export guard refuses it (validate says EXPORT_FAILED) ...
    wobj = API.find_object(win)
    wobj.location.z += 2.0
    bad = API.validate(bid)
    check("moved window: export refused -> EXPORT_FAILED", not bad["ok"] and bad["error_code"] == "EXPORT_FAILED",
          f"{bad['error_code']}: {bad['message'][:100]}")
    wobj.location.z -= 2.0
    # ... and a file whose Window polygon matches no hole is caught by the file check
    import xml.etree.ElementTree as ET
    gc = import_module(ADDON + ".shared.geometry_checks")
    tree = ET.parse(res["path"])
    w = next(e for e in tree.iter() if e.tag.endswith(("}Window", "}WindowSurface")))
    pl = next(e for e in w.iter() if e.tag.endswith("}posList"))
    vals = [float(x) for x in pl.text.split()]
    pl.text = " ".join(str(v + (2.0 if i % 3 == 2 else 0.0)) for i, v in enumerate(vals))
    moved = os.path.join(out, "moved_window.gml")
    tree.write(moved, xml_declaration=True, encoding="utf-8")
    found = gc.check_file(moved)
    check("file check: CGML_FILLING_WITHOUT_HOLE", found.get(bid, {}).get("codes", {}).get("CGML_FILLING_WITHOUT_HOLE") == 1,
          str(found.get(bid, {}).get("codes")))

    # bow-tie: two corners of a ground polygon swapped in the file
    tree = ET.parse(res["path"])
    g = next(e for e in tree.iter() if e.tag.endswith("}GroundSurface"))
    pl = next(e for e in g.iter() if e.tag.endswith("}posList"))
    pts = [pl.text.split()[i:i + 3] for i in range(0, len(pl.text.split()), 3)]
    pts[0], pts[1] = pts[1], pts[0]
    pts[-1] = pts[0]
    pl.text = " ".join(c for p in pts for c in p)
    bow = os.path.join(out, "bowtie.gml")
    tree.write(bow, xml_declaration=True, encoding="utf-8")
    found = gc.check_file(bow)
    check("file check: bow-tie GE_R_SELF_INTERSECTION", found.get(bid, {}).get("codes", {}).get("GE_R_SELF_INTERSECTION") == 1,
          str(found.get(bid, {}).get("codes")))

    # open shell: a whole wall gone
    bid, w0, w1, win = authored()
    other = [s for s in API.inspect(bid)["buildings"][0]["surfaces"] if s["type"] == "WallSurface"
             and s["gml_id"] not in (w0, w1)][0]
    obj = API.find_object(other["gml_id"])
    bpy.data.objects.remove(obj)
    res = API.validate(bid)
    det = res["buildings"][bid]["details"]; det = det.get("GE_S_NOT_CLOSED") or det.get("CGML_SHELL_OPEN") or {}
    check("missing wall: GE_S_NOT_CLOSED with open edges", (findings(res, bid).get("GE_S_NOT_CLOSED") or findings(res, bid).get("CGML_SHELL_OPEN")) == 1
          and det.get("open_edges", 0) >= 4 and det.get("edges"), str(det.get("open_edges")))
    check("missing wall: closed false", res["buildings"][bid]["closed"] is False)

    # Hamburg LoD3
    clear()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(bd, "buildings_lod3_citygml_3_0.gml"))
    res = API.validate(citydoctor=True)
    np_ours = res["summary"].get("GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE")
    check("Hamburg: 3 non-planar polygons", np_ours == 3, str(res["summary"]))
    check("Hamburg: all 3 closed, no filling findings",
          len(res["buildings"]) == 3 and all(b["closed"] for b in res["buildings"].values())
          and "CGML_FILLING_WITHOUT_HOLE" not in res["summary"], str(res["summary"]))
    if API.citydoctor_validator():
        check("Hamburg: CityDoctor ran, per building", res["citydoctor"] is not None
              and all(b["citydoctor"] is not None for b in res["buildings"].values()), str(res["warnings"]))
        np_cd = (res["citydoctor"] or {}).get("GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE", 0)
        check("Hamburg: CityDoctor non-planar = ours", np_cd == np_ours, f"CityDoctor {res['citydoctor']}")
    else:
        print("  [SKIP] CityDoctor not set up (CITYDOCTOR_VALIDATE_SH)")

    # buttons
    r = bpy.ops.cgml.check_geometry()
    check("Check Geometry runs", r == {'FINISHED'}, str(r))
    saved = os.environ.pop("CITYDOCTOR_VALIDATE_SH", None)
    try:
        bpy.ops.cgml.citydoctor_validate()
        msg = ""
    except RuntimeError as e:
        msg = str(e)
    finally:
        if saved is not None:
            os.environ["CITYDOCTOR_VALIDATE_SH"] = saved
    check("CityDoctor button greyed out without a validator, names the ? button", "? button" in msg, msg[:160])
    check("CityDoctor setup help exists (B86)", bpy.ops.cgml.citydoctor_help.poll())

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
