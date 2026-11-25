"""Scene origin by hand and aligning foreign data (point clouds, meshes) with the city model.

Blender keeps float32 vertices, so City Modeler places data around a scene origin (World
``cgml_X/Y/Z-Origin``; Bonsai calls it the false origin, BlenderGIS keeps its own). Other data lines
up when it is shifted by the same origin:

- ``cgml3.set_scene_origin``: type CRS + origin before opening anything (Bonsai "Manual"),
  e.g. the Global Shift a point cloud got in CloudCompare; every importer keeps it for that CRS.
- ``cgml3.align_to_scene_origin``: move selected non-CityGML objects into the scene frame.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel
from mathutils import Vector

from ..shared import georef as gr
from ..shared.import_origin import float32_step, parse_origin
from . import browse_gml

# vertices coarser than this lost detail on import (raw CRS coordinates)
PRECISION_WARN_M = 0.01


def scene_origin():
    """(x, y, z) of Blender 0,0,0 in the World CRS, or None."""
    w = gr.world()
    if w is None or gr.X_ORIGIN not in w or gr.Y_ORIGIN not in w:
        return None
    return (float(w[gr.X_ORIGIN]), float(w[gr.Y_ORIGIN]), float(w.get(gr.Z_ORIGIN, 0.0)))


def _fmt(origin) -> str:
    return " ".join(f"{v:.3f}" for v in origin)


def _city_data_open() -> bool:
    return bool(browse_gml.proxy_objects()) or any(o.users and o.get("cgml3_feature") for o in bpy.data.objects)


class CGML3_OT_set_scene_origin(Operator):
    """Set the CRS and the CRS coordinates of Blender 0,0,0 before opening data, so CityGML lines up
    with data shifted the same way (e.g. a point cloud's Global Shift)"""
    bl_idname = "cgml3.set_scene_origin"
    bl_label = "Set Scene Origin"
    bl_options = {'REGISTER', 'UNDO'}

    crs: StringProperty(name="CRS", description="EPSG code of the data, e.g. EPSG:25832")
    origin: StringProperty(name="Origin", description="X Y Z in the CRS (dot decimals), e.g. 548100 5934200 0")

    def invoke(self, context, event):
        w = gr.world()
        if w is not None and gr.epsg(w.get(gr.CRS)) != "Unknown CRS":
            self.crs = gr.epsg(w.get(gr.CRS))
        o = scene_origin()
        if o is not None:
            self.origin = _fmt(o)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "crs")
        col.prop(self, "origin")
        col.label(text="Use the same shift for the point cloud", icon='INFO')

    def execute(self, context):
        if getattr(context.scene.cgml3, "import_local", False):
            self.report({'ERROR'}, "Local Import is on (File Import Options): it ignores the scene origin")
            return {'CANCELLED'}
        crs = gr.epsg(self.crs)
        if crs == "Unknown CRS":
            self.report({'ERROR'}, f"Not an EPSG code: '{self.crs}' (write e.g. EPSG:25832)")
            return {'CANCELLED'}
        origin = parse_origin(self.origin)
        if origin is None:
            self.report({'ERROR'}, f"Origin needs 2 or 3 numbers (X Y [Z]): '{self.origin}'")
            return {'CANCELLED'}
        # shortcut: no re-basing of open data (BlenderGIS moves every object); add it if users ask
        if _city_data_open():
            self.report({'ERROR'}, "Set the origin before opening data: close the previews and "
                                   "start a new file (File > New), or use Align Selected for the other data")
            return {'CANCELLED'}
        w = gr.georef_world(context.scene)
        w[gr.CRS] = crs
        gr.set_world_float(w, gr.X_ORIGIN, origin[0])
        gr.set_world_float(w, gr.Y_ORIGIN, origin[1])
        gr.set_world_float(w, gr.Z_ORIGIN, origin[2])
        self.report({'INFO'}, f"Scene origin {crs}: {_fmt(origin)}")
        return {'FINISHED'}


class CGML3_OT_align_to_scene_origin(Operator):
    """Move the selected objects that are not CityGML (point cloud, mesh) into the city model's frame"""
    bl_idname = "cgml3.align_to_scene_origin"
    bl_label = "Align Selected to City Model"
    bl_options = {'REGISTER', 'UNDO'}

    source_offset: StringProperty(
        name="Their origin", default="0 0 0",
        description="CRS coordinates of the selected data's own 0,0,0: 0 0 0 when it was imported with "
                    "real CRS coordinates, else the shift its importer subtracted")

    @staticmethod
    def _targets(context):
        objs = [o for o in context.selected_objects
                if not o.get("cgml3_feature") and not o.get(browse_gml.PROXY_PATH_PROP)]
        chosen = set(objs)
        return [o for o in objs if o.parent not in chosen]  # children move with their parent

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        o = scene_origin()
        col.label(text=f"City model origin: {_fmt(o)}" if o else "City model origin: not set")
        col.prop(self, "source_offset")
        col.label(text=f"{len(self._targets(context))} object(s) will move", icon='INFO')

    def execute(self, context):
        origin = scene_origin()
        w = gr.world()
        if origin is None or gr.epsg(w.get(gr.CRS)) == "Unknown CRS":
            self.report({'ERROR'}, "The city model is not georeferenced (no CRS / origin): open a CityGML file "
                                   "with srsName or Set Scene Origin first")
            return {'CANCELLED'}
        src = parse_origin(self.source_offset)
        if src is None:
            self.report({'ERROR'}, f"Their origin needs 2 or 3 numbers (X Y [Z]): '{self.source_offset}'")
            return {'CANCELLED'}
        targets = self._targets(context)
        if not targets:
            self.report({'ERROR'}, "Select objects that are not CityGML (point cloud, mesh)")
            return {'CANCELLED'}
        delta = Vector(src) - Vector(origin)
        coarse = []
        for o in targets:
            # vertex data far from 0 was rounded to float32 on import; moving cannot bring that back
            reach = max((abs(c) for corner in o.bound_box for c in corner), default=0.0)
            if float32_step(reach) > PRECISION_WARN_M:
                coarse.append((o.name, float32_step(reach)))
            o.matrix_world.translation += delta
        moved = f"Moved {len(targets)} object(s) by {delta.x:.3f}, {delta.y:.3f}, {delta.z:.3f}"
        if coarse:
            name, step = max(coarse, key=lambda c: c[1])
            self.report({'WARNING'}, f"{moved}. '{name}' was imported with raw CRS coordinates: its points are "
                                     f"only ~{step:g} m precise. For full precision shift it before import by "
                                     f"the scene origin (e.g. CloudCompare Global Shift)")
        else:
            self.report({'INFO'}, moved)
        return {'FINISHED'}


class CGML3_PT_SceneOrigin(Panel):
    """Open tab: the scene origin and lining other data up with the city model."""
    bl_label = "Align Other Data (Scene Origin)"
    bl_idname = "CGML3_PT_SceneOrigin"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column()
        o = scene_origin()
        if o is not None:
            col.label(text=f"Blender 0,0,0 = {_fmt(o)}", icon='ORIENTATION_GLOBAL')
        col.label(text="Point clouds: shift them by this origin", icon='INFO')
        col.operator(CGML3_OT_set_scene_origin.bl_idname, text="Set Scene Origin...", icon='EMPTY_AXIS')
        col.operator(CGML3_OT_align_to_scene_origin.bl_idname, text="Align Selected to City Model...",
                     icon='SNAP_ON')


classes = (CGML3_OT_set_scene_origin, CGML3_OT_align_to_scene_origin, CGML3_PT_SceneOrigin)
