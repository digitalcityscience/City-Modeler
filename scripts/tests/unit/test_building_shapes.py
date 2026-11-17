import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("building_shapes_under_test", ROOT / "shared" / "building_shapes.py")
shapes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shapes)


def test_rectangle_is_ccw_with_the_requested_size():
    ring = shapes.rectangle((0.0, 0.0, 5.0), 10.0, 8.0, math.radians(30))
    assert len(ring) == 4
    assert math.isclose(shapes.signed_area(ring), 80.0)
    assert all(p[2] == 5.0 for p in ring)


def test_prism_has_ground_roof_and_one_wall_per_edge():
    ring = shapes.rectangle((0.0, 0.0, 0.0), 10.0, 8.0)
    verts, faces, labels = shapes.prism(list(reversed(ring)), 9.0)  # clockwise input is fixed
    assert labels == ["GroundSurface", "RoofSurface"] + ["WallSurface"] * 4
    assert len(verts) == 8 and len(faces) == 6
    assert max(v[2] for v in verts) == 9.0
    # Ground faces down (clockwise from above), roof faces up.
    ground = [verts[i] for i in faces[0]]
    roof = [verts[i] for i in faces[1]]
    assert shapes.signed_area(ground) < 0 < shapes.signed_area(roof)


def test_boundary_loop_of_an_l_shape_made_of_two_quads():
    #  3---2
    #  |   |
    #  4---1---6        two quads sharing edge 1-4; boundary has 6 corners
    #  |       |
    #  0-------5  (index 7 unused)
    faces = [[0, 5, 6, 1, 4], [4, 1, 2, 3]]
    loop = shapes.boundary_loop(faces)
    assert sorted(loop) == [0, 1, 2, 3, 4, 5, 6]
    assert len(loop) == 7


def _area_sum(rects):
    return sum(shapes.signed_area(r) for r in rects)


def _rotate(ring, deg, dx=50.0, dy=-20.0):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s + dx, x * s + y * c + dy, z) for x, y, z in ring]


L_SHAPE = [(0, 0, 3), (10, 0, 3), (10, 5, 3), (5, 5, 3), (5, 12, 3), (0, 12, 3)]
T_SHAPE = [(0, 0, 0), (4, 0, 0), (4, 6, 0), (10, 6, 0), (10, 10, 0), (-6, 10, 0), (-6, 6, 0), (0, 6, 0)]
U_SHAPE = [(0, 0, 0), (12, 0, 0), (12, 9, 0), (8, 9, 0), (8, 4, 0), (4, 4, 0), (4, 9, 0), (0, 9, 0)]


def test_rectilinear_shapes_split_into_the_fewest_rectangles_covering_the_area():
    for ring, n in ((L_SHAPE, 2), (T_SHAPE, 2), (U_SHAPE, 3)):
        for deg in (0, 27):
            r = _rotate(ring, deg)
            rects = shapes.rectilinear_rectangles(r)
            assert len(rects) == n, (ring, deg, len(rects))
            assert math.isclose(_area_sum(rects), shapes.signed_area(shapes.ccw(r)), rel_tol=1e-9)
            assert all(shapes.signed_area(x) > 0 and len(x) == 4 for x in rects)
            assert all(p[2] == r[0][2] for x in rects for p in x)


def test_rectangle_stays_one_and_slanted_shapes_are_refused():
    assert len(shapes.rectilinear_rectangles(shapes.rectangle((0, 0, 0), 10, 8, 0.3))) == 1
    assert shapes.rectilinear_rectangles([(0, 0, 0), (10, 0, 0), (8, 6, 0), (2, 6, 0)]) is None


def test_drop_collinear_removes_midpoints():
    ring = [(0, 0, 0), (5, 0, 0), (10, 0, 0), (10, 8, 0), (0, 8, 0)]
    assert shapes.drop_collinear(ring) == [(0, 0, 0), (10, 0, 0), (10, 8, 0), (0, 8, 0)]
