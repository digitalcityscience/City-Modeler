"""T02: discovery result formatting (what the city-model list shows)."""
from citydb import models
from citydb.models import DatabaseInfo, DiscoveryResult, SchemaInfo


def _hamburg(**kw):
    base = dict(
        database="citydb", schema="hamburg_lod2", status=models.OK, version="5.1.4",
        srid=25832, crs_name="ETRS89 / UTM zone 32N",
        feature_counts={"Building": 3}, extent=(564831.0, 5934297.7, 564870.6, 5934340.6),
        center_lonlat=(9.9841, 53.5466), comment="Hamburg HafenCity (LoD2 only)",
    )
    base.update(kw)
    return SchemaInfo(**base)


def test_label_prefers_alias_then_comment_then_schema():
    info = _hamburg()
    assert info.display_label("My Hamburg") == "My Hamburg"
    assert info.display_label() == "Hamburg HafenCity (LoD2 only)"
    assert _hamburg(comment="").display_label() == "hamburg_lod2"
    assert _hamburg(comment="Line one\nline two").display_label() == "Line one"
    # Default schema "citydb" exists in every 3DCityDB database: name it after the database.
    assert _hamburg(database="citydb_demo", schema="citydb", comment="").display_label() == "citydb_demo"


def test_counts_formatting():
    assert models.format_counts(_hamburg()) == "3 Building"
    many = _hamburg(feature_counts={"Building": 12000, "Bridge": 3, "Road": 40, "WaterBody": 2, "Tunnel": 1})
    assert models.format_counts(many) == "12,000 Building, 40 Road, 3 Bridge, +2 types"
    est = _hamburg(feature_counts={}, feature_estimate=48000)
    assert models.format_counts(est) == "~ 48,000 features (estimate)"
    assert models.format_counts(_hamburg(feature_counts={})) == "no features"


def test_extent_and_place_formatting():
    info = _hamburg()
    assert models.format_extent_size(info) == "40 × 43 m"
    big = _hamburg(extent=(0.0, 0.0, 12500.0, 8300.0), extent_estimated=True)
    assert models.format_extent_size(big) == "~ 12 km × 8.3 km"
    geo = _hamburg(crs_geographic=True, extent=(9.9, 53.5, 10.1, 53.6))
    assert models.format_extent_size(geo).endswith("°")
    assert models.format_lonlat((9.9841, 53.5466)) == "9.98°E 53.55°N"
    assert models.format_lonlat((-73.99, -40.5)) == "73.99°W 40.50°S"
    assert models.format_lonlat(None) == ""


def test_summary_line_per_status():
    assert models.summary_line(_hamburg()) == (
        "EPSG:25832 · 3 Building · 40 × 43 m · 9.98°E 53.55°N")
    empty = _hamburg(status=models.EMPTY, feature_counts={}, extent=None, center_lonlat=None, srid=3857)
    assert models.summary_line(empty) == "EPSG:3857 · empty"
    v4 = _hamburg(status=models.UNSUPPORTED, version="4.4.0")
    assert models.summary_line(v4).endswith("3DCityDB 4.4.0 not supported")
    assert not v4.usable and empty.usable


def test_discovery_summary_and_other_databases():
    result = DiscoveryResult(
        server_version="17.0 (Debian 17.0-1.pgdg110+1)",
        databases=[
            DatabaseInfo("citydb", schemas=[_hamburg(schema="citydb"), _hamburg()]),
            DatabaseInfo("citydb_demo", schemas=[_hamburg(database="citydb_demo", status=models.EMPTY)]),
            DatabaseInfo("legacy", schemas=[_hamburg(database="legacy", status=models.UNSUPPORTED)]),
            DatabaseInfo("postgres", status=models.DB_NOT_CITYDB),
            DatabaseInfo("secret", status=models.DB_NO_ACCESS),
        ],
    )
    assert models.discovery_summary(result) == "3 city models in 2 databases · PostgreSQL 17.0"
    assert models.other_databases_line(result) == (
        "legacy (no v5 schema), postgres (no 3DCityDB), secret (no access)")
    assert [s.key for s in result.city_models] == ["citydb/citydb", "citydb/hamburg_lod2", "citydb_demo/hamburg_lod2"]
