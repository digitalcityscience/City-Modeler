"""B59 (agent plan A4): agent_api commands, no selection / active object / mode.

Every command returns {ok, error_code, message, created, changed, warnings}; each is tried once
successfully and with at least one error code. Nothing is selected or active before a command,
and the mode stays OBJECT throughout. The export of the result is XSD valid.

Old code: no agent_api commands (AttributeError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b59_agent_commands.py -- <out_dir>
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
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

KEYS = {"ok", "error_code", "message", "created", "changed", "warnings"}
API = None


def call(name, *args, **kwargs):
    """Run a command with nothing selected / active; check the contract and the mode."""
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = None
    res = getattr(API, name)(*args, **kwargs)
    json.dumps(res)
    check(f"{name}: result keys", KEYS <= set(res), str(sorted(res)))
    check(f"{name}: still Object Mode", bpy.context.mode == 'OBJECT', bpy.context.mode)
    print("   ", name, res["ok"], res["error_code"], res["message"][:120])
    return res


def expect_error(code, name, *args, **kwargs):
    res = call(name, *args, **kwargs)
    check(f"{name} -> {code}", not res["ok"] and res["error_code"] == code, f"{res['error_code']}: {res['message']}")
    return res


def walls(bid):
    b = API.inspect(bid)["buildings"][0]
    return [(s, f) for s in b["surfaces"] if s["type"] == "WallSurface" for f in s["faces"] if f["role"] == "polygon"]


def main():
    global API
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b59_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    API = import_module(addon + ".shared.agent_api")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    bpy.ops.cgml.building_new(points=footprint(width=12.0, depth=8.0))  # 12 x 8, 9 m
    bid = str(bpy.context.active_object["gml_id"])

    r = call("add_roof", bid, "3100", pitch=30.0)
    check("add_roof ok, building changed", r["ok"] and r["changed"] == [bid])
    expect_error("BAD_ARGUMENT", "add_roof", bid, "9999")
    expect_error("NOT_A_BUILDING", "add_roof", walls(bid)[0][0]["gml_id"])

    # the two gable walls (pentagons): a ground door there failed before the 1 cm snap (B60)
    (s0, f0), (s1, f1) = sorted(walls(bid), key=lambda sf: (sf[1]["frame"]["width"], sf[0]["gml_id"]))[:2]
    r = call("add_opening", s0["gml_id"], f0["index"], 0.3, 0.5, 1.2, 1.4)
    check("add_opening ok, 1 window created", r["ok"] and len(r["created"]) == 1 and r["changed"] == [s0["gml_id"]])
    win = r["created"][0] if r["created"] else ""
    expect_error("PLACEMENT_REFUSED", "add_opening", s0["gml_id"], f0["index"], 0.3, 0.5, 1.2, 1.4)
    expect_error("BAD_ARGUMENT", "add_opening", s0["gml_id"], f0["index"], 0.5, 0.5, kind="Garage")
    expect_error("BAD_ARGUMENT", "add_opening", s0["gml_id"], f0["index"], 1.5, 0.5)
    expect_error("NOT_FOUND", "add_opening", "NOPE", 0, 0.5, 0.5)
    expect_error("BAD_FACE", "add_opening", s0["gml_id"], 99, 0.5, 0.5)
    roof = next(s for s in API.inspect(bid)["buildings"][0]["surfaces"] if s["type"] == "RoofSurface")
    expect_error("NOT_A_WALL", "add_opening", roof["gml_id"], 0, 0.5, 0.5)
    ring = next(f for s, f in [(s, f) for s in API.inspect(bid)["buildings"][0]["surfaces"]
                               if s["gml_id"] == s0["gml_id"] for f in s["faces"]] if f["role"] == "interior_ring")
    expect_error("BAD_FACE", "add_opening", s0["gml_id"], ring["index"], 0.5, 0.5)

    door_v = (2.1 / 2) / f0["frame"]["height"]
    r = call("add_opening", s0["gml_id"], f0["index"], 0.75, door_v, 1.0, 2.1, kind="Door")
    check("ground door: notch topology", r["ok"] and r.get("topology") == "ground_notch", str(r.get("topology")))

    r = call("add_openings_grid", s1["gml_id"], f1["index"], 3, 2, 1.0, 1.2, 2.5, 3.0, origin_uv=(0.25, 0.25))
    check("grid: 6 placed", r["ok"] and len(r["created"]) == 6 and not r["skipped"],
          f"{len(r['created'])} placed, skipped {r.get('skipped')}")
    r = call("add_openings_grid", s1["gml_id"], f1["index"], 3, 1, 1.0, 1.2, 2.5, 3.0, origin_uv=(0.25, 0.25))
    check("grid again: nothing fits -> PLACEMENT_REFUSED with skipped list",
          not r["ok"] and r["error_code"] == "PLACEMENT_REFUSED" and len(r.get("skipped", [])) == 3)

    fr = f1["frame"]
    point = Vector(fr["origin"]) + Vector(fr["u_axis"]) * (fr["width"] * 0.5) + Vector(fr["v_axis"]) * 3.0
    r = call("add_balcony", bid, point[:], f1["normal"], width=2.0)
    check("add_balcony ok", r["ok"] and len(r["created"]) == 1)
    expect_error("BAD_ARGUMENT", "add_balcony", bid, point[:], (0, 0, 1))
    expect_error("BAD_ARGUMENT", "add_balcony", bid, point[:], f1["normal"], kind="FLYING")

    rf = next(f for f in roof["faces"] if f.get("frame"))
    rfr = Vector(rf["frame"]["origin"]) + Vector(rf["frame"]["u_axis"]) * (rf["frame"]["width"] * 0.3) \
        + Vector(rf["frame"]["v_axis"]) * (rf["frame"]["height"] * 0.5)
    r = call("add_chimney", bid, rfr[:], rf["normal"])
    check("add_chimney ok", r["ok"] and len(r["created"]) == 1)
    expect_error("BAD_ARGUMENT", "add_chimney", bid, rfr[:], (1, 0, 0))
    expect_error("ROOF_REFUSED", "add_roof", bid, "1000")  # windows / installations now on it

    r = call("check_planarity", bid)
    check("check_planarity: 0 on an authored building", r["ok"] and r["count"] == 0, str(r.get("faces")))
    r = call("fix_planarity", bid)
    check("fix_planarity: 0 -> 0, nothing changed", r["ok"] and r["before"] == 0 and r["after"] == 0 and not r["changed"])

    r = call("delete_feature", win)
    check("delete window ok", r["ok"] and win in r["deleted"] and r["changed"] == [s0["gml_id"]])
    rings = [f for s, f in [(s, f) for s in API.inspect(bid)["buildings"][0]["surfaces"]
                            if s["gml_id"] == s0["gml_id"] for f in s["faces"]] if f["role"] == "interior_ring"]
    check("its hole is closed", not rings, str(rings))
    expect_error("NOT_DELETABLE", "delete_feature", s1["gml_id"])
    expect_error("NOT_FOUND", "delete_feature", win)

    p = os.path.join(out, "agent.gml")
    r = call("export", p, "3.0")
    rec = r.get("export", {})
    check("export ok, 1 written, XSD valid", r["ok"] and rec.get("written") == [bid] and rec.get("xsd") == "valid",
          f"{r['message']}")
    r = call("export", os.path.join(out, "agent_v2.gml"), "2.0", scope="IDS", gml_ids=[bid])
    check("export 2.0 IDS ok, XSD valid", r["ok"] and r["export"]["xsd"] == "valid", r["message"])
    expect_error("SCOPE_ERROR", "export", p, "3.0", scope="IDS", gml_ids=["NOPE"])
    expect_error("SCOPE_ERROR", "export", p, "3.0", scope="SELECTION")
    expect_error("BAD_ARGUMENT", "export", p, "1.0")

    r = call("delete_feature", bid)
    check("delete building ok", r["ok"] and bid in r["deleted"] and not API.inspect()["buildings"])

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
