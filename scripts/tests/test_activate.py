"""
Test 1: Add-on activation
Checks whether the add-on can be enabled without errors.

Usage:
  blender --background --python scripts/tests/test_activate.py
"""
import sys
import os
import importlib.util
import bpy

sys.path.insert(0, os.path.dirname(__file__))
from addon_test_utils import addon_module_candidates


def main():
    addon_module = None
    last_error = None

    for candidate in addon_module_candidates():
        if importlib.util.find_spec(candidate) is None:
            print(f"[TEST] SKIP: Add-on module not found: {candidate}")
            continue

        print(f"[TEST] Enabling add-on: {candidate}")
        try:
            bpy.ops.preferences.addon_enable(module=candidate)
        except Exception as e:
            last_error = e
            print(f"[TEST] ERROR during addon_enable ({candidate}): {e}")
            continue

        if candidate not in bpy.context.preferences.addons:
            last_error = f"enabled module was not registered in preferences: {candidate}"
            print(f"[TEST] ERROR: {last_error}")
            continue

        addon_module = candidate
        break

    if addon_module is None:
        print(f"[TEST] ERROR: No add-on module could be enabled: {last_error}")
        sys.exit(1)

    # Check whether the add-on is registered in preferences.
    if addon_module not in bpy.context.preferences.addons:
        print(f"[TEST] ERROR: Add-on '{addon_module}' not found in preferences after activation")
        sys.exit(1)

    # Check whether the import operator is available.
    if not hasattr(bpy.ops, "cgml3") or not hasattr(bpy.ops.cgml3, "import_gml_file"):
        print("[TEST] ERROR: Operator cgml3.import_gml_file is not available")
        sys.exit(1)

    # Check whether the export operator is available.
    if not hasattr(bpy.ops.cgml3, "export_gml_file"):
        print("[TEST] ERROR: Operator cgml3.export_gml_file is not available")
        sys.exit(1)

    print("[TEST] OK - Add-on enabled successfully, operators are available")
    sys.exit(0)


if __name__ == "__main__":
    main()
