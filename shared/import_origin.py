# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/import_origin.py
"""Local origin selection for CityGML imports (pure Python, no bpy).

A new import into a scene that is not yet georeferenced uses the centre of
the data as local origin: X/Y are the envelope centre rounded to
``ORIGIN_ROUNDING_M``, Z is the minimum Z. Without an envelope the bounds
come from a coordinate scan. Scenes that already carry an origin keep it;
that decision stays in the importers.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Optional, Sequence, Tuple

ORIGIN_ROUNDING_M = 100.0
FAR_FROM_ORIGIN_M = 50_000.0

Corner = Tuple[float, ...]
Origin = Tuple[float, float, float]


def parse_corner(text: Optional[str]) -> Optional[Corner]:
    """Parse a gml:lowerCorner/upperCorner text into a 2D or 3D tuple."""
    if not text:
        return None
    try:
        vals = [float(v) for v in str(text).split()]
    except ValueError:
        return None
    if len(vals) >= 3:
        return (vals[0], vals[1], vals[2])
    if len(vals) == 2:
        return (vals[0], vals[1])
    return None


def round_to_step(value: float, step: float = ORIGIN_ROUNDING_M) -> float:
    if step <= 0:
        return float(value)
    return float(math.floor(value / step + 0.5) * step)


def center_origin(lower: Optional[Sequence[float]], upper: Optional[Sequence[float]] = None) -> Optional[Origin]:
    """Origin for a lower/upper corner pair: rounded X/Y centre, Z = min Z.

    With no usable upper corner the lower corner X/Y is used (still rounded).
    2D corners give Z = 0.
    """
    if not lower or len(lower) < 2:
        return None
    lx, ly = float(lower[0]), float(lower[1])
    if upper and len(upper) >= 2:
        cx = (lx + float(upper[0])) / 2.0
        cy = (ly + float(upper[1])) / 2.0
    else:
        cx, cy = lx, ly
    if len(lower) >= 3:
        z = float(lower[2])
        if upper and len(upper) >= 3:
            z = min(z, float(upper[2]))
    else:
        z = 0.0
    return (round_to_step(cx), round_to_step(cy), z)


def bounds_from_pos_lists(pos_lists: Iterable[Tuple[str, Optional[int]]]):
    """Bounds of ``(text, srsDimension)`` coordinate lists.

    Returns ``(mins, maxs)`` as 3-tuples, or ``None`` when no coordinate was
    found. A missing dimension is guessed (3 if divisible by 3, else 2).
    """
    mins = [math.inf] * 3
    maxs = [-math.inf] * 3
    found = False
    for text, dim in pos_lists:
        if not text:
            continue
        try:
            vals = [float(v) for v in text.split()]
        except ValueError:
            continue
        if not vals:
            continue
        d = dim if dim in (2, 3) else (3 if len(vals) % 3 == 0 else 2)
        for i in range(0, len(vals) - d + 1, d):
            pt = (vals[i], vals[i + 1], vals[i + 2] if d == 3 else 0.0)
            for k in range(3):
                if pt[k] < mins[k]:
                    mins[k] = pt[k]
                if pt[k] > maxs[k]:
                    maxs[k] = pt[k]
            found = True
    if not found:
        return None
    return tuple(mins), tuple(maxs)


def file_origin(
    lower: Optional[Sequence[float]],
    upper: Optional[Sequence[float]],
    scan_bounds=None,
) -> Origin:
    """Origin of a file: envelope centre, else centre of scanned bounds.

    ``scan_bounds`` is an optional callable returning ``(mins, maxs)`` or
    ``None``; it is only called when the envelope has no lower corner.
    """
    origin = center_origin(lower, upper)
    if origin is not None:
        return origin
    if scan_bounds is not None:
        bounds = scan_bounds()
        if bounds is not None:
            origin = center_origin(bounds[0], bounds[1])
            if origin is not None:
                return origin
    return (0.0, 0.0, 0.0)


def xy_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def is_far_from_origin(local_center_xy: Sequence[float], limit_m: float = FAR_FROM_ORIGIN_M) -> bool:
    """True when data placed in local coordinates lies beyond ``limit_m``."""
    return xy_distance(local_center_xy, (0.0, 0.0)) > limit_m


def parse_origin(text: Optional[str]) -> Optional[Origin]:
    """``"548100 5934200 12.5"`` (space, comma or semicolon between values; dot decimals) -> (x, y, z).

    Two values give Z = 0. Kept as text in the UI because Blender float properties are float32.
    """
    parts = [p for p in re.split(r"[\s,;]+", str(text or "").strip()) if p]
    if len(parts) not in (2, 3):
        return None
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in vals):
        return None
    return (vals[0], vals[1], vals[2] if len(vals) == 3 else 0.0)


def float32_step(value: float) -> float:
    """Distance between neighbouring float32 values at ``value`` (Blender's vertex precision there)."""
    if not value:
        return 0.0
    return 2.0 ** (math.frexp(abs(float(value)))[1] - 24)


def georef_status(epsg: Optional[str], origin: Optional[Sequence[float]]):
    """Header lines ``(text, kind)`` for the scene georeference; kind is "ok", "unset" or "error".

    Blender keeps float32 vertices, so data sits around ``origin`` (CRS coordinates of Blender 0,0,0);
    showing it lets users shift other data (point clouds) by the same amount.
    """
    if epsg:
        lines = [(f"CRS: {epsg}", "ok")]
        if origin is not None:
            x, y, z = (float(v) for v in origin)
            lines.append((f"Origin: {x:.2f}, {y:.2f}, {z:.2f}", "ok"))
        return lines
    if origin is None:
        return [("CRS: not set yet", "unset")]
    # an origin without a CRS: the file had no srsName, so nothing is georeferenced
    return [("CRS unknown: the file has no srsName", "error"),
            ("Not georeferenced; add srsName to the file", "error")]
