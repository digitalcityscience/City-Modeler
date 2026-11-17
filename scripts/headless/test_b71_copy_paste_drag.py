"""B71: Copy / Paste (was Stamp) also for balcony / chimney; Window / Door draw by drag only;
Edit Facade balcony / chimney show their preview on the selected face; no auto 90° for a roof.

- a balcony / chimney made here keeps its type and size (cgml_install_params; not exported: cgml_ key)
- Copy (set_semantic_stamp_source) takes a balcony; Paste on another wall gives the copied size
  at the clicked spot (paste_args), a chimney pastes on a roof
- Paste's step reason follows what was copied (balcony: a wall face, chimney: a roof face)
- the Edit Facade chimney tool carries the hover preview; B81: the balcony tool none (the drag shows it)
  and fills the add operator's sizes the tool does not keep (offset, width ...) with their defaults
- Window / Door: a click (no drag) places nothing
- the facade right-click menu pastes through the Paste tool

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b71_copy_paste_drag.py
"""
import inspect
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES, activate  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402


def xs_of(obj):
    return [(obj.matrix_world @ v.co).x for v in obj.data.vertices]


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")
    fa = import_module(f"{addon}.ops.facade_authoring")
    fv = import_module(f"{addon}.ui.facade_view")

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m, centre at the origin
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    roof = next(o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface")
    def centre(o):
        return sum((o.matrix_world @ v.co for v in o.data.vertices), Vector()) / len(o.data.vertices)
    front = min(walls, key=lambda o: centre(o).y)  # the side walls reach y = -4 too
    wall_y = min((front.matrix_world @ v.co).y for v in front.data.vertices)

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        # a balcony made here keeps its type and size
        bal = ia.make_balcony(bpy.context, b, (0.0, wall_y, 3.0), (0, -1, 0), 'FRENCH', 2.2, 1.0, 1.3)
        params = bal["cgml_install_params"].to_dict()
        check("balcony keeps type and size",
              params == {"kind": 'FRENCH', "width": 2.2, "depth": 1.0, "railing": 1.3}, str(params))
        rz = max((roof.matrix_world @ v.co).z for v in roof.data.vertices)
        ch = ia.make_chimney(bpy.context, b, (2.0, 0.0, rz), (0, 0, 1), 'WIDE', 0.9, 0.5)
        check("chimney keeps type and size", ch["cgml_install_params"].to_dict()
              == {"kind": 'WIDE', "size": 0.9, "depth": 0.9, "above_ridge": 0.5})  # B80: depth = size when not given

        # Copy
        bpy.ops.object.select_all(action='DESELECT')
        activate(bal)
        check("Copy takes a balcony", bpy.ops.cgml.set_semantic_stamp_source.poll())
        check("Copy is labelled Copy", bpy.types.CGML_OT_set_semantic_stamp_source.bl_label == "Copy")
        bpy.ops.cgml.set_semantic_stamp_source()
        check("Copy remembers it", bpy.context.scene.get("cgml_semantic_stamp_source") == bal.name)

        # Paste on another wall: the copied size where clicked
        op, args, why = ia.paste_args(bpy.context, bal, Vector((25.0, 1.0, 2.5)), Vector((-1.0, 0.0, 0.0)))
        check("Paste a balcony on the side wall", args is not None and args["host"] == b.name, why)
        if args:
            check("... copied type and size, the side wall's normal",
                  args["kind"] == 'FRENCH' and args["width"] == 2.2 and args["railing"] == 1.3
                  and Vector(args["normal"]).dot(Vector((1, 0, 0))) > 0.99, str(args))
            activate(b)  # Copy cleared the active object; in Edit Facade the wall is active
            res = op('EXEC_DEFAULT', **args)
            new = bpy.context.active_object
            check("... pasted as a new balcony", res == {'FINISHED'} and new not in (bal, ch)
                  and new.get("cgml_semantic_type") == "Balcony"
                  and abs(new["cgml_install_params"]["width"] - 2.2) < 1e-5)  # operator floats are float32
        op, args, why = ia.paste_args(bpy.context, bal, Vector((0.0, 0.0, rz + 20)), Vector((0.0, 0.0, -1.0)))
        check("a balcony does not paste on a roof", args is None and "wall" in why, why)
        op, args, why = ia.paste_args(bpy.context, ch, Vector((-2.0, 0.5, rz + 20)), Vector((0.0, 0.0, -1.0)))
        check("Paste a chimney on the roof: copied size", args is not None and args["size"] == 0.9
              and args["kind"] == 'WIDE' and abs(args["point"][0] + 2.0) < 1e-3, f"{args} {why}")

        # Paste's step reason follows what was copied
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        edit_one_face(front)
        check("balcony copied, wall face: Paste on", tools.step_reason(bpy.context, "stamp") == "",
              tools.step_reason(bpy.context, "stamp"))
        bpy.context.scene["cgml_semantic_stamp_source"] = ch.name
        check("chimney copied, wall face: Paste says roof", "roof" in tools.step_reason(bpy.context, "stamp"),
              tools.step_reason(bpy.context, "stamp"))
        edit_one_face(roof)
        check("chimney copied, roof face: Paste on", tools.step_reason(bpy.context, "stamp") == "",
              tools.step_reason(bpy.context, "stamp"))

        # hover preview in Edit Facade: only over the selected face
        edit_one_face(front)
        bpy.ops.wm.tool_set_by_id(name="cgml.balcony_tool")
        hover = lambda c: any(k[0] == "cgml.tool_hover" for k in c.bl_keymap)
        check("B81: Edit Facade Chimney tool carries the hover preview, Balcony not (the drag shows it)",
              hover(tools.ChimneyToolEdit) and not hover(tools.BalconyToolEdit))
        on = tools.preview_points(bpy.context, "cgml.balcony_draw", Vector((1.0, wall_y - 20, 2.0)),
                                  Vector((0.0, 1.0, 0.0)))
        check("B81: no balcony ghost over the selected wall face before the drag", on is None)
        check("chimney tool: no auto 90° on a roof", "cgml.chimney_tool" not in fv.FACADE_TOOLS)

    # Window / Door: a click places nothing; the menu pastes through the tool
    modal_src = inspect.getsource(fa._FacadeDraw.modal)
    check("Window / Door: a click places nothing", "Drag on the face to draw" in modal_src
          and "B71" in modal_src)
    check("Window header: drag", "Drag on the selected face" in inspect.getsource(tools.draw_opening_settings))
    check("facade right-click menu: Paste = the Paste tool",
          '"cgml.tool_pick", text="Paste"' in inspect.getsource(fa.CGML_MT_facade_context.draw))
    check("Paste tool label", tools.StampToolEdit.bl_label == "Paste"
          and tools.StampToolObject.bl_label == "Copy")


main()
print(f"B71 copy paste drag: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILED'}")
if FAILURES:
    sys.exit(1)
