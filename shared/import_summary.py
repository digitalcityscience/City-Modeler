# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/import_summary.py
"""One-line import report (pure Python formatting + a bpy collector)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

NO_FEATURES_MESSAGE = "0 features matched filters (bbox/LoD/type) — see Filters"

FEATURE_TYPE_KEYS = ("cgml3_feature", "feature_type")


@dataclass
class ImportSummary:
    feature_counts: Dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    crs: str = ""
    origin: Optional[Sequence[float]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def feature_total(self) -> int:
        return sum(self.feature_counts.values())


def format_summary(summary: ImportSummary) -> str:
    """``Imported 3 features (3 Building) in 0.4 s · EPSG:25832 · origin 564800/5934300``."""
    total = summary.feature_total
    if total == 0:
        return NO_FEATURES_MESSAGE
    noun = "feature" if total == 1 else "features"
    types = ", ".join(
        f"{n} {t}" for t, n in sorted(summary.feature_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    parts = [f"Imported {total} {noun} ({types}) in {summary.seconds:.1f} s"]
    if summary.crs:
        parts.append(summary.crs)
    if summary.origin is not None and len(summary.origin) >= 2:
        parts.append(f"origin {summary.origin[0]:.0f}/{summary.origin[1]:.0f}")
    if summary.warnings:
        n = len(summary.warnings)
        parts.append(f"{n} warning" + ("" if n == 1 else "s"))
    return " · ".join(parts)


def count_root_features(objects) -> Dict[str, int]:
    """Feature types of the top-level imported objects.

    An object is top level when its parent is not part of ``objects``; only
    objects carrying a CityGML feature type are counted.
    """
    objs = list(objects)
    ids = {id_key(o) for o in objs}
    counts: Dict[str, int] = {}
    for o in objs:
        parent = getattr(o, "parent", None)
        if parent is not None and id_key(parent) in ids:
            continue
        ftype = None
        for key in FEATURE_TYPE_KEYS:
            try:
                val = o.get(key)
            except Exception:
                val = None
            if val:
                ftype = str(val)
                break
        if ftype is None:
            continue
        counts[ftype] = counts.get(ftype, 0) + 1
    return counts


def id_key(obj):
    try:
        return obj.as_pointer()
    except Exception:
        return id(obj)
