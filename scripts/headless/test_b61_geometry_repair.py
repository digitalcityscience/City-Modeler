"""B61 (agent plan A6): repairs on request, before/after counts, self-intersection diagnosis.

- Hamburg LoD3 CityGML 3.0: repair SE_BS_UNFRAGMENTED -> 0 bent surfaces (building surfaces and
  installation surfaces); GE_P_NON_PLANAR -> 0; the export stays XSD valid; with CityDoctor set up:
  after both, no GE_P_NON_PLANAR_* and no SE_BS_UNFRAGMENTED, and no error class the source did not
  have (source: self-intersection 3, non-planar 3, unfragmented 6)
- GE_S_SELF_INTERSECTION: diagnosis only - Hamburg zU7 names at least one polygon pair, LoD2 none,
  nothing changes
- a wall's bottom edge split (T-junction with the ground): the shell is open, GE_S_NOT_CLOSED weld
  inserts the corner into the ground polygon, the shell closes and the solid is written again
- error codes: unknown repair code, not a building; the Repair Geometry buttons run

Old code: no agent_api.repair (AttributeError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b61_geometry_repair.py -- <out_dir> [<buildings_dir>]
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

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

BD = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
SOURCE_CODES = {"GE_S_SELF_INTERSECTION", "GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE", "SE_BS_UNFRAGMENTED"}


def load(bd, name):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(bd, name))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b61_")
    bd = argv[1] if len(argv) > 1 else BD
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    api = import_module(addon + ".shared.agent_api")

    # Hamburg LoD3: unfragmented + non-planar
    load(bd, "buildings_lod3_citygml_3_0.gml")
    ids = [b["gml_id"] for b in api.inspect()["buildings"]]
    unf = [api.repair(i, "SE_BS_UNFRAGMENTED") for i in ids]
    check("Hamburg: bent surfaces found and split to 0",
          all(r["ok"] for r in unf) and sum(r["before"] for r in unf) > 0 and sum(r["after"] for r in unf) == 0
          and sum(len(r["created"]) for r in unf) > 0, str([(r["before"], r["after"], len(r["created"])) for r in unf]))
    npl = [api.repair(i, "GE_P_NON_PLANAR") for i in ids]
    check("Hamburg: non-planar 3 -> 0", sum(r["before"] for r in npl) == 3 and sum(r["after"] for r in npl) == 0,
          str([(r["before"], r["after"]) for r in npl]))
    again = [api.repair(i, "SE_BS_UNFRAGMENTED") for i in ids]
    check("Hamburg: after flattening, split again -> 0", sum(r["after"] for r in again) == 0,
          str([(r["before"], r["after"]) for r in again]))
    res = api.validate(citydoctor=True)
    check("Hamburg repaired: XSD valid, our checks clean", res["ok"] and res["xsd"] == "valid" and not res["summary"],
          f"{res['xsd']} {res['summary']}")
    if res["citydoctor"] is not None:
        cd = res["citydoctor"]
        check("Hamburg repaired: CityDoctor no non-planar, no unfragmented",
              not any(k.startswith("GE_P_NON_PLANAR") or k == "SE_BS_UNFRAGMENTED" for k in cd), str(cd))
        check("Hamburg repaired: no new CityDoctor error class", set(cd) <= SOURCE_CODES, str(cd))
    else:
        print("  [SKIP] CityDoctor not set up (CITYDOCTOR_VALIDATE_SH)", res["warnings"])

    # self-intersection: diagnosis only
    load(bd, "buildings_lod3_citygml_3_0.gml")
    r = api.repair("DEHHALKAJ0000zU7", "GE_S_SELF_INTERSECTION")
    check("zU7: a crossing polygon pair is named, nothing changed",
          r["ok"] and r["pairs"] and not r["changed"] and not r["created"]
          and all(p["a"]["surface"] and p["b"]["surface"] for p in r["pairs"]), str(r.get("pairs"))[:200])
    load(bd, "buildings_lod2_citygml_3_0.gml")
    r = api.repair("DEHHALKAJ0000zU7", "GE_S_SELF_INTERSECTION")
    check("LoD2 zU7: no pair", r["ok"] and r["pairs"] == [], str(r.get("pairs")))

    # T-junction: a wall's bottom edge split
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint(width=12.0, depth=8.0))
    bid = str(bpy.context.active_object["gml_id"])
    api.add_roof(bid, "3100")
    wall = api.find_object(next(s["gml_id"] for s in api.inspect(bid)["buildings"][0]["surfaces"]
                                if s["type"] == "WallSurface"))
    bm = bmesh.new()
    bm.from_mesh(wall.data)
    mw = wall.matrix_world
    bottom = next(e for e in bm.edges if all(abs((mw @ v.co).z) < 1e-6 for v in e.verts))
    bmesh.utils.edge_split(bottom, bottom.verts[0], 0.5)
    bm.to_mesh(wall.data)
    bm.free()
    res = api.validate(bid)
    b = res["buildings"][bid]
    check("split wall edge: shell open with a T-junction", b["closed"] is False and
          (b["details"].get("GE_S_NOT_CLOSED") or b["details"].get("CGML_SHELL_OPEN") or {}).get("t_junctions", 0) >= 1,
          str(b["checks"]))
    r = api.repair(bid, "GE_S_NOT_CLOSED")
    check("weld: before >= 1, after 0, a corner inserted", r["ok"] and r["before"] >= 1 and r["after"] == 0
          and r.get("inserted", 0) >= 1, f"{r.get('before')} -> {r.get('after')}, inserted {r.get('inserted')}")
    res = api.validate(bid)
    b = res["buildings"][bid]
    check("weld: shell closed, solid written, XSD valid", b["closed"] is True and b.get("solid") is not False
          and res["xsd"] == "valid" and not b["checks"], str(b["checks"]))

    # errors and buttons
    r = api.repair(bid, "SE_NOPE")
    check("unknown code -> BAD_ARGUMENT", not r["ok"] and r["error_code"] == "BAD_ARGUMENT")
    r = api.repair(wall["gml_id"], "GE_S_NOT_CLOSED")
    check("a wall -> NOT_A_BUILDING", not r["ok"] and r["error_code"] == "NOT_A_BUILDING")
    bpy.ops.object.select_all(action='DESELECT')
    for code in ("SE_BS_UNFRAGMENTED", "GE_S_NOT_CLOSED", "GE_S_SELF_INTERSECTION"):
        check(f"Repair Geometry {code} runs", bpy.ops.cgml.repair_geometry(code=code) == {'FINISHED'})

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
