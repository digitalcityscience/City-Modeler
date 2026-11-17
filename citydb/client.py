# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/client.py
"""Direct, read-only PostgreSQL access to 3DCityDB v5 through psycopg 3.

No bpy here: functions run in worker threads and return plain models.
Every query is parameterised; schema names only ever enter SQL through
``psycopg.sql.Identifier``. Slow statistics (feature counts, extent) run
under a per-statement timeout and fall back to PostgreSQL's estimates.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional, Tuple

from . import errors
from .connection import CityDBTarget, ServerProfile
from .credentials import redact
from .models import (
    DB_CITYDB, DB_ERROR, DB_NO_ACCESS, DB_NOT_CITYDB, EMPTY, NO_ACCESS, OK, UNSUPPORTED,
    DatabaseInfo, DiscoveryResult, SchemaInfo,
)

STATS_TIMEOUT_MS = 3000
MAX_PARALLEL_DATABASES = 4
SUPPORTED_MAJOR = "5"

_SQL_LIST_DATABASES = """
SELECT datname
FROM pg_database
WHERE datallowconn
  AND NOT datistemplate
  AND has_database_privilege(datname, 'CONNECT')
ORDER BY datname
"""

# A 3DCityDB schema is recognised by its database_srs table (as citydb_pkg.schema_exists does).
_SQL_LIST_CITYDB_SCHEMAS = """
SELECT n.nspname,
       coalesce(obj_description(n.oid, 'pg_namespace'), ''),
       has_schema_privilege(n.oid, 'USAGE'),
       to_regclass(format('%I.feature', n.nspname)) IS NOT NULL
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = 'database_srs' AND c.relkind = 'r'
ORDER BY n.nspname
"""


def available() -> bool:
    try:
        import psycopg  # noqa: F401
    except Exception:
        return False
    return True


def _psycopg():
    try:
        import psycopg
        from psycopg import sql
    except Exception as exc:
        raise errors.CityDBError(
            errors.DRIVER_MISSING,
            errors.user_message(errors.DRIVER_MISSING),
            detail=f"{exc.__class__.__name__}: {exc}",
        ) from None
    return psycopg, sql


def _wrap(exc, server: ServerProfile, *, database: str = "", schema: str = "",
          password: Optional[str] = None) -> errors.CityDBError:
    sqlstate = getattr(exc, "sqlstate", None)
    raw = str(exc)
    kind = errors.classify(sqlstate, raw)
    message = errors.user_message(kind, host=server.host, port=server.port, user=server.user,
                                  database=database, schema=schema)
    return errors.CityDBError(kind, message, detail=redact(raw, [password]))


def connect(server: ServerProfile, database: str, password: Optional[str] = None):
    """Open a read-only connection or raise ``CityDBError``."""
    psycopg, _ = _psycopg()
    try:
        conn = psycopg.connect(**server.libpq_params(database, password))
    except psycopg.Error as exc:
        raise _wrap(exc, server, database=database, password=password) from None
    conn.read_only = True
    return conn


def effective_password(conn) -> Optional[str]:
    """The password libpq used (explicit, PGPASSWORD or ~/.pgpass)."""
    try:
        return conn.info.password or None
    except Exception:
        return None


def _scalar(conn, query, params=None):
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def _with_timeout(conn, timeout_ms: int, query, params=None):
    """Run one statement under ``statement_timeout``; None if it was cancelled."""
    psycopg, _ = _psycopg()
    try:
        conn.execute("SELECT set_config('statement_timeout', %s, true)", [str(int(timeout_ms))])
        return conn.execute(query, params).fetchall()
    except psycopg.errors.QueryCanceled:
        conn.rollback()
        return None
    finally:
        try:
            conn.execute("SELECT set_config('statement_timeout', '0', true)")
        except Exception:
            conn.rollback()


def citydb_version(conn) -> str:
    """3DCityDB version of the database, '' when citydb_pkg is missing."""
    psycopg, _ = _psycopg()
    if not _scalar(conn, "SELECT to_regnamespace('citydb_pkg') IS NOT NULL"):
        return ""
    try:
        return str(_scalar(conn, "SELECT version FROM citydb_pkg.citydb_version()") or "")
    except psycopg.Error:
        conn.rollback()
        return ""


def list_citydb_schemas(conn) -> List[Tuple[str, str, bool, bool]]:
    """(schema, comment, can_use, has_feature_table) for every 3DCityDB schema."""
    return [tuple(r) for r in conn.execute(_SQL_LIST_CITYDB_SCHEMAS).fetchall()]


def inspect_schema(conn, database: str, schema: str, *, comment: str = "", can_use: bool = True,
                   has_feature: bool = True, version: str = "", stats: bool = True,
                   timeout_ms: int = STATS_TIMEOUT_MS) -> SchemaInfo:
    """Facts about one schema; ``stats=False`` skips counts, extent and dates."""
    psycopg, sql = _psycopg()
    info = SchemaInfo(database=database, schema=schema, comment=comment, version=version)
    if not can_use:
        info.status = NO_ACCESS
        info.message = "No USAGE privilege on this schema"
        return info
    if not version.startswith(SUPPORTED_MAJOR + ".") or not has_feature:
        info.status = UNSUPPORTED
        info.message = (errors.unsupported_version_message(version)
                        if version and not version.startswith(SUPPORTED_MAJOR + ".")
                        else "Not a 3DCityDB v5 schema")
        return info

    s = sql.Identifier(schema)
    try:
        row = conn.execute(sql.SQL("SELECT srid, srs_name FROM {}.database_srs LIMIT 1").format(s)).fetchone()
        if row:
            info.srid, info.srs_name = int(row[0]), str(row[1] or "")
        if info.srid:
            row = conn.execute(
                "SELECT split_part(srtext, '\"', 2), srtext ~ '^(GEOGCS|GEOGCRS)' "
                "FROM spatial_ref_sys WHERE srid = %s", [info.srid]).fetchone()
            if row:
                info.crs_name, info.crs_geographic = str(row[0] or ""), bool(row[1])
        info.can_write = bool(_scalar(
            conn, "SELECT has_table_privilege(format('%%I.%%I', %s::text, 'feature'), 'INSERT')", [schema]))
    except psycopg.Error as exc:
        conn.rollback()
        info.status = NO_ACCESS if getattr(exc, "sqlstate", "") == "42501" else UNSUPPORTED
        info.message = redact(str(exc)).splitlines()[0]
        return info

    if not stats:
        return info

    toplevel = sql.SQL(
        "FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id "
        "WHERE oc.is_toplevel = 1 AND f.termination_date IS NULL").format(s=s)

    rows = _with_timeout(conn, timeout_ms, sql.SQL(
        "SELECT oc.classname, count(*) {} GROUP BY oc.classname ORDER BY count(*) DESC, oc.classname"
    ).format(toplevel))
    if rows is None:
        # Counts every feature row (also boundary surfaces), hence "estimate" in the UI.
        estimate = _scalar(conn, "SELECT reltuples::bigint FROM pg_class "
                                 "WHERE oid = to_regclass(format('%%I.feature', %s::text))", [schema])
        info.feature_estimate = max(int(estimate or 0), 0)
    else:
        info.feature_counts = {str(name): int(n) for name, n in rows}

    rows = _with_timeout(conn, timeout_ms, sql.SQL(
        "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
        "FROM (SELECT ST_Extent(f.envelope) AS e {}) q").format(toplevel))
    if rows is None:
        try:
            row = conn.execute(
                "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                "FROM (SELECT ST_EstimatedExtent(%s, 'feature', 'envelope') AS e) q", [schema]).fetchone()
            rows = [row] if row else None
            info.extent_estimated = True
        except psycopg.Error:
            conn.rollback()
    if rows and rows[0] and rows[0][0] is not None:
        info.extent = tuple(float(v) for v in rows[0])

    if info.extent and info.srid:
        xmin, ymin, xmax, ymax = info.extent
        try:
            row = conn.execute(
                "SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform("
                "ST_SetSRID(ST_MakePoint(%s, %s), %s), 4326) AS p) q",
                [(xmin + xmax) / 2.0, (ymin + ymax) / 2.0, info.srid]).fetchone()
            if row and row[0] is not None:
                info.center_lonlat = (float(row[0]), float(row[1]))
        except psycopg.Error:
            conn.rollback()

    rows = _with_timeout(conn, timeout_ms, sql.SQL(
        "SELECT to_char(max(f.last_modification_date), 'YYYY-MM-DD HH24:MI') {}").format(toplevel))
    if rows and rows[0] and rows[0][0]:
        info.last_modified = str(rows[0][0])

    if not info.feature_counts and not info.feature_estimate:
        info.status = EMPTY
    return info


def inspect_database(server: ServerProfile, name: str, password: Optional[str] = None, *,
                     stats: bool = True) -> DatabaseInfo:
    """All 3DCityDB schemas of one database; never raises for per-database problems."""
    try:
        conn = connect(server, name, password)
    except errors.CityDBError as exc:
        status = DB_NO_ACCESS if exc.kind in (errors.PERMISSION, errors.AUTH) else DB_ERROR
        return DatabaseInfo(name=name, status=status, message=exc.message)
    psycopg, _ = _psycopg()
    try:
        with conn:
            schemas = list_citydb_schemas(conn)
            if not schemas:
                return DatabaseInfo(name=name, status=DB_NOT_CITYDB)
            version = citydb_version(conn)
            infos = [
                inspect_schema(conn, name, nsp, comment=comment, can_use=usage, has_feature=has_feature,
                               version=version, stats=stats)
                for nsp, comment, usage, has_feature in schemas
            ]
            return DatabaseInfo(name=name, status=DB_CITYDB, version=version, schemas=infos)
    except psycopg.Error as exc:
        return DatabaseInfo(name=name, status=DB_ERROR, message=redact(str(exc), [password]).splitlines()[0])


def discover(server: ServerProfile, password: Optional[str] = None, *,
             databases: Optional[Iterable[str]] = None, stats: bool = True) -> Tuple[DiscoveryResult, Optional[str]]:
    """Find every 3DCityDB v5 schema the user can reach on ``server``.

    ``databases`` restricts the search (for servers where the maintenance
    database is closed). Returns the result and the password libpq actually
    used. Raises ``CityDBError`` only when the server itself is unusable
    (unreachable, wrong password, ...).
    """
    t0 = time.perf_counter()
    names = [d for d in (databases or []) if d]
    first_db = names[0] if names else server.maintenance_db
    conn = connect(server, first_db, password)
    with conn:
        server_version = str(_scalar(conn, "SHOW server_version") or "")
        used_password = effective_password(conn) or password
        if not names:
            names = [r[0] for r in conn.execute(_SQL_LIST_DATABASES).fetchall()]

    workers = max(1, min(MAX_PARALLEL_DATABASES, len(names)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="citydb-discover") as pool:
        dbs = list(pool.map(lambda n: inspect_database(server, n, used_password, stats=stats), names))

    result = DiscoveryResult(server_version=server_version, databases=dbs,
                             seconds=time.perf_counter() - t0)
    return result, used_password


def verify_target(target: CityDBTarget, password: Optional[str] = None) -> Tuple[SchemaInfo, Optional[str]]:
    """Check that ``target`` is a reachable 3DCityDB v5 schema; quick (no statistics).

    Returns the schema facts and the password libpq used, so citydb-tool can
    get the same credentials (including ones that came from ~/.pgpass).
    """
    conn = connect(target.server, target.database, password)
    with conn:
        used_password = effective_password(conn) or password
        found = {nsp: (comment, usage, has_feature)
                 for nsp, comment, usage, has_feature in list_citydb_schemas(conn)}
        if target.schema not in found:
            raise errors.CityDBError(
                errors.NO_SCHEMA,
                errors.user_message(errors.NO_SCHEMA, database=target.database, schema=target.schema))
        comment, usage, has_feature = found[target.schema]
        info = inspect_schema(conn, target.database, target.schema, comment=comment, can_use=usage,
                              has_feature=has_feature, version=citydb_version(conn), stats=False)
    if info.status == NO_ACCESS:
        raise errors.CityDBError(errors.PERMISSION, errors.user_message(
            errors.PERMISSION, user=target.server.user, database=f"{target.database}/{target.schema}"))
    if info.status == UNSUPPORTED:
        raise errors.CityDBError(errors.UNSUPPORTED, info.message or errors.user_message(
            errors.UNSUPPORTED, database=target.database, schema=target.schema))
    return info, used_password
