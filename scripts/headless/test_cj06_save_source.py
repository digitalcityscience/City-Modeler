"""CJ-06: save into the source CityJSON file (cgml3.save_cityjson_source, agent_api.save_cityjson_source).

For a 300-building CityJSON document and the same buildings as CityJSONSeq (scripts/tests/cj05_fixture.py):
- Browse first, 5 buildings loaded with LoD 2.2; a window added to one (agent_api.add_opening), an
  attribute changed on another, one unloaded;
- the dry run names the 2 changed buildings and writes nothing; the save writes them back;
- the file opened again: the other 298 buildings as they were (JSON, 1 mm), the unloaded one still there,
  the LoD 1.2 geometry of the changed building as it was, the 2 changed buildings as the scene has them
  (scripts/tests/cj_fingerprint.py); a CityJSONSeq line not touched is byte for byte the same;
- a second save with no change leaves the file; a deleted building goes with its children, a building
  drawn from scratch is added; a source changed on disk is refused; Export CityJSON to the source is
  refused and names the new button.

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj06_save_source.py
"""
import json
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)   # pure modules as top-level ``shared``

import bpy  # noqa: E402

import cj05_fixture as fx  # noqa: E402
from addon_test_utils import ensure_addon_enabled  # noqa: E402
from cj_fingerprint import diff, fingerprint  # noqa: E402
from shared import cityjson_merge as cm  # noqa: E402
from shared import cityjson_reader  # noqa: E402
from cj05_fixture import deref, subtree, world_points  # noqa: E402

FAILURES = []
ADDON = None
N = 300
PICKS = ["B.001", "B007", "B011", "B012", "B044"]   # the file's ids are the gml_ids, dotted or not
EDITED = ["B007", "B012"]


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else f" - {detail}"))
    if not cond:
        FAILURES.append(label)


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")


def call(op, **kw):
    try:
        return op(**kw), ""
    except RuntimeError as exc:
        return {'CANCELLED'}, str(exc)


def api():
    return import_module(f"{ADDON}.shared.agent_api")


def last_export():
    return json.loads(bpy.context.scene.get("cgml_last_export", "{}"))


def load_doc(path):
    """The file as one document (CityJSONSeq lines joined, vertices shifted)."""
    if not path.endswith(".jsonl"):
        return cityjson_reader.load(path)
    lines = [json.loads(x) for x in open(path, "rb").read().splitlines() if x.strip()]
    doc = dict(lines[0], CityObjects={}, vertices=[])
    for f in lines[1:]:
        k = len(doc["vertices"])
        for oid, o in f["CityObjects"].items():
            doc["CityObjects"][oid] = dict(o, geometry=[dict(g, boundaries=cm._remap(g["boundaries"], lambda i: i + k))
                                                         for g in o.get("geometry") or ()])
        doc["vertices"] += f["vertices"]
    return doc


def roots_of(doc):
    return [o for o, v in doc["CityObjects"].items() if v["type"] == "Building" and not v.get("parents")]


def untouched_same(before, after, skip):
    ids = [o for o in before["CityObjects"] if o not in {i for r in skip for i in subtree(before["CityObjects"], r)}]
    return [i for i in ids if i not in after["CityObjects"] or deref(before, before["CityObjects"][i])
            != deref(after, after["CityObjects"][i])], len([i for i in ids if i in roots_of(before)])


def same_fp(label, a, b, allow=("con:height:uom",)):
    d = diff(a, b)
    props = d.pop("props", {})
    bad = {k: sorted(p for p in set(map(tuple, x or ())) ^ set(map(tuple, y or ())) if p[0] not in allow)
           for k, (x, y) in props.items()}
    bad = {k: v for k, v in bad.items() if v}
    if bad:
        d["props"] = bad
    check(label, not d, json.dumps(d, default=str)[:1500])


def root(gid):
    return mod("shared.export_scope").top_level_feature_objects(bpy.context.scene).get(gid)


# --------------------------------------------------------------------------- the case
def case(label, path):
    print(f"\n[CJ-06] --- {label} ---")
    fresh_scene()
    bm = mod("ops.browse_gml")
    res, _ = call(bpy.ops.cgml3.browse_gml_file, filepath=path, use_cache=False)
    s = bm.get_session(path)
    check(f"{label}: indexed", res == {'FINISHED'} and s is not None and s.complete, str(res))
    bpy.ops.cgml3.browse_select(action='SET', ids=",".join(PICKS))
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    check(f"{label}: 5 buildings loaded", res == {'FINISHED'} and all(root(g) for g in PICKS), err)
    original_bytes = open(path, "rb").read()
    original = load_doc(path)

    # nothing edited yet: nothing to save, the file stays
    r = api().save_cityjson_source(dry_run=True)
    ex = r.get("export") or {}
    check(f"{label}: right after loading every building is the same as in the file",
          r.get("ok") and ex.get("same_count") == 5 and not ex.get("changed") and not ex.get("new")
          and not ex.get("deleted"), str(r)[:400])

    # edits: a window on B007, an attribute on B012; B044 unloaded
    b = api().inspect("B007")["buildings"][0]
    wall = next(su for su in b["surfaces"] if su["type"] == "WallSurface")
    r = api().add_opening(wall["gml_id"], wall["faces"][0]["index"], 0.5, 0.5, 1.2, 1.4)
    check(f"{label}: window added to B007 (agent_api)", r.get("ok"), str(r)[:300])
    root("B012")["bldg:function"] = "31001_2000"
    bpy.ops.cgml3.browse_select(action='SET', ids="B044")
    res, err = call(bpy.ops.cgml3.browse_unload)
    check(f"{label}: B044 unloaded", res == {'FINISHED'} and root("B044") is None, err)

    r = api().save_cityjson_source(dry_run=True)
    ex = r.get("export") or {}
    check(f"{label}: dry run: B007 and B012 changed, nothing new or deleted (unloaded is not deleted)",
          r.get("ok") and sorted(ex.get("changed", [])) == EDITED and ex.get("new") == [] and ex.get("deleted") == [],
          str(r)[:500])
    check(f"{label}: dry run writes nothing", open(path, "rb").read() == original_bytes)

    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    check(f"{label}: saved into the source", res == {'FINISHED'}, err)
    rec = last_export()
    check(f"{label}: record (format, counts, kept, self-check, cjval not run)",
          rec.get("format") == "CityJSON (source)" and rec.get("changed_count") == 2 and rec.get("new_count") == 0
          and rec.get("deleted_count") == 0 and rec.get("untouched_buildings") == N - 2
          and rec.get("kept_not_loaded") == N - 4 and rec.get("kept_lods") == {"1.2": 2}
          and rec.get("lost") == {} and rec.get("self_check") == "passed" and rec.get("cjval") == "not run",
          json.dumps(rec)[:600])

    after = load_doc(path)
    bad, n = untouched_same(original, after, EDITED)
    check(f"{label}: the other {n} buildings (and all their objects) are as they were", not bad and n == N - 2,
          f"{len(bad)} differ: {bad[:5]}")
    check(f"{label}: unloaded B044 is still in the file", "B044" in after["CityObjects"])
    lod12 = [g for g in after["CityObjects"]["B007"]["geometry"] if g["lod"] == "1.2"]
    check(f"{label}: B007 keeps its LoD 1.2 geometry as it was",
          len(lod12) == 1 and world_points(after, after["CityObjects"]["B007"], "1.2")
          == world_points(original, original["CityObjects"]["B007"], "1.2"))
    sems = [s["type"] for g in after["CityObjects"]["B007"]["geometry"]
            for s in (g.get("semantics") or {}).get("surfaces") or ()]
    check(f"{label}: B007 has the window in the file", "Window" in sems, str(sems))
    check(f"{label}: B012 function changed in the file",
          after["CityObjects"]["B012"]["attributes"].get("function") == "31001_2000",
          str(after["CityObjects"]["B012"].get("attributes")))
    check(f"{label}: the file passes the merge self-check", cm.check_merged(after) == [], str(cm.check_merged(after))[:300])
    scene_roots, _ = mod("ops.export_cityjson").scene_to_ir(bpy.context, "IDS", EDITED)
    file_roots = [r for r in cityjson_reader.read_document(after, lod="2.2") if r.gml_id in EDITED]
    same_fp(f"{label}: B007 and B012 in the file are as in the scene", fingerprint(scene_roots), fingerprint(file_roots))
    if path.endswith(".jsonl"):
        old_lines = [x for x in original_bytes.splitlines() if b'"id":"B007"' not in x and b'"id":"B012"' not in x]
        new_lines = open(path, "rb").read().splitlines()
        check(f"{label}: every line not touched is byte for byte the same",
              [x for x in new_lines if x in set(old_lines)] == old_lines, f"{len(old_lines)} lines")

    # again, nothing changed since: the file is not touched
    before = open(path, "rb").read()
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    check(f"{label}: a second save without changes leaves the file",
          res == {'FINISHED'} and open(path, "rb").read() == before
          and "no changes" in " ".join(last_export().get("notes", ())), err)

    # delete B011 (with its installation), add a drawn building
    r = api().delete_feature("B011")
    check(f"{label}: B011 deleted in the scene", r.get("ok"), str(r)[:200])
    import test_b01_authored_building_export as b01
    new = b01.build_cube_building()
    b01.select_only(new)
    bpy.context.scene.cgml3_modeltyper_props.assignment_mode = 'SELECTION'
    bpy.ops.cgml3_modeltyper.auto_assign()
    new_id = str(new.get("gml_id") or "")
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    rec = last_export()
    check(f"{label}: delete + new saved", res == {'FINISHED'} and rec.get("deleted") == ["B011"]
          and rec.get("new") == [new_id] and new_id, f"{err} {json.dumps(rec)[:400]}")
    after2 = load_doc(path)
    objs = after2["CityObjects"]
    check(f"{label}: B011 and its installation are gone", "B011" not in objs and "B011-I" not in objs)
    check(f"{label}: the drawn building is in the file", objs.get(new_id, {}).get("type") == "Building", new_id)
    sym = all(c in objs and o in objs[c].get("parents", ()) for o, v in objs.items() for c in v.get("children") or ())
    check(f"{label}: parents / children agree", sym)
    bad, n = untouched_same(original, after2, EDITED + ["B011"])
    check(f"{label}: the other {N - 3} buildings still as they were", not bad and n == N - 3, str(bad[:5]))
    check(f"{label}: 300 buildings in the file (300 - 1 + 1)", len(roots_of(after2)) == N, str(len(roots_of(after2))))

    # refused: the source changed on disk, Export CityJSON over the source
    with open(path, "ab") as f:
        f.write(b"\n" if path.endswith(".jsonl") else b" ")
    root("B012")["bldg:function"] = "31001_3000"
    changed_disk = open(path, "rb").read()
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    check(f"{label}: a source changed on disk is refused, file unchanged",
          res != {'FINISHED'} and "changed on disk" in err and open(path, "rb").read() == changed_disk, err)
    try:
        res = bpy.ops.cgml3.export_cityjson_file(filepath=path, sequence=path.endswith(".jsonl"))
        err = ""
    except RuntimeError as e:
        res, err = {'CANCELLED'}, str(e)
    check(f"{label}: Export CityJSON over the source is refused and names Save into Source File",
          res != {'FINISHED'} and "Save into Source File" in err and open(path, "rb").read() == changed_disk, err)


def direct_open(out):
    """A small file opened directly (no Browse first), LoD 2.2: B.1 changes; B2 (no LoD 2.2,
    not loaded), the room of B3 and the WaterBody stay as they are."""
    import shutil
    print("\n[CJ-06] --- direct open ---")
    path = os.path.join(out, "cj06_multi_lod.city.json")
    shutil.copy(os.path.join(ROOT, "test_files", "CityJSON", "cj02_multi_lod.city.json"), path)
    fresh_scene()
    check("direct: opens", bpy.ops.cgml3.import_gml_file(filepath=path, cityjson_lod="2.2") == {'FINISHED'})
    original = load_doc(path)
    obj = root("B.1")
    check("direct: the root carries its source file", obj is not None
          and os.path.normcase(str(obj.get("cgml3_cityjson_source"))) == os.path.normcase(os.path.abspath(path)))
    obj["bldg:function"] = "31001_2000"
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    rec = last_export()
    check("direct: saved, B.1 changed, B3 the same", res == {'FINISHED'} and rec.get("changed") == ["B.1"]
          and rec.get("same_count") == 1, f"{err} {json.dumps(rec)[:400]}")
    after = load_doc(path)
    bad, n = untouched_same(original, after, ["B.1"])
    check("direct: B2, B3 with its room and the WaterBody are as they were", not bad and n == 2, str(bad))
    b1 = after["CityObjects"].get("B.1", {})
    check("direct: B.1 keeps its id, LoD 1.2 and roofType; function changed",
          [g["lod"] for g in b1.get("geometry", ())] == ["1.2", "2.2"]
          and b1.get("attributes", {}).get("function") == "31001_2000"
          and b1.get("attributes", {}).get("roofType") == "1000", str(b1.get("attributes")))


def legacy_scene(out):
    """A scene opened before dotted ids were kept (K2 fix): the root's gml_id is the mapped id, the file's
    own id is in cgml3_cityjson_id / cgml3_cityjson_surface_ids. Save into the source and the CityJSON
    export still write "B.1" and "W.B1"."""
    import re
    import shutil
    print("\n[CJ-06] --- a scene opened before the K2 fix (mapped B.1) ---")
    path = os.path.join(out, "cj06_legacy.city.json")
    shutil.copy(os.path.join(ROOT, "test_files", "CityJSON", "cj02_multi_lod.city.json"), path)
    fresh_scene()
    rd = mod("shared.cityjson_reader")
    now, ascii_only = rd.gml_id_for, re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
    rd.gml_id_for = lambda i: i if ascii_only.match(i) else rd.mapped_id(i)   # the reader as it was
    try:
        ok = bpy.ops.cgml3.import_gml_file(filepath=path, cityjson_lod="2.2") == {'FINISHED'}
    finally:
        rd.gml_id_for = now
    gid = rd.mapped_id("B.1")
    obj = root(gid)
    check("legacy: the scene is as the old reader left it (mapped gml_id, original in cgml3_cityjson_id)",
          ok and gid == "B_1_349009fa" and obj is not None and obj.get("cgml3_cityjson_id") == "B.1"
          and "W.B1" in json.loads(obj.get("cgml3_cityjson_surface_ids", "{}")).values() and root("B.1") is None,
          str(sorted(mod("shared.export_scope").top_level_feature_objects(bpy.context.scene))))
    if obj is None:
        return
    original = load_doc(path)
    obj["bldg:function"] = "31001_2000"
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    rec = last_export()
    check("legacy: saved into the source as B.1, B3 the same", res == {'FINISHED'} and rec.get("changed") == ["B.1"]
          and rec.get("same_count") == 1, f"{err} {json.dumps(rec)[:400]}")
    after = load_doc(path)

    def surface_ids(doc):
        return sorted(s["id"] for g in doc["CityObjects"].get("B.1", {}).get("geometry", ())
                      for s in (g.get("semantics") or {}).get("surfaces") or () if "id" in s)

    check("legacy: the file keeps its ids (B.1, W.B1), the mapped id is written nowhere",
          list(after["CityObjects"]) == list(original["CityObjects"]) and surface_ids(after) == surface_ids(original)
          and "W.B1" in surface_ids(after) and "349009fa" not in json.dumps(after)
          and after["CityObjects"]["B.1"]["attributes"].get("function") == "31001_2000",
          f"{list(after['CityObjects'])} {surface_ids(after)}")
    exp = os.path.join(out, "cj06_legacy_export.city.json")
    res, err = call(bpy.ops.cgml3.export_cityjson_file, filepath=exp)
    doc = load_doc(exp) if res == {'FINISHED'} else {"CityObjects": {}}
    check("legacy: CityJSON export writes B.1 and W.B1, not the mapped id",
          "B.1" in doc["CityObjects"] and "W.B1" in surface_ids(doc) and "349009fa" not in json.dumps(doc),
          f"{err} {list(doc['CityObjects'])} {surface_ids(doc)}")


def main():
    global ADDON
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj06_")
    os.makedirs(out, exist_ok=True)
    ok, ADDON = ensure_addon_enabled()
    check("add-on enabled", ok, ADDON)
    case("CityJSON", fx.write_document(os.path.join(out, "cj06_300.city.json"), N))
    case("CityJSONSeq", fx.write_seq(os.path.join(out, "cj06_300.city.jsonl"), N))
    direct_open(out)
    legacy_scene(out)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
