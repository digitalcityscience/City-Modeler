r"""B89: the City Modeler panels live in the "Tool" category, so Blender shows them in the Properties
editor's Tool tab (and in the sidebar's Tool tab); they must work with a Properties area as context.

- every panel of the add-on that sits in a 3D-view sidebar is in the "Tool" category; none is left
  in a "CityGML" tab; the top-level order is still status, Open, Edit, Save, More Tools
- with the Properties editor as context (area PROPERTIES, no region_data), in the states empty
  scene / building / wall / window / installation / Edit Mode / plain mesh: every panel polls and
  draws without an error
- buttons pressed from there work: Make CityGML, theme view, Check Planarity, attribute card

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\cgml-b01\bscripts blender -b --factory-startup --python-use-system-env \
      --python-exit-code 1 --python scripts/headless/test_b89_panels_in_tool_tab.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402


class Recorder:
    """A layout that accepts every call; ``operator`` returns an object that accepts every property."""
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def __setattr__(self, _name, _value):
        pass

    def __iter__(self):  # B95/B101: ``header, body = layout.panel(...)``
        return iter((self, self))


class Title(Recorder):
    """Records label texts."""
    def __init__(self):
        object.__setattr__(self, "labels", [])

    def label(self, text="", **_k):
        self.labels.append(text)
        return self


def panels(addon):
    return [c for c in bpy.types.Panel.__subclasses__() if getattr(c, "is_registered", False)
            and addon in c.__module__ and getattr(c, "bl_space_type", "") == 'VIEW_3D'
            and getattr(c, "bl_region_type", "") == 'UI']


def draw_all(state, addon):
    for tab in ("OPEN", "EDIT", "SAVE"):  # B96: a step panel draws only on its tab
        bpy.context.window_manager.cgml_panel_tab = tab
        _draw_tab(f"{state}/{tab}", addon)
    bpy.context.window_manager.cgml_panel_tab = "OPEN"


def _draw_tab(state, addon):
    for cls in panels(addon):
        try:
            if not hasattr(cls, "poll") or cls.poll(bpy.context):
                for method in ("draw_header", "draw"):
                    if hasattr(cls, method):
                        fake = type("P", (), {"layout": Recorder(), "bl_label": cls.bl_label})()
                        for name in dir(cls):  # helper methods of the panel class
                            if name.startswith("_draw") and callable(getattr(cls, name)):
                                setattr(type(fake), name, getattr(cls, name))
                        getattr(cls, method)(fake, bpy.context)
            problem = ""
        except Exception as e:  # noqa: BLE001
            problem = f"{type(e).__name__}: {e}"
        check(f"[{state}] {cls.bl_label} polls and draws in the Properties editor", not problem, problem)


def select(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    hp = import_module(f"{addon}.ui.header_panel")
    ours = panels(addon)
    check("the add-on has sidebar panels", len(ours) >= 15, str(len(ours)))
    cats = {getattr(c, "bl_category", "") for c in ours}
    check('every one is in the "Tool" category', cats == {"Tool"}, str(cats))
    roots = [c.bl_idname for c in ours if not getattr(c, "bl_parent_id", "")]
    check("B90: one top-level panel, the header-less status strip (it stays above Blender's own panels)",
          roots == ["VCS3DCITYDB_PT_header"] and 'HIDE_HEADER' in bpy.types.VCS3DCITYDB_PT_header.bl_options, str(roots))
    top = sorted((c for c in ours if getattr(c, "bl_parent_id", "") in ("", "VCS3DCITYDB_PT_header")),
                 key=lambda c: getattr(c, "bl_order", 0))
    labels = [c.bl_label for c in top if c.bl_idname != "CGML3_PT_Reload"]
    check("order: status, Open, Edit, Save, More Tools",
          labels == ["CityGML", "Open", "Edit", "Save", "More Tools"], str(labels))

    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type == 'PROPERTIES'), None)
    check("the start screen has a Properties editor", area is not None)
    if area is None:
        return
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=win, area=area, region=region):
        check("context is the Properties editor", bpy.context.area.type == 'PROPERTIES'
              and bpy.context.region_data is None)
        check("B90: the panels show here", bpy.types.VCS3DCITYDB_PT_header.poll(bpy.context))
        title = Title()
        hp.draw_title(type("P", (), {"layout": title})(), bpy.context)
        check("B90: the tab starts with the City Modeler title and says what the tool name is",
              title.labels[:1] == ["City Modeler"] and any("Active tool" in t for t in title.labels), str(title.labels))
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        draw_all("empty scene", addon)
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        draw_all("plain mesh", addon)
        res = bpy.ops.cgml.make_citygml()
        check("Make CityGML from the Properties editor", res == {'FINISHED'} and cube.get("cgml3_feature") == "Building",
              f"{res} {cube.get('cgml3_feature')}")

    view = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=view, region=next(r for r in view.regions if r.type == 'UI')):
        title = Title()
        hp.draw_title(type("P", (), {"layout": title})(), bpy.context)
        check("B90: nothing of it in the 3D view's sidebar (one place only)",
              not bpy.types.VCS3DCITYDB_PT_header.poll(bpy.context) and not title.labels, str(title.labels))
    check("B90: the title is hooked into the active-tool panel the Properties editor shows first",
          any(getattr(f, "__name__", "") == "draw_title"
              for f in bpy.types.VIEW3D_PT_active_tool_duplicate.draw._draw_funcs))

    b = author_everything()  # its tools need the 3D view, as in the real UI
    kids = list(b.children_recursive)
    with bpy.context.temp_override(window=win, area=area, region=region):
        for label, pick in (("building", lambda o: o is b),
                            ("wall", lambda o: o.get("cgml3_feature") == "WallSurface"),
                            ("window", lambda o: o.get("cgml3_feature") == "WindowSurface"),
                            ("installation", lambda o: o.get("cgml3_feature") == "BuildingInstallation")):
            select(next(o for o in [b] + kids if pick(o)))
            draw_all(label, addon)
        wm = bpy.context.window_manager
        wm.cgml_elem_1 = "1030"
        check("attribute card writes from the Properties editor", bpy.context.active_object.get("bldg:function") == "1030")
        select(b)
        for op, kwargs in ((bpy.ops.cgml.check_planarity, {}), (bpy.ops.cgml.theme_view, {})):
            try:
                res = op(**kwargs) if op.poll() else "poll failed"
            except Exception as e:  # noqa: BLE001
                res = f"{type(e).__name__}: {e}"
            check(f"{op.idname()} from the Properties editor", res == {'FINISHED'}, str(res))
        wall = next(o for o in kids if o.get("cgml3_feature") == "WallSurface")
        select(wall)
        bpy.ops.object.mode_set(mode='EDIT')
        draw_all("edit mode", addon)
        bpy.ops.object.mode_set(mode='OBJECT')

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
