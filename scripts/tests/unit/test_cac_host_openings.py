"""One host wall carrying several record-backed openings (CAC-P4.10). No Blender."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import consistency as c  # noqa: E402
from shared.authoring_core.domain import host_openings as ho  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WALL = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 0.0, 5.0), (0.0, 0.0, 5.0))
RING_A = ((1.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 0.0, 2.0), (1.0, 0.0, 2.0))
RING_A_MOVED = ((1.5, 0.0, 1.0), (2.5, 0.0, 1.0), (2.5, 0.0, 2.0), (1.5, 0.0, 2.0))
RING_B = ((5.0, 0.0, 1.0), (6.0, 0.0, 1.0), (6.0, 0.0, 2.0), (5.0, 0.0, 2.0))
RING_B_DRIFTED = ((5.2, 0.0, 1.0), (6.2, 0.0, 1.0), (6.2, 0.0, 2.0), (5.2, 0.0, 2.0))


def _ring(link, coords=(), ring_id=""):
    return RingRecord(ring_id=ring_id, role="interior", coords=coords, opening_link=link)


def _polygon(interiors, state="committed", exterior=WALL):
    return PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state=state,
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=exterior if state == "committed" else ()),
        interiors=tuple(interiors),
    )


def test_polygon_without_the_ring_is_returned_unchanged():
    stored = _polygon([_ring("LINK_B", RING_B, "P_WALL_1_")])
    assert ho.commit_host_polygon(stored, "LINK_A", WALL, {"LINK_A": RING_A}, stored) is stored


def test_sibling_ring_is_kept_verbatim_while_the_edited_ring_is_refreshed():
    stored = _polygon([_ring("LINK_A", RING_A, "P_WALL_1_"), _ring("LINK_B", RING_B, "P_WALL_2_")])
    live = {"LINK_A": RING_A_MOVED, "LINK_B": RING_B_DRIFTED}
    out = ho.commit_host_polygon(stored, "LINK_A", WALL, live, stored)
    assert out.geometry_state == "committed"
    assert out.interiors[0].coords == RING_A_MOVED and out.interiors[0].ring_id == "P_WALL_1_"
    # The sibling's live drift is not absorbed: its stored RingRecord is the same object.
    assert out.interiors[1] is stored.interiors[1]


def test_new_ring_appended_to_a_committed_host_keeps_existing_rings():
    stored = _polygon([_ring("LINK_A", RING_A, "P_WALL_1_")])
    fresh = _polygon([_ring("LINK_A", ring_id="P_WALL_1_"), _ring("LINK_B", ring_id="P_WALL_2_")], state="ids_only")
    out = ho.commit_host_polygon(fresh, "LINK_B", WALL, {"LINK_A": RING_A_MOVED, "LINK_B": RING_B}, stored)
    assert out.geometry_state == "committed"
    assert out.interiors[0] is stored.interiors[0]
    assert out.interiors[1].coords == RING_B and out.interiors[1].ring_id == "P_WALL_2_"
    assert RecordSet(features=(FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",)),),
                     polygons=(out,)).validate() == []


def test_first_commit_of_a_polygon_reads_every_ring_live():
    fresh = _polygon([_ring("LINK_A"), _ring("LINK_B")], state="ids_only")
    out = ho.commit_host_polygon(fresh, "LINK_A", WALL, {"LINK_A": RING_A, "LINK_B": RING_B}, None)
    assert [r.coords for r in out.interiors] == [RING_A, RING_B]
    assert [r.ring_id for r in out.interiors] == ["P_WALL_1_", "P_WALL_2_"]


def test_unresolvable_ring_leaves_an_invalid_ids_only_polygon():
    fresh = _polygon([_ring(""), _ring("LINK_A")], state="ids_only")
    out = ho.commit_host_polygon(fresh, "LINK_A", WALL, {"LINK_A": RING_A}, None)
    assert out.geometry_state == "ids_only"
    problems = RecordSet(features=(FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",)),),
                         polygons=(out,)).validate()
    assert any("coords present while geometry_state is 'ids_only'" in p for p in problems)


# --- the validator's scope contract behind AB-19 ---------------------------

def _window(gml_id, link):
    return FeatureRecord(gml_id=gml_id, kind="Window", semantic_type="Window", parent_id="WALL1",
                         host_id="WALL1", owner_id="BLD1", opening_link=link, lod=3, provenance="authored")


def _two_window_wall():
    return RecordSet(
        features=(FeatureRecord(gml_id="WALL1", kind="WallSurface", parent_id="BLD1", owner_id="BLD1",
                                polygon_ids=("P_WALL",)),),
        polygons=(_polygon([_ring("LINK_A", RING_A, "P_WALL_1_"), _ring("LINK_B", RING_B, "P_WALL_2_")]),),
    )


def test_ring_pairing_reads_a_scope_without_the_sibling_as_dangling():
    """Documents the contract: the RecordSet handed to the validator is treated
    as complete, so the adapter must pass a host-complete scope."""
    partial = _two_window_wall().with_feature(_window("WIN_A", "LINK_A"))
    codes = [(f.code, f.related) for f in c._ring_pairing_findings(partial)]
    assert codes == [(c.DANGLING_RING, ("P_WALL", "LINK_B"))]


def test_ring_pairing_is_clean_for_a_host_complete_scope():
    full = _two_window_wall().with_feature(_window("WIN_A", "LINK_A")).with_feature(_window("WIN_B", "LINK_B"))
    assert c._ring_pairing_findings(full) == []


# --- Case B: rings a record-backed create cannot represent (AB-20/20b) ------

def _legacy_polygon(pid, links):
    return PolygonRecord(polygon_id=pid, feature_id="WALL1",
                         interiors=tuple(_ring(link, ring_id=f"{pid}_{i}_") for i, link in enumerate(links, 1)))


def test_clean_or_fully_owned_host_has_no_unmanaged_rings():
    polygons = (_legacy_polygon("P1", ["LINK_A", "LINK_B"]), _legacy_polygon("P2", []))
    assert ho.unmanaged_host_rings(polygons, {"LINK_A", "LINK_B"}, "P1") == ()


def test_imported_ring_on_the_target_polygon_is_unmanaged():
    polygons = (_legacy_polygon("P1", [""]),)
    assert ho.unmanaged_host_rings(polygons, set(), "P1") == (("P1", "P1_1_", "", ho.UNLINKED),)


def test_imported_ring_on_another_polygon_is_allowed():
    polygons = (_legacy_polygon("P1", []), _legacy_polygon("P2", [""]))
    assert ho.unmanaged_host_rings(polygons, set(), "P1") == ()


def test_linked_ring_without_a_record_is_unmanaged_on_any_polygon():
    polygons = (_legacy_polygon("P1", ["LINK_A"]), _legacy_polygon("P2", ["LINK_LEGACY"]))
    assert ho.unmanaged_host_rings(polygons, {"LINK_A"}, "P1") == (
        ("P2", "P2_1_", "LINK_LEGACY", ho.UNRECORDED),
    )


# --- unrecorded_host: an opening record whose live host has no record -------

def test_opening_whose_live_host_has_no_record_is_blocking():
    records = RecordSet(features=(_window("WIN_A", "LINK_A"),))
    live = {"WIN_A", "WALL1"}
    findings = c._unrecorded_host_findings(records, lambda gid: c.LegacyFact(exists=gid in live))
    assert [(f.code, f.gml_id, f.related) for f in findings] == [(c.UNRECORDED_HOST, "WIN_A", ("WALL1",))]
    assert c.is_blocking(findings) and c.UNRECORDED_HOST in c.BLOCKING


def test_opening_with_recorded_or_missing_host_is_not_unrecorded_host():
    full = _two_window_wall().with_feature(_window("WIN_A", "LINK_A"))
    assert c._unrecorded_host_findings(full, lambda gid: c.LegacyFact(exists=True)) == []
    # A host that no longer exists at all is missing_object's business.
    alone = RecordSet(features=(_window("WIN_A", "LINK_A"),))
    assert c._unrecorded_host_findings(alone, lambda gid: c.LegacyFact(exists=gid == "WIN_A")) == []
