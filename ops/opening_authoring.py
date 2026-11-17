# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/opening_authoring.py
"""Array on Wall (B07): copy the selected window or door across its wall.

Each copy goes through the same placement kernel as Draw Window / Door
(``place_semantic_element``, or ``create_feature`` for record-backed
windows), with the source's size, at a fixed step in metres along the
wall's own horizontal and vertical axes. Copies that would not fit (off the
wall, over another opening) are skipped and counted, never forced.
"""

import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import Operator
from mathutils import Vector

from ..shared.authoring_core.blender import flag
from ..shared.authoring_core.blender.usecases import CreateFeatureError, create_feature
from ..shared.facade_placement import (
    FacadeElementDefinition,
    PlacementRequest,
    place_semantic_element,
    surface_uv_from_world_point,
)
from ..shared.facade_surface_resolver import resolve_surface_context
from ..shared.semantic_tree import is_boundary_surface

_KIND = {"WindowSurface": "Window", "Window": "Window", "DoorSurface": "Door", "Door": "Door"}


def _opening_kind(obj):
    return _KIND.get(str(obj.get("cgml3_feature") or "")) if obj is not None else None


def _surface_at(wall, point):
    """The wall face (surface context) whose frame contains ``point``, or ``None``."""
    for index in range(len(wall.data.polygons)):
        try:
            surface = resolve_surface_context(wall, index)
        except ValueError:
            continue
        u, v = surface_uv_from_world_point(surface, point)
        off_plane = abs((Vector(point) - Vector(surface.origin_world)).dot(surface.normal_world))
        if -1e-6 <= u <= 1 + 1e-6 and -1e-6 <= v <= 1 + 1e-6 and off_plane < 0.5:
            return surface
    return None


def array_openings(wall, centre, h, v, kind, width, height, columns, rows, step_x, step_z, *,
                   skip_first=False, record_backed=False, placement_kw=None):
    """B59: ``columns`` x ``rows`` openings on ``wall`` from the world ``centre`` along the face axes
    ``h`` / ``v`` (the array operator skips the first: its source is there already). Returns
    ``(created objects, [(column, row, reason)])``."""
    created, failed = [], []
    for row in range(rows):
        for col in range(columns):
            if skip_first and row == 0 and col == 0:
                continue
            target = centre + h * (col * step_x) + v * (row * step_z)
            surf = _surface_at(wall, target)
            if surf is None:
                failed.append((col, row, "outside the wall"))
                continue
            definition = FacadeElementDefinition(semantic_type=kind, width=width, height=height,
                                                 generator_name="opening_array")
            request = PlacementRequest(position_uv=surface_uv_from_world_point(surf, target),
                                       **(placement_kw or {"source": "opening_array"}))
            try:
                if record_backed and kind == "Window":
                    created.append(create_feature("Window", surf, definition, request))
                else:
                    created.append(place_semantic_element(definition, surf, request))
            except (ValueError, RuntimeError, NotImplementedError, CreateFeatureError) as e:
                failed.append((col, row, str(e)))
    return created, failed


class CGML_OT_opening_array(Operator):
    """Copy the selected window or door across its wall: columns along the wall, rows up (F9 to adjust)"""
    bl_idname = "cgml.opening_array"
    bl_label = "Array on Wall"
    bl_options = {'REGISTER', 'UNDO'}

    columns: IntProperty(name="Columns", default=3, min=1, max=50)
    rows: IntProperty(name="Rows (floors)", default=1, min=1, max=50)
    step_x: FloatProperty(name="Column step", default=2.5, min=0.1, unit='LENGTH',
                          description="Centre-to-centre distance along the wall")
    step_z: FloatProperty(name="Row step", default=3.0, min=0.1, unit='LENGTH',
                          description="Centre-to-centre distance upwards (storey height)")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = (context.mode == 'OBJECT' and _opening_kind(obj) is not None
              and obj.parent is not None and is_boundary_surface(obj.parent))
        if not ok:
            cls.poll_message_set("Select a window or door that sits on a wall")
        return ok

    def execute(self, context):
        source = context.active_object
        kind, wall = _opening_kind(source), source.parent
        mw = source.matrix_world
        points = [mw @ v.co for v in source.data.vertices]
        centre = sum(points, Vector()) / len(points)
        surface = _surface_at(wall, centre)
        if surface is None:
            self.report({'ERROR'}, f"Could not find the face of '{wall.name}' under '{source.name}'")
            return {'CANCELLED'}
        h, v = Vector(surface.horizontal_axis_world), Vector(surface.vertical_axis_world)
        width = max(p.dot(h) for p in points) - min(p.dot(h) for p in points)
        height = max(p.dot(v) for p in points) - min(p.dot(v) for p in points)

        created, failed = array_openings(wall, centre, h, v, kind, width, height, self.columns, self.rows,
                                         self.step_x, self.step_z, skip_first=True,
                                         record_backed=flag.is_enabled())
        placed, skipped = len(created), len(failed)
        for o in context.selected_objects:
            o.select_set(False)
        source.select_set(True)
        context.view_layer.objects.active = source
        note = f", {skipped} did not fit" if skipped else ""
        self.report({'INFO'} if placed else {'WARNING'}, f"Placed {placed} {kind.lower()}(s){note}")
        return {'FINISHED'} if placed else {'CANCELLED'}


operator_classes = (CGML_OT_opening_array,)
