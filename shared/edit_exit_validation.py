# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Edit-exit opening/ring integrity core (facade-authoring ticket 04, design
doc section 11.1).

Manual mesh edits can silently desync an opening fill from its wall's
interior ring (ADR 0004 D1). This module answers, in change-directed terms,
"what should happen when a relevant object leaves Edit Mode": snapshot its
opening/ring-relevant geometry on entry, diff it on exit, and decide which
side (if either) changed - **never** a blind one-direction Sync.

Split from the Blender-side handler (``ops/edit_exit_validation.py``) so the
diffing/decision rules are plain-data and unit-testable without a running
Blender: ``opening_state_changed``/``changed_ring_keys``/``resolve_action``
take and return plain tuples/dicts/strings, no ``bpy``. Only
``is_relevant_for_edit_exit``/``snapshot_relevant_state`` at the bottom touch
live mesh data, mirroring the lazy-bpy-import convention already used by
``shared/facade_surface_resolver.py``.

**Scope (ticket 04 bullet 5, extended by facade-authoring ticket 06):** an
object is only ever snapshotted/diffed if it is itself a ring-paired opening
fill (every Window, plus a floating Door - see
``ring_opening_sync.is_ring_paired_opening``) or a wall carrying at least
one ``gml:interior`` ring face - checked once, per object, in
``is_relevant_for_edit_exit``. Anything else (GroundSurface, an
opening-less RoofSurface, a ground-touching Door, non-mesh objects, ...) is
never snapshotted, so exit never has anything to diff for it - no
full-model scan is ever triggered by an unrelated edit.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from .ring_opening_sync import (
    RING_OPENING_SYNC_TOLERANCE_M,
    is_ring_paired_opening,
    opening_candidate_faces,
    wall_has_interior_ring,
    wall_interior_ring_faces,
)

# Point3 = (x, y, z) plain tuple of float - deliberately not mathutils.Vector
# so the diff functions below stay bpy-free (see module docstring).
Point3 = tuple


class OpeningSnapshot(NamedTuple):
    """Whole-mesh, world-space vertex snapshot of a Window opening fill
    object. Fill objects are small and dedicated (one element each), so
    whole-mesh comparison is simpler than face-by-face and just as precise."""

    verts: tuple


class WallRingSnapshot(NamedTuple):
    """Per-ring-face, world-space vertex snapshot of a wall's own
    ``gml:interior`` ring faces - **not** the whole wall mesh, so an edit
    elsewhere on the wall (its exterior boundary, an unrelated vertex) never
    registers as a "ring changed" here. Keyed by opening-link id (ADR 0004
    D2) when the ring face carries one, else a legacy ``idx:<face_index>``
    fallback key - the same key both sides of a pairing must agree on."""

    rings: dict


# The four outcomes ``resolve_action`` can return - ticket 04's own labels
# for the change-directed rule ("opening-only -> auto Sync Opening -> Ring",
# etc). Exposed as constants so the handler (``ops/edit_exit_validation.py``)
# switches on them instead of re-deriving the same if/elif shape.
ACTION_NONE = "none"
ACTION_SYNC_OPENING_TO_RING = "sync_opening_to_ring"
ACTION_SYNC_RING_TO_OPENING = "sync_ring_to_opening"
ACTION_AMBIGUOUS = "ambiguous"


def resolve_action(opening_changed: bool, ring_changed: bool) -> str:
    """The change-directed decision itself (design doc section 11.1 / ticket
    04 bullets 2-3), as a pure function of "which side changed" - no bpy, no
    ``RingOpeningPair``, so every branch is unit-testable without Blender.
    Opening-only means the opening is now authoritative -> recut the ring to
    match it (``sync_opening_to_ring``); ring-only means the ring is now
    authoritative -> move the opening back onto it (``sync_ring_to_opening``);
    both changed is never resolved here - the caller must halt and ask."""
    if opening_changed and ring_changed:
        return ACTION_AMBIGUOUS
    if opening_changed:
        return ACTION_SYNC_OPENING_TO_RING
    if ring_changed:
        return ACTION_SYNC_RING_TO_OPENING
    return ACTION_NONE


def ring_key(link: str, face_index: int) -> str:
    """The snapshot/lookup key for one ring face: its own opening-link id
    (ADR 0004 D2) when it carries one, else a legacy ``idx:<face_index>``
    fallback. Public because both the snapshot below and the handler's
    pair-to-key lookup (``ops/edit_exit_validation.py``) must derive this
    key the exact same way from the exact same ring face - deriving it from
    anything else (e.g. the *opening's* own link) can disagree with this
    when a pairing came from the vertex-identity fallback rather than a
    link match."""
    return link if link else f"idx:{face_index}"


def _dist(a: Point3, b: Point3) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _verts_changed(before, after, tol: float) -> bool:
    if len(before) != len(after):
        return True
    return any(_dist(a, b) > tol for a, b in zip(before, after))


def opening_state_changed(
    before: OpeningSnapshot, after: OpeningSnapshot, tol: float = RING_OPENING_SYNC_TOLERANCE_M
) -> bool:
    """True if the opening fill's own geometry moved (or its topology
    changed - vertex count differs) by more than ``tol`` between the two
    snapshots. Below ``tol`` is treated as float/round-trip noise, not a
    user edit - the same tolerance the exporter's sync guard uses."""
    return _verts_changed(before.verts, after.verts, tol)


def changed_ring_keys(
    before: WallRingSnapshot, after: WallRingSnapshot, tol: float = RING_OPENING_SYNC_TOLERANCE_M
) -> set:
    """The set of ring keys whose geometry differs by more than ``tol``
    between the two snapshots (a ring face that appeared or disappeared
    counts as changed too). Empty if nothing on this wall's interior rings
    moved."""
    changed = set()
    all_keys = set(before.rings) | set(after.rings)
    for key in all_keys:
        b = before.rings.get(key)
        a = after.rings.get(key)
        if b is None or a is None:
            changed.add(key)
            continue
        if _verts_changed(b, a, tol):
            changed.add(key)
    return changed


# --------------------------------------------------------------------------
# Live-mesh snapshotting (bpy touched only here, mirroring
# facade_surface_resolver's lazy-import convention).
# --------------------------------------------------------------------------
def is_relevant_for_edit_exit(obj) -> bool:
    """True only for a ring-paired opening fill (Window, or a floating Door),
    or a wall object carrying at least one ``gml:interior`` ring face -
    ticket 04's strict local scope, extended by ticket 06."""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if is_ring_paired_opening(obj):
        return True
    return wall_has_interior_ring(obj)


def snapshot_relevant_state(obj):
    """Snapshot ``obj``'s opening/ring-relevant geometry right now, or
    ``None`` if ``obj`` is out of scope (see ``is_relevant_for_edit_exit`` -
    called here too, so this is the single source of truth for scope).
    Returns ``("opening", OpeningSnapshot)`` or ``("wall", WallRingSnapshot)``.
    """
    if not is_relevant_for_edit_exit(obj):
        return None
    if is_ring_paired_opening(obj):
        verts = []
        for _face_idx, face_verts in opening_candidate_faces(obj):
            verts.extend(tuple(v) for v in face_verts)
        return "opening", OpeningSnapshot(verts=tuple(verts))

    rings = {}
    for face_index, face_verts, link in wall_interior_ring_faces(obj):
        rings[ring_key(link, face_index)] = tuple(tuple(v) for v in face_verts)
    return "wall", WallRingSnapshot(rings=rings)
