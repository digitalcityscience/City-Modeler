# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Pure ``ExportPolygon``/``ExportOpening`` -> writer-back-half mapping
(CAC-P5.6, PLAN.md P5.6).

The v2/v3 writers' existing XML-writing back-half functions
(``write_polygon_with_ring_ids`` in ``io/writer/geometry.py`` and
``io_v2/writer/geometry.py``; the closure ``_write_inline_surface_opening``
in ``io/writer/exporter.py`` and ``io_v2/writer/exporter.py``) already do
everything the P5.6 writer adapter needs - this module's only job is to
turn ``domain/ir_to_groups.py``'s plain ``ExportPolygon``/``ExportOpening``
into the exact argument shapes those functions already read, so the
writer-side adapter (a small closure in each ``export_blender_to_
citygml3``) stays a thin, mechanical call site rather than a second place
where geometry/identity is interpreted.

No bpy here (rule R1, enforced by ``scripts/tests/unit/
test_cac_domain_has_no_bpy.py``'s sibling check for this module). The one
coordinate transform (D-2, ``exporter-seam.md`` §6) is never
reimplemented here - it is injected as ``transform_ring``, a callable the
caller builds from its own ``trf.transform_with_origin`` and already-
resolved document origin, so both writers keep using the single existing
transform.

``multisurface_id``/``compositesurface_id`` (task item 13) flow straight
through onto ``ms_id`` - carried, not newly exercised (no existing
back-half consumer reads ``cs_id`` distinctly from ``ms_id`` today, see
``ir_to_groups.py``'s own field-classification table).
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from .authoring_core.domain.ir_to_groups import ExportOpening, ExportPolygon

# (x, y, z) ring coords -> transformed [(x, y, z), ...] in the document's
# target frame - the caller's own trf.transform_with_origin(...) + origin,
# never reimplemented here.
TransformRing = Callable[[Sequence[Tuple[float, float, float]]], List[Tuple[float, float, float]]]


def transformed_polygon_kwargs(polygon: ExportPolygon, transform_ring: TransformRing) -> dict:
    """kwargs for ``write_polygon_with_ring_ids(parent_ms=..., srs_name=...,
    **transformed_polygon_kwargs(polygon, transform_ring))`` - one
    WallSurface/RoofSurface/GroundSurface/ClosureSurface polygon, exterior
    ring plus interior-ring holes (the real "host hole channel", task item
    6 - ``write_polygon_with_ring_ids`` itself cuts the hole from
    ``interior_rings``, fed here from ``ExportPolygon.interiors``, never
    from Window geometry).
    """
    exterior_ring = transform_ring(polygon.exterior.coords)
    interior_rings = [transform_ring(r.coords) for r in polygon.interiors]
    interior_ring_ids = [r.ring_id for r in polygon.interiors]
    return dict(
        poly_gid=polygon.polygon_id,
        exterior_ring=exterior_ring,
        interior_rings=interior_rings or None,
        exterior_ring_id=polygon.exterior.ring_id,
        interior_ring_ids=interior_ring_ids or None,
    )


def geom_buf_opening_entries(opening: ExportOpening, transform_ring: TransformRing) -> List[dict]:
    """geom_buf-shaped entries for ``_write_inline_surface_opening(surf_el,
    geom_buf_opening_entries(opening, transform_ring), lod)`` - the real
    semantic Window/Door channel (``exporter-seam.md`` §8, task item 6),
    one dict per ``opening.polygons`` entry. ``opening_info`` is built only
    from ``ExportOpening`` fields - never from a face material - which is
    the whole point of feeding the IR path through this seam instead of
    ``_pop_openings_for_surface``'s ring-geometry/material scan.

    Empty ``opening.polygons`` (a geometry-less opening) returns ``[]``;
    ``_write_inline_surface_opening`` itself already no-ops on an empty
    list rather than writing an empty element.
    """
    if opening.semantic_type not in ("Window", "Door"):
        raise ValueError(
            f"{opening.gml_id!r}: unsupported opening semantic_type {opening.semantic_type!r} "
            "(only Window/Door have a con:*Surface filling element)"
        )
    opening_info = {
        "opening_type": opening.semantic_type,
        "filling_surface_type": f"{opening.semantic_type}Surface",
        "opening_id": opening.gml_id,
        "opening_name": "",
    }
    entries = []
    for polygon in opening.polygons:
        ext_xyz = transform_ring(polygon.exterior.coords)
        interior_rings = [transform_ring(r.coords) for r in polygon.interiors]
        interior_ring_ids = [r.ring_id for r in polygon.interiors]
        entries.append({
            "poly_gid": polygon.polygon_id,
            "ext_xyz": ext_xyz,
            "int_xyz": interior_rings or None,
            "ext_id": polygon.exterior.ring_id,
            "int_ids": interior_ring_ids or None,
            "ms_id": polygon.multisurface_id,
            "cs_id": polygon.compositesurface_id,
            "opening_info": opening_info,
        })
    return entries
