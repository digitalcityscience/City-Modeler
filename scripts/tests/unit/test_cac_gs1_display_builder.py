"""GS.1: record-first wall display mesh builder. Pure Python, no bpy.
N-hole kernel hardening + containment/overlap validation added by P3b.2
(docs/agent/authoring-core/phase3b-design-note.md §7/§12).

Scope: ``shared/authoring_core/blender/display_builder.py::build_surface_display``
only (docs/agent/authoring-core/gs-design-note.md S5). No edit_session wiring,
no operator, no export eligibility - see that file's module docstring.
"""
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender.display_builder import (  # noqa: E402
    DisplayBuildError, build_surface_display,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

# A 4x3 wall in the z=0 plane with one 1x1 interior (Window) ring.
WALL_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
# Wound opposite to WALL_EXT (CityGML interior-ring convention: opposite
# winding to the exterior, seen from the outward normal).
WIN_RING = ((1.0, 1.0, 0.0), (1.0, 2.0, 0.0), (2.0, 2.0, 0.0), (2.0, 1.0, 0.0))
# Two more non-overlapping interior rings, both inside WALL_EXT, for the
# N-hole (P3b.2) topology tests.
WIN_RING2 = ((2.5, 0.3, 0.0), (2.5, 1.0, 0.0), (3.2, 1.0, 0.0), (3.2, 0.3, 0.0))
WIN_RING3 = ((0.3, 0.3, 0.0), (0.3, 1.0, 0.0), (0.9, 1.0, 0.0), (0.9, 0.3, 0.0))
# Overlaps WIN_RING (shares the (1.5..2.0, 1.5..2.0) sub-square).
WIN_RING_OVERLAP = ((1.5, 1.5, 0.0), (1.5, 2.5, 0.0), (2.5, 2.5, 0.0), (2.5, 1.5, 0.0))
# Partially outside WALL_EXT's y in [0, 3] bound.
WIN_RING_OUTSIDE = ((3.5, 2.5, 0.0), (3.5, 3.5, 0.0), (4.5, 3.5, 0.0), (4.5, 2.5, 0.0))


def _loop_area_xy(coords):
    """Shoelace area for a planar XY-plane loop (test-local, independent of
    the module under test)."""
    n = len(coords)
    area2 = 0.0
    for i in range(n):
        x1, y1, _ = coords[i]
        x2, y2, _ = coords[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    return abs(area2) * 0.5


def _tri_area_3d(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return math.sqrt(cx * cx + cy * cy + cz * cz) * 0.5


def _point_in_polygon_xy(point, coords):
    x, y = point
    inside = False
    n = len(coords)
    for i in range(n):
        x1, y1, _ = coords[i]
        x2, y2, _ = coords[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def wall_record_set(exterior=WALL_EXT, interiors=(WIN_RING,), committed=True):
    polygon = PolygonRecord(
        polygon_id="P_WALL",
        feature_id="WALL1",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=exterior),
        interiors=tuple(
            RingRecord(ring_id=f"P_WALL_{i + 1}_", role="interior", coords=ring)
            for i, ring in enumerate(interiors)
        ),
        geometry_state="committed" if committed else "ids_only",
    )
    wall = FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        polygon_ids=("P_WALL",),
    )
    return RecordSet(features=(wall,), polygons=(polygon,))


def _hole_centre(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _assert_holed_mesh_topology(mesh, exterior, holes):
    """Shared N-hole assertion (P3b.2): total triangle area matches
    exterior-minus-holes, every hole's own vertices survive into the mesh,
    and no produced triangle covers any hole's centre - the defining
    behaviour of a genuine hole, not just a matching total area."""
    expected_area = _loop_area_xy(exterior) - sum(_loop_area_xy(h) for h in holes)
    got_area = sum(
        _tri_area_3d(mesh.vertices[i], mesh.vertices[j], mesh.vertices[k])
        for (i, j, k) in mesh.faces
    )
    assert got_area == pytest.approx(expected_area, rel=1e-6)

    for hole in holes:
        assert set(hole).issubset(set(mesh.vertices))
        centre = _hole_centre(hole)
        for (i, j, k) in mesh.faces:
            tri2d = [(mesh.vertices[idx][0], mesh.vertices[idx][1], 0.0) for idx in (i, j, k)]
            assert not _point_in_polygon_xy(centre, tri2d)


def test_display_mesh_has_a_hole_matching_the_interior_ring():
    record_set = wall_record_set()
    mesh = build_surface_display(record_set, "WALL1")

    assert mesh.vertices[:4] == WALL_EXT
    _assert_holed_mesh_topology(mesh, WALL_EXT, (WIN_RING,))


def test_display_mesh_has_two_holes_matching_the_interior_rings():
    holes = (WIN_RING, WIN_RING2)
    record_set = wall_record_set(interiors=holes)
    mesh = build_surface_display(record_set, "WALL1")
    _assert_holed_mesh_topology(mesh, WALL_EXT, holes)


def test_display_mesh_has_three_holes_matching_the_interior_rings():
    holes = (WIN_RING, WIN_RING2, WIN_RING3)
    record_set = wall_record_set(interiors=holes)
    mesh = build_surface_display(record_set, "WALL1")
    _assert_holed_mesh_topology(mesh, WALL_EXT, holes)


def test_overlapping_interior_rings_raise():
    record_set = wall_record_set(interiors=(WIN_RING, WIN_RING_OVERLAP))
    with pytest.raises(DisplayBuildError):
        build_surface_display(record_set, "WALL1")


def test_out_of_bounds_interior_ring_raises():
    record_set = wall_record_set(interiors=(WIN_RING_OUTSIDE,))
    with pytest.raises(DisplayBuildError):
        build_surface_display(record_set, "WALL1")


def test_display_mesh_with_no_interior_rings_is_one_solid_polygon():
    record_set = wall_record_set(interiors=())
    mesh = build_surface_display(record_set, "WALL1")

    got_area = sum(
        _tri_area_3d(mesh.vertices[i], mesh.vertices[j], mesh.vertices[k])
        for (i, j, k) in mesh.faces
    )
    assert got_area == pytest.approx(_loop_area_xy(WALL_EXT), rel=1e-6)


def test_unknown_surface_id_raises():
    record_set = wall_record_set()
    with pytest.raises(DisplayBuildError):
        build_surface_display(record_set, "NOPE")


def test_non_committed_geometry_state_raises():
    record_set = wall_record_set(committed=False)
    with pytest.raises(DisplayBuildError):
        build_surface_display(record_set, "WALL1")


def test_missing_polygon_raises():
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    record_set = RecordSet(features=(wall,), polygons=())
    with pytest.raises(DisplayBuildError):
        build_surface_display(record_set, "WALL1")
