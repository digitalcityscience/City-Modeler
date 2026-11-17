# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Rules for one host wall carrying several record-backed openings (CAC-P4.10).

Pure (R1): plain records and coordinate tuples in, records out. The Blender
adapters (``blender/usecases.py``, ``blender/edit_session.py``) read the live
mesh and hand the coordinates in.

Policy (PLAN.md §6 Phase 4 "P4.10"): validation of a host is host-complete,
mutation is feature-specific. Committing one opening rewrites the host
polygon's exterior and that opening's own ring only; every sibling opening's
``RingRecord`` is kept exactly as the wall record already stores it.
"""
from __future__ import annotations

from dataclasses import replace

from . import ids


def _usable(coords) -> bool:
    return coords is not None and len(set(coords)) >= 3


def commit_host_polygon(polygon, opening_link, live_exterior, live_rings, stored=None):
    """``polygon`` (one of a host wall's polygons) committed for one opening.

    Returned unchanged unless it carries the ring for ``opening_link``.
    Otherwise the exterior takes ``live_exterior`` and that ring takes
    ``live_rings[opening_link]`` (world coordinates). Every other ring - a
    sibling opening's - is copied verbatim from ``stored`` (the wall record's
    current copy of this polygon) when it has committed coordinates there, so
    committing one opening never re-reads, and never silently commits, a
    sibling's live ring. A sibling with no stored coordinates (the polygon was
    never committed) takes its live ring.

    If the exterior or any ring cannot be resolved the polygon comes back
    ``ids_only`` with its ring list refreshed, which record validation then
    refuses (coordinates present while ``ids_only``).
    """
    if not any(r.opening_link == opening_link for r in polygon.interiors):
        return polygon
    kept = {
        r.opening_link: r for r in (stored.interiors if stored is not None else ())
        if r.opening_link and r.opening_link != opening_link and r.coords
    }
    ok = _usable(live_exterior)
    interiors = []
    for index, ring in enumerate(polygon.interiors, start=1):
        if ring.opening_link in kept:
            interiors.append(kept[ring.opening_link])
            continue
        coords = live_rings.get(ring.opening_link) if ring.opening_link else None
        if not _usable(coords):
            ok = False
            interiors.append(ring)
            continue
        interiors.append(replace(
            ring, coords=tuple(tuple(c) for c in coords),
            ring_id=ring.ring_id or ids.ring_id(polygon.polygon_id, index),
        ))
    if not ok:
        return replace(polygon, interiors=tuple(interiors))
    exterior = replace(
        polygon.exterior, coords=tuple(tuple(c) for c in live_exterior),
        ring_id=polygon.exterior.ring_id or ids.ring_id(polygon.polygon_id, 0),
    )
    return replace(polygon, exterior=exterior, interiors=tuple(interiors), geometry_state="committed")


UNLINKED = "unlinked"        # imported ring: no opening_link at all
UNRECORDED = "unrecorded"    # linked ring whose opening carries no record


def unmanaged_host_rings(polygons, owned_links, target_polygon_id):
    """Rings of a host wall a record-backed create cannot represent (Case B).

    ``polygons``: the wall's ``PolygonRecord``s read fresh from its live
    legacy state (interiors carry ``opening_link``, no coordinates needed).
    ``owned_links``: the ``opening_link`` of every record-backed opening whose
    ``host_id`` is this wall. ``target_polygon_id``: the polygon the new
    opening is cut into.

    Returns ``((polygon_id, ring_id, opening_link, reason), ...)``:

    - on the target polygon, any ring that is not owned. An imported ring has
      no link, so its coordinates cannot be resolved and the polygon cannot be
      committed (AB-20); a linked, unowned ring is the case below.
    - on any polygon, a linked ring that is not owned: a flag-off authored
      opening. Committing the wall would record a RingRecord that no opening
      record claims - a ``dangling_ring`` at export (AB-20b).

    Unlinked rings on other polygons are allowed: those polygons stay
    ``ids_only`` in the wall record, which is valid and exports unchanged.
    """
    owned = set(owned_links)
    found = []
    for polygon in polygons:
        for ring in polygon.interiors:
            link = ring.opening_link
            if link and link in owned:
                continue
            if link:
                found.append((polygon.polygon_id, ring.ring_id, link, UNRECORDED))
            elif polygon.polygon_id == target_polygon_id:
                found.append((polygon.polygon_id, ring.ring_id, link, UNLINKED))
    return tuple(found)
