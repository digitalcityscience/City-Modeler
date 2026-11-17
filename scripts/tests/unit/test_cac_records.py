"""CityGML authoring records (CAC-P1.2): dict round trip, forward
compatibility, migration hook, id rules and validation. No Blender."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import ids, records as r  # noqa: E402

SQUARE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
HOLE = ((0.2, 0.2, 0.0), (0.4, 0.2, 0.0), (0.4, 0.4, 0.0))


def wall_and_window():
    wall_poly = r.PolygonRecord(
        polygon_id="P_WALL", feature_id="W1", lod=3,
        exterior=r.RingRecord("P_WALL_0_", "exterior", SQUARE),
        interiors=(r.RingRecord("P_WALL_1_", "interior", HOLE, opening_link="OPENINGLINK_a"),),
        multisurface_id="MS1", is_multisurface_member=True, appearance_refs=("app1",),
        geometry_state="committed",
    )
    win_poly = r.PolygonRecord(polygon_id="P_WIN", feature_id="WIN1", lod=3,
                               exterior=r.RingRecord("P_WIN_0_", "exterior"))
    building = r.FeatureRecord(gml_id="B1", kind="Building", schema_version="3.0")
    wall = r.FeatureRecord(gml_id="W1", kind="WallSurface", parent_id="B1", owner_id="B1",
                           lod=3, polygon_ids=("P_WALL",), source_element="con:WallSurface")
    window = r.FeatureRecord(
        gml_id="WIN1", kind="Window", parent_id="W1", owner_id="B1", host_id="W1",
        opening_link="OPENINGLINK_a", lod=3, lod_origin="authored", generator="facade.window.rect",
        params={"width": 1.2, "height": 1.4, "depth": 0.04}, provenance="authored",
        polygon_ids=("P_WIN",),
    )
    return r.RecordSet(features=(building, wall, window), polygons=(wall_poly, win_poly))


def test_valid_set_has_no_problems():
    assert wall_and_window().validate() == []


def test_record_set_round_trips_through_json():
    rs = wall_and_window()
    data = json.loads(json.dumps(rs.to_dict()))
    assert r.RecordSet.from_dict(data) == rs
    assert data["record_version"] == r.RECORD_VERSION


def test_feature_round_trip_is_lossless_and_detached():
    window = wall_and_window().feature("WIN1")
    data = r.feature_to_dict(window)
    data["params"]["width"] = 99.0          # mutating the dict must not touch the record
    assert window.params["width"] == 1.2
    assert r.feature_from_dict(r.feature_to_dict(window)) == window


def test_unknown_future_fields_are_preserved():
    data = r.feature_to_dict(wall_and_window().feature("W1"))
    data["future_field"] = {"nested": [1, 2]}
    data["record_version"] = r.RECORD_VERSION + 1
    feature = r.feature_from_dict(data)
    assert feature.extra == {"future_field": {"nested": [1, 2]}}
    assert feature.record_version == r.RECORD_VERSION + 1   # not downgraded
    assert r.feature_to_dict(feature) == data

    poly = r.polygon_to_dict(wall_and_window().polygon("P_WALL"))
    poly["interiors"][0]["ring_future"] = "x"
    poly["poly_future"] = True
    again = r.polygon_to_dict(r.polygon_from_dict(poly))
    assert again["poly_future"] is True and again["interiors"][0]["ring_future"] == "x"


def test_migration_hook_upgrades_old_versions(monkeypatch):
    monkeypatch.setattr(r, "RECORD_VERSION", 2)
    monkeypatch.setitem(r.MIGRATIONS, 1,
                        lambda d: {**{k: v for k, v in d.items() if k != "type"}, "kind": d["type"]})
    feature = r.feature_from_dict({"record_version": 1, "gml_id": "X", "type": "Door"})
    assert feature.kind == "Door" and feature.record_version == 2 and feature.extra == {}


def test_missing_migration_and_bad_version_are_errors(monkeypatch):
    monkeypatch.setattr(r, "RECORD_VERSION", 3)
    with pytest.raises(r.RecordError, match="no migration"):
        r.feature_from_dict({"record_version": 1, "gml_id": "X", "kind": "Door"})
    with pytest.raises(r.RecordError, match="unreadable"):
        r.feature_from_dict({"record_version": "one", "gml_id": "X", "kind": "Door"})
    with pytest.raises(r.RecordError):
        r.feature_from_dict(["not", "a", "dict"])


@pytest.mark.parametrize("mutate, fragment", [
    (lambda rs: rs.with_feature(replace(rs.feature("W1"), gml_id="")), "invalid or empty gml_id"),
    (lambda rs: rs.with_feature(replace(rs.feature("W1"), polygon_ids=("P_WALL", "P_GONE"))),
     "'P_GONE' referenced but missing"),
    (lambda rs: replace(rs, polygons=rs.polygons + (rs.polygon("P_WIN"),)), "duplicate polygon_id"),
    (lambda rs: replace(rs, features=rs.features + (rs.feature("W1"),)), "duplicate gml_id"),
    (lambda rs: rs.with_feature(replace(rs.feature("WIN1"), polygon_ids=())), "not listed by feature"),
    (lambda rs: rs.with_feature(replace(rs.feature("WIN1"), provenance="guessed")), "provenance"),
    (lambda rs: rs.with_feature(replace(rs.feature("WIN1"), lod=7)), "lod 7"),
    (lambda rs: rs.with_feature(replace(rs.feature("WIN1"), params={"f": float("nan")})), "plain JSON"),
    (lambda rs: rs.with_feature(replace(rs.feature("WIN1"), polygon_ids=("P_WIN", "P_WIN"))),
     "repeated polygon ids"),
])
def test_invalid_records_are_reported(mutate, fragment):
    problems = mutate(wall_and_window()).validate()
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize("polygon, fragment", [
    (r.PolygonRecord("P", "W1", exterior=r.RingRecord("", "exterior", SQUARE)), "coords present"),
    (r.PolygonRecord("P", "W1", exterior=r.RingRecord("", "exterior", SQUARE[:2]),
                     geometry_state="committed"), "at least 3 distinct"),
    (r.PolygonRecord("P", "W1", exterior=r.RingRecord("", "interior")), "role 'interior'"),
    (r.PolygonRecord("P", "W1", interiors=(r.RingRecord("", "exterior"),)), "interior[0]: role"),
    (r.PolygonRecord("P", "W1", geometry_state="half"), "geometry_state 'half'"),
    (r.PolygonRecord("P", "", ), "invalid feature_id"),
])
def test_invalid_polygons_are_reported(polygon, fragment):
    assert any(fragment in p for p in r.validate_polygon(polygon)), r.validate_polygon(polygon)


def test_record_set_queries():
    rs = wall_and_window()
    assert [f.gml_id for f in rs.children("W1")] == ["WIN1"]
    assert [p.polygon_id for p in rs.polygons_of("W1")] == ["P_WALL"]
    assert rs.polygons_of("missing") == ()
    replaced = rs.with_feature(replace(rs.feature("WIN1"), params={"width": 2.0}))
    assert replaced.feature("WIN1").params == {"width": 2.0} and len(replaced.features) == 3


class _FixedUuid:
    hex = "0123456789abcdef0123456789abcdef"


def test_id_minting_uses_existing_forms():
    fixed = lambda: _FixedUuid()  # noqa: E731
    assert ids.mint_gml_id(uuid_factory=fixed) == "ID_0123456789abcdef0123456789abcdef"
    assert ids.mint_opening_link(uuid_factory=fixed) == "OPENINGLINK_0123456789abcdef0123456789abcdef"
    assert ids.ring_id("PolyID7_1", 0) == "PolyID7_1_0_"
    assert ids.mint_gml_id() != ids.mint_gml_id()   # fresh every time (D5)
    with pytest.raises(ValueError):
        ids.mint_gml_id(prefix="1bad")
    with pytest.raises(ValueError):
        ids.ring_id("P", -1)


def test_id_validation():
    assert ids.is_valid_gml_id("UUID_0d935dab-54a5-421e-95d4-d6ef1ccf269e")
    assert not ids.is_valid_gml_id("") and not ids.is_valid_gml_id("a b") and not ids.is_valid_gml_id(None)
    assert ids.is_authored_gml_id("ID_abc.def-1")
    assert not ids.is_authored_gml_id("1abc") and not ids.is_authored_gml_id("a:b")


def test_semantic_type_round_trips():
    chimney = r.FeatureRecord(gml_id="I1", kind="BuildingInstallation", semantic_type="Chimney")
    assert r.feature_from_dict(r.feature_to_dict(chimney)).semantic_type == "Chimney"
    assert r.FeatureRecord(gml_id="W", kind="WallSurface").semantic_type == ""
