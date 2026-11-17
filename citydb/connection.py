# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/connection.py
"""Connection model: a PostgreSQL server and a 3DCityDB schema on it (no bpy)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

DEFAULT_PORT = 5432
DEFAULT_SCHEMA = "citydb"
DEFAULT_MAINTENANCE_DB = "postgres"
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
CONNECT_TIMEOUT_S = 8
APPLICATION_NAME = "Blender CityGML Importer/Exporter"

# Variables citydb-tool reads its connection from (citydb-tool >= 1.0).
CITYDB_ENV_NAMES = (
    "CITYDB_HOST",
    "CITYDB_PORT",
    "CITYDB_NAME",
    "CITYDB_SCHEMA",
    "CITYDB_USERNAME",
    "CITYDB_PASSWORD",
)


def parse_port(value) -> int:
    """Port number from text or int; raises ValueError for anything else."""
    text = str(value).strip()
    if not text:
        return DEFAULT_PORT
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValueError(f"Port out of range: {port}")
    return port


@dataclass(frozen=True)
class ServerProfile:
    """A PostgreSQL server as the user knows it. Holds no password."""

    host: str
    port: int = DEFAULT_PORT
    user: str = ""
    sslmode: str = "prefer"
    maintenance_db: str = DEFAULT_MAINTENANCE_DB

    @property
    def key(self) -> str:
        """Identity used for stored credentials: ``user@host:port``."""
        return f"{self.user}@{self.host}:{self.port}"

    def libpq_params(
        self,
        dbname: str,
        password: Optional[str] = None,
        connect_timeout: int = CONNECT_TIMEOUT_S,
    ) -> Dict[str, object]:
        """Keyword arguments for ``psycopg.connect``.

        Without a password libpq still consults ``PGPASSWORD`` and ``~/.pgpass``.
        """
        params: Dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": dbname,
            "sslmode": self.sslmode or "prefer",
            "connect_timeout": connect_timeout,
            "application_name": APPLICATION_NAME,
        }
        if self.user:
            params["user"] = self.user
        if password:
            params["password"] = password
        return params


@dataclass(frozen=True)
class CityDBTarget:
    """One 3DCityDB schema ("city model") in one database on one server."""

    server: ServerProfile
    database: str
    schema: str = DEFAULT_SCHEMA

    @property
    def label(self) -> str:
        return f"{self.database}/{self.schema} @ {self.server.host}:{self.server.port}"

    def citydb_tool_env(self, password: Optional[str]) -> Dict[str, str]:
        """Environment for one citydb-tool process; the only place the password goes."""
        env = {
            "CITYDB_HOST": self.server.host,
            "CITYDB_PORT": str(self.server.port),
            "CITYDB_NAME": self.database,
            "CITYDB_SCHEMA": self.schema or DEFAULT_SCHEMA,
            "CITYDB_USERNAME": self.server.user,
        }
        if password:
            env["CITYDB_PASSWORD"] = password
        return env

    def citydb_tool_args(self) -> List[str]:
        """Extra citydb-tool options that carry no secret (JDBC SSL mode)."""
        mode = (self.server.sslmode or "prefer").strip()
        if mode and mode != "prefer":
            return ["--db-property", f"sslmode={mode}"]
        return []
