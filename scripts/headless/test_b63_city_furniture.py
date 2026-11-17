"""B63: place_city_furniture - a ready-made CityFurniture copied to a point by the agent.

- Railway scene CityFurniture (CityGML 2.0, 195 polygons): a copy 10 m east, turned 90 degrees:
  its base lands on the point, all gml_ids (feature, polygons, rings) are new, frn:function is
  overridden; inspect lists 2; the 2.0 and 3.0 exports are XSD valid with both features written
- implicit template source (Implicit_CityGML3, F_A): the copy is explicit geometry, export 3.0
  XSD valid, a warning says so
- errors: a non-furniture id, a bad point; delete_feature removes the copy

Old code: no agent_api.place_city_furniture (AttributeError).

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b63_city_furniture.py -- <out_dir>
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

TF = os.path.join(HERE, "..", "..", "test_files", "CityFurniture")


def load(name):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml3.import_gml_file(filepath=os.path.abspath(os.path.join(TF, name)))


def base(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return Vector(((min(p.x for p in pts) + max(p.x for p in pts)) / 2,
                   (min(p.y for p in pts) + max(p.y for p in pts)) / 2, min(p.z for p in pts)))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b63_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    api = import_module(addon + ".shared.agent_api")

    load("Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml")
    src = api.inspect()["city_furniture"][0]
    so = api.find_object(src["gml_id"])
    target = base(so) + Vector((10.0, 0.0, 0.0))
    r = api.place_city_furniture(src["gml_id"], target[:], rotation_deg=90.0, function="1110")
    check("place ok, 1 created", r["ok"] and len(r["created"]) == 1, r["message"])
    new = api.find_object(r["created"][0])
    check("base on the point", (base(new) - target).length < 1e-3, str(base(new) - target))
    check("function overridden, attributes kept", new.get("frn:function") == "1110" and new.get("CRS") == so.get("CRS"))
    old_ids = {m.get("gml_polygon_id") for m in so.data.materials}
    new_ids = {m.get("gml_polygon_id") for m in new.data.materials}
    check("new polygon ids", new_ids and not (old_ids & new_ids) and len(new_ids) == len(old_ids),
          f"{len(new_ids)} new / {len(old_ids & new_ids)} shared")
    check("inspect lists 2 CityFurniture", len(api.inspect()["city_furniture"]) == 2)
    for v in ("2.0", "3.0"):
        res = api.export(os.path.join(out, f"frn_{v}.gml"), v)
        check(f"export {v}: XSD valid, both written",
              res["ok"] and res["export"]["xsd"] == "valid" and set(res["export"]["written"]) == {src["gml_id"], r["created"][0]},
              res["message"])
    r2 = api.delete_feature(r["created"][0])
    check("delete the copy", r2["ok"] and len(api.inspect()["city_furniture"]) == 1)

    load("Implicit_CityGML3.gml")
    r = api.place_city_furniture("F_A", (50.0, 50.0, 0.0))
    check("implicit source: placed with a warning", r["ok"] and r["warnings"], str(r["warnings"]))
    res = api.export(os.path.join(out, "frn_implicit.gml"), "3.0")
    check("implicit source: export XSD valid, copy written", res["ok"] and res["export"]["xsd"] == "valid"
          and r["created"][0] in res["export"]["written"], res["message"])

    r = api.place_city_furniture("NOPE", (0, 0, 0))
    check("unknown id -> NOT_FOUND", r["error_code"] == "NOT_FOUND")
    bpy.ops.cgml.building_new(points=__import__("addon_test_utils").footprint())
    bid = str(bpy.context.active_object["gml_id"])
    check("a building -> BAD_ARGUMENT", api.place_city_furniture(bid, (0, 0, 0))["error_code"] == "BAD_ARGUMENT")
    check("bad point -> BAD_ARGUMENT", api.place_city_furniture("F_B", (0, 0))["error_code"] == "BAD_ARGUMENT")

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
