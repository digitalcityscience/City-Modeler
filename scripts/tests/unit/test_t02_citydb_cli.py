"""T02: citydb-tool invocation - no password in argv, no shell, credentials via env."""
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SECRET = "p&ss|w^rd\"x"
PKG = "cgml3_cli_under_test"


def _load_cli():
    """Import ops/citydb_cli.py without ops/__init__ and common/__init__ (they need bpy)."""
    if f"{PKG}.ops.citydb_cli" in sys.modules:
        return sys.modules[f"{PKG}.ops.citydb_cli"]
    for name, path in ((PKG, ROOT), (f"{PKG}.ops", ROOT / "ops"), (f"{PKG}.common", ROOT / "common")):
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules[name] = mod
    return importlib.import_module(f"{PKG}.ops.citydb_cli")


@pytest.fixture
def cli():
    return _load_cli()


def _prefs(**kw):
    base = dict(use_docker=False, docker_image="", citydb_exe="")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _capture(tool):
    calls = []

    def fake_run(args, cwd=None, timeout=None, env=None):
        calls.append((list(args), dict(env or {})))
        return True, ""

    tool._run = fake_run
    return calls


CONN = dict(host="127.0.0.1", port="55433", db="citydb", user="citydb", password=SECRET, db_schema="hamburg_lod2")


def test_export_puts_credentials_in_env_only(cli, tmp_path):
    tool = cli.CityDBTool(_prefs(citydb_exe="citydb"))
    calls = _capture(tool)
    tool.export_db_to_citygml(str(tmp_path / "out.gml"), **CONN, feature_ids=["DEHHALKAJ0000yAZ"])
    args, env = calls[0]
    joined = " ".join(args)
    assert SECRET not in joined
    for flag in ("-p", "--db-password", "-H", "-u", "-d", "-S"):
        assert flag not in args
    assert env["CITYDB_PASSWORD"] == SECRET
    assert env["CITYDB_SCHEMA"] == "hamburg_lod2"
    assert env["CITYDB_HOST"] == "127.0.0.1" and env["CITYDB_PORT"] == "55433"
    assert "objectId IN ('DEHHALKAJ0000yAZ')" in args


def test_import_and_delete_use_env_too(cli, tmp_path):
    gml = tmp_path / "in.gml"
    gml.write_text("<x/>")
    tool = cli.CityDBTool(_prefs(citydb_exe="citydb"))
    calls = _capture(tool)
    tool.import_citygml_to_db(str(gml), **CONN)
    tool.delete_features_from_db(**CONN, feature_ids=["a", "b"])
    for args, env in calls:
        assert SECRET not in " ".join(args)
        assert env["CITYDB_PASSWORD"] == SECRET


def test_sslmode_becomes_a_jdbc_property(cli, tmp_path):
    tool = cli.CityDBTool(_prefs(citydb_exe="citydb"))
    calls = _capture(tool)
    tool.export_db_to_citygml(str(tmp_path / "o.gml"), **CONN, sslmode="require")
    args, _ = calls[0]
    i = args.index("--db-property")
    assert args[i + 1] == "sslmode=require"


def test_docker_gets_variable_names_not_values(cli, tmp_path):
    tool = cli.CityDBTool(_prefs(use_docker=True, docker_image="3dcitydb/citydb-tool:1.3.0"))
    calls = _capture(tool)
    tool.export_db_to_citygml(str(tmp_path / "o.gml"), **CONN)
    args, env = calls[0]
    assert SECRET not in " ".join(args)
    assert "CITYDB_PASSWORD" in args and args[args.index("CITYDB_PASSWORD") - 1] == "-e"
    assert not any("=" in a and a.startswith("CITYDB_") for a in args)
    assert env["CITYDB_PASSWORD"] == SECRET


def test_bundled_launcher_is_started_through_java(cli, tmp_path, monkeypatch):
    tool_dir = tmp_path / "citydb-tool-1.3.0"
    (tool_dir / "lib").mkdir(parents=True)
    (tool_dir / "lib" / "citydb-cli-1.3.0.jar").write_bytes(b"")
    launcher = tool_dir / ("citydb.bat" if os.name == "nt" else "citydb")
    launcher.write_text("")
    java_home = tmp_path / "jdk"
    (java_home / "bin").mkdir(parents=True)
    java = java_home / "bin" / ("java.exe" if os.name == "nt" else "java")
    java.write_text("")
    monkeypatch.setenv("JAVA_HOME", str(java_home))
    monkeypatch.delenv("JAVA_OPTS", raising=False)
    monkeypatch.delenv("CITYDB_OPTS", raising=False)

    tool = cli.CityDBTool(_prefs(citydb_exe=str(launcher)))
    base = tool._base_native()
    assert base == [str(java), "--module-path", str(tool_dir / "lib"), "--module", "org.citydb.cli/org.citydb.cli.Launcher"]


def test_run_passes_env_to_child_and_redacts_output(cli):
    tool = cli.CityDBTool(_prefs())
    code = "import os, sys; print('pw=' + os.environ['CITYDB_PASSWORD']); print('argv', sys.argv[1:])"
    ok, out = tool._run([sys.executable, "-c", code, "x"], env={"CITYDB_PASSWORD": SECRET, "CITYDB_HOST": "h"})
    assert ok
    assert SECRET not in out
    assert "pw=***" in out
    assert "argv ['x']" in out


def test_run_does_not_leak_parent_citydb_variables(cli, monkeypatch):
    monkeypatch.setenv("CITYDB_SCHEMA", "from_parent")
    tool = cli.CityDBTool(_prefs())
    code = "import os; print(os.environ.get('CITYDB_SCHEMA', 'unset'))"
    ok, out = tool._run([sys.executable, "-c", code], env={"CITYDB_HOST": "h"})
    assert ok and out.strip() == "unset"


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe quoting only matters on Windows")
def test_custom_bat_with_cmd_metacharacters_is_refused(cli, tmp_path):
    bat = tmp_path / "custom.bat"
    bat.write_text("@echo off\r\necho %*\r\n")
    tool = cli.CityDBTool(_prefs())
    ok, out = tool._run([str(bat), "export", "--filter", "name = 'a&b'"])
    assert not ok and "Refusing" in out
