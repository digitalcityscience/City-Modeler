"""Scene origin by hand and aligning foreign data (point clouds, meshes) with the city model.

Blender keeps float32 vertices, so City Modeler places data around a scene origin (World
``cgml_X/Y/Z-Origin``; Bonsai calls it the false origin, BlenderGIS keeps its own). Other data lines
up when it is shifted by the same origin:

- ``cgml3.set_scene_origin``: type CRS + origin before opening anything (Bonsai "Manual"),
  e.g. the Global Shift a point cloud got in CloudCompare; every importer keeps it for that CRS.
- ``cgml3.align_to_scene_origin``: move selected non-CityGML objects into the scene frame. Point clouds
  opened with Point Cloud IO keep their shift in ``las_origin_offset``; it is read automatically
  (user decision 2026-11-25, an exception to B30's "no foreign add-on keys"), the rest is typed.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel
from mathutils import Vector

from ..shared import georef as gr
from ..shared.import_origin import POINT_CLOUD_IO_OFFSET, float32_step, parse_origin, point_cloud_io_offset
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
    bl_label = "Set Origin Before Opening"
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


# scene origin a Point Cloud IO object was aligned to: its shift is fixed, so a second Align skips it
ALIGNED_PROP = "cgml3_aligned_origin"

MANUAL_HELP = (
    "Their origin = real coordinates (X Y Z) of their Blender 0,0,0:",
    "- imported with real coordinates (no shift): 0 0 0",
    "- BlenderGIS: the origin in its Geoscene panel, Z 0",
    "- CloudCompare Global Shift: the shift with the sign flipped",
    "- other importers: the offset/shift they subtracted",
)


def _crs_label() -> str:
    w = gr.world()
    return gr.epsg(w.get(gr.CRS)) if w is not None else "Unknown CRS"


def _aligned_to(obj, origin) -> bool:
    done = obj.get(ALIGNED_PROP)
    return done is not None and len(done) == 3 and all(abs(a - b) < 1e-6 for a, b in zip(done, origin))


class CGML3_OT_align_to_scene_origin(Operator):
    """Move the selected objects that are not CityGML (point cloud, shapefile, mesh) onto the city model.
    Point clouds opened with Point Cloud IO carry their shift and move automatically"""
    bl_idname = "cgml3.align_to_scene_origin"
    bl_label = "Align Selected to City Model"
    bl_options = {'REGISTER', 'UNDO'}

    source_offset: StringProperty(
        name="Their origin", default="0 0 0",
        description="Real CRS coordinates (X Y Z) of the selected data's own Blender 0,0,0: 0 0 0 when it "
                    "was imported with real coordinates, else the shift its importer subtracted")

    @staticmethod
    def _targets(context):
        objs = [o for o in context.selected_objects
                if not o.get("cgml3_feature") and not o.get(browse_gml.PROXY_PATH_PROP)]
        chosen = set(objs)
        return [o for o in objs if o.parent not in chosen]  # children move with their parent

    @staticmethod
    def _split(targets):
        """(auto, manual): auto = [(obj, offset)] read from Point Cloud IO, manual = the rest."""
        auto, manual = [], []
        for o in targets:
            off = point_cloud_io_offset(o.get(POINT_CLOUD_IO_OFFSET))
            if off is None:
                manual.append(o)
            else:
                auto.append((o, off))
        return auto, manual

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        o = scene_origin()
        crs = _crs_label()
        col.label(text=f"City model: {crs}", icon='ORIENTATION_GLOBAL')
        col.label(text=f"Blender 0,0,0 = {_fmt(o)}" if o else "Blender 0,0,0: origin not set", icon='BLANK1')
        auto, manual = self._split(self._targets(context))
        if auto:
            done = sum(1 for obj, _ in auto if o is not None and _aligned_to(obj, o))
            col.label(text=f"{len(auto) - done} point cloud(s) from Point Cloud IO: shift read automatically",
                      icon='CHECKMARK')
            if done:
                col.label(text=f"{done} already aligned: skipped", icon='INFO')
        if manual:
            box = col.box()
            box.label(text=f"{len(manual)} other object(s) (shapefile, mesh, point cloud):", icon='INFO')
            box.prop(self, "source_offset")
            hint = box.column(align=True)
            hint.scale_y = 0.8
            for line in MANUAL_HELP:
                hint.label(text=line)
        if not auto and not manual:
            col.label(text="Select objects that are not CityGML", icon='ERROR')
            return
        col.label(text=f"The data must be in {crs} too (no reprojection)", icon='INFO')

    def execute(self, context):
        origin = scene_origin()
        if origin is None or _crs_label() == "Unknown CRS":
            self.report({'ERROR'}, "The city model is not georeferenced (no CRS / origin): open a CityGML file "
                                   "with srsName or use Set Origin Before Opening first")
            return {'CANCELLED'}
        targets = self._targets(context)
        if not targets:
            self.report({'ERROR'}, "Select objects that are not CityGML (point cloud, shapefile, mesh)")
            return {'CANCELLED'}
        auto, manual = self._split(targets)
        moves = [(o, Vector(off) - Vector(origin), True) for o, off in auto if not _aligned_to(o, origin)]
        skipped = len(auto) - len(moves)
        if manual:
            src = parse_origin(self.source_offset)
            if src is None:
                self.report({'ERROR'}, f"Their origin needs 2 or 3 numbers (X Y [Z]): '{self.source_offset}'")
                return {'CANCELLED'}
            moves += [(o, Vector(src) - Vector(origin), False) for o in manual]
        if not moves:
            self.report({'WARNING'}, f"{skipped} point cloud(s) already aligned to this city model: nothing moved")
            return {'CANCELLED'}
        coarse = []
        for o, delta, from_pcio in moves:
            # vertex data far from 0 was rounded to float32 on import; moving cannot bring that back
            reach = max((abs(c) for corner in o.bound_box for c in corner), default=0.0)
            if float32_step(reach) > PRECISION_WARN_M:
                coarse.append((o.name, float32_step(reach)))
            o.matrix_world.translation += delta
            if from_pcio:
                o[ALIGNED_PROP] = list(origin)
        moved = f"Moved {len(moves)} object(s)"
        deltas = {tuple(round(v, 3) for v in d) for _, d, _ in moves}
        if len(deltas) == 1:
            d = moves[0][1]
            moved += f" by {d.x:.3f}, {d.y:.3f}, {d.z:.3f}"
        n_pcio = sum(1 for m in moves if m[2])
        if n_pcio:
            moved += f" ({n_pcio} with the Point Cloud IO shift)"
        if skipped:
            moved += f"; {skipped} already aligned, skipped"
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
    bl_label = "Align Other Data"
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
        col.label(text="Point cloud, shapefile, mesh: select it, then Align", icon='INFO')
        col.operator(CGML3_OT_align_to_scene_origin.bl_idname, text="Align Selected to City Model...",
                     icon='SNAP_ON')
        col.operator(CGML3_OT_set_scene_origin.bl_idname, text="Set Origin Before Opening...", icon='EMPTY_AXIS')


classes = (CGML3_OT_set_scene_origin, CGML3_OT_align_to_scene_origin, CGML3_PT_SceneOrigin)
