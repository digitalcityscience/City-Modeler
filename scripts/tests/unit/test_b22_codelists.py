"""B22: code list registry and detection (bpy-free)."""
from shared import codelists


def test_default_is_ogc():
    assert codelists.default_id() == "OGC"


def test_detect_adv_by_pattern_code_space_and_roof():
    assert codelists.detect({"bldg:function": "31001_1120"}) == "ADV"
    assert codelists.detect({"bldg:function": "x"}, {"bldg:function": "http://repository.gdi-de.org/x"}) == "ADV"
    assert codelists.detect({"bldg:roofType": "3100"}) == "ADV"  # AdV-only roof code


def test_shared_or_missing_values_fall_back_to_default():
    assert codelists.detect({"bldg:function": "1000"}) == "OGC"
    assert codelists.detect({}) == "OGC"


def test_every_registry_row_loads_with_code_spaces():
    for row in codelists.registry():
        assert codelists.codes(row["id"], "bldg:function")
        assert codelists.code_space(row["id"], "bldg:function").startswith("http")


def test_lists_without_entries_are_text_fields():
    assert not codelists.has_codes("tran:function")
    assert codelists.has_codes("bldg:roofType")


def test_templates_use_codes_from_their_list():
    for tpl in codelists.templates():
        for list_id, values in tpl["values"].items():
            for attr, code in values.items():
                assert code in codelists.codes(list_id, attr), (tpl["id"], list_id, attr, code)
