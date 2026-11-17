# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record-level Window delete lifecycle (CAC-P3.4, PLAN.md P3.4).

Pure: given the Window's own per-object ``RecordSet`` and its host
WallSurface's per-object ``RecordSet`` (ADR 0009 - each lives on its own
object), computes the wall record that results from removing the Window's
ring, or raises ``DeleteFeatureError`` before returning anything if any
expected relation is missing or the resulting wall record would be
invalid.

Validating everything here, before ``blender/usecases.py::delete_feature``
touches a single mesh or object, is what gives P3.4 its "no partial
mutation on refusal" guarantee (PLAN.md P3.4 ERROR/NO-OP CASES): every
failure path in this module runs before any Blender state changes.

Pure Python only: no bpy, bmesh, mathutils or gpu (rule R1, enforced by
scripts/tests/unit/test_cac_domain_has_no_bpy.py).
"""
from __future__ import annotations

from dataclasses import replace


class DeleteFeatureError(ValueError):
    """``delete_feature`` refused before any Blender state was mutated."""


def plan_window_delete(window_gml_id, window_record_set, wall_gml_id, wall_record_set):
    """The window's own feature, the wall's own feature, and the wall's
    ``RecordSet`` with the Window's ring removed from every polygon that
    carries it.

    Returns ``(window_feature, wall_feature, new_wall_record_set, opening_link)``.
    Raises ``DeleteFeatureError`` - mutating nothing - if:

    - ``window_record_set`` is absent, or does not name ``window_gml_id``
      (object exists but no record / already-deleted Window);
    - the named feature's ``kind`` is not ``"Window"`` (scope: Window only);
    - it carries no ``opening_link`` (nothing to un-ring);
    - ``wall_gml_id`` is empty (missing host);
    - ``wall_record_set`` is absent, or does not name ``wall_gml_id``;
    - no polygon of the wall's record carries a ring for that
      ``opening_link`` (wall record missing expected ring);
    - removing that ring would leave the wall's record invalid.
    """
    if window_record_set is None:
        raise DeleteFeatureError(f"{window_gml_id!r}: object carries no authoring record")
    window_feature = window_record_set.feature(window_gml_id)
    if window_feature is None:
        raise DeleteFeatureError(
            f"{window_gml_id!r}: record does not name this object's gml_id (already deleted?)"
        )
    if window_feature.kind != "Window":
        raise DeleteFeatureError(
            f"{window_gml_id!r}: kind {window_feature.kind!r} is not supported by "
            "delete_feature (Window only, CAC-P3.4)"
        )
    opening_link = window_feature.opening_link
    if not opening_link:
        raise DeleteFeatureError(f"{window_gml_id!r}: record has no opening_link")

    wall_gml_id = str(wall_gml_id or "").strip()
    if not wall_gml_id:
        raise DeleteFeatureError(f"{window_gml_id!r}: no host recorded (host_id empty)")
    if wall_record_set is None:
        raise DeleteFeatureError(f"host {wall_gml_id!r}: object carries no authoring record")
    wall_feature = wall_record_set.feature(wall_gml_id)
    if wall_feature is None:
        raise DeleteFeatureError(f"host {wall_gml_id!r}: record does not name this object's gml_id")

    ringed_polygons = [
        p for p in wall_record_set.polygons_of(wall_gml_id)
        if any(r.opening_link == opening_link for r in p.interiors)
    ]
    if not ringed_polygons:
        raise DeleteFeatureError(
            f"host {wall_gml_id!r}: record has no RingRecord for opening_link {opening_link!r}"
        )

    new_wall_record_set = wall_record_set
    for polygon in ringed_polygons:
        kept = tuple(r for r in polygon.interiors if r.opening_link != opening_link)
        new_wall_record_set = new_wall_record_set.with_polygon(replace(polygon, interiors=kept))

    problems = new_wall_record_set.validate()
    if problems:
        raise DeleteFeatureError(
            f"host {wall_gml_id!r}: record would become invalid after removing the ring: {problems}"
        )

    return window_feature, wall_feature, new_wall_record_set, opening_link
