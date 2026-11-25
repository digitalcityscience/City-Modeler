"""CJ-06: save into the source CityJSON file (shared/cityjson_merge.py). No Blender.

The "scene" output is the CJ-03 writer run on the reader's IR of the loaded buildings (what
``scene_to_ir`` gives for an unedited building), edited as JSON where a test needs a change."""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cj05_fixture as fx  # noqa: E402
from cj05_fixture import deref, same_objects, subtree, world_points  # noqa: E402
from shared import cityjson_merge as cm  # noqa: E402
from shared import cityjson_reader as cj  # noqa: E402
from shared import cityjson_writer as cw  # noqa: E402

N = 300
LOD = "2.2"


def written_for(doc, ids, lod=LOD):
    """Writer output of ``ids`` as loaded with ``lod`` (reader -> IR -> writer)."""
    objs = doc["CityObjects"]
    sub = dict(doc, CityObjects={o: objs[o] for r in ids for o in subtree(objs, r)})
    wdoc, _rep = cw.write_document(cj.read_document(sub, lod=lod), "", 3)
    return wdoc


def loaded(doc, ids, lod=LOD):
    return {o: {lod} for r in ids for o in subtree(doc["CityObjects"], r)}


def raise_roof(wdoc, rid, dz=1.0):
    """Move the roof of ``rid`` up by ``dz`` m in writer output (an edit in the scene)."""
    o = wdoc["CityObjects"][rid]
    g = next(g for g in o["geometry"] if g["lod"] == LOD)
    roof = [i for i, s in enumerate(g["semantics"]["surfaces"]) if s["type"] == "RoofSurface"]
    shell, vals = g["boundaries"][0], g["semantics"]["values"][0]
    idx = []
    for p, v in zip(shell, vals):
        if v in roof:
            cm._indices(p, idx)
    step = round(dz / wdoc["transform"]["scale"][2])
    for i in set(idx):
        wdoc["vertices"][i][2] += step


# -- 300 buildings, 2 changed -----------------------------------------------------------------------

def test_two_of_300_changed_rest_untouched():
    doc = fx.document(N)
    ids = ["B007", "B012"]
    wdoc = written_for(doc, ids)
    wdoc["CityObjects"]["B012"]["attributes"]["function"] = "31001_2000"
    raise_roof(wdoc, "B007")
    merged, rep = cm.merge_document(doc, wdoc, changed=ids, loaded_lods=loaded(doc, ids))
    assert rep["changed"] == ids and not rep["new"] and not rep["deleted"]
    untouched = [o for o in doc["CityObjects"] if o not in {i for r in ids for i in subtree(doc["CityObjects"], r)}]
    roots = [o for o in untouched if doc["CityObjects"][o]["type"] == "Building"]
    assert len(roots) == 298
    assert list(merged["CityObjects"]) == list(doc["CityObjects"])        # order kept, nothing lost
    assert same_objects(doc, merged, untouched) == []                     # JSON equal, coordinates exact
    # the changed buildings are as the "scene" has them
    assert merged["CityObjects"]["B012"]["attributes"]["function"] == "31001_2000"
    assert merged["CityObjects"]["B012"]["attributes"]["measuredHeight"] == 8.0   # equal value keeps its type
    top = max(p[2] for p in world_points(merged, merged["CityObjects"]["B007"], LOD))
    assert top == max(p[2] for p in world_points(doc, doc["CityObjects"]["B007"], LOD)) + 1000
    assert rep["orphans_removed"] >= fx.UNUSED_VERTICES
    assert cm.check_merged(merged) == []
    assert doc == fx.document(N)                                           # the original is not modified


def test_unloaded_lod12_of_a_changed_building_stays():
    doc = fx.document(N)
    wdoc = written_for(doc, ["B007"])
    raise_roof(wdoc, "B007")
    merged, rep = cm.merge_document(doc, wdoc, changed=["B007"], loaded_lods=loaded(doc, ["B007"]))
    old, new = doc["CityObjects"]["B007"], merged["CityObjects"]["B007"]
    assert [g["lod"] for g in new["geometry"]] == ["1.2", "2.2"]
    assert deref(merged, new)["geometry"][0] == deref(doc, old)["geometry"][0]
    assert rep["kept_lods"] == {"1.2": 1}
    # the part (MultiSurface LoD 2.2, loaded) is rewritten; the parent link holds
    assert merged["CityObjects"]["B007-P"]["parents"] == ["B007"] and new["children"] == ["B007-P"]


def test_two_geometries_with_the_loaded_text_only_the_first_is_replaced():
    doc = fx.document(20)
    o = doc["CityObjects"]["B002"]
    o["geometry"].append(copy.deepcopy(o["geometry"][1]))      # a second "2.2" Solid
    o["geometry"][2]["type"] = "Solid"
    wdoc = written_for(doc, ["B002"])                          # the reader reads the first one only
    raise_roof(wdoc, "B002")
    merged, _ = cm.merge_document(doc, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    geoms = deref(merged, merged["CityObjects"]["B002"])["geometry"]
    old = deref(doc, o)["geometry"]
    assert [g["lod"] for g in geoms] == ["1.2", "2.2", "2.2"]
    assert geoms[1] != old[1] and geoms[2] == old[2] and geoms[0] == old[0]


def test_bare_integer_lod_text_replaces_the_loaded_one():
    doc = fx.document(20)
    wdoc = written_for(doc, ["B002"])
    for g in wdoc["CityObjects"]["B002"]["geometry"]:
        g["lod"] = "2"
    merged, _ = cm.merge_document(doc, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    assert [g["lod"] for g in merged["CityObjects"]["B002"]["geometry"]] == ["1.2", "2.2"]


# -- new, deleted, children -------------------------------------------------------------------------

def test_new_and_deleted_buildings_with_children():
    doc = fx.document(N)
    wnew = written_for(doc, ["B002"])
    wnew["CityObjects"] = {"NEW1": wnew["CityObjects"]["B002"]}
    wnew["transform"]["translate"][0] += 5000.0                       # somewhere else
    merged, rep = cm.merge_document(doc, wnew, new=["NEW1"], deleted=["B007", "B005"])
    objs = merged["CityObjects"]
    assert "NEW1" in objs and list(objs)[-1] == "NEW1"
    assert not {"B007", "B007-P", "B005", "B005-R"} & set(objs)
    assert rep["deleted"] == ["B007", "B005"] and rep["removed_children"] == 2
    for oid, o in objs.items():
        for c in o.get("children") or ():
            assert c in objs and oid in objs[c]["parents"]
        for p in o.get("parents") or ():
            assert p in objs and oid in objs[p]["children"]
    assert cm.check_merged(merged) == []
    xs = {p[0] for p in world_points(merged, objs["NEW1"], LOD)}
    assert min(xs) >= (fx.TRANSLATE[0] + 5000.0) * 1000


def test_loaded_child_deleted_in_scene_goes_interior_and_unloaded_children_stay():
    doc = fx.document(20)
    # B011: installation loaded (LoD 2.2) and deleted in the scene; B005: the room was never read
    wdoc = written_for(doc, ["B011", "B005"])
    del wdoc["CityObjects"]["B011-I"]
    del wdoc["CityObjects"]["B011"]["children"]
    merged, rep = cm.merge_document(doc, wdoc, changed=["B011", "B005"], loaded_lods=loaded(doc, ["B011", "B005"]))
    objs = merged["CityObjects"]
    assert "B011-I" not in objs and "children" not in objs["B011"]
    assert objs["B005-R"] == {**doc["CityObjects"]["B005-R"], "geometry": objs["B005-R"]["geometry"]}
    assert deref(merged, objs["B005-R"]) == deref(doc, doc["CityObjects"]["B005-R"])
    assert objs["B005"]["children"] == ["B005-R"] and objs["B005-R"]["parents"] == ["B005"]
    assert rep["removed_children"] == 1


def test_new_child_of_a_changed_building_is_added():
    doc = fx.document(20)
    wdoc = written_for(doc, ["B011"])
    inst = copy.deepcopy(wdoc["CityObjects"]["B011-I"])
    wdoc["CityObjects"]["B011-I2"] = inst
    wdoc["CityObjects"]["B011"]["children"].append("B011-I2")
    merged, _ = cm.merge_document(doc, wdoc, changed=["B011"], loaded_lods=loaded(doc, ["B011"]))
    assert merged["CityObjects"]["B011"]["children"] == ["B011-I", "B011-I2"]
    assert cm.check_merged(merged) == []


def test_id_conflicts_are_refused():
    doc = fx.document(20)
    wdoc = written_for(doc, ["B002"])
    with pytest.raises(cm.MergeError, match="already has this id"):
        cm.merge_document(doc, wdoc, new=["B002"])
    with pytest.raises(cm.MergeError, match="not in the file"):
        cm.merge_document(doc, wdoc, deleted=["NOPE"])
    with pytest.raises(cm.MergeError, match="was not written"):
        cm.merge_document(doc, wdoc, changed=["B004"])
    v11 = dict(doc, version="1.1")
    with pytest.raises(cm.MergeError, match="1.1"):
        cm.merge_document(v11, wdoc, changed=["B002"])


# -- vertices ---------------------------------------------------------------------------------------

def test_no_orphan_vertex_and_indices_in_range():
    doc = fx.document(40)
    wdoc = written_for(doc, ["B007"])
    raise_roof(wdoc, "B007", 2.0)
    merged, rep = cm.merge_document(doc, wdoc, changed=["B007"], deleted=["B008"],
                                    loaded_lods=loaded(doc, ["B007"]))
    idx = [i for o in merged["CityObjects"].values() for i in cm._object_indices(o)]
    assert set(idx) == set(range(len(merged["vertices"])))
    assert cw.validate_document(dict(merged, CityObjects={k: v for k, v in merged["CityObjects"].items()})) == []
    assert merged["transform"] == doc["transform"]


def test_new_vertices_use_the_file_transform():
    doc = fx.document(5)
    doc["transform"] = {"scale": [0.01, 0.01, 0.01], "translate": [563999.5, 5933999.5, -3.0]}
    for v in doc["vertices"]:
        v[:] = [int(round((c * 0.001 + t0 - t1) / 0.01)) for c, t0, t1 in
                zip(v, fx.TRANSLATE, doc["transform"]["translate"])]
    wdoc = written_for(doc, ["B002"])
    merged, _ = cm.merge_document(doc, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    assert merged["transform"] == doc["transform"]
    assert world_points(merged, merged["CityObjects"]["B002"], LOD) == world_points(doc, doc["CityObjects"]["B002"], LOD)


# -- attributes, surfaces, what is lost ---------------------------------------------------------------

def test_unmapped_attributes_surface_extras_and_lost_appearance():
    doc = fx.document(20)
    o = doc["CityObjects"]["B012"]
    o["attributes"].update(tags=["a", "b"], note=None, owner={"name": "x"}, flag=True)
    g = o["geometry"][1]
    g["semantics"]["surfaces"][1] = {"type": "RoofSurface", "slope": 30}
    g["material"] = {"visual": {"values": [[0, 0, 0, 0, 0, 0]]}}
    o["geographicalExtent"] = [0, 0, 0, 1, 1, 1]
    wdoc = written_for(doc, ["B012"])
    del wdoc["CityObjects"]["B012"]["attributes"]["function"]       # removed in the scene
    merged, rep = cm.merge_document(doc, wdoc, changed=["B012"], loaded_lods=loaded(doc, ["B012"]))
    m = merged["CityObjects"]["B012"]
    a = m["attributes"]
    assert a["tags"] == ["a", "b"] and a["note"] is None and a["owner"] == {"name": "x"}
    assert a["flag"] is True and "function" not in a
    assert list(a)[:2] == ["measuredHeight", "tags"]                 # original key order
    roof = [s for g in m["geometry"] for s in (g.get("semantics") or {}).get("surfaces") or ()
            if s["type"] == "RoofSurface"]
    assert roof and roof[0]["slope"] == 30
    assert rep["preserved"]["surface attribute slope"] == 1
    assert rep["preserved"]["attribute tags (not read into the scene)"] == 1
    assert rep["lost"] == {"appearance (material/texture) of a replaced geometry": 1}
    assert m["geographicalExtent"] != [0, 0, 0, 1, 1, 1] and len(m["geographicalExtent"]) == 6


def test_scene_opened_before_dotted_ids_were_kept_still_carries_surface_extras():
    """K2 fix: such a scene has the root as mapped_id("B.001") and reader-minted surface ids built on it."""
    doc = fx.document(20)
    g = doc["CityObjects"]["B.001"]["geometry"][1]
    g["semantics"]["surfaces"][1] = {"type": "RoofSurface", "slope": 30}
    wdoc = written_for(doc, ["B.001"])
    new, old = "B.001_g1_s1", cj.mapped_id("B.001") + "_g1_s1"
    sems = [s for g in wdoc["CityObjects"]["B.001"]["geometry"] for s in g["semantics"]["surfaces"]]
    assert [s["id"] for s in sems if s["type"] == "RoofSurface"] == [new] and old.startswith("B_001_")
    for variant in (new, old):
        w = copy.deepcopy(wdoc)
        for g in w["CityObjects"]["B.001"]["geometry"]:
            for s in g["semantics"]["surfaces"]:
                s["id"] = variant if s["id"] == new else s["id"]
        merged, rep = cm.merge_document(doc, w, changed=["B.001"], loaded_lods=loaded(doc, ["B.001"]))
        roof = [s for g in merged["CityObjects"]["B.001"]["geometry"] for s in (g.get("semantics") or {}).get("surfaces") or ()
                if s["type"] == "RoofSurface"]
        assert roof[0]["slope"] == 30 and rep["preserved"]["surface attribute slope"] == 1 and not rep["lost"], variant


def test_merge_attributes_rules():
    orig = {"function": "1000", "measuredHeight": 12, "x": [1], "y": None, "b": True}
    scene = {"function": "2000", "measuredHeight": "12.0", "b": "true", "new": "n"}
    assert cm.merge_attributes(orig, scene) == {"function": "2000", "measuredHeight": 12, "x": [1], "y": None,
                                               "b": True, "new": "n"}


def test_address_kept_when_equal_else_from_scene_with_extras():
    pool = cm._Pool([[0, 0, 0], [1, 1, 1]], {"scale": [1, 1, 1], "translate": [0, 0, 0]})
    old = [{"ThoroughfareName": "A", "Locality": "H", "extraKey": 1, "location": {"type": "MultiPoint",
                                                                                 "lod": "1", "boundaries": [0]}}]
    same = [{"ThoroughfareName": "A", "Locality": "H", "location": {"type": "MultiPoint", "lod": "1",
                                                                    "boundaries": [0]}}]
    assert cm._merge_addresses(old, same, pool) is old
    other = [{"ThoroughfareName": "B", "Locality": "H"}]
    assert cm._merge_addresses(old, other, pool) == [{"ThoroughfareName": "B", "Locality": "H", "extraKey": 1}]


# -- files ------------------------------------------------------------------------------------------

def test_merge_file_single_document_and_dry_run(tmp_path):
    path = fx.write_document(tmp_path / "src.city.json", N)
    doc = cj.load(path)
    wdoc = written_for(doc, ["B007"])
    raise_roof(wdoc, "B007")
    before = Path(path).read_bytes()
    rep = cm.merge_file(path, wdoc, changed=["B007"], loaded_lods=loaded(doc, ["B007"]), dry_run=True)
    assert Path(path).read_bytes() == before and rep["dry_run"] and rep["changed"] == ["B007"]
    assert rep["untouched_buildings"] == 299 and rep["format"] == "CityJSON"
    st = cm.file_stat(path)
    rep = cm.merge_file(path, wdoc, changed=["B007"], loaded_lods=loaded(doc, ["B007"]), expected_stat=st)
    out = cj.load(path)
    assert same_objects(doc, out, [o for o in doc["CityObjects"] if not o.startswith("B007")]) == []
    assert rep["stat"] == list(cm.file_stat(path))
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_merge_file_seq_untouched_lines_byte_identical(tmp_path):
    path = fx.write_seq(tmp_path / "src.city.jsonl", 40, newline=b"\r\n", extra={3: b""})
    lines = Path(path).read_bytes().split(b"\r\n")
    feats = [json.loads(x) for x in lines[1:] if x.strip()]
    doc = cm._mini(json.loads(lines[0]))
    for f in feats:                       # one document with every feature, for the writer
        k = len(doc["vertices"])
        for oid, o in f["CityObjects"].items():
            doc["CityObjects"][oid] = dict(o, geometry=[dict(g, boundaries=cm._remap(g["boundaries"], lambda i: i + k))
                                                         for g in o["geometry"]])
        doc["vertices"] += f["vertices"]
    wdoc = written_for(doc, ["B007", "B002"])
    raise_roof(wdoc, "B007")
    wdoc["CityObjects"]["NEW1"] = wdoc["CityObjects"].pop("B002")
    rep = cm.merge_file(path, wdoc, changed=["B007"], new=["NEW1"], deleted=["B011"],
                        loaded_lods=loaded(doc, ["B007"]))
    after = Path(path).read_bytes().split(b"\r\n")
    assert after[0] == lines[0]
    changed = [x for x in after if b'"id":"B007"' in x]
    kept_before = [x for x in lines if b'"id":"B007"' not in x and b'"id":"B011"' not in x]
    assert [x for x in after if x in kept_before] == kept_before        # untouched lines byte for byte
    assert len(changed) == 1 and changed[0] not in lines
    assert b'"id":"NEW1"' in after[-2] and after[-1] == b""
    assert not any(b'"id":"B011"' in x for x in after)
    assert rep["format"] == "CityJSONSeq" and rep["untouched_buildings"] == 38
    f7 = json.loads(changed[0])
    assert cw.validate_feature(json.loads(lines[0]), f7) == []
    assert [g["lod"] for g in f7["CityObjects"]["B007"]["geometry"]] == ["1.2", "2.2"]


def test_broken_merge_is_caught_and_source_unchanged(tmp_path, monkeypatch):
    path = fx.write_document(tmp_path / "src.city.json", 20)
    doc = cj.load(path)
    wdoc = written_for(doc, ["B002"])
    wdoc["CityObjects"]["B002"]["geometry"][0]["semantics"]["values"][0][0] = 99   # out of range
    before = Path(path).read_bytes()
    with pytest.raises(cm.MergeError, match="self-check failed"):
        cm.merge_file(path, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    assert Path(path).read_bytes() == before
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]
    # a failure while writing leaves it too
    wdoc = written_for(doc, ["B002"])
    monkeypatch.setattr(cm.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        cm.merge_file(path, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    assert Path(path).read_bytes() == before
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_source_changed_on_disk_is_refused(tmp_path):
    path = fx.write_document(tmp_path / "src.city.json", 20)
    doc = cj.load(path)
    st = cm.file_stat(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(" ")
    before = Path(path).read_bytes()
    with pytest.raises(cm.MergeError, match="changed on disk"):
        cm.merge_file(path, written_for(doc, ["B002"]), changed=["B002"], expected_stat=st)
    assert Path(path).read_bytes() == before


def test_check_merged_keeps_other_geometry_types():
    doc = fx.document(3)
    doc["geometry-templates"] = {"templates": [{"type": "MultiSurface", "lod": "1", "boundaries": [[[0, 1, 2]]]}],
                                 "vertices-templates": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]}
    doc["CityObjects"]["F1"] = {"type": "CityFurniture", "geometry": [
        {"type": "GeometryInstance", "template": 0, "boundaries": [5], "transformationMatrix": [1, 0, 0, 0, 0, 1, 0, 0,
                                                                                                0, 0, 1, 0, 0, 0, 0, 1]}]}
    assert not [p for p in cm.check_merged(doc) if "F1" in p]
    doc["CityObjects"]["F1"]["geometry"][0]["template"] = 3
    assert any("template 3" in p for p in cm.check_merged(doc))
    wdoc = written_for(doc, ["B002"])
    merged, _ = cm.merge_document(doc, wdoc, changed=["B002"], loaded_lods=loaded(doc, ["B002"]))
    f1 = merged["CityObjects"]["F1"]["geometry"][0]
    assert merged["vertices"][f1["boundaries"][0]] == doc["vertices"][5]
