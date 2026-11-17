"""T09: DB Browse first - id validation, citydb-tool arguments, catalog SQL,
view extent -> CRS, grouping of versions, version choice and WKB rings."""
import math
import struct

import pytest

from citydb import catalog
from citydb.catalog import CatalogRow, InvalidId
from shared.browse.view_extent import (area_km2, ground_extent, ground_level, ray_ground_point,  # noqa: E402
                                       regrounded_extent, to_crs)


# --------------------------------------------------------------------------- ids
@pytest.mark.parametrize("value, expected", [
    (13, [13]), ("13", [13]), (" 43 ", [43]), ("+7", [7]),
])
def test_feature_ids_accept_plain_integers(value, expected):
    assert catalog.validate_feature_ids([value]) == expected


@pytest.mark.parametrize("value", [
    "1; DROP TABLE feature", "1 OR 1=1", "1,2", "0", "-5", "1e3", "abc", "", True, 2 ** 63, "9" * 30,
    "1)", "1--", "0x10",
])
def test_feature_ids_reject_anything_else(value):
    with pytest.raises(InvalidId):
        catalog.validate_feature_ids([value])


def test_feature_ids_sorted_unique():
    assert catalog.validate_feature_ids(["43", 13, "13"]) == [13, 43]


@pytest.mark.parametrize("text", [
    "1; DROP TABLE feature", "a b", "x'y", 'x"y', "id);--", "", "   ", "a,b", "a|b", "a&b", "x=1",
])
def test_load_by_id_rejects_injection_and_lists(text):
    with pytest.raises(InvalidId):
        catalog.parse_id_token(text)


def test_load_by_id_tokens():
    assert catalog.parse_id_token("DEHHALKAJ0000yAZ") == (None, "DEHHALKAJ0000yAZ")
    assert catalog.parse_id_token(" 43 ") == (43, "43")
    assert catalog.parse_id_token("UUID_6f1c-22a.b:c") == (None, "UUID_6f1c-22a.b:c")


@pytest.mark.parametrize("name, ok", [("Building", True), ("", True), ("Bridge2", True),
                                      ("Building; DROP", False), ("1abc", False), ("a b", False)])
def test_classname(name, ok):
    if ok:
        assert catalog.validate_classname(name) == name
    else:
        with pytest.raises(InvalidId):
            catalog.validate_classname(name)


def test_limit_clamped_to_hard_limit():
    assert catalog.clamp_limit(0) == catalog.DEFAULT_LIMIT
    assert catalog.clamp_limit(50_000) == catalog.HARD_LIMIT == 20_000
    assert catalog.clamp_limit(10) == 10


# --------------------------------------------------------------------------- citydb-tool arguments
def test_id_filter_only_ints():
    assert catalog.id_filter(["43", 13]) == "core:id IN (13,43)"
    assert catalog.sql_filter([13, 43]) == "SELECT id FROM feature WHERE id IN (13,43)"
    with pytest.raises(InvalidId):
        catalog.id_filter(["13); DELETE FROM feature; --"])
    with pytest.raises(InvalidId):
        catalog.id_filter([])
    with pytest.raises(InvalidId):
        catalog.id_filter(range(1, catalog.MAX_EXPORT_IDS + 2))


def test_export_options_lod2_and_highest():
    lod2 = catalog.export_options([5, 3], "LOD2")
    assert lod2["cql2_filter"] == "core:id IN (3,5)"
    assert lod2["lod"] == ["2"] and lod2["lod_mode"] == "maximum"
    assert lod2["citygml_version"] == "3.0" and lod2["lod_search_depth"] == "all"
    high = catalog.export_options([3], "HIGHEST")
    assert high["lod"] == ["0", "1", "2", "3", "4"] and high["lod_mode"] == "maximum"
    with pytest.raises(ValueError):
        catalog.export_options([3], "LOD7")


def test_export_options_reach_the_command_line(monkeypatch, tmp_path):
    """The options produce --filter/--lod arguments; no password, no shell metacharacters."""
    import importlib.util
    import sys
    import types
    from pathlib import Path

    root = Path(catalog.__file__).resolve().parents[1]
    pkg = "cgml3_t09_cli"
    for name, path in ((pkg, root), (f"{pkg}.ops", root / "ops"), (f"{pkg}.common", root / "common"),
                       (f"{pkg}.citydb", root / "citydb")):
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules.setdefault(name, mod)
    spec = importlib.util.spec_from_file_location(f"{pkg}.ops.citydb_cli", root / "ops" / "citydb_cli.py")
    cli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)
    seen = {}

    def fake_run(self, args, cwd=None, timeout=None, env=None, on_output=None, cancelled=None):
        seen.update(args=args, env=env, on_output=on_output)
        return True, ""

    monkeypatch.setattr(cli.CityDBTool, "_run", fake_run)
    monkeypatch.setattr(cli.CityDBTool, "_base_native", lambda self: ["citydb"])
    prefs = types.SimpleNamespace(citydb_exe="", use_docker=False, docker_image="")
    cli.CityDBTool(prefs).export_db_to_citygml(
        str(tmp_path / "sel_1.gml"), host="127.0.0.1", port="55433", db="citydb", user="citydb",
        password="s3cr&t", db_schema="t09", on_output=print, **catalog.export_options([43, 13], "HIGHEST"))
    args = seen["args"]
    assert args[args.index("--filter") + 1] == "core:id IN (13,43)"
    assert args[args.index("--lod") + 1] == "0,1,2,3,4"
    assert args[args.index("--lod-mode") + 1] == "maximum"
    assert args[args.index("--citygml-version") + 1] == "3.0"
    assert "s3cr&t" not in " ".join(args)
    assert seen["env"]["CITYDB_PASSWORD"] == "s3cr&t" and seen["env"]["CITYDB_SCHEMA"] == "t09"
    assert seen["on_output"] is print


# --------------------------------------------------------------------------- SQL
def _render(composed):
    return composed.as_string(None)


@pytest.fixture(autouse=False)
def needs_psycopg():
    pytest.importorskip("psycopg")


@pytest.mark.usefixtures("needs_psycopg")
def test_catalog_sql_is_parameterised_and_quotes_schema():
    q = _render(catalog.page_sql('weird"schema'))
    assert '"weird""schema".feature' in q
    for p in ("%(xmin)s", "%(ymin)s", "%(xmax)s", "%(ymax)s", "%(srid)s", "%(after_id)s", "%(limit)s"):
        assert p in q
    assert "f.envelope && ST_MakeEnvelope" in q and "oc.is_toplevel = 1" in q
    assert "ORDER BY f.id LIMIT" in q and "classname = %(classname)s" not in q
    q2 = _render(catalog.page_sql("citydb", "Building"))
    assert "oc.classname = %(classname)s" in q2
    for fn in (catalog.count_sql, catalog.find_sql, catalog.lod0_sql, catalog.footprint_sql):
        text = _render(fn("citydb"))
        assert '"citydb".' in text
        for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
            assert word not in text.upper().split()


@pytest.mark.usefixtures("needs_psycopg")
def test_find_sql_matches_objectid_or_id():
    q = _render(catalog.find_sql("citydb"))
    assert "f.objectid = %(objectid)s OR f.id = %(fid)s" in q


def test_extent_params():
    p = catalog.extent_params((1, 2, 3, 4), 25832, "Building")
    assert p == {"xmin": 1.0, "ymin": 2.0, "xmax": 3.0, "ymax": 4.0, "srid": 25832, "classname": "Building"}
    with pytest.raises(ValueError):
        catalog.extent_params((3, 2, 1, 4), 25832)


class FakeConn:
    """Records statements; returns canned rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self

    def fetchall(self):
        return self.rows

    def rollback(self):
        pass


@pytest.mark.usefixtures("needs_psycopg")
def test_query_extent_rows_and_params():
    conn = FakeConn([(13, "DEHHALKAJ000179L", "Building", 1.0, 2.0, 0.5, 3.0, 4.0, 9.5,
                      ["lod2MultiSurface", "lod2Solid", "lod2TerrainIntersectionCurve"])])
    rows = catalog.query_extent(conn, "citydb", (0, 0, 10, 10), 25832, limit=99_999, after_id=7)
    assert rows[0].id == 13 and rows[0].bbox == (1.0, 2.0, 0.5, 3.0, 4.0, 9.5)
    assert rows[0].lod_levels == (2,)
    params = conn.calls[0][1]
    assert params["limit"] == catalog.HARD_LIMIT and params["after_id"] == 7 and params["srid"] == 25832


@pytest.mark.usefixtures("needs_psycopg")
def test_find_by_id_never_runs_with_bad_input():
    conn = FakeConn([])
    with pytest.raises(InvalidId):
        catalog.find_by_id(conn, "citydb", "1; DROP TABLE feature")
    assert conn.calls == []
    catalog.find_by_id(conn, "citydb", "43")
    assert conn.calls[0][1] == {"objectid": "43", "fid": 43, "limit": catalog.MAX_ID_MATCHES}


# --------------------------------------------------------------------------- versions
def _row(fid, objectid, lods):
    return CatalogRow(id=fid, objectid=objectid, classname="Building", bbox=(0, 0, 0, 1, 1, 1), lods=tuple(lods))


ROWS = [
    _row(1, "DEHHALKAJ0000yAZ", ["lod2Solid", "lod2TerrainIntersectionCurve"]),
    _row(13, "DEHHALKAJ000179L", ["lod2Solid"]),
    _row(43, "DEHHALKAJ000179L", ["lod3Solid", "lod3TerrainIntersectionCurve"]),
    _row(66, "DEHHALKAJ0000yAZ", ["lod3Solid"]),
    _row(70, "NOLOD", []),
]


def test_group_versions_and_label():
    g = catalog.group_versions(ROWS)
    assert [r.id for r in g["DEHHALKAJ000179L"]] == [13, 43]
    assert catalog.versions_label(g["DEHHALKAJ000179L"]) == "2 versions: LoD2 (id 13), LoD3 (id 43)"
    assert catalog.versions_label([ROWS[0]]) == "LoD2 (id 1)"
    assert catalog.versions_label([ROWS[4]]) == "no LoD (id 70)"


def test_plan_groups_picks_one_version_per_building():
    rows = {r.id: r for r in ROWS}
    load, missing, skipped = catalog.plan_groups(rows, "LOD2")
    assert load == [1, 13] and missing == [70] and skipped == [43, 66]
    load, missing, skipped = catalog.plan_groups(rows, "HIGHEST")
    assert load == [43, 66] and missing == [70] and skipped == [1, 13]
    # a single LoD3 row asked for LoD2 has nothing to load
    assert catalog.plan_groups({43: ROWS[2]}, "LOD2") == ([], [43], [])
    assert catalog.loaded_level(ROWS[2], "HIGHEST") == 3 and catalog.loaded_level(ROWS[2], "LOD2") == -1


def test_preview_label_never_calls_a_box_lod0():
    box = _row(1, "a", ["lod2Solid"])
    real = _row(2, "b", ["lod0MultiSurface"])
    real.rings = [[0, 0, 1, 0, 1, 1, 0, 0]]
    foot = _row(3, "c", ["lod2Solid"])
    foot.rings, foot.footprint = [[0, 0, 1, 0, 1, 1, 0, 0]], True
    assert catalog.preview_label([box]) == "Bounding box"
    assert catalog.preview_label([real]) == "LoD0"
    assert catalog.preview_label([foot]) == "Footprint (ground surface)"
    assert catalog.preview_label([real, foot, box]) == "LoD0 + Footprint (ground surface) + Bounding box"
    assert catalog.preview_label([]) == "Bounding box"
    assert real.has_lod0_geometry and not box.has_lod0_geometry


# --------------------------------------------------------------------------- WKB
def _wkb_line(points, z=False, big=False, srid=None):
    order = ">" if big else "<"
    gtype = 2 | (0x80000000 if z else 0) | (0x20000000 if srid else 0)
    out = bytes([0 if big else 1]) + struct.pack(order + "I", gtype)
    if srid:
        out += struct.pack(order + "I", srid)
    out += struct.pack(order + "I", len(points))
    for p in points:
        out += struct.pack(order + ("3d" if z else "2d"), *p)
    return out


def test_wkb_linestring():
    pts = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    assert catalog.wkb_linestring_xy(_wkb_line(pts)) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert catalog.wkb_linestring_xy(_wkb_line(pts, big=True)) == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    pts3 = [(1.0, 2.0, 9.0), (3.0, 4.0, 9.0)]
    assert catalog.wkb_linestring_xy(_wkb_line(pts3, z=True, srid=25832)) == [1.0, 2.0, 3.0, 4.0]
    assert catalog.wkb_linestring_xy(b"\x01\x01\x00\x00\x00") == []  # a point is not a ring


def test_count_top_level_members(tmp_path):
    p = tmp_path / "x.gml"
    body = "<core:CityModel>" + "".join(
        f"<core:cityObjectMember><bldg:Building gml:id='b{i}'/></core:cityObjectMember>" + " " * 997
        for i in range(2500)) + "</core:CityModel>"
    p.write_text(body)
    assert catalog.count_top_level_members(str(p)) == 2500
    p.write_text("<CityModel><cityObjectMember/><cityObjectMember>x</cityObjectMember></CityModel>")
    assert catalog.count_top_level_members(str(p)) == 2


# --------------------------------------------------------------------------- view extent
def test_ray_hits_ground():
    assert ray_ground_point((0, 0, 100), (0, 0, -1), 0.0) == (0.0, 0.0)
    x, y = ray_ground_point((0, 0, 100), (1, 0, -1), 0.0)
    assert math.isclose(x, 100.0) and math.isclose(y, 0.0)


def test_ray_above_horizon_is_capped():
    x, y = ray_ground_point((0, 0, 10), (0, 1, 0.2), 0.0, max_distance=500.0)
    assert math.isclose(math.hypot(x, y), 500.0)
    x, y = ray_ground_point((0, 0, 10), (0, 1, -0.0001), 0.0, max_distance=500.0)  # hits far away
    assert math.isclose(math.hypot(x, y), 500.0)


def test_ground_extent_top_view_to_crs():
    # orthographic top view: parallel rays from a 200 x 100 m rectangle
    rays = [((x, y, 50.0), (0, 0, -1)) for x, y in ((-100, -50), (100, -50), (100, 50), (-100, 50), (0, 0))]
    local = ground_extent(rays, 0.0)
    assert local == (-100.0, -50.0, 100.0, 50.0)
    crs = to_crs(local, (564800.0, 5934300.0, 17.8))
    assert crs == (564700.0, 5934250.0, 564900.0, 5934350.0)
    assert math.isclose(area_km2(crs), 0.02)


def test_ground_extent_minimum_size_and_empty():
    assert ground_extent([], 0.0) is None
    e = ground_extent([((5, 5, 10), (0, 0, -1))], 0.0)
    assert e[2] - e[0] >= 1.0 and e[3] - e[1] >= 1.0


ORIGIN = (564800.0, 5934300.0, 13.5)


def _box(cx, cy, base):
    """CRS box 10 x 10 m around local (cx, cy), base ``base`` m above the origin."""
    x, y, z = ORIGIN[0] + cx, ORIGIN[1] + cy, ORIGIN[2] + base
    return (x - 5, y - 5, z, x + 5, y + 5, z + 12)


def test_ground_level_without_boxes_is_the_origin_plane():
    assert ground_level([], ORIGIN) == 0.0
    assert ground_level([(1.0, 2.0, 3.0, 4.0)], ORIGIN) == 0.0  # 2D extent: ignored


def test_ground_level_median_of_the_boxes_in_view():
    near = [_box(10 * i, 0, base) for i, base in enumerate((10.0, 11.0, 30.0, 10.5, 9.0))]  # one tall outlier
    far = [_box(2000 + i, 0, 40.0) for i in range(20)]  # a hill shown earlier
    assert ground_level(near + far, ORIGIN, (-50, -50, 100, 50)) == pytest.approx(10.5)
    # fewer than min_count boxes in view: all shown boxes
    assert ground_level(near[:2] + far, ORIGIN, (-50, -50, 100, 50)) == pytest.approx(40.0)
    assert ground_level(near, ORIGIN) == pytest.approx(10.5)


def test_tilted_view_cut_at_the_ground_level_keeps_the_true_extent():
    # camera 150 m above buildings that stand 21 m above the origin plane, 45 degrees tilt
    rays = [((0.0, 0.0, 171.0), (sx * 0.36, 0.71 + sy * 0.2, -0.71 + sy * 0.2))
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1), (0, 0))]
    true = ground_extent(rays, 21.0)
    plane0 = ground_extent(rays, 0.0)
    assert plane0[1] - true[1] > 10.0  # z = 0 slides the near edge away from the camera
    z = ground_level([_box(0, 100 + i, 21.0) for i in range(5)], ORIGIN, plane0)
    assert ground_extent(rays, z) == pytest.approx(true)


def test_first_query_regrounded_at_the_db_base_matches_the_pan_query():
    # T11f (1): no boxes yet; the worker takes the median base from the DB (CRS Z)
    rays = [((0.0, 0.0, 171.0), (sx * 0.36, 0.71 + sy * 0.2, -0.71 + sy * 0.2))
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1), (0, 0))]
    plane0 = ground_extent(rays, 0.0)
    pan = ground_extent(rays, ground_level([_box(0, 100 + i, 21.0) for i in range(5)], ORIGIN, plane0))
    first = regrounded_extent(rays, plane0, ORIGIN, ORIGIN[2] + 21.0)
    assert first == pytest.approx(pan)
    # no base (empty area, 2D envelopes, timeout) or no rays: the origin plane stays
    assert regrounded_extent(rays, plane0, ORIGIN, None) == plane0
    assert regrounded_extent(None, plane0, ORIGIN, 99.0) == plane0
    assert regrounded_extent(rays, plane0, ORIGIN, ORIGIN[2]) == plane0
