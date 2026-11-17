# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Wall-ring <-> Window-opening desync detection and bidirectional sync
(ticket 09 of docs/agent/semantic-hierarchy).

Once ticket 06 explodes a flat Building mesh into WallSurface/Window
objects, a wall's ``gml:interior`` ring and its Window's matching outward
face are two copies of the same outline living in two different meshes
(README.md decision 5/section 1.3). Move one in the viewport and the other
stays put - the model still passes schema validation while being
geometrically wrong. This module finds that mismatch before export and
offers a user-driven, bidirectional fix. Nothing in this module runs
automatically; every function here is only ever invoked by an explicit
export-time check or an explicit user-triggered operator (``ops/
ring_opening_sync.py``).

**Open question 1 (README.md section 6): how is a ring paired with its
opening?** Answered by direct inspection (docs/agent/semantic-hierarchy
ticket 09 investigation) - there is no stored ring-to-opening id anywhere
in the pipeline. ``filling_parent_surface_id``/``opening_surface_id``
(``io/reader/materials.py``) is a *wall-level* link only: which WallSurface
a Window's faces belong to, never which of that wall's several rings is
"its" ring. The importer creates a wall's ``gml:interior`` ring face and
its Window's matching outward-facing frame face from the *same* source
``gml:Polygon`` vertex list, so at import time the two are byte-identical
in world space (verified against the Railway LoD3 fixture: all 15 wall
rings have an exact vertex match to exactly one Window sub-polygon).
**Vertex identity was therefore the original pairing key this module
used**: for a given wall, its interior-ring faces were matched to its
child Window objects by nearest vertex-loop distance (``_best_alignment``
below).

**Facade-authoring ticket 04 supersedes that for authored openings.**
ADR 0004 D2 mints an explicit ``cgml_opening_link`` id (``OPENING_LINK_KEY``
below) at authoring time (``shared/facade_placement.py``), written to both
the fill object and the wall's interior-ring material. Pairing now tries
the opening-link id **first** - an exact, drift-proof match with no
distance computation needed to pick the *pair*, only to compute
``distance``/``delta`` for the sync tolerance check - and only falls back
to nearest-vertex-loop distance for rings/openings that carry no link at
all (legacy imported data, pre-dating ticket 04). A linked ring/opening
never loses its match to an unrelated nearest-distance guess, narrowed
first to only the Window objects actually parented to that wall (via
``filling_parent_surface_id``) so unrelated walls never cross-match.

Doors were originally excluded **by construction**, not by omission: a
ground-touching Door is cut directly into its own wall's exterior boundary
rather than creating a ``gml:interior`` hole (verified: such a wall has a
notched, non-rectangular exterior boundary and owns no Door-specific
interior ring). That Door has no ring to pair with and can never be
flagged as desynced.

**Facade-authoring ticket 06 adds one exception:** a *floating* Door (one
that doesn't reach the wall's lower edge) uses the same
``InteriorRingOpeningStrategy`` a Window does - ring + fill, opening-link
minted the same way - and so genuinely is a pairing candidate. Ground vs
floating is decided at authoring time (ADR 0004 D3); this module tells the
two apart cheaply, per-object, via the presence of the opening-link id
(``is_ring_paired_opening`` below) rather than re-deriving the wall's
geometry - a ground-touching Door (``ExteriorNotchStrategy``, not yet
implemented) never mints that link, so it stays correctly excluded.

**Open question 2: what are the rings *inside* a Window's own polygon?**
Verified by inspection: each Window's outward-facing frame face has its
own, smaller, concentric interior ring (a frame reveal / glazing
boundary), entirely internal to that Window's own geometry. It has no
relationship to the wall it fills and is correctly ignored here - see
``opening_candidate_faces``.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import bpy
from mathutils import Vector

from .object_helpers import breadth_first_descendants, face_material
from .semantic_tree import is_boundary_surface, is_opening

# 1 cm. Chosen to absorb float round-trip noise (local<->world matrix
# multiplication, mesh copy/merge during import/export) without absorbing
# any edit a user could plausibly make in the viewport - a deliberate,
# named tolerance, not an accidental float ``==``. Exposed as a module
# constant so the export check and both sync operators agree on exactly
# one definition of "in sync".
RING_OPENING_SYNC_TOLERANCE_M = 0.01

# The authoritative fill<->ring pairing id (ADR 0004 D2). Defined here, the
# lightest module that needs it (only bpy/mathutils, no Openings_Cutter
# import chain), so both exporters, the edit-exit handler, and the
# placement kernel (``shared/facade_placement.py``, which mints it) all
# import this single definition rather than each carrying their own literal.
OPENING_LINK_KEY = "cgml_opening_link"

_WINDOW_FEATURE_TYPES = {"window", "windowsurface"}
_DOOR_FEATURE_TYPES = {"door", "doorsurface"}


def is_window_opening(obj) -> bool:
    """True only for Window/WindowSurface openings - never Door/DoorSurface.
    Every Window is ring+fill by construction (see module docstring)."""
    if not is_opening(obj):
        return False
    feat = str(obj.get("cgml3_feature") or "").strip().lower()
    return feat in _WINDOW_FEATURE_TYPES


def is_door_opening(obj) -> bool:
    """True for Door/DoorSurface openings, regardless of ring/notch topology."""
    if not is_opening(obj):
        return False
    feat = str(obj.get("cgml3_feature") or "").strip().lower()
    return feat in _DOOR_FEATURE_TYPES


def is_ring_paired_opening(obj) -> bool:
    """True for any opening that owns a wall interior ring to pair with:
    every Window (always ring+fill), plus a *floating* Door authored via the
    same ``InteriorRingOpeningStrategy`` (facade-authoring ticket 06) -
    identified by the opening-link id (ADR 0004 D2) that only that strategy
    ever mints. A ground-touching Door (``ExteriorNotchStrategy``) carries no
    ring and no link, so it stays excluded, same as before ticket 06."""
    if is_window_opening(obj):
        return True
    if is_door_opening(obj):
        return bool(str(obj.get(OPENING_LINK_KEY) or "").strip())
    return False


def _collect_semantic_children(building_obj):
    """Every descendant boundary-surface/opening MESH object of
    ``building_obj``, not descending into nested Building/BuildingPart
    parts. The common breadth-first walk lives in ``object_helpers``; this
    function keeps only the semantic collection and boundary rules."""
    collected = []
    for child in breadth_first_descendants(
        building_obj,
        should_descend=lambda candidate: str(
            candidate.get("cgml3_feature") or ""
        ).strip() not in ("Building", "BuildingPart"),
    ):
        feat = str(child.get("cgml3_feature") or "").strip()
        if feat in ("Building", "BuildingPart"):
            continue
        if getattr(child, "type", None) == "MESH" and (
            is_boundary_surface(child) or is_opening(child)
        ):
            collected.append(child)
    return collected


def building_ancestor(obj):
    """Walk up ``obj.parent`` until a Building/BuildingPart is found (``obj``
    itself qualifies). Returns ``None`` if ``obj`` has no such ancestor."""
    cur = obj
    while cur is not None:
        feat = str(cur.get("cgml3_feature") or "").strip()
        if feat in ("Building", "BuildingPart"):
            return cur
        cur = cur.parent
    return None


_CLOSING_VERTEX_EPS = 1e-6


def _dedupe_closing_vertex(verts: list) -> list:
    """Drop a trailing vertex that only repeats the first one.

    CityGML ``posList`` rings always repeat their first point as their
    last (a closed-ring convention); the importer carries that repeat
    through as a genuine extra mesh vertex rather than deduplicating it,
    so an "n-cornered" face can have n+1 entries in ``face.vertices``. Left
    in, that extra entry breaks the cyclic-rotation alignment in
    ``_best_alignment`` (it has no independent counterpart to rotate onto
    in the other loop), so it must be stripped before any comparison.
    """
    if len(verts) > 1 and (verts[0] - verts[-1]).length <= _CLOSING_VERTEX_EPS:
        return verts[:-1]
    return verts


def _world_verts(obj, face) -> list:
    mw = obj.matrix_world
    mesh = obj.data
    verts = [mw @ mesh.vertices[vi].co for vi in face.vertices]
    return _dedupe_closing_vertex(verts)


def _best_alignment(a_verts, b_verts):
    """Return ``(distance, delta)`` for the best-aligned correspondence
    between two closed vertex loops of equal length.

    The two loops may have been walked from a different starting vertex or
    in the opposite winding direction and still describe the same polygon,
    so every cyclic rotation and both directions are tried. ``distance`` is
    the max per-vertex gap under the winning alignment (0 when the loops
    are identical). ``delta`` is the average ``a_i - b_i`` under that same
    alignment - the rigid world-space translation that would move loop
    ``b`` onto loop ``a``.
    """
    n = len(a_verts)
    if n == 0 or n != len(b_verts):
        return math.inf, Vector((0.0, 0.0, 0.0))

    best_dist = math.inf
    best_delta = Vector((0.0, 0.0, 0.0))
    for do_reverse in (False, True):
        seq = list(reversed(b_verts)) if do_reverse else list(b_verts)
        for start in range(n):
            rotated = seq[start:] + seq[:start]
            diffs = [a - b for a, b in zip(a_verts, rotated)]
            dist = max(d.length for d in diffs)
            if dist < best_dist:
                best_dist = dist
                total = Vector((0.0, 0.0, 0.0))
                for d in diffs:
                    total += d
                best_delta = total / n
    return best_dist, best_delta


def _loop_normal(verts) -> Vector:
    n = Vector((0.0, 0.0, 0.0))
    for a, b in zip(verts, verts[1:] + verts[:1]):
        n += Vector(((a.y - b.y) * (a.z + b.z), (a.z - b.z) * (a.x + b.x), (a.x - b.x) * (a.y + b.y)))
    return n.normalized() if n.length > 0.0 else n


def _reference_cap(ring_verts, opening_faces):
    """``(face_index, distance, delta)`` of the opening's reference for ``ring_verts``, or None.

    Candidates are the opening's caps (faces that are a translated copy of the
    ring) and, for two caps that differ only along the ring normal, the
    prism's mid-plane between them (an authored Window is centred, 2 cm to
    each side of its ring). The reference is the candidate with the smallest
    offset along the ring normal: the mid-plane of a centred prism, the
    coplanar cap of a one-sided imported recess. Using the nearest cap
    instead put a synced ring 2 cm off its wall plane after a move.
    """
    tol = RING_OPENING_SYNC_TOLERANCE_M
    normal = _loop_normal(ring_verts)
    caps = []
    for face_idx, verts in opening_faces:
        distance, delta = _best_alignment(ring_verts, verts)
        if math.isfinite(distance) and distance - delta.length <= tol:
            caps.append((face_idx, delta))
    refs = list(caps)
    for idx, (face_idx, delta) in enumerate(caps):
        for _other_idx, other_delta in caps[idx + 1:]:
            offset = delta - other_delta
            if (offset - normal * offset.dot(normal)).length <= tol:
                refs.append((face_idx, (delta + other_delta) / 2.0))
    if not refs:
        return None
    face_idx, delta = min(refs, key=lambda r: (round(abs(r[1].dot(normal)) / tol), r[1].length))
    return face_idx, delta.length, delta


def wall_interior_ring_faces(wall_obj):
    """Yield ``(face_index, world_verts, opening_link)`` for every
    ``gml:interior``-ring face owned by ``wall_obj`` itself. Identified the
    same way ``io/reader/materials.py`` marks them at import time: the
    face's material has ``Interior=True`` (``ExteriorPolyId`` there points
    at one of the wall's *own* polygons, distinguishing a wall's own ring
    from a Window's internal frame ring, which also sets ``Interior=True``
    but on the Window's own object/material - see
    ``opening_candidate_faces``). ``opening_link`` is that material's
    ``OPENING_LINK_KEY`` (ADR 0004 D2), or ``""`` for legacy imported rings
    that never got one."""
    mesh = wall_obj.data
    if mesh is None:
        return
    for face in mesh.polygons:
        mat = face_material(mesh, face)
        if mat is None or not mat.get("Interior"):
            continue
        link = str(mat.get(OPENING_LINK_KEY) or "").strip()
        yield face.index, _world_verts(wall_obj, face), link


def wall_has_interior_ring(wall_obj) -> bool:
    """True if ``wall_obj`` owns at least one ``gml:interior``-ring face -
    the edit-exit handler's (facade-authoring ticket 04) scope test for
    "is this a wall carrying opening-ring metadata"."""
    for _ in wall_interior_ring_faces(wall_obj):
        return True
    return False


def remove_wall_ring_faces(wall_obj, opening_link: str) -> int:
    """Delete every ``gml:interior``-ring face ``wall_obj`` owns for
    ``opening_link`` (CAC-P3.4 record-backed Window delete lifecycle).

    Identifies the faces the same way ``wall_interior_ring_faces`` does -
    an exact ``opening_link`` match only, never nearest-vertex-loop - so a
    Window that carries no link, or a neighbouring Window's ring, is never
    touched (P3.4 relationship-safety: deleting Window A must leave Window
    B's ring completely intact). Mirrors the ``bmesh.ops.delete(...,
    context='FACES')`` pattern already used to cut a hole open
    (old Cut Openings display path (removed in B17)) - this is its inverse: closing the
    wall mesh back up rather than opening it. Returns the number of faces
    removed (``0`` if ``opening_link`` is empty or matches no face)."""
    opening_link = str(opening_link or "").strip()
    if not opening_link:
        return 0
    mesh = wall_obj.data
    if mesh is None:
        return 0
    face_indices = [idx for idx, _, link in wall_interior_ring_faces(wall_obj) if link == opening_link]
    if not face_indices:
        return 0

    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    to_delete = [bm.faces[i] for i in face_indices]
    bmesh.ops.delete(bm, geom=to_delete, context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(face_indices)


def ring_face_link(wall_obj, face_index: int) -> str:
    """The opening-link (ADR 0004 D2), or ``""``, carried by one specific
    ring face's material - the same value ``wall_interior_ring_faces`` yields
    for that face, for a call site (the edit-exit handler) that already
    knows which face it wants from a ``RingOpeningPair.ring_face_index``
    rather than iterating every ring face on the wall. Deliberately reads
    the *ring face's own* material, never the paired opening's - a pairing
    made by the vertex-identity fallback (pass 2 of ``_pairs_for_wall``) can
    link an unlinked ring to a linked opening or vice versa, and callers
    that need "this ring's own snapshot key" must not conflate the two."""
    mesh = wall_obj.data
    if mesh is None or not (0 <= face_index < len(mesh.polygons)):
        return ""
    poly = mesh.polygons[face_index]
    mat = face_material(mesh, poly)
    if mat is None:
        return ""
    return str(mat.get(OPENING_LINK_KEY) or "").strip()


def opening_candidate_faces(opening_obj):
    """Yield ``(face_index, world_verts)`` for every face of a Window
    opening object that could be the counterpart of a wall's interior
    ring - i.e. every face of the opening's own mesh.

    The Window's own internal "frame reveal" ring (open question 2 - also
    ``Interior=True``, but with ``ExteriorPolyId`` pointing at the
    *opening's own* polygon, not the wall's) is deliberately not excluded:
    it is small and concentric to the Window's outward face, so it never
    wins the nearest-distance comparison against a wall's much larger hole
    outline. Filtering it out would require re-deriving polygon ownership
    to defend against a case that never changes the winning match."""
    mesh = opening_obj.data
    if mesh is None:
        return
    for face in mesh.polygons:
        yield face.index, _world_verts(opening_obj, face)


class RingOpeningPair(NamedTuple):
    """One wall interior-ring face paired with the Window opening face it
    should coincide with. ``delta`` is the world-space vector that moves
    the opening's matched vertices onto the wall ring's vertices; its
    negation moves the ring onto the opening."""

    wall: "bpy.types.Object"
    ring_face_index: int
    opening: "bpy.types.Object"
    opening_face_index: int
    distance: float
    delta: Vector

    @property
    def in_sync(self) -> bool:
        return self.distance <= RING_OPENING_SYNC_TOLERANCE_M


def _pairs_for_wall(wall, candidates) -> list:
    """Pair every interior-ring face on ``wall`` with a matching Window
    opening face among ``candidates`` (already restricted to Windows that
    fill this specific wall). Two passes, opening-link first (ADR 0004 D2):

    1. **Opening-link id** - exact, id-based match between a ring face's
       material and a candidate's own ``OPENING_LINK_KEY``. Authoritative:
       a linked ring/opening is never displaced by a closer-looking
       vertex-distance guess in pass 2.
    2. **Legacy vertex-identity fallback** - nearest-vertex-loop-distance
       greedy assignment (``_best_alignment``), for whatever pass 1 left
       unpaired (rings or openings with no link at all).

    A wall with no Window children, or no interior ring, contributes
    nothing - nothing to pair, nothing to flag.
    """
    ring_faces = list(wall_interior_ring_faces(wall))
    if not ring_faces or not candidates:
        return []

    opening_faces = []
    for opening in candidates:
        opening_link = str(opening.get(OPENING_LINK_KEY) or "").strip()
        for face_idx, verts in opening_candidate_faces(opening):
            opening_faces.append((opening, face_idx, verts, opening_link))
    if not opening_faces:
        return []

    pairs: list[RingOpeningPair] = []
    used_rings = set()
    used_openings = set()

    def _record(ring_idx, opening, face_idx, dist, delta):
        used_rings.add(ring_idx)
        used_openings.add(id(opening))
        pairs.append(
            RingOpeningPair(
                wall=wall,
                ring_face_index=ring_faces[ring_idx][0],
                opening=opening,
                opening_face_index=face_idx,
                distance=dist,
                delta=delta,
            )
        )

    # Pass 1 - opening-link id (authoritative, ADR 0004 D2).
    for ring_idx, (_face_index, ring_verts, ring_link) in enumerate(ring_faces):
        if not ring_link:
            continue
        linked = [of for of in opening_faces if of[3] == ring_link]
        if not linked:
            continue
        by_opening = {}
        for opening, face_idx, verts, _link in linked:
            by_opening.setdefault(opening, []).append((face_idx, verts))
        refs = [(opening, _reference_cap(ring_verts, faces)) for opening, faces in by_opening.items()]
        refs = [(opening, ref) for opening, ref in refs if ref is not None]
        if refs:
            opening, (face_idx, dist, delta) = min(refs, key=lambda r: r[1][1])
            _record(ring_idx, opening, face_idx, dist, delta)
            continue
        opening, face_idx, verts, _link = min(linked, key=lambda of: _best_alignment(ring_verts, of[2])[0])
        dist, delta = _best_alignment(ring_verts, verts)
        _record(ring_idx, opening, face_idx, dist, delta)

    # Pass 2 - legacy vertex-identity fallback, link-less rings/openings only.
    scored = []
    for ring_idx, (_face_index, ring_verts, _ring_link) in enumerate(ring_faces):
        if ring_idx in used_rings:
            continue
        for opening, face_idx, verts, _opening_link in opening_faces:
            if id(opening) in used_openings:
                continue
            faces = [(idx, candidate_verts) for candidate, idx, candidate_verts, _link in opening_faces
                     if candidate is opening]
            ref = _reference_cap(ring_verts, faces)
            if ref is not None:
                ref_face, dist, delta = ref
                scored.append((dist, ring_idx, opening, ref_face, delta))
                continue
            dist, delta = _best_alignment(ring_verts, verts)
            scored.append((dist, ring_idx, opening, face_idx, delta))
    scored.sort(key=lambda t: t[0])

    for dist, ring_idx, opening, face_idx, delta in scored:
        if ring_idx in used_rings or id(opening) in used_openings:
            continue
        _record(ring_idx, opening, face_idx, dist, delta)

    return pairs


def find_ring_opening_pairs(building_obj) -> list:
    """Pair every WallSurface interior ring under ``building_obj`` with its
    matching child ring-paired opening face (every Window, plus a floating
    Door - see ``is_ring_paired_opening``), restricted to openings whose
    ``filling_parent_surface_id`` names that specific wall. A ground-touching
    Door is never a candidate (see module docstring). See ``_pairs_for_wall``
    for the opening-link-first / vertex-identity-fallback pairing rule (ADR
    0004 D2, facade-authoring ticket 04) applied per wall.
    """
    children = _collect_semantic_children(building_obj)
    walls = [c for c in children if is_boundary_surface(c)]
    paired_openings = [c for c in children if is_ring_paired_opening(c)]

    pairs: list[RingOpeningPair] = []
    for wall in walls:
        wall_gml_id = str(wall.get("gml_id") or "").strip()
        if not wall_gml_id:
            continue
        candidates = [
            w for w in paired_openings
            if str(w.get("filling_parent_surface_id") or "").strip() == wall_gml_id
        ]
        pairs.extend(_pairs_for_wall(wall, candidates))

    return pairs


def pairs_for_wall_children(wall) -> list:
    """Local (single-wall) analogue of ``find_ring_opening_pairs``, scoped to
    ``wall``'s own ring-paired opening children (every Window and a floating
    Door carrying ``OPENING_LINK_KEY``). Ground-touching Doors remain
    excluded: their exterior-notch topology has no wall interior ring. Both
    the placement kernel and the importer's explode step parent an opening
    under its wall; this edit-exit path stays strictly local, with no
    ``_collect_semantic_children`` walk or building lookup."""
    candidates = [c for c in wall.children if is_ring_paired_opening(c)]
    return _pairs_for_wall(wall, candidates)


def check_ring_opening_sync(building_obj) -> list:
    """Return every desynced ``RingOpeningPair`` under ``building_obj`` -
    empty if fully in sync (or if there is nothing to check)."""
    return [p for p in find_ring_opening_pairs(building_obj) if not p.in_sync]


def sync_ring_to_opening(pair: RingOpeningPair) -> None:
    """Keep the wall's ring where it is; move the *whole* Window object
    (a rigid translation, since a moved window keeps its own shape) so its
    matched face lands back on the ring."""
    opening = pair.opening
    from mathutils import Matrix

    opening.matrix_world = Matrix.Translation(pair.delta) @ opening.matrix_world


def sync_opening_to_ring(pair: RingOpeningPair) -> None:
    """Keep the Window where it is; recut the wall's ring face vertices so
    they land back on the Window's matched face. Only that one ring face's
    vertices move - the wall's exterior boundary is untouched, matching
    decision 5 (rings are stored, never derived, and a wall keeps its own
    rings)."""
    wall = pair.wall
    mesh = wall.data
    face = mesh.polygons[pair.ring_face_index]

    # pair.delta moves the opening onto the ring (ring - opening); moving
    # the ring onto the opening is therefore the negated vector, converted
    # from a world-space direction into the wall's local mesh space (mesh
    # vertex coordinates are local; only the linear part of matrix_world
    # applies to a direction, never its translation).
    local_delta = wall.matrix_world.inverted().to_3x3() @ (-pair.delta)

    for vi in face.vertices:
        mesh.vertices[vi].co = mesh.vertices[vi].co + local_delta
    mesh.update()
