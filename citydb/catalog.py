# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/catalog.py
"""Read-only catalog of top-level features for DB Browse first (T09, no bpy).

Runs in the browse worker thread. Every statement is parameterised: values go
through psycopg placeholders, the schema name only through
``psycopg.sql.Identifier``; there is no way to pass free SQL. Feature ids that
reach citydb-tool are validated as integers first.

Queries:
- :func:`count_in_extent` - ``count(*)`` of top-level features whose
  ``feature.envelope`` intersects the extent (GiST ``feature_envelope_spx``);
  an EXPLAIN estimate when it is too slow.
- :func:`query_extent` - one page (``id > after_id ORDER BY id LIMIT n``) with
  id, objectid, class, 3D envelope and the LoD geometry properties of the
  feature and its sub-features (two levels: boundaries, parts' boundaries).
- :func:`lod0_rings` - exterior rings of real ``lod0*`` geometry.
- :func:`ground_footprints` - 2D union of the GroundSurface geometry
  (optional preview, labelled "Footprint (ground surface)").
- :func:`find_by_id` - rows whose objectid or feature.id equal one token.
"""

from __future__ import annotations

import re
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_LIMIT = 1000
HARD_LIMIT = 20000
COUNT_TIMEOUT_MS = 2000
FOOTPRINT_TIMEOUT_MS = 5000
MAX_ID_MATCHES = 50
MAX_EXPORT_IDS = 1000
LOD0_PROPERTY_NAMES = ("lod0MultiSurface", "lod0FootPrint", "lod0RoofEdge")
ALL_LODS = ("0", "1", "2", "3", "4")

PREVIEW_BBOX = "Bounding box"
PREVIEW_LOD0 = "LoD0"
PREVIEW_FOOTPRINT = "Footprint (ground surface)"

NARROW_MESSAGE = "Narrow the area or filter by type"

_INT_TOKEN = re.compile(r"^\s*\+?(\d{1,18})\s*$")
# objectid / gml:id: no whitespace, quotes, separators or brackets
_OBJECTID_TOKEN = re.compile(r"""^[^\s;'"\\(),=<>|&`$]{1,256}$""")
_CLASSNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class InvalidId(ValueError):
    """A feature id or objectid that is not accepted."""


# --------------------------------------------------------------------------- validation
def validate_feature_ids(values: Iterable) -> List[int]:
    """Feature ids as ints; raises :class:`InvalidId` for anything that is not a plain integer."""
    out: List[int] = []
    for v in values:
        if isinstance(v, bool):
            raise InvalidId(f"Not a feature id: {v!r}")
        if isinstance(v, int):
            n = v
        else:
            m = _INT_TOKEN.match(str(v))
            if not m:
                raise InvalidId(f"Not a feature id: {str(v)[:40]!r}")
            n = int(m.group(1))
        if n <= 0 or n >= 2 ** 63:
            raise InvalidId(f"Feature id out of range: {n}")
        out.append(n)
    return sorted(set(out))


def parse_id_token(text: str) -> Tuple[Optional[int], str]:
    """``(feature id or None, objectid)`` for the Load by ID field.

    One token only. Digits are tried both as feature.id and as objectid.
    Raises :class:`InvalidId` for empty input, several tokens or characters an
    identifier never has (``1; DROP TABLE feature`` is refused here).
    """
    token = (text or "").strip()
    if not token:
        raise InvalidId("Enter an objectid or a feature id")
    if not _OBJECTID_TOKEN.match(token):
        raise InvalidId(f"Invalid ID: {token[:40]!r} (one objectid or feature id, no spaces or ;)")
    m = _INT_TOKEN.match(token)
    fid = int(m.group(1)) if m and 0 < int(m.group(1)) < 2 ** 63 else None
    return fid, token


def validate_classname(name: str) -> str:
    name = (name or "").strip()
    if name and not _CLASSNAME.match(name):
        raise InvalidId(f"Invalid feature type: {name[:40]!r}")
    return name


def clamp_limit(limit: int) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), HARD_LIMIT))


# --------------------------------------------------------------------------- citydb-tool arguments
def id_filter(ids: Sequence[int]) -> str:
    """CQL2 filter on feature.id (``core:id``) built from validated ints only.

    citydb-tool 1.3.0 renders ``--sql-filter`` (and CQL2 ``SQL(...)``) as
    ``id in (null)`` and exports nothing, so the id list goes through the
    ``core:id`` property of the schema mapping, which is ``feature.id``.
    """
    ids = validate_feature_ids(ids)
    if not ids:
        raise InvalidId("No feature ids")
    if len(ids) > MAX_EXPORT_IDS:
        raise InvalidId(f"At most {MAX_EXPORT_IDS} features per export")
    return "core:id IN (" + ",".join(str(int(i)) for i in ids) + ")"


def sql_filter(ids: Sequence[int]) -> str:
    """The equivalent ``--sql-filter`` text (kept for newer citydb-tool versions and the log)."""
    ids = validate_feature_ids(ids)
    return "SELECT id FROM feature WHERE id IN (" + ",".join(str(int(i)) for i in ids) + ")"


def export_options(ids: Sequence[int], mode: str) -> Dict[str, object]:
    """Keyword arguments for ``CityDBTool.export_db_to_citygml`` (besides output and connection).

    ``mode`` 'LOD2': only LoD2 geometry (features without LoD2 are left out by
    citydb-tool); 'HIGHEST': the highest LoD of every feature.
    """
    if mode == "LOD2":
        lod = ["2"]
    elif mode == "HIGHEST":
        lod = list(ALL_LODS)
    else:
        raise ValueError(f"unknown load mode {mode}")
    return {
        "citygml_version": "3.0",
        "cql2_filter": id_filter(ids),
        "lod": lod,
        "lod_mode": "maximum",
        "lod_search_depth": "all",
    }


# --------------------------------------------------------------------------- rows
@dataclass
class CatalogRow:
    id: int
    objectid: str
    classname: str
    bbox: Optional[Tuple[float, float, float, float, float, float]] = None
    lods: Tuple[str, ...] = ()
    rings: List[List[float]] = field(default_factory=list)
    footprint: bool = False  # rings are a ground-surface footprint, not real LoD0

    @property
    def lod_levels(self) -> Tuple[int, ...]:
        levels = set()
        for name in self.lods:
            m = re.match(r"^lod(\d)[A-Z]", name)
            if m and "TerrainIntersection" not in name:
                levels.add(int(m.group(1)))
        return tuple(sorted(levels))

    @property
    def has_lod0_geometry(self) -> bool:
        return any(n in LOD0_PROPERTY_NAMES for n in self.lods)

    @property
    def label(self) -> str:
        return self.objectid or f"feature {self.id}"


def _row(r) -> CatalogRow:
    bbox = None
    if r[3] is not None:
        zmin = float(r[5]) if r[5] is not None else 0.0
        zmax = float(r[8]) if r[8] is not None else zmin
        bbox = (float(r[3]), float(r[4]), zmin, float(r[6]), float(r[7]), zmax)
    lods = tuple(sorted(str(n) for n in (r[9] or ()) if n))
    return CatalogRow(id=int(r[0]), objectid=str(r[1] or ""), classname=str(r[2] or ""), bbox=bbox, lods=lods)


def group_versions(rows: Iterable[CatalogRow]) -> Dict[str, List[CatalogRow]]:
    """Rows by objectid (several rows: LoD2 and LoD3 imported as separate features)."""
    out: Dict[str, List[CatalogRow]] = {}
    for r in rows:
        out.setdefault(r.objectid or f"#{r.id}", []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r.id)
    return out


def _lod_text(row: CatalogRow) -> str:
    levels = row.lod_levels
    return "/".join(f"LoD{v}" for v in levels) if levels else "no LoD"


def versions_label(rows: Sequence[CatalogRow]) -> str:
    """``"2 versions: LoD2 (id 13), LoD3 (id 43)"``; single rows give ``"LoD2 (id 13)"``."""
    rows = sorted(rows, key=lambda r: r.id)
    parts = ", ".join(f"{_lod_text(r)} (id {r.id})" for r in rows)
    return f"{len(rows)} versions: {parts}" if len(rows) > 1 else parts


def preview_label(rows: Sequence[CatalogRow]) -> str:
    """'LoD0' only for real lod0 geometry, 'Footprint (ground surface)' for the derived one."""
    rows = [r for r in rows if r.bbox]
    real = sum(1 for r in rows if r.rings and not r.footprint)
    foot = sum(1 for r in rows if r.rings and r.footprint)
    parts = []
    if real:
        parts.append(PREVIEW_LOD0)
    if foot:
        parts.append(PREVIEW_FOOTPRINT)
    if real + foot < len(rows) or not parts:
        parts.append(PREVIEW_BBOX)
    return " + ".join(parts)


def plan_groups(rows: Dict[int, CatalogRow], mode: str) -> Tuple[List[int], List[int], List[int]]:
    """``(feature ids to export, ids without the requested LoD, other versions left out)``.

    Several selected rows with one objectid are versions of one building (for
    example LoD2 and LoD3 imported separately); only one is loaded: for 'LOD2'
    the newest row that has LoD2, for 'HIGHEST' the row with the highest LoD
    (the newest on a tie).
    """
    if mode not in ("LOD2", "HIGHEST"):
        raise ValueError(f"unknown load mode {mode}")
    load, missing, skipped = [], [], []
    for _objectid, versions in group_versions(rows.values()).items():
        if mode == "LOD2":
            ok = [r for r in versions if 2 in r.lod_levels]
            best = max(ok, key=lambda r: r.id) if ok else None
        else:
            ok = [r for r in versions if r.lod_levels]
            best = max(ok, key=lambda r: (max(r.lod_levels), r.id)) if ok else None
        if best is None:
            missing.extend(r.id for r in versions)
            continue
        load.append(best.id)
        skipped.extend(r.id for r in versions if r.id != best.id)
    return sorted(load), sorted(missing), sorted(skipped)


def loaded_level(row: CatalogRow, mode: str) -> int:
    """LoD a load in ``mode`` brings for ``row`` (-1 without one)."""
    levels = row.lod_levels
    if mode == "LOD2":
        return 2 if 2 in levels else -1
    return max(levels) if levels else -1


def count_top_level_members(path: str) -> int:
    """Top-level features in a citydb-tool CityGML export (members of the CityModel)."""
    n = 0
    pat = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?(?:cityObjectMember|featureMember)[\s/>]")
    with open(path, "rb") as fh:
        tail = b""
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            buf = tail + chunk
            n += len(pat.findall(buf))
            # keep a short tail so a tag split across chunks is seen once
            cut = max(0, len(buf) - 64)
            n -= len(pat.findall(buf[cut:]))
            tail = buf[cut:]
        n += len(pat.findall(tail))
    return n


# --------------------------------------------------------------------------- WKB
def wkb_linestring_xy(data) -> List[float]:
    """Flat ``[x0, y0, x1, y1, ...]`` of a WKB (or EWKB) 2D/3D LineString."""
    buf = bytes(data)
    if len(buf) < 9:
        return []
    order = "<" if buf[0] == 1 else ">"
    (gtype,) = struct.unpack_from(order + "I", buf, 1)
    off = 5
    has_srid = bool(gtype & 0x20000000)
    has_z = bool(gtype & 0x80000000) or (gtype & 0xFFFF) // 1000 in (1, 3)
    has_m = bool(gtype & 0x40000000) or (gtype & 0xFFFF) // 1000 in (2, 3)
    if has_srid:
        off += 4
    if (gtype & 0xFFFF) % 1000 != 2:
        return []
    (n,) = struct.unpack_from(order + "I", buf, off)
    off += 4
    dim = 2 + int(has_z) + int(has_m)
    vals = struct.unpack_from(order + f"{n * dim}d", buf, off)
    out: List[float] = []
    for i in range(n):
        out.append(vals[i * dim])
        out.append(vals[i * dim + 1])
    return out


# --------------------------------------------------------------------------- SQL
def _sql():
    from .client import _psycopg
    return _psycopg()


def _toplevel_where(sql, s, classname: str):
    where = sql.SQL(
        "oc.is_toplevel = 1 AND f.termination_date IS NULL "
        "AND f.envelope && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, %(srid)s)")
    if classname:
        where = where + sql.SQL(" AND oc.classname = %(classname)s")
    return where


def count_sql(schema: str, classname: str = ""):
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "SELECT count(*) FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id WHERE {w}"
    ).format(s=s, w=_toplevel_where(sql, s, classname))


BASE_Z_SAMPLE = 500


def base_z_sql(schema: str, classname: str = ""):
    """Median ``ST_ZMin(envelope)`` of (at most ``%(sample)s``) top-level features in the extent."""
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY t.z) FROM ("
        " SELECT ST_ZMin(f.envelope) AS z"
        " FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id"
        " WHERE {w} AND f.envelope IS NOT NULL LIMIT %(sample)s) t"
    ).format(s=s, w=_toplevel_where(sql, s, classname))


def page_sql(schema: str, classname: str = ""):
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "WITH sel AS ("
        " SELECT f.id, f.objectid, oc.classname, f.envelope"
        " FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id"
        " WHERE {w} AND f.id > %(after_id)s"
        " ORDER BY f.id LIMIT %(limit)s) "
        + _ROW_SELECT
    ).format(s=s, w=_toplevel_where(sql, s, classname))


# ``val_relation_type + 0`` keeps the planner off property_val_relation_type_inx:
# right after a citydb-tool import the tables have no statistics yet and it
# AND-ed that index (every boundary row) into each lookup - 1.2 s instead of 11 ms.
_ROW_SELECT = (
    "SELECT sel.id, sel.objectid, sel.classname,"
    " ST_XMin(sel.envelope), ST_YMin(sel.envelope), ST_ZMin(sel.envelope),"
    " ST_XMax(sel.envelope), ST_YMax(sel.envelope), ST_ZMax(sel.envelope),"
    " ARRAY(SELECT DISTINCT p.name FROM {s}.property p"
    "  WHERE p.val_lod IS NOT NULL AND p.feature_id IN ("
    "   SELECT sel.id"
    "   UNION ALL SELECT c.val_feature_id FROM {s}.property c"
    "    WHERE c.feature_id = sel.id AND c.val_relation_type + 0 = 1"
    "   UNION ALL SELECT g.val_feature_id FROM {s}.property c"
    "    JOIN {s}.property g ON g.feature_id = c.val_feature_id AND g.val_relation_type + 0 = 1"
    "    WHERE c.feature_id = sel.id AND c.val_relation_type + 0 = 1))"
    " FROM sel ORDER BY sel.id"
)


def find_sql(schema: str):
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "WITH sel AS ("
        " SELECT f.id, f.objectid, oc.classname, f.envelope"
        " FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id"
        " WHERE oc.is_toplevel = 1 AND f.termination_date IS NULL"
        " AND (f.objectid = %(objectid)s OR f.id = %(fid)s)"
        " ORDER BY f.id LIMIT %(limit)s) "
        + _ROW_SELECT
    ).format(s=s)


def lod0_sql(schema: str):
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "SELECT p.feature_id, ST_AsBinary(ST_Force2D(ST_ExteriorRing(d.geom)))"
        " FROM {s}.property p JOIN {s}.geometry_data g ON g.id = p.val_geometry_id,"
        " LATERAL ST_Dump(g.geometry) d"
        " WHERE p.feature_id = ANY(%(ids)s) AND p.name = ANY(%(names)s)"
        " AND GeometryType(d.geom) = 'POLYGON'"
        " ORDER BY p.feature_id, p.id, d.path"
    ).format(s=s)


def footprint_sql(schema: str):
    _, sql = _sql()
    s = sql.Identifier(schema)
    return sql.SQL(
        "SELECT u.fid, ST_AsBinary(ST_ExteriorRing(d.geom)) FROM ("
        " SELECT c.feature_id AS fid, ST_Union(ST_MakeValid(ST_Force2D(g.geometry))) AS geom"
        " FROM {s}.property c"
        " JOIN {s}.feature gs ON gs.id = c.val_feature_id"
        " JOIN {s}.objectclass oc ON oc.id = gs.objectclass_id AND oc.classname = 'GroundSurface'"
        " JOIN {s}.property gp ON gp.feature_id = gs.id AND gp.val_geometry_id IS NOT NULL"
        " JOIN {s}.geometry_data g ON g.id = gp.val_geometry_id"
        " WHERE c.feature_id = ANY(%(ids)s) AND c.val_relation_type + 0 = 1"
        " GROUP BY c.feature_id) u, LATERAL ST_Dump(u.geom) d"
        " WHERE GeometryType(d.geom) = 'POLYGON'"
        " ORDER BY u.fid, d.path"
    ).format(s=s)


def extent_params(extent: Sequence[float], srid: int, classname: str = "") -> Dict[str, object]:
    xmin, ymin, xmax, ymax = (float(v) for v in extent)
    if not (xmin < xmax and ymin < ymax):
        raise ValueError(f"Empty extent: {extent}")
    p: Dict[str, object] = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax, "srid": int(srid)}
    if classname:
        p["classname"] = validate_classname(classname)
    return p


# --------------------------------------------------------------------------- queries
def _estimate(conn, query, params) -> Optional[int]:
    """Planner estimate of the rows ``query`` returns (EXPLAIN, nothing runs)."""
    psycopg, sql = _sql()
    try:
        row = conn.execute(sql.SQL("EXPLAIN (FORMAT JSON) ") + query, params).fetchone()
        plan = row[0][0]["Plan"] if row else None
        return int(plan.get("Plan Rows", 0)) if plan else None
    except (psycopg.Error, KeyError, IndexError, TypeError):
        conn.rollback()
        return None


def count_in_extent(conn, schema: str, extent, srid: int, *, classname: str = "",
                    timeout_ms: int = COUNT_TIMEOUT_MS) -> Tuple[int, bool]:
    """``(count, estimated)`` of top-level features in ``extent``."""
    from .client import _with_timeout
    params = extent_params(extent, srid, classname)
    rows = _with_timeout(conn, timeout_ms, count_sql(schema, classname), params)
    if rows is not None:
        return int(rows[0][0]), False
    _, sql = _sql()
    s = sql.Identifier(schema)
    q = sql.SQL("SELECT f.id FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id WHERE {w}").format(
        s=s, w=_toplevel_where(sql, s, classname))
    return max(int(_estimate(conn, q, params) or 0), 0), True


def base_z_in_extent(conn, schema: str, extent, srid: int, *, classname: str = "",
                     timeout_ms: int = COUNT_TIMEOUT_MS, sample: int = BASE_Z_SAMPLE) -> Optional[float]:
    """Median base height (CRS Z) of the features in ``extent``; None when there
    are none, the envelopes are 2D or the query times out."""
    from .client import _with_timeout
    params = dict(extent_params(extent, srid, classname), sample=int(sample))
    rows = _with_timeout(conn, timeout_ms, base_z_sql(schema, classname), params)
    if not rows or rows[0][0] is None:
        return None
    return float(rows[0][0])


def query_extent(conn, schema: str, extent, srid: int, *, limit: int = DEFAULT_LIMIT,
                 after_id: int = 0, classname: str = "") -> List[CatalogRow]:
    params = extent_params(extent, srid, classname)
    params["limit"] = clamp_limit(limit)
    params["after_id"] = int(after_id)
    return [_row(r) for r in conn.execute(page_sql(schema, classname), params).fetchall()]


def find_by_id(conn, schema: str, text: str) -> List[CatalogRow]:
    fid, objectid = parse_id_token(text)
    params = {"objectid": objectid, "fid": fid, "limit": MAX_ID_MATCHES}
    return [_row(r) for r in conn.execute(find_sql(schema), params).fetchall()]


def active_versions(conn, schema: str, objectid: str) -> List[CatalogRow]:
    """Active top-level rows with exactly this objectid (what citydb-tool calls duplicates, T10)."""
    _fid, objectid = parse_id_token(objectid)
    params = {"objectid": objectid, "fid": None, "limit": MAX_ID_MATCHES}
    return [_row(r) for r in conn.execute(find_sql(schema), params).fetchall()]


def _rings(rows) -> Dict[int, List[List[float]]]:
    out: Dict[int, List[List[float]]] = {}
    for fid, wkb in rows:
        if wkb is None:
            continue
        ring = wkb_linestring_xy(wkb)
        if len(ring) >= 6:
            out.setdefault(int(fid), []).append(ring)
    return out


def lod0_rings(conn, schema: str, ids: Sequence[int]) -> Dict[int, List[List[float]]]:
    ids = validate_feature_ids(ids)
    if not ids:
        return {}
    return _rings(conn.execute(lod0_sql(schema), {"ids": ids, "names": list(LOD0_PROPERTY_NAMES)}).fetchall())


def ground_footprints(conn, schema: str, ids: Sequence[int], *,
                      timeout_ms: int = FOOTPRINT_TIMEOUT_MS) -> Tuple[Dict[int, List[List[float]]], float, bool]:
    """``(rings by feature id, seconds, completed)``; incomplete when the timeout hit or GEOS failed."""
    from .client import _with_timeout
    psycopg, _ = _sql()
    ids = validate_feature_ids(ids)
    t0 = time.perf_counter()
    if not ids:
        return {}, 0.0, True
    try:
        rows = _with_timeout(conn, timeout_ms, footprint_sql(schema), {"ids": ids})
    except psycopg.Error:
        conn.rollback()
        rows = None
    return (_rings(rows) if rows else {}), time.perf_counter() - t0, rows is not None


def schema_extent(conn, schema: str, *, timeout_ms: int = COUNT_TIMEOUT_MS):
    """3D extent of the top-level features ``(xmin, ymin, zmin, xmax, ymax, zmax)`` or None."""
    from .client import _with_timeout
    psycopg, sql = _sql()
    s = sql.Identifier(schema)
    rows = _with_timeout(conn, timeout_ms, sql.SQL(
        "SELECT ST_XMin(e), ST_YMin(e), ST_ZMin(e), ST_XMax(e), ST_YMax(e), ST_ZMax(e) FROM ("
        " SELECT ST_3DExtent(f.envelope) AS e FROM {s}.feature f"
        " JOIN {s}.objectclass oc ON oc.id = f.objectclass_id"
        " WHERE oc.is_toplevel = 1 AND f.termination_date IS NULL) q").format(s=s))
    if rows and rows[0] and rows[0][0] is not None:
        return tuple(float(v) for v in rows[0])
    try:
        row = conn.execute(
            "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
            "FROM (SELECT ST_EstimatedExtent(%s, 'feature', 'envelope') AS e) q", [schema]).fetchone()
    except psycopg.Error:
        conn.rollback()
        return None
    if row and row[0] is not None:
        return (float(row[0]), float(row[1]), 0.0, float(row[2]), float(row[3]), 0.0)
    return None
