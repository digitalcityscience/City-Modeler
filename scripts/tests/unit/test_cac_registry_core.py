"""GML index core (CAC-P2.1): deterministic gml_id -> key index. No Blender."""
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import registry_core as rc  # noqa: E402
from shared.authoring_core.domain.registry_core import GmlIndex, build_index  # noqa: E402


def test_empty_input_gives_empty_index():
    assert build_index([]) == GmlIndex()


def test_unique_ids():
    index = build_index([("b", "ID_B", "WallSurface"), ("a", "ID_A", "Building")])
    assert index.by_id == {"ID_A": "a", "ID_B": "b"}
    assert index.duplicates == {}
    assert index.missing == ()
    assert index.kinds == {"a": "Building", "b": "WallSurface"}


def test_duplicate_id_lists_all_keys_and_answers_with_smallest():
    index = build_index([("wall.001", "W1", "WallSurface"), ("wall", "W1", "WallSurface"),
                         ("roof", "R1", "RoofSurface")])
    assert index.duplicates == {"W1": ("wall", "wall.001")}
    assert index.by_id["W1"] == "wall"
    assert index.keys_for("W1") == ("wall", "wall.001")
    assert index.keys_for("R1") == ("roof",)
    assert index.keys_for("nope") == ()


def test_empty_and_whitespace_ids_are_missing_and_ids_are_stripped():
    index = build_index([("c", "", "Window"), ("b", "  ", "Door"), ("a", None, ""), ("d", " X ", "Window")])
    assert index.missing == ("a", "b", "c")
    assert index.by_id == {"X": "d"}
    assert index.kinds["a"] == ""


def test_duplicate_key_is_a_caller_error():
    with pytest.raises(ValueError):
        build_index([("a", "X", ""), ("a", "Y", "")])


def test_result_independent_of_input_order():
    entries = [("k1", "A", "WallSurface"), ("k2", "A", "WallSurface"), ("k3", "", "Window"),
               ("k4", "B", "RoofSurface"), ("k5", "A", "WallSurface")]
    results = {repr(build_index(p)) for p in itertools.permutations(entries)}
    assert len(results) == 1
    index = build_index(entries)
    assert index.duplicates == {"A": ("k1", "k2", "k5")}
    assert list(index.by_id) == ["A", "B"]


# -- findings (CAC-P2.4) ----------------------------------------------------------

def test_no_conflicts():
    assert rc.record_conflicts([("a", "A", ("A",)), ("b", "B", ("B", "B_SURF"))]) == ({}, {})
    assert rc.findings(build_index([("a", "A", "Window")]), [("a", "A", ("A",))]) == ()


def test_duplicate_record_after_shift_d():
    dup, foreign = rc.record_conflicts([("w.001", "W", ("W",)), ("w", "W", ("W",))])
    assert dup == {"W": ("w", "w.001")} and foreign == {}


def test_foreign_record_after_stamp():
    # AB-14: the stamped copy has its own gml_id but the source's record.
    dup, foreign = rc.record_conflicts([("src", "S1", ("S1",)), ("copy", "S2", ("S1",))])
    assert dup == {"S1": ("copy", "src")}
    assert foreign == {"copy": ("S2", ("S1",))}


def test_record_only_object_is_not_foreign():
    assert rc.record_conflicts([("r", "", ("R1",))]) == ({}, {})


def test_findings_codes_and_order():
    index = build_index([("w", "W", "WallSurface"), ("w.001", "W", "WallSurface"),
                         ("nameless", "", "Window"), ("copy", "S2", "Window")])
    records = [("w", "W", ("W",)), ("w.001", "W", ("W",)), ("copy", "S2", ("S1",))]
    found = rc.findings(index, records)
    assert [(f.code, f.gml_id, f.keys) for f in found] == [
        (rc.DUPLICATE_GML_ID, "W", ("w", "w.001")),
        (rc.MISSING_GML_ID, "", ("nameless",)),
        (rc.DUPLICATE_RECORD, "W", ("w", "w.001")),
        (rc.FOREIGN_RECORD, "S2", ("copy",)),
    ]
    assert all(f.message for f in found)
    assert rc.findings(index, list(reversed(records))) == found
