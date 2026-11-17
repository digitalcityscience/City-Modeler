"""B75: City Modeler view - preference on by default, top-bar toggle registered, nothing done headless.

The workspace layout itself (Outliner | 3D view + CityGML sidebar | Properties) needs a window and
was verified in the live Blender (MCP); background Blender has no windows.

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b75_city_modeler_view.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def main():
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    view = import_module(f"{addon}.ui.city_modeler_view")
    prefs = bpy.context.preferences.addons[addon].preferences
    check("preference exists and is on by default", prefs.city_modeler_view is True)
    check("enabled() follows the preference", view.enabled())
    check("toggle drawn in the top bar", view.draw_toggle in bpy.types.TOPBAR_HT_upper_bar._dyn_ui_initialize())
    check("load_post handler registered", view._on_load_post in bpy.app.handlers.load_post)
    check("apply() does nothing headless", view.apply() is None and view.NAME not in bpy.data.workspaces)
    header = import_module(f"{addon}.ui.header_panel")
    check("sidebar header says City Modeler", 'text="City Modeler"' in open(header.__file__, encoding="utf-8").read())
    if FAILURES:
        raise SystemExit(f"FAILED: {FAILURES}")
    print("ALL OK")


main()
