# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# common/import_landing.py
"""
Shared end of every import (file, streaming, DB): find what was added,
land the view on it and build the one-line report.
"""

from __future__ import annotations

import time
import traceback

import bpy

from ..shared.import_origin import FAR_FROM_ORIGIN_M, is_far_from_origin
from ..shared.import_summary import ImportSummary, count_root_features, format_summary
from ..shared.view_landing import land_on_objects


class ImportSnapshot:
    """Remembers which objects existed before an import."""

    def __init__(self):
        self.object_ptrs = {o.as_pointer() for o in bpy.data.objects}
        self.t0 = time.perf_counter()

    def new_objects(self):
        return [o for o in bpy.data.objects if o.as_pointer() not in self.object_ptrs]

    @property
    def seconds(self) -> float:
        return time.perf_counter() - self.t0


def _root_collection(scene, new_objects):
    """Top-level scene collection that holds the imported objects."""
    if not new_objects:
        return None
    ptrs = {o.as_pointer() for o in new_objects}
    for coll in scene.collection.children:
        for o in coll.all_objects:
            if o.as_pointer() in ptrs:
                return coll
    return None


def _world_origin_and_crs(scene):
    from ..shared import georef as gr

    world = gr.world()  # not scene.world: the georeference lives only in "World"
    if world is None:
        return None, ""
    try:
        origin = None
        if gr.X_ORIGIN in world and gr.Y_ORIGIN in world:
            origin = (float(world[gr.X_ORIGIN]), float(world[gr.Y_ORIGIN]), float(world.get(gr.Z_ORIGIN, 0.0)))
        crs = str(world.get(gr.CRS, "") or "")
    except Exception:
        return None, ""
    if crs == "Unknown CRS":
        crs = ""
    return origin, crs


def _activate_first_feature(context, new_objects) -> None:
    """B13: the first imported feature becomes active, so "2 · Edit" shows its tools at once.

    Only the active object changes; the selection stays as it was, because
    the export scope is the selection.
    """
    layer_objects = context.view_layer.objects
    for o in new_objects:
        try:
            parent = o.parent
            if o.get("cgml3_feature") and not (parent is not None and parent.get("cgml3_feature")) \
                    and o.name in layer_objects:
                layer_objects.active = o
                return
        except ReferenceError:
            continue


def finish_import(context, snapshot: ImportSnapshot, *, frame: bool = True, warnings=()):
    """Land on the imported objects and return ``(level, message, summary)``.

    ``level`` is ``'INFO'`` or ``'WARNING'`` (no features, or warnings).
    """
    new_objects = snapshot.new_objects()
    summary = ImportSummary(seconds=snapshot.seconds, warnings=list(warnings))
    summary.feature_counts = count_root_features(new_objects)
    summary.origin, summary.crs = _world_origin_and_crs(context.scene)

    landing = land_on_objects(
        context,
        new_objects,
        frame=frame,
        root_collection=_root_collection(context.scene, new_objects),
    )
    bounds = landing.get("bounds")
    if bounds is not None:
        cx = (bounds[0][0] + bounds[1][0]) / 2.0
        cy = (bounds[0][1] + bounds[1][1]) / 2.0
        if is_far_from_origin((cx, cy)):
            msg = (
                f"Imported data lies {((cx * cx + cy * cy) ** 0.5) / 1000.0:.0f} km from the scene origin "
                f"(> {FAR_FROM_ORIGIN_M / 1000.0:.0f} km); existing objects were not moved"
            )
            print(f"[CityGML Import] WARNING: {msg}")
            summary.warnings.append(msg)
    _activate_first_feature(context, new_objects)

    message = format_summary(summary)
    level = "WARNING" if (summary.feature_total == 0 or summary.warnings) else "INFO"
    print(f"[CityGML Import] {message}")
    return level, message, summary


def report_import_error(operator, exc: BaseException, log_prefix: str = "citygml_import_error") -> None:
    """One-line ERROR report; the traceback goes to a Text block."""
    from ..shared.export_helpers import log_text_to_blender
    from ..shared.georef import CrsMismatch

    if isinstance(exc, CrsMismatch):  # a user decision, not a bug: no traceback
        operator.report({'ERROR'}, str(exc))
        return
    tb = traceback.format_exc()
    print(tb)
    try:
        log_text_to_blender(log_prefix, tb)
    except Exception:
        pass
    try:
        # The importers open a progress bar that an exception leaves running.
        bpy.context.window_manager.progress_end()
    except Exception:
        pass
    operator.report({'ERROR'}, f"Import failed: {exc} (traceback in Text '{log_prefix}_*')")
