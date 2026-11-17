# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/models.py
"""What discovery finds on a server, and how it reads in the UI (no bpy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# SchemaInfo.status
OK = "ok"                    # 3DCityDB v5 schema with features
EMPTY = "empty"              # 3DCityDB v5 schema without top-level features
UNSUPPORTED = "unsupported"  # 3DCityDB, but not v5 (or broken)
NO_ACCESS = "no_access"      # schema exists, user lacks USAGE/SELECT

# DatabaseInfo.status
DB_CITYDB = "citydb"
DB_NOT_CITYDB = "not_citydb"
DB_NO_ACCESS = "no_access"
DB_ERROR = "error"

Extent = Tuple[float, float, float, float]  # xmin, ymin, xmax, ymax

DEFAULT_SCHEMA_NAME = "citydb"


@dataclass
class SchemaInfo:
    database: str
    schema: str
    status: str = OK
    version: str = ""
    srid: Optional[int] = None
    srs_name: str = ""
    crs_name: str = ""
    crs_geographic: bool = False
    feature_counts: Dict[str, int] = field(default_factory=dict)
    feature_estimate: Optional[int] = None   # set when exact counting timed out
    extent: Optional[Extent] = None
    extent_estimated: bool = False
    center_lonlat: Optional[Tuple[float, float]] = None
    comment: str = ""
    can_write: bool = False
    last_modified: str = ""
    message: str = ""

    @property
    def key(self) -> str:
        return f"{self.database}/{self.schema}"

    @property
    def usable(self) -> bool:
        return self.status in (OK, EMPTY)

    @property
    def feature_total(self) -> int:
        return sum(self.feature_counts.values())

    def display_label(self, alias: str = "") -> str:
        """Alias the user gave > schema comment (COMMENT ON SCHEMA) > a distinctive name.

        The default schema is called ``citydb`` in every 3DCityDB database, so
        for it the database name tells city models apart; other schemas are
        named after themselves.
        """
        for candidate in (alias, self.comment):
            text = (candidate or "").strip()
            if text:
                return text.splitlines()[0][:80]
        return self.database if self.schema == DEFAULT_SCHEMA_NAME else self.schema


@dataclass
class DatabaseInfo:
    name: str
    status: str = DB_CITYDB
    version: str = ""
    schemas: List[SchemaInfo] = field(default_factory=list)
    message: str = ""


@dataclass
class DiscoveryResult:
    server_version: str = ""
    databases: List[DatabaseInfo] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def schemas(self) -> List[SchemaInfo]:
        return [s for db in self.databases for s in db.schemas]

    @property
    def city_models(self) -> List[SchemaInfo]:
        return [s for s in self.schemas if s.usable]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_epsg(srid: Optional[int]) -> str:
    return f"EPSG:{srid}" if srid else "CRS unknown"


def format_counts(info: SchemaInfo, max_types: int = 3) -> str:
    """``6 Building`` / ``12 Building, 3 Bridge, +2 types`` / ``~ 48,000 features``."""
    if info.feature_counts:
        items = sorted(info.feature_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        parts = [f"{n:,} {name}" for name, n in items[:max_types]]
        rest = len(items) - max_types
        if rest > 0:
            parts.append(f"+{rest} type" + ("" if rest == 1 else "s"))
        return ", ".join(parts)
    if info.feature_estimate is not None:
        return f"~ {info.feature_estimate:,} features (estimate)"
    return "no features"


def _format_length(metres: float) -> str:
    if metres >= 10_000:
        return f"{metres / 1000:.0f} km"
    if metres >= 1_000:
        return f"{metres / 1000:.1f} km"
    return f"{metres:.0f} m"


def format_extent_size(info: SchemaInfo) -> str:
    """``190 x 253 m`` (projected) or ``0.12 x 0.08 deg`` (geographic)."""
    if not info.extent:
        return ""
    xmin, ymin, xmax, ymax = info.extent
    w, h = max(xmax - xmin, 0.0), max(ymax - ymin, 0.0)
    prefix = "~ " if info.extent_estimated else ""
    if info.crs_geographic:
        return f"{prefix}{w:.3g} × {h:.3g}°"
    if w >= 1_000 or h >= 1_000:
        return f"{prefix}{_format_length(w)} × {_format_length(h)}"
    return f"{prefix}{w:.0f} × {h:.0f} m"


def format_lonlat(lonlat: Optional[Tuple[float, float]]) -> str:
    """``9.99°E 53.54°N`` - lets the user recognise the city."""
    if not lonlat:
        return ""
    lon, lat = lonlat
    ew = "E" if lon >= 0 else "W"
    ns = "N" if lat >= 0 else "S"
    return f"{abs(lon):.2f}°{ew} {abs(lat):.2f}°{ns}"


def format_status(info: SchemaInfo) -> str:
    if info.status == OK:
        return ""
    if info.status == EMPTY:
        return "empty"
    if info.status == NO_ACCESS:
        return "no access"
    if info.status == UNSUPPORTED:
        return f"3DCityDB {info.version} not supported" if info.version else "not supported"
    return info.status


def summary_line(info: SchemaInfo) -> str:
    """One line for a list row: CRS, contents, extent, place."""
    parts = [format_epsg(info.srid)]
    if info.status == OK:
        parts.append(format_counts(info))
        size = format_extent_size(info)
        if size:
            parts.append(size)
        place = format_lonlat(info.center_lonlat)
        if place:
            parts.append(place)
    status = format_status(info)
    if status:
        parts.append(status)
    return " · ".join(parts)


def server_version_short(result: DiscoveryResult) -> str:
    """``17.0`` from ``17.0 (Debian 17.0-1.pgdg110+1)``."""
    return result.server_version.split()[0] if result.server_version else ""


def discovery_summary(result: DiscoveryResult, *, with_server: bool = True) -> str:
    """``3 city models in 2 databases · PostgreSQL 17.0``."""
    models = len(result.city_models)
    dbs = len({s.database for s in result.city_models})
    text = f"{models} city model" + ("" if models == 1 else "s")
    text += f" in {dbs} database" + ("" if dbs == 1 else "s")
    if with_server and result.server_version:
        text += f" · PostgreSQL {server_version_short(result)}"
    return text


def other_databases_line(result: DiscoveryResult) -> str:
    """Databases without a usable city model, e.g. ``postgres (no 3DCityDB)``."""
    labels = {
        DB_NOT_CITYDB: "no 3DCityDB",
        DB_NO_ACCESS: "no access",
        DB_ERROR: "error",
    }
    parts = []
    for db in result.databases:
        if db.status == DB_CITYDB and any(s.usable for s in db.schemas):
            continue
        reason = labels.get(db.status)
        if db.status == DB_CITYDB:
            reason = "no v5 schema"
        parts.append(f"{db.name} ({reason or db.status})")
    return ", ".join(parts)
