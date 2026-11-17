# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record -> semantic-IR adapter (CAC-P3.5 shadow IR, CAC-P5.2 full builder).

Reads the authoritative record (P3.2's ``FeatureRecord``/``PolygonRecord``/
``RingRecord``, ADR 0009) and builds the same schema-neutral node types the
legacy investigation tooling already uses (``shared/semantic_tree.py``'s
``SurfaceNode``/``OpeningNode``/``PolygonNode``/``RingNode``) - not to run
through that module's own builder (it walks live Blender objects; this
walks records instead), but so both the legacy and record-backed paths
speak the same typed shape instead of ad hoc dicts.

Two entry points live here:

- ``opening_node_from_records`` (CAC-P3.5, PLAN.md P3.5): a narrow,
  deliberately lenient shadow-IR check for one Window against the legacy
  exporter's actual output. It never raises over a missing host ring - a
  mismatch there is exactly the drift P3.5 exists to surface, reported by
  the caller (``scripts/headless/test_cac_p3_shadow_ir.py``), not raised
  here. Kept unchanged for that caller; only its internals now reuse the
  ring-lookup helper shared with the builder below.
- ``building_ir_from_records`` (CAC-P5.2, PLAN.md P5.2): the general,
  strict builder. Given a complete, promoted/record-backed ``RecordSet``
  and a Building's ``gml_id``, it walks the record graph
  (``FeatureRecord.parent_id``/``host_id``, never geometry or Blender
  object state) and produces a full ``BuildingNode`` - every WallSurface's
  own polygons plus the Windows it hosts, each Window with its own
  ``con:fillingSurface`` polygons. Host/ring pairing is resolved from
  ``opening_link`` alone; anything structurally invalid or semantically
  ambiguous raises ``IRAdapterError`` rather than guessing.

No XML writer change: both entry points are read-only. A mismatch against
the legacy exporter's actual output is reported by the caller, never
silently patched here or in production code.

Pure Python only: no bpy, bmesh, mathutils or gpu (rule R1, enforced by
scripts/tests/unit/test_cac_domain_has_no_bpy.py).
"""
from __future__ import annotations

from ...semantic_tree import (
    BOUNDARY_SURFACE_TYPES,
    BuildingNode,
    OpeningNode,
    PolygonNode,
    RingNode,
    SurfaceNode,
    opening_sort_key,
    polygon_sort_key,
    ring_sort_key,
    surface_sort_key,
)

# Kinds this builder recognizes as an opening (mirrors
# ``domain/consistency.py::OPENING_KINDS`` - Window enabled, Door
# declared-but-disabled in ``domain/kinds.py``, but a disabled kind can
# still appear in imported/legacy records, so this checks ``kind`` values
# directly rather than the kind registry's ``enabled`` flag).
OPENING_KINDS = ("Window", "Door")


class IRAdapterError(ValueError):
    """A shadow-IR or Building IR could not be built from the given records."""


def _is_boundary_surface_kind(kind):
    return (kind or "").strip().lower() in BOUNDARY_SURFACE_TYPES


def _find_ring_for_opening_link(polygons, opening_link):
    """First ``(PolygonRecord, RingRecord)`` interior ring pairing ``opening_link``.

    ``(None, None)`` if no interior ring across ``polygons`` carries it.
    Shared by both entry points; whether a caller treats a miss (or more
    than one match) as an error is that caller's own policy.
    """
    for polygon in polygons:
        for ring in polygon.interiors:
            if ring.opening_link == opening_link:
                return polygon, ring
    return None, None


def opening_node_from_records(record_set, window_gml_id):
    """``(OpeningNode, RingNode | None, host_gml_id)`` for ``window_gml_id``.

    ``record_set`` must already carry both the Window's own record and its
    host WallSurface's record merged into one (e.g.
    ``blender/consistency_view.py::merged_record_set`` - per-object storage,
    ADR 0009, means neither record alone has both halves of the relation).

    The ``OpeningNode`` carries the Window's own identity fields
    (``gml_id``, ``semantic_type``, ``source_role``, ``source_element``,
    ``schema_version``) with an empty ``rings`` tuple - CAC-P3.2 does not
    model a Window's *own* internal frame/glazing ring, only the host
    wall's ring (PLAN.md P3.5 scope: "OpeningNode + the host's RingNode").
    ``RingNode`` is the host wall's interior ring whose ``opening_link``
    names this Window, or ``None`` if the wall's record carries none (a
    mismatch for the caller to report, not to raise here - the point of
    P3.5 is to surface exactly that kind of drift). Raises
    ``IRAdapterError`` only when ``window_gml_id`` itself has no feature in
    ``record_set`` at all - there is nothing to compare in that case.
    """
    feature = record_set.feature(window_gml_id)
    if feature is None:
        raise IRAdapterError(f"{window_gml_id!r}: no feature in the given record set")

    opening = OpeningNode(
        gml_id=feature.gml_id,
        semantic_type=feature.semantic_type or feature.kind,
        source_role=feature.source_role,
        source_element=feature.source_element,
        schema_version=feature.schema_version,
    )

    host_gml_id = str(feature.host_id or "").strip()
    host_ring = None
    if host_gml_id and feature.opening_link:
        polygon, ring = _find_ring_for_opening_link(
            record_set.polygons_of(host_gml_id), feature.opening_link,
        )
        if ring is not None:
            host_ring = RingNode(ring_id=ring.ring_id, polygon_id=polygon.polygon_id)

    return opening, host_ring, host_gml_id


# ---------------------------------------------------------------------------
# CAC-P5.2: full-record -> Building IR builder
# ---------------------------------------------------------------------------

def _ring_node_from_record(ring, polygon_id):
    return RingNode(
        ring_id=ring.ring_id,
        polygon_id=polygon_id,
        role=ring.role,
        coords=ring.coords,
        opening_link=ring.opening_link,
    )


def _polygon_node_from_record(polygon):
    exterior = _ring_node_from_record(polygon.exterior, polygon.polygon_id)
    interiors = tuple(sorted(
        (_ring_node_from_record(r, polygon.polygon_id) for r in polygon.interiors),
        key=ring_sort_key,
    ))
    return PolygonNode(
        polygon_id=polygon.polygon_id,
        exterior=exterior,
        interiors=interiors,
        multisurface_id=polygon.multisurface_id,
        compositesurface_id=polygon.compositesurface_id,
        is_multisurface_member=polygon.is_multisurface_member,
    )


def _polygon_nodes_for_feature(record_set, feature):
    nodes = tuple(_polygon_node_from_record(p) for p in record_set.polygons_of(feature.gml_id))
    return tuple(sorted(nodes, key=polygon_sort_key))


def _validate_opening_hosts(record_set):
    """Every opening's ``host_id`` must resolve to a boundary surface.

    A Window whose ``host_id`` names nothing in ``record_set``, or names a
    feature that is not a boundary surface, is a structurally broken
    record: failing here (rather than silently dropping the opening, which
    a per-wall lookup by ``host_id`` would otherwise do) is what "fail
    explicitly rather than silently attach the opening to the wrong host"
    (PLAN.md P5.2) requires.
    """
    for feature in record_set.features:
        if feature.kind not in OPENING_KINDS:
            continue
        host_id = (feature.host_id or "").strip()
        if not host_id:
            raise IRAdapterError(f"{feature.gml_id!r}: opening has no host_id")
        host = record_set.feature(host_id)
        if host is None:
            raise IRAdapterError(
                f"{feature.gml_id!r}: host_id {host_id!r} references no feature in the record set"
            )
        if not _is_boundary_surface_kind(host.kind):
            raise IRAdapterError(
                f"{feature.gml_id!r}: host_id {host_id!r} names a {host.kind!r} feature, "
                "not a boundary surface"
            )


def _host_openings(record_set, wall_feature):
    """Openings whose ``host_id`` names ``wall_feature`` (PLAN.md P5.2:

    "Window.host_id / parent_id -> host WallSurface" - ``host_id`` is the
    relation this builder trusts; never geometry equality, material, or
    Blender parenting/object names.
    """
    return tuple(
        f for f in record_set.features
        if f.kind in OPENING_KINDS and (f.host_id or "").strip() == wall_feature.gml_id
    )


def _validate_host_ring_pairing(wall_feature, host_polygons, openings):
    """Deterministic, unambiguous opening_link <-> ring pairing for one host.

    Every opening hosted here must carry a non-empty, host-unique
    ``opening_link`` that resolves to exactly one interior ring across the
    host's own polygons, and every opening_link-bearing interior ring must
    resolve back to exactly one opening. Anything else - a duplicate
    opening_link, a ring nothing claims, an opening nothing pairs with - is
    an explicit ``IRAdapterError``, never a best-effort guess.
    """
    link_to_opening = {}
    for opening in openings:
        link = (opening.opening_link or "").strip()
        if not link:
            raise IRAdapterError(
                f"{opening.gml_id!r}: opening has no opening_link naming its host ring"
            )
        if link in link_to_opening:
            raise IRAdapterError(
                f"host {wall_feature.gml_id!r}: opening_link {link!r} is claimed by both "
                f"{link_to_opening[link]!r} and {opening.gml_id!r} (ambiguous opening_link)"
            )
        link_to_opening[link] = opening.gml_id

    link_to_ring = {}
    for polygon in host_polygons:
        for ring in polygon.interiors:
            link = (ring.opening_link or "").strip()
            if not link:
                continue
            if link in link_to_ring:
                raise IRAdapterError(
                    f"host {wall_feature.gml_id!r}: opening_link {link!r} is used by more than "
                    f"one interior ring ({link_to_ring[link]!r} and {ring.ring_id!r})"
                )
            link_to_ring[link] = ring.ring_id
            if link not in link_to_opening:
                raise IRAdapterError(
                    f"host {wall_feature.gml_id!r}: ring {ring.ring_id!r} carries opening_link "
                    f"{link!r} but no opening on this host claims it (dangling ring)"
                )

    for link, opening_gml_id in link_to_opening.items():
        if link not in link_to_ring:
            raise IRAdapterError(
                f"{opening_gml_id!r}: host {wall_feature.gml_id!r} has no interior ring for "
                f"opening_link {link!r}"
            )


def _surface_node_from_record(record_set, wall_feature):
    host_polygons = record_set.polygons_of(wall_feature.gml_id)
    openings = _host_openings(record_set, wall_feature)
    _validate_host_ring_pairing(wall_feature, host_polygons, openings)

    opening_nodes = tuple(sorted(
        (
            OpeningNode(
                gml_id=opening.gml_id,
                semantic_type=opening.semantic_type or opening.kind,
                source_role=opening.source_role,
                source_element=opening.source_element,
                schema_version=opening.schema_version,
                lod=opening.lod,
                lod_origin=opening.lod_origin,
                host_id=opening.host_id,
                parent_id=opening.parent_id,
                opening_link=opening.opening_link,
                polygons=_polygon_nodes_for_feature(record_set, opening),
            )
            for opening in openings
        ),
        key=opening_sort_key,
    ))

    return SurfaceNode(
        gml_id=wall_feature.gml_id,
        semantic_type=wall_feature.semantic_type or wall_feature.kind,
        schema_version=wall_feature.schema_version,
        lod=wall_feature.lod,
        lod_origin=wall_feature.lod_origin,
        parent_id=wall_feature.parent_id,
        polygons=_polygon_nodes_for_feature(record_set, wall_feature),
        openings=opening_nodes,
    )


def surface_node_from_record(record_set, wall_feature):
    """Public seam for GS.3.2 (PLAN.md GS.3.2): the same per-surface
    ``SurfaceNode`` construction ``building_ir_from_root`` uses internally
    for each of a Building's WallSurfaces, exposed so a surface-granular
    caller can build IR for exactly one wall without a Building root at
    all - GS.3.2's own writer seam never builds a synthetic Building.

    ``record_set`` must carry ``wall_feature``'s own polygon(s) and, if it
    hosts one, its Window's feature/polygon too (the same scope contract
    ``gs_surface_eligibility.can_export_surface_from_ir`` already
    documents). Raises ``IRAdapterError`` on the same host/ring-pairing
    problems ``_validate_host_ring_pairing`` checks - a caller that has
    already confirmed GS.3.1 eligibility for this wall should not see this
    raise; it is the read-only builder step, never a second eligibility
    check.
    """
    return _surface_node_from_record(record_set, wall_feature)


def building_ir_from_root(record_set, building):
    """Same as ``building_ir_from_records``, but given the Building's own
    ``FeatureRecord`` directly (``building``) instead of looking it up by
    id in ``record_set`` (CAC-P5.3).

    This is the seam ``domain/building_root.py``'s transient Building root
    uses: Phase 4 production code never writes a Building-kind
    ``FeatureRecord`` into any object's stored record (no
    ``storage.write_record`` call ever targets a Building), so
    ``record_set.feature(building_gml_id)`` finds nothing for a real,
    currently-exportable scene. ``building`` lets a caller supply that
    identity from elsewhere - a transient, non-persisted root read fresh
    from legacy state, or (once CAC-P5.9's ``promote_building`` exists) a
    genuinely persisted one - without constructing a fake persisted record
    and writing it into ``record_set`` to smuggle it past this function.
    ``record_set`` itself still must carry every WallSurface/Window/Door
    record this Building's IR needs, exactly as for
    ``building_ir_from_records``.

    Builds, deterministically (PLAN.md P5.1 ordering: ``surface_sort_key``/
    ``opening_sort_key``/``polygon_sort_key``/``ring_sort_key``, all by
    stable id, never input/iteration order):

        Building
        `-- WallSurface (sorted by gml_id)
            |-- polygons (this surface's own geometry, sorted by polygon_id)
            `-- openings (Windows hosted here, sorted by gml_id)
                `-- polygons (each opening's own filling geometry)

    A WallSurface's ``polygons`` and its hosted Windows' ``polygons`` are
    never merged - each feature owns its own polygon collection (PLAN.md
    P5.2). Host/ring pairing comes only from ``FeatureRecord.host_id`` and
    ``opening_link``; coordinates and LoD are copied from the records
    exactly, with no transform, inference or normalization.

    Raises ``IRAdapterError`` for: ``building`` missing or not a Building;
    ``record_set.validate()`` problems (duplicate gml_id, duplicate/missing
    polygon ids, malformed polygon/ring structure - reusing that existing
    validator rather than a second one); an opening's ``host_id`` missing
    or not a boundary surface; and any ambiguous or dangling
    opening_link/ring pairing on a host (see ``_validate_host_ring_pairing``).
    """
    if building is None:
        raise IRAdapterError("no Building root given")
    if building.kind != "Building":
        raise IRAdapterError(f"{building.gml_id!r}: kind {building.kind!r}, expected 'Building'")

    problems = record_set.validate()
    if problems:
        raise IRAdapterError("; ".join(problems))

    _validate_opening_hosts(record_set)

    walls = tuple(
        f for f in record_set.features
        if (f.parent_id or "").strip() == building.gml_id and _is_boundary_surface_kind(f.kind)
    )
    surfaces = tuple(sorted(
        (_surface_node_from_record(record_set, wall) for wall in walls),
        key=surface_sort_key,
    ))

    return BuildingNode(gml_id=building.gml_id, boundary_surfaces=surfaces)


def building_ir_from_records(record_set, building_gml_id):
    """The record-backed Phase 5 slice of a Building as a ``BuildingNode``.

    ``record_set`` is a single ``RecordSet`` (``domain/records.py``) already
    carrying every feature/polygon this Building's IR needs, including the
    Building's own ``FeatureRecord`` itself (found by ``building_gml_id`` -
    a fully promoted/record-backed graph, not a partial or per-object slice
    (P5.2 scope: no Blender-side lookup, no dispatcher, this is a pure
    function of the records given). A caller that only has a transient,
    non-persisted Building root (CAC-P5.3 - the common case today, since no
    production code writes a Building-kind record yet) should call
    ``building_ir_from_root`` instead, passing that root directly.

    Raises ``IRAdapterError`` for: ``building_gml_id`` missing or not a
    Building in ``record_set``; and everything ``building_ir_from_root``
    itself can raise for the rest of the graph.
    """
    building = record_set.feature(building_gml_id)
    if building is None:
        raise IRAdapterError(f"{building_gml_id!r}: no feature in the given record set")
    return building_ir_from_root(record_set, building)
