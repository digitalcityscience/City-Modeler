# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record-level RoofSurface delete/duplicate-init lifecycle (CAC-P6.4).

A RoofSurface has no host-wall ring to maintain the way a Window does
(``blender/roof_edit_session.py``'s own docstring: "no opening_link, no
host_id dual-write, no parent-ring recut" - one object, one record). Both
lifecycle operations here are correspondingly simpler than their Window
counterparts in ``domain/delete_lifecycle.py``:

- delete needs only the Roof's own feature, confirmed to exist and to be
  the right kind - there is no sibling record to re-derive, unlike a
  Window's host wall ring;
- duplicate (D-4: a duplicate never retains the source's semantic
  identity) mints fresh feature/polygon/ring ids onto a copy of the Roof's
  own already-committed geometry. There is no placement kernel to re-run
  (Roof has none - P6.4 does not add one) and no host to re-ring; the
  duplicate's geometry is simply the source's geometry, verbatim, under
  new identity.

Pure Python only (rule R1, scripts/tests/unit/test_cac_domain_has_no_bpy.py).
Validating everything here before ``blender/roof_usecases.py`` touches a
single mesh/material/object is what gives both operations their "no
partial mutation on refusal" guarantee, the same shape
``domain/delete_lifecycle.py::plan_window_delete`` already established.
"""
from __future__ import annotations

from dataclasses import replace

from .ids import mint_gml_id
from .ids import ring_id as mint_ring_id
from .records import RecordSet

ROOF_KIND = "RoofSurface"


class RoofLifecycleError(ValueError):
    """Roof delete/duplicate-init refused before any Blender state was mutated."""


def plan_roof_delete(gml_id, record_set):
    """The RoofSurface's own feature, confirmed deletable.

    Raises ``RoofLifecycleError`` - nothing mutated - if ``record_set`` is
    absent, does not name ``gml_id``, or the named feature's ``kind`` is
    not ``RoofSurface``. Unlike ``plan_window_delete`` there is no host/
    ring relation to validate: deleting a RoofSurface's own object,
    record and display is already the complete operation.
    """
    gml_id = str(gml_id or "").strip()
    if not gml_id:
        raise RoofLifecycleError("gml_id is required")
    if record_set is None:
        raise RoofLifecycleError(f"{gml_id!r}: object carries no authoring record")
    feature = record_set.feature(gml_id)
    if feature is None:
        raise RoofLifecycleError(
            f"{gml_id!r}: record does not name this object's gml_id (already deleted?)"
        )
    if feature.kind != ROOF_KIND:
        raise RoofLifecycleError(
            f"{gml_id!r}: kind {feature.kind!r} is not supported by this delete lifecycle "
            f"({ROOF_KIND} only, CAC-P6.4)"
        )
    return feature


def plan_roof_duplicate(old_gml_id, record_set, *, uuid_factory=None):
    """A fresh, independent ``RecordSet`` for one RoofSurface duplicate
    (D-4: source semantic ids != duplicate semantic ids).

    New feature ``gml_id``, new polygon ids, new ring ids (exterior and
    every interior, generic or not), identical coordinates/role/
    ``geometry_state`` per ring/polygon, and every other feature field
    (``kind``, ``parent_id``, ``owner_id``, ``lod``, ``lod_origin``,
    ``provenance``, ``params``) preserved from the source - except
    ``generator``, re-stamped ``"native_duplicate"`` (the same convention
    ``blender/usecases.py::initialize_duplicate`` already uses for a
    Window duplicate).

    Returns ``(new_record_set, new_gml_id, polygon_id_map)``.
    ``polygon_id_map`` is ``{old_polygon_id: new_polygon_id}``: the
    ``blender/`` adapter uses it to re-tag the duplicate's own per-face
    material identity (``gml_polygon_id``), so the duplicate's live
    geometry never again resolves under the source's polygon ids (CAC-P6.4
    S9/S18 - two objects must never answer to the same polygon identity).

    Raises ``RoofLifecycleError`` - nothing mutated by this pure step, it
    builds the complete candidate before the caller writes anything - if
    ``record_set`` is absent, does not name ``old_gml_id``, the named
    feature's ``kind`` is not ``RoofSurface``, or the freshly-id'd record
    would itself be invalid (defensive; ``RecordSet.validate()`` should
    never actually reject output built this way).
    """
    old_gml_id = str(old_gml_id or "").strip()
    if record_set is None:
        raise RoofLifecycleError(f"{old_gml_id!r}: object carries no authoring record")
    feature = record_set.feature(old_gml_id)
    if feature is None:
        raise RoofLifecycleError(f"{old_gml_id!r}: record does not name this object's gml_id")
    if feature.kind != ROOF_KIND:
        raise RoofLifecycleError(
            f"{old_gml_id!r}: kind {feature.kind!r} is not supported by this duplicate "
            f"lifecycle ({ROOF_KIND} only, CAC-P6.4)"
        )

    new_gml_id = mint_gml_id("ID", uuid_factory)
    polygon_id_map = {}
    new_polygons = []
    for polygon in record_set.polygons_of(old_gml_id):
        new_pid = mint_gml_id("ID", uuid_factory)
        polygon_id_map[polygon.polygon_id] = new_pid
        exterior = replace(polygon.exterior, ring_id=mint_ring_id(new_pid, 0))
        interiors = tuple(
            replace(ring, ring_id=mint_ring_id(new_pid, index + 1))
            for index, ring in enumerate(polygon.interiors)
        )
        new_polygons.append(replace(
            polygon, polygon_id=new_pid, feature_id=new_gml_id,
            exterior=exterior, interiors=interiors,
        ))

    new_feature = replace(
        feature, gml_id=new_gml_id, generator="native_duplicate",
        polygon_ids=tuple(polygon_id_map[pid] for pid in feature.polygon_ids if pid in polygon_id_map),
    )
    new_record_set = RecordSet(features=(new_feature,), polygons=tuple(new_polygons))
    problems = new_record_set.validate()
    if problems:
        raise RoofLifecycleError(f"{old_gml_id!r}: duplicate record would be invalid: {problems}")
    return new_record_set, new_gml_id, polygon_id_map
