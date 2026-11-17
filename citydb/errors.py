# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/errors.py
"""Connection and discovery errors with a short, actionable message (no bpy)."""

from __future__ import annotations

import re
from typing import Optional

# kind -> what the user can do about it
PASSWORD_REQUIRED = "password_required"
AUTH = "auth"
UNREACHABLE = "unreachable"
UNKNOWN_HOST = "unknown_host"
TIMEOUT = "timeout"
NO_DATABASE = "no_database"
NO_SCHEMA = "no_schema"
PERMISSION = "permission"
UNSUPPORTED = "unsupported"
SSL = "ssl"
DRIVER_MISSING = "driver_missing"
OTHER = "other"


class CityDBError(Exception):
    """An error the UI can show as one line; ``detail`` holds the redacted raw text."""

    def __init__(self, kind: str, message: str, detail: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail

    @property
    def needs_password(self) -> bool:
        return self.kind in (PASSWORD_REQUIRED, AUTH)


def classify(sqlstate: Optional[str], message: str) -> str:
    """Error kind from a PostgreSQL SQLSTATE and/or the libpq message."""
    state = (sqlstate or "").upper()
    text = (message or "").lower()

    if state == "28P01" or "password authentication failed" in text:
        return AUTH
    if "no password supplied" in text or "password is required" in text:
        return PASSWORD_REQUIRED
    if state == "28000":
        # pg_hba.conf rejects the host/user/database combination, or a role is missing.
        return PERMISSION if "pg_hba" in text or "no pg_hba" in text else AUTH
    if state == "3D000" or ("database" in text and "does not exist" in text):
        return NO_DATABASE
    if state == "3F000" or ("schema" in text and "does not exist" in text):
        return NO_SCHEMA
    if state == "42501" or "permission denied" in text:
        return PERMISSION
    if "could not translate host name" in text or "name or service not known" in text \
            or "nodename nor servname" in text or "no such host" in text:
        return UNKNOWN_HOST
    if "timeout expired" in text or "timed out" in text:
        return TIMEOUT
    if "ssl" in text and ("unsupported" in text or "required" in text or "certificate" in text
                          or "does not support ssl" in text):
        return SSL
    if "connection refused" in text or "could not connect" in text or "no route to host" in text \
            or ("connection to server" in text and "failed" in text):
        return UNREACHABLE
    return OTHER


def unsupported_version_message(version: str) -> str:
    """The one message for a 3DCityDB that is not 5.x; older versions are rejected, not converted."""
    return f"Unsupported database: 3DCityDB {version}. This add-on requires 3DCityDB 5.x."


def citydb_tool_unsupported_message(log_text: str) -> Optional[str]:
    """The same message when citydb-tool rejected the database version, else None.

    Only reached when the psycopg check could not run before citydb-tool started.
    """
    text = log_text or ""
    low = text.lower()
    if "not supported" not in low or not (
            "databaseversionexception" in low or "supported versions are 5." in low):
        return None
    match = re.search(r"version\s+v?(\d+(?:\.\d+)+)", text, re.IGNORECASE)
    return unsupported_version_message(match.group(1) if match else "(not 5.x)")


def user_message(kind: str, *, host: str = "", port: int = 0, user: str = "",
                 database: str = "", schema: str = "") -> str:
    """English one-liner for the UI."""
    server = f"{host}:{port}" if host else "the server"
    if kind == PASSWORD_REQUIRED:
        return f"Password required for {user or 'this user'} on {server}"
    if kind == AUTH:
        return f"Wrong user name or password for {user or 'this user'} on {server}"
    if kind == UNREACHABLE:
        return f"Cannot reach {server} - is PostgreSQL running and the port open?"
    if kind == UNKNOWN_HOST:
        return f"Unknown host '{host}'"
    if kind == TIMEOUT:
        return f"No answer from {server} (timeout)"
    if kind == NO_DATABASE:
        return f"Database '{database}' does not exist on {server}"
    if kind == NO_SCHEMA:
        return f"Schema '{schema}' in database '{database}' is not a 3DCityDB schema"
    if kind == PERMISSION:
        return f"Access denied for {user or 'this user'} to '{database or server}'"
    if kind == SSL:
        return f"SSL setting does not match the server {server}"
    if kind == DRIVER_MISSING:
        return "PostgreSQL driver (psycopg) is not available in this Blender"
    if kind == UNSUPPORTED:
        return f"'{database}/{schema}' is not a 3DCityDB v5 schema"
    return f"Database error on {server}"
