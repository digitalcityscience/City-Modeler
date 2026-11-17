# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/building_authoring.py
"""Authored buildings (B04): a LoD1 block from a rectangle or a footprint.

The block is built the way imported buildings look (B03 principle 4): a
Building object with one child object per Ground/Roof/Wall surface, made by
the same steps ModelTyper + "Split into Surfaces" use (tag_feature, then
explode_building_into_semantic_objects), so export needs no new path.
"""

import bpy
import bmesh
import json

from bpy.props import FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from mathutils import Vector

from ..common.builder_support.semantic_explode import explode_building_into_semantic_objects
from ..modeltyper.core import materials as mt_materials
from ..shared import building_shapes, modal_hint
from ..shared.footprint_draw import FootprintDraw, edge_lengths

AUTHORED_SCHEMA = "3.0"


def _ensure_crs(context, epsg: int) -> None:
    """New buildings in an empty scene need a CRS; never overwrite one that exists."""
    from ..shared import georef as gr

    world = gr.georef_world(context.scene)  # what import/export read
    if gr.CRS not in world and epsg > 0:
        world[gr.CRS] = f"EPSG:{epsg}"


def set_lod(obj, lod: int) -> None:
    obj["lod"] = lod  # B39: the 2.0 writer takes the Building's LoD (lod{N}Solid) from the object
    for mat in obj.data.materials:
        if mat is not None:
            mat["lod"] = lod


HEIGHT_STATUS = "estimated"  # computed from the model, not surveyed


def model_height(root) -> float:
    """Highest roof edge over the lowest ground point of ``root``'s Ground/Roof/Wall surfaces
    (installations such as a chimney do not count)."""
    zs = [(o.matrix_world @ v.co).z for o in (root, *root.children_recursive)
          if o.type == 'MESH' and o.get("cgml3_feature") in ("GroundSurface", "RoofSurface", "WallSurface")
          for v in o.data.vertices]
    return max(zs) - min(zs) if zs else 0.0


def set_height(obj, height: float) -> None:
    """con:height of an authored building (B39), referenced like Hamburg's: highestRoofEdge over
    lowestGroundPoint. The 2.0 writer turns it into bldg:measuredHeight. A height with
    another status (typed on the attribute card) is left alone."""
    if height <= 0 or obj.get("con:height:status", HEIGHT_STATUS) != HEIGHT_STATUS:
        return
    obj["con:height"] = ""
    obj["con:height:value"] = round(float(height), 3)
    obj["con:height:highReference"] = "highestRoofEdge"
    obj["con:height:lowReference"] = "lowestGroundPoint"
    obj["con:height:status"] = HEIGHT_STATUS
    obj["con:height:uom"] = "m"


def explode_authored(obj, collection) -> None:
    """Split an authored block into surface objects, each face its own surface (B36).

    Every authored face is a whole Ground/Roof/Wall surface, so each gets its own copy of
    the per-type material with a fresh surface / polygon id, the way imported surfaces do.
    Shared materials gave every wall the same ids once a window stamped them, and surfaces
    without ``con_surface_id`` got a non-NCName gml_id ("WallSurface::2") and a new random
    id on every export."""
    from ..shared.authoring_core.domain.ids import mint_gml_id, ring_id

    mesh = obj.data
    shared = {m for m in mesh.materials if m is not None}
    for poly in mesh.polygons:
        base = mesh.materials[poly.material_index] if poly.material_index < len(mesh.materials) else None
        if base is None:
            continue
        mat = base.copy()
        poly_id = mint_gml_id()
        mat["con_surface_id"] = mint_gml_id()
        mat["gml_polygon_id"] = poly_id
        mat["gml_ring_id"] = ring_id(poly_id, 0)
        mat["gml_multisurface_id"] = mint_gml_id()
        mesh.materials.append(mat)
        poly.material_index = len(mesh.materials) - 1
    explode_building_into_semantic_objects(obj, collection, schema_version=AUTHORED_SCHEMA)
    for mat in shared:
        if mat.users == 0:
            bpy.data.materials.remove(mat)


def build_lod1(context, footprint, height: float, storeys: int = 0, name: str = "Building", parent=None):
    """Create a LoD1 Building (or, with ``parent``, a BuildingPart of it) from a
    world-space footprint ring; returns the new object, selected and active."""
    verts, faces, labels = building_shapes.prism(footprint, height)
    origin = Vector((sum(v[0] for v in verts) / len(verts), sum(v[1] for v in verts) / len(verts),
                     min(v[2] for v in verts)))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([Vector(v) - origin for v in verts], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = origin
    collection = (parent.users_collection[0] if parent is not None and parent.users_collection
                  else context.collection or context.scene.collection)
    collection.objects.link(obj)

    mt_materials.tag_feature(obj, labels, "BUILDING", AUTHORED_SCHEMA,
                             feature_name="BuildingPart" if parent is not None else None)
    if parent is not None:
        obj["cgml_parent_id"] = str(parent.get("gml_id") or "")
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
    set_lod(obj, 1)
    if storeys > 0:
        obj["bldg:storeysAboveGround"] = storeys
    obj["cgml_source"] = "authored"
    explode_authored(obj, collection)
    set_height(obj, model_height(obj))

    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def footprint_from_object(obj, selected_only: bool):
    """Outer ring (world space) of the lowest horizontal faces of ``obj``, or ``None``."""
    bm = bmesh.from_edit_mesh(obj.data) if obj.mode == 'EDIT' else bmesh.new()
    try:
        if obj.mode != 'EDIT':
            bm.from_mesh(obj.data)
        bm.verts.index_update()
        mw = obj.matrix_world
        normal_mat = mw.to_3x3().inverted_safe().transposed()
        faces = [f for f in bm.faces if not selected_only or f.select]
        flat = [f for f in faces if abs((normal_mat @ f.normal).normalized().z) > 0.99]
        if not flat:
            return None
        z_of = {f: min((mw @ v.co).z for v in f.verts) for f in flat}
        low = min(z_of.values())
        base = [f for f in flat if z_of[f] - low < 0.01]
        loop = building_shapes.boundary_loop([[v.index for v in f.verts] for f in base])
        bm.verts.ensure_lookup_table()
        ring = [tuple(mw @ bm.verts[i].co) for i in loop]
    finally:
        if obj.mode != 'EDIT':
            bm.free()
    ring = building_shapes.drop_collinear(ring)
    return ring if len(ring) >= 3 else None


class _Lod1Props:
    height: FloatProperty(name="Height", default=9.0, min=0.5, soft_max=200.0, unit='LENGTH')
    storeys: IntProperty(name="Storeys", default=0, min=0, max=200,
                         description="Storeys above ground (0 = not written)")
    epsg: IntProperty(name="EPSG (empty scene)", default=25832, min=0,
                      description="CRS for a scene that has none yet; ignored when the scene has a CRS")

    def _draw_common(self, context):
        col = self.layout.column()
        col.prop(self, "height")
        col.prop(self, "storeys")
        from ..shared import georef as gr
        world = gr.world()
        if world is None or gr.CRS not in world:
            col.prop(self, "epsg")


_draw_handles = []
_drawing = {"state": None}


def _ground_point(context, px):
    """Point on the horizontal plane through the 3D cursor under the mouse, or None.
    B35: Blender's snap (vertex / edge / edge midpoint / increment) as for Window / Door."""
    from bpy_extras import view3d_utils
    from ..Openings_Cutter.Openings_Cutter import resolve_opening_cutter_snap_mouse, snap_point_to_increment
    px, _drawing["snap"], _snapped = resolve_opening_cutter_snap_mouse(context, px, _drawing.get("snap"))
    region, rv3d = context.region, context.region_data
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, px)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, px)
    z = context.scene.cursor.location.z
    if abs(direction.z) < 1e-6:
        return None
    t = (z - origin.z) / direction.z
    if t < 0:
        return None
    p = snap_point_to_increment(context, origin + direction * t, (0.0, 0.0, 1.0))
    return (p.x, p.y, z)


def _draw_lines():
    """POST_VIEW: the footprint being drawn (blue), and the block in the height phase."""
    state = _drawing["state"]
    if state is None:
        return
    pts, closed = state.outline()
    if not pts:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    segs = []
    ring = list(pts) + (pts[:1] if closed and len(pts) > 2 else [])
    for a, b in zip(ring, ring[1:]):
        segs += [a, b]
    if state.phase == "HEIGHT":
        top = [(x, y, z + state.height) for x, y, z in pts]
        for i, (a, b) in enumerate(zip(top, top[1:] + top[:1])):
            segs += [a, b, pts[i], top[i]]
    if not segs:
        return
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    vp = gpu.state.viewport_get()
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    shader.bind()
    shader.uniform_float("viewportSize", (vp[2], vp[3]))
    shader.uniform_float("lineWidth", 2.5)
    shader.uniform_float("color", (0.2, 0.55, 1.0, 1.0))
    batch_for_shader(shader, 'LINES', {"pos": segs}).draw(shader)
    gpu.state.blend_set('NONE')


def _draw_text():
    """POST_PIXEL: live edge lengths at the edge midpoints, the height at the top."""
    state = _drawing["state"]
    context = bpy.context
    if state is None or context.region_data is None:
        return
    import blf
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    pts, closed = state.outline()
    labels = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a[2], f"{d:.2f} m")
              for a, b, d in edge_lengths(pts, closed and len(pts) > 2) if d > 0.01]
    if state.phase == "HEIGHT" and pts:
        x, y, z = pts[0]
        labels.append((x, y, z + state.height, f"{state.height:.1f} m (~{state.storeys()} storeys)"))
    blf.size(0, 15)
    blf.color(0, 1.0, 1.0, 1.0, 1.0)
    for x, y, z, text in labels:
        p = location_3d_to_region_2d(context.region, context.region_data, (x, y, z))
        if p is not None:
            blf.position(0, p.x + 6, p.y + 6, 0)
            blf.draw(0, text)


def _stop_drawing(context):
    _drawing["state"] = None
    _drawing["snap"] = None
    for handle, kind in _draw_handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, kind)
    _draw_handles.clear()
    context.workspace.status_text_set(None)
    if context.area is not None:
        context.area.tag_redraw()


_KEYS = {"RET": "ENTER", "NUMPAD_ENTER": "ENTER", "BACK_SPACE": "BACKSPACE",
         "PERIOD": ".", "NUMPAD_PERIOD": ".", "COMMA": "."}
_KEYS.update({n: str(i) for i, n in enumerate(("ZERO", "ONE", "TWO", "THREE", "FOUR",
                                                "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"))})
_KEYS.update({f"NUMPAD_{i}": str(i) for i in range(10)})
_NAVIGATION = {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM',
               'NDOF_MOTION'}


NO_FOOTPRINT = "New Building needs a footprint: draw it in the 3D view (scripts pass points)"


class CGML_OT_building_new(_Lod1Props, Operator):
    """New LoD1 building: drag a rectangle or click the corners of its footprint, then give the height"""
    bl_idname = "cgml.building_new"
    bl_label = "New Building"
    bl_options = {'REGISTER', 'UNDO'}

    # B27: the drawn footprint, world (x, y, z) as JSON. B34: required; nothing is placed without it
    points: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            cls.poll_message_set("Back to Object Mode (Done)")
        return context.mode == 'OBJECT'

    @classmethod
    def description(cls, context, props):
        from ..ui.tools import step_description
        return step_description(context, "new_building", cls.__doc__)  # B33: "Now: ..." when greyed

    def draw(self, context):
        self._draw_common(context)

    @modal_hint.clears_status
    def invoke(self, context, event):
        """B27: draw the footprint in the 3D view (a script or F9 runs ``execute``)."""
        if context.area is None or context.area.type != 'VIEW_3D' or context.region_data is None:
            self.report({'ERROR'}, NO_FOOTPRINT)  # B34: no 3D view, no drawing, nothing placed
            return {'CANCELLED'}
        if _drawing["state"] is not None:
            _stop_drawing(context)  # a drawing left over (e.g. an add-on reload)
        _drawing["state"] = FootprintDraw()
        _drawing["state"].height = self.height
        _draw_handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'),
                              'WINDOW'))
        _draw_handles.append((bpy.types.SpaceView3D.draw_handler_add(_draw_text, (), 'WINDOW', 'POST_PIXEL'),
                              'WINDOW'))
        context.workspace.status_text_set(modal_hint.hint(_drawing["state"].status(), modal_hint.snap_note(context)))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    @modal_hint.clears_status
    def modal(self, context, event):
        state = _drawing["state"]
        if state is None:
            return {'CANCELLED'}
        if event.type in _NAVIGATION or (event.type == 'MOUSEMOVE' and event.alt):
            return {'PASS_THROUGH'}
        px = (event.mouse_region_x, event.mouse_region_y)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            _stop_drawing(context)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            m_per_px = context.region_data.view_distance * 2.0 / max(context.region.height, 1)
            state.move(_ground_point(context, px), px, ctrl=event.ctrl, m_per_px=m_per_px)
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            state.press(_ground_point(context, px), px)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            state.release(_ground_point(context, px), px)
        elif event.value == 'PRESS' and event.type in _KEYS:
            state.key(_KEYS[event.type])
        context.area.tag_redraw()
        if state.phase == "DONE":
            ring = building_shapes.drop_collinear(list(state.points))
            self.points = json.dumps([[round(c, 4) for c in p] for p in ring])
            self.height = state.height
            _stop_drawing(context)
            if len(ring) < 3:
                self.report({'WARNING'}, "A footprint needs at least three corners")
                return {'CANCELLED'}
            result = self.execute(context)
            _frame_new_building(context)
            return result
        context.workspace.status_text_set(modal_hint.hint(state.status(), modal_hint.snap_note(context)))
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        _stop_drawing(context)

    def execute(self, context):
        if not self.points:  # B34: never a computed spot (it was 10 x 8 m at the 3D cursor)
            self.report({'ERROR'}, NO_FOOTPRINT)
            return {'CANCELLED'}
        _ensure_crs(context, self.epsg)
        ring = [tuple(p) for p in json.loads(self.points)]
        obj = build_lod1(context, ring, self.height, self.storeys)
        self.report({'INFO'}, f"LoD1 building '{obj.name}' created; add a roof to make it LoD2")
        return {'FINISHED'}


def _frame_new_building(context):
    """Zoom to the new building: its own mesh is empty after the split, so frame its surfaces."""
    obj = context.active_object
    if obj is None:
        return
    parts = [obj, *obj.children_recursive]
    for o in parts:
        o.select_set(True)
    try:
        bpy.ops.view3d.view_selected()
    finally:
        for o in parts[1:]:
            o.select_set(False)


class CGML_OT_building_from_footprint(_Lod1Props, Operator):
    """LoD1 building from the selected footprint: a flat mesh, a ground surface, a LoD0 footprint or a box (Edit Mode: selected faces)"""
    bl_idname = "cgml.building_from_footprint"
    bl_label = "From Footprint"  # B33: = the build row button
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = obj is not None and obj.type == 'MESH' and len(obj.data.polygons) > 0
        if not ok:
            cls.poll_message_set("Select a flat mesh (the footprint)")
        return ok

    @classmethod
    def description(cls, context, props):
        from ..ui.tools import step_description
        return step_description(context, "from_footprint", cls.__doc__)

    def draw(self, context):
        self._draw_common(context)

    def execute(self, context):
        source = context.active_object
        ring = footprint_from_object(source, selected_only=source.mode == 'EDIT')
        if ring is None:
            self.report({'ERROR'}, "No flat footprint found: select a flat face (or a mesh with one at its base)")
            return {'CANCELLED'}
        if source.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        _ensure_crs(context, self.epsg)
        obj = build_lod1(context, ring, self.height, self.storeys)
        self.report({'INFO'}, f"LoD1 building '{obj.name}' with {len(ring)} walls from '{source.name}'")
        return {'FINISHED'}


operator_classes = (CGML_OT_building_new, CGML_OT_building_from_footprint)
