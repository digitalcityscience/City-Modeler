"""T11d d3-A: one CityGML version rule behind every entry point.

Every detector (landing sniff, import routing, validation, namespace
manager) must give the same answer for the same document.
"""
import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
GML31 = "http://www.opengis.net/gml"
GML32 = "http://www.opengis.net/gml/3.2"
XSI = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def _load(rel):
    package, name = rel[:-3].split("/")
    for pkg in ("t11d_vd", f"t11d_vd.{package}"):
        if pkg not in sys.modules:
            stub = types.ModuleType(pkg)
            stub.__path__ = [str(ROOT / pkg.split(".")[-1])] if "." in pkg else [str(ROOT)]
            sys.modules[pkg] = stub
    full = f"t11d_vd.{package}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


for _name in ("bpy", "bpy.types", "bpy.props"):
    sys.modules.setdefault(_name, mock.MagicMock())

sniff = _load("shared/citygml_sniff.py")
version_detection = _load("common/version_detection.py")
validate_auto = _load("ops/validate_auto.py")


def _model(core=None, gml=GML32, schema="", member=""):
    tag = "core:CityModel" if core else "CityModel"
    attrs = f'xmlns:gml="{gml}"'
    if core:
        attrs += f' xmlns:core="{core}"'
    if schema:
        attrs += f' {XSI} xsi:schemaLocation="{schema}"'
    return f'<?xml version="1.0" encoding="UTF-8"?><{tag} {attrs}>{member}</{tag}>'


def _child(ns):
    return f'<cityObjectMember><b:Building xmlns:b="{ns}"/></cityObjectMember>'


C = "http://www.opengis.net/citygml/"
CASES = {
    "v1_core": (_model(C + "1.0", GML31), "1.0"),
    "v2_core": (_model(C + "2.0", GML31), "2.0"),
    "v3_core": (_model(C + "3.0", GML32), "3.0"),
    "v3_ns_only_on_child": (_model(None, GML32, member=_child(C + "building/3.0")), "3.0"),
    "v2_ns_only_on_child": (_model(None, GML31, member=_child(C + "building/2.0")), "2.0"),
    "v2_schema_location_only": (_model(None, "http://x", schema=C + "2.0 x.xsd"), "2.0"),
    "v3_schema_location_only": (_model(None, "http://x", schema=C + "3.0 x.xsd"), "3.0"),
    "unrelated_xml": ('<?xml version="1.0"?><foo xmlns="http://example.test"/>', "unknown"),
    "v3_with_bom": ("﻿" + _model(C + "3.0", GML32), "3.0"),
    "ns_not_on_root_v3": ('<?xml version="1.0"?><CityModel><c:m xmlns:c="%s3.0"/></CityModel>' % C, "3.0"),
    "ns_not_on_root_v2": ('<?xml version="1.0"?><CityModel><c:m xmlns:c="%s2.0"/></CityModel>' % C, "2.0"),
    "mixed_prefers_newest": (_model(C + "2.0", GML31, member=_child(C + "building/3.0")), "3.0"),
}
# Files only the header detectors can classify (a parser refuses them).
UNPARSEABLE = {
    "broken_xml_v3": ('<?xml version="1.0"?><core:CityModel xmlns:core="%s3.0"><oops>' % C, "3.0"),
    "empty_file": ("", "unknown"),
}


def _write(tmp_path, name, text):
    path = tmp_path / f"{name}.gml"
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def _roots(path):
    import xml.etree.ElementTree as stdlib
    yield stdlib.parse(path).getroot()
    try:
        from lxml import etree
    except ImportError:
        return
    yield etree.parse(path).getroot()


def _file_detectors():
    return {
        "sniff": lambda p: sniff.sniff_file(p).version,
        "version_from_file": sniff.version_from_file,
        "validate_auto": lambda p: validate_auto.detect_version_from_file(p)[0],
    }


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_entry_point_agrees(tmp_path, name):
    text, expected = CASES[name]
    path = _write(tmp_path, name, text)
    for label, detect in _file_detectors().items():
        assert detect(path) == expected, label
    for root in _roots(path):
        assert version_detection.detect_citygml_version(root) == expected
        assert sniff.version_from_root(root) == expected


@pytest.mark.parametrize("name", sorted(UNPARSEABLE))
def test_header_detectors_survive_unparseable_files(tmp_path, name):
    text, expected = UNPARSEABLE[name]
    path = _write(tmp_path, name, text)
    for label, detect in _file_detectors().items():
        assert detect(path) == expected, label


def test_missing_file_is_unknown_for_validation(tmp_path):
    assert validate_auto.detect_version_from_file(str(tmp_path / "nope.gml"))[0] == "unknown"


def test_gml_namespace_fallback_needs_a_citymodel_root(tmp_path):
    cityMODEL = _write(tmp_path, "gml32", _model(None, GML32))
    other = _write(tmp_path, "other", '<?xml version="1.0"?><x xmlns:gml="%s"/>' % GML32)
    assert sniff.version_from_file(cityMODEL) == "3.0"
    assert sniff.version_from_file(other) == "unknown"
    assert sniff.classify_version(gml_namespace=GML31) == "2.0"
