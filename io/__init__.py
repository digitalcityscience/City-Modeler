# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# io/__init__.py
"""I/O package for CityGML Blender add-on.

Enthält:
- CityGML 3.0: export_blender_to_citygml3 (import: common/unified_builder)
- Common: Version detection utilities
"""

from ..common.version_detection import detect_citygml_version, get_version_info


def export_blender_to_citygml3(*args, **kwargs):
    from .gml3_writer import export_blender_to_citygml3 as _export_blender_to_citygml3
    return _export_blender_to_citygml3(*args, **kwargs)

__all__ = [
    # CityGML 3.0
    "export_blender_to_citygml3",
    # Common
    "detect_citygml_version",
    "get_version_info",
]

