"""
Test: CityGML import for all feature-type fixture folders.
Scans test_files/ for subfolders with .gml files and imports each file separately.
Each file is imported into a fresh Blender scene.

Usage:
  blender --background --addons city_modeler --python scripts/tests/test_import.py -- --dir test_files
"""
import argparse
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(__file__))
from addon_test_utils import ensure_addon_enabled


def _extract_blender_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1:]


def _collect_gml_files(base_dir: str) -> list[str]:
    """Collect all .gml files from direct subfolders of base_dir."""
    gml_files = []
    for entry in sorted(os.listdir(base_dir)):
        subdir = os.path.join(base_dir, entry)
        if not os.path.isdir(subdir):
            continue
        # Skip generated appearance folders.
        if entry.lower() == "appearance":
            continue
        for fname in sorted(os.listdir(subdir)):
            if fname.lower().endswith(".gml"):
                gml_files.append(os.path.join(subdir, fname))
    return gml_files


def _reset_scene():
    """Reset Blender to an empty scene before each import."""
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _import_single_file(filepath: str) -> tuple[bool, str]:
    """Import a single GML file and return (success, message)."""
    _reset_scene()

    ok, msg = ensure_addon_enabled()
    if not ok:
        return False, msg

    # Disable XSD validation for fixture import tests.
    bpy.context.scene.cgml3.xsd_validate = False

    objects_before = len(bpy.data.objects)

    try:
        result = bpy.ops.cgml3.import_gml_file(
            "EXEC_DEFAULT",
            filepath=filepath,
            import_appearance=True,
        )
    except RuntimeError as e:
        return False, f"Import exception: {e}"

    if "FINISHED" not in result:
        return False, f"Import did not finish: {result}"

    imported_count = len(bpy.data.objects) - objects_before
    if imported_count <= 0:
        return False, "No objects imported"

    return True, f"{imported_count} object(s) imported"


def main():
    parser = argparse.ArgumentParser(description="CityGML import test for all feature types")
    parser.add_argument("--dir", required=True, help="Base directory with feature-type subfolders")
    args = parser.parse_args(_extract_blender_args())

    base_dir = os.path.abspath(args.dir)
    if not os.path.isdir(base_dir):
        print(f"[TEST] ERROR: Directory not found: {base_dir}")
        sys.exit(1)

    gml_files = _collect_gml_files(base_dir)
    if not gml_files:
        print(f"[TEST] ERROR: No .gml files found below subfolders of {base_dir}")
        sys.exit(1)

    print(f"[TEST] Import test: {len(gml_files)} files found")
    print("=" * 60)

    passed = 0
    failed = 0
    failures = []

    for filepath in gml_files:
        rel_path = os.path.relpath(filepath, base_dir)
        success, msg = _import_single_file(filepath)
        if success:
            passed += 1
            print(f"  PASS  {rel_path} ({msg})")
        else:
            failed += 1
            failures.append((rel_path, msg))
            print(f"  FAIL  {rel_path} ({msg})")

    print("=" * 60)
    print(f"[TEST] Result: {passed} passed, {failed} failed, {passed + failed} total")

    if failures:
        print("\n[TEST] Failed files:")
        for rel_path, msg in failures:
            print(f"  - {rel_path}: {msg}")
        sys.exit(1)

    print("[TEST] OK - All imports succeeded")
    sys.exit(0)


if __name__ == "__main__":
    main()
