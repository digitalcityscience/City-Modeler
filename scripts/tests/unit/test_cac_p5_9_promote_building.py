"""CAC-P5.9: atomic ``cgml.promote_building`` - candidate graph construction
and refusal rules. Pure Python, no bpy.

Scope: ``domain/promote_building.py`` (``build_promotion_candidate``,
``PromotionPlan``) only. Geometry commit (``blender/promote_building.py::
_commit_polygon``/``_commit_legacy_geometry``), the atomic write/rollback
loop, the registry refresh and the ``cgml.promote_building`` operator are
Blender-facing and covered by
``scripts/headless/test_cac_p5_9_promote_building.py`` instead (see that
file's docstring for why it could not be run in this environment).

Here, "legacy_committed" fixtures play the role ``blender/promote_building
.py::_commit_legacy_geometry`` fills in production: a legacy-derived
RecordSet whose not-yet-persisted polygons already carry committed
world-frame coordinates, exactly as the real adapter would hand it to
``build_promotion_candidate``.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import consistency  # noqa: E402
from shared.authoring_core.domain.building_root import (  # noqa: E402
    PERSISTED, TRANSIENT, resolve_building_root,
)
from shared.authoring_core.domain.export_eligibility import (  # noqa: E402
    IR_WITH_DOOR_PASSTHROUGH, UNSUPPORTED_OR_INCOMPLETE_FEATURE, can_export_building_from_ir,
)
from shared.authoring_core.domain.ir_adapter import building_ir_from_root  # noqa: E402
from shared.authoring_core.domain.promote_building import (  # noqa: E402
    PROMOTED, PromotionError, build_promotion_candidate,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
RING_B = ((2.0, 0.5, 0.0), (3.0, 0.5, 0.0), (3.0, 1.5, 0.0), (2.0, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))


def building(provenance="imported", **over):
    base = dict(gml_id="BLD1", kind="Building", provenance=provenance)
    base.update(over)
    return FeatureRecord(**base)


def wall(provenance="imported", lod=3, polygon_ids=("P_WALL",), **over):
    base = dict(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=lod, lod_origin="source",
        provenance=provenance, polygon_ids=polygon_ids,
    )
    base.update(over)
    return FeatureRecord(**base)


def window(gml_id="WIN_A", opening_link="LINK_A", host_id="WALL1", polygon_id="P_WIN_A",
           provenance="imported", lod=3, **over):
    base = dict(
        gml_id=gml_id, kind="Window", semantic_type="Window",
        parent_id=host_id, host_id=host_id, owner_id="BLD1",
        opening_link=opening_link, lod=lod, lod_origin="source",
        provenance=provenance, polygon_ids=(polygon_id,),
    )
    base.update(over)
    return FeatureRecord(**base)


def door(gml_id="DOOR1", host_id="WALL1", **over):
    base = dict(
        gml_id=gml_id, kind="Door", semantic_type="Door",
        parent_id=host_id, host_id=host_id, owner_id="BLD1", provenance="imported",
    )
    base.update(over)
    return FeatureRecord(**base)


def window_polygon(gml_id="WIN_A", polygon_id="P_WIN_A", coords=WIN_EXT, committed=True):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=gml_id,
        geometry_state="committed" if committed else "ids_only",
        exterior=RingRecord(f"{polygon_id}_0_", "exterior", coords if committed else ()),
    )


def wall_polygon(links_and_coords=(), polygon_id="P_WALL", feature_id="WALL1",
                  exterior_coords=WORLD_EXT, committed=True):
    rings = tuple(
        RingRecord(f"{polygon_id}_{i}_", "interior", coords, opening_link=link)
        for i, (link, coords) in enumerate(links_and_coords, start=1)
    )
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=feature_id,
        geometry_state="committed" if committed else "ids_only",
        exterior=RingRecord(f"{polygon_id}_0_", "exterior", exterior_coords if committed else ()),
        interiors=rings,
    )


def legacy_committed_one_window():
    """Case A: fully legacy Building with WallSurface + Window + Door, all
    polygons already committed (as ``_commit_legacy_geometry`` would hand
    them over)."""
    return RecordSet(
        features=(building(), wall(), window(), door()),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(),
        ),
    )


def legacy_committed_two_windows():
    return RecordSet(
        features=(building(), wall(), window(), window(gml_id="WIN_B", opening_link="LINK_B", polygon_id="P_WIN_B")),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A), ("LINK_B", RING_B))),
            window_polygon(),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B"),
        ),
    )


def persisted_wall_and_window(wall_provenance="authored", window_provenance="authored"):
    """The real Phase 4 case: record-backed Window + host wall, no Building record."""
    return RecordSet(
        features=(wall(provenance=wall_provenance, lod_origin="authored"),
                  window(provenance=window_provenance, lod_origin="authored")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )


# 1/2. fully legacy Building promotion; Building FeatureRecord created ------


def test_fully_legacy_building_is_promoted():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    assert plan.is_noop is False
    assert plan.root_source == TRANSIENT
    assert set(plan.changed_feature_ids) == {"BLD1", "WALL1", "WIN_A"}
    building_record = plan.candidate.feature("BLD1")
    assert building_record.gml_id == "BLD1"
    assert building_record.kind == "Building"
    assert building_record.provenance == PROMOTED


# 3/4. legacy WallSurface/Window ids preserved ------------------------------


def test_wall_and_window_ids_preserved():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    assert plan.candidate.feature("WALL1") is not None
    assert plan.candidate.feature("WIN_A") is not None


# 5/6. polygon/ring ids and committed world coordinates preserved ----------


def test_polygon_and_ring_ids_and_coords_preserved():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    wall_poly = plan.candidate.polygon("P_WALL")
    assert wall_poly.exterior.coords == WORLD_EXT
    assert wall_poly.interiors[0].ring_id == "P_WALL_1_"
    assert wall_poly.interiors[0].coords == RING_A
    win_poly = plan.candidate.polygon("P_WIN_A")
    assert win_poly.exterior.coords == WIN_EXT
    assert win_poly.geometry_state == "committed"


# 7. LoD preserved ------------------------------------------------------------


def test_lod_preserved():
    legacy = legacy_committed_one_window()
    legacy = RecordSet(
        features=tuple(f if f.gml_id != "WALL1" else __import__("dataclasses").replace(f, lod=2)
                        for f in legacy.features),
        polygons=legacy.polygons,
    )
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)
    assert plan.candidate.feature("WALL1").lod == 2


# 8. parent/host relationships preserved -------------------------------------


def test_parent_and_host_relationships_preserved():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    assert plan.candidate.feature("WALL1").parent_id == "BLD1"
    assert plan.candidate.feature("WIN_A").host_id == "WALL1"
    assert plan.candidate.feature("WIN_A").parent_id == "WALL1"


# 9/10. one opening_link pairing; two Windows on one wall --------------------


def test_one_window_opening_link_pairing():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    wall_poly = plan.candidate.polygon("P_WALL")
    assert [r.opening_link for r in wall_poly.interiors] == ["LINK_A"]


def test_two_windows_on_same_wall_both_promoted_with_distinct_rings():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_two_windows())
    assert {"WIN_A", "WIN_B"} <= set(plan.changed_feature_ids)
    wall_poly = plan.candidate.polygon("P_WALL")
    assert [r.opening_link for r in wall_poly.interiors] == ["LINK_A", "LINK_B"]
    ir = building_ir_from_root(plan.candidate, plan.candidate.feature("BLD1"))
    assert [o.gml_id for o in ir.boundary_surfaces[0].openings] == ["WIN_A", "WIN_B"]


# 11/12. existing record-backed Wall/Window preserved unchanged -------------


def test_existing_record_backed_window_preserved_unchanged():
    persisted = persisted_wall_and_window()
    legacy = legacy_committed_one_window()
    plan = build_promotion_candidate("BLD1", persisted, legacy)
    assert "WIN_A" not in plan.changed_feature_ids
    assert plan.candidate.feature("WIN_A") is persisted.feature("WIN_A")


def test_existing_record_backed_wall_preserved_unchanged():
    persisted = persisted_wall_and_window()
    legacy = legacy_committed_one_window()
    plan = build_promotion_candidate("BLD1", persisted, legacy)
    assert "WALL1" not in plan.changed_feature_ids
    assert plan.candidate.feature("WALL1") is persisted.feature("WALL1")


# 13. partially record-backed Building completed -----------------------------


def test_partially_record_backed_building_completed_with_second_window():
    # WALL1 + WIN_A already fully record-backed and self-consistent (its
    # stored polygon carries exactly WIN_A's ring, nothing dangling); a
    # second, still fully-legacy WALL2 (no windows) is the remaining slice a
    # partial promotion must complete.
    persisted = persisted_wall_and_window()
    legacy = RecordSet(
        features=(building(), wall(), window(),
                  wall(gml_id="WALL2", polygon_ids=("P_WALL2",))),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(),
            wall_polygon(polygon_id="P_WALL2", feature_id="WALL2", links_and_coords=()),
        ),
    )
    plan = build_promotion_candidate("BLD1", persisted, legacy)
    assert set(plan.changed_feature_ids) == {"BLD1", "WALL2"}
    assert plan.candidate.feature("WIN_A") is persisted.feature("WIN_A")  # untouched
    assert plan.candidate.feature("WALL1") is persisted.feature("WALL1")  # untouched
    assert plan.candidate.feature("WALL2").provenance == PROMOTED


def test_window_onto_already_persisted_wall_without_stored_ring_refuses():
    """The one deliberately-refused mixed sub-case (rule 5/9/15): the host
    wall is already record-backed but its stored polygon has no ring for the
    new Window's opening_link - completing the graph here would mean
    silently rewriting the authoritative wall record, so promotion refuses
    instead."""
    persisted = RecordSet(
        features=(wall(provenance="authored"),),
        polygons=(wall_polygon(links_and_coords=()),),  # no interior ring at all
    )
    legacy = RecordSet(
        features=(building(), wall(), window()),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    with pytest.raises(PromotionError, match="no interior ring"):
        build_promotion_candidate("BLD1", persisted, legacy)


# 14. conflicting existing record causes atomic refusal ----------------------


def test_conflicting_existing_record_causes_atomic_refusal():
    persisted = persisted_wall_and_window()
    legacy = legacy_committed_one_window()
    findings = (consistency.Finding(consistency.COORDS_STALE, consistency.ERROR, "moved", "WALL1"),)
    with pytest.raises(PromotionError, match="conflict"):
        build_promotion_candidate("BLD1", persisted, legacy, findings)


# 15/16. already-promoted Building no-op; repeated promotion, no id churn ----


def test_already_promoted_building_is_noop():
    first = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    second = build_promotion_candidate("BLD1", first.candidate, legacy_committed_one_window())
    assert second.is_noop is True
    assert second.changed_feature_ids == ()
    assert second.candidate.feature("BLD1") is first.candidate.feature("BLD1")


def test_repeated_promotion_causes_no_id_churn():
    first = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    second = build_promotion_candidate("BLD1", first.candidate, legacy_committed_one_window())
    assert {f.gml_id for f in first.candidate.features} == {f.gml_id for f in second.candidate.features}
    assert first.candidate.feature("WALL1") is second.candidate.feature("WALL1")
    assert first.candidate.feature("WIN_A") is second.candidate.feature("WIN_A")


# 17/18. Door does not block promotion; remains legacy-only ------------------


def test_door_does_not_block_promotion():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    assert plan.is_noop is False
    assert "DOOR1" not in plan.changed_feature_ids


def test_door_remains_legacy_only_after_promotion():
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    assert plan.candidate.feature("DOOR1") is None


# 19. unsupported non-Door content refuses before writes ---------------------


def test_unsupported_non_door_feature_refuses_before_writes():
    legacy = RecordSet(features=(
        building(), wall(), window(),
        FeatureRecord(gml_id="FURN1", kind="CityFurniture", parent_id="BLD1", owner_id="BLD1"),
    ), polygons=(
        wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon(),
    ))
    with pytest.raises(PromotionError, match="FURN1"):
        build_promotion_candidate("BLD1", RecordSet(), legacy)


# CAC-P6.7: a nested BuildingPart is a structural owner/container exactly
# like the Building root itself (legacy_reader._OWNER_KINDS) and must never
# itself be treated as "unsupported non-Door content" or be promoted as a
# generic feature - regression pin for the bug this task found and fixed
# (build_promotion_candidate used to refuse the whole Building whenever any
# BuildingPart was anywhere in scope).


def test_buildingpart_in_scope_does_not_block_promotion():
    legacy = RecordSet(features=(
        building(), wall(), window(),
        FeatureRecord(gml_id="PART1", kind="BuildingPart", parent_id="BLD1", owner_id="BLD1"),
    ), polygons=(
        wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon(),
    ))
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)
    assert plan.is_noop is False
    assert "WALL1" in plan.changed_feature_ids
    assert "WIN_A" in plan.changed_feature_ids


def test_buildingpart_itself_is_never_promoted_as_a_generic_feature():
    legacy = RecordSet(features=(
        building(), wall(), window(),
        FeatureRecord(gml_id="PART1", kind="BuildingPart", parent_id="BLD1", owner_id="BLD1"),
    ), polygons=(
        wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon(),
    ))
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)
    assert "PART1" not in plan.changed_feature_ids
    assert plan.candidate.feature("PART1") is None


def test_buildingpart_owned_roof_promotes_with_part_as_owner_and_parent():
    roof = FeatureRecord(
        gml_id="ROOF1", kind="RoofSurface", semantic_type="RoofSurface",
        parent_id="PART1", owner_id="PART1", lod=2, lod_origin="source",
        provenance="imported", polygon_ids=("P_ROOF",),
    )
    roof_polygon = PolygonRecord(
        polygon_id="P_ROOF", feature_id="ROOF1", geometry_state="committed",
        exterior=RingRecord("P_ROOF_0_", "exterior", WORLD_EXT),
    )
    legacy = RecordSet(features=(
        building(),
        FeatureRecord(gml_id="PART1", kind="BuildingPart", parent_id="BLD1", owner_id="BLD1"),
        roof,
    ), polygons=(roof_polygon,))

    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)

    promoted_roof = plan.candidate.feature("ROOF1")
    assert promoted_roof is not None
    assert promoted_roof.owner_id == "PART1"
    assert promoted_roof.parent_id == "PART1"
    assert "PART1" not in plan.changed_feature_ids
    assert plan.candidate.feature("PART1") is None


# 21. eligibility false before promotion, true after -------------------------


def test_eligibility_false_before_true_after_promotion():
    legacy = legacy_committed_one_window()
    before = can_export_building_from_ir("BLD1", RecordSet(), legacy)
    assert before.eligible is False
    # a transient root already resolves the Building's identity (P5.3); it is
    # the still-unpromoted Wall/Window graph that makes it ineligible.
    assert before.reason == UNSUPPORTED_OR_INCOMPLETE_FEATURE

    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)
    after = can_export_building_from_ir("BLD1", plan.candidate, legacy)
    assert after.eligible is True
    # legacy_committed_one_window() carries a Door: full IR + passthrough.
    assert after.reason == IR_WITH_DOOR_PASSTHROUGH
    assert after.door_passthrough_features == ("DOOR1",)


# 22. transient root before, persisted root after -----------------------------


def test_root_switches_from_transient_to_persisted_after_promotion():
    legacy = legacy_committed_one_window()
    _root, source_before = resolve_building_root(RecordSet(), legacy, "BLD1")
    assert source_before == TRANSIENT

    plan = build_promotion_candidate("BLD1", RecordSet(), legacy)
    root_after, source_after = resolve_building_root(plan.candidate, legacy, "BLD1")
    assert source_after == PERSISTED
    assert root_after.gml_id == "BLD1"


# 23. semantic IR identity stable across promotion ----------------------------


def test_ir_identity_stable_across_promotion():
    # WALL1 + WIN_A already fully record-backed and self-consistent; WALL2 +
    # WIN_B are still fully legacy - the still-to-be-promoted slice.
    persisted = persisted_wall_and_window()
    legacy = RecordSet(
        features=(
            building(), wall(), window(),
            wall(gml_id="WALL2", polygon_ids=("P_WALL2",)),
            window(gml_id="WIN_B", opening_link="LINK_B", host_id="WALL2", polygon_id="P_WIN_B"),
        ),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(),
            wall_polygon(polygon_id="P_WALL2", feature_id="WALL2", links_and_coords=(("LINK_B", RING_B),)),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B"),
        ),
    )
    root_before, _source = resolve_building_root(persisted, legacy, "BLD1")
    ir_before = building_ir_from_root(persisted, root_before)

    plan = build_promotion_candidate("BLD1", persisted, legacy)
    root_after, source_after = resolve_building_root(plan.candidate, legacy, "BLD1")
    assert source_after == PERSISTED
    ir_after = building_ir_from_root(plan.candidate, root_after)

    assert ir_before.gml_id == ir_after.gml_id == "BLD1"
    # Before promotion, only the already record-backed WALL1/WIN_A slice is
    # visible; promotion completes the graph with WALL2/WIN_B without
    # disturbing WALL1/WIN_A's identity.
    assert [s.gml_id for s in ir_before.boundary_surfaces] == ["WALL1"]
    assert [o.gml_id for o in ir_before.boundary_surfaces[0].openings] == ["WIN_A"]
    assert [s.gml_id for s in ir_after.boundary_surfaces] == ["WALL1", "WALL2"]
    assert [o.gml_id for o in ir_after.boundary_surfaces[0].openings] == ["WIN_A"]
    assert [o.gml_id for o in ir_after.boundary_surfaces[1].openings] == ["WIN_B"]


# misc ------------------------------------------------------------------------


def test_no_building_gml_id_refuses():
    with pytest.raises(PromotionError, match="no Building gml_id"):
        build_promotion_candidate("", RecordSet(), legacy_committed_one_window())


def test_invalid_candidate_graph_refuses():
    # a Window naming a polygon_id that has no matching PolygonRecord at all
    # makes the candidate structurally invalid (RecordSet.validate()).
    legacy = RecordSet(
        features=(building(), wall(), window(polygon_id="P_MISSING")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)),),
    )
    with pytest.raises(PromotionError, match="referenced but missing"):
        build_promotion_candidate("BLD1", RecordSet(), legacy)


# CAC-P5.10: a fully-fresh Case A promotion (wall and Window promoted
# together in the same pass - the plain-imported common case) must refuse
# before any write if the Window has no valid opening_link/host-ring
# pairing, instead of committing a candidate that later crashes
# building_ir_from_root. This is a *different* gap from the pre-existing
# "Window onto an already-persisted wall" mixed sub-case above (that one
# guards persisted.feature(host_id) is not None; this one covers the host
# wall being promoted in the very same call). ------------------------------


def test_promotion_refuses_fresh_window_with_no_opening_link_before_any_write():
    legacy = RecordSet(
        features=(building(), wall(), window(opening_link="")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    with pytest.raises(PromotionError, match="opening_link"):
        build_promotion_candidate("BLD1", RecordSet(), legacy)


def test_promotion_refuses_fresh_window_with_dangling_opening_link():
    legacy = RecordSet(
        features=(building(), wall(), window(opening_link="LINK_MISSING")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    with pytest.raises(PromotionError):
        build_promotion_candidate("BLD1", RecordSet(), legacy)


def test_promotion_success_implies_building_ir_from_root_succeeds():
    """The desired end-to-end invariant: a successful promotion's own
    candidate graph must always be accepted by building_ir_from_root - the
    same call promotion itself now runs as a preflight."""
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_one_window())
    building = plan.candidate.feature("BLD1")
    ir = building_ir_from_root(plan.candidate, building)  # must not raise
    assert ir.gml_id == "BLD1"


def test_promotion_with_valid_pairing_still_succeeds_no_regression():
    """Regression guard: the new preflight must not false-positive on the
    already-accepted good path."""
    plan = build_promotion_candidate("BLD1", RecordSet(), legacy_committed_two_windows())
    assert plan.is_noop is False
    assert set(plan.changed_feature_ids) >= {"BLD1", "WALL1", "WIN_A", "WIN_B"}
