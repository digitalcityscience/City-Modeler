# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Headless facade-element placement kernel (facade-authoring ticket 01).

The single, UI-agnostic authoring sink for LoD3 facade elements
(``docs/adr/0004-...`` Decision 6/7/D10). ``place_semantic_element`` takes
three explicit arguments and has **zero** dependency on Blender selection,
Edit-Mode state, viewport raycast, modal-operator state, or the active
area/region - every input is passed in. The interactive Draw/Stamp operators
(later tickets) and a future deep-learning detector are all thin adapters that
build these three arguments and call this function; a manually drawn, stamped,
and machine-detected element are therefore structurally identical.

Ticket 01 implements the ``InteriorRingOpeningStrategy`` for a Window;
facade-authoring ticket 06 reuses it verbatim for a *floating* Door (one
that doesn't reach the wall's lower edge). A ground-touching Door instead
uses the ``ExteriorNotchStrategy`` (``_place_exterior_notch_opening``
below, ticket 06's deferred half):

- An Opening has a **wall interior ring** (a ``gml:interior`` face left on the
  host plane) and an **opening fill** in its own object. Both are cut with
  Openings_Cutter's proven ring/UV/material machinery
  (``create_joined_surfaces``); the fill is then moved out and made into a
  shallow centred prism, so geometry still lives in exactly one place.
- Every identity is minted fresh (ADR 0004 D5) so the exporter's two dedup traps
  (``io_v2/writer/exporter.py``: skip a shared mesh-data block; skip a reused
  ``gml_polygon_id``) never swallow the instance.
- A freshly minted **opening link** (ADR 0004 D2) is written to both the fill
  object and the wall ring material. The ring/opening check recognises the two
  symmetric prism caps while preserving the legacy imported-geometry rule.

A ground-touching Door has no ring at all: the host wall's own exterior
boundary polygon is reshaped so its lower edge has a notch carved into it,
reaching the boundary (see ``shared/ring_opening_sync.py``'s module
docstring, "Doors were originally excluded by construction"). The exterior
fill is still cut with ``create_joined_surfaces(generate_interior=False)``;
no interior-ring face is created and no opening link is minted, so
``is_ring_paired_opening`` stays False for it, same as any legacy imported
ground Door.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import bmesh
import bpy
from mathutils import Matrix, Vector

from ..Openings_Cutter.Openings_Cutter import (
    create_joined_surfaces,
    detect_citygml_version_for_object,
    ensure_uv_layer,
    fit_uv_affine_from_source,
    restore_selected_face,
    opening_surface_type_for_version,
    point_to_plane_basis,
)
from .authoring_core.blender.registry import resolve
from .authoring_core.blender.storage import RECORD_KEY
from .object_helpers import face_material
from .ring_opening_sync import (
    OPENING_LINK_KEY,
    RING_OPENING_SYNC_TOLERANCE_M,
    _reference_cap,
    opening_candidate_faces,
    wall_interior_ring_faces,
)
from .semantic_appearance import SEMANTIC_PALETTE
from .writer_geometry import dedupe_ring_points, describe_ring_invalid, validate_ring


# --------------------------------------------------------------------------
# Explicit input contract (ADR 0004 D10). Every field is data - no bpy.context.
# --------------------------------------------------------------------------
@dataclass
class SurfaceContext:
    """The resolved target surface a placement lands on. Built by the resolver
    (ticket 02) from a selected face, by a preset, or by a detector - never read
    from the active selection inside the kernel."""

    object_name: str            # host WallSurface/RoofSurface object
    semantic_id: str            # that surface's gml_id
    semantic_type: str          # WallSurface | RoofSurface | ...
    building_id: str            # owning Building gml_id
    face_indices: list[int]     # the host face(s) on that surface
    origin_world: Vector        # min-uv corner of the surface frame
    normal_world: Vector        # outward surface normal
    horizontal_axis_world: Vector
    vertical_axis_world: Vector
    width: float                # surface extent along the horizontal axis (m)
    height: float               # surface extent along the vertical axis (m)



# Authoring default depth for a freshly extruded Window/Door fill (facade-
# authoring ticket 01). window-door-upload ticket 02 reuses this same value
# (and the ``_make_centered_prism`` primitive it feeds) as the synthetic
# default applied to flat imported openings - see
# ``apply_synthetic_opening_depth`` below.
DEFAULT_WINDOW_DEPTH = 0.04

# How close a door's bottom edge must be to the host surface's own bottom
# edge (v=0 in the surface frame) to count as "reaches the wall's lower
# edge" (ADR 0004 D3). Deliberately the same value as
# ``ring_opening_sync.RING_OPENING_SYNC_TOLERANCE_M`` (1 cm - absorbs float
# round-trip noise without absorbing a real placement choice), kept as its
# own named constant since the two tolerances answer different questions.
DOOR_GROUND_TOLERANCE_M = 0.01

# A notch is only valid when the target face really has a straight bottom
# boundary for the door to replace.  This is intentionally much tighter than
# the classification tolerance above: the latter absorbs a user's near-ground
# draw, whereas this protects the mesh topology from a sloped/trapezoidal
# boundary.
NOTCH_BOUNDARY_TOLERANCE_M = 1e-5


@dataclass
class FacadeElementDefinition:
    """The reusable, parametric description of an element - the template Draw,
    Stamp, an asset preset, and a detector all resolve to (ADR 0004 D8)."""

    semantic_type: str          # Window | Door | Balcony | Chimney
    width: float
    height: float
    # Newly authored Windows/Doors are a shallow, centred solid by default.
    # This is deliberately an authoring default: imported meshes never pass
    # through this definition and therefore retain their source recess/depth.
    depth: float = DEFAULT_WINDOW_DEPTH
    frame_thickness: float = 0.0
    wall_offset: float = 0.0
    generator_name: str = ""
    source_object_name: str | None = None
    door_touches_ground: bool | None = None   # None = auto (D3), ticket 06
    source_gml_id: str = ""     # identity of source_object_name (P2.3: resolved by id)
    entrance: bool = False      # B81: Door only: stands on the ground (notch), writes the building's address


@dataclass
class PlacementRequest:
    """Where/how one placement lands on the surface, plus non-behavioral
    provenance (ADR 0004 D10) recording origin only."""

    position_uv: tuple[float, float] = (0.5, 0.5)   # fractional centre on the surface
    size_override: tuple[float, float] | None = None
    # provenance - records origin, never changes the authoring path
    source: str = "manual_draw"   # manual_draw | semantic_stamp | asset_preset | deep_learning
    model_name: str | None = None
    model_version: str | None = None
    confidence: float | None = None
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _ExteriorNotchGeometry:
    face_index: int
    edge_index: int
    opening_corners: tuple[Vector, Vector, Vector, Vector]
    edge_start_contact: Vector
    edge_end_contact: Vector


_SOURCE_ELEMENT_TEMPLATES = {
    "2.0": "bldg:opening/bldg:{type}",
    "3.0": "con:fillingSurface/con:{type}",
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def place_semantic_element(
    definition: FacadeElementDefinition | None = None,
    surface: SurfaceContext | None = None,
    placement: PlacementRequest | None = None,
    *,
    source_object=None,
    surface_context: SurfaceContext | None = None,
    position_world=None,
    size_override: tuple[float, float] | None = None,
    rotation_override=None,
) -> bpy.types.Object:
    """Sole headless authoring sink for Draw and Semantic Stamp.

    ``definition``/``surface``/``placement`` is the parametric Draw path.
    The keyword-only form is the Semantic Stamp path:

    ``place_semantic_element(source_object=..., surface_context=...,
    position_world=...)``.

    Both forms deliberately converge before strategy dispatch.  The stamp
    adapter extracts the source Window's physical width, height and depth;
    its orientation is supplied by the target ``SurfaceContext`` (not copied
    from the source world transform), and the usual opening/ring, identity,
    hierarchy and exporter metadata logic remains the one implementation.
    ``rotation_override`` is reserved for future non-window assets; Windows
    always align to their host WallSurface.
    """
    if source_object is not None:
        if any(value is not None for value in (definition, surface, placement)):
            raise ValueError("Semantic Stamp accepts either source_object or a Draw placement, not both")
        if surface_context is None or position_world is None:
            raise ValueError("Semantic Stamp requires surface_context and position_world")
        if rotation_override is not None:
            raise ValueError("Window Semantic Stamp does not support rotation_override")
        definition = definition_from_source_object(source_object)
        placement = PlacementRequest(
            position_uv=surface_uv_from_world_point(surface_context, position_world),
            size_override=size_override,
            source="semantic_stamp",
        )
        surface = surface_context

    if definition is None or surface is None or placement is None:
        raise ValueError("place_semantic_element requires definition, surface and placement")

    kind = (definition.semantic_type or "").strip().lower()
    if kind == "window":
        return _place_interior_ring_opening(definition, surface, placement)
    if kind == "door":
        if definition.entrance:
            definition, placement = _entrance_on_ground(surface, definition, placement)
            door = _place_door(definition, surface, placement)
            door["cgml_entrance_door"] = True
            return door
        if definition.door_touches_ground is not True:
            definition, placement = _door_on_balcony_floor(surface, definition, placement)
        return _place_door(definition, surface, placement)
    raise NotImplementedError(
        f"place_semantic_element: '{definition.semantic_type}' is not implemented "
        "(only Window and Door / interior-ring opening)."
    )


def _entrance_on_ground(surface, definition, placement):
    """B81: an entrance door's bottom goes down to the face's lower edge (its top stays where it was
    drawn) and it is a ground notch (ADR 0004 D3), wherever its bottom was drawn."""
    import dataclasses
    u, v = placement.position_uv
    w, h = placement.size_override or (definition.width, definition.height)
    top = max(v * surface.height + h / 2.0, 0.1)
    definition = dataclasses.replace(definition, height=top, door_touches_ground=True)
    placement = dataclasses.replace(placement, position_uv=(u, (top / 2.0 - 1e-6) / surface.height),
                                    size_override=(w, top) if placement.size_override else None)
    return definition, placement


# B81: a door drawn this close to a balcony floor in front of it is put on that floor (soft snap;
# CityGML does not require it, it only keeps the model plausible)
BALCONY_DOOR_SNAP_M = 0.10


def _door_on_balcony_floor(surface, definition, placement):
    """B81: ``(definition, placement)`` with the door's bottom moved onto the floor of a balcony on this
    wall when it is within ``BALCONY_DOOR_SNAP_M`` of it and the floor runs under the door (top kept)."""
    import dataclasses
    from .lod_helpers import lod_promotion_root
    wall = resolve(surface.semantic_id, surface.object_name)
    if wall is None or surface.height <= 1e-9:
        return definition, placement
    origin, n = Vector(surface.origin_world), Vector(surface.normal_world)
    h_ax, v_ax = Vector(surface.horizontal_axis_world), Vector(surface.vertical_axis_world)
    u, v = placement.position_uv
    w, h = placement.size_override or (definition.width, definition.height)
    bottom, top = v * surface.height - h / 2.0, v * surface.height + h / 2.0
    left, right = u * surface.width - w / 2.0, u * surface.width + w / 2.0
    root = lod_promotion_root(wall)
    for balcony in (o for o in root.children_recursive
                    if o.type == 'MESH' and o.get("cgml_semantic_type") == "Balcony"):
        mats, mw = balcony.data.materials, balcony.matrix_world
        pts = [mw @ balcony.data.vertices[i].co for poly in balcony.data.polygons
               if poly.material_index < len(mats) and mats[poly.material_index] is not None
               and mats[poly.material_index].get("SurfaceTyp") == "OuterFloorSurface" for i in poly.vertices]
        if not pts or min(abs((p - origin).dot(n)) for p in pts) > 0.05:  # no floor, or not on this wall
            continue
        floor = sum((p - origin).dot(v_ax) for p in pts) / len(pts)
        along = [(p - origin).dot(h_ax) for p in pts]
        if abs(bottom - floor) > BALCONY_DOOR_SNAP_M or min(along) >= right or max(along) <= left:
            continue
        hh = top - floor
        definition = dataclasses.replace(definition, height=hh)
        placement = dataclasses.replace(placement, position_uv=(u, (floor + hh / 2.0) / surface.height),
                                        size_override=(w, hh) if placement.size_override else None)
        break
    return definition, placement


def _place_door(definition, surface, placement):
    """ADR 0004 D3: a ground notch when the door crosses the face's lower edge (or is forced), else ring + fill."""
    if definition.door_touches_ground is False:
        return _place_interior_ring_opening(definition, surface, placement)
    notch, reason = _resolve_exterior_notch_geometry(surface, definition, placement)
    if notch is None:
        # B65: a bottom within the D3 ground tolerance but just above the face's lower edge
        # (rounding, a click a few mm high) crosses nothing; put it on the edge, else it
        # silently became a ring hugging the ground
        grounded = _door_bottom_on_ground(surface, definition, placement)
        if grounded is not None:
            notch, reason = _resolve_exterior_notch_geometry(surface, definition, grounded)
            if notch is not None:
                placement = grounded
    if notch is not None:
        return _place_exterior_notch_opening(definition, surface, placement, notch)
    if definition.door_touches_ground is True:
        raise ValueError(f"Cannot force a ground-notch Door: {reason}")
    return _place_interior_ring_opening(definition, surface, placement)


def _door_bottom_on_ground(surface, definition, placement):
    """``placement`` moved down so the door's bottom lies just below the face's lower edge, when
    that bottom is above the edge by at most ``DOOR_GROUND_TOLERANCE_M``; else ``None``."""
    import dataclasses
    if surface.height <= 1e-9:
        return None
    u, v = placement.position_uv
    elem_h = placement.size_override[1] if placement.size_override is not None else definition.height
    bottom = v * surface.height - elem_h / 2.0
    if not 0.0 <= bottom <= DOOR_GROUND_TOLERANCE_M:
        return None
    return dataclasses.replace(placement, position_uv=(u, (elem_h / 2.0 - 1e-6) / surface.height))


def door_touches_ground(
    surface: SurfaceContext,
    placement: PlacementRequest,
    definition: FacadeElementDefinition,
) -> bool:
    """The ADR 0004 D3 ground-vs-floating *candidate* classifier for a Door:
    True if its bottom edge sits within ``DOOR_GROUND_TOLERANCE_M`` of the
    host surface's bottom extent (v=0 in the surface frame).
    ``_resolve_exterior_notch_geometry`` then verifies that the drawn Door
    actually crosses one live host boundary edge before a notch is made.
    ``definition.door_touches_ground`` is the explicit user override and
    always wins when set.

    Pure UV-frame arithmetic over the three plain-data arguments - no mesh,
    no ``bpy.context`` - so it is exercised directly by the headless test
    alongside ``place_semantic_element`` itself."""
    if definition.door_touches_ground is not None:
        return definition.door_touches_ground
    _u, v = placement.position_uv
    if placement.size_override is not None:
        _elem_w, elem_h = placement.size_override
    else:
        elem_h = definition.height
    bottom_v = v * surface.height - elem_h / 2.0
    return bottom_v <= DOOR_GROUND_TOLERANCE_M


def _source_window_axes(source_object) -> tuple[Vector, Vector, Vector]:
    """A Window's own (horizontal, vertical, normal) facade frame, derived
    purely from its own mesh - the same pure math ``resolve_surface_context``
    uses for a WallSurface face (``facade_surface_resolver.compute_facade_frame``),
    applied here to the Window's own largest planar face instead.

    Semantic Stamp only ever needs to *read* the source Window's own shape; it
    never needs to know which WallSurface it came from. Resolving that host is
    both unnecessary and unreliable - imported openings' recorded
    ``filling_parent_surface_id`` can be orphaned (the real host WallSurface
    was never imported/exploded, or the id diverged from the object graph) -
    so deriving the frame from the source's own geometry keeps Stamp working
    regardless of what, if anything, the source is parented to or references.
    """
    from .facade_surface_resolver import compute_facade_frame

    mesh = source_object.data
    if not mesh.polygons:
        raise ValueError(f"Semantic Stamp source '{source_object.name}' has no geometry")
    mw = source_object.matrix_world
    largest = max(mesh.polygons, key=lambda p: p.area)
    world_points = [tuple(mw @ mesh.vertices[vi].co) for vi in largest.vertices]
    frame = compute_facade_frame(world_points)
    return Vector(frame.horizontal_axis), Vector(frame.vertical_axis), Vector(frame.normal)


def definition_from_source_object(source_object) -> FacadeElementDefinition:
    """Read a Window template's dimensions without reading Blender selection.

    Width/height/depth are measured by projecting the source's own
    world-space vertices onto its own self-derived facade frame
    (``_source_window_axes``) even when the object has rotation or
    non-uniform transforms. The created fill is then replaced with a fully
    independent copy of this source mesh by ``_apply_stamp_source_copy``
    below.

    Public (not module-private): the interactive Semantic Stamp operator
    (``ops/facade_authoring.py``) also calls this directly to size its
    click-follow ghost preview before any placement happens.
    """
    if source_object is None or getattr(source_object, "type", None) != "MESH":
        raise ValueError("Semantic Stamp source must be a Window or Door mesh object")
    feature = str(source_object.get("cgml3_feature") or "").strip().lower()
    if feature in {"door", "doorsurface"}:  # B50: a ring-paired Door stamps like a Window
        if str(source_object.get("cgml_authoring_topology") or "") == "ground_notch":
            raise ValueError(
                f"Semantic Stamp source '{source_object.name}' is a ground-notch Door (no wall ring); "
                "only ring-paired Windows and Doors can be stamped"
            )
        semantic_type = "Door"
    elif feature in {"window", "windowsurface"}:
        semantic_type = "Window"
    else:
        raise ValueError(
            f"Semantic Stamp source '{source_object.name}' is not a CityGML Window or Door"
        )

    h_axis, v_axis, n_axis = _source_window_axes(source_object)
    points = [source_object.matrix_world @ vertex.co for vertex in source_object.data.vertices]
    if not points:
        raise ValueError(f"Semantic Stamp source '{source_object.name}' has no geometry")

    def extent(axis):
        values = [point.dot(axis) for point in points]
        return max(values) - min(values)

    width, height, depth = extent(h_axis), extent(v_axis), extent(n_axis)
    if width <= 1e-9 or height <= 1e-9:
        raise ValueError(
            f"Semantic Stamp source '{source_object.name}' has degenerate Window dimensions"
        )
    return FacadeElementDefinition(
        semantic_type=semantic_type,
        width=width,
        height=height,
        depth=depth,
        generator_name="semantic_stamp",
        source_object_name=source_object.name,
        source_gml_id=str(source_object.get("gml_id") or ""),
    )


# --------------------------------------------------------------------------
# Host-face placement validation (AB-17, facade-authoring P3.2 create_feature
# validation path). Shared by the InteriorRingOpeningStrategy below (Window
# and floating Door) - the ExteriorNotchStrategy (ground Door) has its own
# equivalent boundary check in _resolve_exterior_notch_geometry.
#
# create_joined_surfaces writes the exterior fill + interior ring faces
# unconditionally: nothing anywhere in that call chain tests the drawn
# rectangle against the host face's real (possibly concave) exterior polygon
# or against any interior ring already cut into it. This validates the
# rectangle before any of that runs, so a refusal is a clean no-op. It uses
# the same 2-D projection (surface origin/horizontal/vertical axes) and the
# same segment-intersection/point-in-polygon primitives
# (_segment_intersection, _point_in_or_on_polygon, below) that
# _resolve_exterior_notch_geometry already uses for the ground-Door boundary
# check, so a concave host face is handled correctly there too.
# --------------------------------------------------------------------------
def _project_to_surface_uv(surface: SurfaceContext, points_world) -> list[tuple[float, float]]:
    origin = Vector(surface.origin_world)
    h_axis = Vector(surface.horizontal_axis_world).normalized()
    v_axis = Vector(surface.vertical_axis_world).normalized()
    return [((p - origin).dot(h_axis), (p - origin).dot(v_axis)) for p in points_world]


def _classify_rect_vs_polygon(rect_uv, polygon_uv) -> str:
    """Relationship of a rectangle to a (possibly concave) polygon, both in
    the same 2-D frame: ``"inside"``, ``"outside"`` or ``"straddles"``.

    Not a corner-only test: any proper edge/edge intersection between the
    rectangle and the polygon boundary is a straddle regardless of how many
    corners happen to test as inside - this is what lets a rectangle that
    engulfs a concave notch (all 4 corners inside, boundary still crossed)
    be caught. Only once no edge crosses does a single corner's
    point-in-polygon result decide inside vs. outside for the whole
    rectangle (valid for a simple polygon once no boundary crossing exists).
    """
    n = len(polygon_uv)
    for i in range(4):
        r0, r1 = rect_uv[i], rect_uv[(i + 1) % 4]
        for j in range(n):
            p0, p1 = polygon_uv[j], polygon_uv[(j + 1) % n]
            if _segment_intersection(r0, r1, p0, p1) is not None:
                return "straddles"
    return "inside" if _point_in_or_on_polygon(rect_uv[0], polygon_uv) else "outside"


def _validate_new_opening_rect(wall, face_index, surface: SurfaceContext, corners_world) -> None:
    mesh = wall.data
    if not (0 <= face_index < len(mesh.polygons)):
        raise ValueError(f"face_index {face_index} is out of range for {wall.name!r}")
    poly = mesh.polygons[face_index]
    if len(poly.vertices) < 3:
        raise ValueError(f"host face {face_index} is degenerate")
    # a zero-area rectangle (e.g. parallel surface axes) would otherwise be reported as "outside"
    rect_check = validate_ring([tuple(p) for p in corners_world])
    if not rect_check.valid:
        raise ValueError(
            "placement refused: the drawn rectangle is degenerate "
            f"({describe_ring_invalid(rect_check)}) on face {face_index} of {wall.name!r}"
        )

    mw = wall.matrix_world
    host_world = [mw @ mesh.vertices[vi].co.copy() for vi in poly.vertices]
    host_uv = _project_to_surface_uv(surface, host_world)
    rect_uv = _project_to_surface_uv(surface, corners_world)

    verdict = _classify_rect_vs_polygon(rect_uv, host_uv)
    if verdict == "outside":
        raise ValueError(
            "placement refused: the drawn rectangle falls entirely outside the "
            f"host face's solid area (face {face_index} of {wall.name!r}), e.g. inside an "
            "existing cut-out"
        )
    if verdict == "straddles":
        raise ValueError(
            "placement refused: the drawn rectangle crosses the host face's own "
            f"exterior boundary (face {face_index} of {wall.name!r})"
        )

    for ring_face_index, ring_world_verts, _link in wall_interior_ring_faces(wall):
        if ring_face_index == face_index or len(ring_world_verts) < 3:
            continue
        ring_uv = _project_to_surface_uv(surface, ring_world_verts)
        ring_verdict = _classify_rect_vs_polygon(rect_uv, ring_uv)
        if ring_verdict == "straddles":
            raise ValueError(
                "placement refused: the drawn rectangle overlaps an existing opening's "
                f"interior ring (wall face {ring_face_index} of {wall.name!r})"
            )
        if ring_verdict == "inside":
            raise ValueError(
                "placement refused: the drawn rectangle lies entirely inside an existing "
                f"opening's interior ring (wall face {ring_face_index} of {wall.name!r})"
            )


# --------------------------------------------------------------------------
# InteriorRingOpeningStrategy (Window)
# --------------------------------------------------------------------------
def _place_interior_ring_opening(
    definition: FacadeElementDefinition,
    surface: SurfaceContext,
    placement: PlacementRequest,
) -> bpy.types.Object:
    wall = resolve(surface.semantic_id, surface.object_name)
    if wall is None or wall.type != "MESH":
        raise ValueError(f"SurfaceContext.object_name {surface.object_name!r} is not a mesh object")
    if not surface.face_indices:
        raise ValueError("SurfaceContext.face_indices is empty - no host face to cut into")

    # AB-17: create_joined_surfaces writes the exterior fill + interior ring
    # faces unconditionally, with no test that the rectangle actually lands
    # inside the host face's real (possibly concave) polygon or clear of any
    # ring already cut into it. Validate before anything else so a refusal
    # here - like the Stamp-source check right below - is a clean no-op:
    # nothing has been read from create_joined_surfaces, no mode switch has
    # happened yet, no mesh/records have been touched.
    face_index = surface.face_indices[0]
    rect_corners_world = _rect_corners_world(surface, definition, placement)
    _validate_new_opening_rect(wall, face_index, surface, rect_corners_world)

    # Resolve and validate the Semantic Stamp source (export-error ticket 02)
    # before any wall/fill mutation below - a refusal here must be a clean
    # no-op, and by this point nothing has been cut or created yet.
    source_object = None
    if definition.source_object_name:
        source_object = resolve(definition.source_gml_id, definition.source_object_name)
        if source_object is None:
            raise ValueError(f"Semantic Stamp source '{definition.source_object_name}' no longer exists")
        _validate_stamp_source_ring(source_object)

    # create_joined_surfaces is Edit-Mode-safe on its own (it drops to Object
    # Mode and restores the caller's mode in a `finally`), but the
    # fill-extraction bmesh surgery below (_extract_single_face_object /
    # _delete_wall_face) writes mesh data with a plain (non-edit) bmesh, which
    # Blender refuses once that `finally` has put the wall back in Edit Mode -
    # the exact class of bug ticket 02 found in split_into_semantic_surfaces.
    # Force Object Mode for the whole placement (cut + extract + delete) and
    # restore Edit Mode only once every mutation is done.
    original_mode = wall.mode
    if original_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    kind = (definition.semantic_type or "").strip().lower()
    canonical_type = "Window" if kind == "window" else "Door"

    src_material, uv_json, uv_map_name, world_normal = _source_face_info(wall, face_index)
    if src_material is None:
        raise ValueError("host face carries no material - cannot resolve surface ids/UVs")

    corners = rect_corners_world

    version = detect_citygml_version_for_object(wall)
    schema_version = str(wall.get("cgml_schema_version") or ("2.0" if version == "2.0" else "3.0"))

    # Cut the planar exterior fill + interior ring into the wall with the
    # existing, proven machinery. The ring deliberately remains on this plane;
    # the extracted new Window/floating-Door is made solid below.
    faces_before = len(wall.data.polygons)
    create_joined_surfaces(
        obj=wall,
        world_corners=corners,
        opening_type=canonical_type,
        plane_normal=world_normal,
        source_material=src_material,
        uv_mapping_json=uv_json,
        source_uv_map_name=uv_map_name,
        source_face_index=face_index,
        generate_interior=True,
    )
    new_face_indices = list(range(faces_before, len(wall.data.polygons)))
    ext_face_index, int_face_index = _classify_new_faces(wall, new_face_indices)
    if ext_face_index is None or int_face_index is None:
        raise RuntimeError(
            "expected create_joined_surfaces to add one exterior fill + one interior ring "
            f"face, got {len(new_face_indices)} new face(s)"
        )

    link = f"OPENINGLINK_{uuid.uuid4().hex}"

    # Stamp the opening link on the interior-ring material (survives face
    # re-indexing when the fill face is removed below) and grab the exterior
    # fill material's ids before we move the face out.
    ring_mat = wall.data.materials[wall.data.polygons[int_face_index].material_index]
    ring_mat[OPENING_LINK_KEY] = link

    ext_mat = wall.data.materials[wall.data.polygons[ext_face_index].material_index]
    opening_gml_id = str(ext_mat.get("con_surface_id") or "").strip() or f"ID_{uuid.uuid4().hex}"
    filling_parent = str(ext_mat.get("filling_parent_surface_id") or wall.get("gml_id") or "").strip()

    # Move the exterior fill face into its own object (its own mesh-data block,
    # no linked duplicate), then delete it from the wall so geometry lives in
    # exactly one place. The interior ring stays behind on the wall.
    fill = _extract_single_face_object(
        wall,
        ext_face_index,
        name=_fill_object_name(opening_gml_id, schema_version, canonical_type),
    )
    _delete_wall_face(wall, ext_face_index)

    # The cutter material carries the newly minted CityGML identities, but it
    # began as a copy of the host wall material. Replace it on the new element
    # only with a dedicated viewport material (Window: glass; Door: its own
    # opaque semantic-palette colour); imported materials and the wall/ring
    # material are never changed.
    if canonical_type == "Window":
        _assign_authored_window_glass(fill)
    else:
        _assign_authored_door_material(fill)

    # The wall's interior ring stays exactly on the host plane.  Only the new
    # fill is made solid, symmetrically about that plane: this retains the
    # ring as the export/sync reference while giving authored elements a
    # visible, physical 4 cm default depth.
    _make_centered_prism(fill, world_normal, definition.depth)

    # Stamp is deliberately not a parametric re-creation: the cutter above
    # creates the paired wall interior ring, then this replaces its temporary
    # fill with an independent copy of the source Window's mesh, modifiers,
    # display settings and author-defined custom properties.  Semantic/export
    # identity is re-minted below, never inherited from the source.
    stamp_matrix = None
    if source_object is not None:
        stamp_matrix = _apply_stamp_source_copy(
            fill, source_object, surface, placement.position_uv,
        )
    _mark_display_depth(fill, definition.depth)

    opening_surface_type = opening_surface_type_for_version(canonical_type, version)
    fill["cgml3_feature"] = opening_surface_type
    fill["gml_id"] = opening_gml_id
    fill["structure_part"] = "opening"
    fill["cgml_schema_version"] = schema_version
    fill["cgml_source_element"] = _SOURCE_ELEMENT_TEMPLATES.get(
        "2.0" if version == "2.0" else "3.0", _SOURCE_ELEMENT_TEMPLATES["3.0"]
    ).format(type=opening_surface_type)
    if filling_parent:
        fill["filling_parent_surface_id"] = filling_parent
        fill["opening_surface_id"] = filling_parent
        fill["cgml_parent_id"] = filling_parent
    fill[OPENING_LINK_KEY] = link
    fill["semantic_type"] = canonical_type
    if canonical_type == "Door":
        fill["cgml_authoring_topology"] = "floating_ring"

    if definition.source_object_name:
        _remint_stamp_material_identities(fill.data, opening_gml_id, filling_parent)
    _unify_opening_ids(fill.data, opening_gml_id)

    # Provenance (D10) - records origin only.
    fill["cgml_source"] = placement.source
    if placement.model_name:
        fill["cgml_model_name"] = placement.model_name
    if placement.model_version:
        fill["cgml_model_version"] = placement.model_version
    if placement.confidence is not None:
        fill["cgml_confidence"] = float(placement.confidence)

    # Link into the wall's collection and parent under the WallSurface via the
    # opening relation (outliner nesting mirrors the CityGML opening hierarchy).
    for coll in wall.users_collection:
        coll.objects.link(fill)
        break
    else:
        bpy.context.scene.collection.objects.link(fill)
    fill.parent = wall
    fill.matrix_world = stamp_matrix if stamp_matrix is not None else wall.matrix_world.copy()

    if original_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

    return fill


# --------------------------------------------------------------------------
# ExteriorNotchStrategy (ground-touching Door, facade-authoring ticket 06
# follow-up)
# --------------------------------------------------------------------------
def _place_exterior_notch_opening(
    definition: FacadeElementDefinition,
    surface: SurfaceContext,
    placement: PlacementRequest,
    notch: _ExteriorNotchGeometry,
) -> bpy.types.Object:
    """A ground-touching Door: an unpaired exterior fill cut into the host
    wall, with the wall's own exterior boundary polygon reshaped to have a
    notch reaching its lower edge - no interior ring, no opening link (module
    docstring above / ``shared/ring_opening_sync.py``)."""
    wall = resolve(surface.semantic_id, surface.object_name)
    if wall is None or wall.type != "MESH":
        raise ValueError(f"SurfaceContext.object_name {surface.object_name!r} is not a mesh object")
    if not surface.face_indices:
        raise ValueError("SurfaceContext.face_indices is empty - no host face to cut into")

    original_mode = wall.mode
    if original_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    face_index = notch.face_index
    mesh = wall.data
    if not (0 <= face_index < len(mesh.polygons)):
        raise ValueError(f"face_index {face_index} out of range for {wall.name!r}")
    src_material, uv_json, uv_map_name, world_normal = _source_face_info(wall, face_index)
    if src_material is None:
        raise ValueError("host face carries no material - cannot resolve surface ids/UVs")

    corners = list(notch.opening_corners)

    version = detect_citygml_version_for_object(wall)
    schema_version = str(wall.get("cgml_schema_version") or ("2.0" if version == "2.0" else "3.0"))

    # Cut only the planar exterior fill - no interior ring, ADR 0004 D3.
    faces_before = len(wall.data.polygons)
    create_joined_surfaces(
        obj=wall,
        world_corners=corners,
        opening_type="Door",
        plane_normal=world_normal,
        source_material=src_material,
        uv_mapping_json=uv_json,
        source_uv_map_name=uv_map_name,
        source_face_index=face_index,
        generate_interior=False,
    )
    new_face_indices = list(range(faces_before, len(wall.data.polygons)))
    if len(new_face_indices) != 1:
        raise RuntimeError(
            "expected create_joined_surfaces(generate_interior=False) to add exactly one "
            f"exterior fill face, got {len(new_face_indices)}"
        )
    ext_face_index = new_face_indices[0]

    ext_mat = wall.data.materials[wall.data.polygons[ext_face_index].material_index]
    opening_gml_id = str(ext_mat.get("con_surface_id") or "").strip() or f"ID_{uuid.uuid4().hex}"
    filling_parent = str(ext_mat.get("filling_parent_surface_id") or wall.get("gml_id") or "").strip()

    # Move the exterior fill face into its own object, then delete it from the
    # wall - same as the InteriorRingOpeningStrategy above. ext_face_index is
    # always the wall's highest-indexed face at this point (just appended by
    # create_joined_surfaces), so deleting it never shifts face_index.
    fill = _extract_single_face_object(
        wall,
        ext_face_index,
        name=_fill_object_name(opening_gml_id, schema_version, "Door"),
    )
    _delete_wall_face(wall, ext_face_index)

    # Only now reshape the host boundary - it appends a new face of its own,
    # so it must run after the fill extraction above, never before it.
    _notch_host_face(
        wall, notch, surface,
        uv_json, uv_map_name,
    )
    # corners are (top_right, top_left, bottom_left, bottom_right)
    _weld_door_corners_into_neighbours(wall, corners[2:], skip=(wall, fill))

    _assign_authored_door_material(fill)
    _make_centered_prism(fill, world_normal, definition.depth)
    _mark_display_depth(fill, definition.depth)

    opening_surface_type = opening_surface_type_for_version("Door", version)
    fill["cgml3_feature"] = opening_surface_type
    fill["gml_id"] = opening_gml_id
    fill["structure_part"] = "opening"
    fill["cgml_schema_version"] = schema_version
    fill["cgml_source_element"] = _SOURCE_ELEMENT_TEMPLATES.get(
        "2.0" if version == "2.0" else "3.0", _SOURCE_ELEMENT_TEMPLATES["3.0"]
    ).format(type=opening_surface_type)
    if filling_parent:
        fill["filling_parent_surface_id"] = filling_parent
        fill["opening_surface_id"] = filling_parent
        fill["cgml_parent_id"] = filling_parent
    # Deliberately no OPENING_LINK_KEY: there is no wall interior ring to pair
    # with (ring_opening_sync.is_ring_paired_opening relies on its absence).
    fill["semantic_type"] = "Door"
    fill["cgml_authoring_topology"] = "ground_notch"
    _unify_opening_ids(fill.data, opening_gml_id)

    # Provenance (D10) - records origin only.
    fill["cgml_source"] = placement.source
    if placement.model_name:
        fill["cgml_model_name"] = placement.model_name
    if placement.model_version:
        fill["cgml_model_version"] = placement.model_version
    if placement.confidence is not None:
        fill["cgml_confidence"] = float(placement.confidence)

    for coll in wall.users_collection:
        coll.objects.link(fill)
        break
    else:
        bpy.context.scene.collection.objects.link(fill)
    fill.parent = wall
    fill.matrix_world = wall.matrix_world.copy()

    if original_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
        # the notched host face is a new (last) face; keep it selected like a window's wall, so the
        # next element needs no re-selecting
        restore_selected_face(wall, len(wall.data.polygons) - 1)

    return fill


def _weld_door_corners_into_neighbours(wall, points_world, skip=(), tol=1e-4):
    """B54: split every other boundary edge of the building that a ground
    door's bottom corner lies on (in practice the GroundSurface's front edge).
    Without it that edge is a T-junction: the shell is not edge-closed and the
    writer drops lodNSolid."""
    from .lod_helpers import lod_promotion_root

    root = lod_promotion_root(wall)
    if root is None:
        return
    for obj in (root, *root.children_recursive):
        if obj in skip or obj.type != "MESH" or obj.mode == 'EDIT':
            continue
        inv = obj.matrix_world.inverted()
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        changed = False
        for pw in points_world:
            p = inv @ Vector(pw)
            if any((v.co - p).length <= tol for v in bm.verts):
                continue
            for e in bm.edges:
                a, ab = e.verts[0].co, e.verts[1].co - e.verts[0].co
                if ab.length_squared == 0.0:
                    continue
                t = (p - a).dot(ab) / ab.length_squared
                if 0.0 < t < 1.0 and (a + ab * t - p).length <= tol:
                    _new_edge, nv = bmesh.utils.edge_split(e, e.verts[0], t)
                    nv.co = p
                    changed = True
                    break
        if changed:
            bm.to_mesh(obj.data)
            obj.data.update()
        bm.free()


def _host_wall_of(fill):
    if fill.parent is not None and fill.parent.type == "MESH":
        return fill.parent
    host_id = str(fill.get("filling_parent_surface_id") or "").strip()
    return next((o for o in bpy.data.objects if o.type == "MESH" and str(o.get("gml_id") or "") == host_id), None) \
        if host_id else None


def _dissolve_verts_at(obj, points_world, collinear_only, tol=1e-4):
    """Dissolve ``obj``'s vertices sitting on ``points_world``; with
    ``collinear_only`` only those whose two edges run straight through them
    (the extra corners ``_weld_door_corners_into_neighbours`` added). Returns
    the number of vertices dissolved."""
    inv = obj.matrix_world.inverted()
    pts = [inv @ Vector(p) for p in points_world]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    hits = []
    for v in bm.verts:
        if not any((v.co - p).length <= tol for p in pts):
            continue
        if collinear_only:
            if len(v.link_edges) != 2:
                continue
            d0, d1 = (e.other_vert(v).co - v.co for e in v.link_edges)
            if d0.length == 0.0 or d1.length == 0.0 or d0.normalized().dot(d1.normalized()) > -0.99999:
                continue
        hits.append(v)
    if hits:
        bmesh.ops.dissolve_verts(bm, verts=hits)
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return len(hits)


def close_opening(fill) -> str:
    """B55: the inverse of placing ``fill`` (a Window/Door about to be
    deleted), so the host wall is whole again and the shell stays closed:
    - ring-paired opening (Window, floating Door, imported ones too): delete
      the wall's interior-ring face it fills (opening link, else the ring the
      fill's faces coincide with);
    - ground-notch Door: dissolve the notch corners out of the wall's exterior
      ring and the collinear corners B54 welded into neighbours (GroundSurface).
    Returns a short description of what was closed ("" = nothing found)."""
    wall = _host_wall_of(fill)
    if wall is None or wall.type != "MESH" or wall.data is None:
        return ""
    link = str(fill.get(OPENING_LINK_KEY) or "").strip()
    fill_faces = list(opening_candidate_faces(fill))
    ring_faces = []
    for idx, ring_verts, ring_link in wall_interior_ring_faces(wall):
        if link and ring_link == link:
            ring_faces.append(idx)
        elif not ring_link:
            ref = _reference_cap(ring_verts, fill_faces)
            if ref is not None and ref[1] <= RING_OPENING_SYNC_TOLERANCE_M:
                ring_faces.append(idx)
    if ring_faces:
        bm = bmesh.new()
        bm.from_mesh(wall.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in ring_faces], context='FACES')
        bm.to_mesh(wall.data)
        bm.free()
        wall.data.update()
        return f"closed {len(ring_faces)} hole(s) in '{wall.name}'"

    if str(fill.get("cgml3_feature") or "").strip().lower() not in ("door", "doorsurface"):
        return ""
    # Ground-notch Door: its corners are the fill's vertices projected onto the wall plane.
    wall_faces = [f for f in wall.data.polygons if f.material_index < len(wall.data.materials)
                  and not (wall.data.materials[f.material_index] or {}).get("Interior")]
    if not wall_faces:
        return ""
    mw = wall.matrix_world
    n = (mw.to_3x3() @ wall_faces[0].normal).normalized()
    p0 = mw @ wall.data.vertices[wall_faces[0].vertices[0]].co
    corners = []
    for v in fill.data.vertices:
        w = fill.matrix_world @ v.co
        q = w - n * (w - p0).dot(n)
        if not any((q - c).length <= 1e-4 for c in corners):
            corners.append(q)
    if _dissolve_verts_at(wall, corners, collinear_only=False) == 0:
        return ""
    from .lod_helpers import lod_promotion_root

    root = lod_promotion_root(wall)
    welded = 0
    for obj in ((root, *root.children_recursive) if root is not None else ()):
        if obj not in (wall, fill) and obj.type == "MESH" and obj.mode != 'EDIT':
            welded += _dissolve_verts_at(obj, corners, collinear_only=True)
    return f"closed the door notch in '{wall.name}'" + (f", removed {welded} extra corner(s)" if welded else "")


def _notch_corners_world(surface: SurfaceContext, definition, placement):
    """The four world-space corners of a ground-touching Door's rectangle,
    as ``(top_right, top_left, bottom_left, bottom_right)`` - the same corner
    order ``_rect_corners_world`` returns. Unlike that helper, the bottom
    edge is pinned to exactly ``v=0`` (the host surface's own bottom edge)
    rather than centred on the placement's UV, so the notch always reaches
    the wall's real boundary regardless of float noise in the classifier's
    tolerance band."""
    h_axis = Vector(surface.horizontal_axis_world).normalized()
    v_axis = Vector(surface.vertical_axis_world).normalized()
    origin = Vector(surface.origin_world)

    u, _v = placement.position_uv
    if placement.size_override is not None:
        elem_w, elem_h = placement.size_override
    else:
        elem_w, elem_h = definition.width, definition.height
    half_w = elem_w / 2.0

    centre_u = origin + h_axis * (u * surface.width)
    bottom_left = centre_u - h_axis * half_w
    bottom_right = centre_u + h_axis * half_w
    top_left = bottom_left + v_axis * elem_h
    top_right = bottom_right + v_axis * elem_h
    return top_right, top_left, bottom_left, bottom_right


def _resolve_exterior_notch_geometry(
    surface: SurfaceContext,
    definition: FacadeElementDefinition,
    placement: PlacementRequest,
) -> tuple[_ExteriorNotchGeometry | None, str]:
    """Find the real boundary crossings of a door drawn through the ground.

    The opening is the part of the drawn rectangle that lies in the wall. Its
    two lower side edges must cross one host boundary edge; that edge can be
    sloped, so no coordinate is snapped to an invented horizontal baseline.
    """
    wall = resolve(surface.semantic_id, surface.object_name)
    if wall is None or wall.type != "MESH":
        return None, f"SurfaceContext.object_name {surface.object_name!r} is not a mesh object"
    if not surface.face_indices:
        return None, "SurfaceContext.face_indices is empty"
    face_index = surface.face_indices[0]
    mesh = wall.data
    if not (0 <= face_index < len(mesh.polygons)):
        return None, f"face_index {face_index} is out of range for {wall.name!r}"
    poly = mesh.polygons[face_index]
    if len(poly.vertices) < 3:
        return None, f"host face {face_index} is degenerate"

    origin = Vector(surface.origin_world)
    h_axis = Vector(surface.horizontal_axis_world).normalized()
    v_axis = Vector(surface.vertical_axis_world).normalized()
    world_positions = [wall.matrix_world @ mesh.vertices[vi].co.copy() for vi in poly.vertices]
    host = [((point - origin).dot(h_axis), (point - origin).dot(v_axis)) for point in world_positions]
    top_right, top_left, bottom_left, bottom_right = _rect_corners_world(surface, definition, placement)
    door = [((point - origin).dot(h_axis), (point - origin).dot(v_axis)) for point in (top_right, top_left, bottom_left, bottom_right)]
    if not all(_point_in_or_on_polygon(point, host) for point in door[:2]):
        return None, "the Door top edge lies outside the host face"
    left = _segment_polygon_hit(door[1], door[2], host)
    right = _segment_polygon_hit(door[0], door[3], host)
    if left is None or right is None:
        return None, "the drawn Door does not cross the host boundary on both lower sides"
    left_edge, left_point = left
    right_edge, right_point = right
    if left_edge != right_edge:
        return None, "the Door crosses more than one host boundary edge"

    def world(point):
        return origin + h_axis * point[0] + v_axis * point[1]

    start, end = host[left_edge], host[(left_edge + 1) % len(host)]
    left_t = _edge_parameter(start, end, left_point)
    right_t = _edge_parameter(start, end, right_point)
    left_world, right_world = world(left_point), world(right_point)
    if left_t <= right_t:
        opening = (top_right, top_left, left_world, right_world)
        start_contact, end_contact = left_world, right_world
    else:
        opening = (top_left, top_right, right_world, left_world)
        start_contact, end_contact = right_world, left_world
    return _ExteriorNotchGeometry(face_index, left_edge, opening, start_contact, end_contact), ""


def _edge_parameter(start, end, point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    return ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / max(dx * dx + dy * dy, 1e-12)


def _segment_polygon_hit(start, end, polygon):
    """The boundary hit furthest from the Door's top corner."""
    hits = []
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        hit = _segment_intersection(start, end, edge_start, edge_end)
        if hit is not None:
            t, point = hit
            if t > NOTCH_BOUNDARY_TOLERANCE_M:
                hits.append((t, index, point))
    if not hits:
        return None
    _t, index, point = max(hits)
    return index, point


def _segment_intersection(a, b, c, d):
    ab = (b[0] - a[0], b[1] - a[1])
    cd = (d[0] - c[0], d[1] - c[1])
    det = ab[0] * cd[1] - ab[1] * cd[0]
    if abs(det) <= NOTCH_BOUNDARY_TOLERANCE_M:
        return None
    ac = (c[0] - a[0], c[1] - a[1])
    t = (ac[0] * cd[1] - ac[1] * cd[0]) / det
    u = (ac[0] * ab[1] - ac[1] * ab[0]) / det
    if -NOTCH_BOUNDARY_TOLERANCE_M <= t <= 1 + NOTCH_BOUNDARY_TOLERANCE_M and -NOTCH_BOUNDARY_TOLERANCE_M <= u <= 1 + NOTCH_BOUNDARY_TOLERANCE_M:
        return t, (a[0] + t * ab[0], a[1] + t * ab[1])
    return None


def _point_in_or_on_polygon(point, polygon) -> bool:
    """Inclusive 2-D ray test in a surface's horizontal/vertical frame."""
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        dx, dy = x2 - x1, y2 - y1
        cross = (x - x1) * dy - (y - y1) * dx
        if abs(cross) <= NOTCH_BOUNDARY_TOLERANCE_M and min(x1, x2) - NOTCH_BOUNDARY_TOLERANCE_M <= x <= max(x1, x2) + NOTCH_BOUNDARY_TOLERANCE_M and min(y1, y2) - NOTCH_BOUNDARY_TOLERANCE_M <= y <= max(y1, y2) + NOTCH_BOUNDARY_TOLERANCE_M:
            return True
        if (y1 > y) != (y2 > y):
            x_at_y = x1 + (y - y1) * dx / dy
            if x < x_at_y:
                inside = not inside
    return inside


def _notch_host_face(wall, notch, surface, uv_mapping_json, uv_map_name):
    """Reshape the host WallSurface's own exterior boundary polygon so its
    lower edge has a notch carved out matching a ground-touching Door's
    footprint (ADR 0004 D3 ExteriorNotchStrategy) - see module docstring.

    Deletes the face's original 4 vertices/edges (unshared with any other
    face - every ``create_joined_surfaces`` face gets brand-new coincident
    verts, never reused ones) and rebuilds an 8-vertex loop: the two original
    "top" vertices, plus the door's own 4 corners spliced in across its
    original bottom edge, tracing up into the notch and back down instead of
    straight across.
    """
    mesh = wall.data
    face_index = notch.face_index
    poly = mesh.polygons[face_index]
    mw = wall.matrix_world
    mw_inv = mw.inverted()
    loop_vertex_indices = list(poly.vertices)
    world_positions = [mw @ mesh.vertices[vi].co.copy() for vi in loop_vertex_indices]

    new_world_positions = []
    for idx, point in enumerate(world_positions):
        new_world_positions.append(point)
        if idx == notch.edge_index:
            # Follow the existing boundary direction into the opening, across
            # its top, and back to the same boundary.  The contact points are
            # calculated intersections, so this also works on a sloped edge.
            new_world_positions.extend([
                notch.opening_corners[2],
                notch.opening_corners[1],
                notch.opening_corners[0],
                notch.opening_corners[3],
            ])
    new_local_positions = [mw_inv @ p for p in new_world_positions]

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    old_face = bm.faces[face_index]
    material_index = old_face.material_index
    bmesh.ops.delete(bm, geom=[old_face], context="FACES")

    new_verts = [bm.verts.new(co) for co in new_local_positions]
    bm.verts.ensure_lookup_table()
    new_face = bm.faces.new(new_verts)
    new_face.material_index = material_index

    # Same UV map create_joined_surfaces just wrote the exterior fill into,
    # so the reshaped host face's UVs land in the map the exporter reads.
    uv_layer = ensure_uv_layer(bm, uv_map_name)
    if uv_mapping_json:
        mapping = json.loads(uv_mapping_json)
        fitted = fit_uv_affine_from_source(mapping, new_world_positions)
        for loop, uv in zip(new_face.loops, fitted):
            loop[uv_layer].uv = (uv.x, uv.y)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_STAMP_IDENTITY_OVERRIDE_KEYS = {
    "gml_id",
    "cgml_parent_id",
    "filling_parent_surface_id",
    "opening_surface_id",
    OPENING_LINK_KEY,
    "cgml_source",
    "cgml_source_element",
    "cgml_schema_version",
    "cgml3_feature",
    "structure_part",
    # AB-14 (CAC-P3.2a): a copied record describes the source, not the
    # stamped copy. The real fix is create_feature minting a fresh record
    # for the copy; this exclusion is defense-in-depth so a record is never
    # copied even by a caller that reaches place_semantic_element directly.
    RECORD_KEY,
}


def _validate_stamp_source_ring(source) -> None:
    """Refuse a Semantic Stamp source whose mesh already contains a
    degenerate face, before any wall/fill mutation happens (export-error
    ticket 02, root-cause fix site 2 in
    ``docs/agent/export-error/opening-ring-degeneracy-findings.md``).

    ``_apply_stamp_source_copy`` copies ``source.data`` verbatim; a source
    that already carries one bad face (e.g. left over from an earlier buggy
    draw/stamp) would otherwise duplicate that corruption into every future
    stamped copy - the findings doc traced the bulk of a 985-ring-error
    export to exactly this. Checks every polygon (not just the largest,
    frame-defining one ``_source_window_axes`` uses) with the same
    degeneracy check the exporter itself uses to accept/reject a ring
    (ticket 01, ``writer_geometry.validate_ring``), since a corrupt
    secondary face can ride along even when the source's main face is fine.
    """
    mesh = source.data
    if not mesh.polygons:
        raise ValueError(f"Semantic Stamp source '{source.name}' has no geometry")
    mw = source.matrix_world
    for face_index, poly in enumerate(mesh.polygons):
        points = [tuple(mw @ mesh.vertices[vi].co) for vi in poly.vertices]
        result = validate_ring(points)
        if not result.valid:
            raise ValueError(
                f"Semantic Stamp source '{source.name}' has a degenerate face "
                f"(face #{face_index}, {describe_ring_invalid(result)}) - fix the source geometry before stamping"
            )


def _stamp_target_matrix(surface: SurfaceContext, position_uv) -> Matrix:
    """World matrix whose local X/Y/Z are target right/up/normal axes."""
    h_axis = Vector(surface.horizontal_axis_world).normalized()
    v_axis = Vector(surface.vertical_axis_world).normalized()
    normal = Vector(surface.normal_world).normalized()
    centre = surface_world_point_from_uv(surface, position_uv)
    return Matrix((
        (h_axis.x, v_axis.x, normal.x, centre.x),
        (h_axis.y, v_axis.y, normal.y, centre.y),
        (h_axis.z, v_axis.z, normal.z, centre.z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def _apply_stamp_source_copy(fill, source, surface: SurfaceContext, position_uv) -> Matrix:
    """Replace the cutter's temporary fill with a true, independent source copy.

    Source local transform is baked into the copied mesh, then the object is
    given a unit-scale target-frame matrix.  This preserves source geometry
    (including its local recess/offset) while ensuring the new object itself
    has ``scale == (1, 1, 1)`` as required by the stamp contract.
    """
    if source.type != "MESH":
        raise ValueError("Semantic Stamp source must be a mesh object")
    # Called again here (redundant with the earlier preflight in
    # _place_interior_ring_opening) so this function is self-protecting for
    # any future caller, not just today's one - the actual mesh copy below
    # must never run against unvalidated geometry.
    _validate_stamp_source_ring(source)

    old_mesh = fill.data
    copied_mesh = source.data.copy()
    copied_mesh.name = f"{source.data.name}_SemanticStamp"

    # Bake the source's own local offset into the independent mesh so target
    # object scale stays at one rather than leaking the source object's scale
    # into the new instance transform. The reference frame is the source's
    # *own* facade frame/centroid (not a resolved host WallSurface's matrix -
    # some imported Window objects are parented to a Building, and their
    # filling-parent metadata can even be orphaned, pointing at no live
    # object at all), so this never depends on finding the source's host.
    h_axis, v_axis, n_axis = _source_window_axes(source)
    world_points = [source.matrix_world @ vertex.co for vertex in source.data.vertices]
    centroid = sum(world_points, Vector()) / len(world_points)
    source_frame = Matrix((
        (h_axis.x, v_axis.x, n_axis.x, centroid.x),
        (h_axis.y, v_axis.y, n_axis.y, centroid.y),
        (h_axis.z, v_axis.z, n_axis.z, centroid.z),
        (0.0, 0.0, 0.0, 1.0),
    ))
    source_local = source_frame.inverted() @ source.matrix_world
    for vertex in copied_mesh.vertices:
        vertex.co = source_local @ vertex.co
    copied_mesh.update()
    fill.data = copied_mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    _copy_stamp_custom_properties(source, fill)
    _copy_stamp_modifiers(source, fill)
    _copy_stamp_display_settings(source, fill)
    return _stamp_target_matrix(surface, position_uv)


def _copy_stamp_custom_properties(source, target) -> None:
    """Copy authoring parameters while excluding source-specific identity."""
    for key in source.keys():
        if key not in _STAMP_IDENTITY_OVERRIDE_KEYS:
            target[key] = source[key]


def _copy_stamp_modifiers(source, target) -> None:
    """Recreate the source modifier stack without sharing modifier objects."""
    target.modifiers.clear()
    for source_modifier in source.modifiers:
        target_modifier = target.modifiers.new(source_modifier.name, source_modifier.type)
        for prop in source_modifier.bl_rna.properties:
            if prop.identifier in {"name", "rna_type", "type"} or prop.is_readonly:
                continue
            try:
                value = getattr(source_modifier, prop.identifier)
                # Self-references must point to the independent stamped copy.
                setattr(target_modifier, prop.identifier, target if value is source else value)
            except (AttributeError, TypeError, ValueError):
                # Some RNA properties are context-only or collection-valued;
                # Blender creates their safe defaults with the modifier.
                continue


def _copy_stamp_display_settings(source, target) -> None:
    """Carry the source object's viewport-facing display choices forward."""
    for attr in (
        "display_type", "display_bounds_type", "show_name", "show_axis",
        "show_bounds", "show_wire", "show_all_edges", "show_texture_space",
        "show_transparent", "color",
    ):
        try:
            setattr(target, attr, getattr(source, attr))
        except (AttributeError, TypeError, ValueError):
            continue


_OPENING_ID_KEYS = ("opening_gml_id", "opening_id", "con_opening_id")


def _unify_opening_ids(mesh, opening_gml_id: str) -> None:
    """Give the authored opening one id, as imported openings have.

    The Openings Cutter mints its own ``opening_id`` beside ``con_surface_id``
    and Stamp copies the source's; the exporter writes that key as the
    Window/Door gml:id, so it must equal the fill object's ``gml_id``.
    """
    for material in mesh.materials:
        if material is None:
            continue
        for key in _OPENING_ID_KEYS:
            if key in material:
                material[key] = opening_gml_id


def _remint_stamp_material_identities(mesh, opening_gml_id: str, filling_parent: str) -> None:
    """Keep source appearance while avoiding exporter identity de-duplication.

    Visual material settings/nodes/textures are copied from the source slot.
    Every output polygon receives a distinct material copy only because the
    exporter uses ``gml_polygon_id`` as an identity key; reusing the source
    material object would cause stamped geometry to be omitted on export.
    """
    source_slots = list(mesh.materials)
    source_indices = [polygon.material_index for polygon in mesh.polygons]
    mesh.materials.clear()
    for polygon, source_index in zip(mesh.polygons, source_indices):
        original = source_slots[source_index] if source_index < len(source_slots) else None
        material = original.copy() if original is not None else bpy.data.materials.new("CGML_Stamped_Window")
        material.name = f"{material.name}_Stamp"
        material["con_surface_id"] = opening_gml_id
        material["gml_polygon_id"] = f"ID_{uuid.uuid4().hex}"
        if filling_parent:
            material["filling_parent_surface_id"] = filling_parent
        mesh.materials.append(material)
        polygon.material_index = len(mesh.materials) - 1


def _source_face_info(wall, face_index):
    """Selection-free analogue of Openings_Cutter.get_selected_face_info: read a
    host face's material, active UV map, outward normal, and a source UV mapping
    directly from object-mode mesh data for the given face index."""
    mesh = wall.data
    if not (0 <= face_index < len(mesh.polygons)):
        raise ValueError(f"face_index {face_index} out of range for {wall.name!r}")
    poly = mesh.polygons[face_index]

    mw = wall.matrix_world
    world_normal = (mw.to_3x3() @ poly.normal).normalized()

    material = face_material(mesh, poly)

    uv_layer = mesh.uv_layers.active
    uv_map_name = uv_layer.name if uv_layer is not None else ""

    world_points = [mw @ mesh.vertices[vi].co.copy() for vi in poly.vertices]
    origin = world_points[0]
    axis_u = (world_points[1] - world_points[0]).normalized()
    axis_v = world_normal.cross(axis_u).normalized()

    source_points_2d = []
    for wp in world_points:
        p2 = point_to_plane_basis(wp, origin, axis_u, axis_v)
        source_points_2d.append([p2.x, p2.y])

    if uv_layer is not None:
        source_uvs = [
            [uv_layer.data[li].uv.x, uv_layer.data[li].uv.y] for li in poly.loop_indices
        ]
    else:
        xs = [p[0] for p in source_points_2d]
        ys = [p[1] for p in source_points_2d]
        dx = max(max(xs) - min(xs), 1e-9)
        dy = max(max(ys) - min(ys), 1e-9)
        source_uvs = [[(p[0] - min(xs)) / dx, (p[1] - min(ys)) / dy] for p in source_points_2d]

    uv_mapping = {
        "origin": [origin.x, origin.y, origin.z],
        "axis_u": [axis_u.x, axis_u.y, axis_u.z],
        "axis_v": [axis_v.x, axis_v.y, axis_v.z],
        "source_points_2d": source_points_2d,
        "source_uvs": source_uvs,
    }
    return material, json.dumps(uv_mapping), uv_map_name, world_normal


def _rect_corners_world(surface: SurfaceContext, definition, placement) -> list:
    """Four world-space corners of the element rectangle, centred at the
    placement's fractional UV position on the surface frame."""
    h_axis = Vector(surface.horizontal_axis_world).normalized()
    v_axis = Vector(surface.vertical_axis_world).normalized()
    origin = Vector(surface.origin_world)

    u, v = placement.position_uv
    centre = origin + h_axis * (u * surface.width) + v_axis * (v * surface.height)

    if placement.size_override is not None:
        elem_w, elem_h = placement.size_override
    else:
        elem_w, elem_h = definition.width, definition.height
    half_w, half_h = elem_w / 2.0, elem_h / 2.0

    return [
        centre + h_axis * half_w + v_axis * half_h,
        centre - h_axis * half_w + v_axis * half_h,
        centre - h_axis * half_w - v_axis * half_h,
        centre + h_axis * half_w - v_axis * half_h,
    ]


# --------------------------------------------------------------------------
# Click/drag <-> surface-frame conversions (the inverse of _rect_corners_world
# above). Public: interactive operators (ops/facade_authoring.py's Draw
# click/drag) build PlacementRequest.position_uv/size_override from a mouse
# click through these - this is still surface-frame geometry, so it belongs
# beside its inverse in the kernel, not duplicated in a "thin adapter".
# --------------------------------------------------------------------------
def surface_uv_from_world_point(surface: SurfaceContext, world_point) -> tuple[float, float]:
    """A world point's fractional (u, v) position in the surface's own frame -
    what a single click (no drag) resolves to as ``PlacementRequest.position_uv``."""
    rel = Vector(world_point) - Vector(surface.origin_world)
    u = rel.dot(surface.horizontal_axis_world) / surface.width if surface.width > 1e-9 else 0.5
    v = rel.dot(surface.vertical_axis_world) / surface.height if surface.height > 1e-9 else 0.5
    return u, v


def surface_world_point_from_uv(surface: SurfaceContext, position_uv) -> Vector:
    """The inverse of ``surface_uv_from_world_point``: a fractional (u, v)
    position's world point in the surface's own frame. Used both by the
    placement kernel (target-frame matrix below) and by the interactive
    Semantic Stamp operator to resolve ``execute()``'s redo-panel/headless
    ``position_uv`` back to the world point ``place_semantic_element``'s
    stamp path requires."""
    u, v = position_uv
    return (
        Vector(surface.origin_world)
        + Vector(surface.horizontal_axis_world) * (float(u) * surface.width)
        + Vector(surface.vertical_axis_world) * (float(v) * surface.height)
    )


def surface_rect_from_drag(surface: SurfaceContext, world_start, world_end):
    """A click-drag's exact rectangle, in the surface's own frame: fractional
    (u, v) centre plus width/height in metres - what a drag resolves to as
    ``PlacementRequest.position_uv`` and ``FacadeElementDefinition.width/height``."""
    h_axis = surface.horizontal_axis_world
    v_axis = surface.vertical_axis_world
    origin = Vector(surface.origin_world)
    start = Vector(world_start) - origin
    end = Vector(world_end) - origin
    u1, v1 = start.dot(h_axis), start.dot(v_axis)
    u2, v2 = end.dot(h_axis), end.dot(v_axis)
    width = abs(u2 - u1)
    height = abs(v2 - v1)
    position_uv = (
        (u1 + u2) / 2.0 / surface.width if surface.width > 1e-9 else 0.5,
        (v1 + v2) / 2.0 / surface.height if surface.height > 1e-9 else 0.5,
    )
    return position_uv, width, height


def _classify_new_faces(wall, new_face_indices):
    """Return ``(exterior_fill_face_index, interior_ring_face_index)`` among the
    faces just added by create_joined_surfaces, told apart by their material's
    ``Interior`` flag (ring True, fill False)."""
    ext_idx = int_idx = None
    for fi in new_face_indices:
        poly = wall.data.polygons[fi]
        mat = face_material(wall.data, poly)
        if mat is None:
            continue
        if mat.get("Interior"):
            int_idx = fi
        else:
            ext_idx = fi
    return ext_idx, int_idx


# Newly authored Windows use a dedicated warm amber viewport-glass colour;
# Doors deliberately retain their independent semantic material policy (see
# ``_assign_authored_door_material`` below) instead of sharing this look.
_AUTHORED_WINDOW_GLASS_RGBA = (0.85, 0.55, 0.20, 0.65)
_AUTHORED_WINDOW_GLASS_ROUGHNESS = 0.15
_AUTHORED_WINDOW_GLASS_TRANSMISSION = 0.20
_AUTHORED_WINDOW_GLASS_IOR = 1.45
_AUTHORED_WINDOW_GLASS_EMISSION = 0.08


def _assign_authored_window_glass(fill) -> None:
    """Give one newly-authored Window a dedicated readable viewport glass.

    The source slot supplies CityGML polygon/surface identifiers only.  The
    actual Blender material is new and untextured, so a wall texture or its
    opaque shader cannot leak into the Window.  The low emission lift and
    disabled back-face culling keep the thin prism legible in Material Preview
    from frontal and oblique views.  Nothing imported calls this function.
    """
    mesh = fill.data
    source = mesh.materials[0] if mesh.materials else None
    glass = bpy.data.materials.new("CGML_Authored_Window_Glass")
    if source is not None:
        for key in source.keys():
            glass[key] = source[key]
    if mesh.materials:
        mesh.materials[0] = glass
    else:
        mesh.materials.append(glass)

    rgba = _AUTHORED_WINDOW_GLASS_RGBA
    glass.diffuse_color = rgba
    glass.use_nodes = True
    glass.use_backface_culling = False
    glass["x3d_diffuseColor_r"] = rgba[0]
    glass["x3d_diffuseColor_g"] = rgba[1]
    glass["x3d_diffuseColor_b"] = rgba[2]
    # The X3D appearance writer derives transparency from diffuse_color and
    # roughness/emission from the Principled node below.

    nodes = glass.node_tree.nodes
    links = glass.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Alpha"].default_value = rgba[3]
    bsdf.inputs["Roughness"].default_value = _AUTHORED_WINDOW_GLASS_ROUGHNESS
    bsdf.inputs["IOR"].default_value = _AUTHORED_WINDOW_GLASS_IOR
    _set_principled_input(bsdf, ("Transmission Weight", "Transmission"), _AUTHORED_WINDOW_GLASS_TRANSMISSION)
    _set_principled_input(bsdf, ("Emission Color", "Emission"), (rgba[0], rgba[1], rgba[2], 1.0))
    _set_principled_input(bsdf, ("Emission Strength",), _AUTHORED_WINDOW_GLASS_EMISSION)

    # Blender renamed this setting in 4.3.  Cover both APIs so Material
    # Preview displays alpha rather than treating the pane as opaque.
    if hasattr(glass, "surface_render_method"):
        glass.surface_render_method = 'DITHERED'
    elif hasattr(glass, "blend_method"):
        glass.blend_method = 'BLEND'
        if hasattr(glass, "shadow_method"):
            glass.shadow_method = 'HASHED'


_AUTHORED_DOOR_ROUGHNESS = 0.6


def _assign_authored_door_material(fill) -> None:
    """Give one newly-authored floating Door its independent semantic
    material policy (module docstring above): an opaque colour straight from
    ``SEMANTIC_PALETTE["Door"]`` - the same colour the importer's fallback
    appearance resolver uses - rather than a bespoke look like Window's
    glass. No transmission/backface tuning: a door is not translucent.
    """
    mesh = fill.data
    source = mesh.materials[0] if mesh.materials else None
    door_mat = bpy.data.materials.new("CGML_Authored_Door")
    if source is not None:
        for key in source.keys():
            door_mat[key] = source[key]
    if mesh.materials:
        mesh.materials[0] = door_mat
    else:
        mesh.materials.append(door_mat)

    rgba = SEMANTIC_PALETTE["Door"].color
    door_mat.diffuse_color = rgba
    door_mat.use_nodes = True
    door_mat["x3d_diffuseColor_r"] = rgba[0]
    door_mat["x3d_diffuseColor_g"] = rgba[1]
    door_mat["x3d_diffuseColor_b"] = rgba[2]

    nodes = door_mat.node_tree.nodes
    links = door_mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Alpha"].default_value = 1.0
    bsdf.inputs["Roughness"].default_value = _AUTHORED_DOOR_ROUGHNESS


def _set_principled_input(node, names, value) -> None:
    """Set a Principled socket across Blender's versioned socket names."""
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return


def _make_centered_prism(fill, world_normal, depth: float) -> None:
    """Replace a newly extracted opening face with a centred closed prism.

    ``create_joined_surfaces`` owns the planar wall-ring construction.  It is
    intentionally left untouched; this function operates only on the new
    Window mesh after extraction.  A non-positive explicit depth preserves the
    legacy planar authoring option.

    Raises ``ValueError`` if the base face is geometrically degenerate
    (export-error ticket 03, root-cause fix site 4 in
    ``docs/agent/export-error/opening-ring-degeneracy-findings.md``) rather
    than silently returning early - a bare ``len(base) < 3`` index-count
    check (the old behaviour) misses a face whose indices resolve to fewer
    than 3 *distinct* coordinates (Pattern A: 4 indices, 2 unique points),
    which then either silently stayed flat or got extruded into a permanent
    degenerate 6-face solid. Both Draw Window authoring
    (``_place_interior_ring_opening`` / ``_place_exterior_notch_opening``,
    caught by their operator's existing ``except (ValueError, ...)`` in
    ``ops/facade_authoring.py``) and import-time synthetic depth
    (``apply_synthetic_opening_depth``, uncaught - propagates as a loud
    import failure) share this one check.
    """
    if depth <= 0.0 or not fill.data.polygons:
        return

    mesh = fill.data
    face = mesh.polygons[0]
    base = [mesh.vertices[vi].co.copy() for vi in face.vertices]

    mw = fill.matrix_world
    base_world = [tuple(mw @ v) for v in base]
    result = validate_ring(base_world)
    if not result.valid:
        raise ValueError(
            f"'{fill.name}' has a degenerate opening face ({describe_ring_invalid(result)}) - "
            f"refusing to extrude a {depth * 100:.0f} cm prism from it"
        )

    # The base face's own vertex loop can still carry a redundant closing
    # vertex (Pattern B - valid once collapsed, per ticket 01/02) even though
    # ``result`` above confirms the ring itself is geometrically fine.
    # Extruding the raw, undeduped loop as-is builds one zero-length side
    # face for that phantom closing edge - found via export-error ticket 04's
    # shared-serializer safety net on a real LoD3 fixture (an imported door
    # whose source posList repeated its first point). Rebuild ``base`` from
    # the same consecutive-duplicate collapse so the prism's face count
    # matches the ring's actual distinct-point count.
    deduped_local = dedupe_ring_points([tuple(p) for p in base])
    if len(deduped_local) != len(base):
        base = [Vector(pt) for pt in deduped_local]

    # Mesh coordinates are local to the fill object; normals supplied by the
    # placement context are world-space directions.
    local_normal = (fill.matrix_world.inverted().to_3x3() @ Vector(world_normal)).normalized()
    half = float(depth) / 2.0
    outward = [p + local_normal * half for p in base]
    inward = [p - local_normal * half for p in base]
    count = len(base)

    # The extracted face already has the intended exterior winding.  Its
    # opposite cap reverses that winding; each side connects matching edges.
    faces = [list(range(count)), list(reversed(range(count, 2 * count)))]
    faces.extend(
        [i, (i + 1) % count, count + (i + 1) % count, count + i]
        for i in range(count)
    )
    mesh.clear_geometry()
    mesh.from_pydata(outward + inward, [], faces)
    # The material-backed exporter de-duplicates polygons by
    # ``gml_polygon_id``.  A prism's six faces must consequently have distinct
    # polygon identities while retaining the same Window surface identity and
    # appearance.  This affects only this freshly authored mesh.
    base_material = mesh.materials[0] if mesh.materials else None
    for index, poly in enumerate(mesh.polygons):
        if index == 0 or base_material is None:
            poly.material_index = 0
            continue
        material = base_material.copy()
        material.name = f"{base_material.name}_Prism_{index}"
        material["gml_polygon_id"] = f"ID_{uuid.uuid4().hex}"
        mesh.materials.append(material)
        poly.material_index = len(mesh.materials) - 1
    mesh.update()


# Marks an opening object that received a fabricated depth rather than one
# encoded by its source file (window-door-upload ticket 02). Load-bearing for
# export: without this marker, a flat-turned-solid opening round-trips through
# the exporter indistinguishably from a real volumetric source opening.
SYNTHETIC_OPENING_DEPTH_KEY = "cgml_synthetic_opening_depth"


def apply_synthetic_opening_depth(fill, world_normal, semantic_type: str) -> bool:
    """Give one imported, still-flat Window/Door opening the same 4 cm
    centred-prism depth authored openings get (window-door-upload ticket 02).

    ``fill`` must carry exactly one polygon - the shape a CityGML LoD3
    opening almost always arrives in. Whether the source already encodes
    real volumetric depth is never checked (see the plan's Out of Scope);
    but an opening that already owns more than one face (e.g. its own
    interior/frame ring) is left untouched - reusing ``_make_centered_prism``
    on it would silently discard those extra faces, which is not "flat" in
    the sense this function assumes. Returns whether the depth was applied.
    """
    mesh = fill.data
    if mesh is None or len(mesh.polygons) != 1:
        return False
    # Glass must be assigned before prism-ification, matching the authored
    # (Draw Window) call order: _make_centered_prism copies whatever sits in
    # materials[0] onto the prism's other 5 faces, so assigning glass first
    # is what makes every face of the solid come out amber instead of just
    # the original face, leaving the other 5 the old source colour.
    if semantic_type.strip().lower() in ("window", "windowsurface"):
        _assign_authored_window_glass(fill)
    _make_centered_prism(fill, world_normal, DEFAULT_WINDOW_DEPTH)
    fill[SYNTHETIC_OPENING_DEPTH_KEY] = True
    _mark_prism_as_multisurface(mesh)
    return True


def _host_ring_plane(fill):
    """``(point, normal)`` of the wall interior ring paired with ``fill`` (its opening link),
    in world space, or None (no link: a notch door, an imported one-sided opening)."""
    link = str(fill.get(OPENING_LINK_KEY) or "")
    wall = fill.parent
    if not link or wall is None or wall.type != 'MESH':
        return None
    from .ring_opening_sync import _loop_normal
    for _idx, verts, ring_link in wall_interior_ring_faces(wall):
        if ring_link == link and len(verts) >= 3:
            pts = [Vector(v) for v in verts]
            n = _loop_normal(pts)
            return (sum(pts, Vector()) / len(pts), n) if n.length > 0 else None
    return None


def _mark_display_depth(fill, depth) -> None:
    """B49: an authored opening's prism is viewport-only; the export writes the one polygon
    in the wall plane (``flat_synthetic_openings``), as CityGML models a filling surface."""
    if depth and float(depth) > 0.0 and len(fill.data.polygons) > 1:
        fill[SYNTHETIC_OPENING_DEPTH_KEY] = float(depth)


@contextmanager
def flat_synthetic_openings(objects):
    """Export openings whose depth is only synthetic or display-only (``SYNTHETIC_OPENING_DEPTH_KEY``:
    True = the import's 4 cm, a number = the authored depth, B49) as the flat polygon in the wall
    plane: during the block each one's mesh is a one-face copy (the outward cap moved back by half
    the depth); the prism is restored afterwards. Without this an unedited CityGML 3 Door came back
    with 5 invented faces, 2 cm off its plane."""
    swapped = []
    try:
        for obj in objects:
            depth = obj.get(SYNTHETIC_OPENING_DEPTH_KEY)
            if obj.type != 'MESH' or not depth or obj.data is None or len(obj.data.polygons) < 2:
                continue
            depth = float(depth) if isinstance(depth, (int, float)) and not isinstance(depth, bool) else DEFAULT_WINDOW_DEPTH
            flat = obj.data.copy()
            bm = bmesh.new()
            bm.from_mesh(flat)
            bm.faces.ensure_lookup_table()
            cap = bm.faces[0]  # _make_centered_prism: the outward cap, source winding
            # onto the prism's mid-plane (the rule ring_opening_sync._reference_cap applies): the
            # opposite cap is the face of the same size whose offset runs along the cap normal
            shift = cap.normal * (depth / 2.0)
            centre = cap.calc_center_median()
            for other in bm.faces:
                offset = other.calc_center_median() - centre
                if (other is not cap and len(other.verts) == len(cap.verts)
                        and abs(other.calc_area() - cap.calc_area()) < 1e-6 * max(1.0, cap.calc_area())
                        and offset.length > 1e-9 and offset.cross(cap.normal).length < 1e-6):
                    shift = -offset / 2.0
                    break
            keep = set(cap.verts)
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if f is not cap], context='FACES_ONLY')
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if v not in keep], context='VERTS')
            ring = _host_ring_plane(obj)
            if ring is not None:  # the hole's own plane wins (an edited prism need not be centred)
                mw, inv = obj.matrix_world, obj.matrix_world.inverted()
                pt, n = ring
                for v in keep:
                    w = mw @ v.co
                    v.co = inv @ (w - n * (w - pt).dot(n))
            else:
                for v in keep:
                    v.co -= shift
            bm.to_mesh(flat)
            bm.free()
            swapped.append((obj, obj.data))
            obj.data = flat
        yield
    finally:
        for obj, data in swapped:
            flat = obj.data
            obj.data = data
            bpy.data.meshes.remove(flat)


def _mark_prism_as_multisurface(mesh) -> None:
    """A freshly prism-ified opening (``_make_centered_prism``) always ends up
    with more than one face sharing one ``con_surface_id``. The exporter
    (``io/writer/exporter.py``) only groups such faces into a single
    Window/DoorSurface's ``gml:MultiSurface`` when every one of them carries
    ``is_multisurface_member = True`` and the same ``gml_multisurface_id`` -
    a genuinely single-polygon imported opening's material never needed that
    flag, so it must be set here now that the prism gave it siblings.
    Otherwise each face is exported as its own same-gml:id-colliding
    Window/DoorSurface, disambiguated with a ``_2``/``_3``/... suffix.

    Each of the prism's extra faces is also stamped with its own fresh
    ``gml_ring_id`` (``_make_centered_prism`` mints a distinct
    ``gml_polygon_id`` per face but leaves the copied ``gml_ring_id``
    identical across all of them) - a duplicate ring id across sibling
    polygons makes the exporter's own dedup guard discard and regenerate
    every one of them, which breaks id-preservation on round-trip.
    """
    if not mesh.materials:
        return
    ms_id = mesh.materials[0].get("gml_multisurface_id") or f"ID_{uuid.uuid4().hex}"
    for mat in mesh.materials:
        mat["gml_multisurface_id"] = ms_id
        mat["is_multisurface_member"] = True
    for mat in mesh.materials[1:]:
        mat["gml_ring_id"] = f"{mat.get('gml_polygon_id')}_0_"


def _extract_single_face_object(source_obj, face_index, name):
    """A new, unlinked object holding only ``face_index`` of ``source_obj``'s
    mesh - its own mesh-data block (copy, never a linked duplicate), with the
    material slots compacted to just the ones the kept face uses (ADR 0004 D5).
    Mirrors ``io/reader/semantic_explode._extract_face_subset`` for one face."""
    new_mesh = source_obj.data.copy()
    new_mesh.name = name

    bm = bmesh.new()
    bm.from_mesh(new_mesh)
    faces = list(bm.faces)
    to_delete = [f for i, f in enumerate(faces) if i != face_index]
    bmesh.ops.delete(bm, geom=to_delete, context="FACES")
    bm.to_mesh(new_mesh)
    bm.free()
    new_mesh.update()

    _compact_material_slots(new_mesh)

    new_obj = bpy.data.objects.new(name, new_mesh)
    new_obj.matrix_world = source_obj.matrix_world.copy()
    return new_obj


def _delete_wall_face(wall, face_index):
    """Remove one face (the moved-out fill) from the wall mesh; the interior
    ring and every original face stay."""
    mesh = wall.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    faces = list(bm.faces)
    if 0 <= face_index < len(faces):
        bmesh.ops.delete(bm, geom=[faces[face_index]], context="FACES")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def _compact_material_slots(mesh):
    """Drop material slots the (face-pruned) mesh no longer uses, remapping
    ``polygon.material_index``. Same rule as semantic_explode: export reads
    per-polygon ids off the material a face's index points at, so the remap must
    be exact."""
    original_indices = [p.material_index for p in mesh.polygons]
    used = sorted(set(original_indices))
    if not used:
        return
    remap = {old: new for new, old in enumerate(used)}
    kept = [mesh.materials[i] for i in used]
    mesh.materials.clear()
    for m in kept:
        mesh.materials.append(m)
    for p, orig_idx in zip(mesh.polygons, original_indices):
        p.material_index = remap[orig_idx]


def _fill_object_name(opening_gml_id, schema_version, canonical_type="Window"):
    base = opening_gml_id or uuid.uuid4().hex
    return f"{canonical_type}Surface_{base}"[:63]
