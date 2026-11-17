import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


semantic_tree = load_module("shared/semantic_tree.py", "semantic_tree_under_test")


class FakeObject(dict):
    def __init__(self, name, *, obj_type="EMPTY", parent=None, **props):
        super().__init__(**props)
        self.name = name
        self.type = obj_type
        self.parent = parent
        self.children = []
        if parent is not None:
            parent.children.append(self)


def test_builds_boundary_surface_with_nested_opening_and_own_ring():
    building = FakeObject("Building", gml_id="BLDG_1", cgml_hierarchy_model="semantic_objects")
    wall = FakeObject(
        "Wall 1",
        obj_type="MESH",
        parent=building,
        gml_id="WALL_1",
        cgml3_feature="WallSurface",
        cgml_schema_version="2.0",
        cgml_rings=json.dumps([{"ring_id": "RING_WALL_1", "polygon_id": "POLY_WALL_1"}]),
    )
    FakeObject(
        "Window 1",
        obj_type="MESH",
        parent=wall,
        gml_id="WIN_1",
        cgml3_feature="Window",
        cgml_semantic_type="Window",
        cgml_source_role="interior",
        cgml_source_element="bldg:opening",
        cgml_schema_version="2.0",
        cgml_subtype_origin="feature_type",
    )

    tree = semantic_tree.build_semantic_tree(building)

    assert tree.gml_id == "BLDG_1"
    assert tree.hierarchy_model == "semantic_objects"
    assert len(tree.boundary_surfaces) == 1

    wall_node = tree.boundary_surfaces[0]
    assert wall_node.gml_id == "WALL_1"
    assert wall_node.semantic_type == "WallSurface"
    assert wall_node.rings == (semantic_tree.RingNode("RING_WALL_1", "POLY_WALL_1"),)
    assert len(wall_node.openings) == 1

    opening_node = wall_node.openings[0]
    assert opening_node.gml_id == "WIN_1"
    assert opening_node.semantic_type == "Window"
    assert opening_node.source_role == "interior"
    assert opening_node.rings == ()


def test_ring_owned_by_surface_with_no_child_openings_chimney_penetration():
    # Investigation section 1.3: two RoofSurface rings are chimney
    # penetrations - the chimney is a BuildingInstallation, not an opening,
    # so the roof surface that owns the ring has zero openings.
    building = FakeObject("Building", gml_id="BLDG_1")
    roof = FakeObject(
        "Roof 1",
        obj_type="MESH",
        parent=building,
        gml_id="ROOF_1",
        cgml3_feature="RoofSurface",
        cgml_rings=json.dumps([{"ring_id": "RING_CHIMNEY_HOLE", "polygon_id": "POLY_ROOF_1"}]),
    )
    FakeObject(
        "Chimney 2",
        obj_type="MESH",
        parent=building,
        gml_id="CHIMNEY_2",
        cgml3_feature="BuildingInstallation",
        cgml_semantic_type="Chimney",
        cgml_source_role="outer",
        cgml_source_element="bldg:outerBuildingInstallation",
        cgml_schema_version="2.0",
        cgml_subtype_origin="name",
    )

    tree = semantic_tree.build_semantic_tree(building)

    assert len(tree.boundary_surfaces) == 1
    roof_node = tree.boundary_surfaces[0]
    assert roof_node.gml_id == "ROOF_1"
    assert roof_node.openings == ()
    assert len(roof_node.rings) == 1
    assert roof_node.rings[0].ring_id == "RING_CHIMNEY_HOLE"

    assert len(tree.installations) == 1
    assert tree.installations[0].semantic_type == "Chimney"
    assert tree.installations[0].gml_id == "CHIMNEY_2"


def test_rings_are_stored_never_derived_from_child_openings():
    # A WallSurface with no cgml_rings of its own must stay ring-less even
    # though its child Window carries a ring - derivation from children
    # would be silent, and provably wrong for 26 of the fixture's 41 rings.
    building = FakeObject("Building", gml_id="BLDG_1")
    wall = FakeObject(
        "Wall 1",
        obj_type="MESH",
        parent=building,
        gml_id="WALL_1",
        cgml3_feature="WallSurface",
    )
    FakeObject(
        "Window 1",
        obj_type="MESH",
        parent=wall,
        gml_id="WIN_1",
        cgml3_feature="Window",
        cgml_semantic_type="Window",
        cgml_rings=json.dumps([{"ring_id": "RING_WINDOW_1", "polygon_id": "POLY_WIN_1"}]),
    )

    tree = semantic_tree.build_semantic_tree(building)

    wall_node = tree.boundary_surfaces[0]
    assert wall_node.rings == ()
    assert wall_node.openings[0].rings[0].ring_id == "RING_WINDOW_1"


def test_installations_resolve_alongside_boundary_surfaces_with_full_property_model():
    building = FakeObject("Building", gml_id="BLDG_1")
    FakeObject(
        "Balcony 1",
        obj_type="MESH",
        parent=building,
        gml_id="BALC_1",
        cgml3_feature="BuildingInstallation",
        cgml_semantic_type="Balcony",
        cgml_source_role="outer",
        cgml_source_element="bldg:outerBuildingInstallation",
        cgml_schema_version="2.0",
        cgml_subtype_origin="class",
        cgml_appearance_refs=json.dumps(["APP_1"]),
    )

    tree = semantic_tree.build_semantic_tree(building)

    assert len(tree.installations) == 1
    node = tree.installations[0]
    assert node.gml_id == "BALC_1"
    assert node.semantic_type == "Balcony"
    assert node.source_role == "outer"
    assert node.source_element == "bldg:outerBuildingInstallation"
    assert node.schema_version == "2.0"
    assert node.subtype_origin == "class"
    assert node.appearance_refs == ("APP_1",)


def test_semantic_type_never_carries_a_schema_prefix():
    building = FakeObject("Building", gml_id="BLDG_1")
    wall = FakeObject(
        "Wall 1",
        obj_type="MESH",
        parent=building,
        gml_id="WALL_1",
        cgml3_feature="bldg:WallSurface",
    )
    FakeObject(
        "Window 1",
        obj_type="MESH",
        parent=wall,
        gml_id="WIN_1",
        cgml3_feature="con:WindowSurface",
    )

    tree = semantic_tree.build_semantic_tree(building)

    wall_node = tree.boundary_surfaces[0]
    assert wall_node.semantic_type == "WallSurface"
    assert ":" not in wall_node.semantic_type
    opening_node = wall_node.openings[0]
    assert opening_node.semantic_type == "WindowSurface"
    assert ":" not in opening_node.semantic_type


def test_openings_are_routed_to_their_own_parent_surface_only():
    building = FakeObject("Building", gml_id="BLDG_1")
    wall_a = FakeObject(
        "Wall A", obj_type="MESH", parent=building, gml_id="WALL_A", cgml3_feature="WallSurface"
    )
    wall_b = FakeObject(
        "Wall B", obj_type="MESH", parent=building, gml_id="WALL_B", cgml3_feature="WallSurface"
    )
    FakeObject(
        "Window A",
        obj_type="MESH",
        parent=wall_a,
        gml_id="WIN_A",
        cgml3_feature="Window",
        cgml_semantic_type="Window",
    )
    FakeObject(
        "Door B",
        obj_type="MESH",
        parent=wall_b,
        gml_id="DOOR_B",
        cgml3_feature="Door",
        cgml_semantic_type="Door",
    )

    tree = semantic_tree.build_semantic_tree(building)

    surfaces_by_id = {s.gml_id: s for s in tree.boundary_surfaces}
    assert [o.gml_id for o in surfaces_by_id["WALL_A"].openings] == ["WIN_A"]
    assert [o.gml_id for o in surfaces_by_id["WALL_B"].openings] == ["DOOR_B"]


def test_round_trip_preserves_every_id_it_was_given():
    building = semantic_tree.BuildingNode(
        gml_id="BLDG_1",
        hierarchy_model="semantic_objects",
        boundary_surfaces=(
            semantic_tree.SurfaceNode(
                gml_id="WALL_1",
                semantic_type="WallSurface",
                schema_version="2.0",
                appearance_refs=("APP_WALL_1",),
                rings=(semantic_tree.RingNode("RING_1", "POLY_1"),),
                openings=(
                    semantic_tree.OpeningNode(
                        gml_id="WIN_1",
                        semantic_type="Window",
                        source_role="interior",
                        source_element="bldg:opening",
                        schema_version="2.0",
                        subtype_origin="feature_type",
                        appearance_refs=("APP_WIN_1",),
                        rings=(semantic_tree.RingNode("RING_WIN_1", "POLY_WIN_1"),),
                    ),
                ),
            ),
            semantic_tree.SurfaceNode(
                gml_id="ROOF_1",
                semantic_type="RoofSurface",
                rings=(semantic_tree.RingNode("RING_ROOF_1", "POLY_ROOF_1"),),
            ),
        ),
        installations=(
            semantic_tree.InstallationNode(
                gml_id="CHIMNEY_2",
                semantic_type="Chimney",
                source_role="outer",
                source_element="bldg:outerBuildingInstallation",
                schema_version="2.0",
                subtype_origin="name",
            ),
        ),
    )

    restored = semantic_tree.BuildingNode.from_dict(building.to_dict())

    assert restored == building
    assert restored.gml_id == "BLDG_1"
    assert restored.boundary_surfaces[0].rings[0].ring_id == "RING_1"
    assert restored.boundary_surfaces[0].openings[0].rings[0].ring_id == "RING_WIN_1"
    assert restored.boundary_surfaces[1].rings[0].ring_id == "RING_ROOF_1"
    assert restored.installations[0].gml_id == "CHIMNEY_2"


def test_round_trip_of_a_builder_produced_tree_preserves_every_id():
    building = FakeObject("Building", gml_id="BLDG_1", cgml_hierarchy_model="semantic_objects")
    wall = FakeObject(
        "Wall 1",
        obj_type="MESH",
        parent=building,
        gml_id="WALL_1",
        cgml3_feature="WallSurface",
        cgml_rings=json.dumps([{"ring_id": "RING_WALL_1", "polygon_id": "POLY_WALL_1"}]),
    )
    FakeObject(
        "Window 1",
        obj_type="MESH",
        parent=wall,
        gml_id="WIN_1",
        cgml3_feature="Window",
        cgml_semantic_type="Window",
        cgml_rings=json.dumps([{"ring_id": "RING_WIN_1", "polygon_id": "POLY_WIN_1"}]),
    )
    FakeObject(
        "Chimney 2",
        obj_type="MESH",
        parent=building,
        gml_id="CHIMNEY_2",
        cgml3_feature="BuildingInstallation",
        cgml_semantic_type="Chimney",
        cgml_subtype_origin="name",
    )

    built = semantic_tree.build_semantic_tree(building)
    restored = semantic_tree.BuildingNode.from_dict(built.to_dict())

    assert restored == built
    assert restored.gml_id == "BLDG_1"
    assert restored.boundary_surfaces[0].gml_id == "WALL_1"
    assert restored.boundary_surfaces[0].rings[0].ring_id == "RING_WALL_1"
    assert restored.boundary_surfaces[0].openings[0].gml_id == "WIN_1"
    assert restored.boundary_surfaces[0].openings[0].rings[0].ring_id == "RING_WIN_1"
    assert restored.installations[0].gml_id == "CHIMNEY_2"


def test_opening_shaped_object_directly_under_building_is_not_misread_as_installation():
    # cgml_semantic_type is also set on openings (Window/Door), not just
    # installations. An opening has no business being a direct child of the
    # Building outside its parent surface, but if one turns up there it must
    # not be silently folded into `installations`.
    building = FakeObject("Building", gml_id="BLDG_1")
    FakeObject(
        "Stray Window",
        obj_type="MESH",
        parent=building,
        gml_id="WIN_STRAY",
        cgml3_feature="Window",
        cgml_semantic_type="Window",
    )

    tree = semantic_tree.build_semantic_tree(building)

    assert tree.installations == ()
    assert tree.boundary_surfaces == ()


def test_ir_module_has_no_bpy_or_xml_imports():
    source = (ROOT / "shared" / "semantic_tree.py").read_text()
    assert "import bpy" not in source
    assert "xml.etree" not in source
    assert "lxml" not in source
