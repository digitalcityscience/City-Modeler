"""T11d (part 4): Transportation (Road, Railway, Track, Square and the parts they own) and CityFurniture
in the shared builder payload. No Blender.

The builder reads each feature's own values (Bonsai style): a Road does not take the class of
its TrafficArea, a TrafficArea is its own object below the Road.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_ir import SpaceNode  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from test_t11b_gml_geometry import FIXTURES  # noqa: E402
from test_t11c_citygml2_to_ir import NS2, SQUARE, need  # noqa: E402
from test_t11d_builder_payload_30 import NS3  # noqa: E402

TRAN2 = 'xmlns:tran="http://www.opengis.net/citygml/transportation/2.0" ' \
        'xmlns:frn="http://www.opengis.net/citygml/cityfurniture/2.0"'
TRAN3 = 'xmlns:tran="http://www.opengis.net/citygml/transportation/3.0" ' \
        'xmlns:frn="http://www.opengis.net/citygml/cityfurniture/3.0"'


def surface(pid, lod="lod2MultiSurface", ns="tran"):
    return (f'<{ns}:{lod}><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="{pid}">'
            f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{SQUARE}'
            f'</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
            f'</gml:MultiSurface></{ns}:{lod}>')


def doc(body, schema="2.0"):
    ns = (NS2 + " " + TRAN2) if schema == "2.0" else (NS3 + " " + TRAN3)
    return ET.fromstring(f"<core:CityModel {ns}><core:cityObjectMember>{body}"
                         f"</core:cityObjectMember></core:CityModel>")


def plan_of(body, schema="2.0", **kwargs):
    roots = read_document(doc(body, schema), upgrade=False)
    return roots, bp.plan_document(roots, (0.0, 0.0, 0.0), schema=schema, **kwargs)


ROAD2 = ('<tran:Road gml:id="R"><tran:class>1</tran:class><tran:function>2</tran:function>'
         '<tran:usage>3</tran:usage>' + surface("RP") +
         '<tran:trafficArea><tran:TrafficArea gml:id="TA"><tran:function>4</tran:function>'
         '<tran:surfaceMaterial>asphalt</tran:surfaceMaterial>' + surface("TAP", "lod2MultiSurface") +
         '</tran:TrafficArea></tran:trafficArea>'
         '<tran:auxiliaryTrafficArea><tran:AuxiliaryTrafficArea gml:id="AA">' + surface("AAP") +
         '</tran:AuxiliaryTrafficArea></tran:auxiliaryTrafficArea></tran:Road>')

ROAD3 = ('<tran:Road gml:id="R"><tran:class>1</tran:class>' + surface("RP", "lod2MultiSurface", "core") +
         '<tran:section><tran:Section gml:id="S"><tran:trafficSpace><tran:TrafficSpace gml:id="TS">'
         '<tran:granularity>lane</tran:granularity><tran:trafficDirection>forwards</tran:trafficDirection>'
         '<tran:trafficArea><tran:TrafficArea gml:id="TA"><tran:surfaceMaterial>asphalt</tran:surfaceMaterial>'
         + surface("TAP", "lod2MultiSurface", "core") +
         '</tran:TrafficArea></tran:trafficArea></tran:TrafficSpace></tran:trafficSpace>'
         '</tran:Section></tran:section></tran:Road>')


def test_supported_types():
    for name in ("Road", "Railway", "Track", "Square", "Section", "Intersection", "TrafficSpace",
                 "AuxiliaryTrafficSpace", "TrafficArea", "AuxiliaryTrafficArea", "CityFurniture"):
        assert bp.unsupported_reasons([SpaceNode("X", name)]) == []


def test_other_transportation_parts_are_not_built():
    for name in ("Marking", "Hole", "ClearanceSpace", "Waterway"):
        assert bp.unsupported_reasons([SpaceNode("X", name)]) == [f"feature type {name}"]


@pytest.mark.parametrize("schema,body", [("2.0", ROAD2), ("3.0", ROAD3)])
def test_road_with_parts_is_supported(schema, body):
    roots, _plan = plan_of(body, schema)
    assert bp.unsupported_reasons(roots, schema) == []


def test_2_0_road_and_its_traffic_areas_are_separate_objects():
    _roots, plan = plan_of(ROAD2)
    assert [(p.gml_id, p.feature_type) for p in plan.features] == [
        ("R", "Road"), ("TA", "TrafficArea"), ("AA", "AuxiliaryTrafficArea")]
    assert all(len(p.geometry.faces) == 1 for p in plan.features)     # no polygon is built twice
    assert plan.edges == [("R", "TA", "part"), ("R", "AA", "part")]


def test_2_0_road_values_are_its_own():
    _roots, plan = plan_of(ROAD2)
    road, area, _aux = plan.features
    assert road.properties["tran:class"] == "1"
    assert road.properties["tran:function"] == "2"
    assert road.properties["tran:usage"] == "3"
    assert area.properties == {"tran:function": "4", "tran:surfaceMaterial": "asphalt"}


def test_3_0_section_space_area_chain():
    _roots, plan = plan_of(ROAD3, "3.0")
    assert [(p.gml_id, p.feature_type) for p in plan.features] == [
        ("R", "Road"), ("S", "Section"), ("TS", "TrafficSpace"), ("TA", "TrafficArea")]
    assert [len(p.geometry.faces) for p in plan.features] == [1, 0, 0, 1]
    assert plan.edges == [("R", "S", "part"), ("S", "TS", "part"), ("TS", "TA", "part")]
    space = plan.features[2]
    assert space.properties == {"tran:granularity": "lane", "tran:trafficDirection": "forwards"}
    assert plan.features[3].properties == {"tran:surfaceMaterial": "asphalt"}


def test_usage_is_written_for_2_0():
    roots, _plan = plan_of(ROAD2)
    assert "tran:usage" in bp.importer_properties(roots[0], schema="2.0")


def test_usage_is_written_for_3_0():
    node = read_document(doc('<tran:Road gml:id="R"><tran:usage>7</tran:usage></tran:Road>', "3.0"),
                         upgrade=False)[0]
    assert bp.importer_properties(node, schema="3.0")["tran:usage"] == "7"


def test_road_without_own_values_does_not_inherit_from_its_area():
    body = ('<tran:Road gml:id="R">' + surface("RP") + '<tran:trafficArea><tran:TrafficArea gml:id="TA">'
            '<tran:function>4</tran:function></tran:TrafficArea></tran:trafficArea></tran:Road>')
    _roots, plan = plan_of(body)
    assert plan.features[0].properties == {}
    assert plan.features[1].properties == {"tran:function": "4"}


@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_city_furniture_properties(schema):
    body = (f'<frn:CityFurniture gml:id="F"><frn:class>1000</frn:class><frn:function>1010</frn:function>'
            f'<frn:usage>1020</frn:usage>{surface("FP", "lod3MultiSurface", "frn")}'
            '</frn:CityFurniture>')
    roots, plan = plan_of(body, schema)
    assert bp.unsupported_reasons(roots, schema) == []
    furniture = plan.features[0]
    assert furniture.feature_type == "CityFurniture"
    assert furniture.properties == {"frn:class": "1000", "frn:function": "1010", "frn:usage": "1020"}
    assert len(furniture.geometry.faces) == 1
    assert furniture.geometry.surf_labels[0][0] == "CityFurniture"      # no Roof/Wall/Ground inference


def implicit(fid, pid="IP", matrix="", point="", lod="lod3", extra=""):
    return (f'<frn:CityFurniture gml:id="{fid}">{extra}<frn:{lod}ImplicitRepresentation><core:ImplicitGeometry>'
            + (f'<core:transformationMatrix>{matrix}</core:transformationMatrix>' if matrix else "")
            + f'<core:relativeGMLGeometry><gml:MultiSurface gml:id="T_{pid}"><gml:surfaceMember>'
            f'<gml:Polygon gml:id="{pid}"><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{SQUARE}'
            '</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>'
            '</core:relativeGMLGeometry>'
            + (f'<core:referencePoint><gml:Point><gml:pos>{point}</gml:pos></gml:Point></core:referencePoint>'
               if point else "")
            + f'</core:ImplicitGeometry></frn:{lod}ImplicitRepresentation></frn:CityFurniture>')


def test_implicit_only_furniture_is_supported():
    roots, plan = plan_of(implicit("F"))
    assert bp.unsupported_reasons(roots) == []
    assert len(plan.features[0].geometry.faces) == 1


def test_implicit_next_to_explicit_geometry_is_left_to_the_old_importer():
    body = implicit("F", extra=surface("EXPLICIT", "lod2MultiSurface", "frn"))
    roots, _plan = plan_of(body)
    assert bp.unsupported_reasons(roots) == ["ImplicitGeometry next to other geometry"]


def test_implicit_placement_is_matrix_plus_reference_point_minus_origin():
    roots = read_document(doc(implicit("F", matrix="0 -1 0 0 1 0 0 0 0 0 2 0 0 0 0 1", point="100 200 3")),
                          upgrade=False)
    plan = bp.plan_document(roots, (90.0, 190.0, 1.0), schema="2.0")
    placement = plan.features[0].implicit
    assert placement.matrix == [[0.0, -1.0, 0.0, 10.0], [1.0, 0.0, 0.0, 10.0],
                                [0.0, 0.0, 2.0, 2.0], [0.0, 0.0, 0.0, 1.0]]
    # the exported matrix carries no translation; the template mesh keeps its own coordinates
    assert placement.matrix_text.split()[3::4] == ["0.0", "0.0", "0.0", "1.0"]
    assert plan.features[0].geometry.verts[1] == (1.0, 0.0, 0.0)
    assert placement.template_id == "T_IP"


def test_implicit_instances_with_the_same_polygons_share_a_fingerprint():
    a = bp.plan_document(read_document(doc(implicit("A", "P1")), upgrade=False), (0.0, 0.0, 0.0)).features[0]
    b = bp.plan_document(read_document(doc(implicit("B", "P2", point="5 5 5")), upgrade=False),
                         (0.0, 0.0, 0.0)).features[0]
    assert a.implicit.fingerprint == b.implicit.fingerprint
    assert a.implicit.template_id != b.implicit.template_id


def test_implicit_lod_filter_picks_the_highest_selected_lod():
    body = ('<frn:CityFurniture gml:id="F">' + implicit("X", "P3", lod="lod3")[len('<frn:CityFurniture gml:id="X">'):-len('</frn:CityFurniture>')]
            + implicit("X", "P2", lod="lod2")[len('<frn:CityFurniture gml:id="X">'):-len('</frn:CityFurniture>')]
            + '</frn:CityFurniture>')
    roots = read_document(doc(body), upgrade=False)
    node = roots[0]
    assert bp.implicit_geometry(node)[0] == "lod3ImplicitRepresentation"
    assert bp.implicit_geometry(node, {2})[0] == "lod2ImplicitRepresentation"
    assert bp.implicit_geometry(node, {1}) is None
    plan = bp.plan_document(roots, (0.0, 0.0, 0.0), filters=bp.ImportFilters(lods={1}))
    assert plan.features[0].geometry.faces == []


def test_curve_only_road_network_is_left_to_the_old_importer():
    body = ('<tran:Road gml:id="R"><tran:lod0Network><gml:GeometricComplex><gml:element><gml:LineString>'
            '<gml:posList>0 0 0 1 1 1</gml:posList></gml:LineString></gml:element></gml:GeometricComplex>'
            '</tran:lod0Network></tran:Road>')
    roots, _plan = plan_of(body)
    assert bp.unsupported_reasons(roots) == ["feature with curve or point geometry only"]


def test_type_filter_keeps_the_parts_of_a_road():
    class Options:
        use_feature_type_filter = True
        import_transportation = True

    for schema in ("2.0", "3.0"):
        types = bp.feature_type_set(Options(), schema)
        assert {"Road", "TrafficArea"} <= types
    assert "Section" in bp.feature_type_set(Options(), "3.0")
    body = ROAD2
    _roots, plan = plan_of(body, filters=bp.ImportFilters(feature_types=bp.feature_type_set(Options(), "2.0")))
    assert [p.gml_id for p in plan.features] == ["R", "TA", "AA"]


@pytest.mark.parametrize("parent,child,schema,kind", [
    ("Road", "TrafficArea", "2.0", "part"),
    ("Road", "Section", "3.0", "part"),
    ("Section", "TrafficSpace", "3.0", "part"),
    ("Building", "TrafficArea", "3.0", ""),
])
def test_transport_edge_kinds(parent, child, schema, kind):
    assert bp.edge_kind(SpaceNode("P", parent), SpaceNode("C", child), schema) == kind


FIXTURE_SCENES = {
    ("Railway", "Railway_citygml2.gml", "2.0"),
    ("Railway", "Railway_citygml3.gml", "3.0"),
    ("CityFurniture", "Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml", "2.0"),
    ("CityFurniture", "Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml", "3.0"),
}


@pytest.mark.parametrize("folder,name,schema", sorted(FIXTURE_SCENES))
def test_fixture_is_built_as_one_feature(folder, name, schema):
    path = FIXTURES / folder / name
    need(path)
    roots = read_document(ET.parse(path).getroot(), upgrade=False)
    assert bp.unsupported_reasons(roots, schema) == []
    plan = bp.plan_document(roots, (0.0, 0.0, 0.0), schema=schema)
    assert len(plan.features) == 1
    feature = plan.features[0]
    assert feature.feature_type == ("Railway" if folder == "Railway" else "CityFurniture")
    assert len(feature.geometry.faces) > 0
    assert len(feature.geometry.faces) == len(feature.geometry.surf_labels)
