# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Common Utilities for CityGML Import/Export
===========================================

Gemeinsame Funktionen die von CityGML 2.0 und 3.0 verwendet werden.

Funktionen:
- Version-Detection
- Namespace-Handling
- Coordinate-Transformation Helpers
"""

from typing import Any, Literal

from ..shared.citygml_sniff import version_from_root

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree


def _get_namespace_map(root: Any) -> dict:
    """Return namespace URI map for lxml and stdlib ElementTree elements."""
    nsmap = getattr(root, "nsmap", None)
    if nsmap is not None:
        return dict(nsmap)

    namespaces = {}
    for elem in root.iter():
        tag = getattr(elem, "tag", "")
        if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
            uri = tag[1:].split("}", 1)[0]
            namespaces.setdefault(f"ns{len(namespaces)}", uri)
        for attr in getattr(elem, "attrib", {}):
            if isinstance(attr, str) and attr.startswith("{") and "}" in attr:
                uri = attr[1:].split("}", 1)[0]
                namespaces.setdefault(f"ns{len(namespaces)}", uri)
    return namespaces


def detect_citygml_version(root: Any) -> Literal['1.0', '2.0', '3.0', 'unknown']:
    """CityGML version of a parsed root (the rule lives in shared.citygml_sniff)."""
    return version_from_root(root)


def get_version_info(root: Any) -> dict:
    """
    Sammelt detaillierte Version-Informationen aus CityGML-Datei.
    
    Args:
        root: XML Root-Element
    
    Returns:
        Dictionary mit Version-Informationen:
            - version: '1.0', '2.0', '3.0' oder 'unknown'
            - gml_version: GML Version
            - namespaces: Dict aller Namespaces
            - schema_locations: schemaLocation string
            - detected_modules: Liste erkannter CityGML-Module
    """
    version = detect_citygml_version(root)
    nsmap = _get_namespace_map(root)
    
    # Detect GML Version
    gml_version = 'unknown'
    if 'http://www.opengis.net/gml/3.2' in nsmap.values():
        gml_version = '3.2.1'
    elif 'http://www.opengis.net/gml' in nsmap.values():
        gml_version = '3.1.1'
    
    # Detect CityGML Modules
    modules = []
    module_patterns_3_0 = {
        'building': 'http://www.opengis.net/citygml/building/3.0',
        'bridge': 'http://www.opengis.net/citygml/bridge/3.0',
        'tunnel': 'http://www.opengis.net/citygml/tunnel/3.0',
        'transportation': 'http://www.opengis.net/citygml/transportation/3.0',
        'cityfurniture': 'http://www.opengis.net/citygml/cityfurniture/3.0',
        'vegetation': 'http://www.opengis.net/citygml/vegetation/3.0',
        'waterbody': 'http://www.opengis.net/citygml/waterbody/3.0',
        'landuse': 'http://www.opengis.net/citygml/landuse/3.0',
        'relief': 'http://www.opengis.net/citygml/relief/3.0',
        'generics': 'http://www.opengis.net/citygml/generics/3.0',
        'appearance': 'http://www.opengis.net/citygml/appearance/3.0',
        'pointcloud': 'http://www.opengis.net/citygml/pointcloud/3.0',
        'construction': 'http://www.opengis.net/citygml/construction/3.0',
        'cityobjectgroup': 'http://www.opengis.net/citygml/cityobjectgroup/3.0',
        'dynamizer': 'http://www.opengis.net/citygml/dynamizer/3.0',
        'versioning': 'http://www.opengis.net/citygml/versioning/3.0',
    }
    
    module_patterns_2_0 = {
        'building': 'http://www.opengis.net/citygml/building/2.0',
        'bridge': 'http://www.opengis.net/citygml/bridge/2.0',
        'tunnel': 'http://www.opengis.net/citygml/tunnel/2.0',
        'transportation': 'http://www.opengis.net/citygml/transportation/2.0',
        'cityfurniture': 'http://www.opengis.net/citygml/cityfurniture/2.0',
        'vegetation': 'http://www.opengis.net/citygml/vegetation/2.0',
        'waterbody': 'http://www.opengis.net/citygml/waterbody/2.0',
        'landuse': 'http://www.opengis.net/citygml/landuse/2.0',
        'relief': 'http://www.opengis.net/citygml/relief/2.0',
        'generics': 'http://www.opengis.net/citygml/generics/2.0',
        'appearance': 'http://www.opengis.net/citygml/appearance/2.0',
        'cityobjectgroup': 'http://www.opengis.net/citygml/cityobjectgroup/2.0',
        'texturedsurface': 'http://www.opengis.net/citygml/texturedsurface/2.0',
    }
    
    patterns = module_patterns_3_0 if version == '3.0' else module_patterns_2_0
    
    for module_name, namespace_uri in patterns.items():
        if namespace_uri in nsmap.values():
            modules.append(module_name)
    
    return {
        'version': version,
        'gml_version': gml_version,
        'namespaces': dict(nsmap),
        'schema_locations': root.get('{http://www.w3.org/2001/XMLSchema-instance}schemaLocation', ''),
        'detected_modules': modules,
    }


def print_version_info(root: Any) -> None:
    """
    Gibt Version-Informationen auf Konsole aus (für Debugging).
    
    Args:
        root: XML Root-Element
    """
    info = get_version_info(root)
    
    print("=" * 60)
    print("CityGML File Information")
    print("=" * 60)
    print(f"CityGML Version: {info['version']}")
    print(f"GML Version: {info['gml_version']}")
    print(f"Detected Modules: {', '.join(info['detected_modules']) if info['detected_modules'] else 'None'}")
    print(f"\nNamespaces:")
    for prefix, uri in info['namespaces'].items():
        print(f"  {prefix or '(default)'}: {uri}")
    print("=" * 60)
