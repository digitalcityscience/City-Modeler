"""T02: psycopg/keyring wheels - fetch platform expansion and bundle coverage of dotted names."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch_deps = _load("scripts/fetch_deps.py", "fetch_deps_t02")
bundle = _load("scripts/tests/check_bundle_contents.py", "bundle_t02")


def test_manylinux_target_also_offers_older_glibc():
    tags = fetch_deps._compatible_platforms("manylinux_2_28_x86_64")
    assert tags[0] == "manylinux_2_28_x86_64"
    assert "manylinux_2_17_x86_64" in tags and tags[-1] == "manylinux2014_x86_64"
    assert fetch_deps._compatible_platforms("win_amd64") == ["win_amd64"]
    assert fetch_deps._compatible_platforms("macosx_14_0_arm64") == ["macosx_14_0_arm64"]


def test_pure_flag():
    assert fetch_deps._is_pure({"pure": True})
    assert fetch_deps._is_pure({"pure": "true"})
    assert not fetch_deps._is_pure({"name": "psycopg-binary"})


def test_declared_modules_are_parsed():
    data = fetch_deps.load_manifest(ROOT / "dependencies.yaml")
    names = {m["name"] for m in data["python_modules"]}
    assert {"psycopg", "psycopg-binary", "keyring", "jaraco.classes", "tzdata"} <= names


def test_bundle_coverage_matches_dotted_and_dashed_names():
    assert bundle._wheel_covers("jaraco.classes-3.4.0-py3-none-any.whl", "jaraco.classes", "3.13", "win_amd64")
    assert bundle._wheel_covers("jaraco_functools-4.6.0-py3-none-any.whl", "jaraco.functools", "3.11", "macosx_14_0_arm64")
    assert bundle._wheel_covers("psycopg_binary-3.3.6-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                                "psycopg-binary", "3.13", "manylinux_2_28_x86_64")
    assert not bundle._wheel_covers("psycopg_binary-3.3.6-cp313-cp313-win_amd64.whl",
                                    "psycopg-binary", "3.11", "win_amd64")


def test_manifest_lists_a_wheel_for_every_declared_combination():
    data = fetch_deps.load_manifest(ROOT / "dependencies.yaml")
    wheels = bundle._manifest_wheels(ROOT / "blender_manifest.toml")
    modules = [m["name"] for m in data["python_modules"]]
    missing = [
        (m, v, p)
        for m in modules
        for v in data["python"]["versions"]
        for p in data["python"]["platforms"]
        if not any(bundle._wheel_covers(w, m, v, p) for w in wheels)
    ]
    assert missing == []
