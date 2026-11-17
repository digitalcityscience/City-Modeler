"""B44: BuildingInstallation function codes come from the SIG3D code list.

An authored balcony/chimney is written as bldg:function 1000 / 1030 with the SIG3D
codeSpace (OGC 12-019 Annex C.3); the resolver recognises those codes on function and
usage, but not on class (SIG3D class 1000 = "outer characteristics"). The local
5010/5030 list stays readable for files written before B44.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("semantic_types_b44", ROOT / "shared/semantic_types.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["semantic_types_b44"] = module
    spec.loader.exec_module(module)
    return module


st = _load()


def test_exported_codes_are_sig3d_with_codespace():
    assert st.CANONICAL_TYPE_INSTALLATION_CODE == {"Balcony": "1000", "Chimney": "1030"}
    assert st.INSTALLATION_FUNCTION_CODESPACE.endswith("building/2.0/BuildingInstallation_function.xml")
    for code in st.CANONICAL_TYPE_INSTALLATION_CODE.values():
        assert code in st.SIG3D_INSTALLATION_FUNCTION_CODES


def test_sig3d_function_and_usage_codes_resolve():
    assert st.resolve_semantic_type(function_value="1000").canonical_type == "Balcony"
    assert st.resolve_semantic_type(function_value="1000").origin == "function"
    assert st.resolve_semantic_type(usage_value="1030").canonical_type == "Chimney"
    assert st.resolve_semantic_type(source_feature_type="bldg:BuildingInstallation",
                                    function_value="1070").canonical_type == "BuildingInstallation"


def test_sig3d_class_1000_is_not_a_balcony():
    v = st.resolve_semantic_type(source_feature_type="bldg:BuildingInstallation", class_value="1000")
    assert v.canonical_type == "BuildingInstallation"


def test_pre_b44_local_codes_still_read():
    assert st.resolve_semantic_type(function_value="5010").canonical_type == "Balcony"
    assert st.resolve_semantic_type(class_value="5030").canonical_type == "Chimney"
