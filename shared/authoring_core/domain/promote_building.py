# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Controlled promotion of a legacy/partially record-backed Building into a
complete, persisted record graph (CAC-P5.9).

Pure (R1): given a Building's currently-persisted ``RecordSet``, a
legacy-derived ``RecordSet`` whose polygons have already been committed to
live world-frame coordinates by the caller (``blender/promote_building.py``
does that - this module never reads geometry itself), and consistency
findings already computed over that same scope, decides the complete
candidate record graph a promotion would write, or raises
``PromotionError`` before anything is written. Nothing here mutates
Blender, mints a new identity, or re-derives an already-persisted feature.

Supported Phase-5 promotion scope (frozen, PLAN.md P5.9): Building, boundary
surfaces, Windows. Door stays legacy-only (``FeatureKind.Door.enabled`` is
``False``) - never promoted, never checked for a record, never a blocker.
Any other non-Door feature kind under the Building refuses promotion
outright (`` build_promotion_candidate`` never produces a partial graph).
A nested BuildingPart (CAC-P6.7) is likewise never itself promoted or
checked for a record - it is a structural owner/container, not a boundary
surface/opening, exactly like the Building root it is nested under.

Three cases (see ``docs/agent/authoring-core/issues/CAC-P5.9.md``):

- **A - fully legacy**: every boundary surface/Window is freshly promoted
  from ``legacy_committed``.
- **B - partially record-backed**: an existing persisted feature is kept
  byte-identical (its ``FeatureRecord``/``PolygonRecord``s are never
  recreated); only the still-legacy remainder is promoted.
- **C - already promoted**: nothing to promote, ``PromotionPlan.is_noop``
  is ``True`` and the returned candidate is ``persisted`` itself - no new
  ids, no rewrites, no geometry churn.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import consistency
from .building_root import (
    PERSISTED, TRANSIENT, BuildingRootError, resolve_building_root, validate_building_root,
)
from .export_eligibility import ALLOWED_IR_KINDS, PASSTHROUGH_KINDS
from .ir_adapter import IRAdapterError, building_ir_from_root
from .records import RecordSet

PROMOTED = "promoted"

_BOUNDARY_KINDS = tuple(k for k in ALLOWED_IR_KINDS if k != "Window")


class PromotionError(ValueError):
    """A Building cannot be promoted as given; nothing has been written."""


@dataclass(frozen=True)
class PromotionPlan:
    """The complete candidate record graph for one Building's promotion.

    ``candidate`` is the full persisted-scope ``RecordSet`` after promotion
    (unchanged features carried over from ``persisted`` by identity,
    changed/new ones added). ``changed_feature_ids`` names only the
    features promotion actually needs to write - everything else in
    ``candidate`` is exactly what was already persisted, untouched.
    """

    building_gml_id: str
    candidate: RecordSet
    changed_feature_ids: tuple = ()
    root_source: str = TRANSIENT
    is_noop: bool = False

    def changed_features(self) -> tuple:
        ids = set(self.changed_feature_ids)
        return tuple(f for f in self.candidate.features if f.gml_id in ids)


def _blocking_message(findings) -> str:
    errors = [f for f in findings if f.severity == consistency.ERROR]
    return "; ".join(f"{f.code} ({f.gml_id or 'scene'}): {f.message}" for f in errors)


def build_promotion_candidate(building_gml_id, persisted, legacy_committed, findings=()) -> PromotionPlan:
    """The ``PromotionPlan`` for ``building_gml_id``, or raise ``PromotionError``.

    ``persisted``: every feature/polygon currently record-backed in the
    Building's scope (``blender/consistency_view.py::merged_record_set``).
    ``legacy_committed``: every feature legacy state expresses in that same
    scope (``domain/legacy_reader.py::read_legacy_scene``), with every
    not-yet-persisted feature's polygons already committed to live
    world-frame coordinates by the caller. ``findings``: consistency
    findings already computed over the same scope
    (``consistency_view.run_validation``) - a blocking one refuses the
    whole promotion atomically (rule 5: never silently repair or overwrite
    an existing record from the mesh).
    """
    building_gml_id = str(building_gml_id or "").strip()
    if not building_gml_id:
        raise PromotionError("no Building gml_id given")

    if consistency.is_blocking(findings):
        raise PromotionError(
            "existing record(s) conflict with current legacy state; promotion refused: "
            + _blocking_message(findings)
        )

    try:
        building, source = resolve_building_root(persisted, legacy_committed, building_gml_id)
    except BuildingRootError as exc:
        raise PromotionError(str(exc)) from exc
    problems = validate_building_root(building)
    if problems:
        raise PromotionError("; ".join(problems))

    # Rule 15: refuse whole-graph before any write if an unsupported non-Door
    # feature is anywhere in scope - never a partial promotion. A nested
    # BuildingPart (CAC-P6.7) is a structural owner/container exactly like
    # the Building root itself (``legacy_reader._OWNER_KINDS``) - it never
    # becomes its own promoted FeatureRecord (no FeatureKind entry exists
    # for it) and must never block promotion of the boundary surfaces it
    # owns, generalising the existing building_gml_id exclusion rather than
    # adding a second, BuildingPart-specific rule.
    unsupported = sorted(
        f.gml_id for f in legacy_committed.features
        if f.gml_id != building_gml_id
        and f.kind != "BuildingPart"
        and f.kind not in PASSTHROUGH_KINDS
        and f.kind not in ALLOWED_IR_KINDS
    )
    if unsupported:
        raise PromotionError(
            "unsupported non-Door feature(s) prevent promotion: " + ", ".join(unsupported)
        )

    candidate = persisted
    changed = []
    if source == TRANSIENT:
        building = replace(building, provenance=PROMOTED)
        candidate = candidate.with_feature(building)
        changed.append(building.gml_id)

    for feature in legacy_committed.features:
        if (
            feature.gml_id == building_gml_id
            or feature.kind in PASSTHROUGH_KINDS
            or feature.kind == "BuildingPart"
        ):
            continue
        if persisted.feature(feature.gml_id) is not None:
            continue  # Case B/C: already record-backed - preserved unchanged, never recreated

        if feature.kind == "Window":
            host_id = (feature.host_id or "").strip()
            host_record = persisted.feature(host_id)
            if host_record is not None and host_id not in changed:
                # The host wall is already record-backed and is *not* itself
                # being (re)promoted in this pass - its stored polygon must
                # already carry this Window's ring, or completing the graph
                # here would mean silently rewriting an authoritative record
                # (rule 5) instead of refusing outright (rule 9/15).
                host_polys = persisted.polygons_of(host_id)
                if not any(
                    any(r.opening_link == feature.opening_link for r in p.interiors)
                    for p in host_polys
                ):
                    raise PromotionError(
                        f"Window {feature.gml_id!r} cannot be promoted: its host wall "
                        f"{host_id!r} is already record-backed but its stored polygon "
                        f"carries no interior ring for opening_link {feature.opening_link!r}"
                    )

        promoted_feature = replace(feature, provenance=PROMOTED)
        candidate = candidate.with_feature(promoted_feature)
        changed.append(promoted_feature.gml_id)
        for polygon in legacy_committed.polygons_of(feature.gml_id):
            candidate = candidate.with_polygon(polygon)

    problems = candidate.validate()
    if problems:
        raise PromotionError("candidate record graph is invalid: " + "; ".join(problems))

    # CAC-P5.10: the candidate must also satisfy the IR builder's own
    # structural invariants (host_id resolution, opening_link presence,
    # unique non-dangling ring pairing) *before* anything is written -
    # reusing ``building_ir_from_root`` itself (the same dry run
    # ``export_eligibility.can_export_building_from_ir`` now runs) rather
    # than a second, independent promotion-only rule. Without this, a
    # fully-fresh Case A promotion (wall and Window promoted together in
    # the same pass) could commit a Window whose ``opening_link`` was
    # never set at all - the plain importer never writes
    # ``cgml_opening_link`` (only the authored Draw/Stamp flow does,
    # ``shared/facade_placement.py``) - which the P5.9-era check just
    # above this loop never covered (it only guards a Window promoted onto
    # an *already*-persisted host wall).
    try:
        building_ir_from_root(candidate, building)
    except IRAdapterError as exc:
        raise PromotionError(
            "candidate record graph fails the IR builder's structural "
            "invariants (host_id/opening_link/ring pairing), promotion "
            "refused before any write: " + str(exc)
        ) from exc

    return PromotionPlan(
        building_gml_id=building_gml_id,
        candidate=candidate,
        changed_feature_ids=tuple(changed),
        root_source=source,
        is_noop=not changed,
    )
