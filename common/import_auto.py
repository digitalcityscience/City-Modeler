# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# common/import_auto.py
"""
CityGML import with version detection.

Detects CityGML 2.0 or 3.0 and builds the scene with the shared builder
(``common/unified_builder.py``), the only importer.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    import bpy

# detect_version_from_file -> builder schema (a 1.0 file is read like 2.0)
_SCHEMA_BY_VERSION = {'2.0': '2.0', '3.0': '3.0', '1.0': '2.0'}


def import_citygml_auto(
    filepath: str,
    context: 'bpy.types.Context',
    *,
    import_appearance: bool = True,
    data: bytes | None = None,
) -> List[str]:
    """
    Import a CityGML file, detecting its version.

    ``data`` is the content of ``filepath`` when the caller has just written it (a Browse
    fragment): the version and the XML are then read from it and the file is not opened
    again (the first ``open()`` of a fresh file costs several ms on Windows).

    Returns notices for the user (large file, skipped content). Raises
    ``unified_builder.ImportTooLarge`` above the Load-all limit and
    ``unified_builder.ImportNotSupported`` when the file is not CityGML 2.0/3.0
    or holds nothing the add-on imports; the scene is untouched then.
    """
    from ..ops.validate_auto import detect_version_from_file
    from ..shared.citygml_sniff import HEADER_BYTES, version_from_bytes
    from ..shared.load_limits import ImportNotSupported
    from .unified_builder import import_with_builder

    if data is None:
        version, msg = detect_version_from_file(filepath)
    else:
        version = version_from_bytes(data[:HEADER_BYTES])
        msg = (f"CityGML {version} erkannt" if version != 'unknown'
               else "CityGML-Version konnte nicht erkannt werden")
    schema = _SCHEMA_BY_VERSION.get(version)
    if schema is None:
        raise ImportNotSupported(f"Not a CityGML 2.0 or 3.0 file ({msg})")
    print(f"[CityGML Import] {msg} - shared builder, schema {schema}")
    return import_with_builder(filepath, context, schema=schema, import_appearance=import_appearance, data=data)
