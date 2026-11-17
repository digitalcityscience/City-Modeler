"""T11d (part 1): IR -> scene payload (``shared/builder_payload.py``), no Blender.

Mesh totals are the importer's (``t11a_import_snapshot.py``, 2026-10-24, feature
flag off): vertices, faces and objects of the scene a 2.0 file produced, so the
payload is checked against what the old importer builds, not against the IR.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_ir import (CityObjectAttrs, GenericAttribute, GeometryNode, SpaceNode,  # noqa: E402
                               ThematicSurfaceNode)
from shared.citygml_reader import read_document  # noqa: E402
from shared.semantic_tree import PolygonNode, RingNode  # noqa: E402
from shared.semantic_tree import BOUNDARY_SURFACE_TYPES, OPENING_FEATURE_TYPES  # noqa: E402
from test_t11b_gml_geometry import FIXTURES, SAMPLES  # noqa: E402
from test_t11c_citygml2_to_ir import doc2, ms, need  # noqa: E402

BUILDING = FIXTURES / "Building"
CS = BUILDING / "CitySmith"
APP = BUILDING / "SemanticHierarchy_Appearance_"

# file -> (mesh vertices, mesh faces, objects) of the old importer's scene
SCENES = {
    SAMPLES / "buildings_lod2_citygml_2_0.gml": (198, 39, 42),
    SAMPLES / "buildings_lod3_citygml_2_0.gml": (701, 225, 39),
    FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml": (8, 2, 4),
    BUILDING / "SemanticHierarchy_Installations_LoD3_CityGML2.gml": (24, 6, 7),
    BUILDING / "Railway_Scene_LoD3_CityGML2_Building_only.gml": (503, 227, 37),
    CS / "CS1_lod2.gml": (348, 57, 40),
    CS / "CS1_lod3_enhanced.gml": (523, 111, 67),
    CS / "CS2_lod3_enhanced.gml": (597, 188, 18),
    CS / "CS1_multiLOD.gml": (871, 168, 105),
    CS / "CS2_multiLOD.gml": (851, 209, 21),
    Path(str(APP) + "FullAppearance_LoD3_CityGML2.gml"): (32, 8, 9),
    Path(str(APP) + "InstallationSubtype_LoD3_CityGML2.gml"): (40, 10, 11),
    Path(str(APP) + "LoD3_CityGML2.gml"): (12, 3, 4),
    Path(str(APP) + "MixedLoD_LoD3_CityGML2.gml"): (44, 11, 12),
    Path(str(APP) + "PartialAppearance_LoD3_CityGML2.gml"): (38, 10, 9),
    Path(str(APP) + "TextureFallback_LoD3_CityGML2.gml"): (32, 8, 9),
    Path(str(APP) + "UnknownSubtype_LoD3_CityGML2.gml"): (36, 9, 10),
}


def plan_of(path, **kwargs):
    need(path)
    roots = read_document(ET.parse(path).getroot(), upgrade=False)
    assert bp.unsupported_reasons(roots) == []
    return bp.plan_document(roots, (0.0, 0.0, 0.0), **kwargs)


def scene_totals(plan):
    """(vertices, faces, objects) after ``explode_building_into_semantic_objects``:
    a Building/BuildingPart mesh is split by surface id, anything else stays whole."""
    vertices = faces = objects = 0
    for f in plan.features:
        g = f.geometry
        if not g.faces:
            continue
        objects += 1
        faces += len(g.faces)
        if f.feature_type not in ("Building", "BuildingPart"):
            vertices += len(g.verts)
            continue
        groups, rest = {}, set()
        for idxs, label in zip(g.faces, g.surf_labels):
            surface_type, poly_id = label[0], label[1]
            sid = g.surface_id_by_poly.get(poly_id, "")
            key = sid or f"{surface_type}::{poly_id}"
            kind = surface_type.lower()
            if kind in OPENING_FEATURE_TYPES or kind in BOUNDARY_SURFACE_TYPES:
                groups.setdefault(key, set()).update(idxs)
            else:
                rest.update(idxs)
        vertices += sum(len(v) for v in groups.values()) + len(rest)
        objects += len(groups)
    return vertices, faces, objects


@pytest.mark.parametrize("path", sorted(SCENES, key=str), ids=lambda p: p.name)
def test_payload_totals_match_the_importer_scene(path):
    assert scene_totals(plan_of(path)) == SCENES[path]


def test_closing_point_and_welding():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0))
    again = ((1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0.000001), (1, 0, 0))
    polys = tuple(PolygonNode(f"P{i}", RingNode(f"R{i}", f"P{i}", "exterior", r))
                  for i, r in enumerate((ring, again)))
    node = SpaceNode("B", "Building", geometries={
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=polys)})
    geometry = bp.feature_geometry(node, (0.0, 0.0, 0.0))
    # 4 + 4 corners, two shared by the 1e-5 weld (1,0,0) and (1,1,0)
    assert len(geometry.verts) == 6
    assert geometry.faces == [[0, 1, 2, 3], [1, 4, 5, 2]]
    assert [label[1:3] for label in geometry.surf_labels] == [("P0", "R0"), ("P1", "R1")]


def test_origin_is_subtracted():
    ring = ((100, 200, 5), (101, 200, 5), (101, 201, 5))
    node = SpaceNode("B", "Building", geometries={"lod2MultiSurface": GeometryNode(
        gml_type="MultiSurface",
        polygons=(PolygonNode("P", RingNode("R", "P", "exterior", ring)),))})
    assert bp.feature_geometry(node, (100.0, 200.0, 5.0)).verts == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]


def test_interior_ring_is_its_own_face_with_the_polygon_id():
    outer = ((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))
    hole = ((1, 1, 0), (1, 2, 0), (2, 2, 0), (2, 1, 0))
    poly = PolygonNode("P", RingNode("RO", "P", "exterior", outer),
                       (RingNode("RI", "P", "interior", hole),))
    node = SpaceNode("B", "Building", geometries={
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(poly,))})
    labels = bp.feature_geometry(node, (0.0, 0.0, 0.0)).surf_labels
    assert [(label[1], label[2]) for label in labels] == [("P", "RO"), ("P", "RI")]


def surface(gml_id, kind, pid, *, generated=False, ring=((0, 0, 0), (1, 0, 0), (1, 1, 0)), openings=()):
    poly = PolygonNode(pid, RingNode(pid + "_r", pid, "exterior", ring), multisurface_id="MS_" + pid)
    return ThematicSurfaceNode(
        gml_id, kind, {"lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(poly,))},
        openings=tuple(openings), extra={"generated_id": True} if generated else {})


def test_surface_context_and_opening_context():
    window = SpaceNode("W", "Window", geometries={"lod3MultiSurface": GeometryNode(
        gml_type="MultiSurface", polygons=(PolygonNode(
            "PW", RingNode("RW", "PW", "exterior", ((0, 0, 0), (1, 0, 0), (1, 1, 0)))),))},
        attrs=CityObjectAttrs(generic_attributes=(GenericAttribute("Area", "double", 2.5),)))
    node = SpaceNode("B", "Building", boundaries=(surface("WALL", "WallSurface", "PWALL", openings=[window]),))
    g = bp.feature_geometry(node, (0.0, 0.0, 0.0))
    assert [(l[0], l[1], l[5]) for l in g.surf_labels] == [
        ("WallSurface", "PWALL", 2), ("Window", "PW", 3)]
    assert g.surface_id_by_poly == {"PWALL": "WALL", "PW": "W"}
    assert g.surf_attrs_by_poly["PW"] == {
        "Area": 2.5, "filling_parent_surface_id": "WALL", "opening_surface_id": "WALL",
        "BoundarySurfaceType": "WallSurface", "opening_surface_type": "WallSurface"}
    assert g.multisurface_id_by_poly == {"PWALL": "MS_PWALL"}


def test_polygons_without_surface_are_dropped_once_a_surface_exists():
    loose = PolygonNode("LOOSE", RingNode("RL", "LOOSE", "exterior", ((0, 0, 0), (1, 0, 0), (1, 1, 0))))
    node = SpaceNode("B", "Building", boundaries=(surface("WALL", "WallSurface", "PWALL"),),
                     geometries={"lod2Solid": GeometryNode(gml_type="Solid", polygons=(loose,))})
    assert [l[1] for l in bp.feature_geometry(node, (0.0, 0.0, 0.0)).surf_labels] == ["PWALL"]
    upright = PolygonNode("UP", RingNode("RU", "UP", "exterior", ((0, 0, 0), (1, 0, 0), (1, 0, 1))))
    only_loose = SpaceNode("B", "Building", geometries={"lod2Solid": GeometryNode(
        gml_type="Solid", polygons=(upright,))})
    assert [l[0] for l in bp.feature_geometry(only_loose, (0.0, 0.0, 0.0)).surf_labels] == ["WallSurface"]


def test_surface_type_is_inferred_from_the_normal_and_height():
    def poly(pid, ring):
        return PolygonNode(pid, RingNode(pid + "r", pid, "exterior", ring))
    node = SpaceNode("B", "Building", geometries={"lod2Solid": GeometryNode(gml_type="Solid", polygons=(
        poly("FLOOR", ((0, 0, 0), (1, 0, 0), (1, 1, 0))),
        poly("TOP", ((0, 0, 3), (1, 0, 3), (1, 1, 3))),
        poly("SIDE", ((0, 0, 0), (1, 0, 0), (1, 0, 3))),
    ))})
    labels = {l[1]: l[0] for l in bp.feature_geometry(node, (0.0, 0.0, 0.0)).surf_labels}
    assert labels == {"FLOOR": "GroundSurface", "TOP": "RoofSurface", "SIDE": "WallSurface"}


def test_ids_the_reader_made_up():
    wall = surface("B_WallSurface_0", "WallSurface", "PW", generated=True)
    node = SpaceNode("cgml_Building_0", "Building", boundaries=(wall,), extra={"generated_id": True})
    payload = bp.feature_payload(node, (0.0, 0.0, 0.0))
    assert payload.gml_id.startswith("feat_") and payload.gml_id[5:].isdigit()
    assert payload.gml_id == bp.object_id(node)
    assert payload.geometry.surface_id_by_poly == {}          # explode groups by polygon instead
    assert bp.plan_document([node], (0, 0, 0)).edges == []


def test_installation_surface_id_and_multisurface_for_a_generated_id():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0))
    inst = SpaceNode("I_BuildingInstallation_1", "BuildingInstallation", geometries={
        "lod3Geometry": GeometryNode(gml_type="MultiSurface", polygons=(
            PolygonNode("PI", RingNode("RI", "PI", "exterior", ring)),))},
        extra={"generated_id": True})
    g = bp.feature_payload(inst, (0.0, 0.0, 0.0)).geometry
    sid = g.surface_id_by_poly["PI"]
    assert sid.startswith(bp.object_id(inst) + "_BuildingInstallation_")
    assert g.multisurface_id_by_poly == {"PI": sid + "_ms"}
    assert g.surf_labels[0][0] == "BuildingInstallation"


def test_edges_follow_ids_the_file_defines():
    ring = ((0, 0, 0), (1, 0, 0), (1, 1, 0))
    geom = {"lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(
        PolygonNode("P", RingNode("R", "P", "exterior", ring)),))}
    part = SpaceNode("PART", "BuildingPart", geometries=geom)
    inst = SpaceNode("INST", "BuildingInstallation", geometries=geom, source_role="outerBuildingInstallation")
    anonymous = SpaceNode("x_BuildingPart_3", "BuildingPart", geometries=geom, extra={"generated_id": True})
    root = SpaceNode("B", "Building", geometries=geom, spaces=(part, inst, anonymous))
    plan = bp.plan_document([root], (0.0, 0.0, 0.0))
    assert plan.edges == [("B", "PART", "part"), ("B", "INST", "installation")]
    payload = {f.gml_id: f for f in plan.features}["INST"]
    assert (payload.source_role, payload.source_element) == ("outer", "bldg:outerBuildingInstallation")


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

def props_of(body):
    roots = read_document(doc2(body), upgrade=False)
    return bp.importer_properties(roots[0])


def test_values_the_importer_writes_for_a_building():
    props = props_of(
        '<bldg:Building gml:id="B"><gml:name>Haus</gml:name>'
        '<core:creationDate>2023-02-23</core:creationDate>'
        '<gen:stringAttribute name="Quelle"><gen:value> amtlich </gen:value></gen:stringAttribute>'
        '<gen:intAttribute name="N"><gen:value>7</gen:value></gen:intAttribute>'
        '<gen:doubleAttribute name="A"><gen:value>2.5</gen:value></gen:doubleAttribute>'
        '<bldg:class>1000</bldg:class><bldg:function>1010</bldg:function><bldg:function>1020</bldg:function>'
        '<bldg:usage>2000</bldg:usage><bldg:roofType/><bldg:sortKey>12</bldg:sortKey>'
        '<bldg:yearOfConstruction>1999</bldg:yearOfConstruction>'
        '<bldg:storeysAboveGround>5</bldg:storeysAboveGround>'
        '<bldg:storeyHeightsAboveGround uom="m">2.5 3.0</bldg:storeyHeightsAboveGround>'
        '</bldg:Building>')
    assert props == {
        "core:creationDate": "2023-02-23", "bldg:sortKey": 12, "bldg:class": "1000",
        "bldg:function": "1010", "bldg:usage": "2000", "bldg:roofType": "",
        "bldg:yearOfConstruction": 1999, "bldg:storeysAboveGround": 5,
        "bldg:storeyHeightsAboveGround": [2.5, 3.0], "bldg:storeyHeightsAboveGround:uom": "m",
        "Quelle": "amtlich", "N": 7, "A": 2.5}


def test_sort_key_that_is_not_a_number_stays_text():
    assert props_of('<bldg:Building gml:id="B"><bldg:sortKey>a1</bldg:sortKey></bldg:Building>') == {
        "bldg:sortKey": "a1"}


def test_empty_values_are_not_written():
    assert props_of('<bldg:Building gml:id="B"><bldg:class> </bldg:class></bldg:Building>') == {}


def test_lifespan_dates_come_from_the_attrs_and_the_thematic_block():
    props = props_of('<bldg:Building gml:id="B"><core:terminationDate>2030-01-01</core:terminationDate>'
                     '</bldg:Building>')
    assert props == {"core:terminationDate": "2030-01-01"}


def test_luse_function_is_a_list_only_when_repeated():
    props = bp.importer_properties(SpaceNode("L", "LandUse", extra={"thematic_attributes": {
        "luse:class": [{"value": "1"}], "luse:function": [{"value": "a"}, {"value": "b"}],
        "luse:usage": [{"value": "u"}]}}))
    assert props == {"luse:class": "1", "luse:function": ["a", "b"], "luse:usage": "u"}


def test_bridge_attributes_only_for_bridges():
    thematic = {"brid:class": [{"value": "1"}], "brid:isMovable": [{"value": True}],
                "bldg:class": [{"value": "9"}]}
    bridge = bp.importer_properties(SpaceNode("B", "Bridge", extra={"thematic_attributes": thematic}))
    assert bridge == {"bldg:class": "9", "brid:class": "1", "brid:isMovable": True}
    building = bp.importer_properties(SpaceNode("B", "Building", extra={"thematic_attributes": thematic}))
    assert building == {"bldg:class": "9"}


def test_a_feature_does_not_take_the_values_of_features_below_it():
    inst = SpaceNode("I", "BuildingInstallation", extra={"thematic_attributes": {
        "bldg:class": [{"value": "5010"}], "bldg:function": [{"value": "5030"}]}})
    own = SpaceNode("P", "BuildingPart", extra={"thematic_attributes": {"bldg:class": [{"value": "own"}]}})
    root = SpaceNode("B", "Building", spaces=(own, inst))
    assert bp.importer_properties(root) == {}


def test_installation_subtype_follows_class_then_function():
    def installation(**thematic):
        node = SpaceNode("I", "BuildingInstallation", source_role="outerBuildingInstallation",
                         extra={"thematic_attributes": {k: [{"value": v}] for k, v in thematic.items()}})
        return bp.feature_payload(node, (0.0, 0.0, 0.0))
    balcony = installation(**{"bldg:class": "5010"})
    assert (balcony.installation_type, balcony.installation_origin) == ("Balcony", "class")
    chimney = installation(**{"bldg:function": "5030"})
    assert chimney.installation_type == "Chimney" and chimney.installation_origin == "function"
    unknown = installation(**{"bldg:class": "9999"})
    assert unknown.installation_type == "BuildingInstallation" and unknown.installation_unknown_subtype
    assert unknown.installation_unknown_value == "9999"
    bare = installation()
    assert not bare.installation_unknown_subtype


# ---------------------------------------------------------------------------
# what the builder refuses
# ---------------------------------------------------------------------------

def reasons_of(body):
    return bp.unsupported_reasons(read_document(doc2(body), upgrade=False))


def test_supported_building_has_no_reasons():
    assert reasons_of(f'<bldg:Building gml:id="B">{ms("P")}</bldg:Building>') == []


def test_room_is_not_built_yet():
    body = (f'<bldg:Building gml:id="B">{ms("P")}<bldg:interiorRoom><bldg:Room gml:id="R"/>'
            '</bldg:interiorRoom></bldg:Building>')
    assert reasons_of(body) == ["feature type Room"]


def test_curve_only_feature_is_not_built_yet():
    body = ('<bldg:Building gml:id="B"><bldg:lod2TerrainIntersection><gml:MultiCurve><gml:curveMember>'
            '<gml:LineString><gml:posList>0 0 0 1 1 1</gml:posList></gml:LineString></gml:curveMember>'
            '</gml:MultiCurve></bldg:lod2TerrainIntersection></bldg:Building>')
    assert reasons_of(body) == ["feature with curve or point geometry only"]


def test_feature_without_geometry_is_supported_and_builds_nothing():
    roots = read_document(doc2('<bldg:Building gml:id="B"/>'), upgrade=False)
    assert bp.unsupported_reasons(roots) == []
    plan = bp.plan_document(roots, (0.0, 0.0, 0.0))
    assert plan.features[0].geometry.faces == []


def test_other_feature_types_are_not_built_yet():
    body = f'<bldg:Building gml:id="B">{ms("P")}</bldg:Building>'
    roots = read_document(doc2(body), upgrade=False)
    furniture = SpaceNode("F", "Tunnel")
    assert bp.unsupported_reasons([furniture]) == ["feature type Tunnel"]
    assert bp.unsupported_reasons(roots) == []
