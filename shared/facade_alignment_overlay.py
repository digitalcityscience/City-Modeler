# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Alignment overlay wiring + GPU render helper (copy-paste-object-behaviour
ticket 02). Wires ticket 01's pure ``shared/alignment_geometry.py`` engine
into a live wall-local candidate footprint - reading the wall's own Window/Door
children (the placement kernel's ``fill.parent = wall`` scheme,
``shared/facade_placement.py``) as same-WallSurface neighbours - and draws the
result as 2D screen-space GPU lines + ``blf`` labels from a running modal's
``draw_callback``.

This is the one place the ``draw_callback``-facing render helper lives: ticket
03 (Semantic Stamp's own overlay) calls `compute_alignment_overlay` and
`draw_alignment_overlay` directly rather than re-deriving either the wiring or
the drawing, per ``docs/adr/0006-facade-alignment-feedback-scope.md`` and
CONTEXT.md's "Alignment guide" / "Neighbour distance readout" glossary
entries.
"""

from __future__ import annotations

from dataclasses import dataclass

import blf
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from .alignment_geometry import (
    CENTRE_AXIS_U,
    EDGE_BOTTOM,
    EDGE_BOUNDS,
    EDGE_CENTRE,
    EDGE_COLLISION,
    EDGE_LEFT,
    EDGE_RIGHT,
    EDGE_TOP,
    EdgeMatch,
    NeighbourGap,
    OpeningRect,
    WallBounds,
    classify_alignment,
    nearest_same_subtype_gap,
)
from .facade_placement import SurfaceContext
from .ring_opening_sync import is_door_opening, is_window_opening

_STATE_COLORS = {
    "green": (0.15, 0.9, 0.25, 0.95),
    "yellow": (0.95, 0.85, 0.1, 0.95),
    "red": (0.95, 0.15, 0.15, 0.95),
}

_GUIDE_LINE_WIDTH = 2.0
_LABEL_FONT_ID = 0
_LABEL_FONT_SIZE = 13
_LABEL_COLOR = (1.0, 1.0, 1.0, 0.95)

# Screen-pixel offsets from the anchor point (the drag cursor) each label is
# drawn at - the gap label above-right, the size label below-right, so
# neither overlaps the cursor or the other.
_LABEL_OFFSET_X = 12
_GAP_LABEL_OFFSET_Y = 14
_SIZE_LABEL_OFFSET_Y = -20


@dataclass(frozen=True)
class AlignmentOverlay:
    """Everything one ``draw_callback`` tick needs to render the alignment
    overlay for a single in-progress placement: ticket 01's classification
    results, plus the surface/candidate/neighbour footprints needed to turn
    those results back into world- and screen-space lines."""

    surface: SurfaceContext
    candidate: OpeningRect
    neighbours: list[OpeningRect]
    matches: list[EdgeMatch]
    gap: NeighbourGap | None


# --------------------------------------------------------------------------
# Wiring: real wall/opening objects -> ticket 01's plain-float rects
# --------------------------------------------------------------------------
def wall_bounds(surface: SurfaceContext) -> WallBounds:
    """A ``SurfaceContext``'s own footprint as a ``WallBounds`` - always
    ``(0, width, 0, height)`` since ``origin_world`` is already that frame's
    min-uv corner."""
    return WallBounds(u_min=0.0, u_max=surface.width, v_min=0.0, v_max=surface.height)


def _rect_from_world_points(surface: SurfaceContext, points, subtype: str) -> OpeningRect:
    """Same projection `facade_placement.surface_uv_from_world_point` does,
    but kept in metres (no divide by ``surface.width``/``height``) and over
    many points at once - reusing that fractional helper here would mean
    dividing then immediately re-multiplying by the same width/height for
    every vertex, for no benefit."""
    origin = Vector(surface.origin_world)
    h_axis = Vector(surface.horizontal_axis_world)
    v_axis = Vector(surface.vertical_axis_world)
    us = [(Vector(point) - origin).dot(h_axis) for point in points]
    vs = [(Vector(point) - origin).dot(v_axis) for point in points]
    return OpeningRect(u_min=min(us), u_max=max(us), v_min=min(vs), v_max=max(vs), subtype=subtype)


def gather_same_wall_neighbours(surface: SurfaceContext, wall_obj) -> list[OpeningRect]:
    """Every Window/Door already parented to ``wall_obj`` (the placement
    kernel's own parenting scheme - ``fill.parent = wall``,
    ``shared/facade_placement.py``), converted to footprints in this wall's
    local ``(u, v)`` metres frame - ticket 01's ``classify_alignment``/
    ``nearest_same_subtype_gap`` input."""
    neighbours = []
    for child in wall_obj.children:
        if getattr(child, "type", None) != 'MESH' or child.data is None:
            continue
        if is_window_opening(child):
            subtype = "Window"
        elif is_door_opening(child):
            subtype = "Door"
        else:
            continue
        points = [child.matrix_world @ vertex.co for vertex in child.data.vertices]
        if not points:
            continue
        neighbours.append(_rect_from_world_points(surface, points, subtype))
    return neighbours


def candidate_rect(surface: SurfaceContext, position_uv, width: float, height: float, subtype: str) -> OpeningRect:
    """The in-progress placement's own footprint, in the same local metres
    frame ``gather_same_wall_neighbours`` returns its neighbours in -
    ``position_uv`` is the fractional centre `shared/facade_placement.py`'s
    ``PlacementRequest`` uses; ``width``/``height`` are already metres."""
    u, v = position_uv
    centre_u = float(u) * surface.width
    centre_v = float(v) * surface.height
    half_w, half_h = width / 2.0, height / 2.0
    return OpeningRect(
        u_min=centre_u - half_w, u_max=centre_u + half_w,
        v_min=centre_v - half_h, v_max=centre_v + half_h,
        subtype=subtype,
    )


def compute_alignment_overlay(
    surface: SurfaceContext,
    wall_obj,
    position_uv,
    width: float,
    height: float,
    subtype: str,
) -> AlignmentOverlay:
    """The one call Draw's and Stamp's operators make to compute the overlay
    for a candidate footprint - Draw calls it fresh every ``draw_callback``,
    Stamp calls it once per mouse-move/placement and caches the result for
    ``draw_callback`` to render - and: gather ``wall_obj``'s existing
    openings, build the candidate's own footprint, and run both of ticket
    01's classifications against them."""
    neighbours = gather_same_wall_neighbours(surface, wall_obj)
    candidate = candidate_rect(surface, position_uv, width, height, subtype)
    matches = classify_alignment(candidate, neighbours, wall_bounds(surface))
    gap = nearest_same_subtype_gap(candidate, neighbours)
    return AlignmentOverlay(
        surface=surface, candidate=candidate, neighbours=neighbours, matches=matches, gap=gap,
    )


# --------------------------------------------------------------------------
# Rendering: overlay result -> 2D screen-space GPU lines + blf labels
# --------------------------------------------------------------------------
def _world_point(surface: SurfaceContext, u_m: float, v_m: float) -> Vector:
    return (
        Vector(surface.origin_world)
        + Vector(surface.horizontal_axis_world) * u_m
        + Vector(surface.vertical_axis_world) * v_m
    )


def _screen_point(context, world_pt):
    region = context.region
    rv3d = context.space_data.region_3d
    return view3d_utils.location_3d_to_region_2d(region, rv3d, world_pt)


def _guide_segment_uv(overlay: AlignmentOverlay, match: EdgeMatch):
    """The wall-local ``(u, v)`` polyline (in metres) for one ``EdgeMatch``:
    a straight-edge or centre match spans the union of the candidate's and
    matched neighbour's extent along the edge's own axis (so the guide
    visibly connects both footprints, not just one point on each); a
    collision outlines the overlap; an out-of-bounds match outlines the wall
    itself. ``None`` if this match can't be turned into a line (shouldn't
    happen for any kind ``classify_alignment`` emits, but keeps rendering
    defensive against a future edge kind)."""
    candidate = overlay.candidate

    if match.edge == EDGE_BOUNDS:
        bounds = wall_bounds(overlay.surface)
        return [
            (bounds.u_min, bounds.v_min), (bounds.u_max, bounds.v_min),
            (bounds.u_max, bounds.v_max), (bounds.u_min, bounds.v_max),
            (bounds.u_min, bounds.v_min),
        ]

    if match.neighbour_index is None or not (0 <= match.neighbour_index < len(overlay.neighbours)):
        return None
    neighbour = overlay.neighbours[match.neighbour_index]

    if match.edge == EDGE_COLLISION:
        u0 = max(candidate.u_min, neighbour.u_min)
        u1 = min(candidate.u_max, neighbour.u_max)
        v0 = max(candidate.v_min, neighbour.v_min)
        v1 = min(candidate.v_max, neighbour.v_max)
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)]

    if match.edge in (EDGE_LEFT, EDGE_RIGHT):
        u = candidate.u_min if match.edge == EDGE_LEFT else candidate.u_max
        v0 = min(candidate.v_min, neighbour.v_min)
        v1 = max(candidate.v_max, neighbour.v_max)
        return [(u, v0), (u, v1)]

    if match.edge in (EDGE_TOP, EDGE_BOTTOM):
        v = candidate.v_max if match.edge == EDGE_TOP else candidate.v_min
        u0 = min(candidate.u_min, neighbour.u_min)
        u1 = max(candidate.u_max, neighbour.u_max)
        return [(u0, v), (u1, v)]

    if match.edge == EDGE_CENTRE:
        if match.axis == CENTRE_AXIS_U:
            u = candidate.u_centre
            v0 = min(candidate.v_min, neighbour.v_min)
            v1 = max(candidate.v_max, neighbour.v_max)
            return [(u, v0), (u, v1)]
        v = candidate.v_centre
        u0 = min(candidate.u_min, neighbour.u_min)
        u1 = max(candidate.u_max, neighbour.u_max)
        return [(u0, v), (u1, v)]

    return None


def draw_guides(context, overlay: AlignmentOverlay) -> None:
    """Draw every match in ``overlay.matches`` as a colour-coded line batch,
    projected through the wall's own frame into 2D screen space (matching
    ``Openings_Cutter``'s existing ``POST_PIXEL`` snap-marker convention)."""
    if not overlay.matches:
        return

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.line_width_set(_GUIDE_LINE_WIDTH)
    shader.bind()
    try:
        for match in overlay.matches:
            points_uv = _guide_segment_uv(overlay, match)
            if not points_uv or len(points_uv) < 2:
                continue
            screen_pts = []
            for u_m, v_m in points_uv:
                screen_pt = _screen_point(context, _world_point(overlay.surface, u_m, v_m))
                if screen_pt is None:
                    screen_pts = []
                    break
                screen_pts.append((screen_pt[0], screen_pt[1]))
            if len(screen_pts) < 2:
                continue
            indices = [(i, i + 1) for i in range(len(screen_pts) - 1)]
            batch = batch_for_shader(shader, "LINES", {"pos": screen_pts}, indices=indices)
            shader.uniform_float("color", _STATE_COLORS[match.state])
            batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")


def _draw_text(x: float, y: float, text: str) -> None:
    blf.position(_LABEL_FONT_ID, x, y, 0)
    blf.size(_LABEL_FONT_ID, _LABEL_FONT_SIZE)
    blf.color(_LABEL_FONT_ID, *_LABEL_COLOR)
    blf.draw(_LABEL_FONT_ID, text)


def draw_gap_label(overlay: AlignmentOverlay, anchor_screen) -> None:
    """The neighbour-distance readout - shown regardless of the guides'
    colour state, per ADR 0006 ("a spacing fact, not a correctness
    judgement"). No-op if there is no same-subtype neighbour yet, or the
    anchor point is off-screen."""
    if overlay.gap is None or anchor_screen is None:
        return
    x, y = anchor_screen
    text = f"Δu {overlay.gap.gap_u:.2f} m   Δv {overlay.gap.gap_v:.2f} m"
    _draw_text(x + _LABEL_OFFSET_X, y + _GAP_LABEL_OFFSET_Y, text)


def draw_size_label(anchor_screen, width_m: float, height_m: float) -> None:
    """The live width/height readout (Draw only - Stamp's element size is
    fixed by its captured source, ticket 03)."""
    if anchor_screen is None:
        return
    x, y = anchor_screen
    _draw_text(x + _LABEL_OFFSET_X, y + _SIZE_LABEL_OFFSET_Y, f"{width_m:.2f} m x {height_m:.2f} m")


def draw_alignment_overlay(
    context,
    overlay: AlignmentOverlay,
    anchor_screen,
    *,
    width_m: float | None = None,
    height_m: float | None = None,
) -> None:
    """The one call a ``draw_callback`` makes to render everything ticket 02
    asks for: colour-coded guide lines, the neighbour-distance label, and
    (only when ``width_m``/``height_m`` are given - Draw's live drag, not
    Stamp's fixed-size ghost) the live size readout."""
    draw_guides(context, overlay)
    draw_gap_label(overlay, anchor_screen)
    if width_m is not None and height_m is not None:
        draw_size_label(anchor_screen, width_m, height_m)
