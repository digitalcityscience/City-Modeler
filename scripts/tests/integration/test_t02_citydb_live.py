"""T02 integration: discovery, verification and citydb-tool against a live 3DCityDB v5.

Runs only when CGML3_TEST_DB_HOST is set (skipped otherwise), e.g. for the
local 3DCityDB v5 (docker compose):

    CGML3_TEST_DB_HOST=127.0.0.1 CGML3_TEST_DB_PORT=55433 CGML3_TEST_DB_USER=citydb
    CGML3_TEST_DB_PASSWORD=...   (environment only)
    PYTHONPATH=<repo> python -m pytest -q scripts/tests/integration --import-mode=importlib

Expects the fixtures described in citydb-v5/README.md: database ``citydb`` with
schemas ``citydb`` (6 buildings) and ``hamburg_lod2`` (3 buildings, schema
comment), database ``citydb_demo`` (empty, EPSG:3857), database ``postgres``.
Needs psycopg; the citydb-tool test also needs Java and the bundled citydb-tool.
"""
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

HOST = os.environ.get("CGML3_TEST_DB_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="set CGML3_TEST_DB_HOST to run live 3DCityDB tests")

psycopg = pytest.importorskip("psycopg")

from citydb import client, errors, models  # noqa: E402
from citydb.connection import CityDBTarget, ServerProfile  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PORT = int(os.environ.get("CGML3_TEST_DB_PORT", "5432"))
USER = os.environ.get("CGML3_TEST_DB_USER", "citydb")
PASSWORD = os.environ.get("CGML3_TEST_DB_PASSWORD", "")
SPECIAL_PASSWORD = "p&ss|w^rd\"x"


@pytest.fixture(scope="module")
def server():
    return ServerProfile(HOST, PORT, USER)


@pytest.fixture(scope="module")
def discovery(server):
    result, used = client.discover(server, PASSWORD)
    return result


def _by_key(result):
    return {s.key: s for s in result.schemas}


def test_discovery_lists_city_models_across_databases(discovery):
    found = _by_key(discovery)
    assert {"citydb/citydb", "citydb/hamburg_lod2", "citydb_demo/citydb"} <= set(found)
    hh = found["citydb/hamburg_lod2"]
    assert hh.status == models.OK and hh.version.startswith("5.")
    assert hh.srid == 25832 and hh.crs_name == "ETRS89 / UTM zone 32N"
    assert hh.feature_counts == {"Building": 3}
    assert hh.display_label() == "Hamburg HafenCity (LoD2 only)"
    lon, lat = hh.center_lonlat
    assert 9.9 < lon < 10.1 and 53.5 < lat < 53.6  # Hamburg
    assert found["citydb/citydb"].feature_counts == {"Building": 6}
    demo = found["citydb_demo/citydb"]
    assert demo.status == models.EMPTY and demo.srid == 3857
    assert "postgres (no 3DCityDB)" in models.other_databases_line(discovery)


def test_discovery_can_be_restricted_to_databases(server):
    result, _ = client.discover(server, PASSWORD, databases=["citydb_demo"])
    assert [s.key for s in result.schemas] == ["citydb_demo/citydb"]


def test_verify_target_and_errors(server):
    info, used = client.verify_target(CityDBTarget(server, "citydb", "hamburg_lod2"), PASSWORD)
    assert info.srid == 25832 and used == PASSWORD
    with pytest.raises(errors.CityDBError) as exc:
        client.verify_target(CityDBTarget(server, "citydb", "no_such_schema"), PASSWORD)
    assert exc.value.kind == errors.NO_SCHEMA
    with pytest.raises(errors.CityDBError) as exc:
        client.verify_target(CityDBTarget(server, "no_such_db", "citydb"), PASSWORD)
    assert exc.value.kind == errors.NO_DATABASE
    with pytest.raises(errors.CityDBError) as exc:
        client.verify_target(CityDBTarget(server, "postgres", "citydb"), PASSWORD)
    assert exc.value.kind == errors.NO_SCHEMA


def test_wrong_and_missing_password_are_distinguished(server, monkeypatch):
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSFILE", os.devnull)
    with pytest.raises(errors.CityDBError) as exc:
        client.discover(server, "definitely-wrong")
    assert exc.value.kind == errors.AUTH
    assert "definitely-wrong" not in exc.value.message + exc.value.detail
    with pytest.raises(errors.CityDBError) as exc:
        client.discover(server, None)
    assert exc.value.kind == errors.PASSWORD_REQUIRED


@pytest.fixture(scope="module")
def special_role(server):
    """A temporary role whose password contains cmd.exe and shell metacharacters."""
    from psycopg import sql

    role = f"cgml3_t02_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(**server.libpq_params("citydb", PASSWORD), autocommit=True)
    admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(SPECIAL_PASSWORD)))
    admin.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(role)))
    try:
        yield role
    finally:
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        admin.close()


def test_special_character_password_with_psycopg(server, special_role):
    srv = ServerProfile(server.host, server.port, special_role)
    info, _ = client.verify_target(CityDBTarget(srv, "citydb", "hamburg_lod2"), SPECIAL_PASSWORD)
    assert info.status == models.OK


def _java():
    home = os.environ.get("JAVA_HOME")
    if home:
        exe = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if exe.is_file():
            return str(exe)
    from shutil import which
    return which("java")


def _command_lines_of_java():
    """Command lines of running java processes (to prove the password is not among them)."""
    if os.name == "nt":
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True).stdout
    else:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if "org.citydb.cli" in line]


@pytest.mark.skipif(not _java(), reason="needs Java for citydb-tool")
def test_citydb_tool_connects_with_env_only_and_argv_stays_clean(server, special_role, tmp_path):
    import importlib
    import types

    pkg = "cgml3_live_cli"
    for name, path in ((pkg, ROOT), (f"{pkg}.ops", ROOT / "ops"), (f"{pkg}.common", ROOT / "common")):
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules[name] = mod
    cli = importlib.import_module(f"{pkg}.ops.citydb_cli")
    launcher = ROOT / "citydb-tool-1.3.0" / ("citydb.bat" if os.name == "nt" else "citydb")
    tool = cli.CityDBTool(types.SimpleNamespace(use_docker=False, docker_image="", citydb_exe=str(launcher)))

    seen = []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            seen.extend(_command_lines_of_java())
            time.sleep(0.2)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    out = tmp_path / "hh.gml"
    try:
        ok, log = tool.export_db_to_citygml(
            str(out), host=server.host, port=str(server.port), db="citydb",
            user=special_role, password=SPECIAL_PASSWORD, db_schema="hamburg_lod2",
        )
    finally:
        stop.set()
        watcher.join(2)

    assert ok, log
    assert SPECIAL_PASSWORD not in log
    text = out.read_text(encoding="utf-8")
    assert text.count("<bldg:Building ") == 3
    assert seen, "java process was not observed"
    assert not any(SPECIAL_PASSWORD in line for line in seen)
    assert not any(" -p " in f" {line} " or "--db-password" in line for line in seen)
