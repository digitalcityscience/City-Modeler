# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Explicit edit/commit lifecycle for a record-backed RoofSurface (CAC-P6.2).

    begin -> snapshot -> user edits -> validate -> commit OR cancel

This is the "smallest reusable single-surface edit session" P6.2 asks for -
deliberately *not* a copy of ``edit_session.py``'s Window+Wall pair
transaction. A RoofSurface is not paired with a host object the way a Window
is paired with its wall: there is no ``opening_link``, no ``host_id``
dual-write, no parent-ring recut. One object, one record, one commit.

Candidate-geometry extraction and polygon/ring identity matching (P6.2 §4-5)
reuse the *already generic* ``promote_building._commit_polygon``/
``_commit_generic_interior_rings`` (CAC-P6.1a) unchanged: the same
exterior/interior-ring resolution a promotion candidate uses is applied here
to an *already* record-backed RoofSurface's *existing* polygon/ring ids. No
id is ever minted by this module. ``_commit_polygon`` signals "could not
resolve" by returning the exact same ``PolygonRecord`` instance it was given
(see its own tests, ``test_cac_p6_1a_generic_interior_rings.py``); this
module treats that identity as an explicit refusal (P6.2 §5/§11: ambiguous
topology must refuse, never silently reassign ring identity or fall back to
stale coordinates).

Topology validation (P6.2 §6) is exactly ``RecordSet.validate()``
(``domain/records.py``'s P1.7 rules) - no new geometry validator. Consistency
(P3.3) and live-geometry reads are both scoped to the edited RoofSurface's
whole Building/BuildingPart root (``_geometry_scope``, ``building_root.
building_scope``), not to ``[roof]`` alone - see that function's docstring
for the real CS1 fixture that proved a single-object scope wrong (a
RoofSurface's own ``parent_id`` ancestor, and even a hole-bearing polygon's
own interior-ring faces, can live outside the one object carrying its
``gml_id``). This is a *read* scope only; a commit still writes only the
edited RoofSurface's own record - it is not a host-closure cross-feature
check the way Window's ring pairing is (CAC-P4.10).

Display regeneration (CAC-P6.3): every successful commit path below
(``commit``, ``validate_and_commit_existing_roof``, ``commit_transform_
drift``) calls ``display_regen.regenerate_or_restore_roof_display`` right
after ``_commit_roof`` writes the record, inside the same try block that
already restores the pre-commit mesh/record on any failure - a failed
display regeneration rolls back exactly like a failed record write, and
never leaves a stale/orphan display object next to a rolled-back record.

R4: nothing in this module is a handler; every write happens inside the
explicit ``commit``/``validate_and_commit_existing_roof`` call, driven by an
operator. R1 does not apply here - this is the ``blender/`` adapter layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import checksum, consistency
from . import checksum_view, display_regen, registry, storage
from .edit_session import CommitResult, _decide, _mesh_state, _restore_mesh_state

__all__ = [
    "RoofEditSessionError",
    "RoofEditSession",
    "begin",
    "commit",
    "cancel",
    "active_session",
    "commit_transform_drift",
    "validate_and_commit_existing_roof",
]

ROOF_KIND = "RoofSurface"


class RoofEditSessionError(ValueError):
    """begin/commit/cancel refused before mutating anything Blender-side."""


def _resolve_roof(gml_id, object_name=""):
    gml_id = str(gml_id or "").strip()
    if not gml_id:
        raise RoofEditSessionError("gml_id is required")
    roof = registry.resolve(gml_id, object_name)
    if roof is None or roof.type != "MESH":
        raise RoofEditSessionError(f"{gml_id!r}: no such object (already deleted?)")
    record_set = storage.read_record(roof)
    feature = record_set.feature(gml_id) if record_set is not None else None
    if feature is None:
        raise RoofEditSessionError(
            f"{gml_id!r}: object carries no authoring record - not a record-backed feature"
        )
    if feature.kind != ROOF_KIND:
        raise RoofEditSessionError(
            f"{gml_id!r}: kind {feature.kind!r} is not supported by this single-surface edit "
            f"lifecycle ({ROOF_KIND} only, CAC-P6.2)"
        )
    return roof, feature, record_set


def _relations(feature) -> tuple:
    # No host_id/opening_link: those are Window-only relations this module
    # never reads or writes (P6.2 "Window-specific behavior must remain
    # separate").
    return (("parent_id", feature.parent_id), ("owner_id", feature.owner_id), ("kind", feature.kind))


def _geometry_scope(roof) -> list:
    """``roof``'s whole Building/BuildingPart root scope - **not** just
    ``[roof]`` alone. Corrected from an earlier, narrower draft of this
    module after a real CS1 fixture proved it wrong two ways at once:

    1. ``domain/consistency.py::_referenced_object_findings`` can only
       confirm a RoofSurface's ``parent_id``/``owner_id`` "exists" if that
       Building/BuildingPart ancestor is part of the same live legacy scan -
       scoping to ``[roof]`` alone reads its own real ancestor as a false
       ``missing_object``/``parent_id_mismatch``.
    2. ``promote_building._commit_polygon``'s own docstring already warns
       "a feature's polygon materials are not guaranteed to all live on the
       one object carrying its gml_id" - and a real CS1 hole-bearing Roof
       polygon proved it: its two interior-ring (hole) faces live on
       sibling objects under the same Building, not on the RoofSurface's
       own mesh object, because ``CGML_OT_split_surfaces``' mesh-explosion
       step gives a topologically disconnected hole face its own object.
       Scoping ``consistency_view.geometry_fact`` to ``[roof]`` alone finds
       zero generic interior rings for such a polygon and every commit on
       it refuses (§5's explicit-refusal path), even for an edit that never
       touched the hole at all.

    The fix reuses the already-generic, already-existing
    ``building_root.building_scope`` (root object + every descendant) - the
    same scope shape ``promote_building``'s own callers already build for
    exactly this reason - rather than inventing a Roof-specific one. Only
    the *read* scope widens; ``_commit_roof`` still writes only the edited
    RoofSurface's own record."""
    from . import building_root
    from . import usecases as _usecases  # lazy: pulls in ring_opening_sync (imports bpy)

    ancestors = _usecases._ancestor_chain(roof)
    root = ancestors[-1] if ancestors else roof
    return building_root.building_scope(root)


def _geometry_snapshot(roof, feature) -> checksum.GeometrySnapshot:
    from . import consistency_view  # lazy: pulls in ring_opening_sync (imports bpy)

    fact = consistency_view.geometry_fact(_geometry_scope(roof))
    polygons = {pid: coords for pid, coords in fact.polygons.items() if pid in feature.polygon_ids}
    # Generic interior rings (CAC-P6.1a) have no stable key of their own;
    # synthesize one (polygon_id + positional index) purely so this
    # checksum's ``rings`` dict can see a hole's live coordinates change
    # between ``begin``/``commit`` - the key never leaves this function.
    rings = {}
    for pid in feature.polygon_ids:
        for index, coords in enumerate(fact.generic_rings.get(pid, ())):
            rings[f"{pid}#{index}"] = coords
    return checksum.GeometrySnapshot(polygons=polygons, rings=rings)


def _record_snapshot(roof, feature) -> checksum.RecordSnapshot:
    geometry = _geometry_snapshot(roof, feature)
    return checksum_view.record_snapshot(roof, feature.gml_id, geometry, _relations(feature))


def _committed_geometry_snapshot(feature, record_set) -> checksum.GeometrySnapshot:
    polygons, rings = {}, {}
    for p in record_set.polygons_of(feature.gml_id):
        if p.polygon_id not in feature.polygon_ids:
            continue
        polygons[p.polygon_id] = p.exterior.coords
        for index, ring in enumerate(p.interiors):
            rings[f"{p.polygon_id}#{index}"] = ring.coords
    return checksum.GeometrySnapshot(polygons=polygons, rings=rings)


def _committed_record_snapshot(roof, feature, record_set) -> checksum.RecordSnapshot:
    geometry = _committed_geometry_snapshot(feature, record_set)
    return checksum_view.record_snapshot(roof, feature.gml_id, geometry, _relations(feature))


@dataclass(frozen=True)
class RoofEditSession:
    """Everything needed to detect drift (checksum) and to restore the
    pre-edit mesh state (cancel) for one open single-surface edit session."""

    gml_id: str
    object_name: str
    checksum: str
    mesh_state: tuple = field(default_factory=lambda: ((), ()))


# Keyed by gml_id: only one open edit session per feature at a time. Module
# state, not a handler (R4) - every mutation happens inside an explicit
# begin/commit/cancel call, driven by an operator. Deliberately a *separate*
# table from ``edit_session._SESSIONS``: a Window session and a Roof session
# never collide even if (in principle) the same gml_id string somehow did.
_SESSIONS: dict = {}


def active_session(gml_id):
    return _SESSIONS.get(str(gml_id or "").strip())


def _refuse_if_inconsistent(gml_id, roof, operation) -> None:
    """P3.3 consistency check, scoped to ``roof``'s Building root (see
    ``_geometry_scope`` for why ``[roof]`` alone is not enough) - a
    RoofSurface has no sibling-*ring-pairing* closure to widen the scope to
    the way a Window's host wall does (CAC-P4.10); the wider scope here is
    purely so ``missing_object``/``parent_id_mismatch`` can resolve, not for
    any Roof-specific cross-feature check."""
    from . import consistency_view  # lazy: ring_opening_sync (its dependency) imports bpy

    findings = consistency_view.run_validation(_geometry_scope(roof))
    if consistency.is_blocking(findings):
        problems = "; ".join(f"{f.code}: {f.message}" for f in findings if f.severity == consistency.ERROR)
        raise RoofEditSessionError(f"{gml_id!r}: {operation} refused, consistency check failed: {problems}")


def _commit_roof(roof, feature) -> None:
    """Re-derive committed polygon geometry for ``roof`` from its current
    live mesh (P6.2 §4/§5), reusing the generic
    ``promote_building._commit_polygon``/``_commit_generic_interior_rings``
    matcher (CAC-P6.1a) unchanged. Existing polygon/ring identity (ids,
    roles, ``opening_link``) is always preserved; no id is ever minted here -
    only geometry may change.

    All-or-nothing per feature, mirroring ``_commit_polygon``'s own
    per-polygon all-or-nothing rule: if any polygon's live geometry cannot be
    resolved back onto its existing ring identity, the whole commit refuses
    (``RoofEditSessionError``) rather than write a partially-updated feature.
    """
    from . import consistency_view, promote_building  # lazy: import bpy transitively

    record_set = storage.read_record(roof)
    fact = consistency_view.geometry_fact(_geometry_scope(roof))

    new_record_set = record_set
    for polygon in record_set.polygons_of(feature.gml_id):
        committed = promote_building._commit_polygon(polygon, fact)
        if committed is polygon:
            # _commit_polygon's own contract: identical instance back means
            # it could not resolve this polygon's live geometry (missing
            # exterior/ring, degenerate face, ring-count mismatch, or a
            # mixed generic+opening_link interior set it refuses to guess
            # at) - an explicit refusal, never a silent reassignment or a
            # quiet revert to stale coordinates (P6.2 §5).
            raise RoofEditSessionError(
                f"polygon {polygon.polygon_id!r}: could not resolve live geometry back onto its "
                "existing ring identity (topology changed ambiguously, or a face is missing) - "
                "refusing rather than reassign ids"
            )
        new_record_set = new_record_set.with_polygon(committed)

    problems = new_record_set.validate()
    if problems:
        raise RoofEditSessionError(f"{feature.gml_id!r}: edited record would be invalid: {problems}")

    storage.write_record(roof, new_record_set)
    registry.REGISTRY.mark_dirty()


def begin(gml_id) -> RoofEditSession:
    """Snapshot a record-backed RoofSurface's committed state and open an
    editable session for it. Refuses (nothing touched) when the object is
    not record-backed or is not a RoofSurface."""
    gml_id = str(gml_id or "").strip()
    if gml_id in _SESSIONS:
        raise RoofEditSessionError(f"{gml_id!r}: an edit session is already open")

    roof, feature, _record_set = _resolve_roof(gml_id)
    snapshot = _record_snapshot(roof, feature)
    session = RoofEditSession(
        gml_id=gml_id,
        object_name=roof.name,
        checksum=checksum.compute_checksum(snapshot),
        mesh_state=_mesh_state(roof),
    )
    _SESSIONS[gml_id] = session
    return session


def validate_and_commit_existing_roof(gml_id, mutate, *, operation="semantic operation") -> CommitResult:
    """Apply one explicit RoofSurface mutation through the P6.2 single-surface
    seam. ``mutate(roof)`` may change only the live mesh. This then proves
    the resulting record is valid and commits it from that same live state.
    On any refusal the live mesh and record are restored and nothing is
    retained - the same shape as ``edit_session.validate_and_commit_existing_
    feature``, without the Window+Wall pairing it doesn't need.
    """
    gml_id = str(gml_id or "").strip()
    if gml_id in _SESSIONS:
        raise RoofEditSessionError(f"{gml_id!r}: an explicit edit session is already open")

    roof, feature, _record_set = _resolve_roof(gml_id)
    mesh_before = _mesh_state(roof)
    record_before = storage.read_record(roof)

    try:
        mutate(roof)
        _refuse_if_inconsistent(gml_id, roof, operation)
        _commit_roof(roof, feature)
        display_regen.regenerate_or_restore_roof_display(roof, feature)
    except Exception as exc:
        _restore_mesh_state(roof, mesh_before)
        if record_before is not None:
            storage.write_record(roof, record_before)
        if isinstance(exc, RoofEditSessionError):
            raise
        raise RoofEditSessionError(f"{gml_id!r}: {operation} refused: {exc}") from exc
    return CommitResult(status="committed", message=f"{gml_id!r}: {operation} committed", gml_id=gml_id)


def commit(gml_id) -> CommitResult:
    """Checksum -> validate -> commit (or clean no-op / clean refusal), for
    an already-open ``begin()`` session."""
    gml_id = str(gml_id or "").strip()
    session = _SESSIONS.get(gml_id)
    if session is None:
        raise RoofEditSessionError(f"{gml_id!r}: no open edit session (call begin first)")

    roof, feature, _record_set = _resolve_roof(gml_id, session.object_name)

    current_checksum = checksum.compute_checksum(_record_snapshot(roof, feature))
    if _decide(session.checksum, current_checksum) == "no_op":
        del _SESSIONS[gml_id]
        return CommitResult(status="no_op", message=f"{gml_id!r}: no changes to commit", gml_id=gml_id)

    mesh_before = _mesh_state(roof)
    record_before = storage.read_record(roof)
    try:
        _refuse_if_inconsistent(gml_id, roof, "commit")
        _commit_roof(roof, feature)
        display_regen.regenerate_or_restore_roof_display(roof, feature)
    except Exception as exc:
        _restore_mesh_state(roof, mesh_before)
        if record_before is not None:
            storage.write_record(roof, record_before)
        if isinstance(exc, RoofEditSessionError):
            raise
        raise RoofEditSessionError(f"{gml_id!r}: commit refused: {exc}") from exc

    del _SESSIONS[gml_id]
    return CommitResult(status="committed", message=f"{gml_id!r}: committed", gml_id=gml_id)


def cancel(gml_id) -> None:
    """Restore the pre-edit mesh state and close the session. Records were
    never touched between ``begin`` and ``cancel`` (only ``commit`` writes),
    so cancel only needs to restore the mesh ``begin`` snapshotted."""
    gml_id = str(gml_id or "").strip()
    session = _SESSIONS.pop(gml_id, None)
    if session is None:
        raise RoofEditSessionError(f"{gml_id!r}: no open edit session")

    roof = registry.resolve(gml_id, session.object_name)
    if roof is not None:
        _restore_mesh_state(roof, session.mesh_state)


def commit_transform_drift(gml_id) -> CommitResult:
    """Explicit resolution for native G/R/S transform drift on a
    record-backed RoofSurface (P6.2 §9), without requiring a prior
    ``begin()`` session - mirrors ``edit_session.commit_transform_drift``'s
    shape for the single-object case.

    - Checksum unchanged since the last commit: clean no-op.
    - Checksum changed but the result would be invalid/inconsistent:
      refused, nothing written.
    - Valid: the record is re-derived from the object's current (moved)
      world-frame geometry and written, preserving ``gml_id`` and every
      polygon/ring id - no new identity is minted.
    """
    gml_id = str(gml_id or "").strip()
    if gml_id in _SESSIONS:
        raise RoofEditSessionError(
            f"{gml_id!r}: an explicit edit session is already open - use commit/cancel instead"
        )

    roof, feature, record_set = _resolve_roof(gml_id)

    committed_checksum = checksum.compute_checksum(_committed_record_snapshot(roof, feature, record_set))
    current_checksum = checksum.compute_checksum(_record_snapshot(roof, feature))
    if _decide(committed_checksum, current_checksum) == "no_op":
        return CommitResult(status="no_op", message=f"{gml_id!r}: no transform drift to commit", gml_id=gml_id)

    mesh_before = _mesh_state(roof)
    record_before = storage.read_record(roof)
    try:
        _refuse_if_inconsistent(gml_id, roof, "transform-drift commit")
        _commit_roof(roof, feature)
        display_regen.regenerate_or_restore_roof_display(roof, feature)
    except Exception as exc:
        _restore_mesh_state(roof, mesh_before)
        if record_before is not None:
            storage.write_record(roof, record_before)
        if isinstance(exc, RoofEditSessionError):
            raise
        raise RoofEditSessionError(f"{gml_id!r}: transform-drift commit refused: {exc}") from exc

    return CommitResult(status="committed", message=f"{gml_id!r}: transform drift committed", gml_id=gml_id)
