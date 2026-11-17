"""xAL 2.0 addresses written for CityGML 2.0 validate against Schema/citygml_2/xAL/xAL.xsd.

Before B04 the 2.0 Building attributes were never written; once they were,
the flat (sibling) xAL output was schema-invalid in the T08 round trip.
Needs lxml (run with Blender's Python + scripts/headless/pydeps); skipped otherwise.
"""
import importlib.util
from pathlib import Path

import pytest

etree = pytest.importorskip("lxml.etree")

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("xal_writer_under_test", ROOT / "shared" / "xal_writer.py")
xal_writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xal_writer)

XAL = "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0"
SCHEMA = etree.XMLSchema(etree.parse(str(ROOT / "Schema" / "citygml_2" / "xAL" / "xAL.xsd")))

CASES = {
    "street address": dict(country="Germany", locality="Hamburg", localityType="Town", street="Pilatuspool",
                           streetNumber="11", streetType="Street"),
    "full": dict(country="Germany", countryCode="DE", administrativeArea="Hamburg", locality="Hamburg",
                 dependentLocality="Neustadt", street="Pilatuspool", streetNumber="11", buildingNumber="11a",
                 buildingName="HCU", unitNumber="3", postCode="20355", postBox="12"),
    "no country": dict(locality="Hamburg", street="Pilatuspool", postCode="20355"),
    "street only": dict(street="Pilatuspool", streetNumber="11"),
    "country + street": dict(country="Germany", street="Pilatuspool", streetNumber="11"),
    "admin + street": dict(country="Germany", administrativeArea="Bayern", street="Hauptstr."),
    "premise without street": dict(country="Germany", locality="Hamburg", buildingName="Elbphilharmonie"),
    "postcode only": dict(country="Germany", postCode="20355"),
}


def write(data):
    root = etree.Element(f"{{{XAL}}}AddressDetails", nsmap={"xAL": XAL})
    xal_writer.write_xal_address(root, data, version="2.0",
                                 Q_func=lambda p, t: f"{{{XAL}}}{t}", SubElement_func=etree.SubElement)
    return root


@pytest.mark.parametrize("name", sorted(CASES))
def test_xal2_address_is_schema_valid_and_keeps_every_field(name):
    data = CASES[name]
    root = write(data)
    assert SCHEMA.validate(root), f"{name}: {SCHEMA.error_log.last_error}"
    text = " ".join(list(root.itertext()) + [v for el in root.iter() for v in el.attrib.values()])
    for value in data.values():
        assert value in text, f"{name}: {value!r} was dropped"


def test_flat_output_would_be_rejected():
    """Negative control: the old sibling layout fails the same schema."""
    root = etree.Element(f"{{{XAL}}}AddressDetails", nsmap={"xAL": XAL})
    country = etree.SubElement(root, f"{{{XAL}}}Country")
    etree.SubElement(country, f"{{{XAL}}}CountryName").text = "Germany"
    loc = etree.SubElement(root, f"{{{XAL}}}Locality")
    etree.SubElement(loc, f"{{{XAL}}}LocalityName").text = "Hamburg"
    assert not SCHEMA.validate(root)
