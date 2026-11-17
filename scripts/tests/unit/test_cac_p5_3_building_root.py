"""CAC-P5.3: transient Building root + IR-exportability predicate. Pure
Python, no bpy.

Scope: ``domain/building_root.py`` (transient root, persisted/transient
precedence) and ``domain/export_eligibility.py``
(``can_export_building_from_ir``), plus ``ir_adapter.py``'s new
``building_ir_from_root`` seam. No ``ir_to_groups``, no dispatcher, no Door
passthrough implementation, no ``promote_building``, no CRS/origin
transform, no Blender object.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import consistency  # noqa: E402
from shared.authoring_core.domain.building_root import (  # noqa: E402
    BUILDING_KINDS, BuildingRootError, PERSISTED, TRANSIENT,
    resolve_building_root, transient_building_record, validate_building_root,
)
from shared.authoring_core.domain.export_eligibility import (  # noqa: E402
    BLOCKING_CONSISTENCY_FINDING, INVALID_BUILDING_ROOT, IR_BUILDER_INVARIANT_VIOLATION,
    IR_FULL, IR_WITH_DOOR_PASSTHROUGH, NO_BUILDING_ROOT, UNCOMMITTED_TRANSFORM_DRIFT,
    UNSUPPORTED_OR_INCOMPLETE_FEATURE, can_export_building_from_ir,
)
from shared.authoring_core.domain.ir_adapter import (  # noqa: E402
    IRAdapterError, building_ir_from_root,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)
from shared.semantic_tree import BuildingNode  # noqa: E402

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))


def building(**over):
    base = dict(gml_id="BLD1", kind="Building", provenance="imported")
    base.update(over)
    return FeatureRecord(**base)


def wall(**over):
    base = dict(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WALL",),
    )
    base.update(over)
    return FeatureRecord(**base)


def window(gml_id="WIN_A", opening_link="LINK_A", host_id="WALL1", polygon_id="P_WIN_A", **over):
    base = dict(
        gml_id=gml_id, kind="Window", semantic_type="Window",
        parent_id=host_id, host_id=host_id, owner_id="BLD1",
        opening_link=opening_link, lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=(polygon_id,),
    )
    base.update(over)
    return FeatureRecord(**base)


def door(gml_id="DOOR1", host_id="WALL1", **over):
    base = dict(
        gml_id=gml_id, kind="Door", semantic_type="Door",
        parent_id=host_id, host_id=host_id, owner_id="BLD1",
        provenance="imported",
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


def persisted_wall_and_window():
    """The real Phase 4 case: record-backed Window + host wall, no Building record."""
    return RecordSet(
        features=(wall(), window()),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )


def legacy_scene_with_building():
    """A legacy-derived RecordSet, as ``legacy_reader.read_legacy_scene`` would
    produce it: every gml_id-bearing object gets a feature, including the
    Building - this is what P5.3's transient root is read from."""
    return RecordSet(features=(building(), wall(), window()))


# 1. transient Building root preserves existing Building gml_id -------------


def test_transient_root_preserves_existing_building_gml_id():
    root = transient_building_record(legacy_scene_with_building(), "BLD1")
    assert root.gml_id == "BLD1"
    assert root.kind == "Building"


# 2. transient root is not persisted -----------------------------------------


def test_transient_root_is_not_written_into_persisted_record_set():
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    root, source = resolve_building_root(persisted, legacy, "BLD1")
    assert source == TRANSIENT
    # the persisted RecordSet given to the resolver is untouched - no
    # Building feature was added to it as a side effect
    assert persisted.feature("BLD1") is None
    assert root not in persisted.features


# 3. valid record-backed Wall/Window slice builds Building IR successfully --


def test_transient_root_builds_building_ir():
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    root, _source = resolve_building_root(persisted, legacy, "BLD1")
    ir = building_ir_from_root(persisted, root)
    assert isinstance(ir, BuildingNode)
    assert ir.gml_id == "BLD1"
    assert [s.gml_id for s in ir.boundary_surfaces] == ["WALL1"]
    assert [o.gml_id for o in ir.boundary_surfaces[0].openings] == ["WIN_A"]


# 4. two Windows on one WallSurface remain correctly nested ------------------


def test_two_windows_on_one_wall_remain_nested_via_transient_root():
    persisted = RecordSet(
        features=(wall(), window(), window(gml_id="WIN_B", opening_link="LINK_B", polygon_id="P_WIN_B")),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A), ("LINK_B", RING_A))),
            window_polygon(),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B"),
        ),
    )
    legacy = legacy_scene_with_building()
    root, _source = resolve_building_root(persisted, legacy, "BLD1")
    ir = building_ir_from_root(persisted, root)
    wall_node = ir.boundary_surfaces[0]
    assert [o.gml_id for o in wall_node.openings] == ["WIN_A", "WIN_B"]
    assert all(o.host_id == "WALL1" for o in wall_node.openings)


# 5. missing Building identity refuses ---------------------------------------


def test_missing_building_identity_refuses():
    persisted = persisted_wall_and_window()
    legacy = RecordSet(features=(wall(), window()))  # no Building anywhere
    with pytest.raises(BuildingRootError, match="no legacy object"):
        resolve_building_root(persisted, legacy, "BLD1")


def test_can_export_reports_no_building_root():
    persisted = persisted_wall_and_window()
    legacy = RecordSet(features=(wall(), window()))
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == NO_BUILDING_ROOT


# 6. incomplete Window/Wall record graph is not IR-exportable ---------------


def test_incomplete_record_graph_not_exportable():
    persisted = RecordSet(
        features=(wall(), window()),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(committed=False),  # ids_only: not yet committed
        ),
    )
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == UNSUPPORTED_OR_INCOMPLETE_FEATURE
    assert "WIN_A" in result.unsupported_features


def test_wall_with_no_record_at_all_not_exportable():
    persisted = RecordSet(features=(window(),), polygons=(window_polygon(),))
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == UNSUPPORTED_OR_INCOMPLETE_FEATURE
    assert "WALL1" in result.unsupported_features


# 7. blocking consistency issue makes predicate false ------------------------


def test_blocking_consistency_finding_makes_predicate_false():
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    findings = (consistency.Finding(consistency.MISSING_OBJECT, consistency.ERROR, "gone", "WALL1"),)
    result = can_export_building_from_ir("BLD1", persisted, legacy, findings)
    assert result.eligible is False
    assert result.reason == BLOCKING_CONSISTENCY_FINDING
    assert result.unsupported_features == ("WALL1",)


def test_coords_stale_drift_makes_predicate_false_even_though_not_blocking():
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    findings = (consistency.Finding(consistency.COORDS_STALE, consistency.WARNING, "moved", "WALL1"),)
    assert consistency.is_blocking(findings) is False  # COORDS_STALE alone is not in consistency.BLOCKING
    result = can_export_building_from_ir("BLD1", persisted, legacy, findings)
    assert result.eligible is False
    assert result.reason == UNCOMMITTED_TRANSFORM_DRIFT
    assert result.unsupported_features == ("WALL1",)


# 8. unsupported non-Door feature type forces legacy fallback ---------------


def test_unsupported_non_door_feature_forces_fallback():
    persisted = persisted_wall_and_window()
    legacy = RecordSet(features=(
        building(), wall(), window(),
        FeatureRecord(gml_id="FURN1", kind="CityFurniture", parent_id="BLD1", owner_id="BLD1"),
    ))
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == UNSUPPORTED_OR_INCOMPLETE_FEATURE
    assert "FURN1" in result.unsupported_features


# 9. Door-only unsupported feature is classified as passthrough-eligible ----


def test_door_only_unsupported_feature_is_passthrough_eligible():
    persisted = persisted_wall_and_window()
    legacy = RecordSet(features=(building(), wall(), window(), door()))
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is True
    assert result.reason == IR_WITH_DOOR_PASSTHROUGH
    assert result.door_passthrough_features == ("DOOR1",)
    assert result.unsupported_features == ()


def test_no_door_is_full_ir_eligible():
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is True
    assert result.reason == IR_FULL
    assert result.door_passthrough_features == ()


# 10. persisted Building root takes precedence over transient root ----------


def test_persisted_building_root_takes_precedence_over_transient():
    persisted_building = building(provenance="promoted")
    persisted = RecordSet(features=(persisted_building, wall(), window()))
    legacy = legacy_scene_with_building()  # also has a "BLD1" feature
    root, source = resolve_building_root(persisted, legacy, "BLD1")
    assert source == PERSISTED
    assert root is persisted_building
    assert root.provenance == "promoted"


def test_persisted_root_used_means_transient_path_never_consulted():
    persisted_building = building(provenance="promoted")
    persisted = RecordSet(features=(persisted_building, wall(), window()))
    # legacy record set deliberately has NO Building feature at all - if the
    # transient path were consulted this would raise BuildingRootError
    legacy = RecordSet(features=(wall(), window()))
    root, source = resolve_building_root(persisted, legacy, "BLD1")
    assert source == PERSISTED
    assert root is persisted_building


# 11. transient and persisted roots produce the same semantic identity ------


def test_transient_and_persisted_roots_share_identity():
    transient_root = transient_building_record(legacy_scene_with_building(), "BLD1")
    persisted_root = building(provenance="promoted")
    assert transient_root.gml_id == persisted_root.gml_id == "BLD1"
    assert transient_root.kind == persisted_root.kind == "Building"

    persisted_rs = persisted_wall_and_window()
    ir_from_transient = building_ir_from_root(persisted_rs, transient_root)
    ir_from_persisted = building_ir_from_root(persisted_rs, persisted_root)
    assert ir_from_transient == ir_from_persisted


# 12. determinism independent of object/input ordering -----------------------


def test_resolve_building_root_deterministic_regardless_of_feature_order():
    legacy_forward = legacy_scene_with_building()
    legacy_reversed = RecordSet(features=tuple(reversed(legacy_forward.features)))
    persisted = persisted_wall_and_window()
    root_forward, source_forward = resolve_building_root(persisted, legacy_forward, "BLD1")
    root_reversed, source_reversed = resolve_building_root(persisted, legacy_reversed, "BLD1")
    assert root_forward == root_reversed
    assert source_forward == source_reversed


def test_can_export_deterministic_regardless_of_feature_order():
    persisted = persisted_wall_and_window()
    legacy_forward = RecordSet(features=(building(), wall(), window(), door()))
    legacy_reversed = RecordSet(features=tuple(reversed(legacy_forward.features)))
    result_forward = can_export_building_from_ir("BLD1", persisted, legacy_forward)
    result_reversed = can_export_building_from_ir("BLD1", persisted, legacy_reversed)
    assert result_forward == result_reversed


# 13/14/15. no-bpy guard, P5.2 tests, P3.5 shadow IR: covered by running the
# existing test_cac_domain_has_no_bpy.py / test_cac_p5_2_ir_builder.py /
# test_cac_ir_adapter.py files unchanged alongside this one (see
# docs/agent/authoring-core/issues/CAC-P5.3.md "Validation commands").


# BUILDING_KINDS / validate_building_root sanity ------------------------------


def test_building_kinds_includes_building_part():
    assert "Building" in BUILDING_KINDS
    assert "BuildingPart" in BUILDING_KINDS


def test_validate_building_root_reports_problems_for_a_malformed_root():
    broken = FeatureRecord(gml_id="", kind="Building")
    problems = validate_building_root(broken)
    assert problems
    assert any("gml_id" in p for p in problems)


def test_persisted_root_with_wrong_kind_refuses():
    persisted = RecordSet(features=(FeatureRecord(gml_id="BLD1", kind="WallSurface"),))
    legacy = legacy_scene_with_building()
    with pytest.raises(BuildingRootError, match="persisted kind"):
        resolve_building_root(persisted, legacy, "BLD1")


def test_building_ir_from_root_rejects_none():
    with pytest.raises(IRAdapterError, match="no Building root given"):
        building_ir_from_root(persisted_wall_and_window(), None)


# CAC-P5.10: eligibility must agree with the IR builder's own structural
# invariants (host_id resolution, opening_link presence, unique non-dangling
# ring pairing) - reusing building_ir_from_root as a dry run, never raising
# outward. ------------------------------------------------------------------


def test_window_without_opening_link_is_ineligible_not_a_crash():
    """The CAC-P5.10 bug: a plain-imported Window's record has a committed
    polygon but no opening_link at all (the plain importer never writes
    cgml_opening_link - only the authored Draw/Stamp flow does). Eligibility
    must say no, never let this reach building_ir_from_root uncaught."""
    persisted = RecordSet(
        features=(wall(), window(opening_link="")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == IR_BUILDER_INVARIANT_VIOLATION
    assert "opening_link" in result.message
    with pytest.raises(IRAdapterError):
        building_ir_from_root(persisted, resolve_building_root(persisted, legacy, "BLD1")[0])


def test_window_opening_link_naming_no_host_ring_is_ineligible():
    """opening_link present on the Window but no interior ring on the host
    wall's own polygon carries it (dangling on the ring side too, since
    nothing on the wall claims the wall's own unrelated ring)."""
    persisted = RecordSet(
        features=(wall(), window(opening_link="LINK_MISSING")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == IR_BUILDER_INVARIANT_VIOLATION


def test_duplicate_opening_link_ambiguous_pairing_is_ineligible():
    """Two Windows on the same host both claim the same opening_link."""
    win_b = window(gml_id="WIN_B", opening_link="LINK_A", polygon_id="P_WIN_B")
    persisted = RecordSet(
        features=(wall(), window(), win_b),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B"),
        ),
    )
    legacy = RecordSet(features=(building(), wall(), window(), win_b))
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is False
    assert result.reason == IR_BUILDER_INVARIANT_VIOLATION
    assert "ambiguous" in result.message


def test_valid_opening_link_pairing_is_still_eligible_not_invariant_violation():
    """Regression guard: the new check must not false-positive on the
    already-accepted good path (same fixture as test_no_door_is_full_ir_
    eligible, asserted against the new reason constant explicitly)."""
    persisted = persisted_wall_and_window()
    legacy = legacy_scene_with_building()
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is True
    assert result.reason != IR_BUILDER_INVARIANT_VIOLATION
    assert result.reason == IR_FULL


def test_door_passthrough_unaffected_by_the_new_invariant_check():
    """Door never carries opening_link/host-ring pairing (passthrough,
    never checked by the IR builder at all) - the new check must not
    regress the existing Door-passthrough-eligible path."""
    persisted = persisted_wall_and_window()
    legacy = RecordSet(features=(building(), wall(), window(), door()))
    result = can_export_building_from_ir("BLD1", persisted, legacy)
    assert result.eligible is True
    assert result.reason == IR_WITH_DOOR_PASSTHROUGH
