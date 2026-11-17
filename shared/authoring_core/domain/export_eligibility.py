# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Whole-Building IR-exportability predicate (CAC-P5.3, PLAN.md P5.3
section "Revised dispatch predicate").

``can_export_building_from_ir`` answers, for one Building, whether it can
safely use the record-backed IR export path (design B, PLAN.md P5.0
revision 2: ``Decision.IR_FULL``/``Decision.IR_WITH_DOOR_PASSTHROUGH``) or
must fall back to the legacy mesh/material export path
(``Decision.LEGACY``). It is a pure function of facts a caller already
built - it never reads ``FeatureRecord.provenance`` as a completeness flag
(PLAN.md P5.0's rejected-design lesson: nothing in production ever sets a
Building-wide provenance, so that check would never fire for a real scene)
and never touches bpy/writer code itself.

Scope contract: ``persisted_record_set`` and ``legacy_record_set`` must
already be scoped to exactly ``building_gml_id``'s own descendants plus the
Building object itself - e.g. ``blender/building_root.py::building_scope``,
the whole-Building generalisation of
``blender/consistency_view.py::host_validation_scope``'s per-wall walk. A
caller that passes a wider, scene-level ``RecordSet`` gets an incorrect
(too strict) result, because every feature in ``legacy_record_set`` other
than the Building itself is treated as belonging to this Building.

Pure Python only: no bpy/bmesh/mathutils/gpu (rule R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import consistency
from .building_root import BuildingRootError, resolve_building_root, validate_building_root
from .ir_adapter import IRAdapterError, building_ir_from_root

# PLAN.md P5.0 revision 2, "Revised dispatch predicate": the kinds a
# record-backed Building's own boundary graph may consist of, plus the one
# named, bounded legacy passthrough exception (Door - design B). Any other
# kind under the Building forces the whole Building to Decision.LEGACY.
ALLOWED_IR_KINDS = ("WallSurface", "RoofSurface", "GroundSurface", "ClosureSurface", "Window")
PASSTHROUGH_KINDS = ("Door",)

NO_BUILDING_ROOT = "no_building_root"
INVALID_BUILDING_ROOT = "invalid_building_root"
BLOCKING_CONSISTENCY_FINDING = "blocking_consistency_finding"
UNCOMMITTED_TRANSFORM_DRIFT = "uncommitted_transform_drift"
UNSUPPORTED_OR_INCOMPLETE_FEATURE = "unsupported_or_incomplete_feature"
IR_BUILDER_INVARIANT_VIOLATION = "ir_builder_invariant_violation"
IR_FULL = "ir_full"
IR_WITH_DOOR_PASSTHROUGH = "ir_with_door_passthrough"


@dataclass(frozen=True)
class ExportEligibility:
    """Structured result of ``can_export_building_from_ir`` - a bare
    ``bool`` loses exactly the diagnostic a caller (a future P5.6
    dispatcher, or a user-facing report) needs to say *why* a Building
    fell back to legacy."""

    eligible: bool
    reason: str = ""
    message: str = ""
    unsupported_features: tuple = ()          # blocking non-Door gml_ids
    door_passthrough_features: tuple = ()      # Door gml_ids present (informational only)
    building_root_source: str = ""             # building_root.PERSISTED | building_root.TRANSIENT | ""


def can_export_building_from_ir(building_gml_id, persisted_record_set, legacy_record_set, findings=()):
    """``ExportEligibility`` for ``building_gml_id`` (CAC-P5.3).

    Checks, in order (first failure wins - never a best-effort partial
    result):

    1. Building identity is resolvable (``resolve_building_root``) -
       persisted root takes precedence over transient, per that function's
       own idempotency contract.
    2. The resolved root is a structurally valid ``FeatureRecord``
       (``validate_building_root``).
    3. No blocking (``ERROR`` severity) consistency finding anywhere in
       ``findings`` (``domain/consistency.py::is_blocking`` - duplicate/
       foreign records, missing ring pairing, dangling rings, mismatched
       relations, etc.).
    4. No uncommitted transform drift (``consistency.COORDS_STALE``) on any
       feature in scope - mirrors ``blender/consistency_view.py::
       export_preflight``'s P4.5 (D-7b) policy of hard-refusing export on
       drift, even though ``COORDS_STALE`` is a ``WARNING`` and not in
       ``consistency.BLOCKING``.
    5. Every non-Door, non-passthrough feature under the Building (from
       ``legacy_record_set``, i.e. every object that legacy-exists) is a
       supported kind (``ALLOWED_IR_KINDS``), has its own committed record
       in ``persisted_record_set`` (every polygon's
       ``geometry_state == "committed"``) - anything else forces
       ``Decision.LEGACY`` for the *whole* Building, never a partial IR
       result (PLAN.md P5.0: "the partial/mixed case is handled by
       construction, never silently"). Door is the sole named exception:
       present or not, it never blocks eligibility and is never itself
       checked for a record.

    6. (CAC-P5.10) The candidate graph actually builds: ``building_ir_
       from_root`` is run once, read-only, as a dry run over exactly the
       ``persisted_record_set``/``building`` a real export would use next
       (``export_dispatch.building_export_plan`` calls this function and
       then ``building_ir_from_root`` with the same two values). This is
       deliberately the *same* structural invariant the IR builder itself
       enforces (host_id resolution, ``opening_link`` presence, unique
       non-dangling ring pairing - ``ir_adapter._validate_opening_hosts``/
       ``_validate_host_ring_pairing``), reused rather than reimplemented,
       so eligibility can never again drift out of sync with what the
       builder actually requires. A Building whose every feature is
       "committed" by check 5 but whose Window/ring pairing is missing,
       dangling or ambiguous - the CAC-P5.10 bug: a plain-imported Window
       never gets ``cgml_opening_link`` (only the authored Draw/Stamp flow
       writes it, ``shared/facade_placement.py``) - now correctly gets
       ``reason == IR_BUILDER_INVARIANT_VIOLATION`` and falls back to
       legacy, instead of ``building_ir_from_root`` raising
       ``IRAdapterError`` later, uncaught, from inside the real writer.

    A Building with at least one Door and no other blocker is eligible
    with ``reason == IR_WITH_DOOR_PASSTHROUGH`` (that adapter is P5.5's
    job, not built here); with none, ``reason == IR_FULL``.
    """
    try:
        building, source = resolve_building_root(persisted_record_set, legacy_record_set, building_gml_id)
    except BuildingRootError as exc:
        return ExportEligibility(False, NO_BUILDING_ROOT, str(exc))

    problems = validate_building_root(building)
    if problems:
        return ExportEligibility(
            False, INVALID_BUILDING_ROOT, "; ".join(problems), building_root_source=source,
        )

    if consistency.is_blocking(findings):
        blocking = tuple(sorted({f.gml_id for f in findings if f.severity == consistency.ERROR and f.gml_id}))
        return ExportEligibility(
            False, BLOCKING_CONSISTENCY_FINDING,
            "at least one blocking record/legacy consistency finding in scope",
            unsupported_features=blocking, building_root_source=source,
        )

    drifted = tuple(sorted({f.gml_id for f in findings if f.code == consistency.COORDS_STALE and f.gml_id}))
    if drifted:
        return ExportEligibility(
            False, UNCOMMITTED_TRANSFORM_DRIFT,
            "uncommitted transform drift on at least one feature in scope",
            unsupported_features=drifted, building_root_source=source,
        )

    door_ids = []
    unsupported = []
    for feature in legacy_record_set.features:
        if feature.gml_id == building.gml_id:
            continue
        if feature.kind in PASSTHROUGH_KINDS:
            door_ids.append(feature.gml_id)
            continue
        if feature.kind not in ALLOWED_IR_KINDS:
            unsupported.append(feature.gml_id)
            continue
        record = persisted_record_set.feature(feature.gml_id)
        if record is None:
            unsupported.append(feature.gml_id)
            continue
        polys = persisted_record_set.polygons_of(feature.gml_id)
        if not polys or any(p.geometry_state != "committed" for p in polys):
            unsupported.append(feature.gml_id)
            continue

    if unsupported:
        return ExportEligibility(
            False, UNSUPPORTED_OR_INCOMPLETE_FEATURE,
            "at least one non-Door feature is unsupported or lacks a committed record",
            unsupported_features=tuple(sorted(unsupported)), building_root_source=source,
        )

    # Check 6 (CAC-P5.10): the IR builder's own structural invariants,
    # verified by actually running it read-only rather than duplicating its
    # rules here. Never raises outward - a violation is a normal ineligible
    # result, exactly like every other check above.
    try:
        building_ir_from_root(persisted_record_set, building)
    except IRAdapterError as exc:
        return ExportEligibility(
            False, IR_BUILDER_INVARIANT_VIOLATION, str(exc), building_root_source=source,
        )

    reason = IR_WITH_DOOR_PASSTHROUGH if door_ids else IR_FULL
    return ExportEligibility(
        True, reason, "",
        door_passthrough_features=tuple(sorted(door_ids)), building_root_source=source,
    )
