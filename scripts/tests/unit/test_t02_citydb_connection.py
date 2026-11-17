"""T02: connection model, error classification, credential store and redaction (no bpy, no DB)."""
import sys
import types

import pytest

from citydb import credentials, errors
from citydb.connection import (
    CITYDB_ENV_NAMES, SSL_MODES, CityDBTarget, ServerProfile, parse_port,
)

SECRET = "p&ss|w^rd\"x"


# ---------------------------------------------------------------------------
# Connection model
# ---------------------------------------------------------------------------
def test_parse_port():
    assert parse_port("55433") == 55433
    assert parse_port(" 5432 ") == 5432
    assert parse_port("") == 5432
    assert parse_port(6543) == 6543
    for bad in ("0", "70000", "abc"):
        with pytest.raises(ValueError):
            parse_port(bad)


def test_server_key_identifies_user_host_port():
    assert ServerProfile("db.example.org", 5433, "citydb").key == "citydb@db.example.org:5433"


def test_libpq_params_without_and_with_password():
    srv = ServerProfile("127.0.0.1", 55433, "citydb", sslmode="require")
    params = srv.libpq_params("citydb")
    assert params["host"] == "127.0.0.1" and params["port"] == 55433
    assert params["dbname"] == "citydb" and params["user"] == "citydb"
    assert params["sslmode"] == "require"
    assert "password" not in params  # libpq may still use ~/.pgpass
    assert srv.libpq_params("citydb", SECRET)["password"] == SECRET


def test_citydb_tool_env_carries_the_whole_connection():
    target = CityDBTarget(ServerProfile("h", 55433, "u"), "citydb", "hamburg_lod2")
    env = target.citydb_tool_env(SECRET)
    assert env == {
        "CITYDB_HOST": "h",
        "CITYDB_PORT": "55433",
        "CITYDB_NAME": "citydb",
        "CITYDB_SCHEMA": "hamburg_lod2",
        "CITYDB_USERNAME": "u",
        "CITYDB_PASSWORD": SECRET,
    }
    assert set(env) == set(CITYDB_ENV_NAMES)
    assert "CITYDB_PASSWORD" not in target.citydb_tool_env(None)


def test_citydb_tool_args_never_contain_secrets():
    for mode in SSL_MODES:
        target = CityDBTarget(ServerProfile("h", 5432, "u", sslmode=mode), "db", "s")
        args = target.citydb_tool_args()
        assert SECRET not in " ".join(args)
        assert "-p" not in args and not any("password" in a for a in args)
        if mode == "prefer":
            assert args == []
        else:
            assert args == ["--db-property", f"sslmode={mode}"]


def test_target_label():
    t = CityDBTarget(ServerProfile("h", 5432, "u"), "citydb", "hamburg_lod2")
    assert t.label == "citydb/hamburg_lod2 @ h:5432"


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sqlstate,message,kind",
    [
        ("28P01", 'connection to server at "127.0.0.1", port 55433 failed: FATAL:  password authentication failed for user "citydb"', errors.AUTH),
        (None, 'connection to server at "127.0.0.1", port 55433 failed: fe_sendauth: no password supplied', errors.PASSWORD_REQUIRED),
        ("3D000", 'FATAL:  database "nodb" does not exist', errors.NO_DATABASE),
        ("28000", 'FATAL:  no pg_hba.conf entry for host "10.0.0.5"', errors.PERMISSION),
        ("42501", "permission denied for schema hamburg", errors.PERMISSION),
        (None, 'could not translate host name "nohost" to address: Name or service not known', errors.UNKNOWN_HOST),
        (None, 'connection to server at "127.0.0.1", port 55499 failed: timeout expired', errors.TIMEOUT),
        (None, 'connection to server at "127.0.0.1", port 55499 failed: Connection refused', errors.UNREACHABLE),
        (None, "server does not support SSL, but SSL was required", errors.SSL),
        (None, "something else", errors.OTHER),
    ],
)
def test_classify(sqlstate, message, kind):
    assert errors.classify(sqlstate, message) == kind


def test_user_messages_are_specific_and_english():
    msg = errors.user_message(errors.NO_SCHEMA, database="citydb", schema="nope")
    assert msg == "Schema 'nope' in database 'citydb' is not a 3DCityDB schema"
    assert "Password required" in errors.user_message(errors.PASSWORD_REQUIRED, host="h", port=1, user="u")
    assert errors.CityDBError(errors.AUTH, "x").needs_password
    assert not errors.CityDBError(errors.TIMEOUT, "x").needs_password


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        f"citydb export citygml -H h -P 5432 -d db -u u -p {SECRET} -o out.gml",
        f"citydb export --db-password={SECRET}",
        f"CITYDB_PASSWORD={SECRET}",
        f"PGPASSWORD={SECRET} psql",
        f"host=h port=5432 password={SECRET} dbname=x",
        "postgresql://citydb:topsecret@localhost:5432/citydb",
    ],
)
def test_redact_masks_password_forms(text):
    out = credentials.redact(text)
    assert SECRET not in out and "topsecret" not in out
    assert "***" in out


def test_redact_known_secret_anywhere_and_keeps_other_text():
    out = credentials.redact(f"FATAL: something with {SECRET} inside", [SECRET])
    assert out == "FATAL: something with *** inside"
    assert credentials.redact("no secrets here -P 5432 -d db") == "no secrets here -P 5432 -d db"
    # one-character secrets would mangle text; they are left to the patterns
    assert credentials.redact("a b c", ["a"]) == "a b c"


# ---------------------------------------------------------------------------
# Credential store (session + fake keychain)
# ---------------------------------------------------------------------------
class _FakeBackend:
    priority = 5


class _FakeKeyring(types.ModuleType):
    def __init__(self):
        super().__init__("keyring")
        self.store = {}

    def get_keyring(self):
        return _FakeBackend()

    def get_password(self, service, key):
        return self.store.get((service, key))

    def set_password(self, service, key, value):
        self.store[(service, key)] = value

    def delete_password(self, service, key):
        self.store.pop((service, key), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", kr)
    credentials.clear_session()
    yield kr
    credentials.clear_session()


def test_session_store_lookup_forget(fake_keyring):
    key = "u@h:5432"
    assert credentials.lookup(key) == (None, None)
    assert credentials.store(key, SECRET) == credentials.SOURCE_SESSION
    assert credentials.lookup(key) == (SECRET, credentials.SOURCE_SESSION)
    assert fake_keyring.store == {}  # not remembered -> not in the keychain
    credentials.forget(key)
    assert credentials.lookup(key) == (None, None)


def test_remember_puts_password_in_keychain_and_survives_session(fake_keyring):
    key = "u@h:5432"
    assert credentials.store(key, SECRET, remember=True) == credentials.SOURCE_KEYCHAIN
    credentials.clear_session()  # new Blender session
    assert credentials.has_password(key) == credentials.SOURCE_KEYCHAIN
    assert credentials.lookup(key) == (SECRET, credentials.SOURCE_KEYCHAIN)
    credentials.forget(key)
    assert fake_keyring.store == {}


def test_not_remembering_removes_an_old_keychain_entry(fake_keyring):
    key = "u@h:5432"
    credentials.store(key, "old", remember=True)
    credentials.store(key, SECRET, remember=False)
    assert fake_keyring.store == {}
    assert credentials.lookup(key) == (SECRET, credentials.SOURCE_SESSION)


def test_keychain_unavailable_falls_back_to_session(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)  # import fails
    credentials.clear_session()
    usable, reason = credentials.keychain_status()
    assert not usable and reason
    assert credentials.store("k", SECRET, remember=True) == credentials.SOURCE_SESSION
    assert credentials.lookup("k") == (SECRET, credentials.SOURCE_SESSION)
    credentials.clear_session()


def test_null_keyring_backend_counts_as_unavailable(monkeypatch):
    kr = _FakeKeyring()

    class _Fail:
        priority = 0

    kr.get_keyring = lambda: _Fail()
    monkeypatch.setitem(sys.modules, "keyring", kr)
    assert credentials.keychain_status()[0] is False
