# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Align Objects operator core (copy-paste-object-behaviour ticket 04,
`docs/adr/0006-facade-alignment-feedback-scope.md`).

Wires ticket 01's pure axis-inference engine (``shared/alignment_geometry.py``
``classify_relative_axis``) into two real, already-placed openings: the
earlier-selected object (``source``, the one that moves) and the active
object (``target``, Blender's own align/snap-to-active convention - the
selected non-active object moves onto the active one). No axis is picked by
the caller; ``classify_relative_axis`` decides beside (vertical match,
top/bottom edge) vs stacked (horizontal match, left/right edge) vs ambiguous
from the pair's own relative position.

Both openings' footprints are projected into the **source's own host wall
frame** (`shared/facade_surface_resolver.resolve_surface_context`) before
classification - this is what lets a vertical match legitimately target an
opening on a *different* WallSurface (the frame's vertical axis approximates
world height for any roughly-vertical wall) while a horizontal match, whose
meaning depends entirely on the source wall's own horizontal axis, is
rejected outright when the target sits on a different WallSurface (the
ADR 0006 LoD3-safety guard). The move itself is then just the delta between
the two projected rects along the inferred axis, applied as a translation
along the source frame's own horizontal/vertical axis - never its normal -
so the source structurally cannot leave its own host wall's plane.

`sync_opening_to_ring` (`shared/ring_opening_sync.py`) re-cuts the source's
own wall interior ring to match immediately after, so no desync is left for
`ops/edit_exit_validation.py`'s Edit-Mode-transition handler to catch later.
"""

from __future__ import annotations

from dataclasses import dataclass

from mathutils import Matrix, Vector

from .alignment_geometry import (
    AXIS_HORIZONTAL,
    EDGE_BOTTOM,
    EDGE_LEFT,
    EDGE_RIGHT,
    EDGE_TOP,
    RELATION_AMBIGUOUS,
    AlignAxis,
    Edge,
    OpeningRect,
    classify_relative_axis,
)
from .facade_surface_resolver import resolve_surface_context
from .ring_opening_sync import (
    building_ancestor,
    find_ring_opening_pairs,
    is_door_opening,
    is_ring_paired_opening,
    is_window_opening,
    pairs_for_wall_children,
    sync_opening_to_ring,
    wall_interior_ring_faces,
)


@dataclass(frozen=True)
class AlignResult:
    """What actually happened - reported back to the user by the operator."""

    axis: AlignAxis        # "vertical" | "horizontal"
    edge: Edge               # "top" | "bottom" | "left" | "right"
    delta_m: float            # signed distance the source moved along that axis
    synced: bool               # whether a ring/opening pair was found and re-cut


def opening_subtype(obj) -> str:
    """"Window"/"Door" for the two ticket 01 cares about, "" otherwise."""
    if is_window_opening(obj):
        return "Window"
    if is_door_opening(obj):
        return "Door"
    return ""


def _host_wall(obj):
    wall = getattr(obj, "parent", None)
    if wall is None or getattr(wall, "type", None) != "MESH":
        raise ValueError(f"'{obj.name}' has no host WallSurface parent")
    return wall


def _exterior_face_index(wall_obj) -> int:
    """The wall's own largest non-``gml:interior``-ring face - reusing
    `ring_opening_sync.wall_interior_ring_faces` as the one place "is this
    face a ring" is decided, rather than re-deriving the same
    material/``Interior``-flag check here. All of a WallSurface's own faces
    are coplanar (ADR 0004), so any one of them yields the same local
    horizontal/vertical/normal orientation - Align Objects only ever
    measures *differences* between two projections through the same origin,
    which cancels out which face supplied it. Preferring the largest
    non-ring face just keeps the resolved frame's reported width/height
    meaningful, and avoids a degenerate/zero-area ring face on a wall that
    (not yet possible today, but not to rely on) carries no other face.
    """
    mesh = wall_obj.data
    if mesh is None or not mesh.polygons:
        raise ValueError(f"'{wall_obj.name}' has no geometry")
    ring_face_indices = {face_index for face_index, _verts, _link in wall_interior_ring_faces(wall_obj)}
    candidates = [i for i in range(len(mesh.polygons)) if i not in ring_face_indices]
    candidates = candidates or list(range(len(mesh.polygons)))
    return max(candidates, key=lambda i: mesh.polygons[i].area)


def _footprint_rect(surface, obj, subtype: str) -> OpeningRect:
    origin = Vector(surface.origin_world)
    h_axis = Vector(surface.horizontal_axis_world)
    v_axis = Vector(surface.vertical_axis_world)
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    if not points:
        raise ValueError(f"'{obj.name}' has no geometry")
    us = [(point - origin).dot(h_axis) for point in points]
    vs = [(point - origin).dot(v_axis) for point in points]
    return OpeningRect(u_min=min(us), u_max=max(us), v_min=min(vs), v_max=max(vs), subtype=subtype)


def _sync_source_ring(source_obj, source_wall) -> bool:
    """Re-cut whichever ring is paired with ``source_obj`` on its own wall,
    if any. Tries building-wide (`find_ring_opening_pairs`) first - unlike
    the edit-exit handler's strictly-local `pairs_for_wall_children`, this
    also finds a floating Door (`pairs_for_wall_children` is Window-only, by
    its own docstring); Align Objects is a one-shot, user-triggered
    operator, not the high-frequency automatic mode-change handler that
    local scoping protects. Falls back to `pairs_for_wall_children`'s own
    (more permissive, parent-object-based) pairing when the building-wide
    lookup finds nothing - `find_ring_opening_pairs` requires a matching
    ``filling_parent_surface_id``, which a legacy/imported opening can carry
    stale or missing even though it is still genuinely ``fill.parent is
    wall``."""
    building = building_ancestor(source_wall)
    pairs = find_ring_opening_pairs(building) if building is not None else []
    pair = next((p for p in pairs if p.opening is source_obj), None)
    if pair is None:
        pairs = pairs_for_wall_children(source_wall)
        pair = next((p for p in pairs if p.opening is source_obj), None)
    if pair is None:
        return False
    sync_opening_to_ring(pair)
    return True


def align_source_to_target(source_obj, target_obj) -> AlignResult:
    """Move ``source_obj`` onto ``target_obj`` along ticket 01's inferred
    axis and re-sync its wall ring. Raises ``ValueError`` (never guesses)
    for: an ambiguous (diagonal) pair, or a horizontal (left/right) match
    whose target sits on a different WallSurface than the source (ADR 0006's
    deliberate LoD3-safety guard).
    """
    if source_obj is None or target_obj is None or source_obj is target_obj:
        raise ValueError("Align Objects requires two different openings selected")
    if not is_ring_paired_opening(source_obj):
        raise ValueError(f"'{source_obj.name}' is not a ring-paired opening (Window or floating Door)")
    if not is_ring_paired_opening(target_obj):
        raise ValueError(f"'{target_obj.name}' is not a ring-paired opening (Window or floating Door)")

    source_wall = _host_wall(source_obj)
    target_wall = _host_wall(target_obj)

    surface = resolve_surface_context(source_wall, _exterior_face_index(source_wall))
    source_rect = _footprint_rect(surface, source_obj, opening_subtype(source_obj))
    target_rect = _footprint_rect(surface, target_obj, opening_subtype(target_obj))

    alignment = classify_relative_axis(source_rect, target_rect)
    if alignment.relation == RELATION_AMBIGUOUS:
        raise ValueError(
            f"'{source_obj.name}' and '{target_obj.name}' are diagonal to each other - "
            "Align Objects needs a clearly beside (vertical match) or stacked "
            "(horizontal match) pair"
        )

    if alignment.axis == AXIS_HORIZONTAL and target_wall is not source_wall:
        raise ValueError(
            f"Horizontal match requires '{target_obj.name}' to be on the same "
            f"WallSurface as '{source_obj.name}' ('{target_wall.name}' vs '{source_wall.name}')"
        )

    if alignment.edge == EDGE_TOP:
        delta = target_rect.v_max - source_rect.v_max
        world_delta = Vector(surface.vertical_axis_world) * delta
    elif alignment.edge == EDGE_BOTTOM:
        delta = target_rect.v_min - source_rect.v_min
        world_delta = Vector(surface.vertical_axis_world) * delta
    elif alignment.edge == EDGE_LEFT:
        delta = target_rect.u_min - source_rect.u_min
        world_delta = Vector(surface.horizontal_axis_world) * delta
    else:  # EDGE_RIGHT
        delta = target_rect.u_max - source_rect.u_max
        world_delta = Vector(surface.horizontal_axis_world) * delta

    source_obj.matrix_world = Matrix.Translation(world_delta) @ source_obj.matrix_world

    synced = _sync_source_ring(source_obj, source_wall)

    return AlignResult(axis=alignment.axis, edge=alignment.edge, delta_m=delta, synced=synced)
