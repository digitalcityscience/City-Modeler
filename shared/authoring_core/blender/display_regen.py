# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""GS.2: post-commit display regeneration (docs/agent/authoring-core/gs-design-note.md S4 item 2).

``regenerate_committed_display(window, wall, feature, wall_feature)`` is
called once from ``edit_session.commit()``, right after a successful commit
(``_commit_window_and_wall``). It rebuilds the record-derived display mesh
(``display_builder.build_surface_display``) for the host WallSurface and its
hosted Window and writes each into its own auxiliary Blender object - never
the wall's/window's own mesh, which ``ring_opening_sync.py`` still owns for
the legacy dual-write mirror. GS.2 is additive only: it does not read or
change anything ``commit()``'s existing checksum/validate/dual-write flow
already decided.

Ownership model (revised after review - the initial version kept the display
collection unlinked from the scene to dodge export, which also made every
display object invisible in the viewport and left it liable to be dropped on
save/reload, since Blender purges zero-user, unlinked datablocks):

- semantic/edit authority stays entirely on ``window``/``wall`` - their own
  ``gml_id``, records (``storage.py``) and legacy mesh/ring
  (``ring_opening_sync.py``) are the only source of identity, polygon/ring
  ids, opening relations, committed coordinates and export content. This
  module only ever reads them, never writes an id onto a display object.
- each display object is linked into ``_display_collection()``, itself
  linked into ``bpy.context.scene.collection`` - scene-visible, present in
  the active ViewLayer, like any other collection.
- every display object carries ``DISPLAY_ONLY_KEY`` (``is_display_only``),
  the single, explicit marker both CityGML writers
  (``io/writer/exporter.py``, ``io_v2/writer/exporter.py``,
  ``_should_export_obj``) check to exclude it from export - not a name
  pattern, not collection membership. It carries no ``gml_id``, so
  ``registry.py``'s ``identity_of`` (``if not (gml_id or kind or
  has_record): continue``) already skips it for semantic lookup with no
  further change needed there.

``plan_regeneration`` is the bpy-free half: given the two ``RecordSet``s (or
``None`` when an object carries no record) it decides which surfaces get a
rebuilt ``DisplayMesh``, with no ``bpy`` import - unit-testable without
Blender, same spirit as ``display_builder.py`` (whose bpy-free constraint is
scoped to that module only; this module is free to import ``bpy``, gs-design
-note.md S4 item 2 explicitly hands the bpy-bound mesh-writing half to
``blender/``). ``regenerate_committed_display`` is the bpy-bound orchestrator
that reads storage, calls it, and writes the result onto display objects.

GS.2 scope only: no operator, no export dispatch, no per-surface D-6
relaxation, no Door/Stamp/Tab-override, and the legacy mesh/ring sync path
(``ring_opening_sync.py``) is untouched - this only adds a second, separate
display mesh per surface.
"""
from __future__ import annotations

from . import display_builder
from ..domain import display_checksum


class DisplayApplyError(Exception):
    """Raised when a planned ``DisplayMesh`` cannot be written onto its
    Blender display object (CAC-P3b.1's mesh-application failure seam, as
    distinct from ``display_builder.DisplayBuildError``'s topology-planning
    seam)."""


class DisplayVerificationError(Exception):
    """Raised when a display object's mesh, immediately after being written,
    does not match the ``DisplayMesh`` that was supposed to produce it
    (CAC-P3b.1's post-application verification seam)."""


DISPLAY_SURFACE_KEY = "cgml_display_surface_id"

# Leading underscore: hidden from the N-panel Custom Properties list (same
# convention as ``storage.RECORD_KEY``), not a semantic property - it never
# becomes a gml_id, never participates in ``registry``/``storage`` identity,
# and is the sole thing both writers key their export-exclusion guard on
# (``io/writer/exporter.py``/``io_v2/writer/exporter.py``,
# ``display_regen.is_display_only``).
DISPLAY_ONLY_KEY = "_cgml_display_only"

# Persisted freshness provenance (CAC-P3b.4), kept separate from
# DISPLAY_ONLY_KEY's role/classification concern: this key never influences
# export eligibility or semantic identity, it only lets a caller ask "does
# this display mesh still correspond to the current committed record
# state?" without re-running the triangulator. Same leading-underscore
# convention (hidden from the N-panel Custom Properties list).
DISPLAY_SOURCE_CHECKSUM_KEY = "_cgml_display_source_checksum"


def is_display_only(obj) -> bool:
    """True for a GS display-only auxiliary object (never semantic content).

    The single predicate both CityGML writers use to exclude a display
    object from export, and the only marker this module relies on - no
    object naming convention, no collection-name convention.
    """
    try:
        return bool(obj.get(DISPLAY_ONLY_KEY, False))
    except Exception:
        return False


def mark_display_fresh(display_obj, checksum: str) -> None:
    """Persist ``checksum`` (a ``display_checksum.compute_surface_display_
    checksum`` result) onto ``display_obj`` (CAC-P3b.4 §6). Only ever called
    after a display mesh has been written *and* verified - a display object
    must not be marked fresh for a checksum that does not actually
    correspond to what is currently on it."""
    display_obj[DISPLAY_SOURCE_CHECKSUM_KEY] = checksum


def display_freshness(display_obj, record_set, surface_id) -> str:
    """One of ``display_checksum.FRESHNESS_STATES`` for ``surface_id``:
    ``MISSING`` if ``display_obj`` is ``None``, ``INVALID`` if the committed
    records cannot even be turned into an expected checksum (same shapes
    ``display_builder.build_surface_display`` itself rejects) or the stored
    property is malformed, otherwise ``FRESH``/``STALE`` from comparing the
    stored checksum against the freshly recomputed authoritative one.

    Pure classification only - this never rebuilds or rewrites anything on
    ``display_obj``; a caller that wants to repair a stale/missing display
    calls ``regenerate_committed_display``/``regenerate_wall_display``
    itself.
    """
    if display_obj is None:
        return display_checksum.MISSING
    try:
        expected = display_checksum.compute_surface_display_checksum(record_set, surface_id)
    except display_checksum.DisplayChecksumError:
        return display_checksum.INVALID
    stored = display_obj.get(DISPLAY_SOURCE_CHECKSUM_KEY, None)
    return display_checksum.classify_freshness(stored, expected)


def roof_display_freshness(display_obj, record_set, surface_id) -> str:
    """RoofSurface counterpart to ``display_freshness`` (CAC-P6.3 S9): same
    ``MISSING``/``INVALID``/``FRESH``/``STALE`` classification, computed
    from ``display_checksum.compute_roof_display_checksum`` instead of
    ``compute_surface_display_checksum`` - the multi-polygon-aware checksum
    ``build_roof_display``/``regenerate_roof_display`` actually use.

    Not interchangeable with ``display_freshness``: the two checksum
    functions hash the same underlying facts through a different text
    serialization (``compute_surface_display_checksum`` names its single
    polygon ``polygon_id=``; ``compute_roof_display_checksum`` names its
    (possibly several) polygons ``polygon_ids=``), so even a RoofSurface
    with exactly one committed polygon would otherwise compare a ``mark_
    display_fresh``-written Roof checksum against a differently-serialized
    \"expected\" value and misreport a genuinely fresh display as ``STALE``.
    """
    if display_obj is None:
        return display_checksum.MISSING
    try:
        expected = display_checksum.compute_roof_display_checksum(record_set, surface_id)
    except display_checksum.DisplayChecksumError:
        return display_checksum.INVALID
    stored = display_obj.get(DISPLAY_SOURCE_CHECKSUM_KEY, None)
    return display_checksum.classify_freshness(stored, expected)


def plan_regeneration(wall_record_set, wall_surface_id, window_record_set, window_surface_id):
    """Which ``DisplayMesh``(es) a commit should (re)build, and for which surface id.

    Returns a tuple of ``(surface_id, DisplayMesh)`` pairs, wall first (when
    present) then Window. A ``None`` record set (the object carries no
    authoring record) is simply skipped - display regeneration only ever
    covers the wall + its hosted Window closure gs-design-note.md S4 item 2
    names, nothing else is in scope here.
    """
    planned = []
    if wall_record_set is not None:
        planned.append(
            (wall_surface_id, display_builder.build_surface_display(wall_record_set, wall_surface_id))
        )
    if window_record_set is not None and len(window_record_set.polygons_of(window_surface_id)) == 1:
        # A record-backed Window with an authored depth (CAC-P3.2,
        # FacadeElementDefinition.depth > 0) is a box - one PolygonRecord per
        # face, confirmed against a real headless fixture (6 polygons for a
        # centred prism, ``_make_centered_prism``). That does not fit
        # ``build_surface_display``'s single-polygon contract (GS.1 scope:
        # WallSurface-shaped input), so it is skipped here rather than
        # raised - a known shape mismatch, not a broken commit; the wall's
        # own display still regenerates. A flat (depth <= 0) Window, the
        # gs-design-note.md S3 "rectangular Window only" case, always has
        # exactly one polygon and is unaffected.
        planned.append(
            (window_surface_id, display_builder.build_surface_display(window_record_set, window_surface_id))
        )
    return tuple(planned)


def plan_roof_regeneration(roof_record_set, roof_surface_id):
    """Which single ``DisplayMesh`` a RoofSurface commit should (re)build
    (CAC-P6.3 S2), the single-feature counterpart to ``plan_regeneration``'s
    wall+Window pair. A ``None`` record set is skipped the same way
    ``plan_regeneration`` skips a missing wall/Window record set. Reuses
    ``display_builder.build_roof_display`` - the generic multi-polygon
    builder (P6.3 S8) - rather than ``build_surface_display``, since a
    RoofSurface's record contract already allows more than one committed
    polygon under one feature.
    """
    if roof_record_set is None:
        return None
    return display_builder.build_roof_display(roof_record_set, roof_surface_id)


_DISPLAY_COLLECTION_NAME = "CAC GS Display"


def _display_collection():
    """The ``bpy.data.collections`` entry every display object is linked
    into - scene-linked, so it is visible in the viewport/active ViewLayer
    like any other collection (a first fix attempt kept this collection
    unlinked to dodge export; that also made every display object invisible
    and, unlinked, at risk of being dropped on save/reload - reverted).
    Export exclusion is instead the explicit ``DISPLAY_ONLY_KEY`` marker
    both writers check (``is_display_only``), not collection membership.
    """
    import bpy

    collection = bpy.data.collections.get(_DISPLAY_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(_DISPLAY_COLLECTION_NAME)
    if collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(collection)
    return collection


def _find_or_create_display_object(source_obj, surface_id):
    import bpy

    name = f"{source_obj.name}.cgml_display"
    obj = bpy.data.objects.get(name)
    if obj is None or obj.get(DISPLAY_SURFACE_KEY) != surface_id:
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        obj[DISPLAY_SURFACE_KEY] = surface_id
        obj[DISPLAY_ONLY_KEY] = True
        _display_collection().objects.link(obj)
    return obj


def _write_display_mesh(obj, display_mesh):
    from mathutils import Matrix

    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(list(display_mesh.vertices), [], [list(face) for face in display_mesh.faces])
    mesh.update()
    # Record coordinates are already world-space (P3.2 dual-write, reused by
    # commit()'s own snapshots) - keep the display object's transform
    # identity so its mesh-local coords read directly as world coords too.
    obj.matrix_world = Matrix.Identity(4)


def _verify_display_mesh(obj, display_mesh) -> None:
    """Post-application verification seam (CAC-P3b.1): confirm the mesh just
    written onto ``obj`` actually matches the ``DisplayMesh`` that was meant
    to produce it, rather than trusting ``_write_display_mesh`` silently."""
    mesh = obj.data
    if len(mesh.vertices) != len(display_mesh.vertices) or len(mesh.polygons) != len(display_mesh.faces):
        raise DisplayVerificationError(
            f"{obj.name!r}: display mesh verification failed after write "
            f"(expected {len(display_mesh.vertices)} vertices / {len(display_mesh.faces)} faces, "
            f"got {len(mesh.vertices)} / {len(mesh.polygons)})"
        )


def regenerate_committed_display(window, wall, feature, wall_feature):
    """Rebuild the wall + hosted-Window display mesh from just-committed records.

    Three distinct failure seams, in order: ``display_builder.DisplayBuildError``
    (topology planning, inside ``plan_regeneration`` - raised before any
    Blender object is touched, since ``plan_regeneration`` builds every
    ``DisplayMesh`` before this function applies any of them),
    ``DisplayApplyError`` (writing a planned mesh onto its Blender object) and
    ``DisplayVerificationError`` (the mesh actually present afterward does not
    match what was planned). None of these is swallowed here: since CAC-P3b.1,
    the caller (``edit_session.commit()``) wraps this call in the same
    transaction as the record write it follows, and rolls the whole commit
    back - including this function's own partial effects, via
    ``snapshot_existing_display``/``restore_display_snapshot`` - on any of
    these three exceptions.
    """
    from . import storage

    wall_record_set = storage.read_record(wall)
    window_record_set = storage.read_record(window)
    for surface_id, display_mesh in plan_regeneration(
        wall_record_set, wall_feature.gml_id, window_record_set, feature.gml_id
    ):
        source_obj = wall if surface_id == wall_feature.gml_id else window
        record_set = wall_record_set if surface_id == wall_feature.gml_id else window_record_set
        display_obj = _find_or_create_display_object(source_obj, surface_id)
        try:
            _write_display_mesh(display_obj, display_mesh)
        except DisplayApplyError:
            raise
        except Exception as exc:
            raise DisplayApplyError(
                f"{surface_id!r}: failed to apply display mesh to {display_obj.name!r}: {exc}"
            ) from exc
        _verify_display_mesh(display_obj, display_mesh)
        # Only mark fresh after successful mesh application + verification
        # (CAC-P3b.4 §6) - a checksum for records that did not actually
        # produce this mesh must never be written.
        mark_display_fresh(
            display_obj, display_checksum.compute_surface_display_checksum(record_set, surface_id)
        )


def regenerate_or_restore(window, wall, feature, wall_feature):
    """The shared post-commit seam (CAC-P3b.3): snapshot the wall/Window
    display, regenerate it from the just-written records, and restore the
    snapshot (then re-raise) if regeneration fails.

    Every lifecycle entry point that commits a Window+wall record pair
    while both objects still exist (create/F9, Align, manual Sync,
    transform-drift commit) reuses this instead of repeating
    ``snapshot_existing_display``/``regenerate_committed_display``/
    ``restore_display_snapshot`` inline (``edit_session.commit()`` keeps its
    own inline copy since its ``except`` already restores the legacy
    mesh/records in the same block; callers that roll back their own
    mesh/records separately can wrap this call in their own ``except``).
    """
    display_before = snapshot_existing_display(wall, window, wall_feature.gml_id, feature.gml_id)
    try:
        regenerate_committed_display(window, wall, feature, wall_feature)
    except Exception:
        restore_display_snapshot(wall, window, wall_feature.gml_id, feature.gml_id, display_before)
        raise


def regenerate_wall_display(wall, wall_feature):
    """Rebuild only the host wall's display mesh from its current records
    (CAC-P3b.3): used by guarded delete, which removes the Window's own
    record and display object entirely rather than rebuilding it alongside
    the wall."""
    from . import storage

    wall_record_set = storage.read_record(wall)
    for surface_id, display_mesh in plan_regeneration(wall_record_set, wall_feature.gml_id, None, ""):
        display_obj = _find_or_create_display_object(wall, surface_id)
        try:
            _write_display_mesh(display_obj, display_mesh)
        except Exception as exc:
            raise DisplayApplyError(
                f"{surface_id!r}: failed to apply display mesh to {display_obj.name!r}: {exc}"
            ) from exc
        _verify_display_mesh(display_obj, display_mesh)
        mark_display_fresh(
            display_obj, display_checksum.compute_surface_display_checksum(wall_record_set, surface_id)
        )


def remove_display_object(source_obj, surface_id):
    """Remove ``source_obj``'s display object for ``surface_id`` entirely
    (CAC-P3b.3 delete): the semantic feature it mirrored no longer exists,
    so no rebuilt mesh should take its place."""
    obj = _existing_display_object(source_obj, surface_id)
    if obj is None:
        return
    import bpy

    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def regenerate_after_delete_or_restore(wall, window, wall_feature, window_surface_id):
    """The shared post-delete seam (CAC-P3b.3): snapshot the wall/Window
    display, rebuild the wall's display from its post-delete records, then
    remove the deleted Window's own display object - restoring the
    snapshot (then re-raising) if either step fails, so a failed delete
    never leaves a wall display missing its still-valid hole, or a Window
    display orphaned for a feature that turns out not to have been
    deleted after all.

    Must be called with ``window`` still resolvable (before the caller
    removes the Window object itself) - the display object's name is
    derived from it.
    """
    display_before = snapshot_existing_display(wall, window, wall_feature.gml_id, window_surface_id)
    try:
        regenerate_wall_display(wall, wall_feature)
        remove_display_object(window, window_surface_id)
    except Exception:
        restore_display_snapshot(wall, window, wall_feature.gml_id, window_surface_id, display_before)
        raise


def _existing_display_object(source_obj, surface_id):
    """The already-linked display object for ``surface_id``, or ``None`` if
    none exists yet - the same lookup ``_find_or_create_display_object``
    does, without the "create if missing" half, so a snapshot can tell
    "already existed" apart from "about to be created for the first time"."""
    import bpy

    name = f"{source_obj.name}.cgml_display"
    obj = bpy.data.objects.get(name)
    if obj is None or obj.get(DISPLAY_SURFACE_KEY) != surface_id:
        return None
    return obj


def _snapshot_one(source_obj, surface_id):
    """Pre-commit state of a single surface's display object (CAC-P3b.1/
    P6.3): ``None`` if it has no display object yet, else ``(vertices,
    faces, checksum_or_none)`` read back from its existing mesh and its
    ``DISPLAY_SOURCE_CHECKSUM_KEY`` property. Shared by the wall/Window-pair
    snapshot (``snapshot_existing_display``) and the single-feature
    RoofSurface snapshot (``snapshot_single_display``) - the same per-object
    capture either one just applies to a different number of objects.
    """
    obj = _existing_display_object(source_obj, surface_id)
    if obj is None:
        return None
    mesh = obj.data
    vertices = tuple(tuple(v.co) for v in mesh.vertices)
    faces = tuple(tuple(p.vertices) for p in mesh.polygons)
    checksum = obj.get(DISPLAY_SOURCE_CHECKSUM_KEY, None)
    return (vertices, faces, checksum)


def _restore_one(source_obj, surface_id, snap):
    """Undo a failed regeneration attempt for one surface's display object
    (CAC-P3b.1/P6.3): restore its exact pre-attempt mesh/checksum, or - if
    ``snap`` is ``None`` (the failed attempt created this display object
    for the first time) - remove it (and its now-orphaned mesh datablock)
    entirely, so no orphan display datablock/object is left behind. Shared
    by ``restore_display_snapshot`` (wall/Window pair) and
    ``restore_single_display_snapshot`` (single-feature RoofSurface).
    """
    import bpy

    obj = _existing_display_object(source_obj, surface_id)
    if snap is None:
        if obj is not None:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        return
    if obj is None:
        # Nothing survived to restore onto (should not normally happen -
        # _write_display_mesh only rewrites an existing datablock in
        # place, it never removes the object) - nothing to do.
        return
    vertices, faces, checksum = snap
    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(list(vertices), [], [list(face) for face in faces])
    mesh.update()
    if checksum is None:
        if DISPLAY_SOURCE_CHECKSUM_KEY in obj.keys():
            del obj[DISPLAY_SOURCE_CHECKSUM_KEY]
    else:
        obj[DISPLAY_SOURCE_CHECKSUM_KEY] = checksum


def snapshot_existing_display(wall, window, wall_surface_id, window_surface_id):
    """Capture the pre-commit state of the wall/Window display objects
    (CAC-P3b.1), so a failed ``regenerate_committed_display`` call can be
    rolled back to the exact pre-operation display instead of trying to
    rebuild it - the smaller, safer choice (design note §11): it never
    re-invokes ``display_builder``, which is exactly what may have just
    failed, and it needs no assumption that the *restored* records are even
    buildable.

    Returns ``{"wall": snap_or_none, "window": snap_or_none}`` - see
    ``_snapshot_one`` for the shape of each entry.
    """
    return {
        "wall": _snapshot_one(wall, wall_surface_id),
        "window": _snapshot_one(window, window_surface_id),
    }


def restore_display_snapshot(wall, window, wall_surface_id, window_surface_id, snapshot):
    """Undo a failed ``regenerate_committed_display`` call (CAC-P3b.1): a
    surface that already had a display object before the attempt gets its
    exact pre-attempt mesh written back; a surface whose display object was
    freshly created by the failed attempt has that object (and its now-
    orphaned mesh datablock) removed entirely - no orphan display
    datablocks/objects are left behind either way.
    """
    _restore_one(wall, wall_surface_id, snapshot.get("wall"))
    _restore_one(window, window_surface_id, snapshot.get("window"))


def snapshot_single_display(source_obj, surface_id):
    """Single-feature counterpart to ``snapshot_existing_display`` (CAC-P6.3
    S5/S12): capture the pre-commit state of one RoofSurface's own display
    object - a RoofSurface has no paired host object the way a Window has
    its wall, so there is only ever one display object to snapshot."""
    return _snapshot_one(source_obj, surface_id)


def restore_single_display_snapshot(source_obj, surface_id, snapshot):
    """Single-feature counterpart to ``restore_display_snapshot`` (CAC-P6.3
    S5/S12)."""
    _restore_one(source_obj, surface_id, snapshot)


def regenerate_roof_display(roof, feature):
    """Rebuild a record-backed RoofSurface's own display mesh from its
    just-committed records (CAC-P6.3 S2/S5), the single-feature counterpart
    to ``regenerate_committed_display``. Same three failure seams, in the
    same order, with the same meaning: ``display_builder.DisplayBuildError``
    (topology planning, inside ``plan_roof_regeneration`` - raised before
    the Roof's display object is touched), ``DisplayApplyError`` (writing
    the planned mesh onto it) and ``DisplayVerificationError`` (the mesh
    actually present afterward does not match what was planned).

    Reads records straight from ``storage`` rather than taking a
    ``RecordSet`` parameter - mirrors ``regenerate_committed_display``'s own
    shape, and keeps this function's only caller (``regenerate_or_restore_
    roof_display``) a thin orchestration wrapper around the already-
    committed record state, never a second place that decides what "current"
    means.
    """
    from . import storage

    record_set = storage.read_record(roof)
    display_mesh = plan_roof_regeneration(record_set, feature.gml_id)
    if display_mesh is None:
        return
    display_obj = _find_or_create_display_object(roof, feature.gml_id)
    try:
        _write_display_mesh(display_obj, display_mesh)
    except DisplayApplyError:
        raise
    except Exception as exc:
        raise DisplayApplyError(
            f"{feature.gml_id!r}: failed to apply display mesh to {display_obj.name!r}: {exc}"
        ) from exc
    _verify_display_mesh(display_obj, display_mesh)
    mark_display_fresh(
        display_obj, display_checksum.compute_roof_display_checksum(record_set, feature.gml_id)
    )


def regenerate_or_restore_roof_display(roof, feature):
    """The RoofSurface post-commit seam (CAC-P6.3 S5), mirroring
    ``regenerate_or_restore``'s wall/Window shape for the single-feature
    case: snapshot the Roof's own display, regenerate it from the just-
    written record, and restore the snapshot (then re-raise) if
    regeneration fails - so a failed display regeneration never leaves a
    partial/orphan display object sitting next to a rolled-back record.

    Every ``roof_edit_session`` commit entry point (``commit``,
    ``validate_and_commit_existing_roof``, ``commit_transform_drift``) calls
    this right after ``_commit_roof`` succeeds, inside the same try block
    that already restores the Roof's mesh/record on any exception - this
    function's own re-raise on failure is what drives that outer rollback;
    the display object itself is already restored to its exact pre-attempt
    state by the time that exception reaches the outer handler.
    """
    display_before = snapshot_single_display(roof, feature.gml_id)
    try:
        regenerate_roof_display(roof, feature)
    except Exception:
        restore_single_display_snapshot(roof, feature.gml_id, display_before)
        raise
