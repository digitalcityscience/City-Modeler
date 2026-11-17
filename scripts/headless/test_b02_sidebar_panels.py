"""B02: every CityGML sidebar panel draws without errors in the main states.

Headless Blender has no UI to draw into, so each registered panel's poll()
and draw() run against a recording layout that checks what a real layout
would reject: unknown operator ids or operator properties, missing data
properties, unknown icons and menus. Also checks the sidebar order
(status, Open, Edit, Save, More Tools) and that the specialist panels
sit under More Tools.

States: empty scene; imported LoD3 building (building active, a window
active); the same building in Edit Mode via "Add Window / Door"; a plain
cube (not CityGML).

Usage (isolated profile, see tickets/README.md):
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b02_sidebar_panels.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"[PASS] {label}")
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


class OpProps:
    def __init__(self, idname, problems):
        object.__setattr__(self, "_names", set(_op_rna(idname).properties.keys()) if _op_rna(idname) else set())
        object.__setattr__(self, "_idname", idname)
        object.__setattr__(self, "_problems", problems)

    def __setattr__(self, name, value):
        if name not in self._names:
            self._problems.append(f"operator {self._idname} has no property '{name}'")


def _op_rna(idname):
    try:
        module, name = idname.split(".")
        return getattr(getattr(bpy.ops, module), name).get_rna_type()
    except Exception:
        return None


class Layout:
    """Duck-typed UILayout that records problems instead of drawing."""

    def __init__(self, problems):
        object.__setattr__(self, "_problems", problems)
        object.__setattr__(self, "labels", [])

    def __setattr__(self, name, value):  # scale_y, enabled, alert, active, ...
        pass

    def _icon(self, kw):
        icon = kw.get("icon")
        if icon is not None and icon not in ICONS:
            self._problems.append(f"unknown icon {icon}")

    def _child(self, *a, **kw):
        child = Layout(self._problems)
        object.__setattr__(child, "labels", self.labels)
        return child

    column = row = box = split = grid_flow = column_flow = _child

    def panel(self, *a, **kw):  # B95/B101: ``header, body = layout.panel(...)``, drawn open
        return self._child(), self._child()

    def label(self, text="", **kw):
        self._icon(kw)
        self.labels.append(text)

    def operator(self, idname, **kw):
        self._icon(kw)
        if _op_rna(idname) is None:
            self._problems.append(f"unknown operator {idname}")
        return OpProps(idname, self._problems)

    def template_icon_view(self, data, name, **kw):
        self.prop(data, name)

    def operator_menu_enum(self, idname, prop, **kw):
        self._icon(kw)
        rna = _op_rna(idname)
        if rna is None or prop not in rna.properties.keys():
            self._problems.append(f"operator {idname} has no enum '{prop}'")

    def prop(self, data, name, **kw):
        self._icon(kw)
        if data is None or name not in data.bl_rna.properties.keys():
            self._problems.append(f"no property '{name}' on {data!r}")

    def prop_search(self, data, name, search_data, search_name, **kw):
        self.prop(data, name)
        self.prop(search_data, search_name)

    def menu(self, menu, **kw):
        self._icon(kw)
        if not hasattr(bpy.types, menu):
            self._problems.append(f"unknown menu {menu}")

    def menu_contents(self, menu):
        cls = getattr(bpy.types, menu, None)
        if cls is None:
            self._problems.append(f"unknown menu {menu}")
            return
        fake = type("M", (), {"layout": self})()
        cls.draw(fake, bpy.context)

    def template_list(self, list_type, list_id, data, name, active_data, active_name, **kw):
        if not hasattr(bpy.types, list_type):
            self._problems.append(f"unknown UIList {list_type}")
        self.prop(data, name)
        self.prop(active_data, active_name)

    def separator(self, *a, **kw):
        pass


class FakePanel:
    """Stands in for a panel instance; other attributes come from the class."""

    def __init__(self, cls, layout):
        self.layout = layout
        self._cls = cls

    def __getattr__(self, name):
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


def sidebar_panels():
    return [c for c in bpy.types.Panel.__subclasses__()
            if getattr(c, "is_registered", False) and getattr(c, "bl_category", "") == "Tool"
            and ADDON in c.__module__]  # B89: the Tool tab also holds Blender's own panels


def draw_all(state):
    for tab in ("OPEN", "EDIT", "SAVE"):  # B96: a step panel draws only on its tab
        bpy.context.window_manager.cgml_panel_tab = tab
        _draw_tab(f"{state}/{tab}")
    bpy.context.window_manager.cgml_panel_tab = "OPEN"


def _draw_tab(state):
    """poll() + draw() (+ headers) of every registered CityGML panel."""
    for cls in sidebar_panels():
        problems = []
        try:
            visible = cls.poll(bpy.context) if hasattr(cls, "poll") else True
            if visible:
                for method in ("draw_header", "draw_header_preset", "draw"):
                    if hasattr(cls, method):
                        getattr(cls, method)(FakePanel(cls, Layout(problems)), bpy.context)
        except Exception as e:
            problems.append(f"{type(e).__name__}: {e}")
        check(f"[{state}] {cls.bl_label} draws cleanly", not problems, "; ".join(problems[:4]))


def edit_labels():
    layout = Layout([])
    bpy.types.CGML3_PT_Edit.draw(FakePanel(bpy.types.CGML3_PT_Edit, layout), bpy.context)
    return layout.labels


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    # --- structure ---------------------------------------------------------
    # B90: the status strip is the one top-level panel; the steps are its children, in bl_order
    top = sorted((c for c in sidebar_panels() if getattr(c, "bl_parent_id", "") in ("", "VCS3DCITYDB_PT_header")),
                 key=lambda c: getattr(c, "bl_order", 0))
    labels = [c.bl_label for c in top if c.bl_idname != "CGML3_PT_Reload"]
    check("sidebar order: status, Open, Edit, Save, More Tools",
          labels == ["CityGML", "Open", "Edit", "Save", "More Tools"], f"got {labels}")
    wm = bpy.context.window_manager  # B96: one step panel per tab, Assign Surface Class under Edit
    for tab, panel in (("OPEN", "CGML3_PT_Main"), ("EDIT", "CGML3_PT_Edit"), ("SAVE", "CGML3_PT_Save")):
        wm.cgml_panel_tab = tab
        shown = [p for p in ("CGML3_PT_Main", "CGML3_PT_Edit", "CGML3_PT_Save") if getattr(bpy.types, p).poll(bpy.context)]
        check(f"tab {tab} shows only its step", shown == [panel], str(shown))
    wm.cgml_panel_tab = "OPEN"
    check("Assign Surface Class is a panel of the Edit tab",
          bpy.types.CGML3_PT_AssignSurface.bl_parent_id == "CGML3_PT_Edit")
    tools = {c.bl_label for c in sidebar_panels() if getattr(c, "bl_parent_id", "") == "CGML3_PT_Tools"}
    # B17: Viewport Performance, Join Object Parts, Openings Cutter, Cut Openings (Display) removed
    expected_tools = {"Presets", "Surface Types (ModelTyper)", "Ring / Opening Sync"}
    check("specialist panels are under More Tools", tools == expected_tools, f"got {sorted(tools)}")
    check("B97 Reload Extension: bottom of the Open tab only",
          bpy.types.CGML3_PT_Reload.poll(bpy.context) and bpy.types.CGML3_PT_Reload.bl_order == 999)
    wm.cgml_panel_tab = "EDIT"
    check("B97 Reload Extension: not on the Edit tab", not bpy.types.CGML3_PT_Reload.poll(bpy.context))
    wm.cgml_panel_tab = "OPEN"
    from city_modeler.ui import gallery
    items = gallery.roof_items(None, bpy.context)
    # Background Blender gives previews icon_id 0 (no GPU); the live check is in B06's ticket.
    from city_modeler.shared.roof_shapes import ROOF_TYPES
    check("roof gallery: every roof type, each thumbnail loaded",
          len(items) == len(ROOF_TYPES) and all(f"roof_{i[0]}" in gallery._previews for i in items),
          str(sorted(gallery._previews.keys())))

    # --- states ------------------------------------------------------------
    draw_all("empty scene")

    res = bpy.ops.cgml3.browse_gml_file(filepath=str(FIXTURE))
    check("fixture browse finishes", res == {'FINISHED'})
    bpy.ops.cgml3.browse_select(action='ALL')
    draw_all("browse preview")  # B12: Show / Unshow block in 1 · Open
    bpy.ops.cgml3.browse_close()

    res = bpy.ops.cgml3.import_gml_file(filepath=str(FIXTURE))
    check("fixture import finishes", res == {'FINISHED'})
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    for o in bpy.data.objects:
        o.select_set(False)
    building.select_set(True)
    bpy.context.view_layer.objects.active = building
    draw_all("building selected")
    text = " | ".join(edit_labels())
    check("Edit card names the building and its LoD", "Building: " in text and "LoD3" in text, text[:200])

    window = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "WindowSurface")
    bpy.context.view_layer.objects.active = window
    draw_all("window selected")
    # B21: window actions moved to right-click (CityGML) and the Window tool header
    check("card names the selected window", any(t.startswith("Selected: WindowSurface") for t in edit_labels()))

    bpy.context.view_layer.objects.active = building
    res = bpy.ops.cgml.facade_edit_start()
    check("Add Window / Door enters Edit Mode", res == {'FINISHED'} and building.mode == 'EDIT',
          f"result={res} mode={building.mode}")
    in_edit = [o for o in bpy.data.objects if o.mode == 'EDIT']
    check("the whole building is in Edit Mode (walls included)",
          any(o.get("cgml3_feature") == "WallSurface" for o in in_edit), f"{len(in_edit)} in edit")
    draw_all("edit mode")
    # B24: Window / Door / Stamp / Surface Type are in the City Modeler row above the viewport
    check("Edit Mode points at the City Modeler row", any("City Modeler row" in t for t in edit_labels()))
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    draw_all("plain mesh")
    check("plain mesh is offered Make CityGML", any("not a city object yet" in t for t in edit_labels()))
    res = bpy.ops.cgml.make_citygml()
    check("Make CityGML turns the cube into a Building",
          res == {'FINISHED'} and cube.get("cgml3_feature") == "Building", f"got {cube.get('cgml3_feature')!r}")

    if FAILURES:
        print(f"\n[FAIL] B02 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B02 sidebar panels")


main()
