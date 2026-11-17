"""
Validate generated add-on ZIP bundles.

Usage:
  python scripts/tests/check_bundle_contents.py --addon-name city_modeler --version 1.2.3
"""
import argparse
import importlib.util
import re
import sys
import tomllib
import zipfile
from pathlib import Path


FORBIDDEN_PARTS = {
    ".git",
    ".cache",
    "__pycache__",
    "scripts",
    "devops-how-to-master",
    "test_files",
}
FORBIDDEN_FILENAMES = {
    ".gitignore",
    ".gitlab-ci.yml",
    "dependencies.yaml",
}
# The add-on only talks to 3DCityDB 5.x; the old v4 Java importer/exporter must not come back.
REMOVED_V4_DIRS = {"3dcitydb-tool-v4"}
REMOVED_V4_FILENAMES = {"citydb_v4_cli.py"}
REMOVED_V4_JAR = re.compile(r"^impexp-client-cli.*\.jar$", re.IGNORECASE)
CITYDB_TOOL_PREFIX = "citydb-tool-"


ROOT = Path(__file__).resolve().parents[2]


def _load_fetch_deps():
    spec = importlib.util.spec_from_file_location("fetch_deps_for_bundle_check", ROOT / "scripts/fetch_deps.py")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("could not load scripts/fetch_deps.py")
    spec.loader.exec_module(module)
    return module


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


def _normalized_parts(name: str) -> tuple[str, ...]:
    return tuple(part for part in name.replace("\\", "/").split("/") if part)


def _contains_dir(names: list[str], dirname: str) -> bool:
    return any(dirname in _normalized_parts(name) for name in names)


def _contains_file(names: list[str], filename: str) -> bool:
    return any(_normalized_parts(name)[-1:] == (filename,) for name in names)


def _contains_glob_suffix(names: list[str], suffix: str) -> bool:
    return any(name.lower().endswith(suffix.lower()) for name in names)


def _manifest_wheels(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    wheels = data.get("wheels", [])
    if not isinstance(wheels, list):
        raise ValueError(f"{path}: wheels must be an array")
    return [Path(str(wheel).replace("\\", "/")).name for wheel in wheels]


def _citydb_tool_dir(path: Path) -> str | None:
    """Folder name citydb-tool is unpacked to (e.g. citydb-tool-1.3.0), if dependencies.yaml declares it."""
    fetch_deps = _load_fetch_deps()
    tool = (fetch_deps.load_manifest(path).get("tools") or {}).get("citydb-tool")
    if not tool:
        return None
    return str(tool["target_dir"]).replace("{version}", str(tool["version"]))


def _dependency_matrix(path: Path) -> tuple[list[str], list[str], list[str]]:
    fetch_deps = _load_fetch_deps()
    data = fetch_deps.load_manifest(path)
    modules = [str(module["name"]) for module in data.get("python_modules", [])]
    python = data.get("python", {})
    versions = [str(version) for version in python.get("versions", [])]
    platforms = [str(platform) for platform in python.get("platforms", [])]
    if not modules:
        raise ValueError(f"{path}: python_modules must list at least one module")
    if not versions:
        raise ValueError(f"{path}: python.versions must list at least one version")
    if not platforms:
        raise ValueError(f"{path}: python.platforms must list at least one platform")
    return modules, versions, platforms


def _wheel_names_in_zip(names: list[str]) -> list[str]:
    wheels = []
    for name in names:
        parts = _normalized_parts(name)
        if len(parts) >= 2 and "wheels" in parts and parts[-1].lower().endswith(".whl"):
            wheels.append(parts[-1])
    return wheels


# A wheel's filename tags deliberately do not echo the platform requested for it:
# pip answers `--platform macosx_14_0_arm64` with a macosx_10_9_universal2 wheel and
# `--platform manylinux_2_28_x86_64` with a manylinux_2_26_x86_64.manylinux_2_28_x86_64
# one (see scripts/fetch_deps.py). Coverage is therefore decided by tag compatibility,
# not string equality.
_MACOS_TAG = re.compile(r"^macosx_(\d+)_(\d+)_(.+)$")
_MANYLINUX_TAG = re.compile(r"^manylinux_(\d+)_(\d+)_(.+)$")
_LEGACY_MANYLINUX_TAG = re.compile(r"^(manylinux1|manylinux2010|manylinux2014)_(.+)$")
_LEGACY_MANYLINUX_GLIBC = {
    "manylinux1": (2, 5),
    "manylinux2010": (2, 12),
    "manylinux2014": (2, 17),
}
# macOS wheel architectures that satisfy a requested architecture. universal2 and the
# older fat binaries ship several slices in one wheel.
_MACOS_ARCH_PROVIDERS = {
    "x86_64": {"x86_64", "universal2", "intel", "fat64", "fat3", "universal"},
    "arm64": {"arm64", "universal2"},
}


def _split_platform_tag(tag: str) -> tuple[str, tuple[int, int], str] | None:
    """Split a platform tag into (os, minimum OS version, architecture)."""
    match = _MACOS_TAG.match(tag)
    if match:
        return "macosx", (int(match.group(1)), int(match.group(2))), match.group(3)
    match = _MANYLINUX_TAG.match(tag)
    if match:
        return "manylinux", (int(match.group(1)), int(match.group(2))), match.group(3)
    match = _LEGACY_MANYLINUX_TAG.match(tag)
    if match:
        return "manylinux", _LEGACY_MANYLINUX_GLIBC[match.group(1)], match.group(2)
    return None


def _platform_tag_satisfies(wheel_tag: str, target: str) -> bool:
    """Whether a wheel carrying wheel_tag runs on the declared target platform."""
    if wheel_tag == "any" or wheel_tag == target:
        return True

    wheel_parts = _split_platform_tag(wheel_tag)
    target_parts = _split_platform_tag(target)
    if wheel_parts is None or target_parts is None:
        # Windows and anything unrecognised: only an exact tag match counts.
        return False

    wheel_os, wheel_version, wheel_arch = wheel_parts
    target_os, target_version, target_arch = target_parts
    if wheel_os != target_os:
        return False
    # A wheel built against an older OS/glibc runs on the newer declared one.
    if wheel_version > target_version:
        return False
    if wheel_os == "macosx":
        return wheel_arch in _MACOS_ARCH_PROVIDERS.get(target_arch, {target_arch})
    return wheel_arch == target_arch


def _parse_wheel_filename(name: str) -> tuple[str, list[str], list[str]] | None:
    """Split a wheel filename into (distribution, python tags, platform tags).

    Filenames follow {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl,
    where the tag fields may each be a dot-separated compressed set.
    """
    if not name.lower().endswith(".whl"):
        return None
    parts = name[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    return parts[0], parts[-3].split("."), parts[-1].split(".")


def _python_tags_match(python_tags: list[str], python_version: str) -> bool:
    major, minor = python_version.split(".", 1)
    accepted = {f"cp{major}{minor}", f"py{major}{minor}", f"py{major}"}
    return any(tag.lower() in accepted for tag in python_tags)


def _canonical_name(name: str) -> str:
    """PEP 503 name: `jaraco.classes`, `jaraco_classes` and `Jaraco-Classes` are one project."""
    return re.sub(r"[-_.]+", "_", name).lower()


def _wheel_covers(wheel_name: str, module: str, python_version: str, platform: str) -> bool:
    parsed = _parse_wheel_filename(wheel_name)
    if parsed is None:
        return False
    distribution, python_tags, platform_tags = parsed
    if _canonical_name(distribution) != _canonical_name(module):
        return False
    if not _python_tags_match(python_tags, python_version):
        return False
    return any(_platform_tag_satisfies(tag.lower(), platform.lower()) for tag in platform_tags)


def _validate_wheels(
    names: list[str],
    label: str,
    manifest_wheels: list[str],
    modules: list[str],
    versions: list[str],
    platforms: list[str],
) -> list[str]:
    errors = []
    zip_wheel_set = set(_wheel_names_in_zip(names))
    manifest_wheel_set = set(manifest_wheels)

    for wheel in manifest_wheels:
        if wheel not in zip_wheel_set:
            errors.append(f"{label}: manifest wheel is missing from zip: wheels/{wheel}")
    for wheel in sorted(zip_wheel_set - manifest_wheel_set):
        errors.append(f"{label}: zip ships a wheel not declared in blender_manifest.toml: wheels/{wheel}")

    # Only wheels that are both declared and shipped can be loaded by Blender, so
    # coverage is judged on that intersection rather than on either list alone.
    loadable = sorted(zip_wheel_set & manifest_wheel_set)
    for module in modules:
        for version in versions:
            for platform in platforms:
                if not any(_wheel_covers(wheel, module, version, platform) for wheel in loadable):
                    errors.append(
                        f"{label}: no bundled wheel covers {module} / Python {version} / {platform}"
                    )

    return errors


def _validate_common(names: list[str], label: str) -> list[str]:
    errors = []

    if not _contains_file(names, "__init__.py"):
        errors.append(f"{label}: missing __init__.py")
    if not _contains_file(names, "blender_manifest.toml"):
        errors.append(f"{label}: missing blender_manifest.toml")
    if not _contains_file(names, "version.json"):
        errors.append(f"{label}: missing version.json")
    if not _contains_dir(names, "presets"):
        errors.append(f"{label}: missing presets directory")

    for part in FORBIDDEN_PARTS:
        if _contains_dir(names, part):
            errors.append(f"{label}: forbidden directory included: {part}")
    for filename in FORBIDDEN_FILENAMES:
        if _contains_file(names, filename):
            errors.append(f"{label}: forbidden file included: {filename}")
    if _contains_glob_suffix(names, ".pyc"):
        errors.append(f"{label}: compiled Python files included")
    if _contains_glob_suffix(names, ".zip"):
        errors.append(f"{label}: nested zip file included")

    preset_json = [
        name for name in names
        if "/presets/" in name.replace("\\", "/") and name.lower().endswith(".json")
    ]
    if preset_json:
        errors.append(f"{label}: presets JSON files were not cleared: {preset_json[:5]}")

    return errors


def _validate_citydb_tools(names: list[str], label: str, citydb_tool_dir: str | None) -> list[str]:
    errors = []
    for dirname in sorted(REMOVED_V4_DIRS):
        if _contains_dir(names, dirname):
            errors.append(f"{label}: removed 3DCityDB v4 tool included: {dirname}/")
    for filename in sorted(REMOVED_V4_FILENAMES):
        if _contains_file(names, filename):
            errors.append(f"{label}: removed 3DCityDB v4 wrapper included: {filename}")
    jars = sorted({p[-1] for p in map(_normalized_parts, names) if p and REMOVED_V4_JAR.match(p[-1])})
    if jars:
        errors.append(f"{label}: removed 3DCityDB v4 JAR included: {jars[:3]}")

    # citydb-tool sits directly in the add-on folder: <addon>/citydb-tool-X.Y.Z/...
    top_level = {p[1] for p in map(_normalized_parts, names) if len(p) >= 3}
    if citydb_tool_dir:
        if citydb_tool_dir not in top_level:
            errors.append(f"{label}: {citydb_tool_dir}/ is missing from the add-on folder")
    elif not any(d.startswith(CITYDB_TOOL_PREFIX) for d in top_level):
        errors.append(f"{label}: {CITYDB_TOOL_PREFIX}* is missing")
    return errors


def _validate_bundle(
    path: Path,
    label: str,
    manifest_wheels: list[str],
    modules: list[str],
    versions: list[str],
    platforms: list[str],
    citydb_tool_dir: str | None = None,
) -> list[str]:
    if not path.is_file():
        return [f"{label}: zip not found: {path}"]

    try:
        names = _zip_names(path)
    except zipfile.BadZipFile as exc:
        return [f"{label}: bad zip file: {exc}"]

    errors = _validate_common(names, label)
    errors.extend(_validate_wheels(names, label, manifest_wheels, modules, versions, platforms))
    errors.extend(_validate_citydb_tools(names, label, citydb_tool_dir))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate generated add-on ZIP bundles")
    parser.add_argument("--addon-name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", default=".")
    parser.add_argument("--manifest-toml", default="blender_manifest.toml")
    parser.add_argument("--dependencies-yaml", default="dependencies.yaml")
    args = parser.parse_args(argv)

    base_dir = Path(args.directory)
    bundle_zip = base_dir / f"{args.addon_name}-{args.version}.zip"

    errors = []
    try:
        manifest_wheels = _manifest_wheels(Path(args.manifest_toml))
        modules, versions, platforms = _dependency_matrix(Path(args.dependencies_yaml))
        citydb_tool_dir = _citydb_tool_dir(Path(args.dependencies_yaml))
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"metadata: {exc}")
        manifest_wheels = []
        modules = []
        versions = []
        platforms = []
        citydb_tool_dir = None

    errors.extend(_validate_bundle(bundle_zip, "BUNDLE", manifest_wheels, modules, versions, platforms,
                                   citydb_tool_dir))

    if errors:
        print("Bundle validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Bundle validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
