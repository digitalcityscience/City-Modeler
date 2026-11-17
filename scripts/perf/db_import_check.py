"""T03: import a generated set into the local 3DCityDB v5 and count the buildings.

The set goes into a fresh, temporary 3DCityDB schema (official
create-schema.sql inside the database container), is imported with the
compose ``citydb-tool`` service, the top-level Building features are counted
with psycopg, and the schema is dropped again (drop-schema.sql). The existing
``citydb`` schema is never written to.

  CITYDB_PASSWORD=... python scripts/perf/db_import_check.py <set.gml> --expect 300

The password is read from the environment only (CITYDB_PASSWORD, or
CGML3_TEST_DB_PASSWORD). Needs psycopg and Docker; host/port default to the
local dev database (127.0.0.1:55433). Exits 1 on an import error or a count
mismatch.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTAINER = "citydb-v5-dev"


def _password() -> str:
    pw = os.environ.get("CITYDB_PASSWORD") or os.environ.get("CGML3_TEST_DB_PASSWORD")
    if not pw:
        sys.exit("set CITYDB_PASSWORD (or CGML3_TEST_DB_PASSWORD) in the environment")
    return pw


def _run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def count_buildings(conn, schema: str) -> int:
    from psycopg import sql
    q = sql.SQL(
        "SELECT count(*) FROM {s}.feature f JOIN {s}.objectclass oc ON oc.id = f.objectclass_id "
        "WHERE oc.classname = 'Building' AND oc.is_toplevel = 1"
    ).format(s=sql.Identifier(schema))
    with conn.cursor() as cur:
        cur.execute(q)
        return cur.fetchone()[0]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("gml", type=Path)
    p.add_argument("--expect", type=int, required=True, help="expected Building count")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=55433)
    p.add_argument("--db", default="citydb")
    p.add_argument("--user", default="citydb")
    p.add_argument("--compose-dir", type=Path, default=REPO.parent / "citydb-v5")
    p.add_argument("--keep", action="store_true", help="keep the temporary schema")
    a = p.parse_args(argv)

    import psycopg

    password = _password()
    schema = f"t03_perf_{secrets.token_hex(4)}"
    gml = a.gml.resolve()
    created = False
    result = {"schema": schema}
    try:
        r = _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", a.user,
                  "-d", a.db, "-v", f"schema_name={schema}", "-f", "create-schema.sql"])
        if r.returncode:
            sys.exit(f"create-schema failed:\n{r.stdout}\n{r.stderr}")
        created = True

        env = dict(os.environ, CITYDB_SCHEMA=schema, CITYDB_PASSWORD=password)
        t0 = time.perf_counter()
        r = _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "-e", "CITYDB_PASSWORD",
                  "-v", f"{gml.parent}:/perf:ro", "citydb-tool", "import", "citygml",
                  f"/perf/{gml.name}"], cwd=a.compose_dir, env=env)
        result["import_seconds"] = round(time.perf_counter() - t0, 1)
        log = (r.stdout or "") + (r.stderr or "")
        problems = [line for line in log.splitlines() if "[ERROR]" in line or "[WARN]" in line]
        tail = "\n".join(log.splitlines()[-8:])
        print(tail)
        result["import_exit_code"] = r.returncode
        result["import_problems"] = problems

        with psycopg.connect(host=a.host, port=a.port, dbname=a.db, user=a.user,
                             password=password, connect_timeout=10) as conn:
            result["buildings"] = count_buildings(conn, schema)
    finally:
        if created and not a.keep:
            r = _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", a.user,
                      "-d", a.db, "-v", f"schema_name={schema}", "-f", "drop-schema.sql"])
            result["dropped"] = r.returncode == 0

    ok = (result.get("import_exit_code") == 0 and not result.get("import_problems")
          and result.get("buildings") == a.expect)
    print(f"[db-import-check] {'OK' if ok else 'FAIL'} {result}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
