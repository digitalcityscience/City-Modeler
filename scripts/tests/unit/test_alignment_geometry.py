import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.alignment_geometry import (
    AxisAlignment,
    OpeningRect,
    WallBounds,
    classify_alignment,
    classify_relative_axis,
    nearest_same_subtype_gap,
    snap_to_neighbours,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
WALL = WallBounds(u_min=0.0, u_max=10.0, v_min=0.0, v_max=5.0)


def _match(matches, edge, neighbour_index=0):
    for match in matches:
        if match.edge == edge and match.neighbour_index == neighbour_index:
            return match
    return None


# ---------------------------------------------------------------------------
# 1. classify_alignment
# ---------------------------------------------------------------------------
def test_exact_left_edge_match_is_green():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    candidate = OpeningRect(u_min=1.0, u_max=2.0, v_min=3.0, v_max=4.0, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    left = _match(matches, "left")
    assert left is not None
    assert left.state == "green"
    assert left.delta_m == 0.0


def test_near_top_edge_match_is_yellow():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    # Candidate's top edge (v_max) is 10cm below the neighbour's top edge -
    # inside the 15cm yellow band but outside the 2cm green band.
    candidate = OpeningRect(u_min=4.0, u_max=5.0, v_min=0.9, v_max=1.9, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    top = _match(matches, "top")
    assert top is not None
    assert top.state == "yellow"
    assert abs(top.delta_m - 0.1) < 1e-9


def test_far_edge_produces_no_match():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    candidate = OpeningRect(u_min=8.0, u_max=9.0, v_min=4.0, v_max=4.5, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    assert matches == []


def test_overlapping_footprints_are_a_red_collision():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    candidate = OpeningRect(u_min=1.5, u_max=2.5, v_min=1.5, v_max=2.5, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    assert len(matches) == 1
    assert matches[0].edge == "collision"
    assert matches[0].state == "red"
    assert matches[0].neighbour_index == 0


def test_touching_edges_are_not_a_collision():
    # Two windows sharing an exact vertical edge (right of A == left of B) -
    # not an overlap, so no collision, even though they're immediately
    # adjacent.
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    candidate = OpeningRect(u_min=2.0, u_max=3.0, v_min=1.0, v_max=2.0, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    assert all(match.state != "red" for match in matches)


def test_footprint_outside_wall_boundary_is_red():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=2.0, subtype="Window")
    candidate = OpeningRect(u_min=-0.5, u_max=0.5, v_min=1.0, v_max=2.0, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    bounds_match = _match(matches, "bounds", neighbour_index=None)
    assert bounds_match is not None
    assert bounds_match.state == "red"


def test_centre_match_reports_the_matching_axis():
    neighbour = OpeningRect(u_min=1.0, u_max=2.0, v_min=1.0, v_max=3.0, subtype="Window")
    # Candidate's u-centre matches the neighbour's u-centre exactly (u_centre
    # 1.5 both); v-centres differ a lot - so only the u-axis (column) centre
    # match should be reported.
    candidate = OpeningRect(u_min=1.0, u_max=2.0, v_min=4.0, v_max=4.5, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    centre_matches = [m for m in matches if m.edge == "centre"]
    assert len(centre_matches) == 1
    assert centre_matches[0].axis == "u"
    assert centre_matches[0].state == "green"


def test_centre_match_on_both_axes_reports_both_independently():
    # Degenerate (zero-extent) rects sharing both centre coordinates but not
    # overlapping on either axis - exercises that a candidate lining up with
    # a neighbour's centre on *both* u and v gets two independent EDGE_CENTRE
    # entries rather than one axis picked over the other.
    neighbour = OpeningRect(u_min=0.0, u_max=4.0, v_min=3.0, v_max=3.0, subtype="Window")
    candidate = OpeningRect(u_min=2.0, u_max=2.0, v_min=1.0, v_max=5.0, subtype="Window")

    matches = classify_alignment(candidate, [neighbour], WALL)

    centre_matches = {m.axis for m in matches if m.edge == "centre"}
    assert centre_matches == {"u", "v"}
    assert all(m.state != "red" for m in matches)


# ---------------------------------------------------------------------------
# 2. nearest_same_subtype_gap
# ---------------------------------------------------------------------------
def test_nearest_same_subtype_gap_ignores_other_subtypes():
    door = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=2.0, subtype="Door")
    window_far = OpeningRect(u_min=5.0, u_max=6.0, v_min=0.0, v_max=1.0, subtype="Window")
    window_near = OpeningRect(u_min=1.5, u_max=2.5, v_min=0.5, v_max=1.5, subtype="Window")
    candidate = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.5, v_max=1.5, subtype="Window")

    gap = nearest_same_subtype_gap(candidate, [door, window_far, window_near])

    assert gap is not None
    assert gap.neighbour_index == 2
    assert abs(gap.gap_u - 0.5) < 1e-9
    assert gap.gap_v == 0.0


def test_nearest_same_subtype_gap_is_none_without_a_same_subtype_neighbour():
    door = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=2.0, subtype="Door")
    candidate = OpeningRect(u_min=3.0, u_max=4.0, v_min=0.5, v_max=1.5, subtype="Window")

    assert nearest_same_subtype_gap(candidate, [door]) is None


def test_overlapping_axis_reports_zero_gap_on_that_axis():
    neighbour = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=1.0, subtype="Window")
    # Overlaps in u, offset in v.
    candidate = OpeningRect(u_min=0.5, u_max=1.5, v_min=2.0, v_max=3.0, subtype="Window")

    gap = nearest_same_subtype_gap(candidate, [neighbour])

    assert gap.gap_u == 0.0
    assert abs(gap.gap_v - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 3. classify_relative_axis
# ---------------------------------------------------------------------------
def test_beside_pair_implies_vertical_match_on_closer_edge():
    source = OpeningRect(u_min=0.0, u_max=1.0, v_min=1.0, v_max=2.0)
    # Directly to the right, tops nearly level (closer than bottoms).
    target = OpeningRect(u_min=3.0, u_max=4.0, v_min=1.6, v_max=2.05)

    result = classify_relative_axis(source, target)

    assert result == AxisAlignment(relation="beside", axis="vertical", edge="top")


def test_stacked_pair_implies_horizontal_match_on_closer_edge():
    source = OpeningRect(u_min=1.0, u_max=2.0, v_min=0.0, v_max=1.0)
    # Directly above, left edges nearly level (closer than right edges).
    target = OpeningRect(u_min=1.05, u_max=2.4, v_min=3.0, v_max=4.0)

    result = classify_relative_axis(source, target)

    assert result == AxisAlignment(relation="stacked", axis="horizontal", edge="left")


def test_diagonal_pair_is_ambiguous():
    source = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=1.0)
    # Displaced equally in u and v - neither beside nor stacked.
    target = OpeningRect(u_min=2.0, u_max=3.0, v_min=2.0, v_max=3.0)

    result = classify_relative_axis(source, target)

    assert result == AxisAlignment(relation="ambiguous", axis=None, edge=None)


def test_identical_position_pair_is_ambiguous():
    source = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=1.0)
    target = OpeningRect(u_min=0.0, u_max=1.0, v_min=0.0, v_max=1.0)

    result = classify_relative_axis(source, target)

    assert result.relation == "ambiguous"


# ---------------------------------------------------------------------------
# B91: snap_to_neighbours (Shift while pasting)
# ---------------------------------------------------------------------------
def _win(u, v, w=1.0, h=1.5, subtype="Window"):
    return OpeningRect(u_min=u - w / 2, u_max=u + w / 2, v_min=v - h / 2, v_max=v + h / 2, subtype=subtype)


def test_snap_joins_the_row_without_moving_across():
    du, dv = snap_to_neighbours(_win(4.3, 2.4), [_win(2.0, 2.0)])
    assert (round(du, 6), round(dv, 6)) == (0.0, -0.4)


def test_snap_joins_the_column_of_the_floor_below():
    du, dv = snap_to_neighbours(_win(2.3, 5.0), [_win(2.0, 2.0)])
    assert (round(du, 6), round(dv, 6)) == (-0.3, 0.0)


def test_snap_row_and_column_together():
    du, dv = snap_to_neighbours(_win(4.2, 5.1), [_win(2.0, 5.0), _win(4.0, 2.0)])
    assert (round(du, 6), round(dv, 6)) == (-0.2, -0.1)


def test_snap_leaves_far_or_missing_neighbours_alone():
    assert snap_to_neighbours(_win(8.0, 2.0), [_win(2.0, 6.0)]) == (0.0, 0.0)
    assert snap_to_neighbours(_win(8.0, 2.0), []) == (0.0, 0.0)


def test_snap_prefers_same_subtype_and_lines_up_a_door_top():
    door = _win(6.0, 1.3, h=2.2, subtype="Door")
    du, dv = snap_to_neighbours(door, [_win(2.0, 1.65)])  # window top 2.4, door top 2.4 -> keep
    assert (du, round(dv, 6)) == (0.0, 0.0)
    du, dv = snap_to_neighbours(_win(6.0, 2.1), [_win(2.0, 1.95, subtype="Door"), _win(3.0, 2.0)])
    assert round(dv, 6) == -0.1  # the window, not the closer door centre
