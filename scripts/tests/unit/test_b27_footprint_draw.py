"""B27: footprint drawing state (shared/footprint_draw.py), no Blender."""
import math

from shared.footprint_draw import FootprintDraw, rect_from_diagonal, snap_direction


def _rect(d, a, b):
    d.move(a, (100, 100))
    d.press(a, (100, 100))
    d.move(b, (300, 200))
    d.release(b, (300, 200))


def test_drag_makes_a_rectangle_then_height():
    d = FootprintDraw()
    _rect(d, (0, 0, 0), (12, 8, 0))
    assert d.phase == "HEIGHT"
    assert d.points == rect_from_diagonal((0, 0, 0), (12, 8, 0))
    d.move(None, (300, 200), m_per_px=0.1)   # height phase starts at the current mouse
    d.move(None, (300, 230), m_per_px=0.1)   # 30 px up = +3 m
    assert d.height == 12.0
    d.press(None, (300, 230))
    assert d.phase == "DONE"


def test_clicks_make_a_polygon_closed_on_the_first_corner():
    d = FootprintDraw()
    corners = [((0, 0, 0), (100, 100)), ((10, 0, 0), (200, 100)), ((10, 5, 0), (200, 150)),
               ((5, 5, 0), (150, 150)), ((5, 10, 0), (150, 200)), ((0, 10, 0), (100, 200))]
    for p, px in corners:
        d.move(p, px)
        d.press(p, px)
        d.release(p, px)
    assert d.phase == "FOOT" and len(d.points) == 6
    d.move((0.05, 0, 0), (103, 101))
    d.press((0.05, 0, 0), (103, 101))  # near the first corner on screen: closes
    assert d.phase == "HEIGHT" and len(d.points) == 6


def test_enter_closes_backspace_removes_a_corner():
    d = FootprintDraw()
    for i, p in enumerate([(0, 0, 0), (4, 0, 0), (4, 3, 0), (9, 9, 0)]):
        d.move(p, (i * 50, 0))
        d.press(p, (i * 50, 0))
        d.release(p, (i * 50, 0))
    assert d.key("BACKSPACE") and len(d.points) == 3
    assert d.key("ENTER") and d.phase == "HEIGHT"


def test_enter_needs_three_corners():
    d = FootprintDraw()
    d.press((0, 0, 0), (0, 0))
    d.release((0, 0, 0), (0, 0))
    assert not d.key("ENTER") and d.phase == "FOOT"


def test_typed_height_wins_over_the_mouse():
    d = FootprintDraw()
    _rect(d, (0, 0, 0), (10, 10, 0))
    for k in "12.5":
        d.key(k)
    d.move(None, (0, 999))
    assert d.height == 12.5
    d.key("BACKSPACE")
    assert d.height == 12.0
    d.key("ENTER")
    assert d.phase == "DONE"


def test_tiny_drag_stays_a_first_corner():
    d = FootprintDraw()
    d.press((0, 0, 0), (100, 100))
    d.move((0.2, 0.2, 0), (120, 120))      # far on screen, tiny on the ground
    d.release((0.2, 0.2, 0), (120, 120))
    assert d.phase == "FOOT" and d.points == [(0, 0, 0)] and not d.rect


def test_ctrl_snaps_the_edge_to_15_degrees():
    p = snap_direction((0, 0, 0), (10, 1, 0))
    assert math.isclose(p[1], 0.0, abs_tol=1e-9) and math.isclose(p[0], math.hypot(10, 1))
    d = FootprintDraw()
    d.press((0, 0, 0), (0, 0))
    d.release((0, 0, 0), (0, 0))
    d.move((10, 9, 0), (300, 300), ctrl=True)
    ang = math.degrees(math.atan2(d.hover[1], d.hover[0]))
    assert math.isclose(ang, 45.0)


def test_status_and_outline():
    d = FootprintDraw()
    assert "Drag a rectangle" in d.status()
    d.press((0, 0, 0), (0, 0))
    d.move((3, 4, 0), (50, 50))
    pts, closed = d.outline()
    assert len(pts) == 4 and closed  # a live rectangle while dragging
    d.release((3, 4, 0), (50, 50))
    assert "Height" in d.status() and d.storeys() == 3
