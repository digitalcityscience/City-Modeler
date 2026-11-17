"""T11d cleanup (ticket "Sonuc - bolum 5"): small guarantees of the shared builder payload. No Blender."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from test_t11c_citygml2_to_ir import NS2, SQUARE  # noqa: E402
from test_t11d_builder_payload_30 import NS3  # noqa: E402
from test_t11d_builder_transport import doc, implicit, plan_of, surface  # noqa: E402



def building(body, schema="2.0"):
    ns = NS2 if schema == "2.0" else NS3
    return ET.fromstring(
        f'<core:CityModel {ns}><core:cityObjectMember><bldg:Building gml:id="B">{body}'
        f'{surface("BP", "lod2MultiSurface", "bldg")}</bldg:Building></core:cityObjectMember></core:CityModel>')


def props_of(root, schema="2.0"):
    plan = bp.plan_document(read_document(root, upgrade=False), (0.0, 0.0, 0.0), schema=schema)
    return plan.features[0].properties


# -- F5: a broken number loses that field only ---------------------------------------------------------

@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_broken_integer_skips_only_its_own_field(schema):
    props = props_of(building("<bldg:storeysAboveGround>x</bldg:storeysAboveGround>"
                              "<bldg:storeysBelowGround>2</bldg:storeysBelowGround>"
                              "<bldg:yearOfConstruction>1999</bldg:yearOfConstruction>"
                              "<bldg:class>1000</bldg:class>", schema), schema)
    assert "bldg:storeysAboveGround" not in props
    assert props["bldg:storeysBelowGround"] == 2
    assert props["bldg:yearOfConstruction"] == 1999
    assert props["bldg:class"] == "1000"


@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_broken_generic_number_is_kept_as_text(schema):
    if schema == "2.0":
        body = '<gen:doubleAttribute name="h"><gen:value>abc</gen:value></gen:doubleAttribute>' \
               '<gen:intAttribute name="n"><gen:value>7</gen:value></gen:intAttribute>'
    else:
        body = '<core:genericAttribute><gen:DoubleAttribute><gen:name>h</gen:name><gen:value>abc</gen:value>' \
               '</gen:DoubleAttribute></core:genericAttribute>' \
               '<core:genericAttribute><gen:IntAttribute><gen:name>n</gen:name><gen:value>7</gen:value>' \
               '</gen:IntAttribute></core:genericAttribute>'
    props = props_of(building(body, schema), schema)
    assert props["h"] == "abc"
    assert props["n"] == 7


# -- F6: a measure attribute keeps its number, not its spelling -----------------------------------------

def test_measure_attribute_keeps_the_number_not_the_text():
    body = '<gen:measureAttribute name="m"><gen:value uom="m">12.50</gen:value></gen:measureAttribute>'
    value = props_of(building(body))["m"]
    assert float(value) == 12.5         # the old importer keeps the text "12.50"


# -- #13: two reasons, not one ------------------------------------------------------------------------

def test_implicit_next_to_explicit_geometry_reason():
    roots, _plan = plan_of(implicit("F", extra=surface("EXPLICIT", "lod2MultiSurface", "frn")))
    assert bp.unsupported_reasons(roots) == ["ImplicitGeometry next to other geometry"]


def test_implicit_without_polygons_reason():
    body = ('<frn:CityFurniture gml:id="F"><frn:lod3ImplicitRepresentation><core:ImplicitGeometry>'
            '<core:transformationMatrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</core:transformationMatrix>'
            '<core:referencePoint><gml:Point><gml:pos>1 2 3</gml:pos></gml:Point></core:referencePoint>'
            '</core:ImplicitGeometry></frn:lod3ImplicitRepresentation></frn:CityFurniture>')
    roots, _plan = plan_of(body)
    assert "ImplicitGeometry without polygons" in bp.unsupported_reasons(roots)


# -- edge cases ---------------------------------------------------------------------------------------

def test_implicit_template_hole_is_kept():
    hole = "1 1 0 1 2 0 2 2 0 2 1 0 1 1 0"
    body = implicit("F").replace("</gml:exterior>", f"</gml:exterior><gml:interior><gml:LinearRing>"
                                 f'<gml:posList srsDimension="3">{hole}</gml:posList></gml:LinearRing></gml:interior>')
    roots, plan = plan_of(body)
    assert bp.unsupported_reasons(roots) == []
    geometry = plan.features[0].geometry
    # as the old importer: the hole ring becomes a face of its own (the headless cleanup test compares scenes)
    assert len(geometry.faces) == 2


def test_implicit_template_outside_the_selected_lod_builds_nothing():
    roots = read_document(doc(implicit("F")), upgrade=False)
    filters = bp.ImportFilters(lods={2})
    plan = bp.plan_document(roots, (0.0, 0.0, 0.0), schema="2.0", filters=filters)
    assert len(plan.features) == 1
    assert plan.features[0].implicit is None and not plan.features[0].geometry.faces


def test_traffic_area_without_gml_id_gets_a_generated_name():
    body = ('<tran:Road gml:id="R">' + surface("RP") + '<tran:trafficArea><tran:TrafficArea>'
            + surface("TAP") + '</tran:TrafficArea></tran:trafficArea></tran:Road>')
    _roots, plan = plan_of(body)
    names = [p.gml_id for p in plan.features]
    assert names[0] == "R" and names[1].startswith("feat_") and len(set(names)) == 2
    assert plan.edges == []                        # no id in the file: no parent edge (as the old importers)


def test_road_with_only_a_network_curve_is_left_to_the_old_importer():
    body = ('<tran:Road gml:id="R"><tran:lod0Network><gml:GeometricComplex><gml:element><gml:LineString>'
            '<gml:posList srsDimension="3">0 0 0 5 5 0</gml:posList></gml:LineString></gml:element>'
            '</gml:GeometricComplex></tran:lod0Network></tran:Road>')
    roots, _plan = plan_of(body)
    assert bp.unsupported_reasons(roots) == ["feature with curve or point geometry only"]


def test_type_flag_tables_name_the_same_flags_for_both_schemas():
    # the tables differ on purpose (3.0 has Intersection, Section, TrafficSpace ...); the flags must not
    assert [flag for flag, _ in bp._TYPE_FLAGS["2.0"]] == [flag for flag, _ in bp._TYPE_FLAGS["3.0"]]


# -- user notice for what the builder will not import ---------------------------------------------------

def test_scope_notice_names_the_reasons_and_3dcitydb():
    text = bp.scope_notice(["feature type Tunnel", "feature type Room"])
    assert "Tunnel" in text and "Room" in text and "3DCityDB" in text
    assert bp.scope_notice([]) == ""
