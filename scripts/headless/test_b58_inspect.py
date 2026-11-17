"""B58 (agent plan A3): agent_api.inspect() reads the scene as JSON.

- authored building: JSON-serialisable; every WallSurface polygon face can_host, roof/ground
  faces cannot (with a reason); a window placed at (0.3, 0.5) shows up on its wall face with
  u/v within 1 cm and its size; its hole face is an interior_ring that cannot host
- Hamburg LoD3 CityGML 3.0 (3 buildings): 3 buildings, each WallSurface polygon face has a frame,
  errors list the 3 non-planar roof polygons (CityDoctor GE_P_NON_PLANAR 3), inspect of one id
- Transportation (coverage fixture) and CityFurniture (Railway scene) features are listed

Old code: no shared.agent_api (ImportError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b58_inspect.py -- <out_dir> <buildings_dir>
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

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

TF = os.path.join(HERE, "..", "..", "test_files")
BD = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def faces(b, stype):
    return [f for s in b["surfaces"] if s["type"] == stype for f in s["faces"]]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b58_")
    bd = argv[1] if len(argv) > 1 else BD
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    api = import_module(addon + ".shared.agent_api")
    fp = import_module(addon + ".shared.facade_placement")
    fsr = import_module(addon + ".shared.facade_surface_resolver")

    # authored building + one window, no selection / mode involved
    clear()
    bpy.ops.cgml.building_new(points=footprint())
    bpy.ops.cgml.roof_add(roof_type="3100")
    bpy.ops.object.select_all(action='DESELECT')
    data = api.inspect()
    json.dumps(data)
    check("authored: JSON-serialisable, 1 building", len(data["buildings"]) == 1)
    b = data["buildings"][0]
    walls = faces(b, "WallSurface")
    check("authored: every wall polygon face can host", walls and all(f["can_host"] for f in walls),
          str([(f["index"], f["can_host"]) for f in walls]))
    roofs = faces(b, "RoofSurface")
    check("authored: roof faces cannot host, with a reason",
          roofs and all(not f["can_host"] and "WallSurface" in f["reason"] for f in roofs))
    s = next(s for s in b["surfaces"] if s["type"] == "WallSurface")
    wall = bpy.data.objects[s["object"]]
    ctx = fsr.resolve_surface_context(wall, 0)
    fp.place_semantic_element(fp.FacadeElementDefinition(semantic_type="Window", width=1.2, height=1.4),
                              ctx, fp.PlacementRequest(position_uv=(0.3, 0.5)))
    b = api.inspect(b["gml_id"])["buildings"][0]
    s = next(x for x in b["surfaces"] if x["gml_id"] == s["gml_id"])
    ops = [o for f in s["faces"] for o in f.get("openings", [])]
    check("window listed on its wall face", len(ops) == 1, str(ops))
    if ops:
        o = ops[0]
        fw = next(f for f in s["faces"] if f.get("openings"))["frame"]["width"]
        check("window u/v and size", abs(o["u"] - 0.3) * fw < 0.01 and abs(o["width"] - 1.2) < 0.01
              and abs(o["height"] - 1.4) < 0.01 and o["type"] == "Window", str(o))
    rings = [f for f in s["faces"] if f["role"] == "interior_ring"]
    check("window hole is an interior_ring face that cannot host", len(rings) == 1 and not rings[0]["can_host"])

    # Hamburg LoD3 CityGML 3.0
    clear()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(bd, "buildings_lod3_citygml_3_0.gml"))
    data = api.inspect()
    json.dumps(data)
    check("Hamburg: 3 buildings", len(data["buildings"]) == 3, str(len(data["buildings"])))
    errs = [e for b in data["buildings"] for e in b["errors"]]
    check("Hamburg: 3 non-planar polygons in errors", len(errs) == 3, str(errs))
    walls = [f for b in data["buildings"] for f in faces(b, "WallSurface") if f["role"] == "polygon"]
    check("Hamburg: wall polygon faces have a frame", walls and all("frame" in f or not f["can_host"] for f in walls),
          f"{len(walls)} faces")
    check("Hamburg: most wall faces can host", sum(f["can_host"] for f in walls) >= len(walls) * 0.9,
          f"{sum(f['can_host'] for f in walls)}/{len(walls)}")
    check("Hamburg: crs set", "25832" in data["crs"], data["crs"])
    one = api.inspect(data["buildings"][1]["gml_id"])
    check("inspect(id) returns that building", [b["gml_id"] for b in one["buildings"]] == [data["buildings"][1]["gml_id"]])
    check("inspect(unknown) -> NOT_FOUND", api.inspect("NOPE").get("error", {}).get("code") == "NOT_FOUND")
    with open(os.path.join(out, "hamburg_inspect.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)

    # Transportation and CityFurniture
    clear()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(TF, "coverage", "Transportation_citygml3.gml"))
    data = api.inspect()
    json.dumps(data)
    check("Transportation features listed", len(data["transportation"]) > 0,
          str([(t["type"], t["parts"]) for t in data["transportation"]]))
    clear()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(TF, "CityFurniture", "Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml"))
    data = api.inspect()
    json.dumps(data)
    check("CityFurniture features listed", len(data["city_furniture"]) > 0, str(len(data["city_furniture"])))

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
