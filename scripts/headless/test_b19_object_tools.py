"""B19: Object Mode tools Roof, Balcony, Chimney + Stamp (Edit Mode).

- registered: Object Mode Window, Door, Stamp, Roof, Balcony, Chimney; Edit Mode Stamp
- keymaps: a click runs the operator with from_tool (Balcony / Chimney also a
  hover preview), then Blender's selection
- with the tool active the operators poll without a selection (the click picks)
- Balcony / Chimney preview follows the tool header settings (Projecting vs French)
- Roof tool click: imported LoD2 roof refused, a building made here gets the
  shape picked in the header and becomes active
- Stamp header: no source -> how to get one; source set -> its name
- draw_settings of every tool draws in Object and Edit Mode

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b19_object_tools.py -- <buildings_dir>
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
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
        getattr(data, name)
        self.out.append(("prop", name))

    def template_icon_view(self, data, name, **k):
        getattr(data, name)
        self.out.append(("icons", name))

    def separator(self, *a, **k):
        pass


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return dict(window=win, area=area, region=region)


def set_tool(idname):
    with bpy.context.temp_override(**view3d()):
        bpy.ops.wm.tool_set_by_id(name=idname)


def active_tool():
    return bpy.context.workspace.tools.from_space_view3d_mode(bpy.context.mode, create=False)


def settings(cls):
    out = []
    cls.draw_settings(bpy.context, Rec(out), active_tool())
    return out


def tool_ids(mode):
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    cls = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
    return [t.idname if t is not None else None for t in ToolSelectPanelHelper._tools_flatten(cls._tools[mode])]


def biggest_face(objs, ok):
    best = None
    for o in objs:
        mw = o.matrix_world
        for p in o.data.polygons:
            n = (mw.to_3x3() @ p.normal).normalized()
            if ok(n) and (best is None or p.area > best[2]):
                best = (o, p, p.area, mw @ p.center, n)
    return best


def roots():
    return [o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "Building" and o.parent is None]


def fresh():
    bpy.ops.object.select_all(action='DESELECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def main():
    buildings = sys.argv[sys.argv.index("--") + 1]
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")
    roof_mod = import_module(f"{addon}.ops.roof_authoring")

    obj_ids, edit_ids = tool_ids('OBJECT'), tool_ids('EDIT_MESH')
    want = ["cgml.window_tool", "cgml.door_tool", "cgml.stamp_tool", "cgml.roof_tool", "cgml.balcony_tool",
            "cgml.chimney_tool"]
    check("Object Mode: six CityGML tools in order", [i for i in obj_ids if i and i.startswith("cgml.")] == want,
          str(obj_ids[-7:]))
    check("Edit Mode: Window / Door first, Paste (B71, was Stamp) there too",
          [i for i in edit_ids if i and i.startswith("cgml.")][:2] == want[:2]
          and "cgml.stamp_tool" in edit_ids, str(edit_ids[-7:]))
    for cls, op in ((tools.RoofTool, "cgml.roof_add"), (tools.BalconyTool, "cgml.balcony_add"),
                    (tools.ChimneyTool, "cgml.chimney_add")):
        km = cls.bl_keymap
        check(f"{cls.bl_label}: click runs {op} from the tool, selection after",
              km[0][0] == op and dict(km[0][2]["properties"]) == {"from_tool": True}
              and "view3d.select" in [k[0] for k in km])
    check("Balcony / Chimney keymaps carry the hover preview",
          all(any(k[0] == "cgml.tool_hover" for k in c.bl_keymap) for c in (tools.BalconyTool, tools.ChimneyTool)))
    check("Stamp (Edit Mode) click runs cgml.semantic_stamp from the tool",
          tools.StampToolEdit.bl_keymap[0][0] == "cgml.semantic_stamp")

    fresh()
    gml = os.path.join(buildings, "buildings_lod3_citygml_2_0.gml")
    bpy.ops.cgml3.import_gml_file(filepath=gml)
    root = roots()[0]
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None

    # --- polls: nothing selected -------------------------------------------
    set_tool("builtin.select_box")
    check("no tool, nothing selected: balcony does not poll", not bpy.ops.cgml.balcony_add.poll())
    for tid, op in (("cgml.balcony_tool", bpy.ops.cgml.balcony_add), ("cgml.chimney_tool", bpy.ops.cgml.chimney_add),
                    ("cgml.roof_tool", bpy.ops.cgml.roof_add)):
        set_tool(tid)
        check(f"{tid} active: tool_active", tools.tool_active(bpy.context, tid))
        check(f"{tid} active: its operator polls without a selection", op.poll())

    # --- balcony preview follows the header settings ------------------------
    set_tool("cgml.balcony_tool")
    walls = [o for o in root.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    _o, _p, _a, c, n = biggest_face(walls, lambda n: abs(n.z) < 0.2)
    hn = Vector((n.x, n.y, 0)).normalized()
    click = c - Vector((0, 0, 1.0))
    props = active_tool().operator_properties("cgml.balcony_add")
    props.kind = 'PROJECTING'
    pts_p = tools.preview_points(bpy.context, "cgml.balcony_add", click + hn * 30, -hn)
    props.kind = 'FRENCH'
    pts_f = tools.preview_points(bpy.context, "cgml.balcony_add", click + hn * 30, -hn)
    check("balcony preview on a wall", bool(pts_p), str(len(pts_p or [])))
    check("French preview differs from Projecting (header setting used)",
          bool(pts_f) and len(pts_f) < len(pts_p or []), f"{len(pts_f or [])} vs {len(pts_p or [])}")
    check("no preview over the sky", tools.preview_points(bpy.context, "cgml.balcony_add",
                                                          c + Vector((0, 0, 500)), Vector((0, 0, 1))) is None)
    props.kind = 'PROJECTING'
    out = settings(tools.BalconyTool)
    check("Balcony header: type, railing (B67: width / depth from the wall)",
          [o[1] for o in out if o[0] == "prop"] == ["kind", "railing"], str(out))

    set_tool("cgml.chimney_tool")
    roofs = [o for o in root.children_recursive if o.get("cgml3_feature") == "RoofSurface"]
    rb = biggest_face(roofs, lambda n: n.z > 0.3)
    check("chimney preview on a roof",
          rb is not None and bool(tools.preview_points(bpy.context, "cgml.chimney_add", rb[3] + Vector((0, 0, 30)),
                                                       Vector((0, 0, -1)))))
    check("chimney: no preview on a wall",
          tools.preview_points(bpy.context, "cgml.chimney_add", click + hn * 30, -hn) is None)

    # --- roof tool -----------------------------------------------------------
    set_tool("cgml.roof_tool")
    op = type("Op", (), {"report": lambda self, *a: None, "roof_type": "1000", "pitch": 0.0, "from_tool": True})()
    picked = roof_mod.CGML_OT_roof_add.pick_at(op, bpy.context, c + Vector((0, 0, 200)), Vector((0, 0, -1)))
    check("roof tool: imported LoD2/3 roof stays (click refused)", picked is False)
    check("roof tool: a click on nothing selects (False)",
          roof_mod.CGML_OT_roof_add.pick_at(op, bpy.context, c + Vector((0, 0, 500)), Vector((0, 0, 1))) is False)
    fresh()
    bpy.ops.cgml.building_new(points=footprint())
    new_root = roots()[0]
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None
    bpy.context.window_manager.cgml_roof_choice = "2100"
    top = new_root.matrix_world.translation
    centre = sum((new_root.matrix_world @ v.co for v in new_root.data.vertices), Vector()) / max(1, len(new_root.data.vertices)) \
        if new_root.type == 'MESH' and len(new_root.data.vertices) else top
    picked = roof_mod.CGML_OT_roof_add.pick_at(op, bpy.context, Vector((centre.x, centre.y, 500)), Vector((0, 0, -1)))
    check("roof tool: new building picked and active", picked is True
          and bpy.context.view_layer.objects.active == new_root, str(bpy.context.view_layer.objects.active))
    roof_mod.CGML_OT_roof_add.choose(op, bpy.context)  # B33: invoke takes the shape from the shared choice
    check("roof tool: shape from the header", op.roof_type == "2100", op.roof_type)
    res = bpy.ops.cgml.roof_add(roof_type=op.roof_type)
    check("roof added on the picked building", res == {'FINISHED'} and new_root.get("bldg:roofType") == "2100",
          f"{res} {new_root.get('bldg:roofType')}")
    out = settings(tools.RoofTool)
    check("Roof header: icon gallery + name + hint",
          ("icons", "cgml_roof_choice") in out and any("Click a building" in str(o[1]) for o in out), str(out))

    # --- stamp header --------------------------------------------------------
    set_tool("cgml.stamp_tool")
    bpy.context.scene.pop("cgml_semantic_stamp_source", None)
    out = settings(tools.StampToolObject)
    check("Copy: nothing copied -> how to copy", any("Nothing copied" in str(o[1]) for o in out), str(out))
    copied = bpy.data.objects[0].name
    bpy.context.scene["cgml_semantic_stamp_source"] = copied
    out = settings(tools.StampToolObject)
    check("Copy: the copied object named", any(f"Copied: {copied}" == o[1] for o in out), str(out))

    # every header draws in Object and Edit Mode
    problems = []
    for cls, _sep in tools.TOOLS:
        if cls.bl_context_mode != 'OBJECT':
            continue
        set_tool(cls.bl_idname)
        try:
            cls.draw_settings(bpy.context, Rec([]), active_tool())
        except Exception as exc:
            problems.append(f"{cls.__name__}: {exc}")
    bpy.context.view_layer.objects.active = new_root
    new_root.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for cls, _sep in tools.TOOLS:
        if cls.bl_context_mode != 'EDIT_MESH':
            continue  # Blender draws a tool's header only in its own mode
        try:
            cls.draw_settings(bpy.context, Rec([]), active_tool())
        except Exception as exc:
            problems.append(f"{cls.__name__} (edit): {exc}")
    bpy.ops.object.mode_set(mode='OBJECT')
    check("every tool header draws (Object + Edit Mode)", not problems, str(problems))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
