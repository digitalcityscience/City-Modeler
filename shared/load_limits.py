# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Limits of "Load all" (pure Python): one pass, the whole file in memory.

Above ``LOAD_ALL_MAX_MB`` or ``LOAD_ALL_MAX_FEATURES`` nothing is imported; the user is sent to Browse
first or 3DCityDB v5 (T11d d3-C). Between the user's warning threshold and the limit the import runs
with a warning.
"""

from __future__ import annotations

LOAD_ALL_MAX_MB = 200.0
# A built building holds about 3 MB (S.gml: 300 buildings, +0.9 GB); the largest Hamburg LoD2 tiles
# have ~1,900, so a whole tile still loads while a file of 10,000 (~30 GB) is refused.
LOAD_ALL_MAX_FEATURES = 2000


class ImportNotSupported(RuntimeError):
    """The file holds nothing the add-on imports (or is not CityGML 2.0/3.0). The message tells the user."""


class ImportTooLarge(RuntimeError):
    """The file is above the Load-all limit; nothing was imported."""


def size_notice(size_mb: float, threshold_mb: float, max_mb: float = LOAD_ALL_MAX_MB) -> str:
    """'' for a small file, a warning from ``threshold_mb`` on; raises ``ImportTooLarge`` above ``max_mb``."""
    if size_mb > max_mb:
        raise ImportTooLarge(
            f"The file is {size_mb:,.0f} MB; Load all is limited to {max_mb:,.0f} MB and nothing was imported. "
            "Use Browse first, or load the file into 3DCityDB v5 and open the part you need from the "
            "database browser.")
    if size_mb >= threshold_mb:
        return (f"Large file ({size_mb:,.0f} MB): Load all reads all of it into memory and can take long. "
                "Browse first, or 3DCityDB v5, opens just the part you need.")
    return ""


def check_feature_count(count: int, max_features: int = LOAD_ALL_MAX_FEATURES) -> None:
    """Raises ``ImportTooLarge`` when more than ``max_features`` top-level features would be built."""
    if count > max_features:
        raise ImportTooLarge(
            f"{count:,} features; Load all is limited to {max_features:,} (each needs about 3 MB of memory) "
            "and nothing was imported. Use Browse first, or load the file into 3DCityDB v5 and open the part "
            "you need from the database browser.")
