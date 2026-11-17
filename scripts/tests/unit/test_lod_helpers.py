import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


lod_helpers = load_module("shared/lod_helpers.py", "lod_helpers_under_test")


class FakeMaterial(dict):
    pass


class FakeMeshData:
    def __init__(self, materials):
        self.materials = materials


class FakeObject(dict):
    def __init__(self, name, *, obj_type="EMPTY", parent=None, materials=None, **props):
        super().__init__(**props)
        self.name = name
        self.type = obj_type
        self.parent = parent
        self.children = []
        self.data = FakeMeshData(materials or [])
        if parent is not None:
            parent.children.append(self)


class FakeScene:
    def __init__(self, objects):
        self.objects = objects


def test_promote_opening_host_lod_updates_entire_top_level_hierarchy():
    top_material = FakeMaterial(lod=2)
    part_material = FakeMaterial(lod=2)
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=2)
    top_mesh = FakeObject("Building", obj_type="MESH", parent=hierarchy, materials=[top_material], lod=2)
    part_mesh = FakeObject(
        "BuildingPart",
        obj_type="MESH",
        parent=hierarchy,
        materials=[part_material],
        cgml3_feature="BuildingPart",
        lod=2,
    )

    changed = lod_helpers.promote_opening_host_lod(top_mesh)

    assert changed is True
    assert hierarchy["lod"] == 3
    assert top_mesh["lod"] == 3
    assert part_mesh["lod"] == 3
    assert top_material["lod"] == 3
    assert part_material["lod"] == 3


def test_normalize_scene_top_level_lods_raises_mixed_top_level_to_highest_lod():
    wall_material = FakeMaterial(lod=2)
    opening_material = FakeMaterial(lod=3)
    part_material = FakeMaterial(lod=2)
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=2)
    top_mesh = FakeObject("Building", obj_type="MESH", parent=hierarchy, materials=[wall_material, opening_material], lod=2)
    part_mesh = FakeObject(
        "BuildingPart",
        obj_type="MESH",
        parent=hierarchy,
        materials=[part_material],
        cgml3_feature="BuildingPart",
        lod=2,
    )
    scene = FakeScene([hierarchy, top_mesh, part_mesh])

    result = lod_helpers.normalize_scene_top_level_lods(scene)

    assert result["changed_count"] == 1
    assert result["normalized"][0]["lod"] == 3
    assert hierarchy["lod"] == 3
    assert top_mesh["lod"] == 3
    assert part_mesh["lod"] == 3
    assert wall_material["lod"] == 3
    assert opening_material["lod"] == 3
    assert part_material["lod"] == 3


def test_opening_lod_for_citygml3_keeps_lod2():
    wall_material = FakeMaterial(lod=2)
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=2)
    top_mesh = FakeObject("Building", obj_type="MESH", parent=hierarchy, materials=[wall_material], lod=2)

    target_lod = lod_helpers.opening_lod_for_version(top_mesh, "3.0")

    assert target_lod == 2


def test_opening_lod_for_citygml2_keeps_the_citygml2_policy():
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=4)
    top_mesh = FakeObject("Building", obj_type="MESH", parent=hierarchy, lod=4)

    assert lod_helpers.opening_lod_for_version(top_mesh, "2.0") == 3


def test_citygml3_opening_lod_raises_lod1_to_lod2_only():
    wall_material = FakeMaterial(lod=1)
    opening_material = FakeMaterial(lod=2)
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=1)
    top_mesh = FakeObject(
        "Building",
        obj_type="MESH",
        parent=hierarchy,
        materials=[wall_material, opening_material],
        lod=1,
    )

    target_lod = lod_helpers.opening_lod_for_version(top_mesh, "3.0")
    changed = lod_helpers.promote_opening_host_lod(top_mesh, opening_lod=target_lod)

    assert target_lod == 2
    assert changed is True
    assert hierarchy["lod"] == 2
    assert top_mesh["lod"] == 2
    assert wall_material["lod"] == 2
    assert opening_material["lod"] == 2


def test_lod_export_policy_name_is_the_current_unfiltered_behaviour():
    # Ticket 14: no LoD filtering exists in either exporter today, so the
    # named policy must describe that - not a filtering mode that doesn't exist.
    assert lod_helpers.LOD_EXPORT_POLICY_NAME == "Preserve Available Higher-LoD Details"
    assert isinstance(lod_helpers.LOD_EXPORT_POLICY_DESCRIPTION, str)
    assert lod_helpers.LOD_EXPORT_POLICY_DESCRIPTION


def test_mesh_receivers_and_descendants_keep_breadth_first_order():
    root = FakeObject("Root")
    mesh_a = FakeObject("Mesh A", obj_type="MESH", parent=root)
    empty = FakeObject("Empty", parent=root)
    mesh_b = FakeObject("Mesh B", obj_type="MESH", parent=root)
    nested_mesh = FakeObject("Nested Mesh", obj_type="MESH", parent=empty)

    assert [obj.name for obj in lod_helpers._descendants(root)] == [
        "Mesh A", "Empty", "Mesh B", "Nested Mesh"
    ]
    assert [obj.name for obj in lod_helpers._mesh_receivers(root)] == [
        "Mesh A", "Mesh B", "Nested Mesh"
    ]


def _promotion_scene(**wall_props):
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=3)
    wall = FakeObject("Wall", obj_type="MESH", parent=hierarchy, materials=[FakeMaterial(lod=3)],
                      cgml3_feature="WallSurface", **wall_props)
    window = FakeObject("Window", obj_type="MESH", parent=wall, materials=[FakeMaterial(lod=3)],
                        cgml3_feature="WindowSurface", gml_id="W1")
    return hierarchy, wall, window


def test_promotion_changes_is_a_dry_run_matching_the_real_promotion():
    """CAC-P4.10: the dry run names exactly what promote_opening_host_lod writes."""
    _hierarchy, wall, window = _promotion_scene(lod=3)
    lod, changed = lod_helpers.promotion_changes(wall, opening_lod=3)
    # The drawn Window carries no object-level lod of its own (a fill never
    # does): the promotion would add lod=3 to it, and to nothing else.
    assert lod == 3 and [o.name for o in changed] == ["Window"]
    assert "lod" not in window
    assert lod_helpers.promote_opening_host_lod(wall, opening_lod=3) is True
    assert window["lod"] == 3
    assert lod_helpers.promotion_changes(wall, opening_lod=3) == (3, ())


def test_promotion_changes_reports_the_raised_lod():
    _hierarchy, wall, _window = _promotion_scene(lod=2)
    lod, changed = lod_helpers.promotion_changes(wall, opening_lod=3)
    assert lod == 3 and {o.name for o in changed} == {"Wall", "Window"}


def test_promotion_changes_is_empty_when_the_feature_is_already_higher():
    hierarchy = FakeObject("Hierarchy", structure_type="hierarchical", lod=4)
    FakeObject("Wall", obj_type="MESH", parent=hierarchy, materials=[FakeMaterial(lod=2)], lod=2)
    assert lod_helpers.promotion_changes(hierarchy.children[0], opening_lod=3) == (None, ())
