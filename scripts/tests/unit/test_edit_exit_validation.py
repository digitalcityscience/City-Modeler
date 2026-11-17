import sys
import types


if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.ModuleType("bpy")

if "mathutils" not in sys.modules:
    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = object
    sys.modules["mathutils"] = mathutils

from shared.edit_exit_validation import (
    ACTION_AMBIGUOUS,
    ACTION_NONE,
    ACTION_SYNC_OPENING_TO_RING,
    ACTION_SYNC_RING_TO_OPENING,
    OpeningSnapshot,
    WallRingSnapshot,
    changed_ring_keys,
    opening_state_changed,
    resolve_action,
    ring_key,
)


SQUARE = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))


def _shift(points, dx=0.0, dy=0.0, dz=0.0):
    return tuple((x + dx, y + dy, z + dz) for x, y, z in points)


# ---------------------------------------------------------------------------
# opening_state_changed
# ---------------------------------------------------------------------------
def test_opening_unchanged_is_not_flagged():
    before = OpeningSnapshot(verts=SQUARE)
    after = OpeningSnapshot(verts=SQUARE)
    assert opening_state_changed(before, after) is False


def test_opening_moved_beyond_tolerance_is_flagged():
    before = OpeningSnapshot(verts=SQUARE)
    after = OpeningSnapshot(verts=_shift(SQUARE, dx=0.5))
    assert opening_state_changed(before, after) is True


def test_opening_moved_within_tolerance_is_not_flagged():
    before = OpeningSnapshot(verts=SQUARE)
    after = OpeningSnapshot(verts=_shift(SQUARE, dx=0.001))
    assert opening_state_changed(before, after, tol=0.01) is False


def test_opening_topology_change_is_flagged_even_at_same_bbox():
    before = OpeningSnapshot(verts=SQUARE)
    after = OpeningSnapshot(verts=SQUARE + ((0.5, 0.5, 0.0),))
    assert opening_state_changed(before, after) is True


# ---------------------------------------------------------------------------
# changed_ring_keys
# ---------------------------------------------------------------------------
def test_no_ring_faces_changed():
    before = WallRingSnapshot(rings={"LINK_A": SQUARE})
    after = WallRingSnapshot(rings={"LINK_A": SQUARE})
    assert changed_ring_keys(before, after) == set()


def test_one_ring_face_moved_is_isolated_from_untouched_rings():
    before = WallRingSnapshot(rings={"LINK_A": SQUARE, "LINK_B": SQUARE})
    after = WallRingSnapshot(
        rings={"LINK_A": _shift(SQUARE, dx=0.2), "LINK_B": SQUARE}
    )
    assert changed_ring_keys(before, after) == {"LINK_A"}


def test_ring_face_removed_counts_as_changed():
    before = WallRingSnapshot(rings={"LINK_A": SQUARE})
    after = WallRingSnapshot(rings={})
    assert changed_ring_keys(before, after) == {"LINK_A"}


def test_ring_face_added_counts_as_changed():
    before = WallRingSnapshot(rings={})
    after = WallRingSnapshot(rings={"LINK_A": SQUARE})
    assert changed_ring_keys(before, after) == {"LINK_A"}


def test_ring_face_move_within_tolerance_is_not_flagged():
    before = WallRingSnapshot(rings={"LINK_A": SQUARE})
    after = WallRingSnapshot(rings={"LINK_A": _shift(SQUARE, dz=0.001)})
    assert changed_ring_keys(before, after, tol=0.01) == set()


# ---------------------------------------------------------------------------
# ring_key
# ---------------------------------------------------------------------------
def test_ring_key_prefers_the_opening_link():
    assert ring_key("OPENINGLINK_abc", 7) == "OPENINGLINK_abc"


def test_ring_key_falls_back_to_face_index_when_link_less():
    assert ring_key("", 7) == "idx:7"


# ---------------------------------------------------------------------------
# resolve_action - ticket 04 bullet 2/3's change-directed decision, as pure
# data (no bpy, no RingOpeningPair) so every branch is unit-testable.
# ---------------------------------------------------------------------------
def test_resolve_action_neither_changed_is_a_no_op():
    assert resolve_action(opening_changed=False, ring_changed=False) == ACTION_NONE


def test_resolve_action_opening_only_syncs_opening_to_ring():
    assert resolve_action(opening_changed=True, ring_changed=False) == ACTION_SYNC_OPENING_TO_RING


def test_resolve_action_ring_only_syncs_ring_to_opening():
    assert resolve_action(opening_changed=False, ring_changed=True) == ACTION_SYNC_RING_TO_OPENING


def test_resolve_action_both_changed_is_ambiguous_never_a_guess():
    assert resolve_action(opening_changed=True, ring_changed=True) == ACTION_AMBIGUOUS
