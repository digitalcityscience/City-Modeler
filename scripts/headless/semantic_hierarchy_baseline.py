import argparse
import importlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy


ADDON_MODULE = "bl_ext.user_default.city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "semantic_hierarchy_baseline.json"
FIXTURES = {
    "citygml2": {
        "version": "2.0",
        "path": REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML2_Building_only.gml",
    },
    "citygml3": {
        "version": "3.0",
        "path": REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml",
    },
}
COUNT_NAMES = {
    "cityObjectMember",
    "Building",
    "WallSurface",
    "Window",
    "WindowSurface",
    "Door",
    "DoorSurface",
    "BuildingInstallation",
    "Polygon",
    "interior",
}
PROPERTY_KEY_PARTS = (
    "cgml",
    "feature",
    "type",
    "id",
    "parent",
    "opening",
    "surface",
)


def _extract_blender_args() -> list[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1 :]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantic hierarchy baseline import/export harness for the Railway LoD3 building fixtures."
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for exported GML and JSON evidence. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--baseline",
        default=str(BASELINE_PATH),
        help="Expected baseline JSON to compare against.",
    )
    return parser.parse_args(_extract_blender_args())


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _count_elements(path: Path) -> dict[str, int]:
    counts = {name: 0 for name in sorted(COUNT_NAMES)}
    root = ET.parse(path).getroot()
    for elem in root.iter():
        local = _local_name(str(elem.tag))
        if local in counts:
            counts[local] += 1
    return counts


def _interesting_properties(obj: bpy.types.Object) -> dict[str, str]:
    props = {}
    for key in obj.keys():
        lower_key = key.lower()
        if any(part in lower_key for part in PROPERTY_KEY_PARTS):
            props[key] = str(obj[key])
    return props


def _object_probe() -> dict[str, object]:
    objects = sorted(bpy.data.objects, key=lambda obj: obj.name)
    return {
        "object_count": len(objects),
        "objects": [
            {
                "name": obj.name,
                "type": obj.type,
                "parent": obj.parent.name if obj.parent else None,
                "properties": _interesting_properties(obj),
            }
            for obj in objects
        ],
        "parent_edges": [
            {"child": obj.name, "parent": obj.parent.name}
            for obj in objects
            if obj.parent is not None
        ],
    }


def _enable_installed_extension() -> dict[str, str]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON_MODULE)
    module = importlib.import_module(ADDON_MODULE)
    module_file = Path(module.__file__).resolve()
    user_resources = os.environ.get("BLENDER_USER_RESOURCES", "")
    if user_resources:
        user_root = Path(user_resources).resolve()
        if user_root not in module_file.parents:
            raise RuntimeError(f"Loaded add-on is outside BLENDER_USER_RESOURCES: {module_file}")
    return {
        "module": ADDON_MODULE,
        "file": str(module_file),
        "source_tree": str(REPO_ROOT.resolve()),
        "blender_user_resources": user_resources,
    }


def _run_fixture(label: str, fixture: dict[str, object], output_dir: Path) -> dict[str, object]:
    loaded = _enable_installed_extension()
    scene_props = bpy.context.scene.cgml3
    scene_props.xsd_validate = False

    input_path = Path(fixture["path"])
    version = str(fixture["version"])
    import_result = bpy.ops.cgml3.import_gml_file(
        "EXEC_DEFAULT",
        filepath=str(input_path),
        import_appearance=True,
    )
    if "FINISHED" not in import_result:
        raise RuntimeError(f"{label}: import did not finish: {set(import_result)}")

    probe = _object_probe()
    export_path = output_dir / f"{input_path.stem}_semantic_baseline_export.gml"
    export_result = bpy.ops.cgml3.export_gml_file(
        "EXEC_DEFAULT",
        filepath=str(export_path),
        citygml_version=version,
    )
    if "FINISHED" not in export_result:
        raise RuntimeError(f"{label}: export did not finish: {set(export_result)}")
    if not export_path.is_file() or export_path.stat().st_size == 0:
        raise RuntimeError(f"{label}: export file missing or empty: {export_path}")

    return {
        "input": str(input_path),
        "citygml_version": version,
        "loaded_addon": loaded,
        "import_result": sorted(import_result),
        "export_result": sorted(export_result),
        "export_path": str(export_path),
        "input_counts": _count_elements(input_path),
        "export_counts": _count_elements(export_path),
        "import_probe": probe,
    }


def _compare_counts(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    errors = []
    for label, expected_fixture in expected["fixtures"].items():
        expected_counts = expected_fixture["expected_counts"]
        actual_counts = actual["fixtures"][label]["export_counts"]
        for key, expected_value in expected_counts.items():
            got = actual_counts.get(key, 0)
            if got != expected_value:
                errors.append(f"{label}: {key} expected {expected_value}, got {got}")
    return errors


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="cgml_semantic_baseline_"))
    output_dir.mkdir(parents=True, exist_ok=True)

    actual = {
        "repo_root": str(REPO_ROOT.resolve()),
        "fixtures": {
            label: _run_fixture(label, fixture, output_dir)
            for label, fixture in FIXTURES.items()
        },
    }

    baseline_path = Path(args.baseline)
    expected = json.loads(baseline_path.read_text(encoding="utf-8"))
    errors = _compare_counts(actual, expected)

    evidence_path = output_dir / "semantic_hierarchy_baseline_evidence.json"
    evidence_path.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[semantic-baseline] repo_root={actual['repo_root']}")
    for label, result in actual["fixtures"].items():
        loaded = result["loaded_addon"]
        print(f"[semantic-baseline] {label} addon={loaded['module']} file={loaded['file']}")
        print(f"[semantic-baseline] {label} export={result['export_path']}")
        print(f"[semantic-baseline] {label} counts={json.dumps(result['export_counts'], sort_keys=True)}")
        print(f"[semantic-baseline] {label} object_count={result['import_probe']['object_count']}")
    print(f"[semantic-baseline] evidence={evidence_path}")

    if errors:
        for error in errors:
            print(f"[semantic-baseline] ERROR: {error}")
        raise SystemExit(1)

    print("[semantic-baseline] baseline counts reproduced")


if __name__ == "__main__":
    main()
