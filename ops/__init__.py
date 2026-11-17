# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# ops/__init__.py
"""Operations package for CityGML 3 Blender add-on.

Enthält:
- CityDBTool: Wrapper für citydb-tool (3DCityDB v5)
- Validatoren: Wohlgeformt- und XSD-Validierung (CityGML 2.0 + 3.0)
- Auto-Validierung: Automatische Version-Erkennung und Validierung
"""

from .citydb_cli import CityDBTool
from .validate import (
    well_formed_citygml3,
    validate_citygml3_xsd,
)
from .validate_citygml2 import (
    well_formed_citygml2,
    validate_citygml2_xsd,
)
from .validate_auto import (
    detect_version_from_file,
    validate_citygml_auto,
    well_formed_citygml_auto,
)

__all__ = [
    "CityDBTool",
    # CityGML 3.0
    "well_formed_citygml3",
    "validate_citygml3_xsd",
    # CityGML 2.0
    "well_formed_citygml2",
    "validate_citygml2_xsd",
    # Auto (Version-Detection)
    "detect_version_from_file",
    "validate_citygml_auto",
    "well_formed_citygml_auto",
]
