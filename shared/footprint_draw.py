# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/footprint_draw.py
"""Drawing a building footprint with the mouse (B27). Pure state, no bpy.

The modal operator (``cgml.building_new``) turns events into calls here:
``move`` / ``press`` / ``release`` with the ground point under the mouse and
the mouse position in pixels, ``key`` for Enter / Backspace / digits. One
tool, two ways (decision A3):

- press and drag, release: a rectangle (the two corners of the drag);
- click, click, click ...: a polygon; click the first corner (or Enter) to close.

Then the height: move the mouse up / down or type a number, click or Enter.
Ctrl while moving snaps the edge direction to 15 degree steps.
B72: without Ctrl, a corner within 10 degrees of a right angle to the previous edge becomes exact.
B35: after a corner, typed digits set the next edge's length (Blender numeric input: the mouse
gives the direction, Backspace edits, Enter or a click places the corner).
"""

from __future__ import annotations

import math

from .modal_hint import hint

DRAG_PX = 8.0       # a press that moves further than this is a rectangle drag
CLOSE_PX = 12.0     # a click this close to the first corner closes the polygon
MIN_EDGE = 0.1      # metres: shorter clicks are ignored (double clicks)
MIN_HEIGHT = 0.5
STOREY = 3.0        # metres per storey shown next to the height
RIGHT_SNAP_DEG = 10.0  # B72: clicked corners this close to a right angle become one (pitched roofs need it)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def snap_direction(prev, point, step_deg: float = 15.0):
    """``point`` turned around ``prev`` so the edge is a multiple of ``step_deg`` (length kept)."""
    dx, dy = point[0] - prev[0], point[1] - prev[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        return tuple(point)
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    return (prev[0] + length * math.cos(ang), prev[1] + length * math.sin(ang), prev[2])


def snap_right_angle(points, point, tol_deg: float = RIGHT_SNAP_DEG):
    """B72: from the third corner on, an edge within ``tol_deg`` of square (or straight) to the
    previous edge is made exact; the corner then slides along it so the closing edge is square
    to the first edge too, when that is also within ``tol_deg``. Otherwise ``point`` as is."""
    point = tuple(point)
    if len(points) < 2:
        return point
    a, b = points[-2], points[-1]
    le, ld = _dist(a, b), _dist(b, point)
    if le < 1e-9 or ld < 1e-9:
        return point
    ux, uy = (b[0] - a[0]) / le, (b[1] - a[1]) / le
    dx, dy = point[0] - b[0], point[1] - b[1]
    along, across = dx * ux + dy * uy, dy * ux - dx * uy
    ang = math.degrees(math.atan2(abs(across), abs(along)))  # 0 straight on, 90 square
    if ang >= 90.0 - tol_deg:
        d, length = ((-uy, ux) if across > 0 else (uy, -ux)), abs(across)
    elif ang <= tol_deg:
        d, length = ((ux, uy) if along > 0 else (-ux, -uy)), abs(along)
    else:
        return point
    snapped = (b[0] + d[0] * length, b[1] + d[1] * length, b[2])
    if len(points) >= 3:
        p0, p1 = points[0], points[1]
        lf = _dist(p0, p1)
        fx, fy = (p1[0] - p0[0]) / lf, (p1[1] - p0[1]) / lf
        dot = d[0] * fx + d[1] * fy
        if abs(dot) > 0.5:  # this edge runs along the first one: end it square above the first corner
            t = ((p0[0] - b[0]) * fx + (p0[1] - b[1]) * fy) / dot
            close = (b[0] + d[0] * t, b[1] + d[1] * t, b[2])
            if t > MIN_EDGE and _dist(close, snapped) <= math.tan(math.radians(tol_deg)) * _dist(close, p0):
                snapped = close
    return snapped


def rect_from_diagonal(a, b) -> list:
    """Axis-aligned rectangle with opposite corners ``a`` and ``b`` (z of ``a``)."""
    z = a[2]
    return [(a[0], a[1], z), (b[0], a[1], z), (b[0], b[1], z), (a[0], b[1], z)]


def edge_lengths(ring, closed: bool) -> list:
    pairs = list(zip(ring, ring[1:] + (ring[:1] if closed else [])))
    return [(a, b, _dist(a, b)) for a, b in pairs]


class FootprintDraw:
    """State of one drawing: phase FOOT (corners) -> HEIGHT -> DONE (or CANCELLED)."""

    def __init__(self):
        self.phase = "FOOT"
        self.points = []          # placed corners (x, y, z)
        self.hover = None         # ground point under the mouse
        self.rect = False         # the first press became a drag
        self._press_px = None
        self._first_px = None
        self.height = 9.0
        self._h_px = None
        self._h_start = 9.0
        self.typed = ""
        self._aim = None          # B35: the mouse's ground point (direction of a typed edge)

    # --- footprint ------------------------------------------------------------
    def move(self, point, px, ctrl=False, m_per_px=0.05):
        if self.phase == "HEIGHT":
            if self._h_px is None:
                self._h_px = px[1]
            if not self.typed:
                self.height = max(MIN_HEIGHT, round(self._h_start + (px[1] - self._h_px) * m_per_px, 1))
            return
        if point is None:
            return
        if ctrl and self.points and not self.rect:
            point = snap_direction(self.points[-1], point)
        elif not self.rect:
            point = snap_right_angle(self.points, point)
        self._aim = tuple(point)
        self.hover = self._typed_corner() or tuple(point)
        if self._press_px is not None and len(self.points) == 1 and _dist(px, self._press_px) > DRAG_PX:
            self.rect = True

    def press(self, point, px):
        if self.phase == "HEIGHT":
            self._finish()
            return
        if point is None:
            return
        point = self.hover or tuple(point)
        if len(self.points) >= 3 and self._first_px is not None and _dist(px, self._first_px) <= CLOSE_PX:
            self._to_height()
            return
        if self.points and _dist(point, self.points[-1]) < MIN_EDGE:
            return
        if not self.points:
            self._press_px = px
            self._first_px = px
        self.points.append(tuple(point))
        self.typed = ""

    def release(self, point, px):
        if self.phase == "FOOT" and self.rect and self.points:
            end = self.hover or point
            ring = rect_from_diagonal(self.points[0], end)
            if min(_dist(ring[0], ring[1]), _dist(ring[1], ring[2])) < MIN_HEIGHT:
                self.rect = False  # too small a drag: keep it as the first corner
                self.points = self.points[:1]
            else:
                self.points = ring
                self._to_height()
        self._press_px = None

    def key(self, name):
        """Enter / Backspace / digits ('0'-'9', '.'). Returns False when not used."""
        if name == "ENTER":
            if self.phase == "FOOT" and self.typed and self.points and self.hover:
                self.points.append(self.hover)  # B35: the corner at the typed length
                self.typed = ""
            elif self.phase == "FOOT" and len(self.points) >= 3:
                self._to_height()
            elif self.phase == "HEIGHT":
                self._finish()
            else:
                return False
            return True
        if name == "BACKSPACE":
            if self.typed:
                self.typed = self.typed[:-1]
                self._apply_typed()
            elif self.phase == "FOOT" and self.points:
                self.points.pop()
                if not self.points:
                    self._first_px = None
            else:
                return False
            return True
        typing = self.phase == "HEIGHT" or (self.phase == "FOOT" and self.points and not self.rect)
        if typing and (name.isdigit() or name == "."):
            if name == "." and "." in self.typed:
                return True
            self.typed += name
            self._apply_typed()
            return True
        return False

    def _apply_typed(self):
        if self.phase == "FOOT":
            self.hover = self._typed_corner() or self._aim or self.hover
            return
        try:
            self.height = max(MIN_HEIGHT, float(self.typed))
        except ValueError:
            pass

    def _typed_corner(self):
        """The next corner at the typed length, towards the mouse (+x without one); None if not typing."""
        try:
            length = float(self.typed) if self.phase == "FOOT" and self.points else 0.0
        except ValueError:
            return None
        if length <= 0.0:
            return None
        last = self.points[-1]
        aim = self._aim or (last[0] + 1.0, last[1], last[2])
        d = _dist(aim, last)
        dx, dy = ((aim[0] - last[0]) / d, (aim[1] - last[1]) / d) if d > 1e-9 else (1.0, 0.0)
        return (last[0] + dx * length, last[1] + dy * length, last[2])

    def _to_height(self):
        # shortcut: a self-intersecting polygon is accepted as drawn; check edge crossings here if users hit it
        self.typed = ""
        self.phase = "HEIGHT"
        self.hover = None
        self._h_px = None
        self._h_start = self.height

    def _finish(self):
        self.phase = "DONE"

    # --- what to show ---------------------------------------------------------
    def outline(self):
        """Corners to draw now: placed ones + the mouse (a live rectangle while dragging)."""
        if self.phase == "FOOT" and self.rect and self.points and self.hover:
            return rect_from_diagonal(self.points[0], self.hover), True
        pts = list(self.points) + ([self.hover] if self.phase == "FOOT" and self.hover else [])
        return pts, self.phase != "FOOT"

    def storeys(self) -> int:
        return max(1, round(self.height / STOREY))

    def status(self) -> str:
        """B33: the shared status-bar format (shared/modal_hint.py)."""
        cancel = ("Esc/RMB", "Cancel")
        if self.phase == "HEIGHT":
            now = f"Height {self.typed or f'{self.height:.1f}'} m (~{self.storeys()} storeys)"
            return hint(now, ("Move/Type", "Height"), ("LMB/Enter", "Done"), cancel)
        if not self.points:
            return hint(("Drag", "Drag a rectangle"), ("LMB", "First corner of a polygon"), cancel)
        if self.rect:
            return hint(("Release", "Finish the rectangle"), cancel)
        if self.typed:  # B35: Blender numeric input
            return hint(f"Edge length {self.typed}| m", ("Enter/LMB", "Place corner"), ("Backspace", "Edit"), cancel)
        # shortcut: typed lengths work for polygon edges, not for a rectangle drag
        return hint(("LMB", "Next corner"), ("Type", "Edge length"), ("LMB first corner/Enter", "Close"),
                    ("Ctrl", "15° steps"), ("Backspace", "Undo corner"), cancel)
