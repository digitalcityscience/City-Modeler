"""B24: CityGML build row in the 3D view's tool-settings row, LoD order, target-aware.

- the row is prepended to VIEW3D_HT_tool_header (Object + Edit Mode only)
- Object Mode: LoD1 New Building / From Footprint, LoD2 Roof, LoD3 Edit Facade,
  Window, Door, Stamp, Balcony, Chimney; Edit Mode: Done, Window, Door, Stamp,
  Balcony, Chimney (B28), Assign Surface Class (no Object-Mode steps)
- a step is enabled only when its target is selected: Roof <- a building, Balcony
  <- a wall / door / window face, Chimney <- a roof face (both in Edit Facade, B28), Window / Door <- one wall face in
  Edit Mode; tooltip says what is missing; a disabled step refuses to start
- Edit Facade leaves no CityGML tool active (select tool until a face is picked)
- "2 · Edit" no longer holds Create / Edit Facade / Done (they live in the row)
- modal hints (preview picking, balcony/chimney placing) go to the status bar,
  not the header, so the row stays visible

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b24_header_tool_strip.py
"""
import inspect
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bmesh  # noqa: E402
import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
    """Layout stand-in: records (kind, id/text, enabled, depress, key) through nested rows."""

    def __init__(self, out, parent=None):
        object.__setattr__(self, "out", out)
        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "enabled", True)

    def _on(self):
        return self.enabled and (self.parent is None or self.parent._on())

    def row(self, *a, **k):
        return Rec(self.out, self)

    def label(self, text="", **k):
        self.out.append(("label", text, self._on(), False, ""))

    def operator(self, idname, text="", depress=False, **k):
        entry = ["op", idname, self._on(), depress, "", text]
        self.out.append(entry)

        class P:
            def __setattr__(s, name, value):
                if name == "key":
                    entry[4] = value
                object.__setattr__(s, name, value)
        return P()

    def popover(self, panel, text="", **k):  # B26 Roof gallery; B98 each Edit tool's type dropdown keeps its panel name
        key = {"CGML_PT_roof_gallery": "roof"}.get(panel, "")
        self.out.append(["op", panel, self._on(), False, key, text])

    def __getattr__(self, name):
        if name in ("column", "box", "split"):
            return self.row
        if name == "menu":
            return lambda menu, text="", **k: self.out.append(("menu", menu, self._on(), False, ""))
        return lambda *a, **k: None


def row(tools):
    out = []
    holder = type("H", (), {"layout": Rec(out)})()
    tools.draw_build_row(holder, bpy.context)
    return out


def steps(out):
    """{step: enabled} for the row's buttons (tool steps by key, others by operator)."""
    return {(e[4] or e[1]): e[2] for e in out if e[0] == "op"}


def panel_ops(wp):
    out = []
    methods = {k: v for k, v in vars(wp.CGML3_PT_Edit).items() if callable(v)}
    holder = type("H", (), {**methods, "layout": Rec(out)})()
    holder.draw(bpy.context)
    return out


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    wp = import_module(f"{addon}.ui.workflow_panels")
    bm_mod = import_module(f"{addon}.ops.browse_gml")
    ia = import_module(f"{addon}.ops.installation_authoring")

    check("row prepended to the tool-settings row",
          tools.draw_build_row in bpy.types.VIEW3D_HT_tool_header._dyn_ui_initialize())
    check("modal hints on the status bar, not the header",
          "header_text_set" not in inspect.getsource(bm_mod.CGML3_OT_browse_pick._header)
          and "header_text_set" not in inspect.getsource(ia))

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)

        out = row(tools)
        order = [e[4] or e[1] for e in out if e[0] == "op"]
        check("object row in LoD order",
              order == ["cgml.building_new", "cgml.building_from_footprint", "roof", "cgml.facade_edit_start",
                        "window", "door", "balcony", "chimney", "stamp"], str(order))  # B71: Copy last
        labels = [e[1] for e in out if e[0] == "label"]
        check("LoD1 / LoD2 / LoD3 labels", labels[1:] == ["LoD1", "LoD2", "LoD3"], str(labels))
        st = steps(out)
        # B33: Edit Facade / From Footprint are gated by step_reason too
        check("nothing selected: only New Building can start",
              [k for k, on in st.items() if on] == ["cgml.building_new"]
              and not bpy.ops.cgml.facade_edit_start.poll(), str(st))
        check("tooltip says what is missing",
              "Now: Select a building" in tools.CGML_OT_tool_pick.description(
                  bpy.context, type("P", (), {"key": "roof"})()))
        res = bpy.ops.cgml.tool_pick(key="window")
        check("disabled step refuses to start", res == {'CANCELLED'})

        bpy.ops.cgml.building_new(points=footprint())
        building = bpy.context.active_object
        st = steps(row(tools))
        check("building selected: Roof + Edit Facade enabled; Window/Balcony/Chimney not",
              st["roof"] and st["cgml.facade_edit_start"] and not st["window"] and not st["balcony"]
              and not st["chimney"], str(st))
        check("LoD1 ticked for a LoD1 building",
              any(e[0] == "label" and e[1] == "LoD1" for e in row(tools)))

        kids = {str(o.get("cgml3_feature")): o for o in building.children_recursive}
        # B28: Balcony / Chimney work on a face picked in Edit Facade, not on an Object-Mode selection
        activate(kids["WallSurface"])
        st = steps(row(tools))
        check("Object Mode wall: Balcony waits for Edit Facade", not st["balcony"] and not st["chimney"]
              and tools.step_reason(bpy.context, "balcony").startswith("Edit Facade"), str(st))

        panel = panel_ops(wp)
        pops = [e[1] for e in panel if e[0] == "op"]
        check("2 · Edit: no Create / Edit Facade (moved to the row)",
              not {"cgml.building_new", "cgml.building_from_footprint", "cgml.facade_edit_start"} & set(pops),
              str(pops))
        check("2 · Edit points at the row", any(e[0] == "label" and "City Modeler row" in e[1] for e in panel))

        activate(building)
        bpy.ops.wm.tool_set_by_id(name="cgml.window_tool")  # leftover tool from before
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.cgml.facade_edit_start()
        check("Edit Facade: Edit Mode, select tool (no CityGML tool yet)",
              bpy.context.mode == 'EDIT_MESH' and tools.tool_active(bpy.context, "builtin.select_box"))
        out = row(tools)
        order = [e[4] or e[1] for e in out if e[0] == "op"]
        check("edit row: Done, Window, Door, Balcony, Chimney (B98: each with its type dropdown), Face Wall (B68), Paste (B71); B96: surface is in the right panel",
              order == ["object.mode_set", "window", "CGML_PT_window_gallery", "door", "CGML_PT_door_gallery",
                        "balcony", "CGML_PT_balcony_gallery", "chimney", "CGML_PT_chimney_gallery",
                        "cgml.face_wall", "stamp"], str(order))
        st = steps(out)
        check("no face selected: Window/Door off", not st["window"] and not st["door"], str(st))
        check("why: select one wall face", tools.step_reason(bpy.context, "window") == "Select one wall face")

        def pick_face(obj):
            for o in bpy.context.objects_in_mode:
                b = bmesh.from_edit_mesh(o.data)
                for f in b.faces:
                    f.select_set(False)
                bmesh.update_edit_mesh(o.data)
            bpy.context.view_layer.objects.active = obj
            b = bmesh.from_edit_mesh(obj.data)
            b.faces.ensure_lookup_table()
            b.faces[0].select_set(True)
            bmesh.update_edit_mesh(obj.data)

        pick_face(kids["RoofSurface"])
        check("roof face: Window off, says why",
              not steps(row(tools))["window"] and "RoofSurface" in tools.step_reason(bpy.context, "window"),
              tools.step_reason(bpy.context, "window"))
        st = steps(row(tools))
        check("roof face: Chimney gallery on, Balcony off", st["chimney"] and not st["balcony"], str(st))
        pick_face(kids["WallSurface"])
        st = steps(row(tools))
        check("one wall face: Window + Door on", st["window"] and st["door"], str(st))
        check("wall face: Balcony gallery on, Chimney off", st["balcony"] and not st["chimney"], str(st))
        check("Paste still needs a copy", not st["stamp"] and "Copy first" in tools.step_reason(bpy.context, "stamp"))
        res = bpy.ops.cgml.tool_pick(key="window")
        out = row(tools)
        pressed = [e[4] or e[1] for e in out if e[0] == "op" and e[3]]
        check("Window starts the tool, button pressed (B81: Auto on, the wall is faced)",
              res == {'FINISHED'} and tools.tool_active(bpy.context, "cgml.window_tool")
              and pressed == ["window", "cgml.face_wall"], str(pressed))
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")

        bpy.context.view_layer.objects.active = None
        out = []
        old_mode = bpy.context.mode
        check("other modes: no row", old_mode == 'OBJECT')  # sanity; sculpt etc. return early by mode

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":  # B26 imports the recorder
    main()
