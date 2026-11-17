# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""B55: Check / Fix Planarity buttons (CityGML ``GE_P_NON_PLANAR``).

Scope: the selected objects' whole Buildings (or the object itself when it is
not in a Building), else every CityGML mesh in the scene. Fix runs only when the
user presses it (agent-readiness decision: repair on request)."""
from __future__ import annotations

from contextlib import contextmanager

import bpy
from bpy.props import FloatProperty
from bpy.types import Operator

from ..shared.planarity import DEFAULT_TOLERANCE_M, flatten, non_planar_faces
from ..shared.ring_opening_sync import building_ancestor


def _scope(context):
    if context.selected_objects:
        roots = {building_ancestor(o) or o for o in context.selected_objects}
        objs = {o for r in roots for o in (r, *r.children_recursive)}
    else:
        objs = set(context.scene.objects)
    return sorted((o for o in objs if o.type == 'MESH' and o.get("cgml3_feature") and o.mode != 'EDIT'),
                  key=lambda o: o.name)


@contextmanager
def in_object_mode(context):
    """B85: run a check or repair from any mode. The buttons used to be grey in Edit Mode, which is
    where adding a window or a chimney leaves the user. Object Mode for the run (it writes the edit
    mesh to the object), then back to the mode on the same object."""
    obj = context.view_layer.objects.active
    mode = obj.mode if obj is not None and context.mode != 'OBJECT' else None
    if mode:
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        active = context.view_layer.objects.active
        if mode and active is not None and active.name == obj.name and context.mode == 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=mode)
            except RuntimeError:
                pass  # stays in Object Mode


class _PlanarityBase:
    bl_options = {'REGISTER', 'UNDO'}
    tolerance: FloatProperty(name="Tolerance", default=DEFAULT_TOLERANCE_M, min=1e-5, max=1.0,
                             unit='LENGTH', precision=4,
                             description="Largest allowed distance of a vertex from its polygon's plane "
                                         "(CityDoctor default 1 cm)")

    def execute(self, context):
        with in_object_mode(context):
            return self.run(context)


class CGML_OT_check_planarity(_PlanarityBase, Operator):
    """Find polygons whose vertices are not in one plane (selected Buildings, else the whole scene)"""
    bl_idname = "cgml.check_planarity"
    bl_label = "Check Planarity"

    def run(self, context):
        bad = [(o, non_planar_faces(o, self.tolerance)) for o in _scope(context)]
        bad = [(o, faces) for o, faces in bad if faces]
        if not bad:
            self.report({'INFO'}, f"All polygons planar (tolerance {self.tolerance * 1000:.1f} mm)")
            return {'FINISHED'}
        for o in context.view_layer.objects:
            o.select_set(False)
        for o, _faces in bad:
            o.select_set(True)
        context.view_layer.objects.active = bad[0][0]
        n = sum(len(f) for _o, f in bad)
        worst = max(d for _o, f in bad for _i, d in f)
        self.report({'WARNING'}, f"{n} non-planar polygon(s) in {len(bad)} object(s), worst {worst * 1000:.1f} mm "
                                 f"(selected). Fix Planarity flattens them.")
        return {'FINISHED'}


class CGML_OT_fix_planarity(_PlanarityBase, Operator):
    """Flatten non-planar polygons onto their best-fit plane (selected Buildings, else the whole scene)"""
    bl_idname = "cgml.fix_planarity"
    bl_label = "Fix Planarity"

    def run(self, context):
        objs = _scope(context)
        fixed = sum(1 for o in objs if non_planar_faces(o, self.tolerance))
        left = flatten(objs, self.tolerance) if fixed else 0
        if not fixed:
            self.report({'INFO'}, "Nothing to fix: all polygons planar")
        elif left:
            self.report({'WARNING'}, f"Flattened {fixed} object(s); {left} polygon(s) still off-plane "
                                     f"(shared vertices) - check them by hand")
        else:
            self.report({'INFO'}, f"Flattened {fixed} object(s); all polygons planar")
        return {'FINISHED'}


classes = (CGML_OT_check_planarity, CGML_OT_fix_planarity)
