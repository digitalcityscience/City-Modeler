"""T11c: CityGML 1.0/2.0 reader -> SpaceNode IR and the 2.0 -> 3.0 upgrade
(shared/citygml_reader.py, shared/citygml_upgrade.py). 2.0 and 3.0 copies of
the same buildings are read with their own schema_version;
polygon counts match the importer's face counts; xlinks, unknown elements,
validation and the dict round-trip. No Blender."""
import os
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import citygml_upgrade as up  # noqa: E402
from shared.browse import fragment  # noqa: E402
from shared.citygml_ir import (GeometryNode, SpaceNode, ThematicSurfaceNode,  # noqa: E402
                               ir_from_dict, ir_to_dict, iter_features, validate)
from shared.citygml_reader import read_document, read_feature  # noqa: E402
from test_t11b_gml_geometry import FIXTURES, IMPORTER_FACES, SAMPLES  # noqa: E402

HAMBURG = (Path(os.environ.get("CITY_MODELER_PERF_DATA") or Path(__file__).resolve().parents[4] / "perf-data")
           / "real" / "hh2026" / "tiles" / "LoD2_32_564_5934_1_HH.gml")
FULL_APPEARANCE = FIXTURES / "Building" / "SemanticHierarchy_Appearance_FullAppearance_LoD3_CityGML2.gml"
CS1_LOD3 = FIXTURES / "Building" / "CitySmith" / "CS1_lod3.gml"
CS1_LOD0 = FIXTURES / "Building" / "CitySmith" / "CS1_lod0.gml"
FURNITURE = FIXTURES / "CityFurniture" / "Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml"
# Importer faces (t11a_import_snapshot.py, 2026-10-24); the rest come from T11b.
# Measured headless on 2026-10-24 (t11a_import_snapshot.py, mesh_polygons), T11c part 2.
BUILDING = FIXTURES / "Building"
CS = BUILDING / "CitySmith"
FACES = {**IMPORTER_FACES, FULL_APPEARANCE: 8, HAMBURG: 9965,
         BUILDING / "Railway_Scene_LoD3_CityGML2_Building_only.gml": 227,
         BUILDING / "SemanticHierarchy_Appearance_LoD3_CityGML2.gml": 3,
         BUILDING / "SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML2.gml": 11,
         BUILDING / "SemanticHierarchy_Appearance_PartialAppearance_LoD3_CityGML2.gml": 10,
         BUILDING / "SemanticHierarchy_Appearance_TextureFallback_LoD3_CityGML2.gml": 8,
         BUILDING / "SemanticHierarchy_Appearance_UnknownSubtype_LoD3_CityGML2.gml": 9,
         BUILDING / "SemanticHierarchy_Appearance_InstallationSubtype_LoD3_CityGML2.gml": 10,
         CS / "CS1_lod3.gml": 111, CS / "CS1_lod3_enhanced.gml": 111,
         CS / "CS2_lod3.gml": 188, CS / "CS2_lod3_enhanced.gml": 188}
# multiLOD files hold lod0/lod1 beside lod2/lod3; the importer skips lod0/lod1 there.
MULTI_LOD_FACES = {CS / "CS1_multiLOD.gml": 168, CS / "CS2_multiLOD.gml": 209}

PAIRS = [
    (SAMPLES / "buildings_lod2_citygml_2_0.gml", SAMPLES / "buildings_lod2_citygml_3_0.gml"),
    (SAMPLES / "buildings_lod3_citygml_2_0.gml", SAMPLES / "buildings_lod3_citygml_3_0.gml"),
    (FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML2.gml",
     FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML3.gml"),
    (FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml",
     FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml"),
]
ALL_FILES = sorted(set(FACES) | {p for pair in PAIRS for p in pair}
                   | {CS1_LOD3, CS1_LOD0, FURNITURE, HAMBURG}, key=str)

GML = "http://www.opengis.net/gml"
NS2 = (f'xmlns:gml="{GML}" xmlns:xlink="http://www.w3.org/1999/xlink" '
       'xmlns:core="http://www.opengis.net/citygml/2.0" '
       'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
       'xmlns:gen="http://www.opengis.net/citygml/generics/2.0"')
SQUARE = "0 0 0 1 0 0 1 1 0 0 1 0 0 0 0"


def need(path):
    if not path.exists():
        pytest.skip(f"{path} not available")


def read(path, warnings=None, report=None):
    need(path)
    return read_document(ET.parse(path).getroot(), warnings, report)


def doc2(body):
    return ET.fromstring(f"<core:CityModel {NS2}><core:cityObjectMember>{body}"
                         "</core:cityObjectMember></core:CityModel>")


def ms(pid, lod="lod2MultiSurface", ns="bldg"):
    return (f'<{ns}:{lod}><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="{pid}">'
            f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{SQUARE}'
            f'</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
            f'</gml:MultiSurface></{ns}:{lod}>')


def member2(body):
    return doc2(body)[0]


def faces(roots):
    return sum(1 + len(p.interiors) for r in roots for n in iter_features(r)
               for g in n.geometries.values() if "resolved_href" not in g.extra
               for p in g.polygons)


def _norm(gml_id):
    return re.sub(r"_V[23](?=_|$)", "", gml_id)


def projection(roots):
    """Everything the 2.0 and 3.0 copies must share."""
    out = []
    for root in roots:
        for n in iter_features(root):
            kind = n.surface_type if isinstance(n, ThematicSurfaceNode) else n.feature_type
            geoms = {k: sorted((_norm(p.polygon_id), len(p.interiors)) for p in g.polygons)
                     for k, g in n.geometries.items()}
            out.append((type(n).__name__, _norm(n.gml_id), kind, _norm(n.parent_id),
                        getattr(n, "interior", False), json.dumps(geoms, sort_keys=True)))
    return sorted(out)


# -- 2.0 and 3.0 give the same IR ------------------------------------------

@pytest.mark.parametrize("v2,v3", PAIRS, ids=lambda p: p.name)
def test_schema_version_follows_the_file(v2, v3):
    w2, w3 = [], []
    a, b = read(v2, w2), read(v3, w3)
    assert w2 == w3 == []
    assert {n.schema_version for r in a for n in iter_features(r)} == {"2.0"}
    assert {n.schema_version for r in b for n in iter_features(r)} == {"3.0"}
    assert all(not n.lod_origin for r in b for n in iter_features(r))


def test_lod_origin_records_the_renamed_2_0_keys():
    roots = read(SAMPLES / "buildings_lod3_citygml_2_0.gml")
    assert {r.lod_origin for r in roots} == {"lod3TerrainIntersection"}


def test_lod_n_geometry_is_not_mapped():
    # No verified 2.0 -> 3.0 rule (T11c part 2): the key stays, only lod4 -> lod3.
    inst = read(FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML2.gml")[0]
    assert {s.lod_origin for s in inst.spaces} == {""}
    assert {k for s in inst.spaces for k in s.geometries} == {"lod3Geometry"}
    report = []
    read(FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML2.gml", report=report)
    assert report and all(r["from"] == r["to"] == "lod3Geometry" and "no verified 3.0" in r["note"]
                          for r in report)


# -- counts ----------------------------------------------------------------

@pytest.mark.parametrize("path", list(FACES), ids=lambda p: p.name)
def test_polygons_plus_interiors_match_importer_faces(path):
    warnings = []
    assert faces(read(path, warnings)) == FACES[path] and warnings == []


def test_hamburg_citygml_1_0_tile():
    need(HAMBURG)
    tree = ET.parse(HAMBURG).getroot()
    count = lambda local: sum(1 for e in tree.iter() if isinstance(e.tag, str)  # noqa: E731
                              and e.tag.endswith("/1.0}" + local))   # not gml:boundedBy
    warnings = []
    roots = read_document(tree, warnings)
    nodes = [n for r in roots for n in iter_features(r)]
    assert len(roots) == count("Building") == 460
    assert sum(isinstance(n, ThematicSurfaceNode) for n in nodes) == count("boundedBy") == 9945
    assert {n.schema_version for n in nodes} == {"1.0"}
    assert warnings == [] and [p for r in roots for p in validate(r)] == []
    assert sum(len(n.attrs.addresses) for n in nodes) == count("Address")
    assert sum(len(n.attrs.generic_attributes) for n in nodes) == count("stringAttribute")


def test_curve_members_counted_on_a_real_file():
    need(CS1_LOD3)
    roots = read(CS1_LOD3)
    tree = ET.parse(CS1_LOD3).getroot()
    members = sum(1 for e in tree.iter() if e.tag == f"{{{GML}}}curveMember")
    curves = [c for r in roots for n in iter_features(r)
              for k, g in n.geometries.items() if k.endswith("TerrainIntersectionCurve")
              for c in g.curves]
    assert members == 2 and len(curves) == members
    assert [len(c) for c in curves] == [29, 11]


def test_furniture_geometry_key_is_kept():
    root = read(FURNITURE)[0]
    assert root.feature_type == "CityFurniture" and list(root.geometries) == ["lod3Geometry"]
    assert root.lod_origin == ""


def test_implicit_geometry():
    # No fixture with ImplicitGeometry exists yet (see the ticket); inline 2.0.
    root = read_feature(member2(
        '<bldg:Building gml:id="B"><bldg:boundedBy><bldg:WallSurface gml:id="W">'
        f'{ms("WP", "lod3MultiSurface")}<bldg:opening><bldg:Window gml:id="WIN">'
        '<bldg:lod3ImplicitRepresentation><core:ImplicitGeometry gml:id="IG">'
        '<core:mimeType>model/obj</core:mimeType>'
        '<core:transformationMatrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</core:transformationMatrix>'
        '<core:relativeGMLGeometry><gml:MultiSurface gml:id="REL"><gml:surfaceMember>'
        f'<gml:Polygon gml:id="RP"><gml:exterior><gml:LinearRing><gml:posList>{SQUARE}'
        '</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
        '</gml:MultiSurface></core:relativeGMLGeometry>'
        '<core:referencePoint><gml:Point><gml:pos>10 20 30</gml:pos></gml:Point></core:referencePoint>'
        '</core:ImplicitGeometry></bldg:lod3ImplicitRepresentation></bldg:Window></bldg:opening>'
        '</bldg:WallSurface></bldg:boundedBy></bldg:Building>')[0])
    window = root.boundaries[0].openings[0]
    g = window.geometries["lod3ImplicitRepresentation"]
    assert window.host_id == "W" and window.feature_type == "Window"
    assert g.gml_type == "ImplicitGeometry" and g.points == ((10.0, 20.0, 30.0),)
    assert [p.polygon_id for p in g.polygons] == ["RP"]
    assert g.extra["transformation_matrix"][0] == 1.0 and g.extra["mime_type"] == "model/obj"
    assert g.extra["relative_gml_type"] == "MultiSurface" and validate(root) == []


# -- 2.0 -> 3.0 upgrade ----------------------------------------------------

def test_int_installation_room_and_lod4_are_upgraded():
    report = []
    root = read_feature(member2(
        f'<bldg:Building gml:id="B">{ms("BP3", "lod3MultiSurface")}{ms("BP4", "lod4MultiSurface")}'
        '<bldg:lod4Solid><gml:Solid gml:id="S4"><gml:exterior><gml:CompositeSurface>'
        '<gml:surfaceMember xlink:href="#RP"/></gml:CompositeSurface></gml:exterior></gml:Solid>'
        '</bldg:lod4Solid>'
        '<bldg:interiorBuildingInstallation><bldg:IntBuildingInstallation gml:id="IBI">'
        '<bldg:lod4Geometry><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="IP">'
        f'<gml:exterior><gml:LinearRing><gml:posList>{SQUARE}</gml:posList></gml:LinearRing>'
        '</gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></bldg:lod4Geometry>'
        '</bldg:IntBuildingInstallation></bldg:interiorBuildingInstallation>'
        '<bldg:interiorRoom><bldg:Room gml:id="R"><bldg:boundedBy><bldg:FloorSurface gml:id="F">'
        f'{ms("RP", "lod4MultiSurface")}</bldg:FloorSurface></bldg:boundedBy></bldg:Room>'
        '</bldg:interiorRoom></bldg:Building>')[0], report=report)
    # lod3MultiSurface was in the file: lod4MultiSurface does not overwrite it.
    assert set(root.geometries) == {"lod3MultiSurface", "lod3Solid"}
    assert root.geometries["lod3MultiSurface"].polygons[0].polygon_id == "BP3"
    assert root.extra["unmapped_geometries"]["lod4MultiSurface"]["polygons"][0]["polygon_id"] == "BP4"
    assert any(r["note"].startswith("conflict") and r["from"] == "lod4MultiSurface" for r in report)
    assert root.lod_origin == "lod4Solid"
    ibi, room = root.spaces
    assert (ibi.feature_type, ibi.interior, ibi.extra["concept_origin"]) == \
        ("BuildingInstallation", True, "IntBuildingInstallation")
    assert list(ibi.geometries) == ["lod3Geometry"] and ibi.lod_origin == "lod4Geometry"
    assert (room.feature_type, room.interior, room.extra["concept_origin"]) == ("BuildingRoom", True, "Room")
    assert list(room.boundaries[0].geometries) == ["lod3MultiSurface"]
    assert validate(root) == []


def test_lod0_footprint_and_roof_edge_become_surfaces():
    report = []
    root = read_feature(member2(f'<bldg:Building gml:id="B">{ms("FP", "lod0FootPrint")}'
                             f'{ms("RE", "lod0RoofEdge")}</bldg:Building>')[0], report=report)
    assert root.geometries == {}
    assert [(b.gml_id, b.surface_type, list(b.geometries), b.lod_origin) for b in root.boundaries] == [
        ("B_lod0FootPrint", "GroundSurface", ["lod0MultiSurface"], "lod0FootPrint"),
        ("B_lod0RoofEdge", "RoofSurface", ["lod0MultiSurface"], "lod0RoofEdge")]
    assert len(report) == 2 and validate(root) == []
    root = read(CS1_LOD0)[0]
    assert [b.surface_type for b in root.boundaries] == ["GroundSurface"]


def test_upgrade_can_be_skipped():
    root = read_feature(member2(f'<bldg:Building gml:id="B">{ms("FP", "lod4MultiSurface")}'
                             '</bldg:Building>')[0], upgrade=False)
    assert list(root.geometries) == ["lod4MultiSurface"] and not root.lod_origin


def test_geometry_by_unresolved_reference_is_kept_and_reported():
    report, warnings = [], []
    root = read_feature(member2(
        '<bldg:Building gml:id="B"><bldg:outerBuildingInstallation><bldg:BuildingInstallation gml:id="I">'
        '<bldg:lod2Geometry xlink:href="#nowhere"/></bldg:BuildingInstallation>'
        '</bldg:outerBuildingInstallation></bldg:Building>')[0], warnings, report)
    inst = root.spaces[0]
    assert inst.geometries["lod2Geometry"].href == "#nowhere"
    assert any("unresolved xlink '#nowhere'" in w for w in warnings)
    assert any(r["from"] == "lod2Geometry" and "kept as read" in r["note"] for r in report)


def test_citygml_1_0_namespaces_read_as_2_0():
    body = (f'<bldg:Building gml:id="B"><bldg:boundedBy><bldg:WallSurface gml:id="W">'
            f'{ms("P")}</bldg:WallSurface></bldg:boundedBy></bldg:Building>')
    v2 = read_document(doc2(body))
    xml1 = ET.tostring(doc2(body))
    for old, new in up.NAMESPACES_1_TO_2:
        xml1 = xml1.replace(new, old)
    v1 = read_document(ET.fromstring(xml1))
    assert projection(v1) == projection(v2)
    assert v1[0].schema_version == "1.0" and v2[0].schema_version == "2.0"
    assert up.namespace_1_to_2("http://www.opengis.net/citygml/building/1.0") == \
        "http://www.opengis.net/citygml/building/2.0"
    assert up.namespace_1_to_2("urn:other") == "urn:other"
    assert fragment.translate_1_to_2 is up.translate_1_to_2
    assert fragment.NAMESPACES_1_TO_2 is up.NAMESPACES_1_TO_2


# -- xlinks, unknown elements, validation, round-trip -----------------------

def test_xlinks_resolved_against_document_ids():
    warnings = []
    roots = read_document(doc2(
        f'<bldg:Building gml:id="B"><bldg:boundedBy><bldg:WallSurface gml:id="W">{ms("P1")}'
        '</bldg:WallSurface></bldg:boundedBy>'
        '<bldg:lod2Solid><gml:Solid><gml:exterior><gml:CompositeSurface>'
        '<gml:surfaceMember xlink:href="#P1"/><gml:surfaceMember xlink:href="#P9"/>'
        '</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod2Solid>'
        '<bldg:lod2MultiCurve xlink:href="#P1"/>'
        '<bldg:outerBuildingInstallation xlink:href="#missing"/></bldg:Building>'), warnings)
    root = roots[0]
    solid = root.geometries["lod2Solid"]
    assert solid.extra["unresolved_hrefs"] == ["#P9"]
    resolved = root.geometries["lod2MultiCurve"]
    assert resolved.extra["resolved_href"] == "#P1" and resolved.polygons[0].polygon_id == "P1"
    assert root.extra["feature_hrefs"] == [{"role": "outerBuildingInstallation", "href": "#missing"}]
    assert "target_id" not in root.extra["feature_hrefs"][0]
    assert sorted(w.split("xlink ")[1] for w in warnings) == ["'#P9'", "'#missing'"]
    assert faces(roots) == 1   # a resolved reference is not a second polygon


def test_unknown_elements_kept_in_extra():
    root = read_document(doc2(
        '<bldg:Building gml:id="B"><bldg:function>1000</bldg:function>'
        '<bldg:measuredHeight uom="m">7.5</bldg:measuredHeight>'
        '<foo:bar xmlns:foo="urn:ade">x</foo:bar>'
        '<gen:stringAttribute name="s"><gen:value>v</gen:value></gen:stringAttribute>'
        '<gen:intAttribute name="i"><gen:value>oops</gen:value></gen:intAttribute>'
        '</bldg:Building>'))[0]
    unknown = root.extra["unknown_elements"]
    assert len(unknown) == 1 and "urn:ade" in unknown[0]
    assert root.extra["thematic_attributes"] == {
        "bldg:function": [{"value": "1000"}],
        "bldg:measuredHeight": [{"value": 7.5, "uom": "m"}]}
    assert [(a.name, a.type, a.value) for a in root.attrs.generic_attributes] == \
        [("s", "string", "v"), ("i", "string", "oops")]
    assert validate(root) == []


def test_missing_gml_id_is_generated():
    root = read_document(doc2('<bldg:Building gml:id="B"><bldg:boundedBy><bldg:WallSurface>'
                              f'{ms("P")}</bldg:WallSurface></bldg:boundedBy></bldg:Building>'))[0]
    wall = root.boundaries[0]
    assert wall.gml_id == "B_WallSurface_0" and wall.extra["generated_id"] is True
    assert validate(root) == []


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_validate_and_round_trip(path):
    warnings = []
    for root in read(path, warnings):
        assert isinstance(root, SpaceNode) and validate(root) == []
        data = ir_to_dict(root)
        assert ir_from_dict(json.loads(json.dumps(data))) == root
        assert all(isinstance(g, GeometryNode) for n in iter_features(root)
                   for g in n.geometries.values())
    assert warnings == []


# -- part 2: multiLOD faces, thematic attributes, appearances, feature xlinks --

def lod_faces(roots, lods):
    return sum(1 + len(p.interiors) for r in roots for n in iter_features(r)
               for k, g in n.geometries.items() if int(k[3]) in lods
               and "resolved_href" not in g.extra for p in g.polygons)


@pytest.mark.parametrize("path", list(MULTI_LOD_FACES), ids=lambda p: p.name)
def test_multi_lod_importer_faces_are_ir_faces_without_lod0_and_lod1(path):
    roots = read(path)
    assert faces(roots) > MULTI_LOD_FACES[path]
    assert lod_faces(roots, {2, 3, 4}) == MULTI_LOD_FACES[path]


def xml_count(tree, module, local):
    return sum(1 for e in tree.iter() if isinstance(e.tag, str)
               and re.search(rf"/{module}/\d\.\d}}{local}$", e.tag))


THEMATIC = ("class", "function", "usage", "roofType", "measuredHeight", "storeysAboveGround",
            "storeysBelowGround", "yearOfConstruction", "yearOfDemolition",
            "storeyHeightsAboveGround", "storeyHeightsBelowGround")


def test_thematic_attributes_typed_and_structured():
    warnings = []
    root = read_document(doc2(
        '<bldg:Building gml:id="B"><bldg:class codeSpace="cs">1000</bldg:class>'
        '<bldg:function>1000</bldg:function><bldg:function>1010</bldg:function>'
        '<bldg:roofType></bldg:roofType><bldg:yearOfConstruction>1990</bldg:yearOfConstruction>'
        '<bldg:yearOfDemolition>soon</bldg:yearOfDemolition>'
        '<bldg:storeysAboveGround>3</bldg:storeysAboveGround>'
        '<bldg:storeyHeightsAboveGround uom="m">2.5 3.0</bldg:storeyHeightsAboveGround>'
        '<bldg:measuredHeight uom="m">7.5</bldg:measuredHeight></bldg:Building>'), warnings)[0]
    assert root.extra["thematic_attributes"] == {
        "bldg:class": [{"value": "1000", "code_space": "cs"}],
        "bldg:function": [{"value": "1000"}, {"value": "1010"}],
        "bldg:roofType": [{"value": ""}],
        "bldg:yearOfConstruction": [{"value": 1990}],
        "bldg:yearOfDemolition": [{"value": "soon"}],
        "bldg:storeysAboveGround": [{"value": 3}],
        "bldg:storeyHeightsAboveGround": [{"value": [2.5, 3.0], "uom": "m"}],
        "bldg:measuredHeight": [{"value": 7.5, "uom": "m"}]}
    assert len(warnings) == 1 and "yearOfDemolition" in warnings[0]
    assert "unknown_elements" not in root.extra
    assert ir_from_dict(json.loads(json.dumps(ir_to_dict(root)))) == root


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_thematic_attribute_counts_match_the_xml(path):
    need(path)
    tree = ET.parse(path).getroot()
    nodes = [n for r in read_document(tree) for n in iter_features(r)]
    for local in THEMATIC:
        found = sum(len(n.extra.get("thematic_attributes", {}).get(f"bldg:{local}", ()))
                    for n in nodes)
        assert found == xml_count(tree, "building", local), local


def test_thematic_attributes_of_other_modules():
    root = read_document(doc2(
        '<bldg:Building gml:id="B"><core:relativeToTerrain>entirelyAboveTerrain'
        '</core:relativeToTerrain></bldg:Building>'))[0]
    assert root.extra["thematic_attributes"] == {
        "core:relativeToTerrain": [{"value": "entirelyAboveTerrain"}]}


def test_full_appearance_targets_are_linked_to_their_owners():
    roots = read(FULL_APPEARANCE)
    tree = ET.parse(FULL_APPEARANCE).getroot()
    appearances = {a.get(f"{{{GML}}}id"): sorted(
        (t.get("uri") or (t.text or "")).lstrip("#") for t in a.iter() if t.tag.endswith("}target"))
        for a in tree.iter() if a.tag.endswith("}Appearance")}
    assert len(appearances) == 1
    owner = {p.polygon_id: n.gml_id for r in roots for n in iter_features(r)
             for g in n.geometries.values() for p in g.polygons}
    expected = {}
    for app_id, targets in appearances.items():
        for t in targets:
            expected.setdefault(owner[t], set()).add(app_id)
    actual = {n.gml_id: set(n.attrs.appearance_refs) for r in roots for n in iter_features(r)
              if n.attrs.appearance_refs}
    assert actual == expected and len(actual) == 8


def test_nested_appearance_and_unlinked_targets():
    root = read_document(doc2(
        f'<bldg:Building gml:id="B"><bldg:boundedBy><bldg:WallSurface gml:id="W">{ms("P1")}'
        '</bldg:WallSurface></bldg:boundedBy>'
        '<app:appearance xmlns:app="http://www.opengis.net/citygml/appearance/2.0">'
        '<app:Appearance gml:id="A1"><app:surfaceDataMember><app:X3DMaterial>'
        '<app:target>#P1</app:target><app:target>#elsewhere</app:target>'
        '</app:X3DMaterial></app:surfaceDataMember></app:Appearance></app:appearance>'
        '</bldg:Building>'))[0]
    assert root.attrs.appearance_refs == ("A1",)                       # nested in the building
    assert root.boundaries[0].attrs.appearance_refs == ("A1",)         # target P1
    assert "A1" in root.extra["unknown_elements"][0]                   # content kept for T11d


def doc_members(*bodies):
    return ET.fromstring(f"<core:CityModel {NS2}>" + "".join(
        f"<core:cityObjectMember>{b}</core:cityObjectMember>" for b in bodies) + "</core:CityModel>")


def test_feature_xlinks_are_references_not_copies():
    warnings = []
    roots = read_document(doc_members(
        '<bldg:Building gml:id="B"><bldg:consistsOfBuildingPart xlink:href="#PART"/>'
        '<bldg:boundedBy><bldg:WallSurface gml:id="W1"><bldg:opening><bldg:Window gml:id="WIN">'
        f'{ms("WP", "lod3MultiSurface")}</bldg:Window></bldg:opening></bldg:WallSurface></bldg:boundedBy>'
        '<bldg:boundedBy><bldg:WallSurface gml:id="W2"><bldg:opening xlink:href="#WIN"/>'
        '<bldg:opening xlink:href="#P1"/></bldg:WallSurface></bldg:boundedBy></bldg:Building>',
        f'<bldg:BuildingPart gml:id="PART"><bldg:boundedBy><bldg:RoofSurface gml:id="R">{ms("P1")}'
        '</bldg:RoofSurface></bldg:boundedBy></bldg:BuildingPart>'), warnings)
    building, part = roots
    ids = [n.gml_id for r in roots for n in iter_features(r)]
    assert sorted(ids) == sorted(set(ids)) == sorted(["B", "W1", "WIN", "W2", "PART", "R"])
    assert building.extra["feature_hrefs"] == [{"role": "consistsOfBuildingPart", "href": "#PART",
                                                "target_id": "PART", "target_type": "BuildingPart"}]
    assert building.spaces == ()
    w2 = building.boundaries[1]
    assert w2.openings == () and w2.extra["feature_hrefs"][0] == {
        "role": "opening", "href": "#WIN", "target_id": "WIN", "target_type": "Window"}
    assert "target_id" not in w2.extra["feature_hrefs"][1]
    assert len(warnings) == 1 and "does not point to a feature" in warnings[0] and "#P1" in warnings[0]
    assert all(validate(r) == [] for r in roots) and faces(roots) == 2
