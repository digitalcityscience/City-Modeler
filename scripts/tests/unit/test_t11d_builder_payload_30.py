"""T11d (part 1): the CityGML 3.0 importer's flavor of the payload (``schema="3.0"``)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_ir import GeometryNode, SpaceNode  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from shared.semantic_tree import PolygonNode, RingNode  # noqa: E402
from test_t11b_gml_geometry import FIXTURES, SAMPLES  # noqa: E402
from test_t11c_citygml2_to_ir import need  # noqa: E402
from test_t11d_builder_payload import BUILDING, props_of, scene_totals, surface  # noqa: E402

GML32 = "http://www.opengis.net/gml/3.2"
NS3 = (f'xmlns:gml="{GML32}" xmlns:xlink="http://www.w3.org/1999/xlink" '
       'xmlns:core="http://www.opengis.net/citygml/3.0" '
       'xmlns:bldg="http://www.opengis.net/citygml/building/3.0" '
       'xmlns:con="http://www.opengis.net/citygml/construction/3.0" '
       'xmlns:gen="http://www.opengis.net/citygml/generics/3.0" '
       'xmlns:xAL="urn:oasis:names:tc:ciq:xal:3"')

# file -> (mesh vertices, mesh faces, objects) of the 3.0 importer's scene
# (t11a_import_snapshot.py, 2026-10-24, feature flag off)
SCENES_30 = {
    SAMPLES / "buildings_lod2_citygml_3_0.gml": (237, 39, 42),
    SAMPLES / "buildings_lod3_citygml_3_0.gml": (1395, 225, 39),
    FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml": (10, 2, 4),
    BUILDING / "SemanticHierarchy_Installations_LoD3_CityGML3.gml": (30, 6, 7),
    FIXTURES / "Bridge" / "Bridge_citygml3.gml": (150, 30, 1),
}


def plan30(path):
    need(path)
    roots = read_document(ET.parse(path).getroot(), upgrade=False)
    assert bp.unsupported_reasons(roots, "3.0") == []
    return bp.plan_document(roots, (0.0, 0.0, 0.0), schema="3.0")


def doc3(body):
    return ET.fromstring(f"<core:CityModel {NS3}><core:cityObjectMember>{body}"
                         "</core:cityObjectMember></core:CityModel>")


def props3(body):
    node = read_document(doc3(body), upgrade=False)[0]
    return node, bp.importer_properties(node, schema="3.0")


@pytest.mark.parametrize("path", sorted(SCENES_30, key=str), ids=lambda p: p.name)
def test_payload_totals_match_the_3_0_importer_scene(path):
    # the 3.0 importer keeps the closing point and welds nothing: 5 vertices for a quad
    assert scene_totals(plan30(path)) == SCENES_30[path]


def test_every_point_is_kept_and_polygons_without_a_surface_are_labelled_with_the_feature():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0))
    inner = ((0.2, 0.2, 0), (0.4, 0.2, 0), (0.4, 0.4, 0), (0.2, 0.2, 0))
    poly = PolygonNode("P", RingNode("RO", "P", "exterior", ring),
                       (RingNode("RI", "P", "interior", inner),))
    node = SpaceNode("B", "Building", geometries={
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(poly,))})
    g = bp.feature_geometry(node, (0.0, 0.0, 0.0), "EPSG:25832", schema="3.0")
    assert len(g.verts) == 9 and g.faces == [[0, 1, 2, 3, 4], [5, 6, 7, 8]]
    assert g.surf_labels == [
        ("Building", "P", "RO", "EPSG:25832", (0, 1, 2, 3, 4), 2, False, False),
        ("Building", "P", "RI", "EPSG:25832", (5, 6, 7, 8), 2, False, True)]


def test_installation_has_no_surface_of_its_own():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0))
    inst = SpaceNode("I", "BuildingInstallation", geometries={"lod3MultiSurface": GeometryNode(
        gml_type="MultiSurface", polygons=(PolygonNode("PI", RingNode("RI", "PI", "exterior", ring)),))})
    g = bp.feature_geometry(inst, (0.0, 0.0, 0.0), schema="3.0")
    assert g.surf_labels[0][0] == "BuildingInstallation"
    assert g.surface_id_by_poly == {} and g.multisurface_id_by_poly == {}
    payload = bp.feature_payload(
        SpaceNode("I", "BuildingInstallation", source_role="buildingInstallation"),
        (0.0, 0.0, 0.0), schema="3.0")
    assert (payload.source_role, payload.source_element) == ("outer", "bldg:buildingInstallation")


def test_part_keeps_its_own_geometry_unless_a_surface_repeats_it():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0))
    other = ((5, 5, 0), (6, 5, 0), (6, 6, 0))

    def poly(pid, r):
        return PolygonNode(pid, RingNode(pid + "r", pid, "exterior", r))
    wall = surface("WALL", "WallSurface", "PWALL", ring=ring)
    geometry = {"lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(
        poly("DUP", ring), poly("OWN", other)))}
    part = SpaceNode("PART", "BuildingPart", boundaries=(wall,), geometries=geometry)
    ids = [label[1] for label in bp.feature_geometry(part, (0.0, 0.0, 0.0), schema="3.0").surf_labels]
    assert ids == ["PWALL", "OWN"]
    building = SpaceNode("B", "Building", boundaries=(wall,), geometries=geometry)
    ids = [label[1] for label in bp.feature_geometry(building, (0.0, 0.0, 0.0), schema="3.0").surf_labels]
    assert ids == ["PWALL"]


def test_solid_with_inline_polygons_is_built_for_3_0():
    solid = ('<bldg:Building gml:id="B"><bldg:lod2Solid><gml:Solid><gml:exterior><gml:Shell>'
             '<gml:surfaceMember><gml:Polygon gml:id="P"><gml:exterior><gml:LinearRing>'
             '<gml:posList>0 0 0 1 0 0 1 1 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior>'
             '</gml:Polygon></gml:surfaceMember></gml:Shell></gml:exterior></gml:Solid>'
             '</bldg:lod2Solid></bldg:Building>')
    roots = read_document(doc3(solid), upgrade=False)
    assert bp.unsupported_reasons(roots, "3.0") == []
    assert bp.unsupported_reasons(roots, "2.0") == []


def test_generic_attribute_wrapper_height_and_dates():
    node, props = props3(
        '<bldg:Building gml:id="B">'
        '<core:genericAttribute><gen:StringAttribute><gen:name>Quelle</gen:name>'
        '<gen:value>amtlich</gen:value></gen:StringAttribute></core:genericAttribute>'
        '<core:genericAttribute><gen:IntAttribute><gen:name>N</gen:name>'
        '<gen:value>3</gen:value></gen:IntAttribute></core:genericAttribute>'
        '<con:dateOfConstruction>1999-05-01</con:dateOfConstruction>'
        '<con:height><con:Height><con:highReference>highestRoofEdge</con:highReference>'
        '<con:lowReference>lowestGroundPoint</con:lowReference><con:status>measured</con:status>'
        '<con:value uom="urn:adv:uom:m">12.5</con:value></con:Height></con:height>'
        '<bldg:function>1000</bldg:function></bldg:Building>')
    assert props == {
        "bldg:function": "1000", "con:dateOfConstruction": "1999-05-01", "con:height": "",
        "con:height:highReference": "highestRoofEdge", "con:height:lowReference": "lowestGroundPoint",
        "con:height:status": "measured", "con:height:value": 12.5, "con:height:uom": "urn:adv:uom:m",
        "Quelle": "amtlich", "N": 3}
    payload = bp.feature_payload(node, (0.0, 0.0, 0.0), schema="3.0")
    assert payload.generic_attributes == {"Quelle": "amtlich", "N": 3}
    assert bp.feature_payload(node, (0.0, 0.0, 0.0), schema="2.0").generic_attributes == {}


def test_empty_height_value_is_written_as_empty_text():
    _, props = props3('<bldg:Building gml:id="B"><con:height><con:Height><con:value uom="m"/>'
                      '</con:Height></con:height></bldg:Building>')
    assert props == {"con:height": "", "con:height:value": "", "con:height:uom": "m"}


XAL3 = ('<core:address><core:Address><core:xalAddress><xAL:Address><xAL:Country><xAL:NameElement>Germany'
        '</xAL:NameElement></xAL:Country><xAL:Locality xAL:Type="Town"><xAL:NameElement>Hamburg'
        '</xAL:NameElement></xAL:Locality><xAL:Thoroughfare xAL:Type="Street"><xAL:NameElement>'
        'Pilatuspool</xAL:NameElement><xAL:Number>11</xAL:Number></xAL:Thoroughfare></xAL:Address>'
        '</core:xalAddress><core:multiPoint><gml:MultiPoint><gml:pointMember><gml:Point>'
        '<gml:pos>10 20 30</gml:pos></gml:Point></gml:pointMember></gml:MultiPoint></core:multiPoint>'
        '</core:Address></core:address>')


def test_address_goes_through_the_xal_parser():
    _, props = props3(f'<bldg:Building gml:id="B">{XAL3}</bldg:Building>')
    assert props["bldg:address:country"] == "Germany"
    assert props["bldg:address:locality"] == "Hamburg"
    assert props["bldg:address:street"] == "Pilatuspool" and props["bldg:address:streetNumber"] == "11"
    assert (props["bldg:address:point:x"], props["bldg:address:point:y"],
            props["bldg:address:point:z"]) == (10.0, 20.0, 30.0)


def test_2_0_address_details_are_read():
    body = ('<bldg:Building gml:id="B"><bldg:address><core:Address><core:xalAddress>'
            '<xAL:AddressDetails xmlns:xAL="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0"><xAL:Country>'
            '<xAL:CountryName>Germany</xAL:CountryName></xAL:Country></xAL:AddressDetails>'
            '</core:xalAddress></core:Address></bldg:address></bldg:Building>')
    assert props_of(body) == {"bldg:address:country": "Germany"}
