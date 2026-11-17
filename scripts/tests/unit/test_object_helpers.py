import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


object_helpers = load_module("shared/object_helpers.py", "object_helpers_under_test")


class FakeObject:
    def __init__(self, name, *children):
        self.name = name
        self.children = list(children)


class BrokenGet:
    def get(self, key, default=None):
        raise RuntimeError("unavailable")


def test_safe_get_returns_value_or_default_for_inaccessible_blocks():
    assert object_helpers.safe_get({"gml_id": "BUILDING_1"}, "gml_id") == "BUILDING_1"
    assert object_helpers.safe_get({}, "missing", "fallback") == "fallback"
    assert object_helpers.safe_get(BrokenGet(), "gml_id", "fallback") == "fallback"


def test_breadth_first_descendants_preserves_child_list_order():
    deep_a = FakeObject("deep-a")
    first = FakeObject("first", deep_a)
    second = FakeObject("second")
    deep_b = FakeObject("deep-b")
    third = FakeObject("third", deep_b)
    root = FakeObject("root", first, second, third)

    assert [node.name for node in object_helpers.breadth_first_descendants(root)] == [
        "first",
        "second",
        "third",
        "deep-a",
        "deep-b",
    ]


def test_breadth_first_descendants_can_stop_at_a_domain_boundary():
    hidden = FakeObject("hidden")
    boundary = FakeObject("boundary", hidden)
    visible = FakeObject("visible")
    root = FakeObject("root", boundary, visible)

    result = list(
        object_helpers.breadth_first_descendants(
            root, should_descend=lambda node: node.name != "boundary"
        )
    )

    assert [node.name for node in result] == ["boundary", "visible"]
