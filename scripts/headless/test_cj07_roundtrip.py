"""CJ-07: Transportation (CityJSON core profile) and CityFurniture, through Blender.

Per fixture: open the CityGML 3.0 file, export CityGML 3.0 (A), export CityJSON, open the CityJSON,
export CityGML 3.0 (B). Measured between A and B, both read by ``citygml_reader``:
- ids by feature type: every Road / Railway / Square / CityFurniture / TrafficArea /
  AuxiliaryTrafficArea id of A is in B with the same type; the ids of A missing in B, by type, are
  exactly what the CityJSON export record reports (``reduced`` + ``not_written``). One exception by
  construction: a TrafficSpace id the 3.0 writer made itself ("<area id>_space") is made again in B;
- coordinates (1 mm, a furniture's template placed by its matrix and reference point): B has every
  coordinate of A except those of the objects reported as not written;
- the CityJSON file passes the structural self-check and holds the ids the record says it holds;
- the scene opened from CityJSON has the same polygon count per top-level feature;
- Browse first on the exported CityJSON shows a box per road / furniture and loads a picked one.

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj07_roundtrip.py
"""
import importlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))
sys.path.insert(0, ROOT)   # pure modules as top-level ``shared``

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from shared import citygml_reader  # noqa: E402
from shared import cityjson_writer as cw  # noqa: E402
from shared.builder_payload import implicit_placement  # noqa: E402

TF = os.path.join(ROOT, "test_files")
KEPT_TYPES = ("Road", "Railway", "Square", "CityFurniture", "TrafficArea", "AuxiliaryTrafficArea")
FAILURES = []
ADDON = ""


def check(label, condition, detail=""):
    print(("[PASS] " if condition else "[FAIL] ") + label + ("" if condition else f" - {str(detail)[:1500]}"))
    if not condition:
        FAILURES.append(label)


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


def fresh():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.cgml3.xsd_validate = False


def polygons():
    """{top-level gml_id: polygons of it and its descendants}."""
    return {str(o["gml_id"]): sum(len(c.data.polygons) for c in (o, *o.children_recursive) if c.type == 'MESH')
            for o in bpy.data.objects if o.get("gml_id") and o.parent is None}


def mm(p):
    return tuple(round(c * 1000) for c in p[:3])


def measure(path):
    """``(ids: {gml_id: type}, coords: {top-level id: 1 mm coordinate set})`` of a CityGML 3.0 file."""
    roots = citygml_reader.read_document(ET.parse(path).getroot(), upgrade=False)
    ids, coords = {}, {}

    def visit(n, top):
        ids[n.gml_id] = getattr(n, "feature_type", "") or n.surface_type
        for g in n.geometries.values():
            if g.gml_type == "ImplicitGeometry":
                m = implicit_placement(g, (0.0, 0.0, 0.0)).matrix
                pts = ([sum(m[k][j] * c[j] for j in range(3)) + m[k][3] for k in range(3)]
                       for p in g.polygons for r in (p.exterior, *p.interiors) for c in r.coords)
            else:
                pts = (c for p in g.polygons for r in (p.exterior, *p.interiors) for c in r.coords)
            coords.setdefault(top, set()).update(mm(c) for c in pts)
        for c in (*getattr(n, "boundaries", ()), *getattr(n, "spaces", ())):
            visit(c, top)
    for r in roots:
        visit(r, r.gml_id)
    return ids, coords


def near(a, b):
    """Coordinates of ``a`` with no coordinate of ``b`` within 1 mm per axis."""
    return {p for p in a if not any((p[0] + i, p[1] + j, p[2] + k) in b
                                    for i in (0, -1, 1) for j in (0, -1, 1) for k in (0, -1, 1))}


def last_export():
    return json.loads(bpy.context.scene.get("cgml_last_export") or "{}")


def doc_ids(doc):
    ids = set(doc["CityObjects"])
    for o in doc["CityObjects"].values():
        for g in o["geometry"]:
            ids.update(s["id"] for s in (g.get("semantics") or {}).get("surfaces", ()))
    return ids


def run_case(src, out, report):
    name = os.path.splitext(os.path.basename(src))[0]
    print(f"\n[cj07] --- {name} ---")
    a_gml, b_gml = os.path.join(out, f"{name}_A.gml"), os.path.join(out, f"{name}_B.gml")
    cjf = os.path.join(out, f"{name}.city.json")

    fresh()
    res = bpy.ops.cgml3.import_gml_file(filepath=src, load_mode='ALL')
    scene_a = polygons()
    res_a = bpy.ops.cgml3.export_gml_file(filepath=a_gml, citygml_version="3.0")
    res_j = bpy.ops.cgml3.export_cityjson_file(filepath=cjf, scope='SCENE')
    rec = last_export()
    check(f"{name}: open, 3.0 export, CityJSON export", res == res_a == res_j == {'FINISHED'}, f"{res} {res_a} {res_j}")
    doc = json.load(open(cjf, encoding="utf-8"))
    problems = cw.validate_document(doc)
    check(f"{name}: CityJSON self-check", problems == [] and rec.get("self_check") == "passed", problems)

    fresh()
    res = bpy.ops.cgml3.import_gml_file(filepath=cjf, load_mode='ALL')
    scene_b = polygons()
    res_b = bpy.ops.cgml3.export_gml_file(filepath=b_gml, citygml_version="3.0")
    check(f"{name}: CityJSON open, 3.0 export", res == res_b == {'FINISHED'}, f"{res} {res_b}")
    ok, msg = mod("ops.validate").validate_citygml3_xsd(b_gml)
    check(f"{name}: B is XSD valid", ok, msg.strip().splitlines()[-1] if msg else "")

    ids_a, co_a = measure(a_gml)
    ids_b, co_b = measure(b_gml)
    reduced, not_written = rec.get("reduced") or {}, rec.get("not_written") or {}
    reported = Counter(reduced) + Counter(not_written)

    # ids
    kept = {i: t for i, t in ids_a.items() if t in KEPT_TYPES}
    wrong = {i: (t, ids_b.get(i)) for i, t in kept.items() if ids_b.get(i) != t}
    check(f"{name}: {len(kept)} ids of {sorted(set(kept.values()))} unchanged, same class", not wrong, wrong)
    lost = Counter(t for i, t in ids_a.items() if i not in ids_b)
    reduced_ids = {i for ids in (rec.get("reduced_ids") or {}).values() for i in ids}
    # a TrafficSpace the 3.0 writer made for an area ("<area id>_space") is not in the CityJSON file, but the
    # 3.0 writer makes it again in B under the same id: reported as reduced, and back by construction
    again = {i for i in reduced_ids if i in ids_b}
    check(f"{name}: reduced ids found in B again are writer-made '<area>_space' ids only ({sorted(again)})",
          all(i.endswith("_space") and i[:-6] in ids_b for i in again))
    check(f"{name}: ids of A missing in B, by type (+ {len(again)} made again) = the export record "
          f"({dict(reported)})", lost + Counter(ids_a[i] for i in again) == reported, f"measured {dict(lost)}")
    not_in_file = {i for i in ids_a if i not in doc_ids(doc)}
    tops_gone = {i for i in co_a if i not in doc["CityObjects"]}
    check(f"{name}: ids of A not in the CityJSON file = reported ids", not_in_file == reduced_ids | tops_gone,
          f"file misses {sorted(not_in_file)}, record {sorted(reduced_ids)} + {sorted(tops_gone)}")
    check(f"{name}: record lists the unwritten top-level ids", set(rec.get("skipped") or ()) >= tops_gone
          and sum(not_written.values()) == len(tops_gone), f"{rec.get('skipped')} / {tops_gone}")
    notes = " | ".join(rec.get("notes") or ())
    check(f"{name}: every reported kind is named with its count in the export summary",
          all(f"{n} {k}" in notes for k, n in reported.items()), notes)

    # coordinates, per top-level feature
    all_a = set().union(*(c for i, c in co_a.items() if i not in tops_gone)) if co_a else set()
    all_b = set().union(*co_b.values()) if co_b else set()
    miss, extra = near(all_a, all_b), near(all_b, all_a)
    check(f"{name}: coordinates within 1 mm ({len(all_a)} points)", not miss and not extra,
          f"missing {sorted(miss)[:3]} ({len(miss)}), extra {sorted(extra)[:3]} ({len(extra)})")
    per = {i: len(near(co_a[i], co_b.get(i, set()))) for i in co_a if i not in tops_gone}
    check(f"{name}: per top-level feature too", not any(per.values()), {i: n for i, n in per.items() if n})

    # the scene itself
    want = {i: n for i, n in scene_a.items() if i not in tops_gone}
    check(f"{name}: polygons per top-level feature in the scene {want}", scene_b == want, scene_b)
    report[name] = {"ids_A": dict(Counter(ids_a.values())), "ids_B": dict(Counter(ids_b.values())),
                    "kept_ids": len(kept), "reduced": reduced, "not_written": not_written,
                    "measured_missing": dict(lost), "made_again_by_gml_writer": sorted(again), "points": len(all_a), "city_objects": len(doc["CityObjects"]),
                    "templates": len((doc.get("geometry-templates") or {}).get("templates", ())),
                    "notes": rec.get("notes")}
    return cjf


def case_browse(cjf, pick_type):
    """Browse first on an exported CityJSON: boxes for the new types, Load of one pick."""
    name = os.path.basename(cjf)
    print(f"\n[cj07] --- Browse first: {name} ---")
    fresh()
    bm = mod("ops.browse_gml")
    res = bpy.ops.cgml3.browse_gml_file(filepath=cjf, use_cache=False)
    s = bm.get_session(cjf)
    check(f"{name}: browse session", res == {'FINISHED'} and s is not None and s.complete, f"{res}")
    if s is None:
        return
    types = Counter(e.feature_type for e in s.entries)
    check(f"{name}: entries {dict(types)}, each with a box", bool(s.entries) and all(e.bbox for e in s.entries))
    pick = next(e for e in s.entries if e.feature_type == pick_type)
    bpy.ops.cgml3.browse_select(action='SET', ids=pick.gml_id)
    res = bpy.ops.cgml3.browse_load()
    built = [o for o in bpy.data.objects if o.get("gml_id") == pick.gml_id and not o.get("cgml3_browse_path")]
    tops = [o for o in bpy.data.objects if o.get("gml_id") and o.parent is None and not o.get("cgml3_browse_path")]
    check(f"{name}: Load of {pick_type} {pick.gml_id}", res == {'FINISHED'} and len(built) == 1 and len(tops) == 1,
          f"{res} built {len(built)} tops {[o.get('gml_id') for o in tops]}")
    if built:
        polys = sum(len(c.data.polygons) for c in (built[0], *built[0].children_recursive) if c.type == 'MESH')
        check(f"{name}: the loaded {pick_type} has geometry ({polys} polygons)", polys > 0)
    bpy.ops.cgml3.browse_close()


def case_road_without_own_geometry(out):
    """A Road whose polygons are all TrafficArea / AuxiliaryTrafficArea (the usual CityJSON and CityGML 2.0
    road): the scene keeps the Road as the parent, and every export writes the Road with the areas in it."""
    print("\n[cj07] --- Road without geometry of its own ---")
    src = os.path.join(out, "road_areas_only.city.json")
    sq = [[0, 0, 0], [4000, 0, 0], [4000, 3000, 0], [0, 3000, 0]]
    doc = {"type": "CityJSON", "version": "2.0",
           "transform": {"scale": [0.001] * 3, "translate": [564700.0, 5934300.0, 10.0]},
           "vertices": sq + [[x + 5000, y, z] for x, y, z in sq],
           "CityObjects": {"R1": {"type": "Road", "attributes": {"note": "areas only"}, "geometry": [{
               "type": "MultiSurface", "lod": "2", "boundaries": [[[0, 1, 2, 3]], [[4, 5, 6, 7]]],
               "semantics": {"surfaces": [{"type": "TrafficArea", "id": "TA1"},
                                          {"type": "AuxiliaryTrafficArea", "id": "AA1"}], "values": [0, 1]}}]}}}
    json.dump(doc, open(src, "w", encoding="utf-8"))
    fresh()
    res = bpy.ops.cgml3.import_gml_file(filepath=src, load_mode='ALL')
    tops = [o for o in bpy.data.objects if o.get("gml_id") and o.parent is None]
    kids = sorted(str(c.get("gml_id")) for t in tops for c in t.children_recursive if c.get("gml_id"))
    check("road without own geometry: one top-level Road with TA1 and AA1 below it",
          res == {'FINISHED'} and [(t["gml_id"], t.get("cgml3_feature")) for t in tops] == [("R1", "Road")]
          and kids == ["AA1", "TA1"], f"{res} {[(t.get('gml_id'), t.get('cgml3_feature')) for t in tops]} {kids}")
    cjf = os.path.join(out, "road_areas_only_out.city.json")
    res_j = bpy.ops.cgml3.export_cityjson_file(filepath=cjf, scope='SCENE')
    back = json.load(open(cjf, encoding="utf-8"))["CityObjects"] if res_j == {'FINISHED'} else {}
    sem = sorted((s["type"], s.get("id")) for o in back.values() for g in o["geometry"]
                 for s in (g.get("semantics") or {}).get("surfaces", ()))
    check("road without own geometry: CityJSON export writes the Road with both areas",
          list(back) == ["R1"] and back["R1"]["type"] == "Road"
          and sem == [("AuxiliaryTrafficArea", "AA1"), ("TrafficArea", "TA1")], f"{res_j} {list(back)} {sem}")
    for version in ("3.0", "2.0"):
        gml = os.path.join(out, f"road_areas_only_{version}.gml")
        res_g = importlib.import_module(f"{ADDON}.shared.agent_api").export(gml, version=version, xsd=True)
        root = ET.parse(gml).getroot() if res_g["ok"] else None
        members = [m[0] for m in root if m.tag.endswith("cityObjectMember")] if root is not None else []
        inner = {v for m in members for e in m.iter() for k, v in e.attrib.items() if k.endswith("}id")}
        check(f"road without own geometry: CityGML {version} export has one Road member holding TA1 and AA1",
              [m.tag.rsplit("}", 1)[-1] for m in members] == ["Road"] and {"R1", "TA1", "AA1"} <= inner,
              f"{res_g['message']} {[m.tag for m in members]} {sorted(inner)}")
        check(f"road without own geometry: CityGML {version} export is XSD valid",
              res_g.get("export", {}).get("xsd") == "valid", res_g["message"])


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj07_")
    os.makedirs(out, exist_ok=True)
    v3 = os.path.join(TF, "coverage", "Transportation_citygml3.gml")
    # schema-valid 3.0: a TrafficArea is a core:boundary of its TrafficSpace (as test_t14 builds it)
    boundary = os.path.join(out, "Transportation_citygml3_boundary.gml")
    text = open(v3, encoding="utf-8").read()
    open(boundary, "w", encoding="utf-8").write(
        re.sub(r"(</?)tran:(?:auxiliaryT|t)rafficArea>", r"\1core:boundary>", text))
    # the same file with dotted ids ("R.3", "TA.3"): valid NCNames, they must not change anywhere (K2)
    dotted = os.path.join(out, "Transportation_dotted_ids.gml")
    open(dotted, "w", encoding="utf-8").write(
        re.sub(r'gml:id="([A-Z]+)3', r'gml:id="\g<1>.3', open(boundary, encoding="utf-8").read()))
    fixtures = [v3, boundary, dotted, os.path.join(TF, "Railway", "Railway_citygml3.gml"),
                os.path.join(TF, "CityFurniture", "Implicit_CityGML3.gml"),
                os.path.join(TF, "CityFurniture", "Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml")]
    report, files = {}, {}
    for src in fixtures:
        files[os.path.basename(src)] = run_case(src, out, report)
    dotted_ids = set(json.load(open(files["Transportation_dotted_ids.gml"], encoding="utf-8"))["CityObjects"])
    check("dotted ids are the CityJSON object ids", dotted_ids == {"R.3", "SQ.3"}, dotted_ids)
    case_browse(files["Transportation_citygml3_boundary.gml"], "Road")
    case_browse(files["Implicit_CityGML3.gml"], "CityFurniture")
    case_road_without_own_geometry(out)
    with open(os.path.join(out, "cj07_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=str)
    print(json.dumps(report, indent=1, default=str))
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
