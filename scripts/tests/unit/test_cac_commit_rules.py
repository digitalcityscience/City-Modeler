"""CAC-P1.7 spike: the 9 polygon-identity commit-rule cases of PLAN.md §5,
each a deterministic unit test (go/no-go G1b). No Blender."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import commit_rules as cr  # noqa: E402
from shared.authoring_core.domain import records as r  # noqa: E402

# A flat roof panel, z=3, and a smaller flat "chimney hole" fully inside it -
# both planar in the same z=3 plane.
SQUARE = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 3.0), (0.0, 4.0, 3.0))
HOLE = ((1.0, 1.0, 3.0), (2.0, 1.0, 3.0), (2.0, 2.0, 3.0), (1.0, 2.0, 3.0))
MOVED_SQUARE = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.5, 4.5, 3.0), (0.0, 4.0, 3.0))
RESHAPED_SQUARE = ((0.0, 0.0, 3.0), (5.0, 0.0, 3.0), (5.0, 4.0, 3.0), (0.0, 4.0, 3.0))
BROKEN_SQUARE = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 4.0), (0.0, 4.0, 3.0))

# Two adjoining flat quads in the same z=3 plane (for the merge case), and
# their union as one merged quad.
QUAD_A = ((0.0, 0.0, 3.0), (2.0, 0.0, 3.0), (2.0, 4.0, 3.0), (0.0, 4.0, 3.0))
QUAD_B = ((2.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 3.0), (2.0, 4.0, 3.0))
MERGED_QUAD = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 3.0), (0.0, 4.0, 3.0))

# A quad in a different, still-individually-planar plane, for the knife
# split case (the two split-off parts no longer share one plane).
TILTED_QUAD = ((0.0, 0.0, 3.5), (4.0, 0.0, 3.5), (4.0, 4.0, 3.5), (0.0, 4.0, 3.5))
DISJOINT_QUAD = ((10.0, 10.0, 3.0), (12.0, 10.0, 3.0), (12.0, 12.0, 3.0), (10.0, 12.0, 3.0))


def roof_before(**overrides):
    kwargs = dict(
        polygon_id="P_ROOF", feature_id="ROOF1",
        exterior=r.RingRecord("P_ROOF_0_", "exterior", SQUARE),
        geometry_state="committed",
    )
    kwargs.update(overrides)
    return [r.PolygonRecord(**kwargs)]


def counting_id_factory(*ids):
    return iter(ids).__next__


def assert_valid(poly):
    assert r.validate_polygon(poly) == []


# 1. vertex move, same topology -> same polygon id, coords updated.
def test_vertex_move_keeps_id_and_updates_coords():
    before = roof_before()
    plan = cr.decide(before, [cr.FaceGroup("P_ROOF", MOVED_SQUARE)])

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    assert plan.kept[0].exterior.coords == MOVED_SQUARE
    assert plan.minted == () and plan.removed == () and plan.refused == ()
    assert any("kept" in note for note in plan.report)
    assert_valid(plan.kept[0])


# 2. triangulated planar polygon -> dissolved back into one polygon, same id.
def test_triangulate_then_dissolve_keeps_id():
    before = roof_before()
    plan = cr.decide(before, [cr.FaceGroup("P_ROOF", SQUARE)])

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    assert plan.kept[0].exterior.coords == SQUARE
    assert plan.minted == () and plan.removed == ()
    assert_valid(plan.kept[0])


# 3. knife split into two non-coplanar parts -> first keeps the id, second
#    gets a minted id (explicit, reported).
def test_knife_split_first_keeps_id_second_minted():
    before = roof_before()
    plan = cr.decide(
        before,
        [cr.FaceGroup("P_ROOF", SQUARE), cr.FaceGroup("P_ROOF", TILTED_QUAD)],
        id_factory=counting_id_factory("ID_SPLIT_1"),
    )

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    assert plan.kept[0].exterior.coords == SQUARE
    assert [p.polygon_id for p in plan.minted] == ["ID_SPLIT_1"]
    assert plan.minted[0].exterior.coords == TILTED_QUAD
    assert plan.minted[0].feature_id == "ROOF1"
    assert any("split" in note and "P_ROOF" in note for note in plan.report)
    assert_valid(plan.kept[0])
    assert_valid(plan.minted[0])


# 4. two disjoint groups with the same key -> split (as case 3).
def test_two_disjoint_groups_same_key_split():
    before = roof_before()
    plan = cr.decide(
        before,
        [cr.FaceGroup("P_ROOF", SQUARE), cr.FaceGroup("P_ROOF", DISJOINT_QUAD)],
        id_factory=counting_id_factory("ID_SPLIT_2"),
    )

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    assert [p.polygon_id for p in plan.minted] == ["ID_SPLIT_2"]
    assert plan.minted[0].exterior.coords == DISJOINT_QUAD
    assert plan.removed == () and plan.refused == ()


# 5. new face without a key -> new polygon id + default host-surface
#    semantics, reported.
def test_new_face_without_key_mints_id():
    before = roof_before()
    plan = cr.decide(
        before,
        [cr.FaceGroup("P_ROOF", SQUARE), cr.FaceGroup(None, DISJOINT_QUAD)],
        id_factory=counting_id_factory("ID_NEW_1"),
    )

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    assert [p.polygon_id for p in plan.minted] == ["ID_NEW_1"]
    assert plan.minted[0].feature_id == "ROOF1"  # default: the host feature's own id
    assert any("no key" in note for note in plan.report)
    assert_valid(plan.minted[0])


# 6. key disappeared -> polygon removed, reported.
def test_key_disappeared_is_removed():
    before = roof_before()
    plan = cr.decide(before, [])

    assert plan.removed == ("P_ROOF",)
    assert plan.kept == () and plan.minted == ()
    assert any("removed" in note and "P_ROOF" in note for note in plan.report)


# 7. interior ring (chimney hole) survives a reshape of the outer ring.
def test_interior_ring_survives_outer_reshape():
    before = roof_before(
        interiors=(r.RingRecord("P_ROOF_1_", "interior", HOLE),),
    )
    plan = cr.decide(before, [cr.FaceGroup("P_ROOF", RESHAPED_SQUARE, (HOLE,))])

    assert [p.polygon_id for p in plan.kept] == ["P_ROOF"]
    kept = plan.kept[0]
    assert kept.exterior.coords == RESHAPED_SQUARE
    assert len(kept.interiors) == 1
    assert kept.interiors[0].coords == HOLE
    assert kept.interiors[0].role == "interior"
    assert_valid(kept)


# 8. non-planar result beyond tolerance -> refusal with a reason.
def test_non_planar_result_is_refused():
    before = roof_before()
    plan = cr.decide(before, [cr.FaceGroup("P_ROOF", BROKEN_SQUARE)])

    assert plan.kept == () and plan.minted == () and plan.removed == ()
    assert len(plan.refused) == 1
    refusal = plan.refused[0]
    assert refusal.poly_key == "P_ROOF"
    assert "non-planar" in refusal.reason
    assert any("refused" in note for note in plan.report)


# 9. merged two coplanar polygons (dissolve) -> one survives, the other is
#    reported removed.
def test_merge_coplanar_polygons_one_survives_other_removed():
    before = [
        r.PolygonRecord(polygon_id="P_A", feature_id="ROOF1",
                         exterior=r.RingRecord("P_A_0_", "exterior", QUAD_A),
                         geometry_state="committed"),
        r.PolygonRecord(polygon_id="P_B", feature_id="ROOF1",
                         exterior=r.RingRecord("P_B_0_", "exterior", QUAD_B),
                         geometry_state="committed"),
    ]
    plan = cr.decide(before, [cr.FaceGroup("P_A", MERGED_QUAD)])

    assert [p.polygon_id for p in plan.kept] == ["P_A"]
    assert plan.kept[0].exterior.coords == MERGED_QUAD
    assert plan.removed == ("P_B",)
    assert plan.minted == () and plan.refused == ()
    assert any("removed" in note and "P_B" in note for note in plan.report)
    assert_valid(plan.kept[0])
