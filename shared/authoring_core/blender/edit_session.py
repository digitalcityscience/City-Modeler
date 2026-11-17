# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Explicit edit/commit lifecycle for a record-backed Window (CAC-P4.2).

    begin -> snapshot -> user edits -> validate -> commit OR cancel
             -> dual-write record + legacy mirror

The core primitive later Phase 4 tasks reuse (PLAN.md P4.2 "Important design
question"): F9 resize (P4.3), G/R/S commit (P4.5's ``commit_transform_drift``),
Align (P4.6) and manual Sync (P4.6) are all meant to call the same
begin/commit/cancel/checksum-compare shape this module establishes, rather
than each inventing its own record mutation logic. ``commit`` in particular
is written so a caller that only needs "checksum -> validate -> dual-write
commit" without a full begin/cancel session (P4.5's lightweight drift commit)
can reuse ``_commit_window_and_wall`` directly.

Dual-write only (PLAN.md §6 Phase 4 Goal, revised 2026-09-19): commit updates
the record *and* regenerates the legacy mesh/ring it mirrors via
``shared/ring_opening_sync.py``'s existing ``sync_opening_to_ring`` (keep the
edited Window where the user put it; recut the host wall's ring face to
match - the mirror image of what the edit-exit handler already does for
legacy objects). Since GS.2, commit also regenerates a separate,
record-only display mesh for the wall and its hosted Window
(``display_regen.regenerate_committed_display``) - additive, never touching
the legacy mesh/ring this dual-write already maintains.

R4: nothing in this module is a handler; every write happens inside the
explicit ``commit`` call, driven by an operator (``ops/authoring_core_ops.py``).
R1 does not apply here - this is the ``blender/`` adapter layer, not
``domain/``; it is allowed to read ``matrix_world``/mesh data directly
(mirrors ``usecases.py``'s own layering).

Import note: ``shared/ring_opening_sync.py`` (and, through it,
``consistency_view.py``/``usecases.py``) imports ``bpy`` unconditionally at
module scope, unlike this package's other ``blender/`` adapters
(``checksum_view.py``, ``guards.py``, ``registry.py``, ``storage.py``, all
lazy). Those three are therefore imported lazily here too, inside the
functions that need them, so this module - and its pure-part unit test -
stays importable without ``bpy`` (matches R1's spirit for the parts that can
manage it, though R1 itself only binds ``domain/``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import checksum, consistency
from . import checksum_view, registry, storage

__all__ = [
    "EditSessionError",
    "EditSession",
    "CommitResult",
    "begin",
    "commit",
    "cancel",
    "active_session",
    "commit_transform_drift",
    "validate_and_commit_existing_feature",
    "sync_existing_feature",
    "align_existing_window",
]


class EditSessionError(ValueError):
    """begin/commit/cancel refused before mutating anything Blender-side."""


def _flatten_matrix(matrix) -> tuple:
    return tuple(float(value) for row in matrix for value in row)


def _matrix_from_flat(flat):
    from mathutils import Matrix

    rows = [flat[i * 4:(i + 1) * 4] for i in range(4)]
    return Matrix(rows)


NO_OP = "no_op"
CHANGED = "changed"


def _decide(before_checksum, after_checksum) -> str:
    """Pure checksum-comparison decision commit() acts on - factored out so
    it is unit-testable without bpy (the surrounding resolve/validate/write
    steps all need a real or duck-typed Blender object)."""
    return NO_OP if before_checksum == after_checksum else CHANGED


def _mesh_state(obj):
    """``(vertex local coords, flattened matrix_basis)`` for ``obj``, or
    ``((), matrix)`` if it carries no mesh data."""
    mesh = obj.data
    coords = tuple(tuple(v.co) for v in mesh.vertices) if mesh is not None else ()
    return coords, _flatten_matrix(obj.matrix_basis)


def _restore_mesh_state(obj, state) -> None:
    coords, matrix_flat = state
    mesh = obj.data
    if mesh is not None and coords:
        for vertex, co in zip(mesh.vertices, coords):
            vertex.co = co
        mesh.update()
    obj.matrix_basis = _matrix_from_flat(matrix_flat)


def _geometry_snapshot(window, wall, feature, wall_feature) -> checksum.GeometrySnapshot:
    from . import consistency_view  # lazy: ring_opening_sync (its dependency) imports bpy

    fact = consistency_view.geometry_fact([window, wall])
    polygon_ids = set(feature.polygon_ids) | set(wall_feature.polygon_ids)
    polygons = {pid: coords for pid, coords in fact.polygons.items() if pid in polygon_ids}
    rings = {}
    if feature.opening_link and feature.opening_link in fact.rings:
        rings[feature.opening_link] = fact.rings[feature.opening_link]
    return checksum.GeometrySnapshot(polygons=polygons, rings=rings)


def _relations(feature) -> tuple:
    return (
        ("host_id", feature.host_id),
        ("parent_id", feature.parent_id),
        ("owner_id", feature.owner_id),
        ("opening_link", feature.opening_link),
        ("kind", feature.kind),
    )


def _record_snapshot(window, wall, feature, wall_feature) -> checksum.RecordSnapshot:
    """The ancestor-aware (CAC-P4.1) snapshot this session's checksum is
    computed from: ``window``'s own ``matrix_world`` (which already bakes in
    the wall's and every further ancestor's transform, since a Window fill
    is parented under its wall) plus the live world-space geometry of both
    the Window's own polygon(s) and the wall's ring for this opening."""
    geometry = _geometry_snapshot(window, wall, feature, wall_feature)
    return checksum_view.record_snapshot(window, feature.gml_id, geometry, _relations(feature))


def _committed_geometry_snapshot(window, wall, feature, wall_feature) -> checksum.GeometrySnapshot:
    """The counterpart to ``_geometry_snapshot`` sourced from the *stored*
    records instead of the live mesh (CAC-P4.5, D-7b): P3.2 always dual-writes
    world-space committed coordinates (PLAN.md Phase 4 Goal note), so this
    needs no live read at all - it is exactly what P3.3's ``coords_stale``
    already compares the live geometry against (``domain/consistency.py::
    _geometry_findings``), restated as a ``GeometrySnapshot`` so P4.5's drift
    commit can reuse the same before/after checksum comparison
    ``commit()``/``begin()`` use, instead of a second detection path."""
    window_records = storage.read_record(window)
    wall_records = storage.read_record(wall)
    polygons = {}
    for p in window_records.polygons_of(feature.gml_id):
        if p.polygon_id in feature.polygon_ids:
            polygons[p.polygon_id] = p.exterior.coords
    for p in wall_records.polygons_of(wall_feature.gml_id):
        if p.polygon_id in wall_feature.polygon_ids:
            polygons[p.polygon_id] = p.exterior.coords
    rings = {}
    if feature.opening_link:
        for p in wall_records.polygons_of(wall_feature.gml_id):
            for ring in p.interiors:
                if ring.opening_link == feature.opening_link:
                    rings[feature.opening_link] = ring.coords
    return checksum.GeometrySnapshot(polygons=polygons, rings=rings)


def _committed_record_snapshot(window, wall, feature, wall_feature) -> checksum.RecordSnapshot:
    """Last-committed counterpart to ``_record_snapshot``, built from storage
    rather than a live mesh read. ``window``'s own current ``matrix_world``
    is used for the transform field on both sides of the comparison
    (``commit_transform_drift`` compares this against ``_record_snapshot``'s
    live snapshot of the *same* object) so it always cancels out - only the
    geometry/relations actually sourced differently can make the checksums
    differ, which is exactly the drift this function exists to expose."""
    geometry = _committed_geometry_snapshot(window, wall, feature, wall_feature)
    return checksum_view.record_snapshot(window, feature.gml_id, geometry, _relations(feature))


@dataclass(frozen=True)
class EditSession:
    """Everything needed to detect drift (checksum) and to restore the
    pre-edit display state (cancel) for one open edit session."""

    gml_id: str
    window_name: str
    wall_gml_id: str
    wall_name: str
    checksum: str
    window_mesh_state: tuple = field(default_factory=lambda: ((), ()))
    wall_mesh_state: tuple = field(default_factory=lambda: ((), ()))


@dataclass(frozen=True)
class CommitResult:
    status: str          # "no_op" | "committed"
    message: str
    gml_id: str = ""


# Keyed by gml_id: only one open edit session per feature at a time. Module
# state, not a handler (R4) - every mutation happens inside an explicit
# begin/commit/cancel call, driven by an operator.
_SESSIONS: dict = {}


def active_session(gml_id):
    return _SESSIONS.get(str(gml_id or "").strip())


def _resolve_window_and_wall(gml_id, window_name="", wall_gml_id="", wall_name=""):
    window = registry.resolve(gml_id, window_name)
    if window is None or window.type != "MESH":
        raise EditSessionError(f"{gml_id!r}: no such object (already deleted?)")
    record_set = storage.read_record(window)
    feature = record_set.feature(gml_id) if record_set is not None else None
    if feature is None:
        raise EditSessionError(
            f"{gml_id!r}: object carries no authoring record - not a record-backed feature"
        )
    if feature.kind != "Window":
        raise EditSessionError(
            f"{gml_id!r}: kind {feature.kind!r} is not supported by the edit/commit lifecycle "
            "(Window only, CAC-P4.2)"
        )
    host_id = wall_gml_id or feature.host_id
    if not host_id:
        raise EditSessionError(f"{gml_id!r}: record has no host_id")
    wall = registry.resolve(host_id, wall_name or (window.parent.name if window.parent else ""))
    if wall is None or wall.type != "MESH":
        raise EditSessionError(f"host {host_id!r}: object no longer exists")
    wall_record_set = storage.read_record(wall)
    wall_feature = wall_record_set.feature(host_id) if wall_record_set is not None else None
    if wall_feature is None:
        raise EditSessionError(
            f"host {host_id!r}: object carries no authoring record (legacy-only host); "
            "the edit/commit lifecycle requires both the Window and its host wall to be "
            "record-backed"
        )
    if feature.host_id != wall_feature.gml_id or feature.host_id != feature.parent_id:
        raise EditSessionError(
            f"{gml_id!r}: host_id {feature.host_id!r} / parent_id {feature.parent_id!r} "
            f"do not agree with host wall {wall_feature.gml_id!r}"
        )
    return window, feature, wall, wall_feature


def begin(gml_id) -> EditSession:
    """Snapshot a record-backed Window's committed state (P4.1 checksum) and
    open an editable session for it.

    Refuses (``EditSessionError``, nothing touched) when the object is not
    record-backed, is not a Window, or its host WallSurface is legacy-only
    (no record) - editing a record-backed Window whose host has no record
    to dual-write into is not a coherent session (acceptance: "begin on
    legacy-only Window refuses / no-ops").
    """
    gml_id = str(gml_id or "").strip()
    if not gml_id:
        raise EditSessionError("gml_id is required")
    if gml_id in _SESSIONS:
        raise EditSessionError(f"{gml_id!r}: an edit session is already open")

    window, feature, wall, wall_feature = _resolve_window_and_wall(gml_id)

    snapshot = _record_snapshot(window, wall, feature, wall_feature)
    session = EditSession(
        gml_id=gml_id,
        window_name=window.name,
        wall_gml_id=wall_feature.gml_id,
        wall_name=wall.name,
        checksum=checksum.compute_checksum(snapshot),
        window_mesh_state=_mesh_state(window),
        wall_mesh_state=_mesh_state(wall),
    )
    _SESSIONS[gml_id] = session
    return session


def _commit_window_and_wall(window, wall, feature, wall_feature):
    """Re-derive committed records for ``window``/``wall`` from their *current*
    legacy state (mirrors ``usecases.create_feature``'s own dual-write
    derivation - a fresh read, not hand-assembled beside the mesh, so the
    two cannot independently drift) and write them both.

    Callers must have already confirmed the edit is valid. Identity is
    preserved: both features keep their existing ``gml_id`` - this function
    never mints one.
    """
    from dataclasses import replace

    from . import usecases as _usecases  # lazy: pulls in ring_opening_sync (imports bpy)

    scene_objects = [wall, window, *_usecases._ancestor_chain(wall)]
    legacy_scene = _usecases.read_legacy_scene(_usecases.legacy_views.scene_views(scene_objects))

    fresh_window = legacy_scene.records.feature(feature.gml_id)
    fresh_wall = legacy_scene.records.feature(wall_feature.gml_id)
    if fresh_window is None or fresh_wall is None:
        raise EditSessionError(
            "internal error: legacy state could not be read back for the edited feature"
        )

    window_record_set = storage.read_record(window)
    stored_window_feature = window_record_set.feature(feature.gml_id)
    window_polygons = tuple(
        _usecases._commit_window_polygon(window, p)
        for p in window_record_set.polygons_of(feature.gml_id)
    )
    new_window_feature = replace(
        stored_window_feature,
        parent_id=fresh_window.parent_id,
        owner_id=fresh_window.owner_id,
        host_id=fresh_window.host_id,
        opening_link=fresh_window.opening_link,
        polygon_ids=tuple(p.polygon_id for p in window_polygons),
    )
    new_window_record_set = window_record_set.with_feature(new_window_feature)
    for polygon in window_polygons:
        new_window_record_set = new_window_record_set.with_polygon(polygon)
    problems = new_window_record_set.validate()
    if problems:
        raise EditSessionError(f"{feature.gml_id!r}: edited record would be invalid: {problems}")

    opening_link = new_window_feature.opening_link
    ring_faces_by_link = {
        link: tuple(tuple(v) for v in verts)
        for _idx, verts, link in _usecases.wall_interior_ring_faces(wall) if link
    }
    wall_record_set = storage.read_record(wall)
    # Each stored polygon is its own ``stored`` copy: only this Window's ring
    # (and the exterior) is re-read live; sibling Windows' RingRecords are
    # kept exactly as stored (CAC-P4.10, mutation stays feature-specific).
    wall_polygons = {
        p.polygon_id: _usecases._commit_wall_polygon(wall, p, opening_link, ring_faces_by_link, p)
        for p in wall_record_set.polygons_of(wall_feature.gml_id)
    }
    new_wall_record_set = wall_record_set.with_feature(
        replace(wall_record_set.feature(wall_feature.gml_id), polygon_ids=tuple(wall_polygons.keys()))
    )
    for polygon in wall_polygons.values():
        new_wall_record_set = new_wall_record_set.with_polygon(polygon)
    problems = new_wall_record_set.validate()
    if problems:
        raise EditSessionError(f"host {wall_feature.gml_id!r}: edited record would be invalid: {problems}")

    storage.write_record(window, new_window_record_set)
    storage.write_record(wall, new_wall_record_set)
    registry.REGISTRY.mark_dirty()


def _refuse_if_host_inconsistent(gml_id, wall, wall_feature, operation) -> None:
    """Raise ``EditSessionError`` if the host wall's closure has a blocking
    P3.3 finding (CAC-P4.10, AB-19).

    The closure is host-complete (``consistency_view.host_validation_scope``):
    the wall, its ancestors and *every* opening that names it as host, so a
    sibling Window's own ring is paired with that sibling instead of reading
    as ``dangling_ring``. Only findings about the wall or one of its openings
    (or scene-wide ones) count; a genuinely broken sibling does block, since
    the wall record is about to be rewritten. Validation is broad; the write
    that follows stays limited to the edited Window and its own ring.
    """
    from . import consistency_view  # lazy: ring_opening_sync (its dependency) imports bpy

    findings = consistency_view.host_closure_findings(wall, wall_feature.gml_id)
    if consistency.is_blocking(findings):
        problems = "; ".join(f"{f.code}: {f.message}" for f in findings if f.severity == consistency.ERROR)
        raise EditSessionError(f"{gml_id!r}: {operation} refused, consistency check failed: {problems}")


def validate_and_commit_existing_feature(gml_id, mutate, *, operation="semantic operation") -> CommitResult:
    """Apply one explicit Window mutation through the P4 dual-write seam.

    ``mutate(window, wall)`` may change only the live legacy representation.
    This helper then proves that the resulting records are valid and commits
    both the Window and host-wall records from that same representation.  On
    any refusal the live mesh/transform state is restored and no record write
    is retained.  It is deliberately operator-driven, never a handler.

    Align and the two explicit Ring/Opening sync commands use this seam so
    they cannot grow operation-specific record writers (CAC-P4.6).
    """
    gml_id = str(gml_id or "").strip()
    if not gml_id:
        raise EditSessionError("gml_id is required")
    if gml_id in _SESSIONS:
        raise EditSessionError(f"{gml_id!r}: an explicit edit session is already open")

    window, feature, wall, wall_feature = _resolve_window_and_wall(gml_id)
    window_state = _mesh_state(window)
    wall_state = _mesh_state(wall)
    window_record_before = storage.read_record(window)
    wall_record_before = storage.read_record(wall)

    from . import display_regen  # lazy: imports bpy (GS.2, gs-design-note.md S4 item 2)

    display_before = display_regen.snapshot_existing_display(
        wall, window, wall_feature.gml_id, feature.gml_id
    )
    try:
        mutate(window, wall)
        # The same host-complete validation commit()/commit_transform_drift()
        # use (CAC-P4.10), on the mutated live state, before anything is
        # written; a refusal restores the mesh below.
        _refuse_if_host_inconsistent(gml_id, wall, wall_feature, operation)
        # _commit_window_and_wall builds and validates both replacement record
        # sets before either is written.  Thus a rejected final shape cannot
        # leave a record/ring mismatch behind.
        _commit_window_and_wall(window, wall, feature, wall_feature)
        # CAC-P3b.3: Align and manual Sync go through this same seam
        # (``align_existing_window``/``sync_existing_feature``), so their
        # display now regenerates too, on the same transactional footing as
        # ``commit()`` - a failure here rolls back the mesh, both records
        # and the display together, below.
        display_regen.regenerate_committed_display(window, wall, feature, wall_feature)
    except Exception as exc:
        _restore_mesh_state(window, window_state)
        _restore_mesh_state(wall, wall_state)
        if window_record_before is not None:
            storage.write_record(window, window_record_before)
        if wall_record_before is not None:
            storage.write_record(wall, wall_record_before)
        display_regen.restore_display_snapshot(
            wall, window, wall_feature.gml_id, feature.gml_id, display_before
        )
        if isinstance(exc, EditSessionError):
            raise
        raise EditSessionError(f"{gml_id!r}: {operation} refused: {exc}") from exc
    return CommitResult(status="committed", message=f"{gml_id!r}: {operation} committed", gml_id=gml_id)


def _pair_for_window(window, wall):
    from ...ring_opening_sync import pairs_for_wall_children

    pairs = [pair for pair in pairs_for_wall_children(wall) if pair.opening is window]
    if len(pairs) != 1:
        raise EditSessionError(
            f"{window.get('gml_id')!r}: expected exactly one host ring/opening pair, found {len(pairs)}"
        )
    return pairs[0]


def sync_existing_feature(gml_id, direction: str) -> CommitResult:
    """Run one explicit manual Sync through the shared P4 commit seam."""
    from ...ring_opening_sync import sync_opening_to_ring, sync_ring_to_opening

    if direction not in {"opening_to_ring", "ring_to_opening"}:
        raise EditSessionError(f"unknown sync direction {direction!r}")

    def mutate(window, wall):
        pair = _pair_for_window(window, wall)
        if direction == "opening_to_ring":
            sync_opening_to_ring(pair)
        else:
            sync_ring_to_opening(pair)

    return validate_and_commit_existing_feature(gml_id, mutate, operation=f"sync {direction}")


def align_existing_window(source_gml_id, target) -> CommitResult:
    """Align a record-backed Window without creating a second record writer."""
    from ...align_objects import align_source_to_target

    result_box = {}

    def mutate(window, _wall):
        result_box["align"] = align_source_to_target(window, target)

    result = validate_and_commit_existing_feature(source_gml_id, mutate, operation="align")
    align = result_box["align"]
    return CommitResult(
        status=result.status,
        message=(f"{result.gml_id!r}: aligned ({align.axis} match, {align.edge} edge) and committed"),
        gml_id=result.gml_id,
    )


def commit(gml_id) -> CommitResult:
    """Checksum -> validate -> commit (or clean no-op / clean refusal).

    - Checksum unchanged since ``begin``: clean no-op, session closes,
      nothing is written (acceptance: "commit is no-op when checksum
      unchanged").
    - Checksum changed: validated via the P3.3 consistency validator
      (``consistency_view.run_validation``) over the host wall's complete
      closure - the wall and every opening it hosts (CAC-P4.10) - plus a
      live ring/opening pairing check
      (``ring_opening_sync.pairs_for_wall_children``). Any blocking finding
      refuses the commit - nothing is written, the session stays open so the
      caller can cancel or keep editing (acceptance: "invalid edit ->
      refused, state unchanged").
    - Valid: the host wall's ring is recut onto the Window's current
      position (``sync_opening_to_ring``, dual-write of the legacy mirror),
      then both records are re-derived and written, preserving both
      ``gml_id``s, then the derived display is regenerated from those fresh
      records.

    CAC-P3b.1: "dual-write legacy mesh -> write records -> regenerate
    display" is one transaction. Everything the commit can mutate is
    snapshotted first; if any of the three steps above raises (including a
    ``DisplayBuildError``/``DisplayApplyError``/``DisplayVerificationError``
    from display regeneration - previously left the just-written records in
    place with no display to match, design note §10), the legacy mesh,
    both records and the display objects are all restored to their exact
    pre-commit state and the commit fails cleanly: no partial semantic
    commit survives a failed display regeneration, and a failure that
    happens before ``_commit_window_and_wall`` (e.g. an inconsistent host)
    no longer leaves the legacy mesh recut with stale records either.
    """
    gml_id = str(gml_id or "").strip()
    session = _SESSIONS.get(gml_id)
    if session is None:
        raise EditSessionError(f"{gml_id!r}: no open edit session (call feature_edit_begin first)")

    window, feature, wall, wall_feature = _resolve_window_and_wall(
        gml_id, session.window_name, session.wall_gml_id, session.wall_name,
    )

    current_snapshot = _record_snapshot(window, wall, feature, wall_feature)
    current_checksum = checksum.compute_checksum(current_snapshot)
    if _decide(session.checksum, current_checksum) == NO_OP:
        del _SESSIONS[gml_id]
        return CommitResult(status="no_op", message=f"{gml_id!r}: no changes to commit", gml_id=gml_id)

    from ...ring_opening_sync import pairs_for_wall_children, sync_opening_to_ring  # lazy: imports bpy

    _refuse_if_host_inconsistent(gml_id, wall, wall_feature, "commit")

    pairs = [p for p in pairs_for_wall_children(wall) if p.opening is window]
    if not pairs:
        raise EditSessionError(
            f"{gml_id!r}: commit refused, no ring/opening pairing found on host wall "
            f"{wall_feature.gml_id!r}"
        )

    from . import display_regen  # lazy: imports bpy (GS.2, gs-design-note.md S4 item 2)

    # Snapshot everything this commit can mutate, before touching anything:
    # the legacy mesh/transform of both objects (what sync_opening_to_ring
    # recuts), both stored records (what _commit_window_and_wall writes) and
    # the pre-existing display objects, if any (what display regeneration
    # writes). Deliberately not a whole-scene snapshot - only the mutation
    # set this one commit can actually touch (design note "Snapshot scope").
    window_mesh_before = _mesh_state(window)
    wall_mesh_before = _mesh_state(wall)
    window_record_before = storage.read_record(window)
    wall_record_before = storage.read_record(wall)
    display_before = display_regen.snapshot_existing_display(
        wall, window, wall_feature.gml_id, feature.gml_id
    )

    try:
        # Dual-write the legacy mirror first (recut the wall's ring onto the
        # Window's edited position); the commit derives the fresh records from
        # exactly this post-sync legacy state, same as create_feature (P3.2).
        sync_opening_to_ring(pairs[0])
        _commit_window_and_wall(window, wall, feature, wall_feature)
        display_regen.regenerate_committed_display(window, wall, feature, wall_feature)
    except Exception as exc:
        _restore_mesh_state(window, window_mesh_before)
        _restore_mesh_state(wall, wall_mesh_before)
        if window_record_before is not None:
            storage.write_record(window, window_record_before)
        if wall_record_before is not None:
            storage.write_record(wall, wall_record_before)
        display_regen.restore_display_snapshot(
            wall, window, wall_feature.gml_id, feature.gml_id, display_before
        )
        # registry.REGISTRY.mark_dirty() (already called, if reached, by the
        # now-rolled-back _commit_window_and_wall) needs no counteraction:
        # the registry is a purely derived, lazily rebuilt index (R4/P2) -
        # "dirty" only means the next resolve re-reads the (now restored)
        # records, which is correct either way.
        if isinstance(exc, EditSessionError):
            raise
        raise EditSessionError(f"{gml_id!r}: commit refused: {exc}") from exc

    del _SESSIONS[gml_id]
    return CommitResult(status="committed", message=f"{gml_id!r}: committed", gml_id=gml_id)


def commit_transform_drift(gml_id) -> CommitResult:
    """Explicit resolution for native G/R/S transform drift (CAC-P4.5, D-7b):
    checksum (live vs. last-committed, from storage) -> validate -> the same
    dual-write commit ``commit()`` uses - without requiring a prior
    ``begin()`` session, since a native Blender transform operator never
    opens one (R4: nothing here is a handler; this is only ever reached
    through the explicit ``cgml.commit_transform_drift`` use case).

    - Checksum unchanged: clean no-op, nothing written.
    - Checksum changed but the result would be invalid (P3.3 validator):
      refused, nothing written, the object stays dirty for the user to
      repair or undo (acceptance: "invalid transformed state refuses with
      zero partial semantic mutation").
    - Valid: the host wall's ring is recut onto the Window's current
      (moved) position, exactly like ``commit()``, then both records are
      re-derived and written, preserving both ``gml_id``s - no new identity
      is minted, and the object's committed state (and therefore its
      derived dirty status, CAC-P4.5's ``transform_drift`` cache) is clean
      again as soon as this returns.
    """
    gml_id = str(gml_id or "").strip()
    if not gml_id:
        raise EditSessionError("gml_id is required")
    if gml_id in _SESSIONS:
        raise EditSessionError(
            f"{gml_id!r}: an explicit edit session is already open - use "
            "cgml.feature_edit_commit/cancel instead"
        )

    window, feature, wall, wall_feature = _resolve_window_and_wall(gml_id)

    committed_checksum = checksum.compute_checksum(
        _committed_record_snapshot(window, wall, feature, wall_feature)
    )
    current_checksum = checksum.compute_checksum(_record_snapshot(window, wall, feature, wall_feature))
    if _decide(committed_checksum, current_checksum) == NO_OP:
        return CommitResult(status="no_op", message=f"{gml_id!r}: no transform drift to commit", gml_id=gml_id)

    from ...ring_opening_sync import pairs_for_wall_children, sync_opening_to_ring  # lazy: imports bpy

    _refuse_if_host_inconsistent(gml_id, wall, wall_feature, "transform-drift commit")

    pairs = [p for p in pairs_for_wall_children(wall) if p.opening is window]
    if not pairs:
        raise EditSessionError(
            f"{gml_id!r}: commit refused, no ring/opening pairing found on host wall "
            f"{wall_feature.gml_id!r}"
        )

    from . import display_regen  # lazy: imports bpy (GS.2, gs-design-note.md S4 item 2)

    # Only the wall's mesh/ring is about to be mutated (sync_opening_to_ring
    # recuts it onto the Window's already-drifted position); the Window's
    # own transform is the user's drift itself and is left alone on
    # rollback, same as before this seam existed - a failed commit leaves
    # the object dirty for the user to repair or undo, not reverted.
    wall_mesh_before = _mesh_state(wall)
    window_record_before = storage.read_record(window)
    wall_record_before = storage.read_record(wall)
    display_before = display_regen.snapshot_existing_display(
        wall, window, wall_feature.gml_id, feature.gml_id
    )

    # Same order as commit(): dual-write the legacy mirror first, then
    # re-derive both records from that post-sync legacy state.
    try:
        sync_opening_to_ring(pairs[0])
        _commit_window_and_wall(window, wall, feature, wall_feature)
        # CAC-P3b.3: an explicit transform-drift commit also regenerates the
        # affected host-wall display now, on the same transactional footing
        # as commit() - a failure rolls back the wall's ring, both records
        # and the display together, below. Export's own "never auto-commits
        # drift" rule is untouched: this function is still only ever reached
        # through the explicit cgml.commit_transform_drift use case.
        display_regen.regenerate_committed_display(window, wall, feature, wall_feature)
    except Exception as exc:
        _restore_mesh_state(wall, wall_mesh_before)
        if window_record_before is not None:
            storage.write_record(window, window_record_before)
        if wall_record_before is not None:
            storage.write_record(wall, wall_record_before)
        display_regen.restore_display_snapshot(
            wall, window, wall_feature.gml_id, feature.gml_id, display_before
        )
        if isinstance(exc, EditSessionError):
            raise
        raise EditSessionError(f"{gml_id!r}: transform-drift commit refused: {exc}") from exc

    return CommitResult(status="committed", message=f"{gml_id!r}: transform drift committed", gml_id=gml_id)


def cancel(gml_id) -> None:
    """Restore the pre-edit display state and close the session.

    Records were never touched between ``begin`` and ``cancel`` (only
    ``commit`` writes - R4/R5), so cancel only needs to restore the Blender
    mesh/transform of the Window and its host wall to what ``begin``
    snapshotted (acceptance: "cancel -> display restored").
    """
    gml_id = str(gml_id or "").strip()
    session = _SESSIONS.pop(gml_id, None)
    if session is None:
        raise EditSessionError(f"{gml_id!r}: no open edit session")

    window = registry.resolve(gml_id, session.window_name)
    if window is not None:
        _restore_mesh_state(window, session.window_mesh_state)
    wall = registry.resolve(session.wall_gml_id, session.wall_name)
    if wall is not None:
        _restore_mesh_state(wall, session.wall_mesh_state)
