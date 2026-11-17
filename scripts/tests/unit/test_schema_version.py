import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


schema_version = load_module("shared/schema_version.py", "schema_version_under_test")


def test_importer_schema_versions_are_classified_by_one_shared_helper():
    assert schema_version.is_citygml2("2.0") is True
    assert schema_version.is_citygml3("2.0") is False
    assert schema_version.is_citygml3("3.0") is True
    assert schema_version.is_citygml2("3.0") is False
    assert schema_version.is_citygml2_0("2.0") is True


def test_schema_version_classification_normalizes_and_rejects_unknown_values():
    assert schema_version.is_citygml3(" 3.0 ") is True
    assert schema_version.is_citygml2(" 2.0 ") is True
    assert schema_version.is_citygml2(None) is False
    assert schema_version.is_citygml3("unknown") is False
    assert schema_version.is_citygml2_0("2.1") is False
    assert schema_version.is_citygml2_0(" 2.0 ") is False
