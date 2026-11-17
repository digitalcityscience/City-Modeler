# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/view_extent.py
"""Ground area seen by a 3D view, in CRS coordinates (T09, pure Python, no bpy).

The main thread hands over the view rays through the region corners (and the
centre) in Blender's local coordinates. Each ray is cut with the ground plane
``z = ground_z`` (``ground_level``: the median base of the boxes already
shown, else the origin plane z = 0); a ray that looks above the horizon is cut at ``max_distance``
from the camera instead, so a tilted view never asks for a whole country. The
bounding rectangle of the hits plus the scene origin is the CRS extent.
"""

from __future__ import annotations

import math
import statistics
from typing import Iterable, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Extent = Tuple[float, float, float, float]

DEFAULT_MAX_DISTANCE_M = 3000.0
MIN_SIDE_M = 1.0


def ray_ground_point(origin: Sequence[float], direction: Sequence[float], ground_z: float,
                     max_distance: float = DEFAULT_MAX_DISTANCE_M) -> Tuple[float, float]:
    """X/Y where the ray meets ``z = ground_z``, at most ``max_distance`` away (horizontally)."""
    ox, oy, oz = (float(v) for v in origin[:3])
    dx, dy, dz = (float(v) for v in direction[:3])
    horiz = math.hypot(dx, dy)
    if dz < -1e-9:
        t = (ground_z - oz) / dz
        if t >= 0.0:
            px, py = ox + t * dx, oy + t * dy
            dist = math.hypot(px - ox, py - oy)
            if dist <= max_distance:
                return px, py
    if horiz < 1e-12:  # straight up (or down onto a point above the plane)
        return ox, oy
    k = max_distance / horiz
    return ox + k * dx, oy + k * dy


def ground_extent(rays: Iterable[Tuple[Sequence[float], Sequence[float]]], ground_z: float = 0.0,
                  max_distance: float = DEFAULT_MAX_DISTANCE_M) -> Optional[Extent]:
    """Local ``(xmin, ymin, xmax, ymax)`` covered by the rays, or None without rays."""
    pts = [ray_ground_point(o, d, ground_z, max_distance) for o, d in rays]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmax - xmin < MIN_SIDE_M:
        c = (xmin + xmax) / 2.0
        xmin, xmax = c - MIN_SIDE_M / 2.0, c + MIN_SIDE_M / 2.0
    if ymax - ymin < MIN_SIDE_M:
        c = (ymin + ymax) / 2.0
        ymin, ymax = c - MIN_SIDE_M / 2.0, c + MIN_SIDE_M / 2.0
    return xmin, ymin, xmax, ymax


def ground_level(boxes: Iterable[Sequence[float]], origin: Sequence[float],
                 extent: Optional[Sequence[float]] = None, min_count: int = 5) -> float:
    """Local ground plane: the median base (min Z) of the boxes already shown.

    ``boxes`` are CRS ``(xmin, ymin, zmin, xmax, ymax, zmax)``. Boxes whose centre
    lies inside the local ``extent`` (cut at z = 0) count when there are at
    least ``min_count`` of them, else all boxes. Without boxes the origin plane
    (0.0) stays. The origin Z is the lowest point of the city model, so on
    hilly data z = 0 lies under the buildings and a tilted view's extent slides
    away from the camera (Hamburg: 11-21 m, 5-11 % of a close 45 degree view).
    """
    ox, oy, oz = (float(v) for v in origin[:3])
    every, inside = [], []
    for b in boxes:
        if len(b) < 6:
            continue
        z = float(b[2]) - oz
        every.append(z)
        if extent is not None:
            cx, cy = (float(b[0]) + float(b[3])) / 2.0 - ox, (float(b[1]) + float(b[4])) / 2.0 - oy
            if extent[0] <= cx <= extent[2] and extent[1] <= cy <= extent[3]:
                inside.append(z)
    pick = inside if len(inside) >= min_count else every
    return float(statistics.median(pick)) if pick else 0.0


def regrounded_extent(rays, extent: Sequence[float], origin: Sequence[float],
                      base_z: Optional[float]) -> Sequence[float]:
    """First query, no boxes shown yet: the view cut at ``base_z`` (the CRS
    median base of the features in the area, from the DB) instead of the
    origin plane. ``extent`` is returned unchanged without a base or rays."""
    if base_z is None or not rays:
        return extent
    z = float(base_z) - float(origin[2])
    return (ground_extent(rays, z) or extent) if z else extent


def to_crs(extent: Sequence[float], origin: Sequence[float]) -> Extent:
    """Local extent plus the scene origin (X/Y)."""
    ox, oy = float(origin[0]), float(origin[1])
    return (float(extent[0]) + ox, float(extent[1]) + oy, float(extent[2]) + ox, float(extent[3]) + oy)


def area_km2(extent: Sequence[float]) -> float:
    return max(0.0, float(extent[2]) - float(extent[0])) * max(0.0, float(extent[3]) - float(extent[1])) / 1e6


def format_extent(extent: Sequence[float]) -> str:
    return f"{extent[0]:.0f}, {extent[1]:.0f} - {extent[2]:.0f}, {extent[3]:.0f}"
