import importlib.util
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def load_fetch_deps():
    spec = importlib.util.spec_from_file_location(
        "fetch_deps_under_test_wheels",
        ROOT / "scripts/fetch_deps.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fetch_deps = load_fetch_deps()


def _dest_dir_from_cmd(cmd):
    return Path(cmd[cmd.index("--dest") + 1])


def _platform_from_cmd(cmd):
    return cmd[cmd.index("--platform") + 1]


def _py_version_from_cmd(cmd):
    return cmd[cmd.index("--python-version") + 1]


def _wheel_filename(mod_name, mod_version, py_ver, plat):
    py_tag = f"cp{py_ver.replace('.', '')}"
    return f"{mod_name}-{mod_version}-{py_tag}-{py_tag}-{plat}.whl"


def fake_successful_pip_download(cmd, capture_output, text):
    """Simulate `pip download` succeeding by dropping one wheel into --dest."""
    dest = _dest_dir_from_cmd(cmd)
    plat = _platform_from_cmd(cmd)
    py_ver = _py_version_from_cmd(cmd)
    pkg_spec = cmd[-1]
    mod_name, mod_version = pkg_spec.split("==")

    whl_name = _wheel_filename(mod_name, mod_version, py_ver, plat)
    (dest / whl_name).write_bytes(b"fake wheel contents")

    return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


def test_fetch_one_wheel_moves_intact_whl_into_wheels_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()

    fetch_deps._fetch_one_wheel("pyproj", "3.7.2", "3.11", "macosx_11_0_arm64", wheels_dir)

    produced = list(wheels_dir.glob("*.whl"))
    assert len(produced) == 1
    assert produced[0].read_bytes() == b"fake wheel contents"


def test_fetch_one_wheel_raises_when_platform_constrained_download_fails(monkeypatch, tmp_path):
    def fake_failing_pip_download(cmd, capture_output, text):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="No matching distribution found")

    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_failing_pip_download)

    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()

    with pytest.raises(fetch_deps.WheelFetchError) as exc_info:
        fetch_deps._fetch_one_wheel("pyproj", "3.7.2", "3.11", "macosx_11_0_arm64", wheels_dir)

    message = str(exc_info.value)
    assert "pyproj" in message
    assert "3.7.2" in message
    assert "3.11" in message
    assert "macosx_11_0_arm64" in message


def _manifest(modules, versions, platforms):
    return {
        "tools": {},
        "python_modules": modules,
        "python": {"versions": versions, "platforms": platforms},
    }


def test_fetch_python_modules_writes_one_wheel_per_declared_combination(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    modules = [{"name": "lxml", "version": "6.0.2"}, {"name": "pyproj", "version": "3.7.2"}]
    versions = ["3.11", "3.13"]
    platforms = ["win_amd64", "manylinux2014_x86_64", "macosx_11_0_x86_64", "macosx_11_0_arm64"]
    manifest = _manifest(modules, versions, platforms)

    fetch_deps.fetch_python_modules(manifest, str(tmp_path))

    wheels_dir = tmp_path / "wheels"
    produced = sorted(p.name for p in wheels_dir.glob("*.whl"))

    expected_count = len(modules) * len(versions) * len(platforms)
    assert len(produced) == expected_count
    assert len(produced) == len(set(produced))

    expected_names = {
        _wheel_filename(mod["name"], mod["version"], py_ver, plat)
        for mod in modules
        for py_ver in versions
        for plat in platforms
    }
    assert set(produced) == expected_names


def test_fetch_python_modules_fails_loudly_instead_of_falling_back(tmp_path, monkeypatch):
    failing_platform = "macosx_11_0_arm64"

    def fake_pip_download_one_platform_broken(cmd, capture_output, text):
        if _platform_from_cmd(cmd) == failing_platform:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="No matching distribution found")
        return fake_successful_pip_download(cmd, capture_output, text)

    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_pip_download_one_platform_broken)

    modules = [{"name": "pyproj", "version": "3.7.2"}]
    versions = ["3.11"]
    platforms = ["win_amd64", failing_platform]
    manifest = _manifest(modules, versions, platforms)

    with pytest.raises(fetch_deps.WheelFetchError) as exc_info:
        fetch_deps.fetch_python_modules(manifest, str(tmp_path))

    message = str(exc_info.value)
    assert "pyproj" in message
    assert failing_platform in message


def test_fetch_python_modules_returns_fetched_wheel_filenames(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    manifest = _manifest(
        [{"name": "lxml", "version": "6.0.2"}],
        ["3.11"],
        ["win_amd64", "macosx_14_0_arm64"],
    )

    fetched = fetch_deps.fetch_python_modules(manifest, str(tmp_path))

    assert sorted(fetched) == [
        _wheel_filename("lxml", "6.0.2", "3.11", "macosx_14_0_arm64"),
        _wheel_filename("lxml", "6.0.2", "3.11", "win_amd64"),
    ]


def test_fetch_python_modules_clears_stale_wheels_from_previous_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    stale = wheels_dir / _wheel_filename("lxml", "5.0.0", "3.11", "win_amd64")
    stale.write_bytes(b"stale wheel from an older declared version")

    manifest = _manifest([{"name": "lxml", "version": "6.0.2"}], ["3.11"], ["win_amd64"])

    fetch_deps.fetch_python_modules(manifest, str(tmp_path))

    remaining = sorted(p.name for p in wheels_dir.glob("*.whl"))
    assert remaining == [_wheel_filename("lxml", "6.0.2", "3.11", "win_amd64")]
    assert not stale.exists()


MANIFEST_TEMPLATE = """\
schema_version = "1.0.0"
id = "city_modeler"
version = "0.1.1"
blender_version_min = "5.0.0"

wheels = [
  "./wheels/lxml-1.0.0-cp311-cp311-win_amd64.whl",
]

[permissions]
files = "Import and export CityGML files"
"""


def test_update_manifest_wheels_replaces_array_with_sorted_relative_paths(tmp_path):
    manifest_path = tmp_path / "blender_manifest.toml"
    manifest_path.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    fetch_deps.update_manifest_wheels(
        manifest_path,
        [
            "pyproj-3.7.2-cp313-cp313-win_amd64.whl",
            "lxml-6.0.2-cp311-cp311-win_amd64.whl",
        ],
    )

    text = manifest_path.read_text(encoding="utf-8")
    assert (
        'wheels = [\n'
        '  "./wheels/lxml-6.0.2-cp311-cp311-win_amd64.whl",\n'
        '  "./wheels/pyproj-3.7.2-cp313-cp313-win_amd64.whl",\n'
        ']'
    ) in text
    # The stale entry from the previous build is gone, and the rest of the manifest survives.
    assert "lxml-1.0.0" not in text
    assert 'id = "city_modeler"' in text
    assert '[permissions]' in text


def test_update_manifest_wheels_fails_loudly_when_wheels_field_is_missing(tmp_path):
    manifest_path = tmp_path / "blender_manifest.toml"
    manifest_path.write_text('schema_version = "1.0.0"\nid = "x"\n', encoding="utf-8")

    with pytest.raises(fetch_deps.WheelFetchError) as exc_info:
        fetch_deps.update_manifest_wheels(manifest_path, ["lxml-6.0.2-cp311-cp311-win_amd64.whl"])

    assert "wheels" in str(exc_info.value)


def test_update_manifest_wheels_keeps_entries_blender_can_parse(tmp_path):
    """Blender requires forward slashes, a .whl suffix and 5-6 dash-separated filename parts."""
    manifest_path = tmp_path / "blender_manifest.toml"
    manifest_path.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    fetch_deps.update_manifest_wheels(
        manifest_path,
        ["lxml-6.0.2-cp313-cp313-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl"],
    )

    entry = re.findall(r'"(\./wheels/[^"]+)"', manifest_path.read_text(encoding="utf-8"))[0]
    assert "\\" not in entry
    assert entry.endswith(".whl")
    assert 5 <= len(Path(entry).name.split("-")) <= 6


def test_main_python_only_regenerates_manifest_wheels(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    (tmp_path / "dependencies.yaml").write_text(
        "\n".join(
            [
                "tools: {}",
                "python_modules:",
                "  - name: lxml",
                '    version: "6.0.2"',
                "python:",
                "  versions:",
                '    - "3.11"',
                "  platforms:",
                "    - win_amd64",
            ]
        ),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "blender_manifest.toml"
    manifest_path.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    monkeypatch.setattr(
        fetch_deps.sys,
        "argv",
        ["fetch_deps.py", "--output-dir", str(tmp_path), "--python-only"],
    )

    fetch_deps.main()

    text = manifest_path.read_text(encoding="utf-8")
    assert '"./wheels/lxml-6.0.2-cp311-cp311-win_amd64.whl",' in text
    assert "lxml-1.0.0" not in text


def test_main_python_only_produces_expected_wheel_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_deps.subprocess, "run", fake_successful_pip_download)

    manifest_path = tmp_path / "dependencies.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "tools: {}",
                "python_modules:",
                "  - name: lxml",
                '    version: "6.0.2"',
                "python:",
                "  versions:",
                '    - "3.11"',
                "  platforms:",
                "    - win_amd64",
                "    - macosx_11_0_arm64",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "blender_manifest.toml").write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    monkeypatch.setattr(
        fetch_deps.sys,
        "argv",
        [
            "fetch_deps.py",
            "--manifest",
            "dependencies.yaml",
            "--output-dir",
            str(tmp_path),
            "--python-only",
        ],
    )

    fetch_deps.main()

    wheels_dir = tmp_path / "wheels"
    produced = sorted(p.name for p in wheels_dir.glob("*.whl"))
    assert produced == [
        _wheel_filename("lxml", "6.0.2", "3.11", "macosx_11_0_arm64"),
        _wheel_filename("lxml", "6.0.2", "3.11", "win_amd64"),
    ]
    assert not (tmp_path / "python_deps").exists()
