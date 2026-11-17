import sys
import types


if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.ModuleType("bpy")

if "mathutils" not in sys.modules:
    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = object
    sys.modules["mathutils"] = mathutils

from shared.ring_opening_sync import _collect_semantic_children


class FakeObject(dict):
    def __init__(self, name, *, obj_type="EMPTY", parent=None, **props):
        super().__init__(**props)
        self.name = name
        self.type = obj_type
        self.children = []
        if parent is not None:
            parent.children.append(self)


def test_collect_semantic_children_stops_at_nested_buildings_and_parts():
    building = FakeObject("Building", cgml3_feature="Building")
    wall = FakeObject(
        "Outer Wall", obj_type="MESH", parent=building, cgml3_feature="WallSurface"
    )
    nested_part = FakeObject("Nested Part", parent=building, cgml3_feature="BuildingPart")
    FakeObject(
        "Nested Wall", obj_type="MESH", parent=nested_part, cgml3_feature="WallSurface"
    )

    assert _collect_semantic_children(building) == [wall]
