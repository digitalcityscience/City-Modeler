"""T11a: CityGML 3 shaped IR (shared/citygml_ir.py) - round trip of the new
node types, the version-1 (semantic_tree BuildingNode) migration, legacy
2.0 markers and rejection of broken or unknown versions. No Blender."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import citygml_ir as ir  # noqa: E402
from shared import semantic_tree as st  # noqa: E402

SQUARE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))


def poly(pid, link=""):
    interiors = (st.RingNode(f"{pid}_1_", pid, "interior", SQUARE, opening_link=link),) if link else ()
    return st.PolygonNode(pid, st.RingNode(f"{pid}_0_", pid, "exterior", SQUARE), interiors,
                          multisurface_id="MS1", is_multisurface_member=True)


def geom(*pids, gml_type="MultiSurface"):
    return ir.GeometryNode(gml_type=gml_type, polygons=tuple(poly(p) for p in pids))


def full_building():
    attrs = ir.CityObjectAttrs(
        names=("Rathaus",), description="town hall", creation_date="2026-01-01",
        generic_attributes=(
            ir.GenericAttribute("floors", "int", 3),
            ir.GenericAttribute("height", "measure", 12.5, uom="m"),
            ir.GenericAttribute("built", "date", "1897-10-26"),
            ir.GenericAttribute("usage", "code", "1010", code_space="http://example.org/usage"),
            ir.GenericAttribute("survey", "set", members=(
                ir.GenericAttribute("by", "string", "LGV"),
                ir.GenericAttribute("url", "uri", "https://example.org/s/1"),
            )),
        ),
        external_references=(ir.ExternalReference("https://example.org/b/1", "ALKIS", "sameAs"),),
        relations=(ir.CityObjectRelation("B2", "adjacent",
                                         (ir.GenericAttribute("shared_wall", "double", 4.2),)),),
        addresses=(ir.Address("A1", "Rathausmarkt", "1", "20095", "Hamburg", "HH", "DE",
                              multi_point=((1.0, 2.0, 3.0),)),),
        appearance_refs=("app1",),
    )
    window = ir.SpaceNode("WIN1", "Window", "occupied", host_id="W1", parent_id="W1",
                          opening_link="OPENINGLINK_a", geometries={"lod3MultiSurface": geom("P_WIN")})
    wall = ir.ThematicSurfaceNode("W1", "WallSurface", {"lod3MultiSurface": geom("P_WALL")},
                                  openings=(window,), ring_refs=(st.RingNode("P_WALL_1_", "P_WALL", "interior"),),
                                  schema_version="3.0", source_element="con:WallSurface", parent_id="B1")
    room = ir.SpaceNode("R1", "BuildingRoom", "unoccupied",
                        geometries={"lod3Solid": geom("P_R", gml_type="Solid")},
                        boundaries=(ir.ThematicSurfaceNode("F1", "FloorSurface"),))
    storey = ir.SpaceNode("S1", "Storey", "unoccupied", spaces=(room,))
    beam = ir.ConstructiveElementNode("CE1", geometries={"lod3Solid": geom("P_CE", gml_type="Solid")},
                                      boundaries=(ir.ThematicSurfaceNode("CE1_S", "WallSurface"),))
    chimney = ir.SpaceNode("I1", "BuildingInstallation", "occupied", subtype="Chimney",
                           geometries={"lod2MultiSurface": geom("P_I")})
    return ir.SpaceNode(
        "B1", "Building", "occupied", schema_version="3.0", hierarchy_model="semantic_objects",
        geometries={
            "lod2Solid": ir.GeometryNode("SOL1", "Solid", href="#ext"),
            "lod0MultiSurface": geom("P_FP"),
            "lod1TerrainIntersectionCurve": ir.GeometryNode(curves=(SQUARE,)),
            "lod0Point": ir.GeometryNode(points=((5.0, 5.0, 0.0),)),
        },
        boundaries=(wall,), spaces=(storey, chimney), constructive_elements=(beam,), attrs=attrs,
    )


# ---------------------------------------------------------------- round trip

def test_full_tree_round_trips_through_json():
    root = full_building()
    assert ir.validate(root) == []
    data = json.loads(json.dumps(ir.ir_to_dict(root), allow_nan=False))
    assert data["ir_version"] == ir.IR_VERSION
    assert ir.ir_from_dict(data) == root
    assert ir.ir_to_dict(ir.ir_from_dict(data)) == data


@pytest.mark.parametrize("node", [
    ir.GenericAttribute("x", "set", members=(ir.GenericAttribute("y", "int", 1),)),
    ir.ExternalReference("urn:x", "sys", "rel"),
    ir.CityObjectRelation("B9", "partOf"),
    ir.Address("A", "S", "1", multi_point=((1.0, 2.0, 3.0),), xal="<xAL/>"),
    ir.GeometryNode("G", "CompositeSurface", (poly("P1", "OPENINGLINK_b"),)),
    ir.ConstructiveElementNode("CE", "BridgeConstructiveElement"),
    ir.ThematicSurfaceNode("T", "RoofSurface", {"lod2MultiSurface": geom("P")}),
])
def test_each_node_type_round_trips(node):
    data = json.loads(json.dumps(node.to_dict()))
    assert type(node).from_dict(data) == node


def test_dict_is_detached_from_the_node():
    root = full_building()
    data = root.to_dict()
    data["attrs"]["generic_attributes"][0]["value"] = 99
    data["extra_probe"] = 1
    assert root.attrs.generic_attributes[0].value == 3 and root.extra == {}


def test_unknown_fields_are_kept_at_every_level():
    data = ir.ir_to_dict(full_building())
    data["root"]["future_root"] = {"a": [1]}
    data["root"]["boundaries"][0]["future_surface"] = True
    data["root"]["boundaries"][0]["openings"][0]["future_opening"] = "x"
    data["root"]["geometries"]["lod2Solid"]["future_geom"] = 2
    data["root"]["attrs"]["addresses"][0]["future_addr"] = "y"
    root = ir.ir_from_dict(data)
    assert root.extra == {"future_root": {"a": [1]}}
    assert root.boundaries[0].openings[0].extra == {"future_opening": "x"}
    assert ir.ir_to_dict(root) == data


# ---------------------------------------------------------------- LoD keys and 2.0 markers

def test_lod_keys():
    assert ir.lod_key(2, "Solid") == "lod2Solid"
    assert ir.parse_lod_key("lod0MultiSurface") == (0, "MultiSurface")
    assert ir.is_legacy_lod_key("lod4Solid") and ir.is_legacy_lod_key("lod0FootPrint")
    assert ir.is_legacy_lod_key("lod2Geometry") and not ir.is_legacy_lod_key("lod3MultiSurface")
    assert ir.lod3_equivalent("lod4MultiSurface") == "lod3MultiSurface"
    assert ir.lod3_equivalent("lod2Solid") == "lod2Solid"
    for bad in ("lod5Solid", "lod2", "lod2solid", "Lod2Solid", "lod2Banana", "", None, 2):
        with pytest.raises(ir.IRError):
            ir.parse_lod_key(bad)


def test_citygml2_concepts_are_kept_and_flagged():
    inst = ir.SpaceNode("I1", "IntBuildingInstallation", "occupied", interior=True,
                        geometries={"lod4MultiSurface": geom("P_I")})
    room = ir.SpaceNode("R1", "Room", geometries={"lod4Solid": ir.GeometryNode()})
    root = ir.SpaceNode("B1", "Building", spaces=(inst, room),
                        geometries={"lod2Solid": ir.GeometryNode()})
    assert ir.validate(root) == []           # legacy is not invalid
    assert inst.is_citygml2_concept and inst.upgrade_target() == ("BuildingInstallation", True)
    assert not root.is_citygml2_concept and root.upgrade_target() is None
    assert [(g, t) for g, t, _ in ir.citygml2_concepts(root)] == [
        ("I1", "IntBuildingInstallation"), ("R1", "Room")]
    assert ir.legacy_lod_keys(root) == [("I1", "lod4MultiSurface"), ("R1", "lod4Solid")]
    # the 2.0 concept survives a round trip unchanged (no silent upgrade)
    assert ir.ir_from_dict(ir.ir_to_dict(root)).spaces[0].feature_type == "IntBuildingInstallation"
    assert "intbuildinginstallation" in st.CITYGML2_ONLY_FEATURE_TYPES
    assert st.CITYGML2_ONLY_FEATURE_TYPES <= st._INSTALLATION_FEATURE_TYPES


def test_generic_attributes_from_flat_dict():
    attrs = ir.generic_attributes_from_flat({"a": 1, "b": 2.5, "c": "x", "d": True})
    assert [(a.name, a.type, a.value) for a in attrs] == [
        ("a", "int", 1), ("b", "double", 2.5), ("c", "string", "x"), ("d", "string", "True")]
    assert all(ir.validate_generic_attribute(a, "t") == [] for a in attrs)


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("node, fragment", [
    (ir.SpaceNode("", "Building"), "invalid or empty gml_id"),
    (ir.SpaceNode("B", ""), "empty feature_type"),
    (ir.SpaceNode("B", "Building", "half"), "space_type 'half'"),
    (ir.SpaceNode("B", "Building", geometries={"lod7Solid": ir.GeometryNode()}), "invalid LoD"),
    (ir.SpaceNode("B", "Building", geometries={"lod2Solid": {"polygons": []}}), "not a GeometryNode"),
    (ir.SpaceNode("B", "Building", boundaries=(ir.ThematicSurfaceNode("S", ""),)), "empty surface_type"),
    (ir.SpaceNode("B", "Building", spaces=(ir.SpaceNode("B", "BuildingPart"),)), "duplicate gml_id"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        generic_attributes=(ir.GenericAttribute("n", "int", "3"),))), "int value '3'"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        generic_attributes=(ir.GenericAttribute("n", "double", float("nan")),))), "double value"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        generic_attributes=(ir.GenericAttribute("n", "blob", "x"),))), "type 'blob'"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        generic_attributes=(ir.GenericAttribute("n", "set", 1),))), "a set has members"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        generic_attributes=(ir.GenericAttribute("n", "set", members=(
            ir.GenericAttribute("", "string", "x"),)),))), "empty name"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        external_references=(ir.ExternalReference(""),))), "without target_resource"),
    (ir.SpaceNode("B", "Building", attrs=ir.CityObjectAttrs(
        relations=(ir.CityObjectRelation(""),))), "without related_to"),
])
def test_invalid_trees_are_reported(node, fragment):
    problems = ir.validate(node)
    assert any(fragment in p for p in problems), problems


# ---------------------------------------------------------------- version 1 migration

def v1_building():
    window = st.OpeningNode(
        "WIN1", "Window", source_role="opening", source_element="bldg:Window", schema_version="2.0",
        subtype_origin="element", appearance_refs=("appW",), rings=(st.RingNode("WIN1_r", "P_WIN"),),
        lod=3, lod_origin="source", host_id="W1", parent_id="W1", opening_link="OPENINGLINK_a",
        polygons=(poly("P_WIN"),))
    wall = st.SurfaceNode(
        "W1", "WallSurface", schema_version="2.0", appearance_refs=("app1",),
        rings=(st.RingNode("P_WALL_1_", "P_WALL", "interior", opening_link="OPENINGLINK_a"),),
        openings=(window,), lod=3, lod_origin="source", parent_id="B1",
        polygons=(poly("P_WALL", "OPENINGLINK_a"),))
    roof_ids_only = st.SurfaceNode("R1", "RoofSurface", rings=(st.RingNode("R1_r", "P_R1"),))
    roof_no_lod = st.SurfaceNode("R2", "RoofSurface", polygons=(poly("P_R2"),))
    ground_lod_only = st.SurfaceNode("G1", "GroundSurface", lod=2)
    chimney = st.InstallationNode("I1", "Chimney", source_role="interior",
                                  source_element="bldg:IntBuildingInstallation", schema_version="2.0",
                                  subtype_origin="function", appearance_refs=("appI",))
    balcony = st.InstallationNode("I2", "Balcony", source_element="bldg:BuildingInstallation")
    return st.BuildingNode("B1", "semantic_objects",
                           (wall, roof_ids_only, roof_no_lod, ground_lod_only), (chimney, balcony))


def test_v1_building_dict_is_migrated():
    old = json.loads(json.dumps(v1_building().to_dict()))
    assert "ir_version" not in old
    root = ir.ir_from_dict(old)
    assert ir.validate(root) == []
    assert (root.gml_id, root.feature_type, root.space_type, root.hierarchy_model) == (
        "B1", "Building", "occupied", "semantic_objects")

    wall = root.boundaries[0]
    assert (wall.surface_type, wall.parent_id, wall.attrs.appearance_refs) == ("WallSurface", "B1", ("app1",))
    assert list(wall.geometries) == ["lod3MultiSurface"]
    assert wall.geometries["lod3MultiSurface"].polygons[0].interiors[0].opening_link == "OPENINGLINK_a"
    assert wall.ring_refs[0].ring_id == "P_WALL_1_"
    window = wall.openings[0]
    assert (window.feature_type, window.space_type, window.host_id, window.opening_link) == (
        "Window", "occupied", "W1", "OPENINGLINK_a")
    assert list(window.geometries) == ["lod3MultiSurface"] and window.ring_refs[0].ring_id == "WIN1_r"

    assert root.boundaries[1].geometries == {} and root.boundaries[1].ring_refs[0].ring_id == "R1_r"
    assert root.boundaries[2].geometries == {} and "v1_polygons" in root.boundaries[2].extra
    assert root.boundaries[3].geometries["lod2MultiSurface"].polygons == ()

    chimney, balcony = root.spaces
    assert (chimney.feature_type, chimney.subtype, chimney.interior) == ("IntBuildingInstallation", "Chimney", True)
    assert chimney.is_citygml2_concept
    assert (balcony.feature_type, balcony.subtype, balcony.interior) == ("BuildingInstallation", "Balcony", False)


def test_v1_migration_is_lossless():
    old = v1_building().to_dict()
    assert ir.building_node_dict(ir.ir_from_dict(old)) == old
    assert st.BuildingNode.from_dict(ir.building_node_dict(ir.space_from_building_node(v1_building()))) \
        == v1_building()
    empty = st.BuildingNode("B0").to_dict()
    assert ir.building_node_dict(ir.ir_from_dict(empty)) == empty


def test_v2_tree_beyond_v1_cannot_be_written_as_v1():
    with pytest.raises(ir.IRError, match="cannot be written as v1"):
        ir.building_node_dict(ir.SpaceNode("B", "Building", boundaries=(
            ir.ThematicSurfaceNode("S", "WallSurface", {"lod2MultiCurve": ir.GeometryNode()}),)))


def test_migration_hook_runs_registered_steps(monkeypatch):
    monkeypatch.setattr(ir, "IR_VERSION", 3)
    monkeypatch.setitem(ir.MIGRATIONS, 2, lambda d: {**d, "root": {**d["root"], "feature_type": "Bridge"}})
    root = ir.ir_from_dict({"ir_version": 2, "root": {"gml_id": "X", "feature_type": "Building"}})
    assert root.feature_type == "Bridge"
    # version 1 runs both steps
    assert ir.ir_from_dict(st.BuildingNode("B1").to_dict()).feature_type == "Bridge"


# ---------------------------------------------------------------- negative controls

@pytest.mark.parametrize("data, fragment", [
    ({"ir_version": "two", "root": {}}, "unreadable"),
    ({"ir_version": 0, "root": {}}, "unreadable"),
    ({"ir_version": True, "root": {}}, "unreadable"),
    ({"ir_version": 2.0, "root": {}}, "unreadable"),
    ({"ir_version": ir.IR_VERSION + 1, "root": {}}, "newer than supported"),
    ({"root": {"gml_id": "X", "feature_type": "Building"}}, "without ir_version"),
    ({"ir_version": ir.IR_VERSION}, "without a root"),
    ({"ir_version": ir.IR_VERSION, "root": ["not", "a", "dict"]}, "without a root"),
    (["not", "a", "dict"], "expected a dict"),
    (None, "expected a dict"),
])
def test_broken_or_unknown_versions_are_rejected(data, fragment):
    with pytest.raises(ir.IRError, match=fragment):
        ir.ir_from_dict(data)


def test_missing_migration_is_rejected(monkeypatch):
    monkeypatch.setattr(ir, "IR_VERSION", 3)
    with pytest.raises(ir.IRError, match="no migration from ir_version 2"):
        ir.ir_from_dict({"ir_version": 2, "root": {"gml_id": "X", "feature_type": "Building"}})


def test_missing_required_fields_load_empty_and_are_reported():
    root = ir.ir_from_dict({"ir_version": ir.IR_VERSION, "root": {"boundaries": [{}]}})
    assert (root.gml_id, root.feature_type, root.boundaries[0].surface_type) == ("", "", "")
    problems = ir.validate(root)
    assert any("invalid or empty gml_id" in p for p in problems)
    assert any("empty feature_type" in p for p in problems)
    assert any("empty surface_type" in p for p in problems)
    assert ir.GenericAttribute.from_dict({}).name == ""


def test_nested_non_dict_is_rejected():
    data = ir.ir_to_dict(full_building())
    data["root"]["boundaries"][0] = "WallSurface"
    with pytest.raises(ir.IRError, match="ThematicSurfaceNode: expected a dict"):
        ir.ir_from_dict(data)
