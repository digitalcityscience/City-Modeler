# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""GS.1: record-first wall display mesh builder (docs/agent/authoring-core/gs-design-note.md).

``build_surface_display(record_set, surface_id)`` builds the vertex/face
topology of a holed wall mesh straight from a WallSurface's committed
``PolygonRecord``/``RingRecord`` data (``geometry_state == "committed"``) -
not from ``bpy.data.objects`` or materials, unlike the legacy
old Cut Openings display path (removed in B17): ``cut_openings_scene``.

Pure Python, no ``bpy``/``bmesh``/``mathutils`` import: this keeps the
function testable in isolation without a Blender process, in the spirit of
Rule R1 (CAC-P1.8) even though this module lives under ``blender/`` (the
bpy-bound adapter layer) rather than ``domain/``, so R1's automated scan
(``test_cac_domain_has_no_bpy.py``) does not cover it. The Newell/loop-area/
loop-normal planarity math below is a bpy-free re-derivation of
the old Cut Openings display path (removed in B17)'s ``_newell``/``_loop_area``/``_loop_normal`` -
reused for the math only, not that file's material-scanning input path,
which GS deliberately does not repeat (record-first, not mesh/material-first).

GS.1 scope only: this module has no caller yet. No wiring into
``edit_session.commit()``, no operator, no flag plumbing (gs-design-note.md
S5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-9


class DisplayBuildError(Exception):
    """Raised when a wall surface's committed records cannot be built into
    display geometry."""


@dataclass(frozen=True)
class DisplayMesh:
    """Plain vertex/face topology - no ``bpy`` mesh object.

    ``vertices``: tuple of ``(x, y, z)`` world-space points.
    ``faces``: tuple of ``(i, j, k)`` triangle vertex indices into
    ``vertices``.
    """

    vertices: tuple
    faces: tuple


# --- bpy-free planarity/normal math (re-derived from the old Cut Openings display path (removed in B17)) ---


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a):
    return math.sqrt(_dot(a, a))


def _normalized(a):
    n = _length(a)
    if n <= _EPS:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _newell(coords):
    """Newell vector of a 3D polygon loop: length is twice the loop's area,
    direction is the loop's (right-hand) normal."""
    n = [0.0, 0.0, 0.0]
    m = len(coords)
    for i in range(m):
        ax, ay, az = coords[i]
        bx, by, bz = coords[(i + 1) % m]
        n[0] += (ay - by) * (az + bz)
        n[1] += (az - bz) * (ax + bx)
        n[2] += (ax - bx) * (ay + by)
    return (n[0], n[1], n[2])


def _loop_area(coords):
    """Unsigned area of a 3D planar polygon loop."""
    if len(coords) < 3:
        return 0.0
    return _length(_newell(coords)) * 0.5


def _loop_normal(coords):
    """Outward normal of a 3D planar polygon loop."""
    return _normalized(_newell(coords))


def _drop_closing_duplicate(coords):
    """Strip a trailing point that repeats the first one (within ``_EPS``).

    Every loop in this module is an *implicitly* closed ring - every
    ``(i + 1) % n`` wraparound already connects the last point back to the
    first. A ring whose stored coordinates *also* explicitly repeat the
    first point as the last (the standard convention for a shapely
    ``LinearRing``/``Polygon.exterior``, which real wall records are built
    from) would otherwise give that point a real, non-degenerate final
    "closing" edge of length ~0 plus the normal wraparound edge - two
    edges where the rest of the ring has one. Ear-clipping's ear/blocking/
    crossing tests all assume that invariant holds uniformly; N-hole
    hardening (P3b.2) surfaced a real Building fixture where it didn't,
    breaking triangulation on a wall with 2+ hosted Windows (P3b.2's own
    stated risk: real-geometry correctness, not just synthetic fixtures).
    """
    if len(coords) > 1 and _length(_sub(coords[-1], coords[0])) <= _EPS:
        return coords[:-1]
    return coords


# --- polygon-with-holes triangulation (bridge + ear clipping) ---


def _point_in_triangle_2d(p, a, b, c):
    """True if ``p`` lies strictly inside triangle ``a, b, c`` (2D).

    A point on an edge or at a vertex (within ``_EPS``) does not count: a
    bridge seam duplicates coordinates onto the merged polygon's own
    boundary, and those duplicates must never block an otherwise-valid ear."""
    d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
    d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
    d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
    if abs(d1) <= _EPS or abs(d2) <= _EPS or abs(d3) <= _EPS:
        return False
    return (d1 > 0 and d2 > 0 and d3 > 0) or (d1 < 0 and d2 < 0 and d3 < 0)


def _find_bridge(outer2d, hole2d):
    """Held's algorithm: find ``(outer_index, hole_index)`` for a visibility
    bridge from ``hole2d`` into ``outer2d``. Both are closed 2D loops (no
    repeated first/last point)."""
    hi = max(range(len(hole2d)), key=lambda i: hole2d[i][0])
    hx, hy = hole2d[hi]

    n = len(outer2d)
    best_x = None
    best_edge = None
    for i in range(n):
        ax, ay = outer2d[i]
        bx, by = outer2d[(i + 1) % n]
        if (ay > hy) == (by > hy):
            continue  # edge does not cross the horizontal ray's y
        t = (hy - ay) / (by - ay)
        ix = ax + t * (bx - ax)
        if ix >= hx - _EPS and (best_x is None or ix < best_x):
            best_x = ix
            best_edge = (i, (i + 1) % n)

    if best_edge is None:
        raise DisplayBuildError("interior ring is not enclosed by the exterior ring")

    i1, i2 = best_edge
    m_idx = i1 if outer2d[i1][0] >= outer2d[i2][0] else i2

    # Visibility check: any outer vertex strictly inside the triangle formed
    # by the hole point, the ray intersection and the candidate bridge point
    # blocks the direct bridge - the most "visible" such vertex replaces it.
    px, py = outer2d[m_idx]
    candidates = [m_idx]
    for j in range(n):
        if j in (i1, i2):
            continue
        if _point_in_triangle_2d(outer2d[j], (hx, hy), (best_x, hy), (px, py)):
            candidates.append(j)
    if len(candidates) > 1:
        m_idx = min(
            candidates,
            key=lambda j: abs(math.atan2(outer2d[j][1] - hy, outer2d[j][0] - hx)),
        )
    # A vertex an earlier bridge already used appears more than once (nudged copies). Bridge from
    # the copy whose interior wedge faces the hole, else the two bridges cross and the merged
    # polygon folds into flipped, overlapping triangles (two windows stacked beside a door).
    mx, my = outer2d[m_idx]
    copies = [j for j in range(n) if abs(outer2d[j][0] - mx) <= 1e-5 and abs(outer2d[j][1] - my) <= 1e-5]
    if len(copies) > 1:
        m_idx = next((j for j in copies
                      if _in_wedge_2d(outer2d[j - 1], outer2d[j], outer2d[(j + 1) % n], (hx, hy))), m_idx)
    return m_idx, hi


def _in_wedge_2d(prev, cur, nxt, p):
    """True if ``p`` lies in the interior angle at ``cur`` of a CCW polygon (``prev -> cur -> nxt``)."""
    def left(a, b):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0
    convex = (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (cur[1] - prev[1]) * (nxt[0] - cur[0]) > 0
    return (left(prev, cur) and left(cur, nxt)) if convex else (left(prev, cur) or left(cur, nxt))


def _point_in_polygon_2d(p, poly2d):
    """Ray-casting point-in-polygon test (2D). A point on the boundary
    (within ``_EPS``) counts as inside - a shared vertex/edge between a
    ring and the exterior must never register as "outside"."""
    if _point_on_boundary_2d(p, poly2d):
        return True
    x, y = p
    n = len(poly2d)
    inside = False
    for i in range(n):
        x1, y1 = poly2d[i]
        x2, y2 = poly2d[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x1 + t * (x2 - x1) > x:
                inside = not inside
    return inside


def _point_on_segment_2d(p, a, b):
    cross = (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])
    if abs(cross) > _EPS:
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    if dot < -_EPS:
        return False
    sq_len = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
    return dot <= sq_len + _EPS


def _point_on_boundary_2d(p, poly2d):
    n = len(poly2d)
    return any(
        _point_on_segment_2d(p, poly2d[i], poly2d[(i + 1) % n])
        for i in range(n)
    )


def _segments_intersect_2d(a1, a2, b1, b2):
    """True if closed 2D segments ``a1-a2`` and ``b1-b2`` cross or touch,
    including a shared endpoint or one segment's endpoint lying on the
    other's interior."""

    def _orient(p, q, r):
        val = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        if val > _EPS:
            return 1
        if val < -_EPS:
            return -1
        return 0

    o1 = _orient(a1, a2, b1)
    o2 = _orient(a1, a2, b2)
    o3 = _orient(b1, b2, a1)
    o4 = _orient(b1, b2, a2)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _point_on_segment_2d(b1, a1, a2):
        return True
    if o2 == 0 and _point_on_segment_2d(b2, a1, a2):
        return True
    if o3 == 0 and _point_on_segment_2d(a1, b1, b2):
        return True
    if o4 == 0 and _point_on_segment_2d(a2, b1, b2):
        return True
    return False


def _ring_inside_polygon_2d(ring2d, poly2d):
    """True if every vertex of ``ring2d`` lies inside (or on the boundary
    of) ``poly2d``."""
    return all(_point_in_polygon_2d(p, poly2d) for p in ring2d)


def _rings_overlap_2d(ring_a2d, ring_b2d):
    """True if two closed 2D interior-ring loops overlap: any pair of
    edges crosses/touches, or either ring's interior contains a vertex of
    the other. This is the minimum precondition N-hole triangulation needs
    (phase3b-design-note.md §12) - not a general intersection framework."""
    na, nb = len(ring_a2d), len(ring_b2d)
    for i in range(na):
        a1, a2 = ring_a2d[i], ring_a2d[(i + 1) % na]
        for j in range(nb):
            b1, b2 = ring_b2d[j], ring_b2d[(j + 1) % nb]
            if _segments_intersect_2d(a1, a2, b1, b2):
                return True
    if _point_in_polygon_2d(ring_a2d[0], ring_b2d):
        return True
    if _point_in_polygon_2d(ring_b2d[0], ring_a2d):
        return True
    return False


def _nudge_bridge_endpoints(bridge_out, bridge_hole):
    """Move a bridge seam's two endpoints a hair off their exact positions
    (each one towards the other), so the two duplicated copies this bridge
    puts into the merged polygon (see ``_merge_hole``) are never bit-
    identical to any other bridge's duplicated points - purely a
    triangulation-decision detail; the real 3D vertices these 2D points
    stand in for are untouched. See ``_merge_hole`` for why multiplicity
    matters."""
    dx = bridge_hole[0] - bridge_out[0]
    dy = bridge_hole[1] - bridge_out[1]
    seam_len = math.hypot(dx, dy)
    if seam_len <= _EPS:
        return bridge_out, bridge_hole
    nudge = min(1e-6, seam_len * 1e-6)
    ux, uy = dx / seam_len, dy / seam_len
    return (
        (bridge_out[0] + ux * nudge, bridge_out[1] + uy * nudge),
        (bridge_hole[0] - ux * nudge, bridge_hole[1] - uy * nudge),
    )


def _merge_hole(outer2d, outer_vidx, hole2d, hole_vidx):
    """Splice ``hole2d`` into ``outer2d`` via a visibility bridge, returning
    a single simple polygon (with duplicated, nudged-apart bridge vertices)
    plus its parallel global-vertex-index list, plus enough about where the
    bridge landed (``m_idx``, ``hn``, ``local_bridge_edges``) for a caller
    merging several holes in sequence to keep tracking every bridge's own
    two connector legs across each subsequent splice (see ``build_surface_
    display``): those legs are exempt from ``_triangulate_simple_polygon``'s
    diagonal-crossing check, since a bridge is a zero-width there-and-back
    channel and crossing *that* line is never a real self-intersection, only
    crossing a genuine exterior/hole boundary edge is.

    N-hole hardening (P3b.2): bridging a second (or third, ...) hole must
    happen against the polygon that already contains every previously
    merged hole, not against the original exterior ring in isolation - a
    straight ray-cast bridge computed against the bare exterior can pass
    straight through an as-yet-unmerged hole that happens to sit between
    the current hole and the wall boundary (a common real layout: two
    windows side by side on the same wall, where the left one's bridge
    would otherwise cut straight through the right one). ``build_surface_
    display`` merges holes in order of decreasing rightmost-x precisely so
    that, by the time a given hole is bridged, every hole to its right is
    already part of ``outer2d`` and therefore already something ``_find_
    bridge``'s own ray-cast/visibility search naturally bridges to or
    around, instead of straight through.

    Nudging each bridge seam's two duplicated points a hair apart (the
    real 3D vertices they stand in for are untouched) keeps every 2D point
    at multiplicity <= 2, which is what ear-clipping's "no other polygon
    vertex lies inside the candidate ear" test requires to stay a valid
    sufficiency proof once multiple holes are bridged in.
    """
    m_idx, hi = _find_bridge(outer2d, hole2d)
    hn = len(hole2d)
    bridge_out_2nd, bridge_hole_1st = _nudge_bridge_endpoints(outer2d[m_idx], hole2d[hi])

    merged2d = list(outer2d[: m_idx + 1])
    merged_vidx = list(outer_vidx[: m_idx + 1])
    entry_pos = len(merged2d) - 1
    for k in range(hn + 1):
        idx = (hi + k) % hn
        merged2d.append(bridge_hole_1st if k == 0 else hole2d[idx])
        merged_vidx.append(hole_vidx[idx])
    hole_close_pos = len(merged2d) - 1
    merged2d.append(bridge_out_2nd)
    merged_vidx.append(outer_vidx[m_idx])
    exit_pos = len(merged2d) - 1
    merged2d.extend(outer2d[m_idx + 1:])
    merged_vidx.extend(outer_vidx[m_idx + 1:])

    local_bridge_edges = {
        frozenset((entry_pos, entry_pos + 1)),
        frozenset((hole_close_pos, exit_pos)),
    }
    return merged2d, merged_vidx, m_idx, hn, local_bridge_edges


def _segment_properly_crosses_2d(a1, a2, b1, b2):
    """True if segments ``a1-a2`` and ``b1-b2`` cross at a point interior to
    both - a real crossing, not merely touching at/near a shared endpoint
    (any of the four orientation tests landing on ``0`` means "touches,
    not crosses" and is not reported)."""

    def _orient(p, q, r):
        val = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        if val > _EPS:
            return 1
        if val < -_EPS:
            return -1
        return 0

    o1 = _orient(a1, a2, b1)
    o2 = _orient(a1, a2, b2)
    o3 = _orient(b1, b2, a1)
    o4 = _orient(b1, b2, a2)
    return o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0 and o1 != o2 and o3 != o4


def _ring_orientation(poly2d, ring):
    signed_area2 = 0.0
    m = len(ring)
    for i in range(m):
        x1, y1 = poly2d[ring[i]]
        x2, y2 = poly2d[ring[(i + 1) % m]]
        signed_area2 += x1 * y2 - x2 * y1
    return 1.0 if signed_area2 >= 0.0 else -1.0


def _find_ear(poly2d, ring, orientation, bridge_edges):
    """One ear-clipping scan over ``ring`` (a list of ``poly2d`` positions).
    Returns ``(k, triangle)`` for the first valid ear found (``k`` is its
    index *within* ``ring``, for the caller to pop), or ``None`` if the
    classic "3 consecutive vertices" ear test finds nothing - which can
    happen for a genuinely simple (mod bridge seams) polygon whose only
    remaining valid splits are non-consecutive diagonals (see
    ``_triangulate_ring``'s fallback)."""
    m = len(ring)
    for k in range(m):
        i0 = ring[(k - 1) % m]
        i1 = ring[k]
        i2 = ring[(k + 1) % m]
        a, b, c = poly2d[i0], poly2d[i1], poly2d[i2]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if cross * orientation <= _EPS:
            continue  # reflex or degenerate corner, not an ear
        if any(
            j not in (i0, i1, i2) and _point_in_triangle_2d(poly2d[j], a, b, c)
            for j in ring
        ):
            continue
        if any(
            _segment_properly_crosses_2d(a, c, poly2d[ring[t]], poly2d[ring[(t + 1) % m]])
            for t in range(m)
            if ring[t] not in (i0, i1, i2) and ring[(t + 1) % m] not in (i0, i1, i2)
            and frozenset((ring[t], ring[(t + 1) % m])) not in bridge_edges
        ):
            continue  # the new i0-i2 diagonal would cross another boundary edge
        return k, (i0, i1, i2)
    return None


def _find_splitting_diagonal(poly2d, ring, bridge_edges):
    """A non-adjacent-vertex diagonal that splits ``ring`` into two smaller
    simple sub-rings, as ``(a, b)`` positions *within* ``ring``, or
    ``None``. Fallback for a polygon shape where no 3-consecutive-vertex
    ear exists (a real, if rare, possibility once several holes are
    bridged in) even though the polygon as a whole is still triangulable -
    Meisters' two-ears theorem guarantees an ear always exists for a true
    simple polygon, but this one is simple only *modulo* its zero-width
    bridge seams (real ``multiplicity <= 2`` at every point, but not a
    literal single embedded cycle graph the theorem's proof assumes), so a
    slower any-valid-diagonal search is the correct fallback rather than a
    hard failure."""
    m = len(ring)
    for a in range(m):
        pa = poly2d[ring[a]]
        for b in range(a + 2, m):
            if a == 0 and b == m - 1:
                continue  # already-adjacent pair (wraparound)
            pb = poly2d[ring[b]]
            blocked = any(
                ring[t] not in (ring[a], ring[b]) and ring[(t + 1) % m] not in (ring[a], ring[b])
                and frozenset((ring[t], ring[(t + 1) % m])) not in bridge_edges
                and _segment_properly_crosses_2d(pa, pb, poly2d[ring[t]], poly2d[ring[(t + 1) % m]])
                for t in range(m)
            )
            if blocked:
                continue
            midpoint = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
            if not _point_in_polygon_2d(midpoint, [poly2d[i] for i in ring]):
                continue
            return a, b
    return None


def _triangulate_ring(poly2d, ring, bridge_edges):
    """Recursively triangulate one simple sub-ring (a list of ``poly2d``
    positions). Ear-clips where possible (the fast, common path); falls
    back to splitting on any valid non-adjacent diagonal (see
    ``_find_splitting_diagonal``) only when the ear scan finds nothing."""
    if len(ring) == 3:
        return [tuple(ring)]

    orientation = _ring_orientation(poly2d, ring)
    found = _find_ear(poly2d, ring, orientation, bridge_edges)
    if found is not None:
        k, triangle = found
        rest = ring[:k] + ring[k + 1:]
        return [triangle] + _triangulate_ring(poly2d, rest, bridge_edges)

    split = _find_splitting_diagonal(poly2d, ring, bridge_edges)
    if split is None:
        raise DisplayBuildError("failed to triangulate polygon with holes (no ear or diagonal found)")
    a, b = split
    ring_ab = ring[a:b + 1]
    ring_ba = ring[b:] + ring[:a + 1]
    return _triangulate_ring(poly2d, ring_ab, bridge_edges) + _triangulate_ring(poly2d, ring_ba, bridge_edges)


def _triangulate_simple_polygon(poly2d, bridge_edges=frozenset()):
    """Ear-clip a simple (possibly non-convex, possibly bridge-duplicated)
    2D polygon. Returns a list of ``(i, j, k)`` index triples into
    ``poly2d``.

    N-hole hardening (P3b.2): once two or more holes are bridged into one
    polygon, a candidate ear can satisfy the classic "no other vertex lies
    inside the triangle" test yet still overlap a hole whose own vertices
    all sit outside that triangle while one of its *edges* passes through
    it - the vertex-only test is a sufficient ear condition only for a
    polygon with no bridge seams. The diagonal-crossing check (does the
    new edge ``i0-i2`` properly cross any other boundary edge) is the
    condition that actually holds for a multiply-bridged polygon, and it
    subsumes the vertex-in-triangle check rather than replacing it (a
    vertex strictly inside the ear with no edge crossing it is still
    possible only when that vertex's own two edges both stay outside the
    ear, which the vertex check alone already screens for; keeping both is
    cheap and never rejects a genuinely valid ear).

    ``bridge_edges`` (``_merge_hole``'s own connector legs, as poly2d-
    position frozensets, accumulated and position-remapped by
    ``build_surface_display`` across every hole it merges in) are exempt
    from the crossing check: a bridge is a zero-width there-and-back
    channel between two *real* boundary points, so a diagonal crossing
    that exact line is only ever grazing the channel, never actually
    entering a hole or leaving the exterior - unlike crossing a genuine
    exterior/hole boundary edge, which always means exactly that.

    A real (non-synthetic) multi-Window fixture surfaced a shape where no
    3-consecutive-vertex ear exists at some intermediate step even though
    the polygon is still triangulable (``_triangulate_ring``'s diagonal-
    split fallback) - see that function's docstring.
    """
    n = len(poly2d)
    if n < 3:
        return []
    return _triangulate_ring(poly2d, list(range(n)), bridge_edges)


def _build_committed_polygon_mesh(polygon, surface_id):
    """Build the holed-mesh topology for exactly one already-committed
    ``PolygonRecord`` (ear-clipping + hole-bridging kernel shared by
    ``build_surface_display`` - single polygon per surface, WallSurface/
    Window - and ``build_roof_display`` - one or more polygons per
    RoofSurface, CAC-P6.3 S3). The caller is responsible for the surface-
    level contract checks (feature exists, polygon count, committed state);
    this function only ever raises for a geometric defect of ``polygon``
    itself.
    """
    exterior = _drop_closing_duplicate(list(polygon.exterior.coords))
    if len(exterior) < 3:
        raise DisplayBuildError(f"surface {surface_id!r}: exterior ring has fewer than 3 points")
    interiors = [_drop_closing_duplicate(list(ring.coords)) for ring in polygon.interiors]

    normal = _loop_normal(exterior)
    if _length(normal) <= _EPS:
        raise DisplayBuildError(f"surface {surface_id!r}: exterior ring is degenerate (zero area)")

    origin = exterior[0]
    u = _normalized(_sub(exterior[1], exterior[0]))
    if _length(u) <= _EPS:
        raise DisplayBuildError(f"surface {surface_id!r}: exterior ring has a zero-length edge")
    v = _cross(normal, u)

    def to2d(p):
        d = _sub(p, origin)
        return (_dot(d, u), _dot(d, v))

    verts3d = list(exterior)
    outer2d = [to2d(p) for p in exterior]
    outer_vidx = list(range(len(exterior)))

    interiors_2d = []
    for idx, ring in enumerate(interiors):
        if len(ring) < 3:
            raise DisplayBuildError(f"surface {surface_id!r}: interior ring has fewer than 3 points")
        interiors_2d.append([to2d(p) for p in ring])

    # Minimum precondition N-hole triangulation needs (phase3b-design-note.md
    # §12): each interior ring fully inside the exterior, no pairwise overlap.
    for idx, ring2d in enumerate(interiors_2d):
        if not _ring_inside_polygon_2d(ring2d, outer2d):
            raise DisplayBuildError(
                f"surface {surface_id!r}: interior ring {idx} is not fully contained in the exterior ring"
            )
    for i in range(len(interiors_2d)):
        for j in range(i + 1, len(interiors_2d)):
            if _rings_overlap_2d(interiors_2d[i], interiors_2d[j]):
                raise DisplayBuildError(
                    f"surface {surface_id!r}: interior rings {i} and {j} overlap"
                )

    holes_vidx = []
    for ring in interiors:
        start = len(verts3d)
        verts3d.extend(ring)
        holes_vidx.append(list(range(start, start + len(ring))))

    # Merge holes in order of decreasing rightmost-x (see _merge_hole): by
    # the time a given hole is bridged, every hole to its right is already
    # part of the growing merged polygon, so its own bridge naturally
    # bridges to/around them instead of straight through.
    merge_order = sorted(
        range(len(interiors_2d)), key=lambda idx: -max(p[0] for p in interiors_2d[idx]),
    )
    merged2d, merged_vidx = outer2d, outer_vidx
    bridge_edges = set()
    for idx in merge_order:
        merged2d, merged_vidx, m_idx, hn, local_bridge_edges = _merge_hole(
            merged2d, merged_vidx, interiors_2d[idx], holes_vidx[idx],
        )
        # Every earlier bridge_edges position at or before m_idx is
        # untouched by this splice; everything after it shifts by the
        # hn + 2 new positions this splice inserted (hole loop + the
        # outer point's second, nudged visit) - see _merge_hole.
        shift = hn + 2
        bridge_edges = {
            frozenset(p if p <= m_idx else p + shift for p in edge)
            for edge in bridge_edges
        }
        bridge_edges |= local_bridge_edges

    local_tris = _triangulate_simple_polygon(merged2d, bridge_edges)
    faces = tuple(
        (merged_vidx[i0], merged_vidx[i1], merged_vidx[i2])
        for (i0, i1, i2) in local_tris
    )
    # Bridge seams reference the same global vertex twice; the resulting
    # zero-area triangles carry no display geometry and are dropped.
    faces = tuple(f for f in faces if len(set(f)) == 3)

    return DisplayMesh(vertices=tuple(verts3d), faces=faces)


def build_surface_display(record_set, surface_id):
    """Build holed wall mesh topology from ``record_set``'s committed records
    for the WallSurface feature ``surface_id``.

    Reads ``record_set.polygons_of(surface_id)`` - exactly one committed
    ``PolygonRecord`` expected (the wall's own polygon; a hosted Window's
    polygon is a separate feature and out of scope here). Returns a
    ``DisplayMesh`` whose faces leave a hole for every interior ring.
    Raises ``DisplayBuildError`` for anything that does not match that
    contract.
    """
    feature = record_set.feature(surface_id)
    if feature is None:
        raise DisplayBuildError(f"no feature record for surface {surface_id!r}")

    polygons = record_set.polygons_of(surface_id)
    if not polygons:
        raise DisplayBuildError(f"surface {surface_id!r} has no polygon records")
    if len(polygons) != 1:
        raise DisplayBuildError(
            f"surface {surface_id!r}: expected exactly one polygon, found {len(polygons)}"
        )
    polygon = polygons[0]
    if polygon.geometry_state != "committed":
        raise DisplayBuildError(
            f"surface {surface_id!r}: polygon geometry_state is "
            f"{polygon.geometry_state!r}, expected 'committed'"
        )
    return _build_committed_polygon_mesh(polygon, surface_id)


def build_roof_display(record_set, surface_id):
    """Build display mesh topology for a record-backed RoofSurface (CAC-P6.3
    S2/S3/S8), straight from its committed ``PolygonRecord``/``RingRecord``
    data - same contract as ``build_surface_display`` (generic interior
    rings included, same ear-clipping/hole-bridging kernel reused via
    ``_build_committed_polygon_mesh``) but not gated to exactly one polygon:
    a RoofSurface's record contract (``roof_edit_session._commit_roof``)
    already allows more than one committed ``PolygonRecord`` under one
    feature, and P6.3 S8's accepted behavior for that case is one merged
    derived display for the whole RoofSurface rather than silently ignoring
    every polygon past the first.

    Every committed polygon is built independently (so one polygon's holes
    never bridge into another polygon's own boundary) and the results are
    concatenated into a single ``DisplayMesh``, vertex indices offset so
    each polygon's faces still index correctly into the combined vertex
    list. Polygons are processed in ``polygon_id`` order so the result is
    deterministic regardless of ``record_set``/dict construction or
    iteration order (mirrors ``display_checksum``'s own sorting rule).

    Raises ``DisplayBuildError`` if the feature has no record, no polygon
    records at all, or any polygon is not ``geometry_state == "committed"``
    - the same refusal contract ``build_surface_display`` uses, extended to
    "every" polygon rather than "the one" polygon.
    """
    feature = record_set.feature(surface_id)
    if feature is None:
        raise DisplayBuildError(f"no feature record for surface {surface_id!r}")

    polygons = record_set.polygons_of(surface_id)
    if not polygons:
        raise DisplayBuildError(f"surface {surface_id!r} has no polygon records")
    for polygon in polygons:
        if polygon.geometry_state != "committed":
            raise DisplayBuildError(
                f"surface {surface_id!r}: polygon {polygon.polygon_id!r} geometry_state is "
                f"{polygon.geometry_state!r}, expected 'committed'"
            )

    all_vertices = []
    all_faces = []
    for polygon in sorted(polygons, key=lambda p: p.polygon_id):
        mesh = _build_committed_polygon_mesh(polygon, surface_id)
        offset = len(all_vertices)
        all_vertices.extend(mesh.vertices)
        all_faces.extend((i + offset, j + offset, k + offset) for (i, j, k) in mesh.faces)

    return DisplayMesh(vertices=tuple(all_vertices), faces=tuple(all_faces))
