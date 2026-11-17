"""Strict known-bug registry (scripts/tests/known_arch_bugs.json) and the
suite runner's pass/fail/xfail/xpass classification (CAC-P0.1)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "scripts" / "tests" / "known_arch_bugs.json"


def load_module(relative_path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = load_module("scripts/headless/run_cac_suite.py", "run_cac_suite_under_test")

VALID_ENTRY = {
    "test_ids": ["test_cac_p0_known_bugs::ab01_triangulated_roof"],
    "reason": "extra faces become bogus interior rings",
    "fixed_in": "P6",
}


def test_committed_registry_is_valid():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert runner.validate_registry(data) == []


def test_valid_entry_is_accepted():
    assert runner.validate_registry({"AB-01": VALID_ENTRY}) == []


@pytest.mark.parametrize("data, fragment", [
    ([], "top level"),
    ({"AB-1": VALID_ENTRY}, "AB-nn"),
    ({"AB-01": {**VALID_ENTRY, "extra": 1}}, "fields must be exactly"),
    ({"AB-01": {**VALID_ENTRY, "reason": " "}}, "reason must be"),
    ({"AB-01": {**VALID_ENTRY, "fixed_in": ""}}, "fixed_in must be"),
    ({"AB-01": {**VALID_ENTRY, "test_ids": []}}, "non-empty list"),
    ({"AB-01": {**VALID_ENTRY, "test_ids": ["no_separator"]}}, "bad test id"),
    ({"AB-01": VALID_ENTRY, "AB-02": VALID_ENTRY}, "already listed under AB-01"),
])
def test_invalid_registry_is_rejected(data, fragment):
    problems = runner.validate_registry(data)
    assert any(fragment in p for p in problems), problems


def test_load_registry_refuses_invalid_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"AB-01": {"test_ids": []}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.load_registry(bad)


def test_load_registry_maps_test_ids_to_bug(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"AB-01": VALID_ENTRY}), encoding="utf-8")
    assert runner.load_registry(good) == {VALID_ENTRY["test_ids"][0]: "AB-01"}


def test_classification_is_strict():
    checks = {
        "test_cac_x::a": {"ok": True, "detail": ""},
        "test_cac_x::b": {"ok": False, "detail": ""},
        "test_cac_x::c": {"ok": False, "detail": ""},
        "test_cac_x::d": {"ok": True, "detail": ""},
    }
    listed = {"test_cac_x::c": "AB-01", "test_cac_x::d": "AB-02"}
    assert runner.classify(checks, listed) == {
        "test_cac_x::a": "pass",
        "test_cac_x::b": "fail",
        "test_cac_x::c": "xfail",
        "test_cac_x::d": "xpass",
    }


@pytest.mark.parametrize("statuses, code", [
    ({"a": "pass"}, 0),
    ({"a": "pass", "b": "xfail"}, 0),
    ({"a": "pass", "b": "fail"}, 1),
    ({"a": "pass", "b": "xpass"}, 1),
])
def test_suite_exit_code(statuses, code):
    assert runner.suite_exit_code(statuses) == code


def test_update_goldens_needs_env_switch(monkeypatch, tmp_path):
    monkeypatch.delenv("CAC_ALLOW_GOLDEN_UPDATE", raising=False)
    with pytest.raises(SystemExit, match="refused"):
        runner.main([str(tmp_path), "--update-goldens"])
