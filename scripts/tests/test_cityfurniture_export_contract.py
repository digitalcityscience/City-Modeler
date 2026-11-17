"""
Regression checks for CityFurniture export semantics.

These checks are intentionally runnable with plain Python. The full export
roundtrip still needs Blender, but this catches the XML contract that KIT
Viewer depends on: furniture geometry must be under frn:lodXGeometry and must
not be flattened into building WallSurface elements.

Usage:
  python scripts/tests/test_cityfurniture_export_contract.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CITYFURNITURE_FIXTURES = (
    REPO_ROOT / "test_files/CityFurniture/Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml",
    REPO_ROOT / "test_files/CityFurniture/Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml",
)
TRANSPORTATION_FIXTURES = (
    REPO_ROOT / "test_files/Railway/Railway_citygml2.gml",
    REPO_ROOT / "test_files/Railway/Railway_citygml3.gml",
)
CITYGML2_EXPORTER = REPO_ROOT / "io_v2/writer/exporter.py"


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _elements(root: ET.Element, local_name: str) -> list[ET.Element]:
    return [elem for elem in root.iter() if _local_name(str(elem.tag)) == local_name]


def _assert_fixture_contract(path: pathlib.Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    city_furniture = _elements(root, "CityFurniture")
    assert city_furniture, f"{path} contains no CityFurniture feature"

    furniture_geometry = []
    for feature in city_furniture:
        furniture_geometry.extend(
            child
            for child in list(feature)
            if re.fullmatch(r"lod[0-4](Geometry|MultiSurface)", _local_name(str(child.tag)))
        )
    assert furniture_geometry, f"{path} contains CityFurniture without lod geometry"

    wall_surfaces = [
        elem for elem in root.iter()
        if _local_name(str(elem.tag)) == "WallSurface"
        and _namespace(str(elem.tag)) in {
            "http://www.opengis.net/citygml/building/2.0",
            "http://www.opengis.net/citygml/building/3.0",
            "http://www.opengis.net/citygml/construction/3.0",
        }
    ]
    assert not wall_surfaces, f"{path} contains building/construction WallSurface elements"


def _assert_transportation_fixture_contract(path: pathlib.Path) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    transportation_features = [
        elem
        for elem in root.iter()
        if _local_name(str(elem.tag)) in {"Road", "Railway", "Track", "Square"}
        and "citygml/transportation" in _namespace(str(elem.tag))
    ]
    assert transportation_features, f"{path} contains no Transportation feature"

    transportation_geometry = []
    for feature in transportation_features:
        transportation_geometry.extend(
            child
            for child in list(feature)
            if re.fullmatch(r"lod[0-4](Geometry|MultiSurface)", _local_name(str(child.tag)))
        )
    assert transportation_geometry, f"{path} contains Transportation without lod geometry"

    wall_surfaces = [
        elem for elem in root.iter()
        if _local_name(str(elem.tag)) == "WallSurface"
        and _namespace(str(elem.tag)) in {
            "http://www.opengis.net/citygml/building/2.0",
            "http://www.opengis.net/citygml/building/3.0",
            "http://www.opengis.net/citygml/construction/3.0",
        }
    ]
    assert not wall_surfaces, f"{path} contains building/construction WallSurface elements"


def _assert_citygml2_writer_does_not_force_furniture_implicit_geometry() -> None:
    source = CITYGML2_EXPORTER.read_text(encoding="utf-8")
    branch = source.split('if (ns_key, local_name) == ("frn", "CityFurniture"):', 1)[1]
    branch = branch.split('elif (ns_key, local_name) == ("veg", "SolitaryVegetationObject"):', 1)[0]
    assert 'f"lod{lod_level}Geometry"' in branch
    assert 'f"lod{lod_level}ImplicitRepresentation"' not in branch


def main() -> int:
    for fixture in CITYFURNITURE_FIXTURES:
        _assert_fixture_contract(fixture)
    for fixture in TRANSPORTATION_FIXTURES:
        _assert_transportation_fixture_contract(fixture)
    _assert_citygml2_writer_does_not_force_furniture_implicit_geometry()
    print("[TEST] OK - CityFurniture and Transportation export contracts are preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
