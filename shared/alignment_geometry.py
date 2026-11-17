# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Alignment geometry engine (copy-paste-object-behaviour ticket 01).

Answers the three questions `docs/adr/0006-facade-alignment-feedback-scope.md`
defines for the Draw/Stamp live overlays and the Align Objects operator, given
a wall's existing openings and a candidate/source/target opening, all
expressed as axis-aligned rectangles in a wall's own local ``(u, v)`` frame
(the frame `shared/facade_placement.py`'s ``SurfaceContext`` resolves, in
metres - not the 0..1 fractional ``position_uv`` the placement kernel uses):

1. ``classify_alignment`` - edge/centre alignment classification against
   same-WallSurface neighbours (green/yellow/red).
2. ``nearest_same_subtype_gap`` - horizontal/vertical gap, in metres, to the
   nearest same-subtype (Window-to-Window, Door-to-Door) neighbour.
3. ``classify_relative_axis`` - beside/stacked/ambiguous classification for
   Align Objects' axis inference.

**Pure Python - no ``bpy``/``mathutils`` dependency at all**, matching
`shared/facade_surface_resolver.py`'s frame-math half: plain dataclasses over
plain floats, so this is unit-testable without a running Blender. Tickets
02-04 wire this into `draw_callback`s (`ops/facade_authoring.py`,
`ops/semantic_stamp.py`) and a new Align Objects operator - none of them
re-derive this classification logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Literal, Sequence

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

# Exact-snap tolerance for edge/centre alignment (2 cm - small enough that it
# only fires on a deliberate snap, not a coincidental near-placement).
DEFAULT_GREEN_TOLERANCE_M = 0.02

# Near-miss tolerance (15 cm - close enough to be worth a hint, not so close
# it competes with the green band above).
DEFAULT_YELLOW_TOLERANCE_M = 0.15

# Minimum true overlap, in square metres of projected 1-D overlap per axis,
# before two footprints count as "colliding" rather than merely touching at
# a shared edge (which the edge-match classification below already handles).
_COLLISION_EPS_M = 1e-4

# Float noise absorbed when checking a candidate footprint against the wall's
# own boundary - not a design tolerance, just round-trip slack.
_BOUNDS_EPS_M = 1e-6

# Below this centre-to-centre displacement (on both axes at once), a
# source/target pair is treated as the same position rather than measured -
# a distinct concept from `_BOUNDS_EPS_M` above even though it happens to
# share the same float-noise magnitude.
_ZERO_DISPLACEMENT_EPS_M = 1e-6

# How much one axis of displacement must dominate the other before a
# source/target pair reads as cleanly "beside" or "stacked" rather than
# diagonal/ambiguous (ADR 0006's Align Objects axis inference). A ratio of 2
# puts the ambiguous dead-zone at roughly the 27-63 degree band off each
# axis (~37 degrees wide) - generous enough that a near-diagonal placement
# reliably halts and asks instead of guessing an axis the user didn't intend.
_AXIS_DOMINANCE_RATIO = 2.0

# --------------------------------------------------------------------------
# String vocabulary - named constants rather than bare literals scattered
# through the logic below, matching `semantic_appearance.py`'s
# `AppearanceKind`/`KIND_*` pattern.
# --------------------------------------------------------------------------
MatchState = Literal["green", "yellow", "red"]
STATE_GREEN: MatchState = "green"
STATE_YELLOW: MatchState = "yellow"
STATE_RED: MatchState = "red"

# The five edge/centre kinds ticket 01 asks for, plus the two synthetic
# override kinds `classify_alignment` emits instead of a normal edge match
# (a collision or an out-of-bounds footprint each pre-empt the whole
# per-edge comparison for that neighbour - see its docstring).
Edge = Literal["top", "bottom", "left", "right", "centre", "collision", "bounds"]
EDGE_TOP: Edge = "top"
EDGE_BOTTOM: Edge = "bottom"
EDGE_LEFT: Edge = "left"
EDGE_RIGHT: Edge = "right"
EDGE_CENTRE: Edge = "centre"
EDGE_COLLISION: Edge = "collision"
EDGE_BOUNDS: Edge = "bounds"

CentreAxis = Literal["u", "v"]
CENTRE_AXIS_U: CentreAxis = "u"
CENTRE_AXIS_V: CentreAxis = "v"

Relation = Literal["beside", "stacked", "ambiguous"]
RELATION_BESIDE: Relation = "beside"
RELATION_STACKED: Relation = "stacked"
RELATION_AMBIGUOUS: Relation = "ambiguous"

AlignAxis = Literal["vertical", "horizontal"]
AXIS_VERTICAL: AlignAxis = "vertical"
AXIS_HORIZONTAL: AlignAxis = "horizontal"

# `EDGE_KINDS` is the five kinds ticket 01 names (top/bottom/left/right/
# centre) - deliberately excluding the two synthetic override kinds above,
# which aren't "edges" in that sense. Defined here as the flat tuple;
# `_STRAIGHT_EDGE_ACCESSORS` below (after `OpeningRect` exists) is what
# `classify_alignment` actually iterates, keyed by the same four names.
EDGE_KINDS: tuple[Edge, ...] = (EDGE_TOP, EDGE_BOTTOM, EDGE_LEFT, EDGE_RIGHT, EDGE_CENTRE)


@dataclass(frozen=True)
class OpeningRect:
    """An opening's footprint in its host wall's local ``(u, v)`` frame,
    in metres. ``subtype`` is the semantic type (``"Window"``, ``"Door"``,
    ...) used to restrict neighbour-distance and collision checks to
    same-subtype comparisons where the caller asks for that."""

    u_min: float
    u_max: float
    v_min: float
    v_max: float
    subtype: str = ""

    def __post_init__(self):
        if self.u_max < self.u_min or self.v_max < self.v_min:
            raise ValueError("OpeningRect: max must not be less than min on either axis")

    @property
    def u_centre(self) -> float:
        return (self.u_min + self.u_max) / 2.0

    @property
    def v_centre(self) -> float:
        return (self.v_min + self.v_max) / 2.0

    @property
    def width(self) -> float:
        return self.u_max - self.u_min

    @property
    def height(self) -> float:
        return self.v_max - self.v_min


# The four straight-edge kinds' accessors, keyed by name so the comparison
# loop in `classify_alignment` and `EDGE_KINDS` above derive from one source
# of truth instead of two hand-kept-in-sync lists.
_STRAIGHT_EDGE_ACCESSORS: dict[Edge, Callable[[OpeningRect], float]] = {
    EDGE_LEFT: lambda rect: rect.u_min,
    EDGE_RIGHT: lambda rect: rect.u_max,
    EDGE_TOP: lambda rect: rect.v_max,
    EDGE_BOTTOM: lambda rect: rect.v_min,
}


@dataclass(frozen=True)
class WallBounds:
    """A wall's own footprint in its local ``(u, v)`` frame - normally
    ``(0, surface.width, 0, surface.height)`` for `SurfaceContext`."""

    u_min: float
    u_max: float
    v_min: float
    v_max: float


@dataclass(frozen=True)
class EdgeMatch:
    """One alignment-guide result: ``edge`` is one of `EDGE_KINDS`, plus the
    synthetic ``"collision"``/``"bounds"`` kinds emitted instead of a normal
    edge match when the candidate overlaps a neighbour or leaves the wall.
    ``neighbour_index`` indexes the ``neighbours`` sequence passed in, or
    ``None`` for a ``"bounds"`` match (which isn't about any one neighbour).
    ``delta_m`` is the absolute distance, in metres, that drove the
    classification - ``None`` for ``"bounds"``, where no single distance
    applies."""

    edge: Edge
    state: MatchState
    neighbour_index: int | None
    delta_m: float | None
    axis: CentreAxis | None = None  # which axis a "centre" match used


@dataclass(frozen=True)
class NeighbourGap:
    """Horizontal/vertical gap, in metres, from a candidate to the nearest
    same-subtype neighbour. ``gap_u``/``gap_v`` are 0 where the footprints
    already overlap on that axis (nothing to report on that axis)."""

    neighbour_index: int
    gap_u: float
    gap_v: float


@dataclass(frozen=True)
class AxisAlignment:
    """Align Objects' axis inference for a source/target pair. ``relation``
    is ``"beside"`` (implies ``axis="vertical"``), ``"stacked"`` (implies
    ``axis="horizontal"``), or ``"ambiguous"`` (``axis``/``edge`` both
    ``None`` - the caller halts and asks, per ADR 0006)."""

    relation: Relation
    axis: AlignAxis | None
    edge: Edge | None


# --------------------------------------------------------------------------
# 1. Edge/centre alignment classification
# --------------------------------------------------------------------------
def _classify_delta(delta_m: float, green_tolerance_m: float, yellow_tolerance_m: float) -> MatchState | None:
    if delta_m <= green_tolerance_m:
        return STATE_GREEN
    if delta_m <= yellow_tolerance_m:
        return STATE_YELLOW
    return None


def _rects_collide(a: OpeningRect, b: OpeningRect) -> bool:
    overlap_u = min(a.u_max, b.u_max) - max(a.u_min, b.u_min)
    overlap_v = min(a.v_max, b.v_max) - max(a.v_min, b.v_min)
    return overlap_u > _COLLISION_EPS_M and overlap_v > _COLLISION_EPS_M


def _is_out_of_bounds(candidate: OpeningRect, wall_bounds: WallBounds) -> bool:
    return (
        candidate.u_min < wall_bounds.u_min - _BOUNDS_EPS_M
        or candidate.u_max > wall_bounds.u_max + _BOUNDS_EPS_M
        or candidate.v_min < wall_bounds.v_min - _BOUNDS_EPS_M
        or candidate.v_max > wall_bounds.v_max + _BOUNDS_EPS_M
    )


def classify_alignment(
    candidate: OpeningRect,
    neighbours: Sequence[OpeningRect],
    wall_bounds: WallBounds,
    *,
    green_tolerance_m: float = DEFAULT_GREEN_TOLERANCE_M,
    yellow_tolerance_m: float = DEFAULT_YELLOW_TOLERANCE_M,
) -> list[EdgeMatch]:
    """Per-neighbour edge/centre alignment matches for ``candidate`` against
    ``neighbours`` (same-WallSurface openings - filtering to that wall is the
    caller's job, this function does not know which wall anything is on).

    A neighbour the candidate overlaps produces a single ``EDGE_COLLISION``
    red match instead of the normal edge/centre checks (a collision is a
    correctness problem regardless of which edge happens to be close). A
    candidate footprint outside ``wall_bounds`` always produces an
    ``EDGE_BOUNDS`` red match, independent of ``neighbours``. Otherwise, only
    edges that actually land in the green or yellow band are reported - an
    unrelated far-apart neighbour produces no entry, matching the overlay's
    "show a guide line only when there's something to say" behaviour. Centre
    alignment is checked on both axes independently (like the four straight
    edges), so a candidate that happens to line up with a neighbour's centre
    on both ``u`` and ``v`` gets two ``EDGE_CENTRE`` entries, not one picked
    arbitrarily over the other.
    """
    matches: list[EdgeMatch] = []

    if _is_out_of_bounds(candidate, wall_bounds):
        matches.append(EdgeMatch(edge=EDGE_BOUNDS, state=STATE_RED, neighbour_index=None, delta_m=None))

    for index, neighbour in enumerate(neighbours):
        if _rects_collide(candidate, neighbour):
            matches.append(EdgeMatch(edge=EDGE_COLLISION, state=STATE_RED, neighbour_index=index, delta_m=None))
            continue

        for edge, accessor in _STRAIGHT_EDGE_ACCESSORS.items():
            delta = abs(accessor(candidate) - accessor(neighbour))
            state = _classify_delta(delta, green_tolerance_m, yellow_tolerance_m)
            if state is not None:
                matches.append(EdgeMatch(edge=edge, state=state, neighbour_index=index, delta_m=delta))

        for axis, delta in (
            (CENTRE_AXIS_U, abs(candidate.u_centre - neighbour.u_centre)),
            (CENTRE_AXIS_V, abs(candidate.v_centre - neighbour.v_centre)),
        ):
            state = _classify_delta(delta, green_tolerance_m, yellow_tolerance_m)
            if state is not None:
                matches.append(EdgeMatch(edge=EDGE_CENTRE, state=state, neighbour_index=index, delta_m=delta, axis=axis))

    return matches


# --------------------------------------------------------------------------
# 2. Neighbour distance (same-subtype only)
# --------------------------------------------------------------------------
def _axis_gap(candidate_min: float, candidate_max: float, other_min: float, other_max: float) -> float:
    if candidate_max < other_min:
        return other_min - candidate_max
    if other_max < candidate_min:
        return candidate_min - other_max
    return 0.0


def nearest_same_subtype_gap(
    candidate: OpeningRect,
    neighbours: Sequence[OpeningRect],
) -> NeighbourGap | None:
    """The nearest same-subtype (``candidate.subtype``) neighbour's
    horizontal/vertical gap, in metres. ``None`` when ``neighbours`` has no
    opening of that subtype at all."""
    best_index: int | None = None
    best_gap_u = 0.0
    best_gap_v = 0.0
    best_distance = math.inf

    for index, neighbour in enumerate(neighbours):
        if neighbour.subtype != candidate.subtype:
            continue
        gap_u = _axis_gap(candidate.u_min, candidate.u_max, neighbour.u_min, neighbour.u_max)
        gap_v = _axis_gap(candidate.v_min, candidate.v_max, neighbour.v_min, neighbour.v_max)
        distance = math.hypot(gap_u, gap_v)
        if distance < best_distance:
            best_distance = distance
            best_index = index
            best_gap_u, best_gap_v = gap_u, gap_v

    if best_index is None:
        return None
    return NeighbourGap(neighbour_index=best_index, gap_u=best_gap_u, gap_v=best_gap_v)


def _overlap(a_min: float, a_max: float, b_min: float, b_max: float) -> bool:
    return min(a_max, b_max) - max(a_min, b_min) > _COLLISION_EPS_M


def _nearest_shift(pairs, reach: float) -> float:
    """The smallest ``target - current`` of ``pairs`` within ``reach``, else 0."""
    return min((b - a for a, b in pairs if abs(b - a) <= reach), key=abs, default=0.0)


def snap_to_neighbours(candidate: OpeningRect, neighbours: Sequence[OpeningRect]) -> tuple[float, float]:
    """B91, Shift while pasting (like Photoshop's smart guides): the ``(du, dv)`` that lines
    ``candidate`` up with the openings already on the wall. Height first: the nearest centre /
    top / bottom of a neighbour beside it (other columns) within one candidate height - same
    row. Then across: the nearest centre / left / right of a neighbour above or below it (other
    rows) within one width - same column. A beside neighbour never pulls it across (that would
    stack it onto its own row). Same subtype first, any opening when there is none."""
    same = [n for n in neighbours if n.subtype == candidate.subtype] or list(neighbours)
    c = candidate
    beside = [n for n in same if not _overlap(c.u_min, c.u_max, n.u_min, n.u_max)]
    dv = _nearest_shift([(c.v_centre, n.v_centre) for n in beside] + [(c.v_min, n.v_min) for n in beside]
                        + [(c.v_max, n.v_max) for n in beside], c.height)
    v_min, v_max = c.v_min + dv, c.v_max + dv
    stacked = [n for n in same if not _overlap(v_min, v_max, n.v_min, n.v_max)]
    du = _nearest_shift([(c.u_centre, n.u_centre) for n in stacked] + [(c.u_min, n.u_min) for n in stacked]
                        + [(c.u_max, n.u_max) for n in stacked], c.width)
    return du, dv


# --------------------------------------------------------------------------
# 3. Align Objects axis inference
# --------------------------------------------------------------------------
def classify_relative_axis(source: OpeningRect, target: OpeningRect) -> AxisAlignment:
    """Whether ``target`` reads as beside (vertical match) or stacked
    (horizontal match) relative to ``source``, per ADR 0006. Displacement is
    measured centre-to-centre; whichever axis dominates the other by at
    least `_AXIS_DOMINANCE_RATIO` decides the relation, otherwise the pair is
    ambiguous (diagonal) and the caller halts and asks rather than guessing.
    """
    delta_u = abs(source.u_centre - target.u_centre)
    delta_v = abs(source.v_centre - target.v_centre)

    if delta_u < _ZERO_DISPLACEMENT_EPS_M and delta_v < _ZERO_DISPLACEMENT_EPS_M:
        return AxisAlignment(relation=RELATION_AMBIGUOUS, axis=None, edge=None)

    if delta_u > delta_v * _AXIS_DOMINANCE_RATIO:
        # Beside: predominantly horizontal displacement -> vertical match,
        # whichever of top/bottom is already closer.
        dist_top = abs(source.v_max - target.v_max)
        dist_bottom = abs(source.v_min - target.v_min)
        edge = EDGE_TOP if dist_top <= dist_bottom else EDGE_BOTTOM
        return AxisAlignment(relation=RELATION_BESIDE, axis=AXIS_VERTICAL, edge=edge)

    if delta_v > delta_u * _AXIS_DOMINANCE_RATIO:
        # Stacked: predominantly vertical displacement -> horizontal match,
        # whichever of left/right is already closer.
        dist_left = abs(source.u_min - target.u_min)
        dist_right = abs(source.u_max - target.u_max)
        edge = EDGE_LEFT if dist_left <= dist_right else EDGE_RIGHT
        return AxisAlignment(relation=RELATION_STACKED, axis=AXIS_HORIZONTAL, edge=edge)

    return AxisAlignment(relation=RELATION_AMBIGUOUS, axis=None, edge=None)
