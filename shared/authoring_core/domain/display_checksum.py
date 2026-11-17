# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Display-source checksum for one record-driven surface display (CAC-P3b.4).

``records = semantic authority; a display checksum is cache provenance
only`` (docs/agent/authoring-core/STATUS.md P3b.4): this module answers
"does the persisted display mesh for ``surface_id`` still correspond to the
current committed record state?" by hashing exactly the committed record
facts ``blender/display_builder.py::build_surface_display`` reads to build
that surface's mesh, so a caller can compare two checksums instead of
re-deriving the comparison (or re-running the triangulator) itself.

This is deliberately a *different* checksum from the CAC-P4.1 ancestor-aware
feature checksum (``domain/checksum.py::compute_checksum``): that one covers
one feature's own commit-drift (world transform + geometry + relation facts,
read off a live Blender object via ``blender/checksum_view.py``) - the right
tool for guarded-delete/transform-commit drift detection, where "did this
object move" matters. A display-source checksum never needs a transform at
all: ``build_surface_display`` builds purely from already-world-space
committed record coordinates, never from a live ``matrix_world``, so moving
the source object without recommitting does not change what it would build -
and must not register as display staleness either. It does reuse
``domain/checksum.py``'s ``GeometrySnapshot`` shape and deterministic
text/rounding rules (``_geometry_text``/``_relations_text``) rather than
inventing a second rounding/serialization convention.

No ``bpy`` import (R1, CAC-P1.8): this module only reads plain
``RecordSet``/``PolygonRecord``/``RingRecord`` values. Reading/writing the
persisted checksum property onto a display object is
``blender/display_regen.py``'s job, same domain/blender split as
``domain/checksum.py``/``blender/checksum_view.py``.
"""
from __future__ import annotations

import hashlib

from . import checksum as record_checksum

# --------------------------------------------------------------------------
# freshness states (CAC-P3b.4 §9)
# --------------------------------------------------------------------------

FRESH = "fresh"
STALE = "stale"
MISSING = "missing"
INVALID = "invalid"

FRESHNESS_STATES = (FRESH, STALE, MISSING, INVALID)


class DisplayChecksumError(ValueError):
    """The committed record state for a surface could not be turned into a
    display-source checksum (same input shapes
    ``display_builder.build_surface_display`` itself rejects)."""


def _ring_key(ring):
    # Matches domain/checksum.py's own GeometrySnapshot.rings convention
    # (opening_link, the semantic hole identity) - falling back to ring_id
    # only for the malformed/legacy case of an interior ring with no
    # opening_link, so two such rings never collide onto the same dict key.
    return ring.opening_link or ring.ring_id


def surface_geometry_snapshot(polygon) -> record_checksum.GeometrySnapshot:
    """``GeometrySnapshot`` covering exactly the coordinates
    ``build_surface_display`` reads from one committed ``PolygonRecord`` -
    its exterior loop keyed by polygon id, each interior loop keyed by its
    ``opening_link``."""
    return record_checksum.GeometrySnapshot(
        polygons={polygon.polygon_id: tuple(polygon.exterior.coords)},
        rings={_ring_key(ring): tuple(ring.coords) for ring in polygon.interiors},
    )


def _relations(polygon) -> tuple:
    """Plain ``(key, value)`` facts beyond raw coordinates that still affect
    (or provenance-track) the built display: the polygon's own committed
    state, and each interior ring's ``opening_link`` (the hosted-Window
    relationship a hole stands in for) keyed by the same ring key its
    coordinates are hashed under, so a relink onto a different opening
    changes the checksum even where the hole's coordinates happen not to.
    """
    facts = [("geometry_state", polygon.geometry_state)]
    for ring in polygon.interiors:
        facts.append((f"opening_link[{_ring_key(ring)}]", ring.opening_link))
    return tuple(facts)


def compute_surface_display_checksum(record_set, surface_id) -> str:
    """Deterministic checksum of the committed record state that
    ``build_surface_display(record_set, surface_id)`` derives its display
    mesh from.

    Deterministic regardless of ``RecordSet``/dict construction or sibling
    ordering (``GeometrySnapshot``/``_geometry_text`` sort every collection
    before hashing). Raises ``DisplayChecksumError`` for the same shapes
    ``build_surface_display`` itself rejects (no feature record, not exactly
    one polygon) - a caller can use that failure as its own "cannot even
    determine the expected source state" signal.
    """
    feature = record_set.feature(surface_id)
    if feature is None:
        raise DisplayChecksumError(f"no feature record for surface {surface_id!r}")
    polygons = record_set.polygons_of(surface_id)
    if len(polygons) != 1:
        raise DisplayChecksumError(
            f"surface {surface_id!r}: expected exactly one polygon, found {len(polygons)}"
        )
    polygon = polygons[0]
    geometry = surface_geometry_snapshot(polygon)
    text = "\n".join((
        f"surface_id={surface_id}",
        f"polygon_id={polygon.polygon_id}",
        f"geometry={record_checksum._geometry_text(geometry)}",
        f"relations={record_checksum._relations_text(_relations(polygon))}",
    ))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _multi_polygon_geometry_snapshot(polygons) -> record_checksum.GeometrySnapshot:
    """``GeometrySnapshot`` covering every committed polygon ``build_roof_
    display`` merges into one RoofSurface display (CAC-P6.3 S8/S9) - the
    multi-polygon counterpart to ``surface_geometry_snapshot``'s single-
    polygon shape. Each polygon's exterior is keyed by its own polygon id
    (never collides, ``PolygonRecord.polygon_id`` is unique within a
    ``RecordSet``); each interior ring is keyed the same way
    ``surface_geometry_snapshot`` keys one (``_ring_key``) - a Roof's
    generic interior rings carry no ``opening_link`` (CAC-P6.1a), so this
    falls back to ``ring_id``, itself unique within a ``RecordSet``, so two
    rings from two different polygons never collide onto the same key
    either.
    """
    polygons_dict = {}
    rings_dict = {}
    for polygon in polygons:
        polygons_dict[polygon.polygon_id] = tuple(polygon.exterior.coords)
        for ring in polygon.interiors:
            rings_dict[_ring_key(ring)] = tuple(ring.coords)
    return record_checksum.GeometrySnapshot(polygons=polygons_dict, rings=rings_dict)


def compute_roof_display_checksum(record_set, surface_id) -> str:
    """Deterministic checksum of the committed record state that
    ``display_builder.build_roof_display(record_set, surface_id)`` derives
    its display mesh from (CAC-P6.3 S9) - the multi-polygon counterpart to
    ``compute_surface_display_checksum``, covering every committed polygon
    of ``surface_id`` rather than exactly one.

    Raises ``DisplayChecksumError`` for the same shapes ``build_roof_
    display`` itself rejects (no feature record, no polygon records, any
    polygon not ``geometry_state == 'committed'``).
    """
    feature = record_set.feature(surface_id)
    if feature is None:
        raise DisplayChecksumError(f"no feature record for surface {surface_id!r}")
    polygons = record_set.polygons_of(surface_id)
    if not polygons:
        raise DisplayChecksumError(f"surface {surface_id!r} has no polygon records")
    for polygon in polygons:
        if polygon.geometry_state != "committed":
            raise DisplayChecksumError(
                f"surface {surface_id!r}: polygon {polygon.polygon_id!r} geometry_state is "
                f"{polygon.geometry_state!r}, expected 'committed'"
            )

    ordered = sorted(polygons, key=lambda p: p.polygon_id)
    geometry = _multi_polygon_geometry_snapshot(ordered)
    relations = tuple(
        (f"{polygon.polygon_id}.{key}", value)
        for polygon in ordered
        for key, value in _relations(polygon)
    )
    text = "\n".join((
        f"surface_id={surface_id}",
        f"polygon_ids={','.join(p.polygon_id for p in ordered)}",
        f"geometry={record_checksum._geometry_text(geometry)}",
        f"relations={record_checksum._relations_text(relations)}",
    ))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# freshness classification (pure - no bpy; see blender/display_regen.py for
# the property read/write half)
# --------------------------------------------------------------------------

def classify_freshness(stored_checksum, expected_checksum) -> str:
    """One of ``FRESHNESS_STATES`` for a display object that is known to
    *exist*, given its stored checksum property value (``None`` if absent)
    and the freshly recomputed authoritative checksum.

    A caller with no display object at all should use ``MISSING`` directly
    rather than call this (there is nothing stored to classify). A missing
    checksum on an *existing* display object is ``STALE``, not ``FRESH`` -
    it covers older ``.blend`` files and displays created before P3b.4,
    which must never be assumed fresh from geometry equality alone.
    """
    if stored_checksum is None:
        return STALE
    if not isinstance(stored_checksum, str) or not stored_checksum:
        return INVALID
    return FRESH if stored_checksum == expected_checksum else STALE
