# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Narrow legacy Door passthrough adapter (CAC-P5.5, PLAN.md P5.5).

Door is the only intentional legacy-passthrough feature kind in Phase 5.
This module extracts plain, Blender-independent ``ExportOpening`` facts
(``shared/authoring_core/domain/ir_to_groups.py``) for legacy-only Door
objects, so a promoted/IR-exportable Building does not silently lose its
Doors - without turning Door into a ``FeatureRecord``/``FeatureKind`` and
without building a generic per-feature hybrid exporter (PLAN.md explicitly
rejects both for P5.5).

```
legacy Door object (bpy)
    -> extract_door_passthrough (this module)
        -> tuple[ExportOpening, ...]              (plain data, no bpy)
            -> ir_to_groups.merge_door_passthrough  (domain layer, no bpy)
```

## Authority boundary

This module reads only the legacy facts needed to preserve today's Door
export behaviour: the Door object's own ``gml_id``, its polygon geometry,
its host/parent surface relation, and the LoD already stamped on it. It
never provides or overrides record-backed ``WallSurface``/``Window``
identity, geometry or relations - see ``_is_eligible_door`` below, which
is the single gate all of that runs through.

## Host relation (P5.0 channel investigation, PLAN.md P5.5)

The live CityGML 3 writer (``io/writer/exporter.py``) resolves a Door's
host surface from an *object-level* ``opening_surface_id`` property
(populated at import time from the opening's material, see
``io/reader/geometry.py``/``io/reader/materials.py``), grouping openings
by it (``openings_by_surface_id``) before calling the single live inline
channel, ``_export_hier_opening_v3``. ``guards.HOST_ID_KEY``
(``"filling_parent_surface_id"``) is the same relation under its older
name - both keys are already treated as synonyms by every writer/guard
site that reads them (``_first_nonempty_prop(..., ("filling_parent_
surface_id", "opening_surface_id"))``, ``guards.py``'s own host-closure
lookup). This module reads both, preferring ``HOST_ID_KEY`` to match
``guards.py``, and does not invent a third mechanism.

A second, *object-level* ``gml_parent`` property also exists in
``io/writer/exporter.py``, read only inside ``_write_filling_elements`` -
a function with zero call sites anywhere in the writer (verified via
repository-wide reference search). It is dead code from an earlier
attempt at the same "Door as fillingSurface" channel ``_export_hier_
opening_v3`` now serves live, and is unrelated to (and not a different
lifecycle stage of) ``filling_parent_surface_id``/``opening_surface_id``.
This module does not read ``gml_parent``.

## Channel behaviour (P5.0 channel investigation, PLAN.md P5.5)

CityGML 3 and CityGML 2 both write a Door through exactly two channels,
never one:

1. an **inline polygon channel** (``_write_inline_surface_opening`` -
   present, separately, in both ``io/writer/exporter.py`` and
   ``io_v2/writer/exporter.py``) that adds the opening's own polygon(s)
   as extra ``surfaceMember`` entries alongside its host surface's own
   geometry (the "hole" representation);
2. a **semantic filling/opening-element channel** that nests the Door as
   its own feature: ``_export_hier_opening_v3`` writes ``<con:filling
   Surface><con:DoorSurface>`` for CityGML 3; ``io_v2/writer/hierarchical_
   export.py`` writes the CityGML 2 equivalent, ``<bldg:opening><bldg:
   Door>`` (confirmed live via ``opening_container = SubElement(target_
   surface_el, Q(module_ns, "opening"))``, referenced from the v2 boundary-
   surface writer). CityGML 2 has no ``_write_filling_elements``-shaped
   dead second attempt; its one semantic channel lives in
   ``hierarchical_export.py`` rather than ``exporter.py``.

This module represents only channel 2's requirement - the Door's own
filling geometry, as ``ExportOpening.polygons`` - matching what ``Export
Opening.polygons`` already means for a record-backed Window (``ir_to_
groups.py``'s "Opening / hole representation" note: the host surface's
own interior-ring hole and the opening's own fill polygon are always two
separate objects). Channel 1's host-surface hole polygon is not this
module's concern: it belongs to the host ``ExportSurface.polygons``,
which P5.5 does not touch (record-backed IR already owns it, or - for a
legacy-only host surface - P5.6's writer adapter still reads the host
surface through the existing legacy path, unchanged).

## Determinism (PLAN.md P5.5)

The live v3 channel mints a fresh random id
(``_new_gml_id("UUID", ...)``/``uuid4().hex``) for every polygon and ring
on *every* export - legacy Door polygon/ring identity is already
non-deterministic today, and this module cannot and does not claim to
reproduce or improve that. What it *can* make deterministic is its own
plain output: given an unchanged scene, repeat extraction yields the same
``polygon_id``/``ring_id`` values, derived from the Door's own (stable)
``gml_id`` plus a stable per-polygon index - see ``_polygons_from_door_
mesh``. The Door's semantic ``gml_id`` itself is read, never generated,
and is exactly as stable (or as legacy-nondeterministic) as it already is
on the source object.
"""
from __future__ import annotations

from typing import Tuple

import bpy

from ..domain.ids import is_authored_gml_id
from ..domain.ids import ring_id as _mint_ring_id
from ..domain.ir_to_groups import ExportOpening, ExportPolygon, ExportRing
from . import storage
from .guards import HOST_ID_KEY
from ...export_helpers import object_is_viewport_visible
from ...lod_helpers import object_lod
from ...ring_opening_sync import is_door_opening, is_window_opening

_LOD_ORIGIN = "legacy_door_passthrough"


class DoorPassthroughError(ValueError):
    """A legacy Door object could not be extracted as passthrough data."""


def _door_gml_id(obj) -> str:
    return str(obj.get("gml_id") or "").strip() or str(obj.name or "").strip()


def _host_id(obj) -> str:
    host = str(obj.get(HOST_ID_KEY) or "").strip()
    if host:
        return host
    return str(obj.get("opening_surface_id") or "").strip()


def _is_eligible_door(obj) -> bool:
    """The single gate this module's authority boundary runs through.

    A legacy Door object: ``is_door_opening`` (never ``is_window_opening``
    - Door and Window feature types are mutually exclusive by
    construction, but the Window exclusion is checked explicitly here,
    not merely implied, since the "never a record-backed Window" rule is
    load-bearing) and carrying no record (``storage.record_state``): a
    Door that has been promoted to a ``FeatureRecord`` is not this
    adapter's concern - it belongs to the record-backed IR path, not
    legacy passthrough (P5.5 does not migrate Door to ``FeatureRecord``).
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if is_window_opening(obj):
        return False
    if not is_door_opening(obj):
        return False
    if storage.record_state(obj) != storage.RECORD_NONE:
        return False
    return True


def _polygons_from_door_mesh(obj, door_id: str, context) -> Tuple[ExportPolygon, ...]:
    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        if not mesh or not mesh.polygons:
            return ()

        polygons = []
        for index, poly in enumerate(mesh.polygons):
            polygon_id = f"{door_id}_door_poly{index}"
            coords = []
            for loop_index in poly.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                world_co = obj_eval.matrix_world @ mesh.vertices[vertex_index].co
                coords.append((float(world_co.x), float(world_co.y), float(world_co.z)))
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])

            exterior = ExportRing(
                ring_id=_mint_ring_id(polygon_id, 0),
                role="exterior",
                coords=tuple(coords),
            )
            polygons.append(ExportPolygon(polygon_id=polygon_id, exterior=exterior))

        return tuple(polygons)
    finally:
        try:
            obj_eval.to_mesh_clear()
        except Exception:
            pass


def _export_opening_from_door(obj, context) -> ExportOpening:
    door_id = _door_gml_id(obj)
    if not is_authored_gml_id(door_id):
        raise DoorPassthroughError(
            f"legacy Door object {obj.name!r} has no usable gml_id"
        )

    host_id = _host_id(obj)
    if not host_id:
        raise DoorPassthroughError(
            f"legacy Door {door_id!r} has no host surface "
            f"({HOST_ID_KEY!r}/'opening_surface_id')"
        )

    return ExportOpening(
        gml_id=door_id,
        semantic_type="Door",
        host_id=host_id,
        lod=object_lod(obj),
        lod_origin=_LOD_ORIGIN,
        polygons=_polygons_from_door_mesh(obj, door_id, context),
    )


def extract_door_passthrough(objects=None, context=None, allowed_ids=None) -> Tuple[ExportOpening, ...]:
    """Extract plain ``ExportOpening`` facts for every legacy-only Door
    object in ``objects`` (default: ``bpy.data.objects``).

    Skips (see ``_is_eligible_door``/authority boundary above):

    - anything that is not a Door opening, in particular every Window
      object regardless of record state;
    - any object carrying a readable *or* malformed record
      (``storage.record_state``) - a promoted/record-backed Door is out
      of this adapter's scope;
    - objects hidden from the current view layer
      (``object_is_viewport_visible``), matching writer behaviour;
    - (P5.6) when ``allowed_ids`` is given, any Door whose ``gml_id`` is
      not in it. This is an extra membership filter, not a second
      membership algorithm: the intended caller is a P5.6 writer dispatch
      that already knows a Building's own Door ids from
      ``ExportEligibility.door_passthrough_features``
      (``domain/export_eligibility.py``), and passes them here so this
      function cannot accidentally pull a Door belonging to a different
      Building. ``None`` (the default) keeps every eligible Door, as
      before P5.6.

    Raises ``DoorPassthroughError`` for a Door with no usable ``gml_id``
    or no resolvable host surface, and for a duplicate ``gml_id`` among
    the extracted Doors - never returns a partial result for an invalid
    input. Result is sorted by ``gml_id`` for deterministic ordering.
    """
    if objects is None:
        objects = bpy.data.objects
    if context is None:
        context = bpy.context

    doors = [
        obj for obj in objects
        if _is_eligible_door(obj) and object_is_viewport_visible(obj, context)
    ]
    if allowed_ids is not None:
        allowed_ids = set(allowed_ids)
        doors = [obj for obj in doors if _door_gml_id(obj) in allowed_ids]

    openings = tuple(sorted(
        (_export_opening_from_door(obj, context) for obj in doors),
        key=lambda opening: opening.gml_id,
    ))

    seen = set()
    for opening in openings:
        if opening.gml_id in seen:
            raise DoorPassthroughError(
                f"duplicate legacy Door gml_id {opening.gml_id!r}"
            )
        seen.add(opening.gml_id)

    return openings
