"""Hand the 3DCityDB password to the add-on without putting it on a command line.

The headless DB scripts read the password from an environment variable
(default ``CITYDB_PASSWORD``, the same name citydb-tool uses) and store it in
the add-on's session credential cache for ``user@host:port``. Without it,
libpq / citydb-tool may still find one in ``~/.pgpass``.
"""

import os
import sys

DEFAULT_PASSWORD_ENV = "CITYDB_PASSWORD"


def _addon_credentials_module():
    candidates = [
        module for name, module in sys.modules.items()
        if name.endswith(".citydb.credentials") and module is not None
    ]
    if not candidates:
        return None
    # Prefer the add-on that registered the operators (extension or legacy name).
    candidates.sort(key=lambda m: 0 if m.__name__.startswith("bl_ext.") else 1)
    return candidates[0]


def register_password(*, host: str, port, user: str, env_name: str = DEFAULT_PASSWORD_ENV, log_prefix: str = "") -> bool:
    """Store the password from ``env_name`` for this server; True when one was found."""
    password = os.environ.get(env_name or DEFAULT_PASSWORD_ENV, "")
    if not password:
        print(f"{log_prefix} No password in ${env_name}; relying on ~/.pgpass or trust authentication")
        return False
    credentials = _addon_credentials_module()
    if credentials is None:
        print(f"{log_prefix} ERROR: add-on credential store not loaded")
        return False
    key = f"{user}@{host}:{int(str(port).strip() or 5432)}"
    credentials.store(key, password, remember=False)
    print(f"{log_prefix} Password taken from ${env_name} for {key}")
    return True
