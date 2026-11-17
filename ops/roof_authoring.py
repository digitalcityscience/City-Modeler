# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/roof_authoring.py
"""Add Roof (B05/B06/B11): put a parametric LoD2 roof on a building.

The building's footprint (its GroundSurface) and eave height (lowest roof
point) are kept; its Wall/Roof/Ground surface objects are rebuilt from
``shared.roof_shapes`` the same way B04 builds a LoD1 block, so gml:id and
attributes of the Building itself survive and export needs no new path.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator
from mathutils import Vector

from ..modeltyper.core import materials as mt_materials
from ..shared import building_shapes, codelists, roof_shapes
from ..shared.attribute_utils import CODE_SPACE_PREFIX
from ..shared.authoring_core.blender.guards import refuse_if_record_backed
from ..shared.lod_helpers import lod_promotion_root
from .building_authoring import (AUTHORED_SCHEMA, explode_authored, footprint_from_object, model_height,
                                 set_height, set_lod)

_SURFACES = {"WallSurface", "RoofSurface", "GroundSurface"}
DEFAULT_PITCH = {"1000": 0.0, "2100": 10.0, "2200": 15.0, "3800": 25.0, "3900": 30.0,
                 "3600": 50.0, "3700": 45.0, "4000": 65.0}  # 3700: 45 = hemisphere
ROOF_ITEMS = [(code, f"{name} ({code})", f"AdV roofType {code}") for code, name in roof_shapes.ROOF_TYPES.items()]


_OWNERS = ("Building", "BuildingPart")


def _owner(obj):
    """The nearest Building/BuildingPart at or above ``obj`` (a part, not its whole building)."""
    while obj is not None and str(obj.get("cgml3_feature") or "") not in _OWNERS:
        obj = obj.parent
    return obj


def _parts(obj):
    return [c for c in obj.children if c.get("cgml3_feature") == "BuildingPart"]


def roof_target(context):
    """``(target, reason)``: the Building/BuildingPart a roof goes on, or why not.
    A Building made of parts gets the roof on every part."""
    obj = context.active_object
    if obj is None or context.mode != 'OBJECT':
        return None, "Select a building in Object Mode"
    return roof_target_for(obj)


def roof_target_for(obj):
    """B59: ``roof_target`` for a given object (no selection or mode)."""
    target = _owner(obj)
    if target is None:
        return None, "Select a CityGML building"
    others = [o for o in target.children_recursive
              if str(o.get("cgml3_feature") or "") not in _SURFACES | {"BuildingPart"}]
    if others:
        return None, "Remove windows, doors and installations first (the roof rebuilds the walls)"
    return target, ""


def split_into_parts(context, target, rects, base, eave):
    """Turn ``target``'s single block into one LoD1 BuildingPart per rectangle (B09)."""
    from .building_authoring import build_lod1
    for child in list(target.children_recursive):
        data = child.data
        bpy.data.objects.remove(child)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
    old = target.data
    target.data = bpy.data.meshes.new(old.name)
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return [build_lod1(context, rect, eave - base, name=f"{target.name} part {i + 1}", parent=target)
            for i, rect in enumerate(rects)]


def _footprint_and_heights(root):
    surfaces = [o for o in root.children if o.type == 'MESH']
    ground = next((o for o in surfaces if o.get("cgml3_feature") == "GroundSurface"), None)
    ring = footprint_from_object(ground or root, selected_only=False)
    roof_z = [(o.matrix_world @ v.co).z for o in surfaces if o.get("cgml3_feature") == "RoofSurface"
              for v in o.data.vertices]
    all_z = [(o.matrix_world @ v.co).z for o in surfaces for v in o.data.vertices]
    if ring is None or not all_z:
        return None, 0.0, 0.0
    base = min(p[2] for p in ring)
    eave = min(roof_z) if roof_z else max(all_z)
    return building_shapes.ccw(ring), base, eave


class CGML_OT_roof_add(Operator):
    """Put a parametric LoD2 roof (AdV roof types) on the selected building; F9 to adjust"""
    bl_idname = "cgml.roof_add"
    bl_label = "Roof"  # B33: = the tool / row name
    bl_options = {'REGISTER', 'UNDO'}

    roof_type: EnumProperty(name="Roof", items=ROOF_ITEMS, default="3100")
    pitch: FloatProperty(name="Pitch", default=35.0, min=0.0, max=75.0, subtype='NONE',
                         description="Roof pitch in degrees")
    ridge_along_long: BoolProperty(name="Ridge along long side", default=True)
    flip: BoolProperty(name="Flip high side", default=False, description="Mono-pitch: high side on the other edge")
    from_tool: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        from ..ui.tools import tool_active
        if context.mode == 'OBJECT' and tool_active(context, "cgml.roof_tool"):
            return True  # B19: the click picks the building
        root, reason = roof_target(context)
        if root is None:
            cls.poll_message_set(reason)  # tooltip of the greyed-out button
        return root is not None

    def invoke(self, context, event):
        if self.from_tool and not self._pick_clicked(context, event):
            return {'PASS_THROUGH'}
        self.choose(context)
        return self.execute(context)

    def choose(self, context):
        """B33: popover and T tool share ``wm.cgml_roof_choice``; the pitch goes back to the type's
        default only when the type changes (otherwise the last pitch, F9 included, stays).
        Tool click: the remembered ``roof_type`` is the one the pitch belongs to."""
        wm = context.window_manager
        prev = self.roof_type if self.from_tool else wm.cgml_roof_choice
        if self.from_tool:
            self.roof_type = wm.cgml_roof_choice
        else:
            wm.cgml_roof_choice = self.roof_type
        if self.roof_type != prev:
            self.pitch = DEFAULT_PITCH.get(self.roof_type, 35.0)

    def _pick_clicked(self, context, event):
        from .installation_authoring import ray_under_mouse
        return self.pick_at(context, *ray_under_mouse(context, event))

    def pick_at(self, context, origin, direction):
        """B19 roof tool: make the building on the ray active, shape from the tool header.
        False (click selects) on no building; an imported LoD2 roof stays (B15 rule)."""
        from .installation_authoring import building_root
        from ..ui.workflow_panels import roof_editable
        dg = context.evaluated_depsgraph_get()
        hit, _loc, _n, _i, obj, _m = context.scene.ray_cast(dg, origin, direction)
        root = building_root(obj.original) if hit else None
        if root is None:
            return False
        owner = lod_promotion_root(root)
        if not roof_editable(owner):
            self.report({'WARNING'}, f"'{owner.name}' keeps its imported roof")
            return False
        for o in context.selected_objects:
            o.select_set(False)
        owner.select_set(True)
        context.view_layer.objects.active = owner
        return True

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "roof_type")
        if self.roof_type != "1000":
            col.prop(self, "pitch")
        if self.roof_type not in ("1000", "3200", "3500") + roof_shapes.RADIAL:
            col.prop(self, "ridge_along_long")
        if self.roof_type == "2100":
            col.prop(self, "flip")

    def execute(self, context):
        root, reason = roof_target(context)
        if root is None:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        message = refuse_if_record_backed([root, *root.children_recursive], self.bl_label)
        if message:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        try:
            parts = put_roof(context, root, self.roof_type, self.pitch, self.ridge_along_long, self.flip)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        where = f"{len(parts)} parts of '{root.name}'" if parts != [root] else f"'{root.name}'"
        self.report({'INFO'}, f"{roof_shapes.ROOF_TYPES[self.roof_type]} roof on {where} (LoD2)")
        return {'FINISHED'}


def put_roof(context, root, roof_type, pitch=35.0, ridge_along_long=True, flip=False):
    """B59: the roof ``roof_add`` and the agent API put on ``root`` (from ``roof_target_for``).
    Returns the parts that got a roof; ``ValueError`` when it does not fit (nothing changed,
    except a later part failing after the split: the caller's undo step reverts that)."""
    parts = _parts(root)
    if not parts:
        ring, base, eave = _footprint_and_heights(root)
        if ring is None:
            raise ValueError(f"No footprint found on '{root.name}'")
        square = building_shapes.squared(ring)  # B72: a few degrees off a right angle is fine
        if (roof_type == "1000" or (square and roof_shapes.rectangle_frame(square) is not None)
                or (roof_type in roof_shapes.RADIAL and roof_shapes.is_convex(ring))):
            parts = [root]  # B51: a flat roof fits any footprint; B87: cone / dome / tower any convex one
        else:
            rects = building_shapes.rectilinear_rectangles(square) if square else None
            if not rects:
                raise ValueError("Pitched roofs need a footprint with (nearly) right-angled corners "
                                 "(rectangle, L, T, U); the Flat roof fits any footprint, "
                                 "Cone, Dome and Tower any convex one")
            parts = split_into_parts(context, root, rects, base, eave)
    for part in parts:
        ring, base, eave = _footprint_and_heights(part)
        if ring and roof_type != "1000":
            ring = building_shapes.squared(ring) or ring  # the walls are rebuilt on the squared footprint
        solid =roof_shapes.roof_solid(ring, base, eave, roof_type, pitch, ridge_along_long, flip) if ring else None
        if solid is None:
            raise ValueError(f"'{part.name}' is not rectangular; no roof put on it")
        apply_solid(context, part, solid, lod=2)
        _set_roof_type(part, roof_type)
        set_height(part, model_height(part))
    _set_roof_type(root, roof_type)
    if parts != [root]:
        set_height(root, model_height(root))
    return parts


def _set_roof_type(obj, code) -> None:
    """B87: the AdV code with its codeSpace; "3100" alone does not say which list it is from."""
    obj["bldg:roofType"] = code
    obj[CODE_SPACE_PREFIX + "bldg:roofType"] = codelists.code_space("ADV", "bldg:roofType")


def apply_solid(context, root, solid, lod: int) -> None:
    """Replace ``root``'s surface objects with ``solid`` (world-space verts, faces, labels)."""
    verts, faces, labels = solid
    for child in list(root.children_recursive):
        data = child.data
        bpy.data.objects.remove(child)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
    old = root.data
    inv = root.matrix_world.inverted()
    mesh = bpy.data.meshes.new(old.name)
    mesh.from_pydata([inv @ Vector(v) for v in verts], [], faces)
    mesh.update()
    root.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    mt_materials.tag_feature(root, labels, "BUILDING", AUTHORED_SCHEMA,
                             feature_name=root.get("cgml3_feature"))  # Building or BuildingPart
    set_lod(root, lod)
    collection = root.users_collection[0] if root.users_collection else context.scene.collection
    explode_authored(root, collection)


operator_classes = (CGML_OT_roof_add,)
