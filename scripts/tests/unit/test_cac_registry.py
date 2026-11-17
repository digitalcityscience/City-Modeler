"""GmlRegistry (CAC-P2.2) with fake objects: identity rule, lazy rebuild,
stale-reference validation. No Blender (bpy is only imported by the
default object source and the handler registration)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import registry, storage  # noqa: E402
from shared.authoring_core.domain.records import FeatureRecord, RecordSet  # noqa: E402


class FakeObject:
    def __init__(self, name, props=None):
        self.name = name
        self.name_full = name
        self._props = dict(props or {})
        self.removed = False

    def _alive(self):
        if self.removed:
            raise ReferenceError("StructRNA of type Object has been removed")

    def get(self, key, default=None):
        self._alive()
        return self._props.get(key, default)

    def keys(self):
        self._alive()
        return self._props.keys()

    def __contains__(self, key):
        return key in self._props

    def __setitem__(self, key, value):
        self._props[key] = value

    def __delitem__(self, key):
        del self._props[key]


def _record(*ids, kind="WallSurface"):
    return RecordSet(features=tuple(FeatureRecord(gml_id=i, kind=kind) for i in ids))


def _with_record(obj, record_set):
    storage.write_record(obj, record_set)
    return obj


class Scene:
    def __init__(self, *objs):
        self.objs = list(objs)

    def __call__(self):
        return list(self.objs)


def _registry(scene, records=True):
    return registry.GmlRegistry(objects=scene, records_enabled=lambda: records)


# -- identity rule --------------------------------------------------------------

def test_identity_legacy_only():
    obj = FakeObject("w", {"gml_id": " W1 ", "cgml3_feature": "WallSurface"})
    assert registry.identity_of(obj, use_records=True) == ("W1", "WallSurface", False)


def test_identity_record_agrees_with_legacy():
    obj = _with_record(FakeObject("w", {"gml_id": "W1"}), _record("W0", "W1"))
    assert registry.identity_of(obj, use_records=True) == ("W1", "WallSurface", True)


def test_identity_record_only_object_uses_first_record_feature():
    obj = _with_record(FakeObject("w"), _record("R1", kind="Window"))
    assert registry.identity_of(obj, use_records=True) == ("R1", "Window", True)
    assert registry.identity_of(obj, use_records=False) == ("", "", False)


def test_identity_foreign_record_keeps_object_identity():
    obj = _with_record(FakeObject("copy", {"gml_id": "OWN"}), _record("SOURCE", kind="Window"))
    assert registry.identity_of(obj, use_records=True) == ("OWN", "", True)


def test_identity_malformed_record_is_ignored():
    obj = FakeObject("w", {"gml_id": "W1", storage.RECORD_KEY: "{not json"})
    assert registry.identity_of(obj, use_records=True) == ("W1", "", False)


# -- index ------------------------------------------------------------------------

def test_rebuild_skips_non_citygml_objects_and_lists_missing():
    scene = Scene(FakeObject("Camera"), FakeObject("w", {"gml_id": "W1"}),
                  FakeObject("nameless", {"cgml3_feature": "Window"}))
    index = _registry(scene).rebuild()
    assert index.by_id == {"W1": "w"}
    assert index.missing == ("nameless",)


def test_flag_off_ignores_record_only_objects_identity():
    scene = Scene(_with_record(FakeObject("r"), _record("R1")))
    off = _registry(scene, records=False).rebuild()
    assert off.by_id == {} and off.missing == ()  # records ignored: not a CityGML object
    assert _registry(scene, records=True).rebuild().by_id == {"R1": "r"}


def test_get_is_lazy_and_does_not_rebuild_on_clean_hits():
    wall = FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(wall))
    assert reg.get("W1") is wall
    assert reg.get("W1") is wall
    assert reg.rebuild_count == 1


def test_renamed_object_still_resolves():
    wall = FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(wall))
    reg.rebuild()
    wall.name = wall.name_full = "renamed"
    assert reg.get("W1") is wall


def test_removed_object_rebuilds_once_and_returns_none():
    wall = FakeObject("w", {"gml_id": "W1"})
    scene = Scene(wall)
    reg = _registry(scene)
    reg.rebuild()
    wall.removed = True
    scene.objs.remove(wall)
    assert reg.get("W1") is None
    assert reg.rebuild_count == 2


def test_identity_change_is_a_mismatch():
    wall = FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(wall))
    reg.rebuild()
    wall["gml_id"] = "W2"
    assert reg.get("W1") is None
    assert reg.get("W2") is wall


def test_miss_rebuilds_once_to_find_new_objects():
    scene = Scene()
    reg = _registry(scene)
    reg.rebuild()
    new = FakeObject("new", {"gml_id": "N1"})
    scene.objs.append(new)  # no mark_dirty: e.g. no depsgraph update fired
    assert reg.get("N1") is new
    assert reg.get("unknown") is None
    assert reg.rebuild_count == 3


def test_mark_dirty_rebuilds_on_next_get_only():
    wall = FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(wall))
    reg.get("W1")
    reg.mark_dirty()
    assert reg.dirty and reg.rebuild_count == 1
    reg.get("W1")
    assert not reg.dirty and reg.rebuild_count == 2


def test_get_all_lists_duplicates_in_key_order():
    a, b = FakeObject("w.001", {"gml_id": "W1"}), FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(a, b))
    assert reg.get_all("W1") == [b, a]
    assert reg.get("W1") is b
    assert reg.get_all("") == []


def test_clear_drops_references():
    wall = FakeObject("w", {"gml_id": "W1"})
    reg = _registry(Scene(wall))
    reg.get("W1")
    reg.clear()
    assert reg.dirty and reg._refs == {}


# -- resolve (P2.3) ------------------------------------------------------------------

def _resolver(*objs):
    scene = Scene(*objs)
    reg = _registry(scene)
    by_name = {o.name: o for o in objs}.get
    return lambda gml_id, name="": registry.resolve(gml_id, name, registry=reg, by_name=by_name)


def test_resolve_prefers_identity_over_name():
    renamed = FakeObject("renamed", {"gml_id": "W1"})
    impostor = FakeObject("old_name", {"gml_id": "W2"})
    resolve = _resolver(renamed, impostor)
    assert resolve("W1", "old_name") is renamed


def test_resolve_duplicate_id_prefers_the_named_candidate():
    a, b = FakeObject("w", {"gml_id": "W1"}), FakeObject("w.001", {"gml_id": "W1"})
    resolve = _resolver(a, b)
    assert resolve("W1", "w.001") is b
    assert resolve("W1", "gone") is a  # first by key


def test_resolve_miss_falls_back_to_a_compatible_name():
    no_id = FakeObject("wall", {"cgml3_feature": "WallSurface"})  # id came from its material
    resolve = _resolver(no_id)
    assert resolve("MATERIAL_SURFACE_ID", "wall") is no_id


def test_resolve_refuses_a_name_now_owned_by_another_feature():
    other = FakeObject("wall", {"gml_id": "W2"})
    resolve = _resolver(other)
    assert resolve("W1", "wall") is None


def test_resolve_without_id_is_a_plain_name_lookup():
    helper = FakeObject("cube")
    resolve = _resolver(helper)
    assert resolve("", "cube") is helper
    assert resolve("", "") is None
    assert resolve("", "missing") is None
