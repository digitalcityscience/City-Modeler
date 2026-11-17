"""B74: the Paste tool shows what a click would place while the mouse is over the selected face,
before the first click (it used to appear only after a click).

- the Edit Facade Paste tool carries the hover preview (cgml.tool_hover with op=cgml.semantic_stamp)
- copied window: a rectangle of the window's size on the selected wall face; none off the face
- copied balcony: its boxes on the selected wall face
- nothing copied: no preview

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b74_paste_hover_preview.py
"""
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


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")

    check("Paste tool carries the hover preview",
          any(k[0] == "cgml.tool_hover" and dict(k[2]["properties"]).get("op") == "cgml.semantic_stamp"
              for k in tools.StampToolEdit.bl_keymap))

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m, centre at the origin
    b = bpy.context.active_object
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]

    def centre(o):
        return sum((o.matrix_world @ v.co for v in o.data.vertices), Vector()) / len(o.data.vertices)
    front = min(walls, key=lambda o: centre(o).y)
    wall_y = min((front.matrix_world @ v.co).y for v in front.data.vertices)

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        edit_one_face(front)
        bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front.name, face_index=0,
                                 position_uv=(0.2, 0.2), width=1.2, height=1.4)
        bpy.ops.object.mode_set(mode='OBJECT')
        window = next(o for o in bpy.data.objects if o.get("cgml3_feature") in ("Window", "WindowSurface"))
        bal = ia.make_balcony(bpy.context, b, (0.0, wall_y, 3.0), (0, -1, 0), 'FRENCH', 2.2, 1.0, 1.3)

        edit_one_face(front)
        bpy.ops.wm.tool_set_by_id(name="cgml.stamp_tool")
        on = (Vector((3.0, wall_y - 20, 6.0)), Vector((0.0, 1.0, 0.0)))
        off = (Vector((25.0, 1.0, 2.5)), Vector((-1.0, 0.0, 0.0)))  # the side wall

        bpy.context.scene.pop("cgml_semantic_stamp_source", None)
        check("nothing copied: no preview", tools.preview_points(bpy.context, "cgml.semantic_stamp", *on) is None)

        bpy.context.scene["cgml_semantic_stamp_source"] = window.name
        pts = tools.preview_points(bpy.context, "cgml.semantic_stamp", *on)
        check("copied window: a rectangle on the selected face", pts is not None and len(pts) == 8, str(pts))
        if pts:
            xs, zs = [p[0] for p in pts], [p[2] for p in pts]
            check("... the window's size at the mouse",
                  abs(max(xs) - min(xs) - 1.2) < 1e-3 and abs(max(zs) - min(zs) - 1.4) < 1e-3
                  and abs((max(xs) + min(xs)) / 2 - 3.0) < 1e-3 and abs((max(zs) + min(zs)) / 2 - 6.0) < 1e-3
                  and all(abs(p[1] - wall_y) < 1e-3 for p in pts), str(pts))
        check("copied window: no preview off the selected face",
              tools.preview_points(bpy.context, "cgml.semantic_stamp", *off) is None)

        bpy.context.scene["cgml_semantic_stamp_source"] = bal.name
        check("copied balcony: its boxes on the selected face",
              bool(tools.preview_points(bpy.context, "cgml.semantic_stamp", *on)))


main()
print(f"B74 paste hover preview: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILED'}")
if FAILURES:
    sys.exit(1)
