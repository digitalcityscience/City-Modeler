# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record-backed feature creation and deletion (CAC-P3.2, CAC-P3.4).

``create_feature`` is the generic authoring entry point a ``FeatureKind``
caller (Draw today, Semantic Stamp in P3.2a) invokes to place a new opening
and author both its own CityGML record and its host's ring record, in one
undo step. It wraps ``shared/facade_placement.py::place_semantic_element``
unchanged - P3.2 does not touch geometry - and adds a dual-write:

- the legacy props/materials/mesh ``place_semantic_element`` already
  writes, still authoritative for the exporter until P3.3/P4;
- a fresh ``FeatureRecord``/``PolygonRecord``/``RingRecord`` set, built by
  reading back the *exact* legacy state that call just wrote
  (``domain/legacy_reader.py``, the same oracle P1.3 built and P3.3 later
  validates against). Because the record is derived from the legacy state
  rather than hand-assembled beside it, the two cannot independently drift
  for the relational fields (parent/host/owner/opening_link/polygon_ids) -
  only the authored-only fields (provenance, generator, params, lod) are
  set explicitly, since legacy state has no way to express them.

Per-object storage (ADR 0009): the Window's record lives on the fill
object; the host WallSurface's record is read, updated (not replaced) and
written back onto the wall object, so any of its own record content from
outside this call is preserved.
"""
from __future__ import annotations

from dataclasses import replace

from ...object_helpers import face_material
from ...facade_placement import (
    FacadeElementDefinition,
    PlacementRequest,
    _unify_opening_ids,
    place_semantic_element,
    surface_uv_from_world_point,
)
from ...facade_surface_resolver import resolve_surface_context_for_face
from ...ring_opening_sync import OPENING_LINK_KEY, remove_wall_ring_faces, wall_interior_ring_faces
from ..domain import host_openings, ids, kinds
from ..domain.delete_lifecycle import DeleteFeatureError, plan_window_delete
from ..domain.legacy_reader import neutral_kind, read_legacy_scene
from ..domain.records import RecordSet
from . import display_regen, legacy_views, placement_rollback, registry, storage

__all__ = [
    "CreateFeatureError", "DeleteFeatureError", "DuplicateFeatureError",
    "create_feature", "create_preflight", "delete_feature", "initialize_duplicate",
]


class CreateFeatureError(ValueError):
    """``create_feature`` refused or failed before/while writing records."""


class DuplicateFeatureError(ValueError):
    """A native duplicate cannot be initialized as an independent feature."""


def _ancestor_chain(obj):
    chain = []
    seen = set()
    current = obj.parent
    while current is not None and current.name not in seen:
        seen.add(current.name)
        chain.append(current)
        current = current.parent
    return chain


def _exterior_face_world_coords(obj, polygon_id):
    """World-space vertex loop of the one non-interior face whose material
    carries ``gml_polygon_id == polygon_id``, or ``None`` if none does -
    the same identity convention ``domain/legacy_reader.py`` uses to build
    a ``PolygonRecord`` from a material in the first place."""
    mesh = obj.data
    if mesh is None:
        return None
    mw = obj.matrix_world
    for poly in mesh.polygons:
        mat = face_material(mesh, poly)
        if mat is None or mat.get("Interior"):
            continue
        if str(mat.get("gml_polygon_id") or "").strip() == polygon_id:
            return tuple(tuple(mw @ mesh.vertices[vi].co) for vi in poly.vertices)
    return None


def _commit_window_polygon(fill, polygon):
    coords = _exterior_face_world_coords(fill, polygon.polygon_id)
    if coords is None or len(set(coords)) < 3:
        return polygon  # can't commit safely; leave ids_only rather than guess
    exterior = replace(
        polygon.exterior, coords=coords,
        ring_id=polygon.exterior.ring_id or ids.ring_id(polygon.polygon_id, 0),
    )
    return replace(polygon, exterior=exterior, geometry_state="committed")


def _commit_wall_polygon(wall, polygon, opening_link, ring_faces_by_link, stored=None):
    """Commit ``polygon`` (the wall's host polygon) for the opening identified
    by ``opening_link``: live exterior and live ring for that opening, every
    sibling ring kept from ``stored`` (the wall record's current copy of the
    polygon) - see ``domain/host_openings.commit_host_polygon`` (CAC-P4.10)."""
    if not any(r.opening_link == opening_link for r in polygon.interiors):
        return polygon
    return host_openings.commit_host_polygon(
        polygon, opening_link, _exterior_face_world_coords(wall, polygon.polygon_id),
        ring_faces_by_link, stored,
    )


def create_feature(kind_name, surface, definition, placement, *, generator=None, existing_gml_id=None,
                   resolving_duplicate=None):
    """Place a ``kind_name`` feature and write its record + the host's ring record.

    ``surface``/``definition``/``placement`` are the same three explicit
    arguments ``place_semantic_element`` takes for the Draw path (facade
    ``SurfaceContext``/``FacadeElementDefinition``/``PlacementRequest``).
    ``generator`` overrides the kind's default ``FeatureRecord.generator``
    (e.g. Semantic Stamp passes ``"semantic_stamp"`` - CAC-P3.2a - so a
    stamped copy's record is distinguishable from one drawn directly);
    omitted, the kind's own generator name is used (Draw's Window).

    ``existing_gml_id`` (CAC-P4.3): when given, the freshly minted id
    ``place_semantic_element`` stamps onto the new fill object is
    overwritten with this one *before* anything is read back or written,
    so every derived record/registry entry below is keyed by it - the
    identity of an existing feature survives Blender's F9 redo-panel
    mechanism destroying and recreating the object (see
    ``ops/facade_authoring.py::CGML_OT_facade_draw.execute``), even though
    the geometry and both records are otherwise rebuilt exactly as a fresh
    ``create_feature`` call would. Omitted (the default), a fresh id is
    minted as before - this is still the one and only creation/identity
    code path, not a second F9-specific one.

    ``resolving_duplicate`` (CAC-P4.7/P4.10): the unresolved native Shift+D
    copy ``initialize_duplicate`` is replacing. It is left out of the host
    preflight, and its own ``duplicate_gml_id``/``duplicate_record`` collision
    is tolerated there - that collision is what this call resolves.

    Atomic (CAC-P4.10, AB-20):

    1. ``create_preflight`` refuses before anything is touched: host closure
       inconsistent, unmanaged rings on the host (Case B), malformed records
       in the placement's reach, or a LoD promotion that would raise a
       record-backed feature above its recorded LoD.
    2. Placement, then both the Window's and the host wall's ``RecordSet``
       are derived and validated; only then are both written.
    3. Any failure after the preflight restores the scene exactly
       (``placement_rollback``): objects, meshes, materials, the wall mesh,
       custom properties (records, LoD) and the wall's mode.

    Returns the created fill object. Raises ``CreateFeatureError`` (or
    ``KindRegistryError``, or whatever ``place_semantic_element`` raises)
    with the scene exactly as it was before the call.
    """
    kind = kinds.ensure_enabled(kind_name)

    wall = registry.resolve(surface.semantic_id, surface.object_name)
    if wall is None or wall.type != "MESH":
        raise CreateFeatureError(
            f"host {surface.object_name!r} ({surface.semantic_id!r}) no longer exists"
        )
    host_kind_name = neutral_kind(wall.get("cgml3_feature"))
    kinds.ensure_host_allowed(kind_name, host_kind_name)

    params = {"width": definition.width, "height": definition.height, "depth": definition.depth}
    problems = kinds.validate_params(kind_name, params)
    if problems:
        raise CreateFeatureError("; ".join(problems))

    face_index = surface.face_indices[0] if surface.face_indices else -1
    exclude = (resolving_duplicate,) if resolving_duplicate is not None else ()
    tolerate = str(resolving_duplicate.get("gml_id") or "").strip() if resolving_duplicate is not None else ""
    # Nothing mutated above this line or inside the preflight: a refusal here
    # is a clean no-op, as the AB-17 rectangle-vs-host-face check inside
    # place_semantic_element itself already guarantees for its own class of
    # refusal.
    create_preflight(kind_name, wall, face_index, exclude=exclude, tolerate_duplicate_of=tolerate)

    snapshot = placement_rollback.capture(wall)
    try:
        fill = place_semantic_element(definition, surface, placement)
        window_records, wall_record = _derive_created_records(
            kind_name, kind, wall, fill, params, generator, existing_gml_id,
        )
        # Both derived and validated above; written only now, so a refusal can
        # never leave a Window record behind without its host's ring record.
        storage.write_record(fill, window_records)
        storage.write_record(wall, wall_record)
        # CAC-P3b.3: the wall + hosted-Window display mesh is derived state,
        # kept in sync with every commit that changes the records it is
        # built from (edit_session.commit()'s own seam) - a fresh create
        # (Draw or its F9 redo re-run, which is this same call with
        # existing_gml_id set) is no exception. A display failure here is
        # caught by the same ``except`` below as any other failure in this
        # block, so ``snapshot.restore()`` undoes the display write too
        # (new display objects are removed as "created since capture";
        # a pre-existing sibling's display, restored first by
        # ``display_regen``'s own snapshot/restore, is left untouched).
        window_feature = window_records.features[0]
        wall_feature = wall_record.feature(str(wall.get("gml_id") or "").strip())
        display_regen.regenerate_or_restore(fill, wall, window_feature, wall_feature)
    except Exception:
        snapshot.restore()
        raise
    snapshot.discard()

    registry.REGISTRY.mark_dirty()
    return fill


def _derive_created_records(kind_name, kind, wall, fill, params, generator, existing_gml_id):
    """``(window RecordSet, wall RecordSet)`` for a just-placed ``fill``, both
    validated, neither written (``create_feature`` writes them)."""
    existing_gml_id = str(existing_gml_id or "").strip()
    if existing_gml_id:
        # Reuse an existing feature's identity (CAC-P4.3 F9 resize) instead
        # of the id place_semantic_element just minted for this brand-new
        # object - nothing has been read back yet, so this is still a clean
        # override, not a correction of anything already derived.
        fill["gml_id"] = existing_gml_id
        # CAC-P4.3 follow-up: place_semantic_element already stamped the
        # freshly-minted id it just overwrote onto fill's own material
        # OPENING_ID_KEYS (_unify_opening_ids's own invariant, AB-09/P2.5.5:
        # "must equal the fill object's gml_id") and its Openings_Cutter-
        # minted ``con_surface_id`` (also expected to equal the fill's own
        # gml_id - see ``_place_interior_ring_opening``'s own
        # ``ext_mat.get("con_surface_id")`` read). Left alone, both would
        # silently re-diverge from the preserved object identity on every
        # resize, reopening AB-09's "one id per authored opening" invariant
        # for exactly this case. Re-stamp both with the preserved id so the
        # material channel and the object channel agree, as they do for a
        # first draw.
        #
        # Verified NOT sufficient on its own (CAC-P4.3 follow-up
        # investigation): the legacy v2/v3 exporters' *literal exported*
        # WindowSurface gml:id, for a plain (non-Room) WallSurface host,
        # comes from a further-downstream, per-export scene-flattening
        # pass (observed reading a distinct merged/join mesh object, not
        # ``fill`` itself) whose own id derivation is decoupled from both
        # of the properties re-stamped here. That pass sits well outside
        # ``usecases.py``/``ops/facade_authoring.py``'s reach and outside
        # what a small, scoped fix can safely correct - see the P4.3
        # follow-up report. This re-stamp is kept because it is correct
        # and independently valuable (it keeps the fill object's own
        # material properties internally consistent with its gml_id, the
        # AB-09 invariant ``_unify_opening_ids`` already documents), not
        # because it fully closes the exported-id gap.
        _unify_opening_ids(fill.data, existing_gml_id)
        for _material in fill.data.materials:
            if _material is not None and "con_surface_id" in _material:
                _material["con_surface_id"] = existing_gml_id

    scene_objects = [wall, fill, *_ancestor_chain(wall)]
    legacy_scene = read_legacy_scene(legacy_views.scene_views(scene_objects))

    new_gml_id = str(fill.get("gml_id") or "").strip()
    window_feature = legacy_scene.records.feature(new_gml_id)
    if window_feature is None:
        raise CreateFeatureError(
            f"internal error: legacy state for the new {kind_name} {new_gml_id!r} "
            "could not be read back after placement"
        )
    opening_link = str(fill.get(OPENING_LINK_KEY) or "").strip()

    window_feature = replace(
        window_feature,
        semantic_type=window_feature.semantic_type or kind_name,
        generator=generator or kind.generator,
        params=params,
        provenance="authored",
        lod=3,
        lod_origin="authored",
    )
    window_polygons = tuple(
        _commit_window_polygon(fill, p) for p in legacy_scene.records.polygons_of(new_gml_id)
    )
    window_feature = replace(window_feature, polygon_ids=tuple(p.polygon_id for p in window_polygons))

    if not window_feature.host_id or window_feature.host_id != window_feature.parent_id:
        raise CreateFeatureError(
            f"new {kind_name} {new_gml_id!r}: host_id {window_feature.host_id!r} does not match "
            f"parent_id {window_feature.parent_id!r} after placement"
        )

    window_records = RecordSet(features=(window_feature,), polygons=window_polygons)
    problems = window_records.validate()
    if problems:
        raise CreateFeatureError(f"new {kind_name} {new_gml_id!r} record is invalid: {problems}")

    wall_gml_id = str(wall.get("gml_id") or "").strip()
    wall_feature = legacy_scene.records.feature(wall_gml_id)
    ring_faces_by_link = {
        link: tuple(tuple(v) for v in verts)
        for _, verts, link in wall_interior_ring_faces(wall) if link
    }
    existing_wall_record = storage.read_record(wall) or RecordSet()
    existing_wall_polygons = {p.polygon_id: p for p in existing_wall_record.polygons}
    fresh_wall_polygons = {
        p.polygon_id: _commit_wall_polygon(
            wall, p, opening_link, ring_faces_by_link, existing_wall_polygons.get(p.polygon_id),
        )
        for p in legacy_scene.records.polygons_of(wall_gml_id)
    }
    if not any(any(r.opening_link == opening_link for r in p.interiors) for p in fresh_wall_polygons.values()):
        raise CreateFeatureError(
            f"host wall {wall_gml_id!r} gained no interior ring for opening_link {opening_link!r}"
        )

    wall_record = existing_wall_record.with_feature(
        replace(wall_feature, polygon_ids=tuple(fresh_wall_polygons.keys()))
    )
    for polygon_id, polygon in fresh_wall_polygons.items():
        # Preserve any polygon this call didn't touch as it already was
        # (e.g. committed by an earlier create_feature call on this same
        # wall) rather than overwrite it with a fresh, ids_only re-read.
        touched = any(r.opening_link == opening_link for r in polygon.interiors)
        final = polygon if touched or polygon_id not in existing_wall_polygons else existing_wall_polygons[polygon_id]
        wall_record = wall_record.with_polygon(final)

    problems = wall_record.validate()
    if problems:
        raise CreateFeatureError(f"host wall {wall_gml_id!r} record is invalid: {problems}")
    return window_records, wall_record


def _host_polygon_id(wall, face_index):
    """``gml_polygon_id`` of the exterior face ``face_index`` of ``wall``, or
    ``""`` (a face whose polygon id ``create_joined_surfaces`` will mint)."""
    mesh = wall.data
    if mesh is None or not (0 <= face_index < len(mesh.polygons)):
        return ""
    mat = face_material(mesh, mesh.polygons[face_index])
    if mat is None or mat.get("Interior"):
        return ""
    return str(mat.get("gml_polygon_id") or "").strip()


def _all_descendants(obj):
    out, stack = [], list(obj.children)
    while stack:
        child = stack.pop()
        out.append(child)
        stack.extend(child.children)
    return out


def create_preflight(kind_name, wall, face_index, *, exclude=(), tolerate_duplicate_of=""):
    """Refuse a record-backed create of ``kind_name`` on ``wall`` before any
    mutation (CAC-P4.10). Raises ``CreateFeatureError``; returns ``None`` when
    the placement may proceed. Read-only.

    - **Malformed record** on any object the placement can write (the wall's
      whole top-level feature, which LoD promotion rewrites): refused, fail
      closed, as the P3.0 guard always did.
    - **Case A** - the host closure (``consistency_view.host_closure_findings``:
      the wall and every opening it hosts) must have no blocking finding. A
      consistent record-backed wall with other record-backed Windows passes;
      a half-authored wall (``unrecorded_host``, AB-20's old debris) or one
      with a genuinely broken opening does not.
    - **Case B** - ``domain/host_openings.unmanaged_host_rings``: an unowned
      ring on the target polygon (imported, AB-20), or a linked ring of an
      opening without a record anywhere on the wall (AB-20b).
    - **LoD** - ``lod_helpers.promotion_changes`` (dry run): the promotion may
      not raise a record-backed feature above its recorded ``lod``.
    """
    from ....Openings_Cutter.Openings_Cutter import detect_citygml_version_for_object
    from ...lod_helpers import lod_promotion_root, opening_lod_for_version, promotion_changes
    from ..domain import consistency
    from . import consistency_view

    wall_gml_id = str(wall.get("gml_id") or "").strip()
    if not wall_gml_id:
        raise CreateFeatureError(f"host {wall.name!r} carries no gml_id")

    root = lod_promotion_root(wall) or wall
    reach = [root, *_all_descendants(root)]
    if all(o is not wall for o in reach):
        reach.append(wall)
    malformed = sorted(o.name for o in reach if storage.record_state(o) == storage.RECORD_MALFORMED)
    if malformed:
        more = f" (+{len(malformed) - 1} more)" if len(malformed) > 1 else ""
        raise CreateFeatureError(
            f"{malformed[0]!r} has a malformed authoring record ({storage.RECORD_KEY} cannot be read); "
            f"repair or remove it before placing a record-backed opening{more}"
        )

    findings = consistency_view.host_closure_findings(
        wall, wall_gml_id, exclude=exclude, tolerate_duplicate_of=tolerate_duplicate_of,
    )
    if consistency.is_blocking(findings):
        problems = "; ".join(f"{f.code}: {f.message}" for f in findings if f.severity == consistency.ERROR)
        raise CreateFeatureError(f"host wall {wall.name!r} is not consistent: {problems}")

    scope = consistency_view.host_validation_scope(wall, exclude=exclude)
    owned = {
        f.opening_link for f in consistency_view.merged_record_set(scope).features
        if f.kind in consistency.OPENING_KINDS and f.host_id == wall_gml_id and f.opening_link
    }
    legacy_scene = read_legacy_scene(legacy_views.scene_views([wall, *_ancestor_chain(wall)]))
    unmanaged = host_openings.unmanaged_host_rings(
        legacy_scene.records.polygons_of(wall_gml_id), owned, _host_polygon_id(wall, face_index),
    )
    if unmanaged:
        unlinked = sum(1 for row in unmanaged if row[3] == host_openings.UNLINKED)
        parts = []
        if unlinked:
            parts.append(f"{unlinked} imported opening ring(s) on the target polygon")
        if len(unmanaged) > unlinked:
            parts.append(f"{len(unmanaged) - unlinked} ring(s) of openings that carry no record")
        raise CreateFeatureError(
            f"host wall {wall.name!r} has openings the authoring core does not manage "
            f"({', '.join(parts)}); a record-backed {kind_name} cannot be added to it"
        )

    lod, changed = promotion_changes(wall, opening_lod_for_version(wall, detect_citygml_version_for_object(wall)))
    for obj in changed:
        if storage.record_state(obj) != storage.RECORD_OK:
            continue
        for feature in storage.read_record(obj).features:
            if feature.lod is not None and lod > feature.lod:
                raise CreateFeatureError(
                    f"placing a {kind_name} would raise {obj.name!r} to LoD{lod}, above its recorded "
                    f"LoD{feature.lod}"
                )


def _duplicate_center_world(obj):
    """Centre of a native duplicate's displayed mesh, in world coordinates."""
    mesh = getattr(obj, "data", None)
    if mesh is None or not mesh.vertices:
        raise DuplicateFeatureError(f"'{obj.name}' has no mesh vertices to initialize")
    from mathutils import Vector

    points = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    return sum(points, Vector()) / len(points)


def _nearest_host_face(wall, point):
    """The exterior host face nearest to ``point``.

    Native Shift+D preserves a Window's parent and its mesh but creates no
    second wall opening.  Reusing the existing placement kernel requires a
    concrete host face.  This is deliberately a geometric lookup, never an
    object-name lookup; the normal distance is exact for a valid duplicate
    still on its wall and makes a moved/off-wall duplicate fail later in the
    existing AB-17 rectangle validation rather than guessing a new host.
    """
    best = None
    for poly in wall.data.polygons:
        material = face_material(wall.data, poly)
        if material is None or material.get("Interior"):
            continue
        world_center = wall.matrix_world @ poly.center
        normal = (wall.matrix_world.to_3x3() @ poly.normal).normalized()
        distance = abs((point - world_center).dot(normal))
        candidate = (distance, poly.index)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise DuplicateFeatureError(f"host wall '{wall.name}' has no exterior face")
    return best[1]


def initialize_duplicate(duplicate):
    """Turn one native Shift+D Window copy into a new semantic Window.

    Blender copies custom properties, ``_cgml_record``, materials, parent and
    mesh links verbatim.  That temporary state is intentionally invalid: the
    duplicate shares the original ``gml_id`` and has no own host-wall ring,
    so export pre-flight refuses it.  This explicit use case replaces the
    temporary object only after it can create a fresh Window through the
    ordinary ``create_feature`` lifecycle.  The lifecycle mints the feature
    and opening identities and writes the new, independent wall RingRecord;
    no handler mutates authoritative records (R4).

    A failed placement leaves the original and the unresolved duplicate
    untouched.  The caller is a single UNDO operator, so successful replace
    / undo / redo is one Blender transaction.
    """
    if duplicate is None or getattr(duplicate, "type", None) != "MESH":
        raise DuplicateFeatureError("select a duplicated record-backed Window")
    record_set = storage.read_record(duplicate)
    old_id = str(duplicate.get("gml_id") or "").strip()
    feature = record_set.feature(old_id) if record_set is not None and old_id else None
    if feature is None or feature.kind != "Window":
        raise DuplicateFeatureError("selected object is not a record-backed Window duplicate")

    siblings = [
        obj for obj in registry.REGISTRY.get_all(old_id)
        if obj is not duplicate and storage.has_record(obj)
    ]
    if len(siblings) != 1:
        raise DuplicateFeatureError(
            f"'{old_id}' must have exactly one original Window before initialization; found {len(siblings)}"
        )
    host = registry.resolve(feature.host_id, duplicate.parent.name if duplicate.parent else "")
    if host is None or host.type != "MESH":
        raise DuplicateFeatureError(f"host {feature.host_id!r} no longer exists")

    params = feature.params or {}
    try:
        definition = FacadeElementDefinition(
            semantic_type="Window",
            width=float(params["width"]),
            height=float(params["height"]),
            depth=float(params["depth"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DuplicateFeatureError(f"'{old_id}' has no usable Window authoring parameters") from exc

    center = _duplicate_center_world(duplicate)
    face_index = _nearest_host_face(host, center)
    try:
        surface = resolve_surface_context_for_face(host, face_index)
    except ValueError as exc:
        raise DuplicateFeatureError(str(exc)) from exc
    placement = PlacementRequest(
        position_uv=surface_uv_from_world_point(surface, center),
        source="native_duplicate",
    )

    # ``create_feature`` validates before a placement refusal and returns a
    # new object only once the legacy mirror and both records are complete.
    # Do not delete the unresolved copy until that succeeds.
    try:
        replacement = create_feature(
            "Window", surface, definition, placement, generator="native_duplicate",
            resolving_duplicate=duplicate,
        )
    except (CreateFeatureError, ValueError) as exc:
        raise DuplicateFeatureError(f"duplicate placement refused: {exc}") from exc

    import bpy

    mesh = duplicate.data
    bpy.data.objects.remove(duplicate, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    registry.REGISTRY.mark_dirty()
    return replacement


def delete_feature(gml_id):
    """Delete a record-backed Window and its complete semantic feature (CAC-P3.4).

    Resolves the Window by ``gml_id`` (never by object name/collection
    position - R2), resolves its host WallSurface through the same
    ``host_id``/``opening_link`` relations ``create_feature`` wrote, then:

    1. removes the wall's own ``gml:interior`` ring face(s) for that
       ``opening_link`` from its legacy mesh (``remove_wall_ring_faces``);
    2. writes the host wall's record back with that ring gone from every
       polygon that carried it;
    3. removes the Window object (and its mesh datablock, if now unused).

    Every relationship is resolved and validated (``domain/delete_lifecycle
    .plan_window_delete``) *before* any of the three steps above runs, so a
    refusal is always a clean no-op - no stale references, no partial
    mutation (PLAN.md P3.4 ERROR/NO-OP CASES). Raises ``DeleteFeatureError``
    on refusal. One call, one undo step, when driven from an operator with
    ``bl_options = {'REGISTER', 'UNDO'}`` (the Blender operator undo model
    already covers this - P3.4 does not need its own transaction layer).
    """
    gml_id = str(gml_id or "").strip()
    window = registry.resolve(gml_id) if gml_id else None
    if window is None or window.type != "MESH":
        raise DeleteFeatureError(f"{gml_id!r}: no such object (already deleted?)")

    window_record_set = storage.read_record(window)
    window_feature = window_record_set.feature(gml_id) if window_record_set is not None else None
    host_id = str(window_feature.host_id or "").strip() if window_feature is not None else ""

    wall = registry.resolve(host_id, window.parent.name if window.parent else "") if host_id else None
    wall_record_set = storage.read_record(wall) if wall is not None else None

    window_feature, wall_feature, new_wall_record_set, opening_link = plan_window_delete(
        gml_id, window_record_set, host_id, wall_record_set,
    )
    if wall is None or wall.type != "MESH":
        raise DeleteFeatureError(f"host {host_id!r}: object no longer exists")

    removed = remove_wall_ring_faces(wall, opening_link)
    if removed == 0:
        raise DeleteFeatureError(
            f"host {host_id!r}: no legacy ring face found for opening_link {opening_link!r}"
        )

    storage.write_record(wall, new_wall_record_set)

    # CAC-P3b.3: regenerate the wall's display from the just-written,
    # ring-removed record and drop the deleted Window's own display object,
    # before the Window object itself is removed - the display object's
    # name is derived from it, and a failure here must still find both
    # objects in place to restore their pre-delete display onto.
    display_regen.regenerate_after_delete_or_restore(wall, window, wall_feature, window_feature.gml_id)

    import bpy

    mesh = window.data
    bpy.data.objects.remove(window, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)

    registry.REGISTRY.mark_dirty()
    return gml_id
