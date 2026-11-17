"""B72: pitched roofs on nearly right-angled footprints; clicked corners snap to right angles."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared import building_shapes, roof_shapes  # noqa: E402  (pure Python, no bpy)
from shared.footprint_draw import FootprintDraw, snap_right_angle  # noqa: E402

# Footprints read from the user's scene (buildings_lod2_citygml_3_0.gml, Hamburg), local metres.
REAL_8 = [(70.4, 13.51, 0.6), (70.23, 18.27, 0.6), (70.19, 19.16, 0.6), (69.99, 22.41, 0.6),
          (58.91, 22.02, 0.6), (58.97, 20.4, 0.6), (59.43, 7.51, 0.6), (70.59, 7.91, 0.6)]
DRAWN_SKEW = [(76.81, 39.71, 0), (71.83, 58.35, 0), (60.17, 52.65, 0), (68.26, 33.84, 0)]  # 79°..110°
SKEWED = [(0, 0, 10.0), (10, 0, 10.0), (12, 7, 10.0), (-1, 6, 10.0)]  # B51 fixture


def _angles(ring):
    out = []
    for i, b in enumerate(ring):
        a, c = ring[i - 1], ring[(i + 1) % len(ring)]
        u, v = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
        out.append(math.degrees(math.acos((u[0] * v[0] + u[1] * v[1]) / math.hypot(*u) / math.hypot(*v))))
    return out


def test_real_footprint_squares_to_a_rectangle_and_takes_every_roof():
    sq = building_shapes.squared(REAL_8)
    assert sq is not None and len(sq) == 4
    assert all(abs(a - 90.0) < 1e-6 for a in _angles(sq))
    assert abs(building_shapes.signed_area(sq) - building_shapes.signed_area(building_shapes.ccw(REAL_8))) < 2.0
    assert all(p[2] == 0.6 for p in sq)
    for code in roof_shapes.ROOF_TYPES:
        assert roof_shapes.roof_solid(sq, 0.6, 9.6, code) is not None, code


def test_skewed_footprints_stay_refused():
    assert building_shapes.squared(SKEWED) is None
    assert building_shapes.squared(DRAWN_SKEW) is None


def test_rotated_jittered_l_shape_splits_into_two_rectangles():
    base = [(0, 0), (10, 0), (10, 4), (4, 4.03), (4, 9), (0, 9)]  # one edge 0.3° off
    c, s = math.cos(0.4), math.sin(0.4)
    ring = [(564800 + x * c - y * s, 5934200 + x * s + y * c, 5.0) for x, y in base]
    sq = building_shapes.squared(ring)
    assert sq is not None and len(sq) == 6
    rects = building_shapes.rectilinear_rectangles(sq)
    assert rects is not None and len(rects) == 2


def test_tiny_jog_is_ignored():
    ring = [(0, 0, 0), (10, 0, 0), (10, 3, 0), (10.02, 3.0, 0), (10.02, 6, 0), (0, 6, 0)]  # 2 cm step
    sq = building_shapes.squared(ring)
    assert sq is not None and len(sq) == 4


def test_clicked_corners_snap_to_an_exact_rectangle():
    d = FootprintDraw()
    clicks = [(0, 0, 0), (10, 0.0, 0), (10.6, 6.0, 0), (0.4, 6.5, 0)]  # each a few degrees off
    for i, p in enumerate(clicks):
        d.move(p, (i * 100, 0))
        d.press(p, (i * 100, 0))
        d.release(p, (i * 100, 0))  # a click, not a drag
    assert d.key("ENTER")
    assert roof_shapes.rectangle_frame(building_shapes.ccw(d.points)) is not None
    assert all(abs(a - 90.0) < 1e-6 for a in _angles(d.points))
    assert abs(d.points[3][0]) < 1e-9  # the last corner sits square above the first


def test_far_off_corner_is_not_snapped():
    pts = [(0, 0, 0), (10, 0, 0)]
    assert snap_right_angle(pts, (13, 5, 0)) == (13, 5, 0)  # ~59°: a deliberate angle stays
    assert snap_right_angle(pts[:1], (3, 0.2, 0)) == (3, 0.2, 0)  # the first edge is free
