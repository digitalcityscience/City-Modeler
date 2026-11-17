# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/roof_shapes.py
"""Parametric LoD2 roofs on rectangular footprints (B05, B11; B87 Cone / Dome / Tower on any
convex footprint). Pure geometry.

Our own analytic shapes (no straight skeleton, nothing copied from other
projects). Each builder returns a closed solid as ``(verts, faces, labels)``
like ``building_shapes.prism``: GroundSurface first, then walls and roofs.
Gable ends are part of the wall (one polygon per wall), as in German LoD2
data.

Roof types use the AdV ``bldg:roofType`` code list values
(https://repository.gdi-de.org/schemas/adv/citygml/Codelisten/RoofTypeTypeAdV.xml).
"""

from __future__ import annotations

import math

# AdV codes, Hamburg LoD2 share (2026-11-04, 7,240 buildings) in the comment.
ROOF_TYPES = {
    "1000": "Flat",               # Flachdach, 44 %
    "2100": "Mono-pitch",         # Pultdach, 5 %
    "3100": "Gable",              # Satteldach, 41 %
    "3200": "Hip",                # Walmdach, 8 %
    "3500": "Pyramid",            # Zeltdach, 1.3 %
    # B11: common in Germany, absent from the Hamburg sample.
    "2200": "Split mono-pitch",   # Versetztes Pultdach
    "3300": "Half-hip",           # Krüppelwalmdach
    "3400": "Mansard",            # Mansardendach
    "3800": "Sawtooth",           # Sheddach
    "3900": "Barrel",             # Bogendach
    # B87: the rest of the AdV list that has a shape (5000 Mischform and 9999 Sonstiges have none:
    # they are attribute values only, set on the attribute card).
    "3600": "Cone",               # Kegeldach
    "3700": "Dome",               # Kuppeldach
    "4000": "Tower",              # Turmdach
}
RADIAL = ("3600", "3700", "4000")  # built around the footprint centre: any convex footprint
DOME_RINGS = 4

# Fixed shape constants of the B11 types (pitch stays the user's parameter).
HALF_HIP_FRACTION = 0.4      # top share of the ridge height that is hipped
MANSARD_LOWER_PITCH = 70.0   # steep lower part, degrees
MANSARD_INSET = 0.2          # horizontal run of the lower part, share of the width
SPLIT_STEP = 1.0             # m, height step between the two mono-pitch halves
SAWTOOTH_TEETH = 3
BARREL_SEGMENTS = 8


def rectangle_frame(ring, tol_deg: float = 2.0):
    """``(origin, u, v, length, width)`` of a 4-corner footprint with right angles, else ``None``.

    ``u`` runs along the first edge, ``v`` along the second (counter-clockwise ring)."""
    if len(ring) != 4:
        return None
    edges = [(ring[(i + 1) % 4][0] - ring[i][0], ring[(i + 1) % 4][1] - ring[i][1]) for i in range(4)]
    lengths = [math.hypot(*e) for e in edges]
    if min(lengths) <= 1e-6:
        return None
    for i in range(4):
        a, b = edges[i], edges[(i + 1) % 4]
        cos = (a[0] * b[0] + a[1] * b[1]) / (lengths[i] * lengths[(i + 1) % 4])
        if abs(cos) > math.sin(math.radians(tol_deg)):
            return None
    u = (edges[0][0] / lengths[0], edges[0][1] / lengths[0])
    v = (edges[1][0] / lengths[1], edges[1][1] / lengths[1])
    return (ring[0][0], ring[0][1]), u, v, lengths[0], lengths[1]


def _builder(frame, base_z):
    (ox, oy), u, v, _l, _w = frame
    verts = []

    def at(a, b, z):
        verts.append((ox + a * u[0] + b * v[0], oy + a * u[1] + b * v[1], z))
        return len(verts) - 1

    return verts, at


def roof_solid(ring, base_z: float, eave_z: float, roof_type: str, pitch_deg: float = 35.0,
               ridge_along_long: bool = True, flip: bool = False):
    """Closed LoD2 solid for a rectangular ``ring`` (counter-clockwise); ``None`` if not a rectangle.

    ``eave_z`` is the wall top. ``ridge_along_long`` puts the ridge (gable,
    hip) or the slope direction (mono-pitch) along the longer side; ``flip``
    puts the high side of a mono-pitch roof on the other long side."""
    if roof_type in RADIAL:
        return _radial_solid(ring, base_z, eave_z, roof_type, pitch_deg) if is_convex(ring) else None
    frame = rectangle_frame(ring)
    if roof_type == "1000" and frame is None and len(ring) >= 3:
        # B51: a flat roof is defined on any footprint: the block with its top as RoofSurface
        from .building_shapes import prism
        verts, faces, labels = prism([(x, y, base_z) for x, y, *_z in ring], eave_z - base_z)
        return verts, faces, labels
    if frame is None or roof_type not in ROOF_TYPES:
        return None
    if roof_type in ("3200", "3500"):
        ridge_along_long = True  # a hip ridge on the short side would be negative
    (_o, u, v, length, width) = frame
    if (length >= width) != ridge_along_long:
        # Re-start the ring one corner later so the ridge axis u is the other side.
        ring = ring[1:] + ring[:1]
        frame = rectangle_frame(ring)
        (_o, u, v, length, width) = frame
    verts, at = _builder(frame, base_z)
    L, W, e, b = length, width, eave_z, base_z
    t = math.tan(math.radians(pitch_deg))

    g = [at(0, 0, b), at(L, 0, b), at(L, W, b), at(0, W, b)]
    faces = [[g[3], g[2], g[1], g[0]]]
    labels = ["GroundSurface"]

    def add(face, label):
        faces.append(face)
        labels.append(label)

    if roof_type == "1000":
        top = [at(0, 0, e), at(L, 0, e), at(L, W, e), at(0, W, e)]
        for i in range(4):
            add([g[i], g[(i + 1) % 4], top[(i + 1) % 4], top[i]], "WallSurface")
        add(top, "RoofSurface")
    elif roof_type == "2100":
        h = W * t
        lo, hi = (e, e + h) if not flip else (e + h, e)
        top = [at(0, 0, lo), at(L, 0, lo), at(L, W, hi), at(0, W, hi)]
        for i in range(4):
            add([g[i], g[(i + 1) % 4], top[(i + 1) % 4], top[i]], "WallSurface")
        add(top, "RoofSurface")
    elif roof_type == "3100":
        h = W / 2 * t
        e0, e1, e2, e3 = at(0, 0, e), at(L, 0, e), at(L, W, e), at(0, W, e)
        r0, r1 = at(0, W / 2, e + h), at(L, W / 2, e + h)
        add([g[0], g[1], e1, e0], "WallSurface")
        add([g[1], g[2], e2, r1, e1], "WallSurface")      # gable end
        add([g[2], g[3], e3, e2], "WallSurface")
        add([g[3], g[0], e0, r0, e3], "WallSurface")      # gable end
        add([e0, e1, r1, r0], "RoofSurface")
        add([e2, e3, r0, r1], "RoofSurface")
    elif roof_type == "3200":
        d = W / 2                                         # same pitch on all four sides
        if L - 2 * d <= 1e-6:
            return roof_solid(ring, base_z, eave_z, "3500", pitch_deg, True, flip)
        h = W / 2 * t
        e0, e1, e2, e3 = at(0, 0, e), at(L, 0, e), at(L, W, e), at(0, W, e)
        r0, r1 = at(d, W / 2, e + h), at(L - d, W / 2, e + h)
        for i, (a, c) in enumerate(((e0, e1), (e1, e2), (e2, e3), (e3, e0))):
            add([g[i], g[(i + 1) % 4], c, a], "WallSurface")
        add([e0, e1, r1, r0], "RoofSurface")
        add([e1, e2, r1], "RoofSurface")
        add([e2, e3, r0, r1], "RoofSurface")
        add([e3, e0, r0], "RoofSurface")
    elif roof_type == "3500":
        h = min(L, W) / 2 * t
        e0, e1, e2, e3 = at(0, 0, e), at(L, 0, e), at(L, W, e), at(0, W, e)
        apex = at(L / 2, W / 2, e + h)
        for i, (a, c) in enumerate(((e0, e1), (e1, e2), (e2, e3), (e3, e0))):
            add([g[i], g[(i + 1) % 4], c, a], "WallSurface")
            add([a, c, apex], "RoofSurface")
    elif roof_type == "3300":
        h = W / 2 * t
        f = HALF_HIP_FRACTION
        vk, zk, d = W / 2 * (1 - f), e + h * (1 - f), W / 2 * f
        e0, e1, e2, e3 = at(0, 0, e), at(L, 0, e), at(L, W, e), at(0, W, e)
        k0a, k0b, k1a, k1b = at(0, vk, zk), at(0, W - vk, zk), at(L, vk, zk), at(L, W - vk, zk)
        r0, r1 = at(d, W / 2, e + h), at(L - d, W / 2, e + h)
        add([g[0], g[1], e1, e0], "WallSurface")
        add([g[1], g[2], e2, k1b, k1a, e1], "WallSurface")
        add([g[2], g[3], e3, e2], "WallSurface")
        add([g[3], g[0], e0, k0a, k0b, e3], "WallSurface")
        add([e0, e1, k1a, r1, r0, k0a], "RoofSurface")
        add([e2, e3, k0b, r0, r1, k1b], "RoofSurface")
        add([k1a, k1b, r1], "RoofSurface")
        add([k0b, k0a, r0], "RoofSurface")
    else:
        # The rest are extrusions along u of a cross-section in (v, z), eave to eave.
        profile, roof_kinds = _profile(roof_type, W, e, t)
        _extrude_profile(at, add, g, L, W, e, profile, roof_kinds)
    return verts, faces, labels


def is_convex(ring) -> bool:
    """A counter-clockwise ring without reflex corners (collinear points are fine)."""
    n = len(ring)
    for i in range(n):
        a, b, c = ring[i], ring[(i + 1) % n], ring[(i + 2) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -1e-9:
            return False
    return n >= 3


def _radial_solid(ring, base_z, eave_z, roof_type, pitch_deg):
    """B87 Cone / Tower (apex over the centre) and Dome (rings on a quarter circle, then the apex)
    on a convex footprint. Rise = pitch over the inradius, so 45 degrees is a hemisphere; a round
    footprint (many corners) gives the round roof, a rectangle a pyramid / cloister vault."""
    pts = [(p[0], p[1]) for p in ring]
    n = len(pts)
    edges = list(zip(pts, pts[1:] + pts[:1]))
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    cx = sum((a[0] + b[0]) * (a[0] * b[1] - b[0] * a[1]) for a, b in edges) / (3 * area)
    cy = sum((a[1] + b[1]) * (a[0] * b[1] - b[0] * a[1]) for a, b in edges) / (3 * area)
    radius = min(abs((b[0] - a[0]) * (cy - a[1]) - (b[1] - a[1]) * (cx - a[0])) / math.hypot(b[0] - a[0], b[1] - a[1])
                 for a, b in edges)
    rise = radius * math.tan(math.radians(max(pitch_deg, 1.0)))
    verts = [(x, y, base_z) for x, y in pts] + [(x, y, eave_z) for x, y in pts]
    faces, labels = [list(range(n - 1, -1, -1))], ["GroundSurface"]
    for i in range(n):
        faces.append([i, (i + 1) % n, n + (i + 1) % n, n + i])
        labels.append("WallSurface")
    lower = list(range(n, 2 * n))
    for k in range(1, DOME_RINGS if roof_type == "3700" else 1):
        angle = math.pi / 2 * k / DOME_RINGS
        s, z = math.cos(angle), eave_z + rise * math.sin(angle)
        upper = list(range(len(verts), len(verts) + n))
        verts += [(cx + s * (x - cx), cy + s * (y - cy), z) for x, y in pts]
        for i in range(n):  # same edge direction on both rings: a planar trapezoid
            faces.append([lower[i], lower[(i + 1) % n], upper[(i + 1) % n], upper[i]])
            labels.append("RoofSurface")
        lower = upper
    verts.append((cx, cy, eave_z + rise))
    for i in range(n):
        faces.append([lower[i], lower[(i + 1) % n], len(verts) - 1])
        labels.append("RoofSurface")
    return verts, faces, labels


def _profile(roof_type, W, e, t):
    """Top outline of the cross-section from (0, e) to (W, e_end) and, per segment, its label."""
    if roof_type == "2200":
        step = min(SPLIT_STEP, 0.9 * W * t)
        s = (W - step / t) / 2                       # same pitch on both halves
        pts = [(0, e), (s, e + s * t), (s, e + s * t + step), (W, e)]
        kinds = ["RoofSurface", "WallSurface", "RoofSurface"]
    elif roof_type == "3400":
        i = MANSARD_INSET * W
        zb = e + i * math.tan(math.radians(MANSARD_LOWER_PITCH))
        top = zb + (W / 2 - i) * t
        pts = [(0, e), (i, zb), (W / 2, top), (W - i, zb), (W, e)]
        kinds = ["RoofSurface"] * 4
    elif roof_type == "3800":
        n, h = SAWTOOTH_TEETH, W / SAWTOOTH_TEETH * t
        pts, kinds = [(0, e)], []
        for k in range(n):
            pts.append(((k + 1) * W / n, e + h))
            kinds.append("RoofSurface")
            if k < n - 1:
                pts.append(((k + 1) * W / n, e))
                kinds.append("WallSurface")   # vertical glazing step
    else:  # "3900": circular arc through both eaves, rise = W/2 * tan(pitch) / 2
        rise = W / 4 * t
        radius = (rise * rise + (W / 2) ** 2) / (2 * rise)
        cz = e + rise - radius
        n = BARREL_SEGMENTS
        pts = [(W * k / n, cz + math.sqrt(max(radius * radius - (W * k / n - W / 2) ** 2, 0.0))) for k in range(n + 1)]
        pts[0], pts[-1] = (0, e), (W, e)
        kinds = ["RoofSurface"] * n
    return pts, kinds


def _extrude_profile(at, add, g, L, W, e, profile, kinds):
    near = [at(0, v, z) for v, z in profile]      # u = 0 side
    far = [at(L, v, z) for v, z in profile]       # u = L side
    add([g[0], g[1], far[0], near[0]], "WallSurface")                      # eave wall v = 0
    add([g[1], g[2]] + list(reversed(far)), "WallSurface")                 # end wall u = L
    add([g[2], g[3], near[-1], far[-1]], "WallSurface")                    # eave wall v = W
    add([g[3], g[0]] + near, "WallSurface")                                # end wall u = 0
    for k, kind in enumerate(kinds):
        add([near[k], far[k], far[k + 1], near[k + 1]], kind)
