# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/view_landing.py
"""Bring freshly imported data into view.

``land_on_objects`` is called after every import (CityGML 2/3, streaming,
DB): it widens the viewport clip range to the data extent, frames the new
objects in every 3D view and makes the import's root collection active.
The clip table is pure Python so it can be unit-tested without Blender.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence, Tuple

CLIP_END_MAX = 1e7


def clip_start_for_extent(extent_m: float) -> float:
    if extent_m < 1_000.0:
        return 0.1
    if extent_m < 10_000.0:
        return 1.0
    return 10.0


def clip_end_for_extent(extent_m: float) -> float:
    return min(5.0 * max(float(extent_m), 0.0), CLIP_END_MAX)


def grown_clip_range(current_start: float, current_end: float, extent_m: float) -> Tuple[float, float]:
    """Target clip range for ``extent_m``; values only ever grow."""
    start = max(float(current_start), clip_start_for_extent(extent_m))
    end = max(float(current_end), clip_end_for_extent(extent_m))
    return start, end


def bounds_extent(mins: Sequence[float], maxs: Sequence[float]) -> float:
    return max(float(maxs[i]) - float(mins[i]) for i in range(3))


_GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "POINTCLOUD", "VOLUME", "CURVES"}


def has_geometry(obj) -> bool:
    """True for objects with drawable geometry.

    Imported feature roots are often empty meshes whose bound box collapses
    to their origin; they must not pull bounds or framing towards (0, 0, 0).
    """
    try:
        if obj.type not in _GEOMETRY_TYPES:
            return False
        if obj.type == "MESH":
            return len(obj.data.vertices) > 0
        if obj.type == "POINTCLOUD":
            return len(obj.data.points) > 0
        return True
    except (ReferenceError, AttributeError):
        return False


def world_bounds(objects: Iterable) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """World-space bounds of objects' bound boxes, or None."""
    from mathutils import Vector

    mins = [math.inf] * 3
    maxs = [-math.inf] * 3
    found = False
    for obj in objects:
        if not has_geometry(obj):
            continue
        try:
            mw = obj.matrix_world
            corners = obj.bound_box
        except ReferenceError:
            continue
        for c in corners:
            w = mw @ Vector(c)
            for k in range(3):
                if w[k] < mins[k]:
                    mins[k] = w[k]
                if w[k] > maxs[k]:
                    maxs[k] = w[k]
            found = True
    if not found:
        return None
    return tuple(mins), tuple(maxs)


def _iter_view3d_spaces():
    import bpy

    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    yield space


def _apply_clip(extent_m: float) -> None:
    for space in _iter_view3d_spaces():
        start, end = grown_clip_range(space.clip_start, space.clip_end, extent_m)
        space.clip_start = start
        space.clip_end = end


def _find_layer_collection(layer_collection, collection):
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection)
        if found is not None:
            return found
    return None


def _activate_collection(context, collection) -> None:
    if collection is None:
        return
    view_layer = context.view_layer
    layer_coll = _find_layer_collection(view_layer.layer_collection, collection)
    if layer_coll is not None:
        view_layer.active_layer_collection = layer_coll


def _frame_objects(context, objects) -> int:
    """Run view3d.view_selected on the given objects in every 3D view.

    Restores the previous selection and active object. Returns the number of
    views that were framed.
    """
    import bpy

    view_layer = context.view_layer
    prev_selected = [o for o in view_layer.objects if o.select_get()]
    prev_active = view_layer.objects.active

    framed = 0
    try:
        for o in prev_selected:
            o.select_set(False)
        selectable = 0
        for o in objects:
            try:
                if o.name in view_layer.objects and o.visible_get(view_layer=view_layer):
                    o.select_set(True)
                    selectable += 1
            except (ReferenceError, RuntimeError):
                continue
        if not selectable:
            return 0

        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                framed += 1
    finally:
        for o in view_layer.objects:
            if o.select_get():
                o.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except (ReferenceError, RuntimeError):
                pass
        view_layer.objects.active = prev_active
    return framed


def land_on_objects(context, objects, *, frame: bool = True, root_collection=None) -> dict:
    """Make imported ``objects`` visible: clip range, framing, active collection.

    Returns a dict with ``bounds``, ``extent`` and ``framed`` (view count).
    Framing is skipped in background mode.
    """
    import bpy

    objects = [o for o in objects if has_geometry(o)]
    info = {"bounds": None, "extent": 0.0, "framed": 0}
    bounds = world_bounds(objects)
    if bounds is None:
        return info
    extent = bounds_extent(*bounds)
    info["bounds"] = bounds
    info["extent"] = extent

    _apply_clip(extent)
    try:
        _activate_collection(context, root_collection)
    except Exception as exc:
        print(f"[CityGML Import] Could not activate collection: {exc}")

    if frame and not bpy.app.background:
        try:
            info["framed"] = _frame_objects(context, objects)
        except Exception as exc:
            print(f"[CityGML Import] Framing the view failed: {exc}")
    return info
