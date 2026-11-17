# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/selection.py
"""Selection rules of the browse panel (T08, pure Python, no bpy)."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .gml_index import FeatureIndexEntry

DEFAULT_LOAD_LIMIT = 10
LOAD_MODES = ("LOD2", "HIGHEST")


def limit_message(count: int, limit: int) -> Optional[str]:
    """Error text when ``count`` selected features exceed ``limit``, else None."""
    if count <= 0:
        return "Select at least one building first"
    if limit > 0 and count > limit:
        noun = "building" if limit == 1 else "buildings"
        return f"Select at most {limit} {noun} (preferences)"
    return None


def lod_summary(entries: Sequence[FeatureIndexEntry]) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """``(levels every entry has, levels any entry has)``."""
    sets = [set(e.lod_levels) for e in entries]
    if not sets:
        return (), ()
    common = set.intersection(*sets)
    union = set.union(*sets)
    return tuple(sorted(common)), tuple(sorted(union))


def format_levels(levels: Iterable[int]) -> str:
    levels = list(levels)
    return ", ".join(str(v) for v in levels) if levels else "none"


def plan_load(entries: Dict[int, FeatureIndexEntry], mode: str) -> Tuple[Dict[int, List[int]], List[int]]:
    """Group feature indices by the LoD to import.

    ``mode`` 'LOD2' loads LoD2 where present; 'HIGHEST' the highest level of
    each feature. Returns ``({level: [feature indices]}, [indices without a
    matching LoD])``.
    """
    if mode not in LOAD_MODES:
        raise ValueError(f"unknown load mode {mode}")
    groups: Dict[int, List[int]] = {}
    missing: List[int] = []
    for fi in sorted(entries):
        levels = entries[fi].lod_levels
        if mode == "LOD2":
            level = 2 if 2 in levels else None
        else:
            level = max(levels) if levels else None
        if level is None:
            missing.append(fi)
        else:
            groups.setdefault(level, []).append(fi)
    return groups, missing


def project_point(matrix: Sequence[Sequence[float]], p: Sequence[float], width: float, height: float):
    """Region pixel of ``p`` under a 4x4 perspective matrix (row major), or None behind the view."""
    x, y, z = p[0], p[1], p[2]
    cx = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3]
    cy = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3]
    cw = matrix[3][0] * x + matrix[3][1] * y + matrix[3][2] * z + matrix[3][3]
    if cw <= 1e-9:
        return None
    return (width / 2.0) * (1.0 + cx / cw), (height / 2.0) * (1.0 + cy / cw)


def features_in_rect(
    centers: Dict[int, Sequence[float]],
    matrix: Sequence[Sequence[float]],
    width: float,
    height: float,
    rect: Tuple[float, float, float, float],
) -> List[int]:
    """Feature indices whose (local) centre projects into ``rect`` = (xmin, ymin, xmax, ymax)."""
    x0, y0, x1, y1 = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
    out = []
    for fi, c in centers.items():
        px = project_point(matrix, c, width, height)
        if px is not None and x0 <= px[0] <= x1 and y0 <= px[1] <= y1:
            out.append(fi)
    return sorted(out)
