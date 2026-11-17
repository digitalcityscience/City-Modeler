"""B84: pending changes between the scene and 3DCityDB - classification and commit conflicts."""
from citydb import changes
from citydb.changes import CHANGED, DELETED, NEW, SYNCED, Change


def test_classify():
    feats = {"a": ("A", True), "b": ("B", False), "n": ("N", False)}
    got = {c.objectid: (c.kind, c.fid) for c in changes.classify(feats, {"a": 1, "b": 2, "gone": 3})}
    assert got == {"a": (SYNCED, 1), "b": (CHANGED, 2), "n": (NEW, 0), "gone": (DELETED, 3)}
    assert changes.summary(changes.classify(feats, {"a": 1, "b": 2, "gone": 3})) == "1 new · 1 changed · 1 deleted"
    assert changes.summary([Change(SYNCED, "a", 1)]) == "no changes"


def test_no_conflict_when_database_matches_what_was_loaded():
    todo = [Change(NEW, "n"), Change(CHANGED, "b", 2), Change(DELETED, "d", 3), Change(DELETED, "gone", 4),
            Change(CHANGED, "unknown-row", 0)]
    active = {"n": [], "b": [2], "d": [3], "gone": [], "unknown-row": [9]}
    assert changes.conflicts(todo, active) == {}


def test_conflicts():
    todo = [Change(NEW, "n"), Change(CHANGED, "b", 2), Change(CHANGED, "c", 5), Change(DELETED, "d", 3),
            Change(CHANGED, "two", 0)]
    got = changes.conflicts(todo, {"n": [7], "b": [8], "c": [], "d": [3, 4], "two": [1, 2]})
    assert set(got) == {"n", "b", "c", "d", "two"}
    assert "already in the database" in got["n"]
    assert "no longer in the database" in got["c"]
    assert "now id 8" in got["b"] and "now id 3, 4" in got["d"]
