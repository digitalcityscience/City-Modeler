# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/credentials.py
"""Where 3DCityDB passwords live: session memory and, on request, the OS keychain.

Never in a .blend file, a preset, a custom property, a log or a command line.
Lookup order: this session -> OS keychain. When neither has one, libpq still
tries ``~/.pgpass`` / ``PGPASSFILE`` on its own (see ``citydb.client``).
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Optional, Tuple

KEYRING_SERVICE = "CityGML Importer/Exporter - 3DCityDB"

SOURCE_SESSION = "session"
SOURCE_KEYCHAIN = "keychain"

_session: Dict[str, str] = {}

# Backends that store nothing; keyring falls back to them when no OS store exists.
_NULL_BACKENDS = ("fail", "null", "chainer")


def _keyring():
    try:
        import keyring  # bundled wheel; absent in plain test environments
    except Exception:
        return None
    return keyring


def keychain_status() -> Tuple[bool, str]:
    """(usable, backend name) of the OS keychain."""
    kr = _keyring()
    if kr is None:
        return False, "keyring not installed"
    try:
        backend = kr.get_keyring()
    except Exception as exc:
        return False, f"keyring error: {exc.__class__.__name__}"
    name = backend.__class__.__name__
    module = backend.__class__.__module__.lower()
    if any(part in module for part in _NULL_BACKENDS) or getattr(backend, "priority", 1) <= 0:
        return False, "no system keychain"
    return True, name


def _keychain_get(key: str) -> Optional[str]:
    usable, _ = keychain_status()
    if not usable:
        return None
    try:
        return _keyring().get_password(KEYRING_SERVICE, key)
    except Exception:
        return None


def _keychain_set(key: str, password: str) -> bool:
    usable, _ = keychain_status()
    if not usable:
        return False
    try:
        _keyring().set_password(KEYRING_SERVICE, key, password)
        return True
    except Exception:
        return False


def _keychain_delete(key: str) -> bool:
    usable, _ = keychain_status()
    if not usable:
        return False
    try:
        _keyring().delete_password(KEYRING_SERVICE, key)
        return True
    except Exception:
        return False


def lookup(key: str, *, use_keychain: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """(password, source) for a server key; (None, None) when nothing is stored."""
    pw = _session.get(key)
    if pw:
        return pw, SOURCE_SESSION
    if use_keychain:
        pw = _keychain_get(key)
        if pw:
            _session[key] = pw
            return pw, SOURCE_KEYCHAIN
    return None, None


def store(key: str, password: str, *, remember: bool = False) -> str:
    """Keep the password for this session; with ``remember`` also in the keychain.

    Returns the strongest place it was stored in.
    """
    if not password:
        forget(key)
        return ""
    _session[key] = password
    if remember and _keychain_set(key, password):
        return SOURCE_KEYCHAIN
    if not remember:
        _keychain_delete(key)
    return SOURCE_SESSION


def forget(key: str) -> None:
    _session.pop(key, None)
    _keychain_delete(key)


def has_password(key: str) -> Optional[str]:
    """Source of a stored password without touching the keychain twice."""
    if key in _session:
        return SOURCE_SESSION
    return SOURCE_KEYCHAIN if _keychain_get(key) else None


def clear_session() -> None:
    _session.clear()


_MIN_VERBATIM_SECRET = 3

_REDACT_PATTERNS = (
    # -p secret / --db-password=secret / --db-password secret
    re.compile(r"(?P<k>(?<!\S)(?:-p|--db-password|--password))(?P<sep>=|\s+)(?P<v>\S+)"),
    # CITYDB_PASSWORD=secret, PGPASSWORD=secret (env dumps)
    re.compile(r"(?P<k>\b(?:CITYDB_PASSWORD|PGPASSWORD))(?P<sep>\s*=\s*)(?P<v>\S+)"),
    # libpq conninfo / URLs: password=secret, password='se cret'
    re.compile(r"(?P<k>\bpassword)(?P<sep>\s*=\s*)(?P<v>'[^']*'|\S+)", re.IGNORECASE),
    # postgresql://user:secret@host
    re.compile(r"(?P<k>postgres(?:ql)?://[^:/@\s]+)(?P<sep>:)(?P<v>[^@\s]+)(?=@)"),
)


def redact(text: str, secrets: Iterable[Optional[str]] = ()) -> str:
    """Replace passwords in log or error text with ``***``.

    Known secrets are replaced verbatim first, then common ``key=value`` and
    option forms are masked even when the secret itself is not known.
    """
    if not text:
        return text
    out = str(text)
    # Very short secrets would mask ordinary characters; the patterns below still cover them.
    for secret in list(secrets) + list(_session.values()):
        if secret and len(secret) >= _MIN_VERBATIM_SECRET:
            out = out.replace(secret, "***")
    for pattern in _REDACT_PATTERNS:
        out = pattern.sub(lambda m: f"{m.group('k')}{m.group('sep')}***", out)
    return out
