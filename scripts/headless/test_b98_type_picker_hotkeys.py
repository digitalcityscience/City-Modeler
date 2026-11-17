# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
"""B98 headless: balcony drag preview, type dropdowns with search, 1-4 hotkeys (Edit Facade).

- drag_to_balcony / drag_to_chimney with final=False (the live preview) never use the header size: a tiny drag
  gives the 0.5 m / 0.2 m minimum and the width grows with the mouse (it used to start at the header's 3 m and
  jump down); final=True (the release) still gives the header size for a very short drag (B73);
- each Edit tool has a type dropdown (Window, Door, Balcony, Chimney) with a search field: the list is filtered
  by name; a type button starts its tool and sets the type before any face is selected;
- cgml.tool_key (hotkeys 1-4) starts that tool in Edit Facade, polls False elsewhere (plain mesh, Object Mode).

Run: blender -b --factory-startup --python scripts/headless/test_b98_type_picker_hotkeys.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
    """Layout stand-in that records the operators (kind, id, text, depress, kind prop) and labels."""

    def __init__(self, out):
        object.__setattr__(self, "out", out)

    def row(self, *a, **k):
        return self

    column = grid_flow = row

    def label(self, text="", **k):
        self.out.append(("label", text))

    def template_icon(self, **k):
        self.out.append(("icon",))

    def operator(self, idname, text="", depress=False, **k):
        entry = ["op", idname, text, depress, ""]
        self.out.append(entry)

        class P:
            def __setattr__(s, name, value):
                if name == "kind":
                    entry[4] = value
                object.__setattr__(s, name, value)
        return P()

    def __getattr__(self, name):
        return lambda *a, **k: None


def gallery(panel_cls):
    """Draw a gallery panel into the recorder (a stand-in self: layout + the class settings)."""
    out = []
    holder = type("H", (), {"layout": Rec(out), "OP": panel_cls.OP, "PREFIX": panel_cls.PREFIX,
                            "HINT": panel_cls.HINT, "TYPES_NAME": panel_cls.TYPES_NAME})()
    holder.TYPES = panel_cls.TYPES.fget(holder)
    panel_cls.draw(holder, bpy.context)
    return out


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ia = import_module(f"{addon}.ops.installation_authoring")
    tools = import_module(f"{addon}.ui.tools")
    wm = bpy.context.window_manager

    # --- drag preview does not jump --------------------------------------------------------------
    wall = {"host": "H", "normal": (0, -1, 0), "plane": (0, -4, 0), "lo": -5.0, "hi": 5.0, "zmin": 0.0}
    widths = [ia.drag_to_balcony(wall, (0, -4, 3), (dx, -4, 3), 'PROJECTING', 3.0, 1.4, 1.0, final=False)["width"]
              for dx in (0.05, 0.3, 0.6, 1.0, 2.0)]
    check("preview: starts at the 0.5 m minimum, not the header's 3 m", widths[0] == 0.5, str(widths))
    check("preview: the width never shrinks as the drag grows", widths == sorted(widths), str(widths))
    short = ia.drag_to_balcony(wall, (0, -4, 3), (0.1, -4, 3), 'PROJECTING', 3.0, 1.4, 1.0)
    check("release after a very short drag: the header's width (B73 kept)", short["width"] == 3.0, str(short["width"]))
    roof = {"host": "H", "normal": (0, 0, 1)}
    c = ia.drag_to_chimney(roof, (0, 0, 9), (0.05, 0, 9), 'SQUARE', 0.9, 0.9, final=False)
    check("chimney preview: the 0.2 m minimum, not the header's size", c["size"] == 0.2 and c["depth"] == 0.2, str(c))
    c = ia.drag_to_chimney(roof, (0, 0, 9), (0.05, 0, 9), 'SQUARE', 0.9, 0.9)
    check("chimney release after a tiny drag: the header's size (B73 kept)", c["size"] == 0.9, str(c))

    # --- type dropdowns with search ---------------------------------------------------------------
    for key, panel, n in (("window", tools.CGML_PT_window_gallery, 6), ("door", tools.CGML_PT_door_gallery, 4),
                          ("balcony", tools.CGML_PT_balcony_gallery, 3), ("chimney", tools.CGML_PT_chimney_gallery, 5)):
        ops = [e for e in gallery(panel) if e[0] == "op"]
        check(f"{key} dropdown lists all {n} types", len(ops) == n and {e[1] for e in ops} == {"cgml.tool_type_set"},
              str(len(ops)))
    wm.cgml_search_window = "WIDE"
    names = [e[2] for e in gallery(tools.CGML_PT_window_gallery) if e[0] == "op"]
    check("search is case-insensitive and filters by name", names == ["Wide window 2.00 × 1.40"], str(names))
    wm.cgml_search_window = "zzz"
    out = gallery(tools.CGML_PT_window_gallery)
    check("no match: says so, no buttons", ("label", "No match") in out and not [e for e in out if e[0] == "op"])
    wm.cgml_search_window = ""

    # --- picking a type starts the tool; hotkeys --------------------------------------------------
    win = wm.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        bpy.ops.cgml.building_new(points=footprint())
        check("Object Mode: hotkey operator does not poll", not bpy.ops.cgml.tool_key.poll())
        bpy.ops.cgml.facade_edit_start()
        check("Edit Facade: hotkey operator polls", bpy.ops.cgml.tool_key.poll())
        for key, tool_id in (("window", "cgml.window_tool"), ("door", "cgml.door_tool"),
                             ("balcony", "cgml.balcony_tool"), ("chimney", "cgml.chimney_tool")):
            bpy.ops.cgml.tool_key(key=key)
            check(f"key for {key}: its tool is active", tools.tool_active(bpy.context, tool_id))
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        bpy.ops.cgml.tool_type_set(key="door", kind="D_201x2135")
        check("door type picked first: tool active, type set",
              tools.tool_active(bpy.context, "cgml.door_tool") and wm.cgml_door_type == "D_201x2135")
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        bpy.ops.cgml.tool_type_set(op="cgml.balcony_draw", kind="FRENCH")
        tool = bpy.context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
        check("balcony type picked first: tool active, kind set (no face selected yet)",
              tools.tool_active(bpy.context, "cgml.balcony_tool")
              and tool.operator_properties("cgml.balcony_draw").kind == 'FRENCH')
        check("the search box is cleared by a pick", wm.cgml_search_balcony == "")
        bpy.ops.object.mode_set(mode='OBJECT')
        check("Done: hotkey operator does not poll again", not bpy.ops.cgml.tool_key.poll())

    print("\nB98:", "NOT satisfied" if FAILURES else "satisfied", FAILURES or "")
    sys.exit(1 if FAILURES else 0)


main()
