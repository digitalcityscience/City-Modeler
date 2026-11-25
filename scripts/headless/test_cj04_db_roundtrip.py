"""CJ-04: a building opened from CityJSON goes to the local 3DCityDB v5 through the B84 commit and comes back.

Scratch schemas ``cj04_<pid>_a`` .. ``_d`` (created here, dropped at the end, also on failure).

The ids of the file are the ids everywhere (K2 fix): "B.1", "W.B1", "B.001" are valid gml:ids, so they are
the scene gml_id, the gml:id of the CityGML 3.0 / 2.0 export (XSD checked), the DB objectid, the id after the
DB Browse load, the id of the CityJSON export and of Save into Source File.

- a (multi-LoD file, B.1 / W.B1): open LoD 2.2 -> New -> commit dialog -> Same; an edit is Changed in
  the DB *and* in the source file; save into the source leaves the DB state, a commit leaves the source state;
  scene cleared -> DB Browse load -> CityJSON export: fingerprint (scripts/tests/cj_fingerprint.py) against the
  export made right after opening, the file's own ids on both sides; only core:creationDate may differ.
  What the DB loses of the CityJSON bookkeeping (LoD text) is counted.
- b (300 buildings, Browse first, 5 loaded): two working copies of one building. After the commit the file
  browse still knows its loaded buildings; Unload in the file browse or in the DB browse is a delete in
  neither; save into the source and commit do not disturb each other's state; DB round trip of the 3 left.
- c (plan section 10): the same file already in the DB through ``citydb import cityjson``: citydb-tool
  writes objectid B.1, our commit sees B.1 and B3 as conflicts and opens no second row.
- d (plan section 7, independent comparison, Building only): our CityJSON export -> ``citydb import cityjson``
  -> ``citydb export citygml`` against our own CityGML 3.0 export of the same scene.

The numbers for the report (K2 loss, objectids, CRS) are printed as one ``[CJ-04] MEASURE`` line.

Usage (factory-startup, throwaway profile as scripts/headless/run_regression.sh, psycopg on PYTHONPATH,
password only in the environment):
  CITYDB_PASSWORD=... blender -b --factory-startup --python-use-system-env --python-exit-code 1 \
      --python scripts/headless/test_cj04_db_roundtrip.py -- <out_dir> <citydb_compose_dir> [--keep-schema]
"""
import json
import os
import re
import shutil
import subprocess
import sys
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
from shared import cityjson_reader as cjr  # noqa: E402
from shared.citygml_reader import read_document as read_gml  # noqa: E402

FAILURES = []
MEASURE = {}
ADDON = None
CONTAINER = "citydb-v5-dev"
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"
SCHEMAS = tuple(f"cj04_{os.getpid()}_{c}" for c in "abcd")   # this run's own schemas
EXTENT = (563900.0, 5933900.0, 565100.0, 5934700.0)
MULTI = os.path.join(ROOT, "test_files", "CityJSON", "cj02_multi_lod.city.json")
B1 = "B.1"
PICKS = ["B.001", "B007", "B011", "B012", "B044"]
COMPOSE = ""


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else f" - {detail}"))
    if not cond:
        FAILURES.append(label)
    return cond


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


def _run(cmd, env=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    return _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])[1].strip()


def schema_sql(schema, script):
    return _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                 "-v", f"schema_name={schema}", "-f", script])


def tool(schema, folder, *argv):
    """``citydb <argv>`` against ``schema``; ``folder`` is mounted as /cj04."""
    env = dict(os.environ, CITYDB_SCHEMA=schema, MSYS_NO_PATHCONV="1")
    rc, out = _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "-v", f"{os.path.abspath(folder)}:/cj04",
                    "citydb-tool", *argv], env=env, cwd=COMPOSE)
    if rc != 0 or "successfully completed" not in out:
        raise RuntimeError(f"citydb {' '.join(argv)} failed: {out[-800:]}")
    return out


def checksum(schema):
    return psql(
        f"SELECT (SELECT count(*) FROM {schema}.feature) || ' ' || md5("
        f" coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {schema}.feature t), '')"
        f" || coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {schema}.property t), ''))")


def building_rows(schema):
    """[(objectid, active)] of the Building rows."""
    out = psql(f"SELECT f.objectid, f.termination_date IS NULL FROM {schema}.feature f "
               f"JOIN {schema}.objectclass oc ON oc.id = f.objectclass_id "
               f"WHERE oc.classname = 'Building' ORDER BY f.id")
    return [(a, b == "t") for a, b in (line.split("|") for line in out.splitlines() if line)]


def active_ids(schema):
    return sorted(o for o, a in building_rows(schema) if a)


# --------------------------------------------------------------------------- scene helpers
def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False


def set_target(schema):
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("CJ04 local") or prefs.db_profiles.add()
    p.name, p.host, p.port, p.user = "CJ04 local", HOST, PORT, USER
    prefs.db_active_profile = p.name
    mod("citydb.credentials")._session[p.server().key] = SECRET
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", schema
    sc.db_target_label, sc.db_target_srid = f"CJ04 {schema}", 25832
    return bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)


def call(op, *a, **kw):
    try:
        return op(*a, **kw), ""
    except RuntimeError as exc:
        return {'CANCELLED'}, str(exc)


def roots():
    return mod("shared.export_scope").top_level_feature_objects(bpy.context.scene)


def kinds():
    cc = mod("ops.commit_citydb")
    cc._cache.clear()
    return {c.objectid: c.kind for c in cc.scan(bpy.context)[1]}


def cj_state(path=None):
    """Save into the source, dry run: {gml_id: changed / new / deleted / same}."""
    r = import_module(f"{ADDON}.shared.agent_api").save_cityjson_source(path, dry_run=True)
    ex = r.get("export") or {}
    ids = {v: k for k, v in mod("ops.export_cityjson").source_changes(bpy.context, path or ex["path"])["ids"].items()}
    out = {}
    for k in ("changed", "new", "deleted"):
        out.update({ids.get(i, i): k for i in ex.get(k, ())})
    return out, ex


def same_count(ex):
    return ex.get("same_count")


def commit_dialog(schema, label, expect):
    """The commit dialog's path: the list its invoke shows, nothing written by listing, then Commit with the
    dialog's checkboxes. (``blender -b`` runs a props dialog's execute at once, so the dialog itself is
    checked in the live Blender.)"""
    cc, ch = mod("ops.commit_citydb"), mod("citydb.changes")
    before = checksum(schema)
    cc._cache.clear()
    todo = ch.pending(cc.scan(bpy.context)[1])
    check(f"{label}: the dialog lists {expect}", {c.objectid: c.kind for c in todo} == expect, str(todo))
    items = bpy.context.window_manager.cgml3_db_commit_items
    items.clear()
    for c in todo:
        item = items.add()
        item.objectid, item.kind = c.objectid, c.kind
    check(f"{label}: nothing written before Commit is pressed", checksum(schema) == before)
    res, err = call(bpy.ops.cgml3.db_commit, from_dialog=True)
    s = mod("ops.browse_citydb").active_session(bpy.context)
    check(f"{label}: Commit finishes", res == {'FINISHED'}, f"{err} {s.last_update.get('message') if s else ''}")


def db_load(objectids, mode='LOD2'):
    bc = mod("ops.browse_citydb")
    bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
    s = bc.active_session(bpy.context)
    fids = [str(r.id) for r in s.rows if r.objectid in objectids]
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(fids))
    res, err = call(bpy.ops.cgml3.db_browse_load, mode=mode)
    return res == {'FINISHED'} and all(roots().get(o) for o in objectids), err


def export_cj(path, gml_ids=None):
    kw = dict(scope='IDS', scope_gml_ids=",".join(gml_ids)) if gml_ids else {}
    res, err = call(bpy.ops.cgml3.export_cityjson_file, filepath=path, **kw)
    if res != {'FINISHED'}:
        raise RuntimeError(f"CityJSON export {path}: {err}")
    return cjr.load(path)


def export_gml(path, version):
    """``(gml:ids written, XSD result, error)`` of a CityGML export with the XSD check on."""
    sc = bpy.context.scene
    sc.cgml3.xsd_validate = True
    try:
        res, err = call(bpy.ops.cgml3.export_gml_file, filepath=path, citygml_version=version)
    finally:
        sc.cgml3.xsd_validate = False
    rec = mod("shared.export_scope").last_export(sc) or {}
    if res != {'FINISHED'}:
        return set(), rec.get("xsd"), err
    with open(path, encoding="utf-8") as f:
        text = f.read()
    ids = set(re.findall(r'gml:id="([^"]+)"', text))
    dangling = sorted(set(re.findall(r'xlink:href="#([^"]+)"', text)) - ids)
    return ids, rec.get("xsd"), f"dangling xlink:href {dangling[:3]}" if dangling else ""


def gml_keeps(label, out, expect):
    """CityGML 3.0 and 2.0 export: ``expect`` are gml:ids as they stand, no broken xlink, XSD valid."""
    for v in ("3.0", "2.0"):
        ids, xsd, err = export_gml(os.path.join(out, f"{label.split()[0]}_{len(MEASURE.get('xsd', ()))}_{v[0]}.gml"), v)
        MEASURE.setdefault("xsd", {})[f"{label} {v}"] = xsd
        check(f"{label}: CityGML {v} export keeps {sorted(expect)}, XSD valid",
              set(expect) <= ids and xsd == "valid" and not err, f"missing {sorted(set(expect) - ids)} xsd={xsd} {err}")


def surface_objectids(schema, ids):
    return psql(f"SELECT coalesce(string_agg(objectid, ',' ORDER BY objectid), '') FROM {schema}.feature "
                f"WHERE objectid IN ({','.join(repr(i) for i in ids)})").split(",")


def bookkeeping(gml_ids):
    """How many roots / surfaces still carry what only the CityJSON open knows (K2 ids, LoD text)."""
    out = {"roots": 0, "cgml3_cityjson_id": 0, "cgml3_cityjson_surface_ids": 0, "cgml3_cityjson_lods": 0,
           "cgml3_cityjson_source": 0}
    for gid in gml_ids:
        r = roots().get(gid)
        if r is None:
            continue
        out["roots"] += 1
        for k in [k for k in out if k != "roots"]:
            out[k] += int(any(o.get(k) for o in (r, *r.children_recursive)))
    return out


def file_facts(doc):
    """CityJSON ids, LoD texts and semantic surface ids of the Buildings in ``doc``."""
    objs = doc["CityObjects"]
    blds = sorted(o for o, v in objs.items() if v["type"] in ("Building", "BuildingPart"))
    lods = sorted({str(g.get("lod")) for o in blds for g in objs[o].get("geometry") or ()})
    sids = sorted(s["id"] for o in blds for g in objs[o].get("geometry") or ()
                  for s in (g.get("semantics") or {}).get("surfaces") or () if "id" in s)
    attrs = sorted({k for o in blds for k in (objs[o].get("attributes") or {})})
    return {"ids": blds, "lods": lods, "surface_ids": sids, "attributes": attrs}


def k2_loss(before, after):
    """What the DB round trip changed of the file's own ids (must be nothing) and LoD texts (plan K2 / K6)."""
    a, b = file_facts(before), file_facts(after)
    return {"buildings": len(a["ids"]), "building_ids_changed": sorted(set(a["ids"]) - set(b["ids"])),
            "surfaces_with_id": len(a["surface_ids"]),
            "surface_ids_changed": sorted(set(a["surface_ids"]) - set(b["surface_ids"])),
            "lod_texts": [a["lods"], b["lods"]], "attributes_added": sorted(set(b["attributes"]) - set(a["attributes"]))}


def fp_of(doc, lod=None, original_ids=True):
    return fingerprint(cjr.read_document(doc, lod=lod), original_ids=original_ids)


DB_ADDED = ("core:creationDate",)   # citydb-tool stamps it on import, the DB browse brings it back


def _without_db_added(fp):
    return dict(fp, props={k: [p for p in v if p[0] not in DB_ADDED] for k, v in fp["props"].items()})


def compare(label, ref_doc, got_doc, lod=None):
    """Ids (the file's own), geometry, classes, rings, 1 mm coordinates, relations, attribute card: the only
    difference the DB may make is its creation date. Returns the whole diff, for the report."""
    a, b = fp_of(ref_doc, lod), fp_of(got_doc)
    d = diff(a, _without_db_added(b))
    check(f"{label}: ids, surface classes, rings, 1 mm coordinates, relations, attribute card as before the DB",
          not d, json.dumps(d, default=str)[:1500])
    loss = k2_loss(ref_doc, got_doc)
    check(f"{label}: no building or surface id changed (K2 loss 0)",
          not loss["building_ids_changed"] and not loss["surface_ids_changed"], str(loss))
    return diff(a, b)


# --------------------------------------------------------------------------- a: multi-LoD file
def case_a(out):
    schema = SCHEMAS[0]
    print(f"\n[CJ-04] --- a: multi-LoD file, B.1 / W.B1 ({schema}) ---")
    fresh_scene()
    path = os.path.join(out, "cj04_multi_lod.city.json")
    shutil.copy(MULTI, path)
    check("a: opens with LoD 2.2", call(bpy.ops.cgml3.import_gml_file, filepath=path, cityjson_lod="2.2")[0]
          == {'FINISHED'})
    gr = mod("shared.georef")
    MEASURE["d_world_crs"] = str(gr.world().get(gr.CRS))
    MEASURE["d_schema_srid"] = psql(f"SELECT srid FROM {schema}.database_srs")
    check("a: the scene gml_ids are the file's ids, nothing mapped",
          sorted(roots()) == ["B.1", "B3"] and bookkeeping(["B.1", "B3"])["cgml3_cityjson_id"] == 0
          and bookkeeping(["B.1", "B3"])["cgml3_cityjson_surface_ids"] == 0,
          f"{sorted(roots())} {bookkeeping(['B.1', 'B3'])}")
    gml_keeps("a", out, ["B.1", "W.B1", "B3", "W.B3"])
    ref = export_cj(os.path.join(out, "a_ref.city.json"))
    check("a: the CityJSON export has the file's ids", {"B.1", "B3"} <= set(file_facts(ref)["ids"])
          and {"W.B1", "W.B3"} <= set(file_facts(ref)["surface_ids"]), str(file_facts(ref)))
    res, err = call(set_target, schema)
    check("a: city model picked (empty schema)", res == {'FINISHED'}, err)
    check("a: CityJSON buildings not in the DB are New", kinds() == {B1: "new", "B3": "new"}, str(kinds()))
    check("a: right after opening the source file has nothing to save", cj_state()[0] == {}, str(cj_state()))

    commit_dialog(schema, "a", {B1: "new", "B3": "new"})
    MEASURE["k2_objectids_in_db"] = active_ids(schema)
    check("a: in the DB under the file's own objectid (B.1, surface W.B1)",
          active_ids(schema) == ["B.1", "B3"] and surface_objectids(schema, ["W.B1", "W.B3"]) == ["W.B1", "W.B3"],
          f"{building_rows(schema)} {surface_objectids(schema, ['W.B1', 'W.B3'])}")
    check("a: after the commit both are Same", kinds() == {B1: "synced", "B3": "synced"}, str(kinds()))
    state, ex = cj_state()
    check("a: after the commit the source file still says same (dry run)", state == {} and same_count(ex) == 2,
          str(ex)[:300])

    roots()["B3"]["bldg:function"] = "31001_2000"
    check("a: an edit is Changed for the DB", kinds() == {B1: "synced", "B3": "changed"}, str(kinds()))
    check("a: ... and for the source file", cj_state()[0] == {"B3": "changed"}, str(cj_state()[0]))
    before = checksum(schema)
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    check("a: saved into the source", res == {'FINISHED'}, err)
    check("a: save into the source writes nothing to the DB and leaves its state",
          checksum(schema) == before and kinds() == {B1: "synced", "B3": "changed"}, str(kinds()))
    src, saved = file_facts(cjr.load(MULTI)), file_facts(cjr.load(path))
    check("a: Save into Source File keeps every id of the file",
          (saved["ids"], saved["surface_ids"]) == (src["ids"], src["surface_ids"]) and "B.1" in saved["ids"]
          and "W.B1" in saved["surface_ids"], str(saved))
    commit_dialog(schema, "a (edit)", {"B3": "changed"})
    check("a: after the second commit both Same", kinds() == {B1: "synced", "B3": "synced"}, str(kinds()))
    check("a: the commit leaves the source file state (same)", cj_state()[0] == {}, str(cj_state()[0]))
    ref = export_cj(os.path.join(out, "a_ref2.city.json"))   # the state that is now in the DB

    print("\n[CJ-04] --- a: scene cleared, DB Browse load, CityJSON export ---")
    fresh_scene()
    res, err = call(set_target, schema)
    ok, err = db_load([B1, "B3"])
    check("a: B.1 and B3 loaded from the DB under the same gml_id", ok and sorted(roots()) == ["B.1", "B3"],
          f"{err} {sorted(roots())}")
    gml_keeps("a (from the DB)", out, ["B.1", "W.B1", "B3", "W.B3"])
    check("a: loaded from the DB: Same", kinds() == {B1: "synced", "B3": "synced"}, str(kinds()))
    MEASURE["b_bookkeeping_after_db"] = bookkeeping([B1, "B3"])
    got = export_cj(os.path.join(out, "a_from_db.city.json"))
    MEASURE["b_k2_loss_multi_lod"] = k2_loss(ref, got)
    MEASURE["b_diff_original_ids"] = json.loads(json.dumps(compare("a: DB round trip", ref, got), default=str))
    before = checksum(schema)
    for gid in (B1, "B3"):
        bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=str(roots()[gid]["cgml3_db_feature_id"]))
        bpy.ops.cgml3.db_browse_unload(all=False)
    check("a: DB Unload is not a delete", kinds() == {} and checksum(schema) == before, str(kinds()))


# --------------------------------------------------------------------------- b: two working copies
def case_b(out):
    schema = SCHEMAS[1]
    print(f"\n[CJ-04] --- b: 300 buildings, Browse first, 5 loaded ({schema}) ---")
    fresh_scene()
    bm = mod("ops.browse_gml")
    path = fx.write_document(os.path.join(out, "cj04_300.city.json"), 300)
    res, err = call(bpy.ops.cgml3.browse_gml_file, filepath=path, use_cache=False)
    s = bm.get_session(path)
    check("b: indexed", res == {'FINISHED'} and s is not None and s.complete, err)
    bpy.ops.cgml3.browse_select(action='SET', ids=",".join(PICKS))
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    check("b: 5 buildings loaded", res == {'FINISHED'} and all(roots().get(g) for g in PICKS), err)
    res, err = call(set_target, schema)
    check("b: 5 New", kinds() == {g: "new" for g in PICKS}, str(kinds()))
    gml_keeps("b", out, PICKS)
    commit_dialog(schema, "b", {g: "new" for g in PICKS})
    check("b: in the DB under the file's own objectids (B.001)", active_ids(schema) == sorted(PICKS),
          str(building_rows(schema)))
    check("b: 5 Same after the commit", kinds() == {g: "synced" for g in PICKS}, str(kinds()))
    MEASURE["a_source_kind_after_commit"] = sorted({str(roots()[g].get("cgml3_source_kind")) for g in PICKS})
    bm._refresh_loaded(s)
    check("b: the file browse still knows its 5 loaded buildings after the commit",
          sorted(s.entries[i].gml_id for i in s.loaded) == sorted(PICKS), str(sorted(s.loaded)))
    state, ex = cj_state()
    check("b: the source file says same for the 5", state == {} and same_count(ex) == 5, str(ex)[:300])

    before = checksum(schema)
    bpy.ops.cgml3.browse_select(action='SET', ids="B044")
    res, err = call(bpy.ops.cgml3.browse_unload)
    check("b: file Unload of B044 removes it from the scene", res == {'FINISHED'} and roots().get("B044") is None,
          err)
    bc = mod("ops.browse_citydb")
    fid = roots()["B011"]["cgml3_db_feature_id"]
    bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
    bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=str(fid))
    res, err = call(bpy.ops.cgml3.db_browse_unload, all=False)
    check("b: DB Unload of B011 removes it from the scene", res == {'FINISHED'} and roots().get("B011") is None,
          err)
    left = [g for g in PICKS if g not in ("B044", "B011")]
    check("b: unloaded (file or DB) is not a delete for the DB", kinds() == {g: "synced" for g in left},
          str(kinds()))
    state, ex = cj_state()
    check("b: ... nor for the source file", state == {} and same_count(ex) == 3, str(ex)[:300])
    check("b: unloading writes nothing", checksum(schema) == before)
    dbs = bc.active_session(bpy.context)
    db_loaded = sorted(dbs.rows[i].objectid for i in dbs.loaded)
    check("b: the DB boxes show B011 and B044 as not loaded", db_loaded == sorted(left), str(db_loaded))
    bm._refresh_loaded(s)
    file_loaded = sorted(s.entries[i].gml_id for i in s.loaded)
    check("b: the file boxes show B011 and B044 as not loaded", file_loaded == sorted(left), str(file_loaded))

    roots()["B007"]["bldg:function"] = "31001_2000"
    check("b: edit -> Changed in both", kinds().get("B007") == "changed" and cj_state()[0] == {"B007": "changed"},
          f"{kinds()} {cj_state()[0]}")
    res, err = call(bpy.ops.cgml3.save_cityjson_source, filepath=path)
    check("b: save into the source, DB state unchanged",
          res == {'FINISHED'} and kinds().get("B007") == "changed" and checksum(schema) == before, f"{err} {kinds()}")
    commit_dialog(schema, "b (edit)", {"B007": "changed"})
    check("b: after the commit: Same in both", kinds() == {g: "synced" for g in left} and cj_state()[0] == {},
          f"{kinds()} {cj_state()[0]}")
    ref = export_cj(os.path.join(out, "b_ref.city.json"), left)
    # Load of loaded buildings replaces them from the file (T08); committed ones must be seen as loaded
    bpy.ops.cgml3.browse_select(action='SET', ids=",".join(left))
    res, err = call(bpy.ops.cgml3.browse_load, lod="2.2")
    twice = {g: sum(1 for o in bpy.data.objects if o.get("gml_id") == g and o.parent is None) for g in left}
    check("b: Load of the committed buildings does not build them twice", set(twice.values()) == {1}, str(twice))
    MEASURE["a_reload_after_commit"] = {"db_kinds": kinds(), "B007_function_scene": roots()["B007"].get("bldg:function"),
                                        "B007_function_file": cjr.load(path)["CityObjects"]["B007"]["attributes"]
                                        .get("function")}

    print("\n[CJ-04] --- b: scene cleared, DB Browse load of the 3, CityJSON export ---")
    fresh_scene()
    call(set_target, schema)
    ok, err = db_load(left)
    check("b: the 3 loaded from the DB under the same gml_id (B.001)", ok and sorted(roots()) == sorted(left),
          f"{err} {sorted(roots())}")
    MEASURE["b_bookkeeping_after_db_300"] = bookkeeping(left)
    got = export_cj(os.path.join(out, "b_from_db.city.json"))
    MEASURE["b_k2_loss_300"] = k2_loss(ref, got)
    MEASURE["b_diff_original_ids_300"] = json.loads(json.dumps(compare("b: DB round trip", ref, got), default=str))


# --------------------------------------------------------------------------- c: the same file imported by citydb-tool
def case_c(out):
    schema = SCHEMAS[2]
    print(f"\n[CJ-04] --- c: the source already imported with citydb import cityjson ({schema}) ---")
    path = os.path.join(out, "cj04_twin.city.json")
    shutil.copy(MULTI, path)
    tool(schema, out, "import", "cityjson", "/cj04/cj04_twin.city.json")
    MEASURE["c_objectids_citydb_import_cityjson"] = building_rows(schema)
    fresh_scene()
    call(bpy.ops.cgml3.import_gml_file, filepath=path, cityjson_lod="2.2")
    call(set_target, schema)
    MEASURE["c_kinds_before_commit"] = kinds()
    before = checksum(schema)
    res, err = call(bpy.ops.cgml3.db_commit)
    s = mod("ops.browse_citydb").active_session(bpy.context)
    conflicts = sorted((s.last_update or {}).get("conflicts", {}))
    MEASURE["c_commit_all"] = {"result": sorted(res), "conflicts": conflicts, "written": checksum(schema) != before,
                               "message": s.last_update.get("message") if s else err}
    check("c: citydb import cityjson wrote objectid B.1", ("B.1", True) in MEASURE["c_objectids_citydb_import_cityjson"],
          str(MEASURE["c_objectids_citydb_import_cityjson"]))
    check("c: B.1 and B3 (same ids in the DB) are conflicts, nothing written",
          {"B.1", "B3"} <= set(conflicts) and checksum(schema) == before, str(MEASURE["c_commit_all"]))
    res, err = call(bpy.ops.cgml3.db_commit, objectids=B1)
    rows = building_rows(schema)
    conflicts = sorted((mod("ops.browse_citydb").active_session(bpy.context).last_update or {}).get("conflicts", {}))
    MEASURE["c_commit_B1"] = {"result": sorted(res), "rows_after": rows, "conflicts": conflicts,
                              "rows_for_building_B.1": [o for o, a in rows if o.startswith(("B.1", "B_1"))]}
    check("c: committing B.1 alone is a conflict too: no second row",
          "B.1" in conflicts and checksum(schema) == before
          and MEASURE["c_commit_B1"]["rows_for_building_B.1"] == ["B.1"], str(MEASURE["c_commit_B1"]))


# --------------------------------------------------------------------------- d: independent comparison
def case_d(out):
    schema = SCHEMAS[3]
    print(f"\n[CJ-04] --- d: independent comparison through citydb-tool ({schema}) ---")
    fresh_scene()
    path = os.path.join(out, "cj04_ind_src.city.json")
    shutil.copy(MULTI, path)
    call(bpy.ops.cgml3.import_gml_file, filepath=path, cityjson_lod="2.2")
    export_cj(os.path.join(out, "d_ours.city.json"))
    res, err = call(bpy.ops.cgml3.export_gml_file, filepath=os.path.join(out, "d_ours_3.gml"), citygml_version='3.0')
    check("d: our CityGML 3.0 export", res == {'FINISHED'}, err)
    tool(schema, out, "import", "cityjson", "/cj04/d_ours.city.json")
    MEASURE["d_objectids"] = building_rows(schema)
    tool(schema, out, "export", "citygml", "-o", "/cj04/d_citydb_3.gml")
    try:
        from lxml import etree as ET
    except ImportError:
        import xml.etree.ElementTree as ET
    ours = read_gml(ET.parse(os.path.join(out, "d_ours_3.gml")).getroot(), upgrade=False)
    theirs = read_gml(ET.parse(os.path.join(out, "d_citydb_3.gml")).getroot(), upgrade=False)

    # citydb-tool keeps the CityJSON ids and so do we: the ids are compared as they are
    d = diff(fingerprint(ours), _without_db_added(fingerprint(theirs)))
    MEASURE["d_independent_diff"] = json.loads(json.dumps(d, default=str))
    check("d: our CityJSON through citydb-tool = our CityGML 3.0 (ids, classes, rings, 1 mm, relations, card)", not d,
          json.dumps(d, default=str)[:1500])


def main():
    global COMPOSE
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out, COMPOSE = os.path.abspath(argv[0]), argv[1]
    os.makedirs(out, exist_ok=True)
    if not SECRET:
        print("Set CITYDB_PASSWORD")
        sys.exit(2)
    ok, name = ensure_addon_enabled()
    check("add-on enabled", ok, name)
    for s in SCHEMAS:
        schema_sql(s, "drop-schema.sql")
        rc, txt = schema_sql(s, "create-schema.sql")
        if rc != 0:
            raise RuntimeError(txt[-400:])
    try:
        for case in (case_a, case_b, case_c, case_d):
            try:
                case(out)
            except Exception as exc:   # one broken case must not hide the others
                import traceback
                traceback.print_exc()
                check(f"{case.__name__} ran to the end", False, repr(exc))
    finally:
        if "--keep-schema" not in argv:
            for s in SCHEMAS:
                schema_sql(s, "drop-schema.sql")
        print("[CJ-04] MEASURE " + json.dumps(MEASURE, default=str, sort_keys=True))
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
