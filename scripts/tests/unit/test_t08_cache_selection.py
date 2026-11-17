"""T08: index cache (key and invalidation), selection limit, LoD plan,
proxy arrays, sniff summary and the bpy-free worker."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t08_fixtures as fx  # noqa: E402

from shared.browse import index_cache  # noqa: E402
from shared.browse.browse_worker import decide_origin, run_browse_index  # noqa: E402
from shared.browse.gml_index import FeatureIndexEntry, index_file  # noqa: E402
from shared.browse.proxy_geometry import (  # noqa: E402
    PREVIEW_BBOX,
    PREVIEW_LOD0,
    STATE_INDEXED,
    STATE_RISK,
    ProxyArrays,
    build_proxy_arrays,
    outline_edges,
    preview_label,
)
from shared.browse.selection import (  # noqa: E402
    features_in_rect,
    limit_message,
    lod_summary,
    plan_load,
)
from shared.citygml_sniff import browse_recommended, sniff_file  # noqa: E402


# --------------------------------------------------------------------------- cache
def test_cache_round_trip_and_key_stability(tmp_path):
    path = fx.write(tmp_path, "a.gml", fx.citygml30())
    cdir = str(tmp_path / "cache")
    assert index_cache.load(path, cdir) is None
    idx = index_file(path)
    target = index_cache.save(idx, cdir)
    assert target and os.path.isfile(target)
    assert index_cache.cache_key(path) == index_cache.cache_key(path)
    back = index_cache.load(path, cdir)
    assert back is not None and back.to_dict() == idx.to_dict()


def test_cache_invalidated_when_file_changes(tmp_path):
    path = fx.write(tmp_path, "a.gml", fx.citygml30())
    cdir = str(tmp_path / "cache")
    index_cache.save(index_file(path), cdir)
    key = index_cache.cache_key(path)
    # same size, new content and mtime
    data = Path(path).read_bytes().replace(b"Bee", b"Bea")
    Path(path).write_bytes(data)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert index_cache.cache_key(path) != key
    assert index_cache.load(path, cdir) is None


def test_cache_rejects_mismatching_stat_even_with_same_key(tmp_path, monkeypatch):
    path = fx.write(tmp_path, "a.gml", fx.citygml30())
    cdir = str(tmp_path / "cache")
    idx = index_file(path)
    idx.size += 1  # stale entry written under the current key
    index_cache.save(idx, cdir)
    assert index_cache.load(path, cdir) is None


def test_cache_prune_keeps_newest(tmp_path):
    cdir = tmp_path / "cache"
    cdir.mkdir()
    for i in range(4):
        p = cdir / f"{i}{index_cache.SUFFIX}"
        p.write_bytes(b"x" * 400_000)
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    removed = index_cache.prune(1.0, str(cdir))  # 1 MiB: two files fit
    assert removed == 2
    assert sorted(p.name for p in cdir.iterdir()) == [f"2{index_cache.SUFFIX}", f"3{index_cache.SUFFIX}"]
    assert index_cache.clear(str(cdir)) == 2


def test_default_cache_dir_uses_localappdata(monkeypatch):
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", r"C:\X")
        assert index_cache.default_cache_dir() == os.path.join(r"C:\X", "city_modeler", "index")


# --------------------------------------------------------------------------- selection
def test_selection_limit():
    assert limit_message(10, 10) is None
    assert limit_message(11, 10) == "Select at most 10 buildings (preferences)"
    assert limit_message(2, 1) == "Select at most 1 building (preferences)"
    assert limit_message(0, 10) == "Select at least one building first"


def _e(gid, lods, bbox=(0, 0, 0, 1, 1, 1), **kw):
    return FeatureIndexEntry(gid, "Building", bbox=bbox, lods=tuple(lods), **kw)


def test_plan_load_and_lod_summary():
    ents = {0: _e("a", ["lod2Solid"]), 1: _e("b", ["lod1Solid"]),
            2: _e("c", ["lod2MultiSurface", "lod3MultiSurface", "lod2TerrainIntersection"])}
    assert plan_load(ents, "LOD2") == ({2: [0, 2]}, [1])
    assert plan_load(ents, "HIGHEST") == ({2: [0], 1: [1], 3: [2]}, [])
    assert lod_summary([ents[0], ents[2]]) == ((2,), (2, 3))
    with pytest.raises(ValueError):
        plan_load(ents, "LOD9")


def test_features_in_rect_with_orthographic_matrix():
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    centers = {0: (0.0, 0.0, 0.0), 1: (0.9, 0.9, 0.0), 2: (-0.5, 0.5, 0.0)}
    # region 200x200: NDC -1..1 -> 0..200
    assert features_in_rect(centers, identity, 200, 200, (90, 90, 200, 200)) == [0, 1]
    assert features_in_rect(centers, identity, 200, 200, (0, 100, 80, 200)) == [2]


# --------------------------------------------------------------------------- proxy arrays
def test_flat_arrays_lod0_footprint_and_bbox():
    """B14: every feature is flat at its bbox min Z: LoD0, else ground footprint, else bbox rectangle."""
    box = _e("a", ["lod2Solid"], bbox=(100, 200, 10, 110, 210, 20))
    lod0 = _e("b", ["lod0MultiSurface"], bbox=(100, 200, 5, 110, 210, 9),
              lod0=[[100, 200, 110, 200, 110, 210, 100, 200]],
              footprint=[[0, 0, 1, 0, 1, 1, 0, 0]])  # LoD0 wins over the footprint
    foot = _e("c", ["lod2Solid"], bbox=(100, 200, 12, 110, 210, 30),
              footprint=[[100, 200, 110, 200, 110, 210, 105, 215, 100, 210, 100, 200]])
    risky = _e("d", ["lod2Solid"], implicit_geometry=True)
    a = build_proxy_arrays([box, lod0, foot, risky], (100, 200, 10))
    assert a.vertex_count == 4 + 3 + 5 + 4 and a.face_count == 4
    assert list(a.face_feature) == [0, 1, 2, 3]
    assert list(a.face_state) == [STATE_INDEXED] * 3 + [STATE_RISK]
    assert list(a.loop_total) == [4, 3, 5, 4]
    # local coordinates, closing point dropped, nothing above the bbox min Z
    assert tuple(a.co[0:12]) == (0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 10.0, 10.0, 0.0, 0.0, 10.0, 0.0)
    assert tuple(a.co[12:21]) == (0.0, 0.0, -5.0, 10.0, 0.0, -5.0, 10.0, 10.0, -5.0)
    assert {a.co[k] for k in range(23, 36, 3)} == {2.0}
    assert a.lod0_features == 1


def test_outline_edges_follow_the_flat_faces():
    ents = [_e("a", [], bbox=(0, 0, 3, 2, 2, 9)), _e("b", [], bbox=(5, 0, 0, 6, 1, 1))]
    a = build_proxy_arrays(ents, (0, 0, 0))
    pts = outline_edges(a, {0})
    assert len(pts) == 8 and {p[2] for p in pts} == {3.0}
    assert outline_edges(a, set()) == []


def test_batches_with_offsets_equal_one_build():
    ents = [_e(str(i), ["lod2Solid"], bbox=(i, 0, 0, i + 1, 1, 1)) for i in range(5)]
    full = build_proxy_arrays(ents, (0, 0, 0))
    acc = ProxyArrays()
    for lo, hi in ((0, 2), (2, 5)):
        acc.extend(build_proxy_arrays(ents[lo:hi], (0, 0, 0), first_feature=lo,
                                      vertex_offset=acc.vertex_count, loop_offset=acc.loop_count))
    for name in ("co", "loop_vertex", "loop_start", "loop_total", "face_feature", "face_state"):
        assert getattr(acc, name) == getattr(full, name), name


def test_skip_hides_features():
    ents = [_e(str(i), ["lod2Solid"]) for i in range(3)]
    a = build_proxy_arrays(ents, (0, 0, 0), skip={1})
    assert set(a.face_feature) == {0, 2}


def test_preview_label_never_calls_a_box_lod0():
    box = _e("a", ["lod2Solid"])
    lod0 = _e("b", ["lod0FootPrint"], lod0=[[0, 0, 1, 0, 1, 1]])
    assert preview_label([box]) == PREVIEW_BBOX == "Bounding box"
    assert preview_label([lod0]) == PREVIEW_LOD0 == "LoD0"
    assert preview_label([box, lod0]) == "LoD0 + Bounding box"
    assert preview_label([]) == PREVIEW_BBOX
    foot = _e("c", ["lod2Solid"], footprint=[[0, 0, 1, 0, 1, 1]])
    assert preview_label([foot]) == "Footprint (ground surface)"
    assert preview_label([lod0, foot, box]) == "LoD0 + Footprint (ground surface) + Bounding box"


# --------------------------------------------------------------------------- sniff
def test_sniff_summary_small_file(tmp_path):
    s = sniff_file(fx.write(tmp_path, "a.gml", fx.citygml30()))
    assert s.is_citygml and s.version == "3.0" and s.exact and s.features_estimate == 6
    assert s.label().startswith("CityGML 3.0 · EPSG:25832 · ")
    assert s.label().endswith(" · 6 features")
    s10 = sniff_file(fx.write(tmp_path, "h.gml", fx.citygml10(3)))
    assert s10.version == "1.0"
    assert s10.label().startswith("CityGML 1.0 · EPSG:25832 · ")  # ADV URN shortened


def test_sniff_estimate_large_file(tmp_path):
    path = fx.write(tmp_path, "h.gml", fx.citygml10(3000))
    s = sniff_file(path)
    assert not s.exact
    assert 2400 <= s.features_estimate <= 3600
    assert s.label().split(" · ")[-1].startswith("~")


def test_browse_threshold():
    from shared.citygml_sniff import SniffSummary
    small = SniffSummary("x", size=5 * 2**20, is_citygml=True, features_estimate=20)
    big = SniffSummary("x", size=25 * 2**20, is_citygml=True, features_estimate=20)
    many = SniffSummary("x", size=5 * 2**20, is_citygml=True, features_estimate=250)
    assert not browse_recommended(small)
    assert browse_recommended(big) and browse_recommended(many)
    assert browse_recommended(small, threshold_mb=4.0)


def test_browse_threshold_30_buildings():
    from shared.citygml_sniff import BROWSE_THRESHOLD_FEATURES, SniffSummary
    assert BROWSE_THRESHOLD_FEATURES == 30

    def mk(n, mb=1, exact=True):
        return SniffSummary("x", size=int(mb * 2**20), is_citygml=True, features_estimate=n, exact=exact)

    assert not browse_recommended(mk(30))
    assert browse_recommended(mk(31))
    # preference change moves the threshold
    assert not browse_recommended(mk(31), threshold_features=40)
    assert browse_recommended(mk(41), threshold_features=40)
    # MB threshold stays inclusive
    assert browse_recommended(mk(1, mb=20))
    assert not browse_recommended(mk(1, mb=19.9))
    # estimated (not exact) counts decide the same way
    assert not browse_recommended(mk(30, exact=False))
    assert browse_recommended(mk(31, exact=False))


# --------------------------------------------------------------------------- worker
def test_worker_messages_and_cache_hit(tmp_path):
    path = fx.write(tmp_path, "h.gml", fx.citygml10(40))
    cdir = str(tmp_path / "cache")
    msgs = []
    idx = run_browse_index(path, msgs.append, cache_dir=cdir)
    kinds = [m[0] for m in msgs]
    assert kinds[0] == "origin" and kinds[-1] == "final" and "batch" in kinds
    assert msgs[0][1] == (564500.0, 5934500.0, 13.0)   # CityModel envelope centre (T01 rule)
    acc = ProxyArrays()
    for m in msgs:
        if m[0] == "batch":
            acc.extend(m[2])
    assert acc.face_count == len(idx.entries)  # one flat face per building (B14)
    assert msgs[-1][3] is False
    again = []
    run_browse_index(path, again.append, cache_dir=cdir)
    assert [m[0] for m in again] == ["origin", "final"] and again[-1][3] is True
    assert again[-1][2].face_count == acc.face_count


def test_worker_fixed_origin_and_origin_without_envelope(tmp_path):
    path = fx.write(tmp_path, "h.gml", fx.citygml10(2))
    msgs = []
    cdir = str(tmp_path / "cache")
    run_browse_index(path, msgs.append, fixed_origin=(1.0, 2.0, 3.0), use_cache=False, cache_dir=cdir)
    assert msgs[0][1] == (1.0, 2.0, 3.0)
    # ignoring the cache still refreshes it
    assert index_cache.load(path, cdir) is not None
    ents = [_e("a", [], bbox=(564000, 5934000, 13, 564030, 5934010, 25))]
    assert decide_origin(None, ents) == (564000.0, 5934000.0, 13.0)
