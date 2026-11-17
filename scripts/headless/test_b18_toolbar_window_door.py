"""B18: left toolbar skeleton + Window / Door tools.

- the tools are registered in Edit Mesh and Object mode, after a separator
- Edit Mesh keymap: a left click starts cgml.facade_draw (from_tool) first,
  then Blender's own click / box selection
- draw_settings never fails and follows the state: no selection -> type +
  "Select a building first"; building in Object Mode -> Edit Facade; Edit Mode
  without one face -> "Select a wall face first"; one face -> click hint;
  window selected -> edit actions (array, stamp source, align, delete)
- the click test: a ray through the selected face hits it, a ray beside it
  does not (that click falls through to selection)
- facade_draw run as the tool would after the click (one face, from_tool)
  places one window, one undo step is one operator call

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b18_toolbar_window_door.py -- <buildings_dir>
"""
import os
import sys
from importlib import import_module
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
REPO = Path(__file__).resolve().parents[2]


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
    """Layout stand-in: records operator idnames, labels and props."""

    def __init__(self, out):
        object.__setattr__(self, "out", out)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **k):
        return Rec(self.out)

    row = column = box = split = _child

    def operator(self, idname, text="", **k):
        self.out.append(("op", idname, text))
        return type("P", (), {})()

    def label(self, text="", **k):
        self.out.append(("label", text))

    def prop(self, data, name, **k):
        getattr(data, name)  # must exist
        self.out.append(("prop", name))

    def separator(self, *a, **k):
        pass


def settings(cls):
    out = []
    cls.draw_settings(bpy.context, Rec(out), None)
    return out


def tool_ids(mode):
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    cls = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
    return [t.idname if t is not None else None
            for t in ToolSelectPanelHelper._tools_flatten(cls._tools[mode])]


def labels(out):
    return [o[1] for o in out if o[0] == "label"]


def ops(out):
    return [o[1] for o in out if o[0] == "op"]


def wall_and_face(root):
    """The largest wall face of the building (room for a small window)."""
    best = (None, -1, 0.0)
    for o in [root, *root.children_recursive]:
        if o.type == 'MESH' and str(o.get("cgml3_feature") or "") == "WallSurface":
            for p in o.data.polygons:
                if p.area > best[2]:
                    best = (o, p.index, p.area)
    return best[0], best[1]


def main():
    buildings = sys.argv[sys.argv.index("--") + 1]
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    fa = import_module(f"{addon}.ops.facade_authoring")

    edit_ids, obj_ids = tool_ids('EDIT_MESH'), tool_ids('OBJECT')
    for mode, ids in (("EDIT_MESH", edit_ids), ("OBJECT", obj_ids)):
        check(f"{mode}: window and door tools registered",
              "cgml.window_tool" in ids and "cgml.door_tool" in ids, str(ids[-4:]))
        i = ids.index("cgml.window_tool") if "cgml.window_tool" in ids else 0
        check(f"{mode}: separator before the CityGML tools", ids[i - 1] is None)
        check(f"{mode}: door follows window", ids[i + 1:i + 2] == ["cgml.door_tool"])

    km = tools.WindowToolEdit.bl_keymap
    check("edit keymap: click starts facade_draw from the tool, first",
          km[0][0] == "cgml.facade_draw" and dict(km[0][2]["properties"]) == {"semantic_type": "Window", "from_tool": True})
    check("edit keymap: Blender selection after it", [k[0] for k in km[1:]].count("view3d.select") == 2)
    check("door keymap draws a Door", dict(tools.DoorToolEdit.bl_keymap[0][2]["properties"])["semantic_type"] == "Door")
    check("icons are Blender's own", all(c.bl_icon.startswith(("ops.", "brush.")) for c, _ in tools.TOOLS))

    # --- states ------------------------------------------------------------
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None
    out = settings(tools.WindowToolObject)
    check("nothing selected: window type + 'Select a building first'",
          ("prop", "cgml_window_type") in out and "Select a building first" in labels(out), str(out))
    out = settings(tools.DoorToolObject)
    check("door tool shows the door type", ("prop", "cgml_door_type") in out)

    gml = os.path.join(buildings, "buildings_lod3_citygml_3_0.gml")
    bpy.ops.cgml3.import_gml_file(filepath=gml)
    roots = [o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "Building" and o.parent is None]
    check("LoD3 fixture imported", bool(roots), gml)
    root = roots[0]
    bpy.context.view_layer.objects.active = root
    root.select_set(True)
    out = settings(tools.WindowToolObject)
    check("building in Object Mode: Edit Facade", "cgml.facade_edit_start" in ops(out), str(out))

    wall, face = wall_and_face(root)
    check("fixture has a wall face", wall is not None)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = wall
    wall.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    out = settings(tools.WindowToolEdit)
    check("Edit Mode, no face: 'Select a wall face first'", "Select a wall face first" in labels(out), str(out))

    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    f = bm.faces[face]
    f.select = True
    bm.select_flush(True)
    bmesh.update_edit_mesh(wall.data)
    out = settings(tools.WindowToolEdit)
    check("Edit Mode, one face: drag hint (B71: a click draws nothing), no 'select first'",
          "Select a wall face first" not in labels(out) and any("Drag" in t for t in labels(out)), str(out))

    mw = wall.matrix_world
    centre = mw @ f.calc_center_median()
    normal = (mw.to_3x3() @ f.normal).normalized()
    origin = centre + normal * 20.0
    hit = fa.ray_hits_selected_face(wall, face, origin, -normal)
    check("ray through the selected face hits it", hit)
    far = centre + Vector((1000.0, 1000.0, 0.0)) + normal * 20.0
    check("ray beside the face misses (click falls through to selection)",
          not fa.ray_hits_selected_face(wall, face, far, -normal))

    # what the tool does after a hit: facade_draw on the selected face (execute path)
    before = {o.name for o in bpy.data.objects}
    try:
        res = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type="Window", from_tool=True,
                                       width=0.6, height=0.6)
    except RuntimeError as exc:
        res = str(exc)
    added = {o.name for o in bpy.data.objects} - before
    check("facade_draw places a window on the selected face", res == {'FINISHED'} and bool(added), f"{res} {added}")
    bpy.ops.object.mode_set(mode='OBJECT')
    opening = next((bpy.data.objects[n] for n in added
                    if str(bpy.data.objects[n].get("cgml3_feature") or "") in tools.OPENINGS), None)
    if opening is not None:
        bpy.ops.object.select_all(action='DESELECT')
        bpy.context.view_layer.objects.active = opening
        opening.select_set(True)
        out = settings(tools.WindowToolObject)
        check("window selected: edit actions",
              {"cgml.opening_array", "cgml.set_semantic_stamp_source", "cgml.align_objects",
               "cgml.delete_feature"} <= set(ops(out)), str(out))
    else:
        check("placed window is a CityGML opening", False, str(added))


    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
