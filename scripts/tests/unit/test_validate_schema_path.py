"""The CityGML 3 XSD is found where the repository keeps it, with the folder's exact case.

Windows ignores case, so a wrong ``schema`` only failed on Linux/macOS: the local XSD was missed and
validation fell back to downloading the OGC schema on the main thread.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _validate():
    spec = importlib.util.spec_from_file_location("cgml3_validate_under_test", ROOT / "ops" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_schema_base_matches_the_folder_on_disk():
    base = _validate()._default_schema_base()
    assert base.name in {p.name for p in ROOT.iterdir()}   # exact case, not a case-insensitive match
    assert (base / "citygml_3/profiles/base/3.0/CityGML.xsd").is_file()
