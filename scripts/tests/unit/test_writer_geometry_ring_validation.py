import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


wg = load_module("shared/writer_geometry.py", "writer_geometry_under_test")


VALID_QUAD = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 0.0, 1.0),
    (0.0, 0.0, 1.0),
]

VALID_PENTAGON = [
    (0.0, 0.0, 0.0),
    (2.0, 0.0, 0.0),
    (2.0, 0.0, 1.0),
    (1.0, 0.0, 2.0),
    (0.0, 0.0, 1.0),
]


def test_pattern_a_too_few_points_is_degenerate():
    # A -> B -> B -> A
    ring = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    ]

    result = wg.validate_ring(ring)

    assert result.valid is False
    assert result.reason == wg.RING_INVALID_TOO_FEW_POINTS
    assert result.point_count == 2


def test_pattern_b_duplicated_start_vertex_is_degenerate():
    # P1 -> P1 -> P2 -> P3 -> P4 -> P1(close), 4 correct unique corners but
    # the start vertex is duplicated right after the ring begins.
    p1 = (0.0, 0.0, 0.0)
    p2 = (1.0, 0.0, 0.0)
    p3 = (1.0, 0.0, 1.0)
    p4 = (0.0, 0.0, 1.0)
    ring = [p1, p1, p2, p3, p4, p1]

    result = wg.validate_ring(ring)

    # After dedup this is a valid quad (4 distinct points, non-zero area) -
    # the duplicated start vertex alone must not make it degenerate once
    # collapsed.
    assert result.valid is True
    assert result.point_count == 4
    assert result.reason is None


def test_pattern_b_collapses_before_count_check():
    # Regression guard: the consecutive-duplicate-at-start point must
    # actually be dropped by the dedup step, not merely tolerated - verify
    # the cleaned point list itself has exactly 4 entries, not 5 or 6.
    p1 = (0.0, 0.0, 0.0)
    p2 = (1.0, 0.0, 0.0)
    p3 = (1.0, 0.0, 1.0)
    p4 = (0.0, 0.0, 1.0)
    ring = [p1, p1, p2, p3, p4, p1]

    result = wg.validate_ring(ring)

    assert result.points == [p1, p2, p3, p4]


def test_valid_quad_ring_is_accepted():
    result = wg.validate_ring(VALID_QUAD)

    assert result.valid is True
    assert result.reason is None
    assert result.point_count == 4
    assert result.area > 0.0


def test_valid_pentagon_ring_is_accepted():
    result = wg.validate_ring(VALID_PENTAGON)

    assert result.valid is True
    assert result.reason is None
    assert result.point_count == 5
    assert result.area > 0.0


def test_collinear_ring_is_rejected_despite_three_distinct_points():
    ring = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
    ]

    result = wg.validate_ring(ring)

    assert result.valid is False
    assert result.reason == wg.RING_INVALID_DEGENERATE_AREA
    assert result.point_count == 3
    assert result.area == 0.0


def test_closing_duplicate_point_is_dropped_before_count_check():
    ring = VALID_QUAD + [VALID_QUAD[0]]

    result = wg.validate_ring(ring)

    assert result.valid is True
    assert result.point_count == 4
    assert result.points == VALID_QUAD


def test_single_point_repeated_is_degenerate():
    p = (5.0, 5.0, 5.0)
    ring = [p, p, p, p]

    result = wg.validate_ring(ring)

    assert result.valid is False
    assert result.reason == wg.RING_INVALID_TOO_FEW_POINTS
    assert result.point_count == 1
