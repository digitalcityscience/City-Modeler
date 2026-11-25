"""CJ-05: Browse first for CityJSON without bpy - CityJSONSeq and single-document index, building unit,
LoD list, the small document of a selection, session origin, dispatch decision, cache."""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cj05_fixture as fx  # noqa: E402

from shared import cityjson_open as co  # noqa: E402
from shared import cityjson_reader as cj  # noqa: E402
from shared.browse import cityjson_index as cji  # noqa: E402
from shared.browse import index_cache  # noqa: E402
from shared.browse.browse_worker import decide_origin, run_browse_index  # noqa: E402
from shared.browse.gml_index import GmlIndexError, IndexCancelled  # noqa: E402
from shared.citygml_sniff import SniffSummary, sniff_file  # noqa: E402
from shared.import_origin import center_origin  # noqa: E402

SMALL = ROOT / "test_files" / "CityJSON" / "cj05_seq_small.city.jsonl"
N = 300


def key(e):
    return (e.gml_id, e.feature_type, e.name, e.bbox, e.lods, e.lod_texts, e.footprint, e.lod0)


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """The 300-building file in both forms, made here (nothing large lives in the repo)."""
    d = tmp_path_factory.mktemp("cj05")
    return fx.write_document(d / "b300.city.json", N), fx.write_seq(d / "b300.city.jsonl", N)


# -- CityJSONSeq index -----------------------------------------------------------------------------

@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_seq_byte_ranges(tmp_path, newline):
    path = fx.write_seq(tmp_path / "s.jsonl", 12, newline=newline)
    idx = cji.index_cityjson(path)
    data = Path(path).read_bytes()
    assert (idx.root_name, idx.version, len(idx.entries), idx.document) == ("CityJSONSeq", "2.0", 12, None)
    assert json.loads(data[:idx.root_open_end])["type"] == "CityJSON"
    assert data[idx.root_open_end:idx.root_open_end + len(newline)] == newline
    for i, e in enumerate(idx.entries):
        feature = json.loads(data[e.start:e.end])      # the range is exactly one feature line
        assert feature["type"] == "CityJSONFeature" and feature["id"] == fx.oid(i)
        assert e.gml_id == cj.gml_id_for(fx.oid(i))
        assert data[e.end:e.end + len(newline)] == newline and data[e.start - 1:e.start] == b"\n"
    assert idx.size == len(data) and idx.srs_name == fx.CRS


def test_seq_blank_and_broken_lines(tmp_path):
    broken = b'{"type":"CityJSONFeature","id":"X","CityObjects":{"X":{"ty'
    path = fx.write_seq(tmp_path / "s.jsonl", 12, extra={2: b"", 3: broken, 4: b"   ", 6: b"[1,2]"})
    idx = cji.index_cityjson(path)
    data = Path(path).read_bytes()
    assert [e.gml_id for e in idx.entries] == [cj.gml_id_for(fx.oid(i)) for i in range(12)]
    assert all(json.loads(data[e.start:e.end])["id"] == fx.oid(i) for i, e in enumerate(idx.entries))
    # blank lines are ignored; the cut-off line and the non-feature line are counted, the rest is indexed
    assert idx.skipped_kinds == {"unreadable line": 2, "WaterBody": 2, "BuildingRoom": 1}
    assert idx.skipped_members == 5


def test_seq_needs_its_header_line(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_bytes(fx.seq_lines(2)[1] + b"\n")
    with pytest.raises(GmlIndexError, match="first line"):
        cji.index_cityjson(str(path))


def test_small_seq_fixture_in_the_repo():
    idx = cji.index_cityjson(str(SMALL))
    assert (idx.root_name, len(idx.entries)) == ("CityJSONSeq", 6)
    assert idx.skipped_kinds == {"WaterBody": 2, "BuildingRoom": 1}
    assert SMALL.stat().st_size < 20_000


# -- single document -------------------------------------------------------------------------------

def test_document_index(big):
    idx = cji.index_cityjson(big[0])
    assert (idx.root_name, idx.version, len(idx.entries)) == ("CityJSON", "2.0", N)
    assert idx.document["type"] == "CityJSON" and len(idx.document["CityObjects"]) > N
    assert idx.skipped_kinds == {"WaterBody": 2, "BuildingRoom": 1} and idx.skipped_members == 3
    assert all(e.start == e.end == 0 and e.feature_type == "Building" for e in idx.entries)
    for i in (0, 1, 7, 11, 77, 299):
        assert idx.entries[i].bbox == pytest.approx(fx.bbox(i), abs=1e-6)
    assert idx.envelope == (tuple(fx.extent(N)[:3]), tuple(fx.extent(N)[3:]))
    # ids: what the builder gives the object - the file's own id, dotted or not (K2 fix)
    assert [(idx.entries[i].gml_id, idx.entries[i].name) for i in (1, 2)] == [("B.001", ""), ("B002", "")]


def test_invalid_id_is_mapped_and_shown_beside(tmp_path):
    path = fx.write_document(tmp_path / "bad.city.json", 4)
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    doc["CityObjects"] = {("2 B" if k == "B002" else k): v for k, v in doc["CityObjects"].items()}
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    idx = cji.index_cityjson(path)
    e = idx.entries[2]
    assert (e.gml_id, e.name) == (cj.gml_id_for("2 B"), "2 B") and e.gml_id != "2 B"
    sub, _ = cji.selection_document(idx, [2])
    assert list(sub["CityObjects"]) == ["2 B"]


def test_document_on_one_line(tmp_path):
    path = fx.write_document(tmp_path / "min.city.json", 4, indent=None)
    idx = cji.index_cityjson(path)
    assert (idx.root_name, len(idx.entries)) == ("CityJSON", 4) and idx.document is not None
    with open(path, "ab") as f:
        f.write(b"\n\n")
    assert cji.index_cityjson(path).root_name == "CityJSON"


@pytest.mark.parametrize("indent", [None, 1])
@pytest.mark.parametrize("vertex_slice", [1, 7, 1 << 18])
def test_stepwise_parse_equals_json_loads(indent, vertex_slice):
    doc = fx.document(9)
    # member order must not matter: vertices first, in the middle (as written) and last
    orders = [doc, {"vertices": doc["vertices"], **{k: v for k, v in doc.items() if k != "vertices"}},
              {"CityObjects": doc["CityObjects"], "type": "CityJSON", "vertices": [], "x": {"vertices": [[1]]}}]
    for d in orders:
        text = json.dumps(d, indent=indent)
        steps = []
        assert cji.load_stepwise(text, lambda done, total: steps.append(done), vertex_slice) == json.loads(text)
        assert steps == sorted(steps) and len(steps) >= len(d["CityObjects"])
    assert cji.load_stepwise(' {"vertices": [ ] , "CityObjects": { } } \n') == {"vertices": [], "CityObjects": {}}
    with pytest.raises(ValueError, match="expected an object"):
        cji.load_stepwise("[1, 2]")     # valid JSON, but not a document


@pytest.mark.parametrize("text", [
    '{"type": "CityJSON", "CityObjects": {"B": {"ty', '{"vertices": [[1,2,3],[4,5', '{"vertices": [[1,2,3],]}',
    '{"vertices": [[1,2,3] [4,5,6]]}', '{"a": 1} x', '{"a" 1}', '{"a": 1,}', '{"CityObjects": {"B": 1 "C": 2}}', ''])
def test_stepwise_parse_refuses_what_json_refuses(text):
    with pytest.raises(ValueError):
        json.loads(text)
    for vertex_slice in (1, 1 << 18):
        with pytest.raises(ValueError):
            cji.load_stepwise(text, None, vertex_slice)


def test_stepwise_parse_can_be_cancelled(big):
    def stop(done, total):
        if done > total / 2:
            raise IndexCancelled()
    with pytest.raises(IndexCancelled):
        cji.load_stepwise(Path(big[0]).read_text(encoding="utf-8"), stop)


def test_building_unit_parent_and_children(big):
    idx = cji.index_cityjson(big[0])
    ids = {e.gml_id for e in idx.entries}
    assert not any(i.endswith(("-P", "-I", "-R")) for i in ids)       # children come with their Building
    part, plain = idx.entries[7], idx.entries[8]
    assert part.bbox[3] - part.bbox[0] == pytest.approx(14.0) and plain.bbox[3] - plain.bbox[0] == pytest.approx(10.0)
    inst = idx.entries[11]                                                # installation on the roof
    assert inst.bbox[5] - inst.bbox[2] == pytest.approx(fx.size(11)[2] / 1000 + 1.0)
    # a BuildingPart whose parent is not in the file is a root, as the reader reads it
    doc = fx.document(8)
    del doc["CityObjects"]["B007"]
    path = Path(big[0]).parent / "orphan.city.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    orphan = cji.index_cityjson(str(path))
    assert [(e.gml_id, e.feature_type) for e in orphan.entries][-1] == ("B007-P", "BuildingPart")
    assert [r.gml_id for r in cj.read_document(doc)] == [e.gml_id for e in orphan.entries]


def test_lod_list(big):
    idx = cji.index_cityjson(big[0])
    assert cji.lod_texts(idx.entries) == ["1.2", "2.2"]
    assert co.lod_choices(cji.lod_texts(idx.entries)) == ["2.2", "1.2"]
    assert idx.entries[3].lod_texts == ("1.2",) and idx.entries[3].lod_levels == (1,)
    assert idx.entries[4].lod_texts == ("1.2", "2.2") and idx.entries[4].lod_levels == (1, 2)
    # outline: the ground surface of LoD 2.2; a building without semantics keeps its box
    assert len(idx.entries[4].footprint) == 1 and len(idx.entries[4].footprint[0]) == 8
    assert idx.entries[3].footprint == [] and idx.entries[7].footprint and len(idx.entries[7].footprint) == 2


def test_seq_and_document_give_the_same_index(big):
    doc, seq = cji.index_cityjson(big[0]), cji.index_cityjson(big[1])
    assert len(seq.entries) == N and [key(e) for e in seq.entries] == [key(e) for e in doc.entries]
    assert seq.skipped_kinds == doc.skipped_kinds and seq.envelope == doc.envelope
    assert all(e.end > e.start > 0 for e in seq.entries)


def test_index_counts_from_the_tree_not_from_tokens(tmp_path):
    # CJ-02 shortcut: the sniff scans tokens, so an attribute named "lod" or "type" counts too
    doc = fx.document(3)
    doc["CityObjects"]["B000"]["attributes"].update({"lod": "9.9", "type": "Building"})
    path = tmp_path / "a.city.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    sniffed = sniff_file(str(path))
    assert sniffed.features_estimate == 4 and "9.9" in sniffed.lods
    idx = cji.index_cityjson(str(path))
    assert len(idx.entries) == 3 and cji.lod_texts(idx.entries) == ["1.2", "2.2"]


def test_broken_building_keeps_its_place_without_a_box(tmp_path):
    doc = fx.document(3)
    doc["CityObjects"]["B002"]["geometry"][0]["boundaries"][0][0][0][0] = 10**9
    path = tmp_path / "x.city.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    idx = cji.index_cityjson(str(path))
    assert [e.bbox is None for e in idx.entries] == [False, False, True] and idx.entries[2].risky
    with pytest.raises(cj.CityJSONError, match="out of range"):
        cji.selection_document(idx, [2])


def test_document_above_the_limit_goes_to_3dcitydb(tmp_path):
    for indent in (1, None):
        path = fx.write_document(tmp_path / f"d{indent}.city.json", 6, indent=indent)
        with pytest.raises(GmlIndexError, match="citydb import cityjson") as exc:
            cji.index_cityjson(path, max_mb=0.002)
        assert "3DCityDB v5" in str(exc.value)
    seq = fx.write_seq(tmp_path / "s.jsonl", 6)        # lines are read one by one: no size limit
    assert len(cji.index_cityjson(seq, max_mb=0.002).entries) == 6
    assert os.path.getsize(seq) > 0.002 * 2**20


def test_not_cityjson(tmp_path):
    path = tmp_path / "g.json"
    path.write_text('{\n "type": "FeatureCollection", "features": []}', encoding="utf-8")
    with pytest.raises(GmlIndexError, match="Not a CityJSON"):
        cji.index_cityjson(str(path))
    path.write_text('{\n "type": "CityJSON", "version": "1.0"}', encoding="utf-8")
    with pytest.raises(GmlIndexError, match="1.0"):
        cji.index_cityjson(str(path))
    path.write_text('{\n "type": "CityJSON", ', encoding="utf-8")
    with pytest.raises(GmlIndexError, match="Not a readable"):
        cji.index_cityjson(str(path))


def test_cancel_and_progress(big):
    calls = []
    with pytest.raises(IndexCancelled):
        cji.index_cityjson(big[1], cancel=lambda: len(calls) >= 3 or calls.append(1), batch_features=10)
    with pytest.raises(IndexCancelled):
        cji.index_cityjson(big[0], cancel=lambda: True)
    seen = []
    cji.index_cityjson(big[1], progress=lambda done, total, n: seen.append((done, total, n)), batch_features=50)
    assert len(seen) >= 6 and seen[-1] == (os.path.getsize(big[1]), os.path.getsize(big[1]), N)
    assert [s[0] for s in seen] == sorted(s[0] for s in seen)


# -- the small document of a selection ---------------------------------------------------------------

def signature(roots):
    """Per building: every polygon as (owner id, surface type, coordinates in mm)."""
    out = {}

    def polys(node, owner, kind, acc):
        for g in node.geometries.values():
            for p in g.polygons:
                rings = [p.exterior] + list(p.interiors)
                acc.append((owner, kind, tuple(tuple(round(c * 1000) for c in pt) for r in rings for pt in r.coords)))

    def walk(node, acc):
        polys(node, node.gml_id, node.feature_type, acc)
        for b in node.boundaries:
            polys(b, b.gml_id, b.surface_type, acc)
            for o in b.openings:
                walk(o, acc)
        for s in node.spaces:
            walk(s, acc)

    for r in roots:
        acc = []
        walk(r, acc)
        out[r.gml_id] = sorted(acc)
    return out


PICKS = [1, 5, 7, 11, 44]       # dotted id (B.001), room child, part, installation, installation


def test_subdocument_of_a_document(big):
    idx = cji.index_cityjson(big[0])
    before = copy.deepcopy(idx.document)
    sub, bytes_read = cji.selection_document(idx, PICKS)
    assert bytes_read == 0 and idx.document == before                   # the source is not changed
    assert sub["transform"] == before["transform"] and sub["metadata"] == before["metadata"]
    assert (sub["type"], sub["version"]) == ("CityJSON", "2.0")
    assert sorted(sub["CityObjects"]) == sorted(["B.001", "B005", "B005-R", "B007", "B007-P", "B011", "B011-I",
                                                 "B044", "B044-I"])
    # only the vertices in use, re-indexed from 0: 8 per box, 9 boxes
    assert len(sub["vertices"]) == 72

    def indices(b):
        return [i for x in b for i in indices(x)] if isinstance(b, list) else [b]
    used = {i for o in sub["CityObjects"].values() for g in o["geometry"] for i in indices(g["boundaries"])}
    assert used == set(range(72))
    # coordinates: the same buildings read from the small document and from the whole file, to the mm
    skipped = []
    small = signature(cj.read_document(sub, skipped=skipped, lod="2.2"))
    whole = signature(cj.read_document(before, lod="2.2"))
    assert set(small) == {idx.entries[i].gml_id for i in PICKS}
    assert all(small[g] == whole[g] and len(small[g]) >= 6 for g in small)
    assert co.skipped_counts(skipped) == {"BuildingRoom": 1}            # the summary counts as in CJ-02
    assert co.unloaded_lods(sub, "2.2") == {"1.2": 5}


def test_subdocument_of_seq_lines(big):
    doc_idx, idx = cji.index_cityjson(big[0]), cji.index_cityjson(big[1])
    sub, bytes_read = cji.selection_document(idx, PICKS)
    assert bytes_read == idx.root_open_end + sum(idx.entries[i].end - idx.entries[i].start for i in PICKS)
    assert bytes_read < os.path.getsize(big[1]) / 20
    assert len(sub["vertices"]) == 72 and sub["transform"] == doc_idx.document["transform"]
    small = signature(cj.read_document(sub, lod="2.2"))
    whole = signature(cj.read_document(doc_idx.document, lod="2.2"))
    assert len(small) == 5 and all(small[g] == whole[g] for g in small)
    # each line has its own vertex list: indices of different lines must not be mixed up
    assert len({pt for sig in small.values() for _o, _k, pts in sig for pt in pts}) > 8 * 5


def test_seq_changed_after_indexing(tmp_path):
    path = fx.write_seq(tmp_path / "s.jsonl", 4)
    idx = cji.index_cityjson(path)
    with open(path, "ab") as f:
        f.write(b"\n")
    with pytest.raises(GmlIndexError, match="changed"):
        cji.selection_document(idx, [0])


def test_address_point_is_reindexed():
    header = {"type": "CityJSON", "version": "2.0", "transform": {"scale": [1, 1, 1], "translate": [0, 0, 0]}}
    objects = {"B": {"type": "Building", "address": [{"Country": "DE", "location": {
        "type": "MultiPoint", "lod": "1", "boundaries": [3]}}], "geometry": [
        {"type": "MultiSurface", "lod": "2", "boundaries": [[[2, 1, 0]]]}]}}
    sub = cji.build_subdocument(header, [(objects, [[0, 0, 0], [1, 0, 0], [0, 1, 0], [7, 7, 7], [9, 9, 9]])])
    assert sub["vertices"] == [[0, 1, 0], [1, 0, 0], [0, 0, 0], [7, 7, 7]]
    assert sub["CityObjects"]["B"]["address"][0]["location"]["boundaries"] == [3]
    assert sub["CityObjects"]["B"]["geometry"][0]["boundaries"] == [[[0, 1, 2]]]
    assert cj.read_document(sub)[0].attrs.addresses[0].multi_point == ((7.0, 7.0, 7.0),)


# -- session origin, worker, cache -----------------------------------------------------------------

def run(path, cache_dir, **kw):
    msgs = []
    idx = run_browse_index(path, msgs.append, indexer=cji.index_cityjson, cache_dir=str(cache_dir), **kw)
    return idx, msgs


def test_session_origin_from_the_file_extent(big, tmp_path):
    ext = fx.extent(N)
    expected = center_origin(ext[:3], ext[3:])
    for path in big:
        idx, msgs = run(path, tmp_path / "c", use_cache=False)
        assert [m[0] for m in msgs if m[0] == "origin"] == ["origin"] and msgs[0][0] == "origin"
        assert msgs[0][1:] == (expected, "2.0", fx.CRS)
        final = msgs[-1]
        boxes = sum(m[2].feature_count for m in msgs if m[0] == "batch") + (final[2].feature_count if final[2] else 0)
        assert final[0] == "final" and final[1] is idx and boxes == N
    # the extent of the file decides, not the buildings indexed first
    doc = fx.document(40)
    doc["metadata"]["geographicalExtent"] = [500000.0, 5900000.0, 3.0, 500400.0, 5900400.0, 50.0]
    path = tmp_path / "ext.city.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run(str(path), tmp_path / "c", use_cache=False)[1][0][1] == (500200.0, 5900200.0, 3.0)


def test_session_origin_from_the_whole_index_without_extent(tmp_path):
    for write, name in ((fx.write_document, "n.city.json"), (fx.write_seq, "n.city.jsonl")):
        path = write(tmp_path / name, N, with_extent=False)
        idx, msgs = run(path, tmp_path / "c", use_cache=False)
        # no boxes before the whole file is indexed: the origin is computed once, from all of it
        assert [m[0] for m in msgs] == ["origin", "final"]
        ext = fx.extent(N)
        assert msgs[0][1] == decide_origin(None, idx.entries) == center_origin(ext[:3], ext[3:])
        assert msgs[1][2].feature_count == N
        first = cji.index_cityjson(path).entries[:5]
        assert decide_origin(None, first) != msgs[0][1]


def test_cache_key_and_round_trip(big, tmp_path):
    cdir = tmp_path / "cache"
    seq = fx.write_seq(tmp_path / "c.city.jsonl", 40)
    idx, msgs = run(seq, cdir)
    assert msgs[-1][3] is False and os.path.isfile(index_cache.cache_file(seq, str(cdir)))
    again, msgs2 = run(seq, cdir)
    assert msgs2[-1][3] is True and [m[0] for m in msgs2] == ["origin", "final"] and msgs2[0][1] == msgs[0][1]
    assert [key(e) + (e.start, e.end) for e in again.entries] == [key(e) + (e.start, e.end) for e in idx.entries]
    assert (again.root_name, again.root_open_end, again.skipped_kinds) == ("CityJSONSeq", idx.root_open_end,
                                                                           idx.skipped_kinds)
    assert cji.selection_document(again, [0])[0]["CityObjects"].keys() == {"B000", "B000-P", "B000-I"}
    old_key = index_cache.cache_key(seq)
    assert old_key == index_cache.cache_key(seq)
    with open(seq, "ab") as f:
        f.write(fx.seq_lines(41)[-3] + b"\n")
    assert index_cache.cache_key(seq) != old_key and index_cache.load(seq, str(cdir)) is None
    assert run(seq, cdir)[1][-1][3] is False
    # a single document is parsed for Load anyway: its index is not cached
    doc_dir = tmp_path / "doc_cache"
    idx, msgs = run(big[0], doc_dir)
    assert idx.document is not None and not os.path.isdir(doc_dir)


# -- dispatch ---------------------------------------------------------------------------------------

def summary(fmt="CityJSON", n=3, mb=1.0, exact=True):
    return SniffSummary("x", size=int(mb * 2**20), is_citygml=fmt == "CityGML", features_estimate=n,
                        exact=exact, format=fmt)


def test_dispatch_decision():
    assert co.open_decision(summary(n=30)) == ("cityjson", "")                  # at the threshold: as in CJ-02
    assert co.open_decision(summary(n=31)) == ("browse", "31 buildings: Browse first above 30")
    assert co.open_decision(summary(n=31), threshold_features=40) == ("cityjson", "")
    assert co.open_decision(summary(n=5000))[0] == "browse"                     # Browse first has no count limit
    assert co.open_decision(summary("CityJSONSeq", n=1))[0] == "browse"         # always, whatever the size
    assert co.open_decision(summary("CityJSONSeq", n=10**6, mb=5000.0))[0] == "browse"
    kind, text = co.open_decision(summary(n=3, mb=200.5))
    assert kind == "error" and "citydb import cityjson" in text and "3DCityDB v5" in text
    assert co.open_decision(summary(n=300, mb=200.0))[0] == "browse"
    assert co.open_decision(summary("CityGML", n=300)) == ("citygml", "")


def test_load_all_limits():
    assert co.load_all_refusal(summary(n=300)) == ""
    assert co.load_all_refusal(summary(n=2000, mb=200.0)) == ""
    assert co.load_all_refusal(summary(n=2001)) == "2,001 features, limit 2,000"
    assert co.load_all_refusal(summary(n=2100, exact=False)) == "~2,100 features, limit 2,000"
    assert co.load_all_refusal(summary(mb=200.5)) == "200 MB, limit 200 MB"
    assert co.load_all_refusal(summary("CityGML", n=2001)) == "2,001 features, limit 2,000"
    assert "Browse first only" in co.load_all_refusal(summary("CityJSONSeq", n=1))


# -- the file changed after it was indexed (Save into source, CJ-06) ---------------------------------

def test_load_after_the_file_changed_reads_the_file_not_the_kept_document(tmp_path):
    path = fx.write_document(tmp_path / "b20.city.json", 20)
    idx = cji.index_cityjson(path)
    a, b = idx.entries[3].gml_id, idx.entries[7].gml_id
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    doc["CityObjects"][a].setdefault("attributes", {})["saved"] = "after the index"
    del doc["CityObjects"][b]                      # no children in this fixture row
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    sub, _n = cji.selection_document(idx, [3])
    assert sub["CityObjects"][a]["attributes"]["saved"] == "after the index"
    assert cji.selection_document(idx, [3])[0] == sub and idx.document == doc   # read once, then kept
    with pytest.raises(GmlIndexError, match="browse it again"):
        cji.selection_document(idx, [7])            # gone from the file
