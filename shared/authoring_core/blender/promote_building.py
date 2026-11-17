# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Blender-facing orchestration for CAC-P5.9 Building promotion.

``promote_building(building_obj)`` is the one entry point: read the
Building's current legacy + persisted state, build a complete candidate
record graph (``domain/promote_building.py``, pure - nothing here decides
what to promote, only reads/writes on its behalf), then write it
atomically. See ``docs/agent/authoring-core/issues/CAC-P5.9.md`` for the
three supported promotion cases and the atomicity contract.

Read-before-write architecture (rule 4): candidate graph first, writes
second. Nothing here mutates a scene object until the whole candidate has
been derived and validated.
"""
from __future__ import annotations

from dataclasses import replace

from ..domain import ids
from ..domain.host_openings import commit_host_polygon
from ..domain.promote_building import PromotionError, build_promotion_candidate
from ..domain.records import RecordSet
from . import building_root, consistency_view, legacy_views, registry, storage

__all__ = ["PromotionError", "PromotionResult", "promote_building"]


class PromotionResult:
    """What ``promote_building`` did (never what it merely planned to do)."""

    def __init__(self, building_gml_id, is_noop, promoted_feature_ids):
        self.building_gml_id = building_gml_id
        self.is_noop = is_noop
        self.promoted_feature_ids = promoted_feature_ids


def _commit_polygon(polygon, geometry):
    """``polygon`` with every ring it carries filled from live world-frame
    coordinates (D-2: Blender world frame, no CRS transform), read from
    ``geometry`` - a scope-wide ``consistency.GeometryFact``
    (``consistency_view.geometry_fact``), the same live-geometry oracle
    P3.3/P4.10 already validate records against. Scope-wide, not
    per-owner-object: a feature's polygon materials are not guaranteed to
    all live on the one object carrying its ``gml_id`` (a real fixture
    surfaced this - a multi-polygon Window whose polygon-bearing faces span
    more than one mesh object in its own hierarchy), the same reason
    ``geometry_fact`` itself is built by walking every mesh object in scope
    rather than one object at a time. A polygon whose interior rings are
    all ordinary holes (no ``opening_link`` at all - see
    ``_commit_generic_interior_rings``, CAC-P6.1a) is committed the same
    way; one with any Window/Door opening ring keeps the existing
    ``commit_host_polygon`` path untouched, and a polygon mixing both stays
    ``ids_only`` (that combination is not resolvable through either path -
    an explicit, not silent, refusal)."""
    exterior_coords = geometry.polygons.get(polygon.polygon_id)
    if not polygon.interiors:
        if exterior_coords is None or len(set(exterior_coords)) < 3:
            return polygon  # can't commit safely; leave ids_only rather than guess
        exterior = replace(
            polygon.exterior, coords=exterior_coords,
            ring_id=polygon.exterior.ring_id or f"{polygon.polygon_id}_0_",
        )
        return replace(polygon, exterior=exterior, geometry_state="committed")

    if not any(ring.opening_link for ring in polygon.interiors):
        # CAC-P6.1a: an ordinary interior ring - a hole with no Window/Door
        # behind it (a Roof skylight cutout, say) - not the Window/opening_link
        # relationship below, which stays untouched.
        return _commit_generic_interior_rings(polygon, exterior_coords, geometry)

    committed = polygon
    for ring in polygon.interiors:
        if not ring.opening_link:
            continue
        committed = commit_host_polygon(
            committed, ring.opening_link, exterior_coords, geometry.rings, stored=committed,
        )
    return committed


def _commit_generic_interior_rings(polygon, exterior_coords, geometry):
    """``polygon`` committed when every one of its interior rings carries no
    ``opening_link`` at all (CAC-P6.1a). Unlike a Window's ring, a generic
    ring has no stable key to look its live coordinates up by, so it is
    matched to ``geometry.generic_rings[polygon.polygon_id]`` positionally,
    in the same deterministic face order both were built in. All-or-nothing:
    if the exterior or any interior ring cannot be resolved this way,
    ``polygon`` is returned exactly as given - ``ids_only`` - rather than
    partially committed (rule: never commit an exterior while an interior
    ring stays without coordinates)."""
    if exterior_coords is None or len(set(exterior_coords)) < 3:
        return polygon
    live = geometry.generic_rings.get(polygon.polygon_id, ())
    if len(live) < len(polygon.interiors):
        return polygon  # can't resolve every ring; leave ids_only rather than guess
    interiors = []
    for index, (ring, coords) in enumerate(zip(polygon.interiors, live), start=1):
        if coords is None or len(set(coords)) < 3:
            return polygon
        interiors.append(replace(
            ring, coords=coords, ring_id=ring.ring_id or ids.ring_id(polygon.polygon_id, index),
        ))
    exterior = replace(
        polygon.exterior, coords=tuple(tuple(c) for c in exterior_coords),
        ring_id=polygon.exterior.ring_id or ids.ring_id(polygon.polygon_id, 0),
    )
    return replace(polygon, exterior=exterior, interiors=tuple(interiors), geometry_state="committed")


def _commit_legacy_geometry(legacy_record_set, persisted, geometry):
    """``legacy_record_set`` with every polygon of a not-yet-persisted
    feature committed to live world coordinates. An already-persisted
    feature's polygons are left exactly as ``persisted`` has them - Case
    B/C features are never re-derived from the mesh (rule 5)."""
    result = legacy_record_set
    for feature in legacy_record_set.features:
        if persisted.feature(feature.gml_id) is not None:
            continue
        for polygon in legacy_record_set.polygons_of(feature.gml_id):
            result = result.with_polygon(_commit_polygon(polygon, geometry))
    return result


def _rollback(snapshot):
    for obj, saved in snapshot:
        if saved is None:
            if storage.RECORD_KEY in obj.keys():
                del obj[storage.RECORD_KEY]
        else:
            obj[storage.RECORD_KEY] = saved


def promote_building(building_obj):
    """Promote ``building_obj`` to a complete, persisted record graph (CAC-P5.9).

    Building-scoped, deterministic, atomic and idempotent (see the P5.9
    task card for the full contract). Raises ``PromotionError`` with the
    scene completely untouched on any refusal or mid-write failure - no
    partially promoted Building is ever left behind.
    """
    building_gml_id = str(building_obj.get("gml_id") or "").strip()
    if not building_gml_id:
        raise PromotionError(f"{building_obj.name!r} carries no gml_id")

    scope = building_root.building_scope(building_obj)
    persisted = consistency_view.merged_record_set(scope)
    legacy_scene = legacy_views.read_scene(scope)
    geometry = consistency_view.geometry_fact(scope)
    legacy_committed = _commit_legacy_geometry(legacy_scene.records, persisted, geometry)
    findings = consistency_view.run_validation(scope)

    # Candidate graph first, writes second (rule 4): nothing below this line
    # decides what to promote - it only executes what has already been
    # derived and validated as one complete, valid RecordSet.
    plan = build_promotion_candidate(building_gml_id, persisted, legacy_committed, findings)

    if plan.is_noop:
        return PromotionResult(building_gml_id, True, ())

    write_map = {}
    for feature in plan.changed_features():
        obj = building_obj if feature.gml_id == building_gml_id else registry.resolve(feature.gml_id)
        if obj is None:
            raise PromotionError(
                f"{feature.gml_id!r}: no live object to write the promoted record onto"
            )
        record_set = RecordSet(features=(feature,), polygons=plan.candidate.polygons_of(feature.gml_id))
        problems = record_set.validate()
        if problems:
            raise PromotionError(f"{obj.name!r}: candidate record is invalid: {'; '.join(problems)}")
        write_map[obj] = record_set

    snapshot = [(obj, obj.get(storage.RECORD_KEY)) for obj in write_map]
    try:
        for obj, record_set in write_map.items():
            storage.write_record(obj, record_set)
        for obj, record_set in write_map.items():
            written = storage.read_record(obj)
            if written is None or written.to_dict() != record_set.to_dict():
                raise PromotionError(f"{obj.name!r}: written record did not verify after write")
    except Exception:
        _rollback(snapshot)
        registry.REGISTRY.mark_dirty()
        raise

    registry.REGISTRY.mark_dirty()
    promoted_ids = tuple(f.gml_id for record_set in write_map.values() for f in record_set.features)
    return PromotionResult(building_gml_id, False, promoted_ids)
