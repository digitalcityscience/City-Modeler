import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


semantic_types = load_module("shared/semantic_types.py", "semantic_types_under_test")


def resolve(**kwargs):
    return semantic_types.resolve_semantic_type(**kwargs)


def test_explicit_feature_type_resolves_openings_and_preserves_source_feature_type():
    verdict = resolve(source_feature_type="con:WindowSurface", name="Door 1")

    assert verdict.canonical_type == "Window"
    assert verdict.origin == "feature_type"
    assert verdict.source_feature_type == "con:WindowSurface"


def test_priority_chain_keeps_lower_rungs_from_overriding_higher_rungs():
    verdict = resolve(function_value="5030", name="Balcony 1")

    assert verdict.canonical_type == "Chimney"
    assert verdict.origin == "function"


def test_feature_type_beats_conflicting_class_function_usage():
    verdict = resolve(
        source_feature_type="bldg:BalconySurface",
        class_value="5030",
        function_value="door",
        usage_value="window",
    )

    assert verdict.canonical_type == "Balcony"
    assert verdict.origin == "feature_type"


def test_class_beats_conflicting_function_usage_generic_attribute_and_name():
    verdict = resolve(
        class_value="balcony",
        function_value="5030",
        usage_value="door",
        generic_attributes={"semantic_type": "window"},
        name="Chimney 2",
    )

    assert verdict.canonical_type == "Balcony"
    assert verdict.origin == "class"


def test_usage_and_codelist_codes_are_separate_rungs():
    assert resolve(usage_value="door").canonical_type == "Door"

    verdict = resolve(usage_value="5010")
    assert verdict.canonical_type == "Balcony"
    assert verdict.origin == "usage"


def test_class_function_usage_reject_non_codelist_balcony_chimney_synonyms():
    # "flue" used to be an extra Chimney synonym beyond the local installation
    # codelist (INSTALLATION_CLASS_CODES only spells out "Chimney" itself).
    # Under the strict policy it is not a recognized codelist entry, so it
    # must not resolve at any of the class/function/usage rungs.
    assert resolve(class_value="flue").canonical_type == "Unknown"
    assert resolve(function_value="flue").canonical_type == "Unknown"
    assert resolve(usage_value="flue").canonical_type == "Unknown"


def test_name_never_influences_the_verdict():
    # Strict policy (appearance ticket 01): gml:name is preserved as object
    # data by the importer but is never a classification input. A literal
    # "Balcony"/"Chimney" name with no other evidence stays generic
    # BuildingInstallation - it must not fabricate a Balcony/Chimney verdict.
    balcony_name = resolve(source_feature_type="bldg:BuildingInstallation", name="Balcony")
    chimney_name = resolve(source_feature_type="bldg:BuildingInstallation", name="Chimney")

    assert balcony_name.canonical_type == "BuildingInstallation"
    assert balcony_name.origin == "generic"
    assert chimney_name.canonical_type == "BuildingInstallation"
    assert chimney_name.origin == "generic"

    # With no feature type either, a name-only installation falls all the way
    # to Unknown - not Door, even though "door" used to be a separate exact
    # token match under the old tokenized-name rung.
    verdict = resolve(name="Chimneysweep Door")
    assert verdict.canonical_type == "Unknown"
    assert verdict.origin == "unknown"


def test_generic_attributes_never_influence_the_verdict():
    # A generic attribute containing a plausible subtype string - or an
    # unrecognized ADE-style element flattened into generic attributes - must
    # not affect the verdict even though it looks like a match.
    verdict = resolve(
        source_feature_type="bldg:BuildingInstallation",
        generic_attributes={
            "detailType": "Balcony",
            "myade:balconyType": "Balcony",
        },
    )

    assert verdict.canonical_type == "BuildingInstallation"
    assert verdict.origin == "generic"


def test_generic_building_installation_fallback_is_distinct_from_unknown():
    generic = resolve(source_feature_type="bldg:BuildingInstallation", name="North Wall")
    unknown = resolve(source_feature_type="bldg:WallSurface", name="North Wall")

    assert generic.canonical_type == "BuildingInstallation"
    assert generic.origin == "generic"
    assert unknown.canonical_type == "Unknown"
    assert unknown.origin == "unknown"


def test_every_canonical_type_is_reachable():
    cases = [
        ({"source_feature_type": "bldg:Window"}, "Window"),
        ({"source_feature_type": "bldg:Door"}, "Door"),
        ({"class_value": "balcony"}, "Balcony"),
        ({"function_value": "5030"}, "Chimney"),
        ({"source_feature_type": "bldg:BuildingInstallation"}, "BuildingInstallation"),
        ({}, "Unknown"),
    ]

    for kwargs, expected in cases:
        assert resolve(**kwargs).canonical_type == expected


def test_installation_codelist_codes_resolve_through_class_and_function():
    assert resolve(class_value="5010").canonical_type == "Balcony"
    assert resolve(class_value="5010").origin == "class"
    assert resolve(function_value="5030").canonical_type == "Chimney"
    assert resolve(function_value="5030").origin == "function"

    # A code with no canonical mapping (e.g. "other building installation")
    # must not accidentally resolve - it should fall through to later rungs.
    assert resolve(class_value="5070").canonical_type == "Unknown"
