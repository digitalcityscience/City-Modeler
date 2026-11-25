"""CJ-03: CityJSON 2.0 / CityJSONSeq export through the Save tab operator (cgml3.export_cityjson_file).

Fingerprints (scripts/tests/cj_fingerprint.py: feature counts, surface classes, ring counts, 1 mm
coordinates, parent/host links, attribute-card view), not bytes:
- CityJSON -> scene -> CityJSON is the same (lod2, lod3 samples, multi-LoD fixture with original ids
  and LoD text 2.2); CityJSONSeq of the same scene goes through the CJ-05 index
- CityGML 3.0 -> scene -> CityJSON -> scene -> CityGML 3.0 is the same (samples, Railway LoD3 with
  doors/windows/installations, BuildingParts)
- a building drawn from scratch (B01 fixture) goes out as CityJSON and opens again
- the GML writers' id helpers never rename a valid gml:id (xs:ID = NCName, dots allowed); an invalid one
  is rewritten as before
Guards: the scene's own CityJSON source is refused (file unchanged); a partly loaded scene says so;
CityFurniture is counted, not written; a failed self-check leaves the old file; agent_api.export.

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj03_export.py
"""
import json
import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)   # pure modules as top-level ``shared``

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from cj_fingerprint import diff, fingerprint  # noqa: E402
from shared import citygml_reader, cityjson_open, cityjson_reader  # noqa: E402
from shared.browse import cityjson_index  # noqa: E402

SAMPLES = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
TF = os.path.join(ROOT, "test_files")
MULTI = os.path.join(TF, "CityJSON", "cj02_multi_lod.city.json")
RAILWAY = os.path.join(TF, "Building", "Railway_Scene_LoD3_CityGML3_Building_only.gml")
FURNITURE = os.path.join(TF, "CityFurniture", "Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml")
FAILURES = []
ADDON = None


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


def read_cj(path, lod=None):
    if path.endswith(".jsonl"):
        idx = cityjson_index.index_cityjson(path)
        return cityjson_reader.read_document(cityjson_index.selection_document(idx, range(len(idx.entries)))[0])
    return cityjson_open.with_geometry(cityjson_reader.read_document(cityjson_reader.load(path), lod=lod))[0]


def read_gml(path):
    return [r for r in citygml_reader.read_document(ET.parse(path).getroot())
            if r.feature_type in ("Building", "BuildingPart")]


def export_cj(path, **kw):
    try:
        return bpy.ops.cgml3.export_cityjson_file(filepath=path, **kw)
    except RuntimeError as e:
        return str(e)


def last_export():
    return json.loads(bpy.context.scene.get("cgml_last_export", "{}"))


def same(label, a, b, allow=("con:height:uom",)):
    """Fingerprints equal; ``allow``: card keys CityJSON cannot carry (measuredHeight has no uom)."""
    d = diff(a, b)
    props = d.pop("props", {})
    bad = {k: sorted(p for p in set(map(tuple, x or ())) ^ set(map(tuple, y or ())) if p[0] not in allow)
           for k, (x, y) in props.items()}
    bad = {k: v for k, v in bad.items() if v}
    if bad:
        d["props"] = bad
    check(label, not d, json.dumps(d, default=str)[:1500])


def cityjson_round_trip(out, src, lod=None):
    name = os.path.basename(src).split(".")[0]
    clean()
    kw = {"cityjson_lod": lod} if lod else {}
    check(f"{name}: opens", bpy.ops.cgml3.import_gml_file(filepath=src, **kw) == {'FINISHED'})
    check(f"{name}: Save tab format defaults to CityJSON",
          bpy.context.window_manager.cgml_export_format == 'CITYJSON')
    path = os.path.join(out, f"{name}_cj.city.json")
    res = export_cj(path)
    check(f"{name}: CityJSON export finishes", res == {'FINISHED'} and os.path.isfile(path), str(res))
    if not os.path.isfile(path):
        return
    rec = last_export()
    check(f"{name}: export record (format, self-check, cjval not run)",
          rec.get("format") == "CityJSON" and rec.get("self_check") == "passed" and rec.get("cjval") == "not run", str(rec)[:300])
    same(f"{name}: CityJSON -> scene -> CityJSON fingerprint", fingerprint(read_cj(src, lod)), fingerprint(read_cj(path)),
         allow=())
    seq = os.path.join(out, f"{name}_cj.city.jsonl")
    res = export_cj(seq, sequence=True)
    check(f"{name}: CityJSONSeq export finishes", res == {'FINISHED'} and os.path.isfile(seq), str(res))
    if os.path.isfile(seq):
        same(f"{name}: CityJSONSeq through the CJ-05 index, same fingerprint",
             fingerprint(read_cj(src, lod)), fingerprint(read_cj(seq)), allow=())
    return path


def multi_lod_specifics(path):
    doc = cityjson_reader.load(path)
    objs = doc["CityObjects"]
    check("multi-LoD: original id B.1 written back", "B.1" in objs, str(list(objs)))
    lods = {g["lod"] for o in objs.values() for g in o.get("geometry") or ()}
    check("multi-LoD: LoD text 2.2 written back", lods == {"2.2"}, str(lods))
    ids = {s.get("id") for o in objs.values() for g in o.get("geometry") or ()
           for s in (g.get("semantics") or {}).get("surfaces") or ()}
    check("multi-LoD: original surface id W.B1 written back", "W.B1" in ids, str(ids))
    rec = last_export()
    check("multi-LoD: partly loaded scene says so in the summary",
          any("only the loaded geometry" in n for n in rec.get("notes", ())), str(rec.get("notes")))
    check("multi-LoD: crs as OGC URL", doc["metadata"].get("referenceSystem", "").endswith("/EPSG/0/25832"),
          str(doc.get("metadata")))


def gml_round_trip(out, src):
    name = os.path.basename(src).split(".")[0]
    clean()
    check(f"{name}: opens", bpy.ops.cgml3.import_gml_file(filepath=src) == {'FINISHED'})
    check(f"{name}: Save tab format defaults to CityGML", bpy.context.window_manager.cgml_export_format == 'CITYGML')
    a = os.path.join(out, f"{name}_a.gml")
    j = os.path.join(out, f"{name}.city.json")
    b = os.path.join(out, f"{name}_b.gml")
    ok = bpy.ops.cgml3.export_gml_file(filepath=a, citygml_version="3.0") == {'FINISHED'}
    ok = ok and export_cj(j) == {'FINISHED'}
    clean()
    ok = ok and bpy.ops.cgml3.import_gml_file(filepath=j) == {'FINISHED'}
    ok = ok and bpy.ops.cgml3.export_gml_file(filepath=b, citygml_version="3.0") == {'FINISHED'}
    check(f"{name}: GML -> scene -> CityJSON -> scene -> GML runs", ok)
    if ok:
        same(f"{name}: GML 3.0 -> CityJSON -> GML 3.0 fingerprint",
             fingerprint(read_gml(a), original_ids=False), fingerprint(read_gml(b), original_ids=False))
    return j if ok else None


def authored_b01(out):
    import test_b01_authored_building_export as b01
    clean()
    building = b01.build_cube_building()
    b01.select_only(building)
    bpy.context.scene.cgml3_modeltyper_props.assignment_mode = 'SELECTION'
    bpy.ops.cgml3_modeltyper.auto_assign()
    b01.select_face_in_edit_mode(building, b01.south_wall_face(building))
    bpy.ops.cgml.split_surfaces()
    if bpy.context.object and bpy.context.object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    wall = [o for o in building.children if o.get("cgml3_feature") == "WallSurface"][0]
    b01.select_face_in_edit_mode(wall, 0)
    res = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=wall.name, face_index=0)
    if wall.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    check("B01: drawn building with a window", res == {'FINISHED'})
    a, a2, j, b = (os.path.join(out, f"b01_{x}") for x in ("a.gml", "a2.gml", "cj.city.json", "b.gml"))
    ok = bpy.ops.cgml3.export_gml_file(filepath=a, citygml_version="3.0") == {'FINISHED'}
    ok = ok and bpy.ops.cgml3.export_gml_file(filepath=a2, citygml_version="3.0") == {'FINISHED'}
    res = export_cj(j)
    check("B01: CityJSON export finishes", res == {'FINISHED'} and os.path.isfile(j), str(res))
    if not os.path.isfile(j):
        return
    doc = cityjson_reader.load(j)
    geoms = [g for o in doc["CityObjects"].values() for g in o.get("geometry") or ()]
    sems = [s for g in geoms for s in (g.get("semantics") or {}).get("surfaces") or ()]
    check("B01: window written with its wall as parent",
          any(s["type"] == "Window" and sems[s["parent"]]["type"] == "WallSurface" for s in sems if "parent" in s)
          or any(s["type"] == "Window" and "parent" in s for s in sems), str(sems)[:400])
    holes = sum(1 for g in geoms for p in (g["boundaries"][0] if g["type"] == "Solid" else g["boundaries"]) if len(p) > 1)
    check("B01: the wall keeps the hole of the window (inner ring)", holes >= 1, str(geoms)[:300])
    clean()
    ok = ok and bpy.ops.cgml3.import_gml_file(filepath=j) == {'FINISHED'}
    check("B01: the CityJSON opens again", ok and any(o.get("cgml3_feature") == "Building" for o in bpy.data.objects))
    ok = ok and bpy.ops.cgml3.export_gml_file(filepath=b, citygml_version="3.0") == {'FINISHED'}
    if ok:
        fa, fa2, fb = (fingerprint(read_gml(p), original_ids=False) for p in (a, a2, b))
        # the GML writer mints a new id per export for faces still on the authored building's mesh
        # (ground, roof): two GML exports of the same scene differ there already, so those ids are not compared
        unstable = {r[0] for r in fa["relations"] - fa2["relations"]}
        known = {x for r in fa["relations"] for x in r[:2]} - unstable
        check("B01: only the unsplit ground/roof ids change between two GML exports", len(unstable) <= 2, str(unstable))
        for f in (fa, fb):
            f["relations"] = {tuple(x if x in known else "minted" for x in r[:2]) + (r[2],) for r in f["relations"]}
        same("B01: GML 3.0 -> CityJSON -> GML 3.0 fingerprint (minted ids aside)", fa, fb)


def guards(out):
    src = os.path.join(out, "guard_source.city.json")
    shutil.copy(MULTI, src)
    clean()
    bpy.ops.cgml3.import_gml_file(filepath=src)
    before = open(src, "rb").read()
    res = export_cj(src)
    check("guard: the scene's own CityJSON source is refused", res != {'FINISHED'}, str(res))
    check("guard: the source file is unchanged", open(src, "rb").read() == before)

    target = os.path.join(out, "guard_old.city.json")
    with open(target, "w", encoding="utf-8") as f:
        f.write("OLD")
    cw = sys.modules[f"{ADDON}.shared.cityjson_writer"]
    real = cw.validate_document
    cw.validate_document = lambda doc: ["forced problem"]
    try:
        res = export_cj(target)
    finally:
        cw.validate_document = real
    check("guard: a failed self-check cancels the export", res != {'FINISHED'}, str(res))
    check("guard: ... and leaves the old file as it was", open(target, encoding="utf-8").read() == "OLD")
    check("guard: ... and no temporary file is left", sorted(os.listdir(out)).count("guard_old.city.json") == 1
          and not any(n.startswith("guard_old.city.json.") for n in os.listdir(out)))

    clean()
    bpy.ops.cgml3.import_gml_file(filepath=RAILWAY)
    bpy.ops.cgml3.import_gml_file(filepath=FURNITURE)
    path = os.path.join(out, "railway_with_furniture.city.json")
    res = export_cj(path)
    rec = last_export()
    nw = rec.get("not_written", {})
    check("scope (CJ-07): CityFurniture is written, nothing counted as not written",
          res == {'FINISHED'} and not nw and not rec.get("reduced"), f"{res} {nw} {rec.get('notes')}")
    if os.path.isfile(path):
        types = {o["type"] for o in cityjson_reader.load(path)["CityObjects"].values()}
        check("scope (CJ-07): Building module objects and CityFurniture in the file",
              "CityFurniture" in types and types <= {"Building", "BuildingPart", "BuildingInstallation",
                                                     "CityFurniture"}, str(types))
    clean()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(TF, "coverage", "Transportation_citygml3.gml"))
    path = os.path.join(out, "track.city.json")
    res = export_cj(path)
    rec = last_export()
    check("scope: a Track (no CityJSON core type) is counted, not written",
          res == {'FINISHED'} and rec.get("not_written") == {"Track": 1}
          and any("1 Track" in n for n in rec.get("notes", ())), f"{res} {rec.get('not_written')} {rec.get('notes')}")
    if os.path.isfile(path):
        types = {o["type"] for o in cityjson_reader.load(path)["CityObjects"].values()}
        check("scope: no Track in the file", types == {"Road", "TransportSquare"}, str(types))

    from importlib import import_module
    api = import_module(f"{ADDON}.shared.agent_api")
    r = api.export(os.path.join(out, "agent.city.json"), format="cityjson")
    check("agent_api.export(format='cityjson')", r.get("ok") and r["export"]["format"] == "CityJSON", str(r)[:300])
    r = api.export(os.path.join(out, "agent.city.jsonl"), format="cityjson", seq=True)
    check("agent_api.export(format='cityjson', seq=True)", r.get("ok"), str(r)[:300])
    r = api.export(os.path.join(out, "agent.gml"), format="cityjson")
    check("agent_api: CityJSON to a .gml path is refused", not r.get("ok") and r.get("error_code") == "BAD_ARGUMENT", str(r)[:200])
    r = api.export(os.path.join(out, "agent_v3.gml"))
    check("agent_api: the CityGML default is unchanged", r.get("ok") and r["export"]["version"] == "3.0", str(r)[:200])


def writer_id_helpers():
    """K2 fix: ``ensure_xs_id`` / ``make_gml_id`` ask ``ids.is_ncname`` and leave a valid id alone."""
    from importlib import import_module
    v3, v2 = import_module(f"{ADDON}.io.writer.helpers"), import_module(f"{ADDON}.io_v2.writer.helpers")
    valid = ["B.1", "W.B1", "B.001", "NL.IMBAG.Pand.0503100000000010", "DEHH_x-1", "_a", "Gebäude.7"]
    check("ids: ensure_xs_id keeps a valid gml:id", [v3.ensure_xs_id(i) for i in valid] == valid,
          str([v3.ensure_xs_id(i) for i in valid]))
    for name, m in (("3.0", v3), ("2.0", v2)):
        got = [m.make_gml_id(i, set()) for i in valid]
        check(f"ids: make_gml_id ({name} writer) keeps a valid gml:id", got == valid, str(got))
    got = (v3.ensure_xs_id("1a b"), v3.ensure_xs_id("#a:b", "X"), v3.ensure_xs_id("").startswith("ID_"),
           v3.make_gml_id("1.a", set()), v2.make_gml_id("1.a", set()), v2.make_gml_id("a b", set()))
    check("ids: an invalid id is rewritten as before", got == ("ID_1a_b", "a_b", True, "ID_1.a", "ID_1_a", "a_b"), str(got))
    used = set()
    got = [v2.make_gml_id("B.1", used), v2.make_gml_id("B.1", used)]
    check("ids: a second use of the same id still gets a suffix", got == ["B.1", "B.1_2"], str(got))


def root_without_gml_id(out):
    """A feature root with no gml_id gets one ``ID_<uuid>``, kept on the object: never the object name
    (it changes on rename and gets Blender's ".001"), and the same id in every later export."""
    import re
    clean()
    bpy.ops.cgml3.import_gml_file(filepath=MULTI, cityjson_lod="2.2")
    root = next(o for o in bpy.data.objects if o.get("gml_id") == "B.1")
    del root["gml_id"]
    root.name = "Zzz.007"
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    written = []
    for i, v in enumerate(("3.0", "2.0", "3.0")):
        path = os.path.join(out, f"noid_{i}.gml")
        ok = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=v) == {'FINISHED'}
        text = open(path, encoding="utf-8").read() if ok else ""
        written.append(sorted(re.findall(r'<bldg:Building gml:id="([^"]+)"', text)))
    gid = str(root.get("gml_id") or "")
    check("no gml_id: the root got one ID_<uuid> and keeps it", re.fullmatch(r"ID_[0-9a-f]{32}", gid) is not None, gid)
    check("no gml_id: 3.0, 2.0 and a second 3.0 export write that id, not the object name",
          written == [sorted([gid, "B3"])] * 3 and "Zzz" not in str(written), str(written))
    check("no gml_id: a plain Blender object is left alone", "gml_id" not in cube, str(list(cube.keys())))


def main():
    global ADDON
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj03_")
    os.makedirs(out, exist_ok=True)
    ok, ADDON = ensure_addon_enabled()
    check("add-on enabled", ok, ADDON)
    writer_id_helpers()
    for f in ("buildings_lod2_cityjson_2_0.city.json", "buildings_lod3_cityjson_2_0.city.json"):
        cityjson_round_trip(out, os.path.join(SAMPLES, f))
    path = cityjson_round_trip(out, MULTI, lod="2.2")
    if path:
        multi_lod_specifics(path)
    for src in (os.path.join(SAMPLES, "buildings_lod2_citygml_3_0.gml"),
                os.path.join(SAMPLES, "buildings_lod3_citygml_3_0.gml"), RAILWAY,
                os.path.join(TF, "BuildingParts", "BuildingParts_citygml3.gml")):
        gml_round_trip(out, src)
    authored_b01(out)
    guards(out)
    root_without_gml_id(out)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
