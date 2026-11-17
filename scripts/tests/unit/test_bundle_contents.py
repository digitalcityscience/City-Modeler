import importlib.util
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def load_bundle_checker():
    spec = importlib.util.spec_from_file_location(
        "check_bundle_contents_under_test",
        ROOT / "scripts/tests/check_bundle_contents.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bundle_checker = load_bundle_checker()


def write_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            if name.endswith("/"):
                archive.writestr(name, "")
            else:
                archive.writestr(name, "content")


def write_metadata(tmp_path: Path, wheels: list[str], platforms: list[str] | None = None) -> tuple[Path, Path]:
    platforms = platforms or ["win_amd64", "manylinux_2_28_x86_64"]
    manifest = tmp_path / "blender_manifest.toml"
    dependencies = tmp_path / "dependencies.yaml"
    manifest.write_text(
        "schema_version = \"1.0.0\"\n"
        "id = \"city_modeler\"\n"
        "wheels = [\n"
        + "".join(f"  \"./wheels/{wheel}\",\n" for wheel in wheels)
        + "]\n",
        encoding="utf-8",
    )
    dependencies.write_text(
        "python_modules:\n"
        "  - name: lxml\n"
        "    version: \"6.0.2\"\n"
        "python:\n"
        "  versions:\n"
        "    - \"3.11\"\n"
        "  platforms:\n"
        + "".join(f"    - {platform}\n" for platform in platforms),
        encoding="utf-8",
    )
    return manifest, dependencies


def run_checker(
    tmp_path: Path,
    manifest: Path | None = None,
    dependencies: Path | None = None,
    addon_name="city_modeler",
    version="1.2.3",
):
    args = [
        "--addon-name",
        addon_name,
        "--version",
        version,
        "--directory",
        str(tmp_path),
    ]
    if manifest is not None:
        args.extend(["--manifest-toml", str(manifest)])
    if dependencies is not None:
        args.extend(["--dependencies-yaml", str(dependencies)])
    return bundle_checker.main(args)


def common_entries(root: str) -> list[str]:
    return [
        f"{root}/",
        f"{root}/__init__.py",
        f"{root}/blender_manifest.toml",
        f"{root}/version.json",
        f"{root}/presets/",
    ]


def write_valid_bundles(
    tmp_path: Path,
    addon_name="city_modeler",
    version="1.2.3",
    wheel_names: list[str] | None = None,
):
    root = addon_name
    write_zip(
        tmp_path / f"{addon_name}-{version}.zip",
        [
            *common_entries(root),
            *[f"{root}/wheels/{wheel}" for wheel in (wheel_names or [])],
            f"{root}/citydb-tool-1.3.0/citydb",
        ],
    )


def test_bundle_checker_accepts_expected_archive(tmp_path):
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    assert result == 0


def test_bundle_checker_rejects_missing_expected_tool(tmp_path):
    addon_name = "city_modeler"
    version = "1.2.3"
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_zip(
        tmp_path / f"{addon_name}-{version}.zip",
        [
            *common_entries(addon_name),
            *[f"{addon_name}/wheels/{wheel}" for wheel in wheels],
        ],
    )

    result = run_checker(tmp_path, manifest, dependencies, addon_name, version)

    assert result == 1


def test_bundle_checker_rejects_missing_blender_manifest(tmp_path):
    addon_name = "city_modeler"
    version = "1.2.3"
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_zip(
        tmp_path / f"{addon_name}-{version}.zip",
        [
            f"{addon_name}/",
            f"{addon_name}/__init__.py",
            f"{addon_name}/version.json",
            f"{addon_name}/presets/",
            *[f"{addon_name}/wheels/{wheel}" for wheel in wheels],
            f"{addon_name}/citydb-tool-1.3.0/citydb",
        ],
    )

    result = run_checker(tmp_path, manifest, dependencies, addon_name, version)

    assert result == 1


def test_bundle_checker_rejects_forbidden_files_and_preset_json(tmp_path):
    addon_name = "city_modeler"
    version = "1.2.3"
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_zip(
        tmp_path / f"{addon_name}-{version}.zip",
        [
            *common_entries(addon_name),
            *[f"{addon_name}/wheels/{wheel}" for wheel in wheels],
            f"{addon_name}/citydb-tool-1.3.0/citydb",
            f"{addon_name}/scripts/test.py",
            f"{addon_name}/presets/Soest.json",
        ],
    )

    result = run_checker(tmp_path, manifest, dependencies, addon_name, version)

    assert result == 1


def test_bundle_checker_rejects_missing_platform_wheel(tmp_path, capsys):
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(tmp_path, wheel_names=wheels[:1])

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "no bundled wheel covers lxml / Python 3.11 / manylinux_2_28_x86_64" in captured.out


def test_bundle_checker_rejects_manifest_wheel_missing_from_zip(tmp_path, capsys):
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(tmp_path, wheel_names=wheels[:1])

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "manifest wheel is missing from zip" in captured.out
    assert "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl" in captured.out


def test_bundle_checker_accepts_complete_wheel_coverage(tmp_path):
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    assert result == 0


def test_bundle_checker_rejects_right_wheel_count_covering_wrong_platforms(tmp_path, capsys):
    """The regression ticket 03 names: 'silently fetched Linux-only wheels for every platform'.

    The wheel count matches the declared matrix, so a count-based check passes,
    but no wheel actually covers the declared Linux platform.
    """
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-win32.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "lxml" in captured.out
    assert "manylinux_2_28_x86_64" in captured.out


def test_bundle_checker_accepts_macos_wheel_tagged_below_declared_platform(tmp_path):
    """pip answers `--platform macosx_13_0_x86_64` with a macosx_10_9_x86_64 wheel.

    A wheel built against an older deployment target runs on the newer OS, and
    universal2 carries the arm64 slice, so both declared macOS targets are covered.
    """
    wheels = [
        "lxml-6.0.2-cp311-cp311-macosx_10_9_x86_64.whl",
        "lxml-6.0.2-cp311-cp311-macosx_10_9_universal2.whl",
    ]
    manifest, dependencies = write_metadata(
        tmp_path, wheels, platforms=["macosx_13_0_x86_64", "macosx_14_0_arm64"]
    )
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    assert result == 0


def test_bundle_checker_accepts_compressed_manylinux_tag_set(tmp_path):
    """pip answers `--platform manylinux_2_28_x86_64` with a compressed tag set."""
    wheels = [
        "lxml-6.0.2-cp311-cp311-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(
        tmp_path, wheels, platforms=["manylinux_2_28_x86_64"]
    )
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    assert result == 0


def test_bundle_checker_rejects_macos_wheel_tagged_above_declared_platform(tmp_path, capsys):
    """A wheel needing macOS 15 does not cover a declared macOS 13 target."""
    wheels = ["lxml-6.0.2-cp311-cp311-macosx_15_0_x86_64.whl"]
    manifest, dependencies = write_metadata(
        tmp_path, wheels, platforms=["macosx_13_0_x86_64"]
    )
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "macosx_13_0_x86_64" in captured.out


def test_bundle_checker_rejects_arm64_target_covered_only_by_x86_64_wheel(tmp_path, capsys):
    wheels = ["lxml-6.0.2-cp311-cp311-macosx_10_9_x86_64.whl"]
    manifest, dependencies = write_metadata(
        tmp_path, wheels, platforms=["macosx_14_0_arm64"]
    )
    write_valid_bundles(tmp_path, wheel_names=wheels)

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "macosx_14_0_arm64" in captured.out


def test_bundle_checker_rejects_wheel_present_but_undeclared_in_manifest(tmp_path, capsys):
    """Blender only loads wheels the manifest declares, so an undeclared wheel is dead weight."""
    wheels = [
        "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
    ]
    manifest, dependencies = write_metadata(tmp_path, wheels)
    write_valid_bundles(
        tmp_path, wheel_names=[*wheels, "lxml-5.0.0-cp311-cp311-win_amd64.whl"]
    )

    result = run_checker(tmp_path, manifest, dependencies)

    captured = capsys.readouterr()
    assert result == 1
    assert "not declared in blender_manifest.toml" in captured.out
    assert "lxml-5.0.0-cp311-cp311-win_amd64.whl" in captured.out


LXML_WHEELS = [
    "lxml-6.0.2-cp311-cp311-win_amd64.whl",
    "lxml-6.0.2-cp311-cp311-manylinux_2_28_x86_64.whl",
]


@pytest.mark.parametrize("entry, expected", [
    ("3dcitydb-tool-v4/impexp_config.xml", "3dcitydb-tool-v4/"),
    ("lib/impexp-client-cli-5.5.3.jar", "impexp-client-cli-5.5.3.jar"),
    ("ops/citydb_v4_cli.py", "citydb_v4_cli.py"),
])
def test_bundle_checker_rejects_removed_v4_tooling(tmp_path, capsys, entry, expected):
    """T05: the add-on is 3DCityDB 5.x only; the v4 Java importer/exporter must not be shipped."""
    addon_name, version = "city_modeler", "1.2.3"
    manifest, dependencies = write_metadata(tmp_path, LXML_WHEELS)
    write_zip(
        tmp_path / f"{addon_name}-{version}.zip",
        [
            *common_entries(addon_name),
            *[f"{addon_name}/wheels/{wheel}" for wheel in LXML_WHEELS],
            f"{addon_name}/citydb-tool-1.3.0/citydb",
            f"{addon_name}/{entry}",
        ],
    )

    result = run_checker(tmp_path, manifest, dependencies, addon_name, version)

    assert result == 1
    assert expected in capsys.readouterr().out


def test_bundle_checker_requires_declared_citydb_tool_in_addon_folder(tmp_path, capsys):
    """citydb-tool-<version>/ from dependencies.yaml must sit directly in the add-on folder."""
    addon_name, version = "city_modeler", "1.2.3"
    manifest, dependencies = write_metadata(tmp_path, LXML_WHEELS)
    dependencies.write_text(
        "tools:\n"
        "  citydb-tool:\n"
        "    version: \"1.3.0\"\n"
        "    target_dir: \"citydb-tool-{version}\"\n"
        + dependencies.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    entries = [*common_entries(addon_name), *[f"{addon_name}/wheels/{wheel}" for wheel in LXML_WHEELS]]

    write_zip(tmp_path / f"{addon_name}-{version}.zip", [*entries, f"{addon_name}/tools/citydb-tool-1.3.0/citydb"])
    assert run_checker(tmp_path, manifest, dependencies, addon_name, version) == 1
    assert "citydb-tool-1.3.0/ is missing" in capsys.readouterr().out

    write_zip(tmp_path / f"{addon_name}-{version}.zip", [*entries, f"{addon_name}/citydb-tool-1.2.0/citydb"])
    assert run_checker(tmp_path, manifest, dependencies, addon_name, version) == 1

    write_zip(tmp_path / f"{addon_name}-{version}.zip", [*entries, f"{addon_name}/citydb-tool-1.3.0/citydb"])
    assert run_checker(tmp_path, manifest, dependencies, addon_name, version) == 0


def test_bundle_checker_accepts_the_repository_real_wheel_matrix(tmp_path):
    """Guards against false failures: the repo's real manifest + dependencies.yaml must pass.

    The real wheel filenames deliberately do not echo the declared platform
    strings, so this is what keeps the tag-compatibility rules honest.
    """
    real_manifest = ROOT / "blender_manifest.toml"
    real_dependencies = ROOT / "dependencies.yaml"
    declared = bundle_checker._manifest_wheels(real_manifest)
    assert declared, "expected the real manifest to declare wheels"

    write_valid_bundles(tmp_path, wheel_names=declared)

    result = run_checker(tmp_path, real_manifest, real_dependencies)

    assert result == 0
