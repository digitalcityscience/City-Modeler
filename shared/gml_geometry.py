"""T11b: one GML geometry reader for CityGML 1.0/2.0 (GML 3.1.1) and 3.0
(GML 3.2), producing ``shared/citygml_ir.py::GeometryNode``.

Pure module (ElementTree or lxml elements, no bpy). Coordinates are returned
as read: no CRS axis swap, no origin shift (the builder applies those, T11d).
xlink references are not resolved; a property that only points elsewhere
becomes ``GeometryNode(href=...)`` and member references inside a geometry are
kept in ``extra["member_hrefs"]``.

Id rule (same as ``io/reader/geometry.py::_rings_from_polygon`` and
``build_geometry``): ``polygon_id`` is the ``gml:id`` of the Polygon (or
patch), ``ring_id`` the ``gml:id`` of the LinearRing, both ``""`` when
absent. Rings are closed like ``shared/xml_parsing.py::parse_poslist`` does.

Broken coordinates never raise: the ring (an exterior ring takes its polygon
with it) is skipped and a message is appended to ``warnings`` and logged.
"""
from __future__ import annotations

import logging
from typing import Iterator, List, Optional, Tuple

try:
    from .citygml_ir import GeometryNode
    from .semantic_tree import PolygonNode, RingNode
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.citygml_ir import GeometryNode
    from shared.semantic_tree import PolygonNode, RingNode

log = logging.getLogger(__name__)

GML_NAMESPACES = ("http://www.opengis.net/gml", "http://www.opengis.net/gml/3.2")
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

_POLYGON_LIKE = {"Polygon", "PolygonPatch", "Triangle", "Rectangle"}
# Surfaces whose polygons sit in a patch list.
_PATCHED = {"Surface", "TriangulatedSurface", "Tin", "PolyhedralSurface"}
_PATCH_LISTS = {"patches", "trianglePatches", "polygonPatches"}
_SURFACE_AGGREGATES = {"MultiSurface", "CompositeSurface", "Shell", "OrientableSurface",
                       "Solid", "CompositeSolid", "MultiSolid", "MultiGeometry", "GeometricComplex"}
_CURVES = {"LineString", "Curve", "LineStringSegment", "MultiCurve", "CompositeCurve",
           "OrientableCurve"}
_POINTS = {"Point", "MultiPoint"}
GEOMETRY_TYPES = frozenset(_POLYGON_LIKE | _PATCHED | _SURFACE_AGGREGATES | _CURVES | _POINTS
                           | {"LinearRing"})

Coord = Tuple[float, float, float]


def _split(tag) -> Tuple[str, str]:
    if not isinstance(tag, str):
        return "", ""
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def is_gml(el, *names: str) -> bool:
    ns, local = _split(el.tag)
    return ns in GML_NAMESPACES and (not names or local in names)


def _local(el) -> str:
    return _split(el.tag)[1]


def _gml_id(el) -> str:
    for ns in GML_NAMESPACES:
        value = el.get(f"{{{ns}}}id")
        if value:
            return value
    return ""


def _children(el, *names: str):
    return [c for c in el if is_gml(c, *names)]


def _first(el, *names: str):
    for c in el:
        if is_gml(c, *names):
            return c
    return None


class _Ctx:
    def __init__(self, warnings: Optional[list]):
        self.warnings = warnings if warnings is not None else []
        self.polygons: List[PolygonNode] = []
        self.curves: List[Tuple[Coord, ...]] = []
        self.points: List[Coord] = []
        self.hrefs: List[str] = []

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning("gml_geometry: %s", msg)


# -- coordinates ---------------------------------------------------------

def _dimension(el, fallback: Optional[int]) -> Optional[int]:
    dim = el.get("srsDimension") or el.get("dimension")
    if dim and dim.strip().isdigit():
        return int(dim)
    return fallback


def _to_xyz(values: List[float], dim: int) -> List[Coord]:
    if dim >= 3:
        return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), dim)]
    return [(values[i], values[i + 1], 0.0) for i in range(0, len(values), dim)]


def _floats(text: str) -> List[float]:
    return [float(v) for v in text.split()]


def _read_poslist(el, dim: Optional[int], where: str, ctx: _Ctx) -> Optional[List[Coord]]:
    try:
        values = _floats(el.text or "")
    except ValueError:
        ctx.warn(f"{where}: posList has a non-numeric value")
        return None
    dim = _dimension(el, dim)
    if dim is None:
        dim = 3 if len(values) % 3 == 0 else 2
    if dim < 2:
        ctx.warn(f"{where}: srsDimension {dim} is not usable")
        return None
    if len(values) % dim:
        ctx.warn(f"{where}: posList has {len(values)} values, not a multiple of srsDimension {dim}")
        return None
    count = el.get("count")
    if count and count.strip().isdigit() and int(count) * dim != len(values):
        ctx.warn(f"{where}: posList count={count} but {len(values) // dim} positions")
        return None
    return _to_xyz(values, dim)


def _read_pos(el, dim: Optional[int], where: str, ctx: _Ctx) -> Optional[Coord]:
    try:
        values = _floats(el.text or "")
    except ValueError:
        ctx.warn(f"{where}: pos has a non-numeric value")
        return None
    dim = _dimension(el, dim) or len(values)
    if dim < 2 or len(values) != dim:
        ctx.warn(f"{where}: pos has {len(values)} values for srsDimension {dim}")
        return None
    return _to_xyz(values, dim)[0]


def _read_coordinates(el, where: str, ctx: _Ctx) -> Optional[List[Coord]]:
    """GML 3.1.1 ``gml:coordinates`` (removed in 3.2 but still read there)."""
    cs, ts, dec = el.get("cs") or ",", el.get("ts") or " ", el.get("decimal") or "."
    text = (el.text or "").strip()
    tuples = text.split() if ts.isspace() else [t for t in text.split(ts) if t.strip()]
    out = []
    for t in tuples:
        parts = [p for p in t.strip().split(cs) if p != ""]
        try:
            values = [float(p.replace(dec, ".") if dec != "." else p) for p in parts]
        except ValueError:
            ctx.warn(f"{where}: coordinates has a non-numeric value")
            return None
        if len(values) not in (2, 3):
            ctx.warn(f"{where}: coordinates tuple with {len(values)} values")
            return None
        out.append((values[0], values[1], values[2] if len(values) == 3 else 0.0))
    return out


def _read_positions(el, dim: Optional[int], where: str, ctx: _Ctx) -> Optional[List[Coord]]:
    """Positions of a LinearRing / LineString / segment / Point: posList,
    a run of pos / pointProperty / pointRep, or coordinates."""
    dim = _dimension(el, dim)
    pos_list = _first(el, "posList")
    if pos_list is not None:
        return _read_poslist(pos_list, dim, where, ctx)
    coordinates = _first(el, "coordinates")
    if coordinates is not None:
        return _read_coordinates(coordinates, where, ctx)
    out = []
    for c in el:
        if is_gml(c, "pos"):
            p = _read_pos(c, dim, where, ctx)
        elif is_gml(c, "pointProperty", "pointRep"):
            point = _first(c, "Point")
            if point is None:
                ctx.warn(f"{where}: {_local(c)} without an inline Point "
                         f"({c.get(_XLINK_HREF) or 'empty'})")
                return None
            pts = _read_positions(point, dim, where, ctx)
            p = pts[0] if pts and len(pts) == 1 else None
        else:
            continue
        if p is None:
            return None
        out.append(p)
        if dim is None and is_gml(c, "pos"):
            dim = _dimension(c, None) or len(c.text.split())  # the run keeps one dimension
    return out


# -- polygons ------------------------------------------------------------

def _ring(boundary, poly_id: str, role: str, dim, ctx: _Ctx) -> Optional[RingNode]:
    lr = _first(boundary, "LinearRing", "Ring")
    if lr is None:
        ctx.warn(f"polygon {poly_id or '?'}: {role} without a LinearRing")
        return None
    ring_id = _gml_id(lr)
    where = f"polygon {poly_id or '?'} {role} ring {ring_id or '?'}"
    if _local(lr) == "Ring":  # curve-member ring: join the member curves
        coords = []
        for member in _children(lr, "curveMember"):
            for curve in member:
                if is_gml(curve):
                    part = _curve_positions(curve, dim, where, ctx)
                    if part is None:
                        return None
                    coords.extend(part)
    else:
        coords = _read_positions(lr, dim, where, ctx)
    if coords is None:
        return None
    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords = list(coords) + [coords[0]]
    if len(coords) < 4:
        ctx.warn(f"{where}: {len(coords)} positions, a ring needs at least 4")
        return None
    return RingNode(ring_id, poly_id, role, tuple(coords))


def _polygon(el, dim, ms_id: str, cs_id: str, ctx: _Ctx) -> None:
    poly_id = _gml_id(el)
    dim = _dimension(el, dim)
    # GML 3.1.1 also allows outerBoundaryIs/innerBoundaryIs (GML 2 names).
    ext = _first(el, "exterior", "outerBoundaryIs")
    if ext is None:
        ctx.warn(f"polygon {poly_id or '?'}: no exterior, skipped")
        return
    exterior = _ring(ext, poly_id, "exterior", dim, ctx)
    if exterior is None:
        ctx.warn(f"polygon {poly_id or '?'}: exterior ring unreadable, polygon skipped")
        return
    interiors = []
    for b in _children(el, "interior", "innerBoundaryIs"):
        r = _ring(b, poly_id, "interior", dim, ctx)
        if r is not None:
            interiors.append(r)
    ctx.polygons.append(PolygonNode(poly_id, exterior, tuple(interiors),
                                    multisurface_id=ms_id, compositesurface_id=cs_id,
                                    is_multisurface_member=bool(ms_id)))


def _members(el):
    """Geometry children of an aggregate, through any member property;
    hrefs of members that only point elsewhere come back separately."""
    geoms, hrefs = [], []
    for prop in el:
        if not is_gml(prop):
            continue
        if _local(prop) in GEOMETRY_TYPES:  # e.g. Solid/exterior/CompositeSurface
            geoms.append(prop)
            continue
        inline = [c for c in prop if is_gml(c) and _local(c) in GEOMETRY_TYPES]
        if inline:
            geoms.extend(inline)
        elif prop.get(_XLINK_HREF):
            hrefs.append(prop.get(_XLINK_HREF))
        elif _local(prop) in _PATCH_LISTS:
            geoms.extend(c for c in prop if is_gml(c))
    return geoms, hrefs


def _walk(el, dim, ms_id: str, cs_id: str, ctx: _Ctx) -> None:
    name = _local(el)
    dim = _dimension(el, dim)
    if name in _POLYGON_LIKE:
        _polygon(el, dim, ms_id, cs_id, ctx)
        return
    if name in _CURVES:
        _curves(el, dim, ctx)
        return
    if name in _POINTS:
        _points(el, dim, ctx)
        return
    if name == "MultiSurface":
        ms_id = _gml_id(el) or ms_id
    elif name == "CompositeSurface":
        cs_id = _gml_id(el) or cs_id
    if name in _PATCHED or name in _SURFACE_AGGREGATES:
        geoms, hrefs = _members(el)
        ctx.hrefs.extend(hrefs)
        for g in geoms:
            _walk(g, dim, ms_id, cs_id, ctx)


# -- curves and points ---------------------------------------------------

def _curve_positions(el, dim, where: str, ctx: _Ctx) -> Optional[List[Coord]]:
    name = _local(el)
    if name in ("LineString", "LineStringSegment"):
        return _read_positions(el, dim, where, ctx)
    if name == "Curve":
        out = []
        for seg_list in _children(el, "segments"):
            for seg in seg_list:
                if not is_gml(seg):
                    continue
                part = _read_positions(seg, _dimension(seg, dim), where, ctx)
                if part is None:
                    return None
                # consecutive segments share their joint position
                out.extend(part[1:] if out and part and part[0] == out[-1] else part)
        return out
    if name == "OrientableCurve":
        base = _first(el, "baseCurve")
        inner = _first(base, *_CURVES) if base is not None else None
        return _curve_positions(inner, dim, where, ctx) if inner is not None else None
    ctx.warn(f"{where}: unsupported curve {name}")
    return None


def _curves(el, dim, ctx: _Ctx) -> None:
    name = _local(el)
    if name in ("MultiCurve", "CompositeCurve"):
        geoms, hrefs = _members(el)
        ctx.hrefs.extend(hrefs)
        for g in geoms:
            _curves(g, _dimension(el, dim), ctx)
        return
    where = f"{name} {_gml_id(el) or '?'}"
    coords = _curve_positions(el, dim, where, ctx)
    if coords is None:
        return
    if len(coords) < 2:
        ctx.warn(f"{where}: {len(coords)} positions, a curve needs at least 2")
        return
    ctx.curves.append(tuple(coords))


def _points(el, dim, ctx: _Ctx) -> None:
    if _local(el) == "MultiPoint":
        geoms, hrefs = _members(el)
        ctx.hrefs.extend(hrefs)
        for g in geoms:
            _points(g, _dimension(el, dim), ctx)
        return
    where = f"Point {_gml_id(el) or '?'}"
    coords = _read_positions(el, dim, where, ctx)
    if coords is not None and len(coords) == 1:
        ctx.points.append(coords[0])
    elif coords is not None:
        ctx.warn(f"{where}: {len(coords)} positions, a point needs exactly 1")


# -- public API ----------------------------------------------------------

def parse_geometry(el, warnings: Optional[list] = None) -> GeometryNode:
    """``GeometryNode`` for a GML geometry element, or for a property
    element holding one (``lod2MultiSurface``, ``surfaceMember``, ...).
    A property that only carries an xlink becomes ``GeometryNode(href=...)``."""
    geom = el
    if not (is_gml(el) and _local(el) in GEOMETRY_TYPES):
        geom = next((c for c in el if is_gml(c) and _local(c) in GEOMETRY_TYPES), None)
        if geom is None:
            href = el.get(_XLINK_HREF) or ""
            if not href:
                ctx = _Ctx(warnings)
                ctx.warn(f"{_local(el) or '?'}: no GML geometry inside")
            return GeometryNode(href=href)
    ctx = _Ctx(warnings)
    _walk(geom, None, "", "", ctx)
    extra = {"member_hrefs": list(ctx.hrefs)} if ctx.hrefs else {}
    return GeometryNode(gml_id=_gml_id(geom), gml_type=_local(geom),
                        polygons=tuple(ctx.polygons), curves=tuple(ctx.curves),
                        points=tuple(ctx.points), extra=extra)


def iter_geometries(root, warnings: Optional[list] = None) -> Iterator[Tuple[object, GeometryNode]]:
    """``(property_element, GeometryNode)`` for every outermost GML geometry
    below ``root``, in document order. Geometry nested in another geometry
    is part of the outer node."""
    stack = [root]
    while stack:
        el = stack.pop()
        kids = [c for c in el if isinstance(c.tag, str)]
        for c in kids:
            if is_gml(c) and _local(c) in GEOMETRY_TYPES:
                yield el, parse_geometry(c, warnings)
        stack.extend(reversed([c for c in kids
                               if not (is_gml(c) and _local(c) in GEOMETRY_TYPES)]))
