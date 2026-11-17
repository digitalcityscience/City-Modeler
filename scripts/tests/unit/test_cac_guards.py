"""Record-backed guard (CAC-P3.0) with fake objects: C1 closure, C6 flag-off
no-op, fail-closed malformed records, message format, no writes. No Blender."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import guards, storage  # noqa: E402
from shared.authoring_core.domain.records import FeatureRecord, RecordSet  # noqa: E402


class FakeObject:
    def __init__(self, name, props=None, parent=None):
        self.name = name
        self._props = dict(props or {})
        self.children = []
        self.parent = parent
        self.writes = 0
        if parent is not None:
            parent.children.append(self)

    def get(self, key, default=None):
        return self._props.get(key, default)

    def keys(self):
        return self._props.keys()

    def __contains__(self, key):
        return key in self._props

    def __setitem__(self, key, value):
        self.writes += 1
        self._props[key] = value

    def __delitem__(self, key):
        self.writes += 1
        del self._props[key]


def _give_record(obj):
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id=obj.get("gml_id"), kind="Window"),)))
    obj.writes = 0
    return obj


def _building():
    """Building > (wall_a > window_a), (wall_b > window_b), plus a detached opening
    whose host is named only by id."""
    b = FakeObject("Building", {"gml_id": "B1", "cgml3_feature": "Building"})
    wall_a = FakeObject("WallA", {"gml_id": "WA"}, parent=b)
    win_a = FakeObject("WinA", {"gml_id": "OA", "filling_parent_surface_id": "WA"}, parent=wall_a)
    wall_b = FakeObject("WallB", {"gml_id": "WB"}, parent=b)
    win_b = FakeObject("WinB", {"gml_id": "OB"}, parent=wall_b)
    loose = FakeObject("Loose", {"gml_id": "OL", "filling_parent_surface_id": "WB"})
    objs = {o.name: o for o in (b, wall_a, win_a, wall_b, win_b, loose)}
    by_id = {o.get("gml_id"): o for o in objs.values()}
    return objs, (lambda gml_id: [by_id[gml_id]] if gml_id in by_id else [])


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(guards.flag, "is_enabled", lambda: True)


def test_flag_off_is_noop_even_with_record(monkeypatch):
    monkeypatch.setattr(guards.flag, "is_enabled", lambda: False)
    objs, lookup = _building()
    _give_record(objs["WinA"])
    assert guards.refuse_if_record_backed([objs["WinA"]], "Align", lookup=lookup) is None


def test_no_record_passes(flag_on):
    objs, lookup = _building()
    assert guards.refuse_if_record_backed(list(objs.values()), "Align", lookup=lookup) is None


def test_record_on_self(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinA"])
    msg = guards.refuse_if_record_backed([objs["WinA"]], "Align Objects", lookup=lookup)
    assert msg.startswith("Align Objects: refused - 'WinA' (OA) is record-backed")


def test_record_on_ancestor_host_wall_and_building(flag_on):
    objs, lookup = _building()
    _give_record(objs["WallA"])
    assert "'WallA'" in guards.refuse_if_record_backed([objs["WinA"]], "Sync", lookup=lookup)
    objs, lookup = _building()
    _give_record(objs["Building"])
    assert "'Building'" in guards.refuse_if_record_backed([objs["WinB"]], "Sync", lookup=lookup)


def test_record_on_descendant_opening(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinA"])
    assert "'WinA'" in guards.refuse_if_record_backed([objs["WallA"]], "Cut", lookup=lookup)
    assert "'WinA'" in guards.refuse_if_record_backed([objs["Building"]], "Split", lookup=lookup)


def test_record_on_id_named_host(flag_on):
    objs, lookup = _building()
    _give_record(objs["WallB"])
    msg = guards.refuse_if_record_backed([objs["Loose"]], "Align", lookup=lookup)
    assert "'WallB' (WB)" in msg


def test_siblings_are_not_scanned(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinB"])
    assert guards.refuse_if_record_backed([objs["WinA"]], "Align", lookup=lookup) is None
    assert guards.refuse_if_record_backed([objs["WallA"]], "Cut", lookup=lookup) is None


@pytest.mark.parametrize("raw", [
    "{not json", "", json.dumps([1, 2]), json.dumps({"features": [{}]}), json.dumps({"features": "x"}),
])
def test_malformed_record_fails_closed(flag_on, raw):
    objs, lookup = _building()
    objs["WallA"][storage.RECORD_KEY] = raw
    assert not storage.has_record(objs["WallA"])  # readers: still "no record" (ADR 0009)
    msg = guards.refuse_if_record_backed([objs["WinA"]], "Stamp", lookup=lookup)
    assert msg.startswith("Stamp: refused - 'WallA' (WA) has a malformed authoring record")
    assert storage.RECORD_KEY in msg


def test_malformed_reported_first_and_extras_counted(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinA"])
    _give_record(objs["WinB"])
    objs["WallB"][storage.RECORD_KEY] = "{bad"
    msg = guards.refuse_if_record_backed([objs["Building"]], "Set LoD", lookup=lookup)
    assert "'WallB'" in msg and "malformed" in msg and msg.endswith("(+2 more)")


def test_none_and_duplicates_ignored(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinA"])
    msg = guards.refuse_if_record_backed([None, objs["WinA"], objs["WinA"], objs["WallA"]], "Join",
                                         lookup=lookup)
    assert not msg.endswith("more)")
    assert guards.refuse_if_record_backed([None], "Join", lookup=lookup) is None
    assert guards.refuse_if_record_backed([], "Join", lookup=lookup) is None


def test_missing_gml_id_is_named(flag_on):
    obj = _give_record(FakeObject("Mesh", {}))
    obj._props[storage.RECORD_KEY] = "{bad"
    assert "'Mesh' (no gml_id)" in guards.refuse_if_record_backed([obj], "Join", lookup=lambda _i: [])


def test_guard_never_writes(flag_on):
    objs, lookup = _building()
    _give_record(objs["WinA"])
    objs["WallB"]._props[storage.RECORD_KEY] = "{bad"
    guards.refuse_if_record_backed(list(objs.values()), "Align", lookup=lookup)
    assert all(o.writes == 0 for o in objs.values())


def test_internal_error_fails_closed(flag_on):
    def broken(_gml_id):
        raise RuntimeError("boom")

    objs, _lookup = _building()
    msg = guards.refuse_if_record_backed([objs["WinA"]], "Align", lookup=broken)
    assert msg == "Align: refused - record guard failed (boom); see the console"


def test_record_state():
    obj = FakeObject("O", {"gml_id": "X"})
    assert storage.record_state(obj) == storage.RECORD_NONE
    _give_record(obj)
    assert storage.record_state(obj) == storage.RECORD_OK
    obj._props[storage.RECORD_KEY] = "{bad"
    assert storage.record_state(obj) == storage.RECORD_MALFORMED
