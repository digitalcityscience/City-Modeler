"""B68: Face Wall turns the view straight at the selected wall face (90°, upright, orthographic).

- face_rotation: looks along -normal from outside, world Z up, X horizontal (also a turned or tilted wall)
- Face Wall (Shift+F / build row): ortho, facing the face, textures in Solid; again: your view back
- Auto: with the Window / Door / Stamp tool, a newly selected face is faced; a coplanar one is not;
  Auto off or another tool: nothing; picking Window in the row faces the face already selected
- leaving Edit Mode (Done) brings your view back
- handler registered once (also after a re-register); Shift+F in the Mesh keymap
- B70: the Edit Facade Balcony tool faces the wall like Window; B71: Auto never turns to a roof

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b68_face_wall.py
"""
import math
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def close(a, b, tol=1e-4):
    return (Vector(a) - Vector(b)).length < tol


def axes(q):
    m = q.to_matrix()
    return m @ Vector((1, 0, 0)), m @ Vector((0, 1, 0)), m @ Vector((0, 0, -1))  # right, up, looking


def pick_face(normal):
    """Select the one face (of the objects in Edit Mode) whose world normal is ``normal``; make it active."""
    hit = None
    for o in bpy.context.objects_in_mode:
        b = bmesh.from_edit_mesh(o.data)
        for f in b.faces:
            n = (o.matrix_world.to_3x3() @ f.normal).normalized()
            on = hit is None and close(n, normal, 1e-3)
            f.select_set(on)
            if on:
                hit = (o, f.index)
                b.faces.active = f
        bmesh.update_edit_mesh(o.data)
    if hit:
        bpy.context.view_layer.objects.active = hit[0]
    return hit


def tools_active(idname):
    tool = bpy.context.workspace.tools.from_space_view3d_mode(bpy.context.mode, create=False)
    return tool is not None and tool.idname == idname


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    fv = import_module(f"{addon}.ui.facade_view")

    for n in ((0, -1, 0), (1, 0, 0), (math.cos(0.5), math.sin(0.5), 0), (0.3, -0.9, 0.3), (0, 0, 1)):
        n = Vector(n).normalized()
        right, up, look = axes(fv.face_rotation(n))
        upright = up.z > 0 if abs(n.z) < 0.99 else close(up, (0, 1, 0))
        check(f"normal {tuple(round(c, 2) for c in n)}: looks at it from outside, upright, X level",
              close(look, -n) and upright and abs(right.z) < 1e-6, f"look={look} up={up} right={right}")
    _r, up, _l = axes(fv.face_rotation((0, -1, 0)))
    check("a vertical wall: view up is world Z", close(up, (0, 0, 1)))

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space, rv3d = area.spaces.active, area.spaces.active.region_3d
    with bpy.context.temp_override(window=win, area=area, region=region):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        bpy.ops.cgml.building_new(points=footprint(10.0, 8.0))
        bpy.ops.cgml.facade_edit_start()
        check("Edit Facade: Edit Mode", bpy.context.mode == 'EDIT_MESH')
        pick_face((0, 0, 1))
        for o in bpy.context.objects_in_mode:  # nothing selected
            b = bmesh.from_edit_mesh(o.data)
            for f in b.faces:
                f.select_set(False)
            bmesh.update_edit_mesh(o.data)
        check("no face: Face Wall greyed", not bpy.ops.cgml.face_wall.poll())

        rv3d.view_perspective = 'PERSP'
        space.shading.type, space.shading.color_type = 'SOLID', 'MATERIAL'
        before = (rv3d.view_rotation.copy(), rv3d.view_location.copy(), rv3d.view_distance)
        check("front wall selected", pick_face((0, -1, 0)) is not None)
        check("one face: Face Wall available", bpy.ops.cgml.face_wall.poll())
        res = bpy.ops.cgml.face_wall()
        _r, up, look = axes(rv3d.view_rotation)
        check("Face Wall: ortho, looking at the -Y wall from outside, Z up",
              res == {'FINISHED'} and rv3d.view_perspective == 'ORTHO' and close(look, (0, 1, 0))
              and close(up, (0, 0, 1)), f"{res} {rv3d.view_perspective} look={look}")
        check("Face Wall: textures shown", space.shading.color_type == 'TEXTURE')
        check("Face Wall: framed on the wall (centre in its plane)", abs(rv3d.view_location.y + 4.0) < 0.5,
              str(rv3d.view_location))
        check("row button pressed", fv.is_facing())

        res = bpy.ops.cgml.face_wall()
        check("again: your own view back",
              res == {'FINISHED'} and not fv.is_facing() and rv3d.view_perspective == 'PERSP'
              and rv3d.view_rotation.rotation_difference(before[0]).angle < 1e-4
              and close(rv3d.view_location, before[1]) and abs(rv3d.view_distance - before[2]) < 1e-4
              and space.shading.color_type == 'MATERIAL')

        # Auto
        wm = bpy.context.window_manager
        check("B81: Auto on by default", wm.cgml_face_wall_auto)
        wm.cgml_face_wall_auto = True
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        check("another tool: no auto", not fv.auto_face(bpy.context) and not fv.is_facing())
        bpy.ops.cgml.tool_pick(key="window")
        _r, _u, look = axes(rv3d.view_rotation)
        check("Window picked in the row: the selected wall turns to you",
              fv.is_facing() and close(look, (0, 1, 0)), f"look={look}")
        pick_face((1, 0, 0))
        check("Window tool, another wall selected: faced", fv.auto_face(bpy.context))
        _r, _u, look = axes(rv3d.view_rotation)
        check("... looking at the +X wall", close(look, (-1, 0, 0)), str(look))
        check("same plane again: view not moved", not fv.auto_face(bpy.context))
        wm.cgml_face_wall_auto = False
        pick_face((0, 1, 0))
        check("Auto off: not faced", not fv.auto_face(bpy.context))
        wm.cgml_face_wall_auto = True
        check("handler wants it once Auto is back", fv._auto_wanted(bpy.context))

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')
        fv._on_depsgraph_update(bpy.context.scene, None)
        check("Done: view restore waits for the timer, not the depsgraph tick", fv.is_facing())
        bpy.ops.object.mode_set(mode='EDIT')
        fv._deferred_restore()
        check("transient Object Mode (placement): view kept", fv.is_facing())
        bpy.ops.object.mode_set(mode='OBJECT')
        fv._deferred_restore()
        check("Done: your own view back (from before the first facing)",
              not fv.is_facing() and rv3d.view_perspective == 'PERSP'
              and rv3d.view_rotation.rotation_difference(before[0]).angle < 1e-4
              and space.shading.color_type == 'MATERIAL')

        # B70: Balcony / Chimney are facade tools like Window: the wall / the roof turns to you
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        pick_face((0, -1, 0))
        bpy.ops.cgml.tool_pick(key="balcony")
        _r, _u, look = axes(rv3d.view_rotation)
        check("B70 Balcony picked in the row: the selected wall turns to you",
              tools_active("cgml.balcony_tool") and fv.is_facing() and close(look, (0, 1, 0)), str(look))
        # B92: a corner balcony wraps around the corner: no 90° for it
        bpy.ops.cgml.tool_type_set(op="cgml.balcony_draw", kind='CORNER')
        check("B92 Corner picked: your own view back", fv.corner_balcony(bpy.context) and not fv.is_facing()
              and rv3d.view_perspective == 'PERSP')
        pick_face((1, 0, 0))
        check("B92 Corner, another wall selected: not faced", not fv.auto_face(bpy.context) and not fv.is_facing())
        bpy.ops.cgml.tool_type_set(op="cgml.balcony_draw", kind='PROJECTING')
        _r, _u, look = axes(rv3d.view_rotation)
        check("B92 Projecting again: the selected wall turns to you", fv.is_facing() and close(look, (-1, 0, 0)),
              str(look))
        pick_face((0, 0, 1))
        bpy.ops.wm.tool_set_by_id(name="cgml.chimney_tool")
        check("B71 Chimney tool, roof face selected: the view stays (no auto 90° for the roof)",
              not fv.auto_face(bpy.context))
        bpy.ops.wm.tool_set_by_id(name="cgml.window_tool")
        check("B71 Auto turns to walls only (a roof face with Window: no)", not fv.auto_face(bpy.context))
        check("B71 Shift+F still faces the roof", bpy.ops.cgml.face_wall.poll())
        bpy.ops.object.mode_set(mode='OBJECT')
        fv._deferred_restore()
        check("B70 Done: view back", not fv.is_facing())

    tools = import_module(f"{addon}.ui.tools")
    check("B70 Edit Facade Balcony / Chimney tools: a press on the selected face clicks or drags",
          tools.BalconyToolEdit.bl_keymap[0][0] == "cgml.balcony_draw"
          and tools.ChimneyToolEdit.bl_keymap[0][0] == "cgml.chimney_draw"
          and tools.BalconyToolEdit.bl_idname == "cgml.balcony_tool"
          and {tools.BalconyToolEdit, tools.ChimneyToolEdit} <= {c for c, _s in tools.TOOLS})

    fv.unregister()
    fv.register()  # a reload re-registers
    mine = [f for f in bpy.app.handlers.depsgraph_update_post if getattr(f, "__module__", "") == fv.__name__]
    check("handler registered once", len(mine) == 1, str(mine))
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.get("Mesh")
        check("Shift+F in the Mesh keymap", km is not None and any(
            k.idname == "cgml.face_wall" and k.type == 'F' and k.shift for k in km.keymap_items))


main()
print(f"B68 face wall: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILED'}")
if FAILURES:
    sys.exit(1)
