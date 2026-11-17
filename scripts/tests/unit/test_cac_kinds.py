"""Feature-kind registry (CAC-P3.1): Window/Door declarations, no Blender."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import kinds as k  # noqa: E402
from shared.authoring_core.domain.kinds import FeatureKind, KindRegistryError  # noqa: E402


# -- Window lookup -----------------------------------------------------------

def test_window_lookup():
    window = k.get("Window")
    assert window.name == "Window"
    assert window.enabled is True
    assert window.host_kinds == ("WallSurface",)
    assert window.host_effect == ("interior_ring",)
    assert window.generator == "rectangular_facade_window"
    assert window.owner_rule == "host"
    assert set(window.param_schema) == {"width", "height", "depth"}


def test_window_is_in_all_kinds():
    assert k.WINDOW in k.all_kinds()


# -- Door declared but disabled ----------------------------------------------

def test_door_declared_but_disabled():
    door = k.get("Door")
    assert door.name == "Door"
    assert door.enabled is False
    assert k.is_enabled("Door") is False


def test_door_future_host_effects_present():
    door = k.get("Door")
    assert "interior_ring" in door.host_effect
    assert "exterior_notch" in door.host_effect


def test_door_refused_by_ensure_enabled():
    with pytest.raises(KindRegistryError):
        k.ensure_enabled("Door")


# -- invalid/unknown kind -----------------------------------------------------

def test_unknown_kind_raises():
    with pytest.raises(KindRegistryError):
        k.get("Balcony")


def test_unknown_kind_in_helpers_raises():
    with pytest.raises(KindRegistryError):
        k.is_enabled("Roof")
    with pytest.raises(KindRegistryError):
        k.allows_host("Chimney", "WallSurface")
    with pytest.raises(KindRegistryError):
        k.validate_params("Nope", {})


# -- allowed host validation ---------------------------------------------------

def test_allows_host_true_and_false():
    assert k.allows_host("Window", "WallSurface") is True
    assert k.allows_host("Window", "RoofSurface") is False


def test_ensure_host_allowed_raises_for_wrong_host():
    with pytest.raises(KindRegistryError):
        k.ensure_host_allowed("Window", "RoofSurface")
    assert k.ensure_host_allowed("Window", "WallSurface") is k.WINDOW


# -- disabled-kind refusal ------------------------------------------------------

def test_ensure_enabled_returns_enabled_kind():
    assert k.ensure_enabled("Window") is k.WINDOW


# -- param schema validation shape ----------------------------------------------

def test_validate_params_reports_missing_required():
    errors = k.validate_params("Window", {"width": 1.0})
    assert any("height" in e for e in errors)
    assert any("depth" in e for e in errors)


def test_validate_params_reports_unknown():
    errors = k.validate_params("Window", {"width": 1.0, "height": 1.0, "depth": 0.04, "bogus": 1})
    assert any("bogus" in e for e in errors)


def test_validate_params_empty_when_valid():
    assert k.validate_params("Window", {"width": 1.0, "height": 2.0, "depth": 0.04}) == ()


# -- registry determinism / duplicate registration protection -------------------

def test_all_kinds_is_sorted_and_deterministic():
    names = [kind.name for kind in k.all_kinds()]
    assert names == sorted(names)
    assert k.all_kinds() == k.all_kinds()


def test_duplicate_registration_raises():
    with pytest.raises(KindRegistryError):
        k.register(FeatureKind(name="Window", enabled=True))


def test_registering_new_name_then_cleaning_up_keeps_registry_intact():
    probe = FeatureKind(name="__Probe__", enabled=False)
    k.register(probe)
    try:
        assert k.get("__Probe__") is probe
    finally:
        del k._REGISTRY["__Probe__"]
    with pytest.raises(KindRegistryError):
        k.get("__Probe__")
