"""B88: element fields of the attribute card (bpy-free)."""
import json
from pathlib import Path

from shared import codelists

DATA = json.loads((Path(codelists.DIR) / "elements.json").read_text(encoding="utf-8"))


def test_every_feature_has_fields_from_an_existing_list():
    for feature, kind in DATA["features"].items():
        fields = codelists.element_fields(feature)
        assert fields and codelists.element_kind(feature) == kind, feature
        assert len(fields) <= 3  # ui/attribute_card.py ELEMENT_SLOTS
        for f in fields:
            assert f["codes"] and f["label"] and ("attr" in f) != ("name" in f), (feature, f)


def test_installation_fields_are_citygml_attributes_with_sig3d_code_spaces():
    fields = codelists.element_fields("BuildingInstallation")
    assert [f["attr"] for f in fields] == ["bldg:class", "bldg:function", "bldg:usage"]
    for f in fields:
        assert f["codeSpace"].startswith("http://www.sig3d.org/codelists/standard/building/2.0/BuildingInstallation_")
    assert fields[1]["codes"]["1000"] == "balcony" and fields[1]["codes"]["1030"].startswith("chimney")


def test_surfaces_and_openings_get_generic_attributes_only():
    for feature in ("WallSurface", "RoofSurface", "WindowSurface", "Window", "DoorSurface", "Door"):
        assert all("name" in f for f in codelists.element_fields(feature)), feature
    assert codelists.element_kind("Window") == codelists.element_kind("WindowSurface") == "window"


def test_other_features_have_no_element_fields():
    for feature in ("GroundSurface", "ClosureSurface", "Building", "Road", ""):
        assert codelists.element_fields(feature) == ()
