# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Surface Context resolver + inline explode (facade-authoring ticket 02).

Turns a user-selected face into the ``SurfaceContext`` the headless placement
kernel (ticket 01, ``shared/facade_placement.py``) consumes: outward normal,
local horizontal/vertical facade axes, origin, surface type/id, owning
building, and dimensions.

The frame math (``compute_facade_frame`` and its helpers below) is
**pure Python** - plain ``(x, y, z)`` tuples in, a plain ``FacadeFrame`` out,
no ``bpy``/``mathutils`` dependency at all - so it is unit-testable without a
running Blender and, per ADR 0004 D10, reusable by a future deep-learning
client exactly like ``place_semantic_element`` itself. Only the thin
``resolve_surface_context``/split wrappers at the bottom of this module touch
live mesh data, and they import ``bpy``/``mathutils`` lazily so importing this
module for the pure math never requires them.

**Outward orientation follows the surface's own winding, not a transformed
"face normal".** The normal is recomputed from the world-space vertex loop
order via Newell's method (the same technique ``Openings_Cutter.polygon_normal``
and the old Cut Openings display path (removed in B17)'s ``_newell`` use) rather than trusting
``mesh.polygons[i].normal`` transformed by the object's 3x3 matrix - the
latter silently flips outward for objects with mirrored/negative-scale
transforms, while recomputing from the stored winding order matches exactly
what the exporter treats as the ring's orientation.

**Merged (un-exploded) buildings (D6).** ``SurfaceContext.object_name`` must
be an already-exploded boundary-surface object (a WallSurface/RoofSurface/...
in its own right - ``shared/semantic_tree.is_boundary_surface``), matching
the design's "Surface Context = selected surface object + picked point"
invariant. If the active object is instead a merged Building/BuildingPart
whose face still only carries surface identity on its *material*
(``SurfaceTyp``/``con_surface_id`` - exactly what
``io/reader/semantic_explode.py`` groups by), ``resolve_surface_context_for_face``
offers a one-click inline split (``split_into_semantic_surfaces``, reusing the
importer's own ``explode_building_into_semantic_objects``) and then resolves
the equivalent face on the new surface object - never a hard error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .object_helpers import face_material
from .semantic_tree import BOUNDARY_SURFACE_TYPES, is_boundary_surface

Point3 = tuple  # (x, y, z) of float


# --------------------------------------------------------------------------
# Pure vector helpers (plain tuples - no mathutils dependency).
# --------------------------------------------------------------------------
def _vsub(a: Point3, b: Point3) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vadd(a: Point3, b: Point3) -> Point3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vscale(a: Point3, s: float) -> Point3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _vdot(a: Point3, b: Point3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vcross(a: Point3, b: Point3) -> Point3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vlen(a: Point3) -> float:
    return math.sqrt(_vdot(a, a))


def _vnormalize(a: Point3) -> Point3:
    length = _vlen(a)
    if length < 1e-12:
        raise ValueError("cannot normalize a zero-length vector")
    return _vscale(a, 1.0 / length)


# --------------------------------------------------------------------------
# Frame math (pure).
# --------------------------------------------------------------------------
_GLOBAL_Z: Point3 = (0.0, 0.0, 1.0)
# Below this projected-length, global Z is treated as parallel to the surface
# normal (flat roof/floor) and the longest-edge fallback (design section 7) is
# used for the horizontal axis instead.
_Z_PARALLEL_EPS = 1e-6
_PLANARITY_ABS_TOL = 1e-4
_PLANARITY_REL_TOL = 1e-3
_DEDUP_TOL = 1e-9


@dataclass(frozen=True)
class FacadeFrame:
    """Pure-tuple result of ``compute_facade_frame`` - the local facade frame
    for one planar polygon loop, before any ``mathutils``/``bpy`` conversion."""

    origin: Point3
    normal: Point3
    horizontal_axis: Point3
    vertical_axis: Point3
    width: float
    height: float


def _dedup_consecutive(points: list[Point3], tol: float = _DEDUP_TOL) -> list[Point3]:
    """Drop consecutive (and wrap-around) duplicate points, mirroring
    ``Openings_Cutter.cleaned_polygon_points`` for plain tuples."""
    cleaned: list[Point3] = []
    for p in points:
        if cleaned and _vlen(_vsub(cleaned[-1], p)) <= tol:
            continue
        cleaned.append(p)
    if len(cleaned) >= 2 and _vlen(_vsub(cleaned[0], cleaned[-1])) <= tol:
        cleaned.pop()
    return cleaned


def _newell_normal(points: list[Point3]) -> Point3:
    """Newell's-method normal of a polygon loop - its direction follows the
    loop's own (right-hand) winding order, exactly what the exporter treats as
    the ring's orientation. Unnormalized; length is twice the loop's area."""
    n = (0.0, 0.0, 0.0)
    count = len(points)
    for i in range(count):
        p0 = points[i]
        p1 = points[(i + 1) % count]
        n = (
            n[0] + (p0[1] - p1[1]) * (p0[2] + p1[2]),
            n[1] + (p0[2] - p1[2]) * (p0[0] + p1[0]),
            n[2] + (p0[0] - p1[0]) * (p0[1] + p1[1]),
        )
    return n


def _bbox_diagonal(points: list[Point3]) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return _vlen((max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))


def _check_planar(points: list[Point3], normal: Point3) -> None:
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    cz = sum(p[2] for p in points) / len(points)
    centroid = (cx, cy, cz)
    tol = max(_PLANARITY_ABS_TOL, _PLANARITY_REL_TOL * _bbox_diagonal(points))
    for p in points:
        deviation = abs(_vdot(_vsub(p, centroid), normal))
        if deviation > tol:
            raise ValueError(
                f"non-planar face: a vertex deviates {deviation:.6g}m from the "
                f"best-fit plane (tolerance {tol:.6g}m) - reject and report"
            )


def _longest_edge(points: list[Point3]) -> Point3:
    count = len(points)
    best_edge = _vsub(points[1 % count], points[0])
    best_len = _vlen(best_edge)
    for i in range(1, count):
        edge = _vsub(points[(i + 1) % count], points[i])
        length = _vlen(edge)
        if length > best_len:
            best_len = length
            best_edge = edge
    return best_edge


def _facade_axes(points: list[Point3], normal: Point3) -> tuple[Point3, Point3]:
    """Local horizontal (H) / vertical (V) facade axes (design doc section 7):

        V = normalize(globalZ - (globalZ.N) N)
        H = normalize(V x N)
        V = normalize(N x H)          # re-orthogonalize

    degrading to the surface's longest edge for H when the normal is parallel
    to global Z (flat roof/floor - the projection above is ~zero-length)."""
    raw_v = _vsub(_GLOBAL_Z, _vscale(normal, _vdot(_GLOBAL_Z, normal)))
    if _vlen(raw_v) < _Z_PARALLEL_EPS:
        h = _vnormalize(_longest_edge(points))
        v = _vnormalize(_vcross(normal, h))
        h = _vnormalize(_vcross(v, normal))
        return h, v
    v = _vnormalize(raw_v)
    h = _vnormalize(_vcross(v, normal))
    v = _vnormalize(_vcross(normal, h))
    return h, v


def compute_facade_frame(world_points: list[Point3]) -> FacadeFrame:
    """Resolve one planar polygon loop (world-space, in the mesh's own stored
    winding order) into a :class:`FacadeFrame`. Raises ``ValueError`` with a
    clear message for a degenerate (fewer than 3 distinct vertices, zero-area)
    or non-planar face."""
    points = _dedup_consecutive([tuple(p) for p in world_points])
    if len(points) < 3:
        raise ValueError("degenerate face: fewer than 3 distinct vertices")

    raw_normal = _newell_normal(points)
    normal_length = _vlen(raw_normal)
    if normal_length < 1e-9:
        raise ValueError("degenerate face: zero area / collinear vertices")
    normal = _vscale(raw_normal, 1.0 / normal_length)

    _check_planar(points, normal)

    h, v = _facade_axes(points, normal)

    v0 = points[0]
    us = [_vdot(_vsub(p, v0), h) for p in points]
    vs = [_vdot(_vsub(p, v0), v) for p in points]
    min_u, max_u = min(us), max(us)
    min_v, max_v = min(vs), max(vs)
    origin = _vadd(v0, _vadd(_vscale(h, min_u), _vscale(v, min_v)))

    return FacadeFrame(
        origin=origin,
        normal=normal,
        horizontal_axis=h,
        vertical_axis=v,
        width=max_u - min_u,
        height=max_v - min_v,
    )


# --------------------------------------------------------------------------
# Merged-building detection (pure - duck-typed on obj/mesh/material, no bpy).
# --------------------------------------------------------------------------
def surface_needs_split(obj, face_index: int) -> bool:
    """True when ``obj`` is a merged (un-exploded) Building/BuildingPart whose
    ``face_index`` still carries recognizable boundary-surface identity only
    on its *material* (``SurfaceTyp``/``surface_type`` - what
    ``io/reader/semantic_explode.py`` groups faces by). False both when
    ``obj`` is already an exploded surface object (nothing to split) and when
    the face carries no recognizable surface identity at all (splitting
    would not help - resolution should reject with a clear message instead)."""
    if obj is None or getattr(obj, "type", None) != "MESH":
        return False
    if is_boundary_surface(obj):
        return False
    mesh = getattr(obj, "data", None)
    polygons = getattr(mesh, "polygons", None) if mesh is not None else None
    if not polygons or not (0 <= face_index < len(polygons)):
        return False
    poly = polygons[face_index]
    mat = face_material(mesh, poly)
    if mat is None:
        return False
    surface_type = str(mat.get("SurfaceTyp") or mat.get("surface_type") or "").strip().lower()
    return surface_type in BOUNDARY_SURFACE_TYPES


# --------------------------------------------------------------------------
# Live-mesh resolver (bpy/mathutils imported lazily - only needed here).
# --------------------------------------------------------------------------
def resolve_surface_context(obj, face_index: int):
    """Resolve one face of an already-exploded boundary-surface object
    (a WallSurface/RoofSurface/... object in its own right - D6) into a
    ``shared.facade_placement.SurfaceContext``. Raises ``ValueError`` with a
    clear message if ``obj`` is not such an object, ``face_index`` is out of
    range, or the face is non-planar/degenerate."""
    from mathutils import Vector

    from .facade_placement import SurfaceContext

    if obj is None or getattr(obj, "type", None) != "MESH":
        raise ValueError("resolve_surface_context requires a MESH object")
    if not is_boundary_surface(obj):
        feature = str(obj.get("cgml3_feature") or obj.type)
        raise ValueError(
            f"'{obj.name}' ({feature}) is not an exploded boundary surface "
            "(WallSurface/RoofSurface/...) - split the building into surfaces "
            "first (surface_needs_split / split_into_semantic_surfaces)"
        )

    mesh = obj.data
    polygons = mesh.polygons if mesh is not None else []
    if not (0 <= face_index < len(polygons)):
        raise ValueError(f"face_index {face_index} out of range for '{obj.name}'")

    poly = polygons[face_index]
    mw = obj.matrix_world
    world_points = [tuple(mw @ mesh.vertices[vi].co) for vi in poly.vertices]

    frame = compute_facade_frame(world_points)

    mat = face_material(mesh, poly)
    semantic_id = str(
        obj.get("gml_id") or (mat.get("con_surface_id") if mat is not None else "") or ""
    ).strip()
    semantic_type = str(obj.get("cgml3_feature") or "").strip()

    building = obj.parent
    while building is not None and str(building.get("cgml3_feature") or "") not in (
        "Building",
        "BuildingPart",
    ):
        building = building.parent
    building_id = str(building.get("gml_id") or "").strip() if building is not None else ""

    return SurfaceContext(
        object_name=obj.name,
        semantic_id=semantic_id,
        semantic_type=semantic_type,
        building_id=building_id,
        face_indices=[face_index],
        origin_world=Vector(frame.origin),
        normal_world=Vector(frame.normal),
        horizontal_axis_world=Vector(frame.horizontal_axis),
        vertical_axis_world=Vector(frame.vertical_axis),
        width=frame.width,
        height=frame.height,
    )


def _object_collection(obj):
    import bpy

    return obj.users_collection[0] if obj.users_collection else bpy.context.scene.collection


def split_into_semantic_surfaces(obj) -> bool:
    """Inline one-click split (D6): explode a merged Building/BuildingPart
    mesh into per-surface WallSurface/RoofSurface/... objects in place,
    reusing the importer's own ``explode_building_into_semantic_objects`` -
    the same step ``use_semantic_objects`` runs automatically at import time.
    Returns True if anything was exploded (mirroring that function).

    The explode step rebuilds the mesh with a plain (non-edit) ``bmesh``, which
    Blender refuses while ``obj`` is being edited (D9's whole entry gesture is
    Edit-Mode face-select) - so this drops to Object Mode first if needed.
    There is no meaningful "resume Edit Mode on obj" afterward: the selected
    face just moved into a brand-new object."""
    import bpy

    from ..common.builder_support.semantic_explode import explode_building_into_semantic_objects
    from ..Openings_Cutter.Openings_Cutter import detect_citygml_version_for_object

    if obj is None or getattr(obj, "type", None) != "MESH":
        raise ValueError("split_into_semantic_surfaces requires a MESH object")

    if obj.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    collection = _object_collection(obj)
    detected_version = detect_citygml_version_for_object(obj)
    schema_version = str(obj.get("cgml_schema_version") or ("2.0" if detected_version == "2.0" else "3.0"))
    return explode_building_into_semantic_objects(obj, collection, schema_version=schema_version)


def _world_face_center(obj, face_index: int):
    mesh = obj.data
    polygons = mesh.polygons if mesh is not None else []
    if not (0 <= face_index < len(polygons)):
        raise ValueError(f"face_index {face_index} out of range for '{obj.name}'")
    return obj.matrix_world @ polygons[face_index].center


def _find_face_by_world_centroid(candidates, target_point, tol: float = 1e-4):
    """Among ``candidates`` (new surface objects created by
    ``split_into_semantic_surfaces``), find the face whose world-space
    centroid matches ``target_point`` - the originally selected face,
    captured before the split moved it into a new object/mesh. Faces are
    moved (not rebuilt) by the explode step, so the match is exact up to
    floating point, never a heuristic nearest-neighbour guess."""
    best = None
    best_dist = tol
    for cand in candidates:
        mesh = getattr(cand, "data", None)
        if mesh is None:
            continue
        mw = cand.matrix_world
        for poly in mesh.polygons:
            center = mw @ poly.center
            dist = (center - target_point).length
            if dist < best_dist:
                best_dist = dist
                best = (cand, poly.index)
    return best


def resolve_surface_context_for_face(obj, face_index: int):
    """One-call entry point (D6): if ``obj`` is a merged Building/BuildingPart,
    split it into per-surface objects first and resolve the equivalent face on
    the new surface object; otherwise resolve directly. Raises ``ValueError``
    with a clear message if the face cannot be resolved even after splitting
    (no recognizable surface identity at all)."""
    if is_boundary_surface(obj):
        return resolve_surface_context(obj, face_index)

    if not surface_needs_split(obj, face_index):
        raise ValueError(
            f"'{obj.name}' face {face_index} does not belong to a recognized "
            "boundary surface (WallSurface/RoofSurface/...) - select a face "
            "on an imported CityGML building"
        )

    target_point = _world_face_center(obj, face_index)
    split_into_semantic_surfaces(obj)

    candidates = [child for child in obj.children if is_boundary_surface(child)]
    found = _find_face_by_world_centroid(candidates, target_point)
    if found is None:
        raise ValueError(
            f"split '{obj.name}' into surfaces, but could not relocate the "
            "originally selected face - select a face on the new surface "
            "object and resolve again"
        )
    new_obj, new_face_index = found
    return resolve_surface_context(new_obj, new_face_index)
