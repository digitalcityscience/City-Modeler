import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]

# Deliberately a different version from version.json below, so tests can tell which
# of the two sources a version was actually read from.
MANIFEST_TEMPLATE = """\
schema_version = "1.0.0"

id = "city_modeler"
version = "9.9.9"
blender_version_min = "5.0.0"

wheels = [
  "./wheels/lxml-6.0.2-cp311-cp311-win_amd64.whl",
]

[permissions]
files = "Import and export CityGML files"
"""


def load_version_bump():
    spec = importlib.util.spec_from_file_location(
        "version_bump_under_test",
        ROOT / "scripts/version_bump.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


version_bump = load_version_bump()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Point version_bump at a throwaway copy of the files it rewrites."""
    version_json = tmp_path / "version.json"
    manifest = tmp_path / "blender_manifest.toml"
    version_json.write_text(json.dumps({"major": 0, "minor": 1, "patch": 1}), encoding="utf-8")
    manifest.write_text(MANIFEST_TEMPLATE, encoding="utf-8")

    monkeypatch.setattr(version_bump, "VERSION_JSON", version_json)
    monkeypatch.setattr(version_bump, "MANIFEST_FILE", manifest)
    return tmp_path


def test_set_version_writes_version_json_and_manifest(repo):
    version_bump.set_version(1, 2, 3)

    assert json.loads((repo / "version.json").read_text(encoding="utf-8")) == {
        "major": 1,
        "minor": 2,
        "patch": 3,
    }

    manifest_text = (repo / "blender_manifest.toml").read_text(encoding="utf-8")
    assert 'version = "1.2.3"' in manifest_text
    # Bumping the version must not disturb the rest of the manifest.
    assert 'blender_version_min = "5.0.0"' in manifest_text
    assert './wheels/lxml-6.0.2-cp311-cp311-win_amd64.whl' in manifest_text


def test_set_version_fails_loudly_when_manifest_has_no_version_field(repo):
    (repo / "blender_manifest.toml").write_text('schema_version = "1.0.0"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        version_bump.set_version(1, 0, 0)

    assert exc_info.value.code != 0


def test_get_current_version_prefers_version_json_over_manifest(repo):
    assert version_bump.get_current_version() == "0.1.1"


def test_get_current_version_falls_back_to_manifest(repo):
    (repo / "version.json").unlink()

    assert version_bump.get_current_version() == "9.9.9"
