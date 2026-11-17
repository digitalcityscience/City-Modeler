# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Semantic IR -> plain export groups (CAC-P5.4, PLAN.md P5.4).

```
BuildingNode (shared/semantic_tree.py)
    -> ExportBuildingGroups (this module)
        -> a later writer adapter (P5.6) folds this into geom_buf
```

This module is the bridge between the record-backed semantic IR (P5.1/P5.2/
P5.3) and the existing CityGML 2/3 XML back half, which today consumes a
``geom_buf: Dict[surface_type, List[entry]]`` structure
(``docs/agent/authoring-core/exporter-seam.md`` §3). It does **not**
reproduce ``geom_buf`` - that shape is a writer-local grouping (by surface
*type*, mixing single-polygon and MultiSurface entries, one dict per
Building) built by whichever writer adapter consumes this module's output
(P5.6). Instead it defines the smallest common **semantic/geometric**
contract both writers' back halves actually need (traced field-by-field in
``exporter-seam.md`` §3-4), so that later adapter is a thin, mechanical
group-by-surface-type step, not a second place where record data is
interpreted.

## geom_buf back-half field classification (task item 1)

Every field an ``entry`` in today's ``geom_buf`` carries, classified:

| geom_buf field | class | disposition here |
|---|---|---|
| `poly_gid` | A | `ExportPolygon.polygon_id` |
| `ext_xyz` | A | `ExportPolygon.exterior.coords` |
| `int_xyz` | A | `ExportPolygon.interiors[*].coords` |
| `ext_id` | A | `ExportPolygon.exterior.ring_id` |
| `int_ids` | A | `ExportPolygon.interiors[*].ring_id` |
| `surf_id` | A | `ExportSurface.gml_id` / `ExportOpening.gml_id` |
| `ms_id`, `cs_id` | A | `ExportPolygon.multisurface_id` / `.compositesurface_id` |
| `is_multisurface_member`, `is_compositesurface_member` | A | `ExportPolygon.is_multisurface_member` (no `geom_buf` consumer read a `is_compositesurface_member` flag distinct from the id itself; not reproduced) |
| `opening_info` (dict, built from a face **material**) | C -> A | not carried as a dict; the *semantic* facts it held (opening id/type/host) already exist as `ExportOpening.gml_id`/`.semantic_type`/`.host_id`, sourced from records, never from a material |
| `filling_parent_sid` | A | `ExportOpening.host_id` |
| `subfeature_type/id/parent_id/relation/name` (inline installation/part passthrough) | D | out of P5.4 scope - `ALLOWED_IR_KINDS` (PLAN.md P5.0) does not include installations; a Building containing one is `Decision.LEGACY` and never reaches this module |
| `mat` (`bpy.types.Material`) | C | **not carried.** Forbidden by this module's no-bpy rule (R1) and by design (module docstring, task instructions) |
| side tables `ptx_by_image`, `gtx_by_image`, `x3d_by_key` (appearance/UV) | B | deferred - see "Appearance / generic attributes" below |
| `bbox_coords_abs` | D | writer-computed from coordinates already present in `ExportPolygon`; not a domain fact to store separately |

Class key: **A** semantic/geometric, belongs here. **B** appearance/generic
attribute, deferred. **C** Blender runtime state, must never enter this
module. **D** obsolete or front-half-only, not needed by this seam.

## Plain export-group schema (task item 2)

```
ExportBuildingGroups
`-- surfaces: tuple[ExportSurface, ...]     (WallSurface, RoofSurface, ...)
    |-- polygons: tuple[ExportPolygon, ...]  (this surface's own boundary geometry)
    |   |-- exterior: ExportRing
    |   `-- interiors: tuple[ExportRing, ...] (opening holes, chimney penetrations, ...)
    `-- openings: tuple[ExportOpening, ...]  (Windows/Doors this surface hosts)
        `-- polygons: tuple[ExportPolygon, ...] (the opening's OWN filling geometry,
                                                   PLAN.md P5.0 deep-dive "channel 2")
```

A surface's `polygons` (its own boundary geometry, holes included) and an
opening's `polygons` (the opening's own fill surface) are two independent
collections, never merged (task item 4) - exactly as `SurfaceNode`/
`OpeningNode` already keep them apart (`semantic_tree.py`).

No Blender object, material or property-bag reference appears anywhere in
this schema (task item 2/9): every field is a `str`, `int`, `float`,
`bool`, `None` or a tuple of the above/another plain dataclass here.

## Coordinate frame (task item 5)

Unchanged from the IR (`semantic_tree.py`'s own D-2 note): coordinates are
carried through **exactly as given** - Blender-world frame, no CRS
transform, no document-origin offset, no renumbering. This module performs
no coordinate arithmetic at all. Applying `T(P + O)` (the document origin
and target CRS) stays a write-time concern for the P5.6 writer adapter,
exactly as `exporter-seam.md` D-2 already decided for the legacy path - this
module does not change that boundary, only carries the coordinates across
it unmodified.

## Semantic-id preservation (task item 6)

No id is invented, renumbered or reused here. Building, surface, opening,
polygon and ring ids are copied verbatim from the given `BuildingNode`
(itself sourced from `FeatureRecord.gml_id`/`PolygonRecord.polygon_id`/
`RingRecord.ring_id` - already canonical, D-3, by the time a record exists;
see `semantic_tree.py`'s own note on this). `groups_from_building_ir` fails
explicitly (`IRToGroupsError`) rather than silently emitting a duplicate or
missing id - see "Determinism and validation" below.

## Opening / hole representation (task item 4 of the design brief)

A `WallSurface`'s interior ring (the hole an opening sits in) and that
opening's own fill polygon are two separate `ExportRing`/`ExportPolygon`
objects that happen to share an `opening_link` value - never the same
object, never substituted for one another. `ExportOpening.host_id` is the
*semantic* host relation (from `FeatureRecord.host_id`, PLAN.md P5.0
decision 2); `ExportRing.opening_link` on the host's own interior ring is
the independently-checkable ring-level pairing (from `RingRecord
.opening_link`). Both survive unchanged; this module does not attempt to
reconcile or drop either.

## Appearance / generic attributes (task item 9)

`PolygonRecord.appearance_refs` exists in the record schema but nothing in
this repository populates it yet (confirmed in `exporter-seam.md` and
PLAN.md's frozen G5 parity contract, "Allowed named differences"). This
module therefore defines no appearance/generic-attribute field at all -
there is no live data to carry, and no placeholder is introduced ahead of
need (no speculative schema). `mat["cgml3_surface_generic_attributes"]` and
object-level generic/specific attributes (`exporter-seam.md` §4 #1, #2)
are the same accepted, documented gap PLAN.md's G5 contract already names;
nothing here silently drops anything that has a record-backed source today.

## Door passthrough seam for P5.5 (task item 10)

`groups_from_building_ir` never represents a Door (`ALLOWED_IR_KINDS`
excludes it; Door has no `FeatureRecord` on the record-backed path), so its
id sets are disjoint from anything a legacy Door reads *by construction* -
the two sources read disjoint object sets. `merge_door_passthrough` (below)
is the P5.5 merge helper anticipated by this note: it takes the
`ExportBuildingGroups` this module built and the separately-sourced
``ExportOpening`` tuple that
``shared/authoring_core/blender/door_passthrough.py`` extracts for legacy
Door objects, and folds the latter onto the matching host surface's
`.openings`. It still re-checks every id (`gml_id`, `polygon_id`,
`ring_id`) at merge time and fails closed (`DoorPassthroughMergeError`) on
any collision or on a Door whose `host_id` names a surface this Building's
IR does not have - construction-time disjointness is the common case, not
a substitute for the runtime check when the two producers are combined.

## Determinism and validation (task items 8, 17)

``groups_from_building_ir`` re-sorts every level itself
(``semantic_tree.py``'s own ``surface_sort_key``/``opening_sort_key``/
``polygon_sort_key``/``ring_sort_key`` - by stable id, duck-typed onto this
module's own dataclasses, which share the same attribute names) rather than
trusting the input ``BuildingNode``'s own tuple order. Two structurally
equal ``BuildingNode`` trees built in any input/construction order produce
`==`-equal ``ExportBuildingGroups``.

Fails explicitly, before returning anything, on: a missing Building/surface/
opening `gml_id`; a polygon with no `polygon_id` or no exterior ring
(`ring_id`); a duplicate `gml_id` among surfaces or openings; a duplicate
`polygon_id` or `ring_id` anywhere in the Building (both must be
document-wide unique, D-3); an opening with no `host_id`. Never emits a
partial result for a structurally invalid input.

Pure Python only: no bpy, bmesh, mathutils, gpu, and no writer/exporter
import (rule R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Tuple

from ...semantic_tree import (
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


class IRToGroupsError(ValueError):
    """A Building IR could not be converted to plain export groups."""


class DoorPassthroughMergeError(IRToGroupsError):
    """Legacy Door passthrough data collides with record-backed IR data,
    or names a host surface this Building's IR does not have (PLAN.md
    P5.5's merge-safety requirement)."""


# ---------------------------------------------------------------------------
# Plain export-group node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportRing:
    """One ring (exterior or interior), coordinates carried unchanged."""

    ring_id: str
    role: str
    coords: Tuple[Tuple[float, float, float], ...] = ()
    opening_link: str = ""

    def to_dict(self) -> dict:
        return {
            "ring_id": self.ring_id,
            "role": self.role,
            "coords": [list(c) for c in self.coords],
            "opening_link": self.opening_link,
        }


@dataclass(frozen=True)
class ExportPolygon:
    """One polygon: one exterior ring plus zero or more interior rings."""

    polygon_id: str
    exterior: ExportRing
    interiors: Tuple[ExportRing, ...] = ()
    multisurface_id: str = ""
    compositesurface_id: str = ""
    is_multisurface_member: bool = False

    def to_dict(self) -> dict:
        return {
            "polygon_id": self.polygon_id,
            "exterior": self.exterior.to_dict(),
            "interiors": [r.to_dict() for r in self.interiors],
            "multisurface_id": self.multisurface_id,
            "compositesurface_id": self.compositesurface_id,
            "is_multisurface_member": self.is_multisurface_member,
        }


@dataclass(frozen=True)
class ExportOpening:
    """A Window or Door surface's own facts, nested under its host surface."""

    gml_id: str
    semantic_type: str
    host_id: str
    opening_link: str = ""
    lod: int | None = None
    lod_origin: str = ""
    polygons: Tuple[ExportPolygon, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "semantic_type": self.semantic_type,
            "host_id": self.host_id,
            "opening_link": self.opening_link,
            "lod": self.lod,
            "lod_origin": self.lod_origin,
            "polygons": [p.to_dict() for p in self.polygons],
        }


@dataclass(frozen=True)
class ExportSurface:
    """A boundary surface (WallSurface, RoofSurface, ...) and what it hosts."""

    gml_id: str
    semantic_type: str
    parent_id: str = ""
    lod: int | None = None
    lod_origin: str = ""
    polygons: Tuple[ExportPolygon, ...] = ()
    openings: Tuple[ExportOpening, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "semantic_type": self.semantic_type,
            "parent_id": self.parent_id,
            "lod": self.lod,
            "lod_origin": self.lod_origin,
            "polygons": [p.to_dict() for p in self.polygons],
            "openings": [o.to_dict() for o in self.openings],
        }


@dataclass(frozen=True)
class ExportBuildingGroups:
    """Root of one Building's plain export representation."""

    building_gml_id: str
    surfaces: Tuple[ExportSurface, ...] = ()

    def to_dict(self) -> dict:
        return {
            "building_gml_id": self.building_gml_id,
            "surfaces": [s.to_dict() for s in self.surfaces],
        }


# ---------------------------------------------------------------------------
# Builder: BuildingNode -> ExportBuildingGroups
# ---------------------------------------------------------------------------


def _ring_from_node(node: RingNode, where: str) -> ExportRing:
    if not node.ring_id:
        raise IRToGroupsError(f"{where}: ring has no ring_id")
    return ExportRing(
        ring_id=node.ring_id,
        role=node.role,
        coords=node.coords,
        opening_link=node.opening_link,
    )


def _polygon_from_node(node: PolygonNode) -> ExportPolygon:
    if not node.polygon_id:
        raise IRToGroupsError("polygon has no polygon_id")
    where = f"polygon {node.polygon_id!r}"
    if node.exterior is None or not node.exterior.ring_id:
        raise IRToGroupsError(f"{where}: missing exterior ring")
    interiors = tuple(sorted(
        (_ring_from_node(r, where) for r in node.interiors), key=ring_sort_key,
    ))
    return ExportPolygon(
        polygon_id=node.polygon_id,
        exterior=_ring_from_node(node.exterior, where),
        interiors=interiors,
        multisurface_id=node.multisurface_id,
        compositesurface_id=node.compositesurface_id,
        is_multisurface_member=node.is_multisurface_member,
    )


def _polygons_from_nodes(nodes: Tuple[PolygonNode, ...]) -> Tuple[ExportPolygon, ...]:
    return tuple(sorted((_polygon_from_node(p) for p in nodes), key=polygon_sort_key))


def _opening_from_node(node: OpeningNode) -> ExportOpening:
    if not node.gml_id:
        raise IRToGroupsError("opening has no gml_id")
    if not node.host_id:
        raise IRToGroupsError(f"opening {node.gml_id!r}: no host_id")
    return ExportOpening(
        gml_id=node.gml_id,
        semantic_type=node.semantic_type,
        host_id=node.host_id,
        opening_link=node.opening_link,
        lod=node.lod,
        lod_origin=node.lod_origin,
        polygons=_polygons_from_nodes(node.polygons),
    )


def _surface_from_node(node: SurfaceNode) -> ExportSurface:
    if not node.gml_id:
        raise IRToGroupsError("surface has no gml_id")
    if not node.semantic_type:
        raise IRToGroupsError(f"surface {node.gml_id!r}: no semantic_type")
    openings = tuple(sorted(
        (_opening_from_node(o) for o in node.openings), key=opening_sort_key,
    ))
    return ExportSurface(
        gml_id=node.gml_id,
        semantic_type=node.semantic_type,
        parent_id=node.parent_id,
        lod=node.lod,
        lod_origin=node.lod_origin,
        polygons=_polygons_from_nodes(node.polygons),
        openings=openings,
    )


def surface_group_from_node(node: SurfaceNode) -> ExportSurface:
    """Public seam for GS.3.2 (PLAN.md GS.3.2): the same per-surface fold
    ``groups_from_building_ir`` uses internally, exposed for a caller that
    has a single ``SurfaceNode`` (``ir_adapter.surface_node_from_record``)
    and no ``BuildingNode`` to run ``groups_from_building_ir`` over. Raises
    ``IRToGroupsError`` on the same structural problems (missing gml_id,
    missing exterior ring, missing opening host_id) - see that function's
    own docstring.
    """
    return _surface_from_node(node)


def _assert_unique(ids, label):
    seen = set()
    for gml_id in ids:
        if gml_id in seen:
            raise IRToGroupsError(f"duplicate {label} {gml_id!r}")
        seen.add(gml_id)


def _all_polygon_ids(surfaces: Tuple[ExportSurface, ...]):
    for surface in surfaces:
        for polygon in surface.polygons:
            yield polygon.polygon_id
        for opening in surface.openings:
            for polygon in opening.polygons:
                yield polygon.polygon_id


def _all_ring_ids(surfaces: Tuple[ExportSurface, ...]):
    for surface in surfaces:
        for polygon in surface.polygons:
            yield polygon.exterior.ring_id
            for ring in polygon.interiors:
                yield ring.ring_id
        for opening in surface.openings:
            for polygon in opening.polygons:
                yield polygon.exterior.ring_id
                for ring in polygon.interiors:
                    yield ring.ring_id


def groups_from_building_ir(building: BuildingNode) -> ExportBuildingGroups:
    """Convert a record-backed ``BuildingNode`` (P5.1/P5.2/P5.3) into a plain,
    Blender-independent ``ExportBuildingGroups`` - the smallest common
    contract the CityGML 2/3 writer back halves need (see module docstring).

    Deterministic: re-sorts every level by stable id regardless of the input
    tree's own construction order. Raises ``IRToGroupsError`` (never a
    partial result) for a missing/duplicate id or a structurally incomplete
    polygon/opening - see the module docstring's "Determinism and
    validation" section for the exact list.

    Installations (``BuildingNode.installations``) and any non-boundary-
    surface content are out of this module's scope (they are not part of
    ``ALLOWED_IR_KINDS``, PLAN.md P5.0) and are not represented in the
    output.
    """

    if building is None:
        raise IRToGroupsError("no Building IR given")
    if not building.gml_id:
        raise IRToGroupsError("Building IR has no gml_id")

    surfaces = tuple(sorted(
        (_surface_from_node(s) for s in building.boundary_surfaces), key=surface_sort_key,
    ))

    _assert_unique((s.gml_id for s in surfaces), "surface gml_id")
    _assert_unique(
        (o.gml_id for s in surfaces for o in s.openings), "opening gml_id",
    )
    _assert_unique(_all_polygon_ids(surfaces), "polygon_id")
    _assert_unique(_all_ring_ids(surfaces), "ring_id")

    return ExportBuildingGroups(building_gml_id=building.gml_id, surfaces=surfaces)


# ---------------------------------------------------------------------------
# P5.5: merge legacy Door passthrough openings into IR-derived groups
# ---------------------------------------------------------------------------


def merge_door_passthrough(
    groups: ExportBuildingGroups, door_openings: Tuple[ExportOpening, ...],
) -> ExportBuildingGroups:
    """Fold legacy-only Door ``ExportOpening`` facts onto the matching host
    surface's ``.openings`` in ``groups`` (PLAN.md P5.5).

    ``door_openings`` is expected to come from
    ``shared/authoring_core/blender/door_passthrough.py`` (or an
    equivalent producer emitting only ``semantic_type == "Door"``
    entries sourced from legacy, non-record-backed Blender objects).
    This function itself never inspects a Door's provenance - it is a
    pure id-and-structure merge, and it fails closed rather than trust
    that the two sources are disjoint:

    - every Door ``gml_id`` must be unique among the Doors themselves,
      and must not collide with any IR surface or opening ``gml_id``;
    - every Door polygon/ring id must be unique among the Doors
      themselves, and must not collide with any IR polygon/ring id
      (document-wide uniqueness, D-3, same rule ``groups_from_building_ir``
      already enforces for the record-backed side);
    - every Door's ``host_id`` must name a surface already present in
      ``groups.surfaces`` - this function never invents a host surface.

    Any violation raises ``DoorPassthroughMergeError`` before anything is
    merged; ``groups`` is never partially updated. An empty
    ``door_openings`` returns ``groups`` unchanged (including object
    identity), so callers on the record-backed-only path pay nothing.
    """
    if not door_openings:
        return groups

    existing_surface_ids = {s.gml_id for s in groups.surfaces}
    existing_opening_ids = {o.gml_id for s in groups.surfaces for o in s.openings}
    existing_polygon_ids = set(_all_polygon_ids(groups.surfaces))
    existing_ring_ids = set(_all_ring_ids(groups.surfaces))

    def _assert_unique_or_merge_error(ids, label):
        try:
            _assert_unique(ids, label)
        except IRToGroupsError as exc:
            raise DoorPassthroughMergeError(str(exc)) from exc

    door_ids = tuple(o.gml_id for o in door_openings)
    _assert_unique_or_merge_error(door_ids, "legacy Door gml_id")
    for door_id in door_ids:
        if door_id in existing_surface_ids or door_id in existing_opening_ids:
            raise DoorPassthroughMergeError(
                f"Door gml_id {door_id!r} collides with an existing IR feature id"
            )

    door_polygon_ids = tuple(
        p.polygon_id for o in door_openings for p in o.polygons
    )
    _assert_unique_or_merge_error(door_polygon_ids, "legacy Door polygon_id")
    for polygon_id in door_polygon_ids:
        if polygon_id in existing_polygon_ids:
            raise DoorPassthroughMergeError(
                f"Door polygon_id {polygon_id!r} collides with an existing IR polygon_id"
            )

    door_ring_ids = tuple(
        ring.ring_id
        for opening in door_openings
        for polygon in opening.polygons
        for ring in (polygon.exterior, *polygon.interiors)
    )
    _assert_unique_or_merge_error(door_ring_ids, "legacy Door ring_id")
    for ring_id_ in door_ring_ids:
        if ring_id_ in existing_ring_ids:
            raise DoorPassthroughMergeError(
                f"Door ring_id {ring_id_!r} collides with an existing IR ring_id"
            )

    openings_by_host = defaultdict(list)
    for opening in door_openings:
        if opening.host_id not in existing_surface_ids:
            raise DoorPassthroughMergeError(
                f"Door {opening.gml_id!r}: host surface {opening.host_id!r} "
                "is not part of this Building's IR"
            )
        openings_by_host[opening.host_id].append(opening)

    new_surfaces = []
    for surface in groups.surfaces:
        extra = openings_by_host.get(surface.gml_id)
        if not extra:
            new_surfaces.append(surface)
            continue
        merged_openings = tuple(sorted(
            surface.openings + tuple(extra), key=opening_sort_key,
        ))
        new_surfaces.append(replace(surface, openings=merged_openings))

    return ExportBuildingGroups(
        building_gml_id=groups.building_gml_id, surfaces=tuple(new_surfaces),
    )
