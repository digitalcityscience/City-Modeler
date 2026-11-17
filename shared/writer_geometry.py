# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Consolidated writer geometry helpers shared by CityGML 2.0 and 3.0 exporters.

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Literal, Optional, Tuple
from xml.etree.ElementTree import SubElement


Formatter = Callable[[float], str]
QNameFactory = Callable[[str, str], str]
Ring3D = List[Tuple[float, float, float]]

# Default point-coincidence tolerance, matching the convention already used
# for this kind of check elsewhere in the codebase
# (``Openings_Cutter.Openings_Cutter.cleaned_polygon_points``).
_DEFAULT_POINT_TOL = 1e-6
_DEFAULT_MIN_AREA = 1e-6

RingInvalidReason = Literal["too_few_points", "degenerate_area"]

RING_INVALID_TOO_FEW_POINTS: RingInvalidReason = "too_few_points"
RING_INVALID_DEGENERATE_AREA: RingInvalidReason = "degenerate_area"


@dataclass(frozen=True)
class RingValidation:
    """Verdict from :func:`validate_ring`, plus enough detail for callers to
    build a useful rejection message (see facade-authoring/export-error
    ticket 01)."""

    valid: bool
    points: Ring3D
    point_count: int
    area: float
    reason: Optional[RingInvalidReason]


def _same_xyz(a, b, tol: float) -> bool:
    return (
        abs(a[0] - b[0]) <= tol
        and abs(a[1] - b[1]) <= tol
        and abs(a[2] - b[2]) <= tol
    )


def _loop_area(points: Ring3D) -> float:
    """Unsigned area of a planar 3D polygon loop (Newell's method)."""
    n = len(points)
    if n < 3:
        return 0.0
    nx = ny = nz = 0.0
    for i in range(n):
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return 0.5 * (nx * nx + ny * ny + nz * nz) ** 0.5


def shell_is_closed(rings, ndigits: int = 4) -> bool:
    """True when every edge of ``rings`` (all exterior and interior rings of a shell's
    polygons) is used by exactly two of them: a closed, watertight shell (B39). A hole
    without its filling, a missing face or a T-junction counts as open."""
    from collections import Counter

    edges = Counter()
    for ring in rings:
        pts = [tuple(round(float(c), ndigits) for c in p) for p in ring]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            if a != b:
                edges[frozenset((a, b))] += 1
    return bool(edges) and all(n == 2 for n in edges.values())


def dedupe_ring_points(points: Ring3D, tol: float = _DEFAULT_POINT_TOL) -> List[Tuple[float, float, float]]:
    """Collapse consecutive-duplicate and duplicate-closing points from a 3D
    point loop, mirroring ``Openings_Cutter.Openings_Cutter.cleaned_polygon_points``.
    Does not mutate ``points``. Pure cleanup with no validity judgement -
    callers that need to know whether the *result* is degenerate should call
    :func:`validate_ring` instead, which uses this internally."""
    cleaned: List[Tuple[float, float, float]] = []
    for point in points or []:
        p = (point[0], point[1], point[2])
        if cleaned and _same_xyz(cleaned[-1], p, tol):
            continue
        cleaned.append(p)

    if len(cleaned) >= 2 and _same_xyz(cleaned[0], cleaned[-1], tol):
        cleaned.pop()

    return cleaned


def validate_ring(
    points: Ring3D,
    tol: float = _DEFAULT_POINT_TOL,
    min_area: float = _DEFAULT_MIN_AREA,
) -> RingValidation:
    """Decide whether a 3D point loop is geometrically valid enough to use.

    This is the single place that answers "is this ring degenerate?" for
    both an opening's exterior boundary and a wall polygon's interior hole
    ring (export-error findings doc,
    ``docs/agent/export-error/opening-ring-degeneracy-findings.md``). It
    catches two patterns observed in CityDoctor-flagged output: a ring
    collapsed to 2 unique vertices (``A -> B -> B -> A``), and a ring with
    the right unique-corner count but a duplicated start vertex
    (``P1 -> P1 -> P2 -> P3 -> P4 -> P1``) that only reveals itself as
    degenerate once consecutive duplicates are collapsed.
    """
    cleaned = dedupe_ring_points(points, tol)
    point_count = len(cleaned)
    if point_count < 3:
        return RingValidation(False, cleaned, point_count, 0.0, RING_INVALID_TOO_FEW_POINTS)

    area = _loop_area(cleaned)
    if area <= min_area:
        return RingValidation(False, cleaned, point_count, area, RING_INVALID_DEGENERATE_AREA)

    return RingValidation(True, cleaned, point_count, area, None)


def describe_ring_invalid(result: RingValidation) -> str:
    """Human-readable detail for an invalid :func:`validate_ring` result -
    the wording callers (export-error tickets 02/03) fold into their own
    error/refusal message. Only meaningful when ``result.valid`` is False."""
    if result.reason == RING_INVALID_TOO_FEW_POINTS:
        return f"only {result.point_count} distinct point(s)"
    return "zero area"


def format_poslist(ring_xyz: Ring3D, fmt: Formatter) -> str:
    flat: list[str] = []
    for x, y, z in ring_xyz:
        flat.extend((fmt(x), fmt(y), fmt(z)))
    return " ".join(flat)


def _raise_if_degenerate(points: Ring3D, poly_gid: str, ring_id: str, ring_label: str) -> None:
    """Defense-in-depth halt for the last point before ring coordinates
    become XML (export-error ticket 04). Everything upstream (tickets 01-03)
    is meant to catch degenerate rings earlier, but if one still reaches
    here - a gap those tickets didn't cover, a future regression, or a scene
    edited by hand outside the authoring tools - halt loudly rather than
    emit malformed ``posList`` text the way ``window18.gml`` did."""
    result = validate_ring(points)
    if not result.valid:
        raise ValueError(
            f"Refusing to export degenerate CityGML geometry: polygon "
            f"'{poly_gid}' {ring_label} (ring id '{ring_id}') is degenerate "
            f"({describe_ring_invalid(result)})"
        )


def _paired_interior_rings(
    rings: Optional[List[Ring3D]], ids: Optional[List[str]]
) -> List[Tuple[int, Ring3D, str]]:
    """(index, ring, ring_id) triples for the interior rings that will
    actually be emitted - the same ``zip`` pairing both the validate and
    emit loops below need, computed once."""
    if not rings or not ids:
        return []
    return [(idx, ring, rid) for idx, (ring, rid) in enumerate(zip(rings, ids))]


def write_polygon_with_ring_ids(
    parent_ms,
    poly_gid: str,
    exterior_ring: Ring3D,
    interior_rings: Optional[List[Ring3D]],
    srs_name: Optional[str],
    exterior_ring_id: str,
    interior_ring_ids: Optional[List[str]],
    *,
    qname: QNameFactory,
    fmt: Formatter,
):
    interior_pairs = _paired_interior_rings(interior_rings, interior_ring_ids)
    _raise_if_degenerate(exterior_ring, poly_gid, exterior_ring_id, "exterior ring")
    for idx, ring, rid in interior_pairs:
        _raise_if_degenerate(ring, poly_gid, rid, f"interior ring #{idx}")

    s_member = SubElement(parent_ms, qname("gml", "surfaceMember"))
    gpoly = SubElement(s_member, qname("gml", "Polygon"), {qname("gml", "id"): poly_gid})

    exterior = SubElement(gpoly, qname("gml", "exterior"))
    lre = SubElement(exterior, qname("gml", "LinearRing"), {qname("gml", "id"): exterior_ring_id})
    posliste = SubElement(lre, qname("gml", "posList"))
    posliste.set("srsDimension", "3")
    posliste.text = format_poslist(exterior_ring, fmt)

    for _idx, ring, rid in interior_pairs:
        interior = SubElement(gpoly, qname("gml", "interior"))
        lri = SubElement(interior, qname("gml", "LinearRing"), {qname("gml", "id"): rid})
        poslisti = SubElement(lri, qname("gml", "posList"))
        poslisti.set("srsDimension", "3")
        poslisti.text = format_poslist(ring, fmt)


def write_compositesurface_with_polygons(
    parent_ms,
    cs_id: str,
    polygons: List[dict],
    srs_name: Optional[str],
    *,
    qname: QNameFactory,
    fmt: Formatter,
):
    interior_pairs_by_poly = [
        _paired_interior_rings(poly.get("int_xyz"), poly.get("int_ids")) for poly in polygons
    ]
    for poly, interior_pairs in zip(polygons, interior_pairs_by_poly):
        _raise_if_degenerate(poly["ext_xyz"], poly["poly_gid"], poly["ext_id"], "exterior ring")
        for idx, ring, rid in interior_pairs:
            _raise_if_degenerate(ring, poly["poly_gid"], rid, f"interior ring #{idx}")

    s_member = SubElement(parent_ms, qname("gml", "surfaceMember"))
    cs = SubElement(s_member, qname("gml", "CompositeSurface"), {qname("gml", "id"): cs_id})

    for poly, interior_pairs in zip(polygons, interior_pairs_by_poly):
        poly_s_member = SubElement(cs, qname("gml", "surfaceMember"))
        gpoly = SubElement(poly_s_member, qname("gml", "Polygon"), {qname("gml", "id"): poly["poly_gid"]})

        exterior = SubElement(gpoly, qname("gml", "exterior"))
        lre = SubElement(exterior, qname("gml", "LinearRing"), {qname("gml", "id"): poly["ext_id"]})
        posliste = SubElement(lre, qname("gml", "posList"))
        posliste.set("srsDimension", "3")
        posliste.text = format_poslist(poly["ext_xyz"], fmt)

        for _idx, ring, rid in interior_pairs:
            interior = SubElement(gpoly, qname("gml", "interior"))
            lri = SubElement(interior, qname("gml", "LinearRing"), {qname("gml", "id"): rid})
            poslisti = SubElement(lri, qname("gml", "posList"))
            poslisti.set("srsDimension", "3")
            poslisti.text = format_poslist(ring, fmt)
