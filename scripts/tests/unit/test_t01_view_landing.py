"""T01: clip table, import summary line and CityGML header sniffing."""
import pytest

from shared.citygml_sniff import looks_like_citygml
from shared.import_summary import (
    NO_FEATURES_MESSAGE,
    ImportSummary,
    count_root_features,
    format_summary,
)
from shared.view_landing import (
    CLIP_END_MAX,
    bounds_extent,
    clip_end_for_extent,
    clip_start_for_extent,
    grown_clip_range,
)


# ---------------------------------------------------------------------------
# Clip table
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extent,start,end",
    [
        (250.0, 0.1, 1250.0),
        (999.0, 0.1, 4995.0),
        (1_000.0, 1.0, 5_000.0),
        (9_999.0, 1.0, 49_995.0),
        (10_000.0, 10.0, 50_000.0),
        (5_000_000.0, 10.0, CLIP_END_MAX),
    ],
)
def test_clip_table(extent, start, end):
    assert clip_start_for_extent(extent) == start
    assert clip_end_for_extent(extent) == pytest.approx(end)


def test_clip_range_only_grows():
    # Blender defaults: 0.01 / 1000
    assert grown_clip_range(0.01, 1000.0, 250.0) == (0.1, 1250.0)
    # A larger existing range is kept
    assert grown_clip_range(1.0, 100_000.0, 250.0) == (1.0, 100_000.0)
    # Larger data widens it
    assert grown_clip_range(0.1, 1250.0, 20_000.0) == (10.0, 100_000.0)


def test_bounds_extent_is_largest_axis():
    assert bounds_extent((0, 0, 0), (150, 160, 25)) == 160


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------
def test_summary_format_matches_ticket_example():
    s = ImportSummary(
        feature_counts={"Building": 3},
        seconds=0.42,
        crs="EPSG:25832",
        origin=(564800.0, 5934300.0, 17.23),
    )
    assert format_summary(s) == (
        "Imported 3 features (3 Building) in 0.4 s · EPSG:25832 · origin 564800/5934300"
    )


def test_summary_multiple_types_and_warnings():
    s = ImportSummary(
        feature_counts={"Bridge": 1, "Building": 3},
        seconds=1.25,
        crs="EPSG:25832",
        origin=(1.0, 2.0, 0.0),
        warnings=["a", "b"],
    )
    assert format_summary(s) == (
        "Imported 4 features (3 Building, 1 Bridge) in 1.2 s · EPSG:25832 · origin 1/2 · 2 warnings"
    )


def test_summary_single_feature_without_crs_or_origin():
    s = ImportSummary(feature_counts={"Building": 1}, seconds=0.0)
    assert format_summary(s) == "Imported 1 feature (1 Building) in 0.0 s"


def test_summary_no_features():
    assert format_summary(ImportSummary()) == NO_FEATURES_MESSAGE
    assert NO_FEATURES_MESSAGE.startswith("0 features matched filters (bbox/LoD/type)")


class _Obj(dict):
    def __init__(self, parent=None, **props):
        super().__init__(**props)
        self.parent = parent


def test_count_root_features_skips_children_and_untyped():
    b1 = _Obj(cgml3_feature="Building")
    b2 = _Obj(feature_type="Building")
    wall = _Obj(parent=b1, cgml3_feature="WallSurface")
    stray = _Obj()
    bridge = _Obj(cgml3_feature="Bridge")
    # Parent outside the imported set (e.g. pre-existing empty) still counts as root
    outsider = _Obj(parent=_Obj(cgml3_feature="Building"), cgml3_feature="Building")
    counts = count_root_features([b1, b2, wall, stray, bridge, outsider])
    assert counts == {"Building": 3, "Bridge": 1}


# ---------------------------------------------------------------------------
# CityGML sniffing for dropped .xml files
# ---------------------------------------------------------------------------
def test_sniff_detects_citygml_namespace(tmp_path):
    p = tmp_path / "city.xml"
    p.write_text(
        '<?xml version="1.0"?>\n<core:CityModel xmlns:core="http://www.opengis.net/citygml/3.0"/>',
        encoding="utf-8",
    )
    assert looks_like_citygml(str(p))


def test_sniff_rejects_other_xml(tmp_path):
    p = tmp_path / "other.xml"
    p.write_text('<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0"/>', encoding="utf-8")
    assert not looks_like_citygml(str(p))


def test_sniff_only_reads_header(tmp_path):
    p = tmp_path / "late.xml"
    p.write_bytes(b"<root>" + b" " * 70_000 + b'xmlns="http://www.opengis.net/citygml/2.0"</root>')
    assert not looks_like_citygml(str(p))


def test_sniff_missing_file(tmp_path):
    assert not looks_like_citygml(str(tmp_path / "missing.xml"))
