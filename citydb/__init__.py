# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/__init__.py
"""3DCityDB v5 access.

Two channels, each used for what it does best:

- ``client`` talks to PostgreSQL directly through psycopg 3: connection test,
  database/schema discovery ("which city lives where"), version and CRS
  checks, later the catalog/index queries. Read-only, parameterised SQL,
  milliseconds instead of a JVM start per question.
- ``ops/citydb_cli.CityDBTool`` runs citydb-tool, the official v5 client, for
  the CityGML <-> database conversion only. Credentials reach it through
  ``CITYDB_*`` environment variables of that one child process, never on the
  command line.

Passwords live in ``credentials`` (session memory, optionally the OS
keychain) and are never written to .blend files, presets or logs.
The modules here, except ``client``, do not import bpy or psycopg.
"""
