"""B17: More Tools cleanup (removal only).

- More Tools holds exactly Presets, Surface Types (ModelTyper), Ring / Opening Sync
- removed operators are gone (viewport performance, cut openings display)
- object right-click menu has no Material Preview entry; Join stays
- ModelTyper panel: Mapping + Analyze only (no auto/manual assign buttons),
  feature types limited to Building / Transportation / CityFurniture
- the operators the new tools reuse are still registered (auto/manual assign)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b17_more_tools_cleanup.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
    """Layout stand-in recording operator idnames and labels."""

    def __init__(self, ops):
        self.ops = ops

    def __getattr__(self, name):
        if name in ("row", "column", "box", "split", "grid_flow", "column_flow"):
            return lambda *a, **k: Rec(self.ops)
        if name in ("operator", "operator_menu_enum"):
            def op(idname, *a, **k):
                self.ops.append(idname)
                return type("P", (), {})()
            return op
        return lambda *a, **k: None

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


def op_registered(idname):
    mod, name = idname.split(".")
    return hasattr(getattr(bpy.ops, mod), name) and getattr(getattr(bpy.ops, mod), name).idname() != ""


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)

    panels = [c for c in bpy.types.Panel.__subclasses__()
              if getattr(c, "is_registered", False) and getattr(c, "bl_category", "") == "Tool"
              and addon in c.__module__]  # B89
    tools = {c.bl_label for c in panels if getattr(c, "bl_parent_id", "") == "CGML3_PT_Tools"}
    check("More Tools has three panels", tools == {"Presets", "Surface Types (ModelTyper)", "Ring / Opening Sync"},
          str(sorted(tools)))
    gone = {"Viewport Performance", "Join Object Parts", "Openings Cutter", "Cut Openings (Display)",
            "Surface Types Info"}
    check("removed panels are not registered", not (gone & {c.bl_label for c in panels}))

    for idname in ("cgml3.cut_openings_display", "cgml3.optimize_viewport", "cgml3.restore_viewport",
                   "cgml3.material_preview_selection", "cgml3.quick_local_view"):
        check(f"{idname} removed", not hasattr(bpy.types, idname.replace(".", "_OT_").upper())
              and idname.split(".")[1] not in dir(bpy.ops.cgml3))
    for idname in ("cgml3_modeltyper.auto_assign", "cgml3_modeltyper.manual_assign",
                   "cgml3_modeltyper.apply_property_mapping", "cgml3_modeltyper.analyze_model",
                   "cgml3.join_object_parts"):
        check(f"{idname} still registered", idname.split(".")[1] in dir(getattr(bpy.ops, idname.split(".")[0])))

    funcs = [getattr(f, "__module__", "") + "." + f.__name__
             for f in bpy.types.VIEW3D_MT_object_context_menu._dyn_ui_initialize()]
    check("no Material Preview in the object right-click menu",
          not any("material_preview" in f or "viewport_performance" in f for f in funcs), str(funcs))
    # B21: Join sits in the "CityGML" submenu, which draws join_object_parts' own entry
    check("Join stays in the object right-click menu (CityGML submenu)",
          any("draw_object_context_menu" in f for f in funcs) and "draw_join_object_parts_context_menu"
          in bpy.types.CGML_MT_object_context.draw.__code__.co_names)

    props = bpy.context.scene.cgml3_modeltyper_props
    from importlib import import_module
    mt_props = import_module(f"{addon}.modeltyper.ui.properties")
    ids = [i[0] for i in mt_props.get_feature_type_items(props, bpy.context)]
    check("feature types: Building, Transportation, CityFurniture",
          ids == ["BUILDING", "TRANSPORTATION", "CITYFURNITURE"], str(ids))

    ops = []
    panel = bpy.types.CGML3_MODELTYPER_PT_main_panel
    panel.draw(type("S", (), {"layout": Rec(ops)})(), bpy.context)
    check("ModelTyper panel: mapping + analyze",
          "cgml3_modeltyper.apply_property_mapping" in ops and "cgml3_modeltyper.analyze_model" in ops, str(ops))
    check("ModelTyper panel: no auto/manual assign buttons",
          "cgml3_modeltyper.auto_assign" not in ops and "cgml3_modeltyper.manual_assign" not in ops)

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
