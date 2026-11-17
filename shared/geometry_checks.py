"""B60 (agent plan A5): geometry checks on a written CityGML file, no bpy.

They read what an export wrote (the same polygons CityDoctor sees), per Building / BuildingPart:

- ``GE_S_NOT_CLOSED``: edges of the shell (boundary polygons, their holes and the openings'
  fillings) not used by exactly two rings, for a building written with a lodNSolid (CityDoctor
  checks solids only); without a solid the same finding is ``CGML_SHELL_OPEN`` (information: the
  writer left the solid out because of it). An open edge whose end lies on another open edge is
  counted as a T-junction
- ``GE_R_SELF_INTERSECTION``: a ring crossing itself (2D, in its own plane)
- ``GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE``: a polygon vertex farther than ``tolerance`` from the
  polygon's plane (CityDoctor's distance rule, 1 cm)
- ``CGML_FILLING_WITHOUT_HOLE``: a Window / Door polygon that matches no hole (interior ring) of its
  wall and is no notch in the wall's outline either

Pure Python on purpose: it runs in a worker thread and in unit tests.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import Counter

GML_NS = ("http://www.opengis.net/gml", "http://www.opengis.net/gml/3.2")
BUILDINGS = ("Building", "BuildingPart")
FILLINGS = ("Window", "Door", "WindowSurface", "DoorSurface")  # 2.0 opening / 3.0 fillingSurface
SKIP = ("BuildingInstallation", "IntBuildingInstallation", "BuildingConstructiveElement",
        "BuildingRoom", "Room", "BuildingFurniture", "Storey", "BuildingUnit")
NON_PLANAR_TOL_M = 0.01
EDGE_DIGITS = 4          # 0.1 mm: same rounding as writer_geometry.shell_is_closed
FILLING_TOL_M = 0.01
_MAX_LISTED = 50


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _gml_id(el) -> str:
    for ns in GML_NS:
        v = el.get(f"{{{ns}}}id")
        if v:
            return v
    return ""


def _ring_points(ring_el):
    """Points of a gml:LinearRing (posList or pos), without the closing duplicate."""
    vals = []
    dim = 3
    for el in ring_el.iter():
        name = _local(el.tag)
        if name == "posList":
            dim = int(el.get("srsDimension") or 3)
            vals += [float(x) for x in (el.text or "").split()]
        elif name == "pos":
            vals += [float(x) for x in (el.text or "").split()]
    pts = [tuple(vals[i:i + 3]) for i in range(0, len(vals) - dim + 1, dim)] if dim == 3 else []
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _polygon(poly_el):
    ext, ints = [], []
    for child in poly_el:
        name = _local(child.tag)
        rings = [r for r in child.iter() if _local(r.tag) == "LinearRing"]
        if not rings:
            continue
        if name == "exterior":
            ext = _ring_points(rings[0])
        elif name == "interior":
            ints.append(_ring_points(rings[0]))
    return {"id": _gml_id(poly_el), "exterior": ext, "interiors": ints}


def _polygons_under(el):
    return [_polygon(p) for p in el.iter() if _local(p.tag) == "Polygon"]


def _collect_building(bldg_el):
    """``(surfaces, parts, has_solid)``: boundary surfaces with their polygons and fillings, nested
    parts, and whether the building itself carries a lodNSolid."""
    surfaces, parts, solid = [], [], []

    def walk(el, depth=0):
        for child in el:
            name = _local(child.tag)
            if name in SKIP:
                continue
            if name == "BuildingPart":
                parts.append(child)
                continue
            if name in ("lod1Solid", "lod2Solid", "lod3Solid", "lod4Solid"):
                solid.append(name)
                continue
            if name.endswith("Surface") and name not in ("MultiSurface", "CompositeSurface") \
                    and _local(el.tag) in ("boundedBy", "boundary"):
                surfaces.append(_surface(child, name))
                continue
            walk(child, depth + 1)
    walk(bldg_el)
    return surfaces, parts, bool(solid)


def _surface(surf_el, stype):
    own, fillings = [], []

    def walk(el):
        for child in el:
            name = _local(child.tag)
            if name in FILLINGS:
                fillings.append({"type": name, "id": _gml_id(child), "polygons": _polygons_under(child)})
                continue
            if name == "Polygon":
                own.append(_polygon(child))
                continue
            walk(child)
    walk(surf_el)
    return {"type": stype, "id": _gml_id(surf_el), "polygons": own, "fillings": fillings}


def read_buildings(path):
    """``{gml_id: {"type", "surfaces"}}`` of every Building and BuildingPart in the file."""
    root = ET.parse(path).getroot()
    out = {}
    todo = [el for el in root.iter() if _local(el.tag) == "Building"]
    seen = set()
    while todo:
        el = todo.pop(0)
        if id(el) in seen:
            continue
        seen.add(id(el))
        surfaces, parts, solid = _collect_building(el)
        out[_gml_id(el) or f"<no-id {len(out)}>"] = {"type": _local(el.tag), "surfaces": surfaces, "solid": solid}
        todo.extend(parts)
    return out


# ---------------------------------------------------------------- geometry helpers
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _newell(pts):
    nx = ny = nz = 0.0
    for (x1, y1, z1), (x2, y2, z2) in zip(pts, pts[1:] + pts[:1]):
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / length, ny / length, nz / length) if length > 1e-12 else None


def plane_deviation(pts) -> float:
    """Largest distance of a point from the Newell plane through the centroid."""
    if len(pts) < 4:
        return 0.0
    n = _newell(pts)
    if n is None:
        return 0.0
    c = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    return max(abs(sum(d * k for d, k in zip(_sub(p, c), n))) for p in pts)


def _to_2d(pts):
    n = _newell(pts)
    if n is not None:
        drop = max(range(3), key=lambda i: abs(n[i]))
    else:  # zero net area (a bow-tie whose halves cancel): drop the flattest axis
        drop = min(range(3), key=lambda i: max(p[i] for p in pts) - min(p[i] for p in pts))
    keep = [i for i in range(3) if i != drop]
    return [(p[keep[0]], p[keep[1]]) for p in pts]


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1, p2, p3, p4, eps=1e-9):
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
            ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)):
        return True

    def on(a, b, c):  # c on segment a-b (collinear touch)
        return abs(_orient(a, b, c)) <= eps and min(a[0], b[0]) - eps <= c[0] <= max(a[0], b[0]) + eps \
            and min(a[1], b[1]) - eps <= c[1] <= max(a[1], b[1]) + eps
    return on(p3, p4, p1) or on(p3, p4, p2) or on(p1, p2, p3) or on(p1, p2, p4)


def ring_self_intersects(pts) -> bool:
    """True when two non-adjacent edges of the ring touch or cross, or an edge runs back along
    the previous one (in the ring's own plane)."""
    flat = _to_2d(pts) if len(pts) >= 4 else None
    if flat is None:
        return False
    n = len(flat)
    edges = [(flat[i], flat[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        (a0, a1), (b0, b1) = edges[i], edges[(i + 1) % n]
        da, db = (a1[0] - a0[0], a1[1] - a0[1]), (b1[0] - b0[0], b1[1] - b0[1])
        if abs(_orient(a0, a1, b1)) <= 1e-9 and da[0] * db[0] + da[1] * db[1] < 0:
            return True  # a spike: the next edge runs back along this one
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_cross(*edges[i], *edges[j]):
                return True
    return False


def _key(p, nd=EDGE_DIGITS):
    return tuple(round(c, nd) for c in p)


def open_edges(rings):
    """``[(a, b, uses)]`` of the edges not used by exactly two rings."""
    edges = Counter()
    for ring in rings:
        pts = [_key(p) for p in ring]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if a != b:
                edges[frozenset((a, b))] += 1
    return [(*sorted(e), n) for e, n in edges.items() if n != 2]


def _point_on_segment(p, a, b, tol=1e-3):
    ab, ap = _sub(b, a), _sub(p, a)
    ll = sum(c * c for c in ab)
    if ll < 1e-18:
        return False
    t = sum(x * y for x, y in zip(ap, ab)) / ll
    if t <= 1e-6 or t >= 1 - 1e-6:
        return False
    proj = tuple(a[i] + ab[i] * t for i in range(3))
    return math.dist(proj, p) <= tol


def _t_junctions(edges):
    """Open edges with a vertex of another open edge lying inside them (a T-junction)."""
    verts = {v for a, b, _n in edges for v in (a, b)}
    return [(a, b) for a, b, _n in edges if any(_point_on_segment(v, a, b) for v in verts if v not in (a, b))]


def _same_points(ring_a, ring_b, tol=FILLING_TOL_M):
    if not ring_a or not ring_b:
        return False
    return all(any(math.dist(p, q) <= tol for q in ring_b) for p in ring_a)


# ---------------------------------------------------------------- checks
def check_building(bldg, tolerance=NON_PLANAR_TOL_M) -> dict:
    """``{"codes": {code: count}, "details": {code: [...]}, "closed": bool}`` for one building."""
    codes, details = Counter(), {}
    shell = []
    for s in bldg["surfaces"]:
        for p in s["polygons"]:
            shell.append(p["exterior"])
            shell.extend(p["interiors"])
            for ring in [p["exterior"], *p["interiors"]]:
                if ring_self_intersects(ring):
                    codes["GE_R_SELF_INTERSECTION"] += 1
                    details.setdefault("GE_R_SELF_INTERSECTION", []).append({"polygon": p["id"], "surface": s["id"]})
            dev = plane_deviation(p["exterior"])
            if dev > tolerance:
                codes["GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE"] += 1
                details.setdefault("GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE", []).append(
                    {"polygon": p["id"], "surface": s["id"], "value_m": round(dev, 4)})
        holes = [r for p in s["polygons"] for r in p["interiors"]]
        outlines = [p["exterior"] for p in s["polygons"]]
        for f in s["fillings"]:
            verts = [p for fp in f["polygons"] for p in fp["exterior"]]
            shell.extend(fp["exterior"] for fp in f["polygons"])
            if not verts:
                continue
            # it plugs a hole of its wall (every hole corner is one of its corners; a recessed 3D
            # window has more), or it is a ground-notch door (4 of its corners are outline corners)
            fitted = any(_same_points(h, verts) for h in holes) or any(
                sum(any(math.dist(v, q) <= FILLING_TOL_M for q in o) for v in verts) >= 4 for o in outlines)
            if not fitted:
                codes["CGML_FILLING_WITHOUT_HOLE"] += 1
                details.setdefault("CGML_FILLING_WITHOUT_HOLE", []).append(
                    {"filling": f["id"], "type": f["type"], "surface": s["id"]})
    edges = open_edges(shell) if shell else []
    if edges:
        # like CityDoctor: an error only for a building written with a solid; otherwise the open
        # shell is why the writer left the solid out (B48) and is reported as information
        code = "GE_S_NOT_CLOSED" if bldg.get("solid") else "CGML_SHELL_OPEN"
        codes[code] += 1
        tj = _t_junctions(edges)
        details[code] = {
            "open_edges": len(edges), "t_junctions": len(tj),
            "edges": [{"a": a, "b": b, "uses": n} for a, b, n in edges[:_MAX_LISTED]]}
    return {"codes": dict(codes), "details": details, "closed": not edges and bool(shell),
            "solid": bool(bldg.get("solid"))}


def check_file(path, tolerance=NON_PLANAR_TOL_M) -> dict:
    """``{gml_id: check_building(...)}`` for every Building / BuildingPart with polygons in ``path``."""
    out = {}
    for gid, bldg in read_buildings(path).items():
        if any(s["polygons"] for s in bldg["surfaces"]):
            out[gid] = check_building(bldg, tolerance)
    return out


def summary(results) -> dict:
    """``{code: count}`` over all buildings."""
    total = Counter()
    for r in results.values():
        total.update(r["codes"])
    return dict(total)
