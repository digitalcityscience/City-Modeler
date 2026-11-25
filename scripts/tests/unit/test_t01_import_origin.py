"""T01: local origin = data centre (X/Y rounded to 100 m, Z = min Z)."""
import pytest

from shared.import_origin import (
    FAR_FROM_ORIGIN_M,
    bounds_from_pos_lists,
    center_origin,
    file_origin,
    float32_step,
    georef_status,
    is_far_from_origin,
    parse_origin,
    parse_corner,
    point_cloud_io_offset,
    round_to_step,
)


def test_point_cloud_io_offset_reads_its_shift_and_rejects_junk():
    assert point_cloud_io_offset([564512.0, 5934010.5, 3.2]) == (564512.0, 5934010.5, 3.2)
    assert point_cloud_io_offset((564512, 5934010)) == (564512.0, 5934010.0, 0.0)
    for bad in (None, "564512 5934010 3", [1.0], [1, 2, 3, 4], ["x", 1, 2], [float("nan"), 1, 2], 5):
        assert point_cloud_io_offset(bad) is None

# Envelope of buildings_lod2_citygml_3_0.gml (Hamburg, EPSG:25832)
LOWER = (564719.04, 5934183.51, 17.23)
UPPER = (564909.456, 5934436.156, 53.755)


def test_parse_corner_3d_2d_and_invalid():
    assert parse_corner("1 2 3") == (1.0, 2.0, 3.0)
    assert parse_corner("  1\n2 ") == (1.0, 2.0)
    assert parse_corner("1") is None
    assert parse_corner("") is None
    assert parse_corner(None) is None
    assert parse_corner("a b c") is None


def test_round_to_step_rounds_half_up():
    assert round_to_step(564794.8) == 564800.0
    assert round_to_step(5934262.06) == 5934300.0
    assert round_to_step(149.99) == 100.0
    assert round_to_step(150.0) == 200.0
    assert round_to_step(-149.0) == -100.0


def test_center_origin_3d_envelope():
    assert center_origin(LOWER, UPPER) == (564800.0, 5934300.0, 17.23)


def test_center_origin_2d_envelope_gives_zero_z():
    assert center_origin((100.0, 200.0), (300.0, 400.0)) == (200.0, 300.0, 0.0)


def test_center_origin_uses_min_z_of_both_corners():
    assert center_origin((0.0, 0.0, 10.0), (0.0, 0.0, 5.0))[2] == 5.0


def test_center_origin_without_upper_uses_rounded_lower():
    assert center_origin(LOWER, None) == (564700.0, 5934200.0, 17.23)


def test_center_origin_invalid_lower():
    assert center_origin(None, UPPER) is None
    assert center_origin((1.0,), UPPER) is None


def test_file_origin_prefers_envelope_and_does_not_scan():
    def _scan():
        raise AssertionError("scan must not run when an envelope exists")

    assert file_origin(LOWER, UPPER, scan_bounds=_scan) == (564800.0, 5934300.0, 17.23)


def test_file_origin_scans_without_envelope():
    bounds = ((1000.0, 2000.0, 5.0), (1400.0, 2600.0, 30.0))
    assert file_origin(None, None, scan_bounds=lambda: bounds) == (1200.0, 2300.0, 5.0)


def test_file_origin_falls_back_to_zero():
    assert file_origin(None, None) == (0.0, 0.0, 0.0)
    assert file_origin(None, None, scan_bounds=lambda: None) == (0.0, 0.0, 0.0)


def test_bounds_from_pos_lists_mixed_dimensions():
    bounds = bounds_from_pos_lists([
        ("10 20 30 40 50 60", 3),
        ("5 70", 2),
        ("", 3),
        ("not numbers", None),
        ("1 2 3 4 5 6", None),  # guessed 3D
    ])
    assert bounds == ((1.0, 2.0, 0.0), (40.0, 70.0, 60.0))


def test_bounds_from_pos_lists_empty():
    assert bounds_from_pos_lists([]) is None
    assert bounds_from_pos_lists([("", None)]) is None


@pytest.mark.parametrize(
    "center,expected",
    [
        ((0.0, 0.0), False),
        ((FAR_FROM_ORIGIN_M - 1.0, 0.0), False),
        ((FAR_FROM_ORIGIN_M + 1.0, 0.0), True),
        ((40_000.0, 40_000.0), True),  # diagonal ~56.6 km
    ],
)
def test_far_from_origin_warning_threshold(center, expected):
    assert is_far_from_origin(center) is expected


def test_georef_status_shows_origin_and_flags_missing_crs():
    assert georef_status("EPSG:25832", (548100.0, 5934200.0, 3.5)) == [
        ("CRS: EPSG:25832", "ok"), ("Origin: 548100.00, 5934200.00, 3.50", "ok")]
    assert georef_status(None, None) == [("CRS: not set yet", "unset")]
    assert all(kind == "error" for _, kind in georef_status(None, (548100.0, 5934200.0, 0.0)))


def test_parse_origin_accepts_separators_and_rejects_junk():
    assert parse_origin("548100 5934200 12.5") == (548100.0, 5934200.0, 12.5)
    assert parse_origin("548100.25, 5934200;3") == (548100.25, 5934200.0, 3.0)
    assert parse_origin("548100 5934200") == (548100.0, 5934200.0, 0.0)
    for bad in ("", "1", "1 2 3 4", "a b", "1 nan 2"):
        assert parse_origin(bad) is None


def test_float32_step_is_half_a_metre_at_utm_northing():
    assert float32_step(5934200.0) == 0.5
    assert float32_step(500.0) < 0.0001
    assert float32_step(0.0) == 0.0
