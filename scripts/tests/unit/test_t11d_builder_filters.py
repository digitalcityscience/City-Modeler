"""T11d (part 2): import filters in the shared builder payload, no Blender.

The semantics are the old importers' (``iter_features`` / ``build_geometry``):
a feature without its own gml:id passes the id filter, one without a readable
envelope passes the bbox filter, an empty id / type set filters nothing, and
a geometry under ``lodN...`` is dropped when N is not selected.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from test_t11c_citygml2_to_ir import NS2, SQUARE  # noqa: E402

GML = "http://www.opengis.net/gml"
NS2_ALL = NS2 + ' xmlns:tun="http://www.opengis.net/citygml/tunnel/2.0"'


def surface(sid, lod, z=0):
    coords = " ".join(str(float(v) + (z if i % 3 == 2 else 0)) for i, v in enumerate(SQUARE.split()))
    return (f'<bldg:lod{lod}MultiSurface><gml:MultiSurface><gml:surfaceMember>'
            f'<gml:Polygon gml:id="{sid}"><gml:exterior><gml:LinearRing>'
            f'<gml:posList>{coords}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>'
            f'</gml:surfaceMember></gml:MultiSurface></bldg:lod{lod}MultiSurface>')


def envelope(x0, y0, x1, y1):
    return (f'<gml:boundedBy><gml:Envelope srsName="EPSG:25832"><gml:lowerCorner>{x0} {y0} 0'
            f'</gml:lowerCorner><gml:upperCorner>{x1} {y1} 9</gml:upperCorner></gml:Envelope>'
            '</gml:boundedBy>')


def document(*members):
    body = "".join(f"<core:cityObjectMember>{m}</core:cityObjectMember>" for m in members)
    return ET.fromstring(f"<core:CityModel {NS2_ALL}>{body}</core:CityModel>")


BUILDING = ('<bldg:Building gml:id="B1">' + envelope(0, 0, 10, 10)
            + surface("B1_p1", 1) + surface("B1_p2", 2, z=1)
            + '<bldg:consistsOfBuildingPart><bldg:BuildingPart gml:id="B1_part">'
            + envelope(0, 0, 2, 2) + surface("B1_part_p", 2, z=2)
            + '</bldg:BuildingPart></bldg:consistsOfBuildingPart></bldg:Building>')
FAR = '<bldg:Building gml:id="B2">' + envelope(100, 100, 110, 110) + surface("B2_p", 2) + '</bldg:Building>'
NO_ID = '<bldg:Building>' + envelope(200, 200, 210, 210) + surface("N_p", 2) + '</bldg:Building>'
NO_ENVELOPE = '<bldg:Building gml:id="B3">' + surface("B3_p", 2) + '</bldg:Building>'


def plan(*members, filters=None, envelopes=True):
    roots = read_document(document(*members), upgrade=False, envelopes=envelopes)
    return bp.plan_document(roots, (0.0, 0.0, 0.0), filters=filters), roots


def ids(p):
    return sorted(f.gml_id for f in p.features if not f.generated_id)


def test_no_filter_keeps_everything_and_counts_the_objects():
    p, _ = plan(BUILDING, FAR)
    assert ids(p) == ["B1", "B1_part", "B2"] and p.total == 3
    assert not bp.ImportFilters().active


def test_gml_id_filter_keeps_listed_ids_and_features_without_an_id():
    p, _ = plan(BUILDING, FAR, NO_ID, filters=bp.ImportFilters(gml_ids={"B2"}))
    assert ids(p) == ["B2"]
    assert sum(1 for f in p.features if f.generated_id) == 1   # no gml:id: passes
    assert p.total == 4
    # an empty set is no filter
    p, _ = plan(BUILDING, FAR, filters=bp.ImportFilters(gml_ids=set()))
    assert ids(p) == ["B1", "B1_part", "B2"]


def test_filtered_parent_drops_the_edge_but_keeps_the_part():
    p, _ = plan(BUILDING, filters=bp.ImportFilters(gml_ids={"B1_part"}))
    assert ids(p) == ["B1_part"] and p.edges == []
    p, _ = plan(BUILDING, filters=bp.ImportFilters(gml_ids={"B1", "B1_part"}))
    assert p.edges == [("B1", "B1_part", "part")]


def test_feature_type_filter():
    p, _ = plan(BUILDING, filters=bp.ImportFilters(feature_types={"BuildingPart"}))
    assert ids(p) == ["B1_part"]
    p, _ = plan(BUILDING, filters=bp.ImportFilters(feature_types={"Bridge"}))
    assert p.features == [] and p.total == 2


def test_bbox_filter_uses_the_first_envelope_below_the_feature():
    box = (5.0, 5.0, 20.0, 20.0)
    p, _ = plan(BUILDING, FAR, filters=bp.ImportFilters(bbox=box))
    assert ids(p) == ["B1"]                      # the part's own envelope (0..2) is outside
    p, _ = plan(NO_ID, NO_ENVELOPE, filters=bp.ImportFilters(bbox=box))
    assert ids(p) == ["B3"] and len(p.features) == 1   # no envelope passes; NO_ID is outside


def test_bbox_filter_needs_the_envelopes_read():
    box = (5.0, 5.0, 20.0, 20.0)
    p, _ = plan(FAR, filters=bp.ImportFilters(bbox=box), envelopes=False)
    assert ids(p) == ["B2"]          # without envelopes in the IR nothing can be excluded


def test_envelope_with_a_broken_corner_passes():
    broken = BUILDING.replace("0 0 0</gml:lowerCorner>", "x y 0</gml:lowerCorner>", 1)
    p, _ = plan(broken, filters=bp.ImportFilters(bbox=(500.0, 500.0, 600.0, 600.0)))
    assert ids(p) == ["B1"]          # B1 passes (unreadable), the part's envelope is outside


def test_lod_filter_drops_the_polygons_of_other_lods():
    p, _ = plan(BUILDING)
    base = {f.gml_id: [l[1] for l in f.geometry.surf_labels] for f in p.features}
    assert base["B1"] == ["B1_p1", "B1_p2"]
    p, _ = plan(BUILDING, filters=bp.ImportFilters(lods={2}))
    assert {f.gml_id: len(f.geometry.faces) for f in p.features} == {"B1": 1, "B1_part": 1}
    p, _ = plan(BUILDING, filters=bp.ImportFilters(lods={0}))
    assert all(not f.geometry.faces for f in p.features)
    p, _ = plan(BUILDING, filters=bp.ImportFilters(lods=set()))
    assert all(not f.geometry.faces for f in p.features)


def test_lod_filter_keeps_no_id_of_a_dropped_multisurface():
    named = surface("B1_p1", 1).replace("<gml:MultiSurface>", '<gml:MultiSurface gml:id="MS1">')
    body = '<bldg:Building gml:id="B9">' + named + surface("B9_p2", 2, z=1) + "</bldg:Building>"
    p, _ = plan(body)
    assert p.features[0].geometry.multisurface_id_by_poly == {"B1_p1": "MS1"}
    p, _ = plan(body, filters=bp.ImportFilters(lods={2}))
    assert p.features[0].geometry.multisurface_id_by_poly == {}


def test_unsupported_features_that_the_filters_leave_out_do_not_block():
    tunnel = '<tun:Tunnel gml:id="T1"><gml:name>t</gml:name></tun:Tunnel>'
    _, roots = plan(BUILDING, tunnel)
    assert bp.unsupported_reasons(roots) != []
    assert bp.unsupported_reasons(roots, filters=bp.ImportFilters(feature_types={"Building"})) == []
    assert bp.unsupported_reasons(roots, filters=bp.ImportFilters(gml_ids={"B1"})) == []
    assert bp.unsupported_reasons(roots, filters=bp.ImportFilters(gml_ids={"T1"})) != []


def test_option_readers():
    off = SimpleNamespace(use_feature_type_filter=False, use_lod_filter=False)
    assert bp.feature_type_set(off, "2.0") is None and bp.lod_set(off) is None
    on = SimpleNamespace(use_feature_type_filter=True, import_buildings=True, import_bridges=False,
                         use_lod_filter=True, lod_0=False, lod_1=True, lod_2=True, lod_3=False, lod_4=False)
    assert bp.feature_type_set(on, "2.0") == {"Building", "BuildingPart", "BuildingInstallation",
                                              "IntBuildingInstallation"}
    assert "BuildingInstallation" in bp.feature_type_set(on, "3.0")
    assert bp.lod_set(on) == {1, 2}
    none = SimpleNamespace(use_feature_type_filter=True, use_lod_filter=True)
    assert bp.feature_type_set(none, "3.0") == set() and bp.lod_set(none) == set()
    assert not bp.ImportFilters(feature_types=set()).active
    assert bp.ImportFilters(lods=set()).active


def test_2_0_address_details_are_read():
    from shared.citygml_ir import Address, CityObjectAttrs, SpaceNode
    xal = ('<xAL:AddressDetails xmlns:xAL="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0"><xAL:Country/>'
           '</xAL:AddressDetails>')
    attrs = CityObjectAttrs(addresses=(Address(street="Hauptstrasse", house_number="5", locality="Hamburg",
                                               postal_code="20095", country="DE", xal=xal),))
    node = SpaceNode("B", "Building", attrs=attrs)
    props = bp.importer_properties(node, schema="2.0")
    assert props["bldg:address:street"] == "Hauptstrasse" and props["bldg:address:postCode"] == "20095"
    assert props["bldg:address:streetNumber"] == "5"
