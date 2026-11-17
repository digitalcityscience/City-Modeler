"""T05: the add-on is 3DCityDB 5.x only - v4 is rejected with one clear message."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from citydb import client, errors, models

ROOT = Path(__file__).resolve().parents[3]
MESSAGE_44 = "Unsupported database: 3DCityDB 4.4.0. This add-on requires 3DCityDB 5.x."


def test_unsupported_version_message_is_exact():
    assert errors.unsupported_version_message("4.4.0") == MESSAGE_44


@pytest.fixture
def no_driver(monkeypatch):
    # inspect_schema decides on the version before touching the connection.
    monkeypatch.setattr(client, "_psycopg", lambda: (None, None))


def test_inspect_schema_rejects_v4_with_the_message(no_driver):
    info = client.inspect_schema(None, "citydb", "citydb", version="4.4.0")
    assert info.status == models.UNSUPPORTED
    assert info.message == MESSAGE_44


def test_inspect_schema_without_version_or_feature_table_is_not_v5(no_driver):
    assert client.inspect_schema(None, "db", "x", version="").message == "Not a 3DCityDB v5 schema"
    info = client.inspect_schema(None, "db", "x", version="5.1.4", has_feature=False)
    assert info.status == models.UNSUPPORTED and info.message == "Not a 3DCityDB v5 schema"


def test_verify_target_raises_unsupported_for_v4(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    from citydb.connection import CityDBTarget, ServerProfile

    monkeypatch.setattr(client, "_psycopg", lambda: (None, None))
    monkeypatch.setattr(client, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(client, "effective_password", lambda conn: None)
    monkeypatch.setattr(client, "list_citydb_schemas", lambda conn: [("citydb", "", True, False)])
    monkeypatch.setattr(client, "citydb_version", lambda conn: "4.4.0")

    target = CityDBTarget(ServerProfile(host="127.0.0.1", port=55432, user="postgres"), "citydb", "citydb")
    with pytest.raises(errors.CityDBError) as exc:
        client.verify_target(target, None)
    assert exc.value.kind == errors.UNSUPPORTED
    assert exc.value.message == MESSAGE_44


@pytest.mark.parametrize("log, expected", [
    ("org.citydb.database.DatabaseVersionException: The 3DCityDB version 4.4.0 is not supported. "
     "Supported versions are 5.x.", MESSAGE_44),
    ("ERROR: 3DCityDB version v4.3.1 is not supported (supported versions are 5.0 and later)",
     "Unsupported database: 3DCityDB 4.3.1. This add-on requires 3DCityDB 5.x."),
    ("DatabaseVersionException: not supported", errors.unsupported_version_message("(not 5.x)")),
])
def test_citydb_tool_rejection_gets_the_same_message(log, expected):
    assert errors.citydb_tool_unsupported_message(log) == expected


@pytest.mark.parametrize("log", ["", None, "Connection refused", "Feature type 'x' is not supported"])
def test_other_citydb_tool_failures_are_not_version_errors(log):
    assert errors.citydb_tool_unsupported_message(log) is None


def _load_presets(monkeypatch):
    fake = types.ModuleType("bpy")
    fake.types = types.SimpleNamespace(Operator=object, Panel=object)
    fake.props = types.SimpleNamespace(StringProperty=lambda **k: None, EnumProperty=lambda **k: None)
    monkeypatch.setitem(sys.modules, "bpy", fake)
    monkeypatch.setitem(sys.modules, "bpy.types", fake.types)
    monkeypatch.setitem(sys.modules, "bpy.props", fake.props)
    spec = importlib.util.spec_from_file_location("presets_t05", ROOT / "presets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_old_preset_with_v4_keys_loads(monkeypatch, tmp_path):
    presets = _load_presets(monkeypatch)
    prop = presets.SAVEABLE_PROPS[0]
    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        prop: True,
        "citydb_v4_java_exe": "C:/java/bin/java.exe",
        "citydb_v4_impexp_jar": "impexp-client-cli-5.5.3.jar",
        "citydb_v4_default_config": "impexp_config.xml",
    }), encoding="utf-8")
    cgml3 = types.SimpleNamespace(**{prop: False})

    ok, msg = presets.load_preset(types.SimpleNamespace(scene=types.SimpleNamespace(cgml3=cgml3)), str(path))

    assert ok and msg == "1 settings loaded"
    assert getattr(cgml3, prop) is True
    assert not hasattr(cgml3, "citydb_v4_java_exe")


def test_v4_wrapper_and_tool_are_gone():
    assert not (ROOT / "ops" / "citydb_v4_cli.py").exists()
    assert not (ROOT / "3dcitydb-tool-v4").exists()
    source = (ROOT / "__init__.py").read_text(encoding="utf-8")
    for name in ("CityDBV4Tool", "citydb_v4_", "_citydb_v5_requires_v4_fallback", "_log_citydb_backend_"):
        assert name not in source
