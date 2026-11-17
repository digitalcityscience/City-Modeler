# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/building_shapes.py
"""Footprints and LoD1 blocks for authored buildings (B04). Pure geometry,
no bpy: points are (x, y, z) tuples, faces are vertex-index lists.

A footprint is a closed ring of points, counter-clockwise seen from above,
without the closing duplicate. Face lists come with one semantic label per
face (GroundSurface / RoofSurface / WallSurface) so the Blender side never
guesses which face is which.
"""

from __future__ import annotations

import math


def signed_area(ring) -> float:
    """Shoelace area in the XY plane; positive when counter-clockwise."""
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))


def ccw(ring) -> list:
    return list(ring) if signed_area(ring) >= 0 else list(reversed(ring))


def rectangle(center, width: float, depth: float, angle: float = 0.0) -> list:
    """Rectangle footprint around ``center`` (x, y, z); ``width`` along the rotated X axis."""
    cx, cy, cz = center
    c, s = math.cos(angle), math.sin(angle)
    corners = ((-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2))
    return [(cx + x * c - y * s, cy + x * s + y * c, cz) for x, y in corners]


def boundary_loop(faces) -> list:
    """Vertex indices of the outer boundary of the union of ``faces``, in walking order.

    Boundary edges are the ones used by exactly one face. With several loops
    (holes, separate islands) the longest one is returned."""
    count = {}
    for face in faces:
        for a, b in zip(face, face[1:] + face[:1]):
            key = (min(a, b), max(a, b))
            count[key] = count.get(key, 0) + 1
    neighbours = {}
    for a, b in (k for k, n in count.items() if n == 1):
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    loops, seen = [], set()
    for start in neighbours:
        if start in seen:
            continue
        loop, prev, cur = [start], None, start
        seen.add(start)
        while True:
            nxt = next((n for n in neighbours[cur] if n != prev and (n not in seen or n == start)), None)
            if nxt is None or nxt == start:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        loops.append(loop)
    return max(loops, key=len) if loops else []


def drop_collinear(ring, tol: float = 1e-6) -> list:
    """Remove points lying on the straight line between their neighbours."""
    out = []
    n = len(ring)
    for i, p in enumerate(ring):
        a, b = ring[i - 1], ring[(i + 1) % n]
        cross = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
        if abs(cross) > tol:
            out.append(p)
    return out


def _rect_slabs(local, tol):
    """Rectangles (x0, x1, y0, y1) covering a rectilinear ring given in local (x, y)."""
    xs = sorted({round(p[0] / tol) * tol for p in local})
    edges = [(a, b) for a, b in zip(local, local[1:] + local[:1]) if abs(a[1] - b[1]) <= tol]  # along x
    slabs = []
    for x0, x1 in zip(xs, xs[1:]):
        mid = (x0 + x1) / 2
        ys = sorted(a[1] for a, b in edges if min(a[0], b[0]) < mid < max(a[0], b[0]))
        intervals = tuple(zip(ys[0::2], ys[1::2]))
        if slabs and slabs[-1][1] == x0 and all(
                abs(p - q) <= tol for i, j in zip(slabs[-1][2], intervals) for p, q in zip(i, j)) \
                and len(slabs[-1][2]) == len(intervals):
            slabs[-1] = (slabs[-1][0], x1, intervals)
        else:
            slabs.append((x0, x1, intervals))
    return [(x0, x1, y0, y1) for x0, x1, ivs in slabs for y0, y1 in ivs]


def rectilinear_rectangles(ring, tol: float = 1e-4):
    """Split a footprint whose edges meet at right angles into rectangles (counter-clockwise
    rings, same z), fewest of the two axis sweeps; ``None`` if the ring is not rectilinear."""
    ring = ccw(drop_collinear(ring))
    if len(ring) < 4:
        return None
    ox, oy = ring[0][0], ring[0][1]
    d = (ring[1][0] - ox, ring[1][1] - oy)
    length = math.hypot(*d)
    if length <= tol:
        return None
    u = (d[0] / length, d[1] / length)
    v = (-u[1], u[0])
    local = [((p[0] - ox) * u[0] + (p[1] - oy) * u[1], (p[0] - ox) * v[0] + (p[1] - oy) * v[1]) for p in ring]
    for a, b in zip(local, local[1:] + local[:1]):
        if abs(a[0] - b[0]) > tol and abs(a[1] - b[1]) > tol:
            return None
    z = min(p[2] for p in ring)
    by_x = _rect_slabs(local, tol)
    by_y = [(x0, x1, y0, y1) for y0, y1, x0, x1 in _rect_slabs([(y, x) for x, y in local], tol)]
    best = by_x if len(by_x) <= len(by_y) else by_y

    def world(x, y):
        return (ox + x * u[0] + y * v[0], oy + x * u[1] + y * v[1], z)

    return [[world(x0, y0), world(x1, y0), world(x1, y1), world(x0, y1)] for x0, x1, y0, y1 in best]


def squared(ring, tol_deg: float = 5.0, min_edge: float = 0.05):
    """B72: a nearly right-angled footprint made exactly right-angled, else ``None``.

    Real footprints are a few tenths of a degree off and carry almost-straight extra points,
    which the exact checks of the pitched roofs refuse. The main direction is the
    length-weighted mean of the edge directions modulo 90°; every edge must lie within
    ``tol_deg`` of it or its normal (edges shorter than ``min_edge`` are ignored).
    Neighbouring edges on the same axis merge into one line (length-weighted offset), and
    the corners are where the lines meet. Counter-clockwise, at the lowest z of ``ring``."""
    ring = ccw(ring)
    edges = [(a, b) for a, b in zip(ring, ring[1:] + ring[:1]) if _dist2(a, b) >= min_edge]
    if len(edges) < 4:
        return None
    sx = sy = 0.0
    for a, b in edges:
        ang, length = math.atan2(b[1] - a[1], b[0] - a[0]), _dist2(a, b)
        sx += length * math.cos(4 * ang)
        sy += length * math.sin(4 * ang)
    phi = math.atan2(sy, sx) / 4
    u, v = (math.cos(phi), math.sin(phi)), (-math.sin(phi), math.cos(phi))
    lines = []  # [axis, offset, weight]: axis "x" = an edge along u at local y = offset
    for a, b in edges:
        (ax, ay), (bx, by) = [((p[0] * u[0] + p[1] * u[1]), (p[0] * v[0] + p[1] * v[1])) for p in (a, b)]
        dev = math.degrees(math.atan2(abs(by - ay), abs(bx - ax)))  # 0 = along u, 90 = along v
        if tol_deg < dev < 90 - tol_deg:
            return None
        axis, offset = ("x", (ay + by) / 2) if dev <= tol_deg else ("y", (ax + bx) / 2)
        w = _dist2(a, b)
        if lines and lines[-1][0] == axis:
            last = lines[-1]
            last[1], last[2] = (last[1] * last[2] + offset * w) / (last[2] + w), last[2] + w
        else:
            lines.append([axis, offset, w])
    if len(lines) > 1 and lines[0][0] == lines[-1][0]:
        a, b = lines.pop(), lines[0]
        b[1], b[2] = (a[1] * a[2] + b[1] * b[2]) / (a[2] + b[2]), a[2] + b[2]
    if len(lines) < 4:
        return None
    z = min(p[2] for p in ring)
    out = []
    for line, nxt in zip(lines, lines[1:] + lines[:1]):
        x, y = (nxt[1], line[1]) if line[0] == "x" else (line[1], nxt[1])
        out.append((x * u[0] + y * v[0], x * u[1] + y * v[1], z))
    return out if signed_area(out) > 0 else None


def _dist2(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def prism(footprint, height: float):
    """LoD1 block: ``(verts, faces, labels)``; base at each point's z, flat top at min z + height."""
    ring = ccw(footprint)
    n = len(ring)
    base = min(p[2] for p in ring)
    top = base + height
    verts = [(x, y, base) for x, y, _ in ring] + [(x, y, top) for x, y, _ in ring]
    faces = [list(reversed(range(n))), list(range(n, 2 * n))]
    labels = ["GroundSurface", "RoofSurface"]
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j, n + i])
        labels.append("WallSurface")
    return verts, faces, labels
