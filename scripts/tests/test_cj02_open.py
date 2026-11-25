"""CJ-02: opening CityJSON without bpy - sniff, dispatch decision, LoD list, thresholds, georeference,
skip counts, address and original-id carry-through into the builder payload."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared import cityjson_open as co  # noqa: E402
from shared import cityjson_reader as cj  # noqa: E402
from shared.citygml_sniff import SniffSummary, sniff_file  # noqa: E402
from shared.import_summary import ImportSummary, format_summary  # noqa: E402

MULTI = ROOT / "test_files" / "CityJSON" / "cj02_multi_lod.city.json"
SAMPLES = Path(os.environ.get("CITY_MODELER_SAMPLES")
               or Path.home() / "Desktop" / "blender_test_files" / "buildings")


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return str(p)


# -- sniff -------------------------------------------------------------------------------------

def test_sniff_cityjson_by_content_not_extension(tmp_path):
    s = sniff_file(write(tmp_path, "data.gml", MULTI.read_text(encoding="utf-8")))
    assert (s.format, s.is_citygml, s.version, s.exact) == ("CityJSON", False, "2.0", True)
    assert s.features_estimate == 3          # B.1, B2, B3; BuildingRoom/WaterBody are not buildings
    assert s.lods == ("1.2", "2.2")
    assert s.srs_name.endswith("EPSG/0/25832")
    assert s.label() == "CityJSON 2.0 · EPSG:25832 · 0.0 MB · 3 buildings · LoD 1.2, 2.2"


def test_sniff_cityjson_feature_lines(tmp_path):
    seq = ('{"type":"CityJSON","version":"2.0","transform":{"scale":[1,1,1],"translate":[0,0,0]},'
           '"CityObjects":{},"vertices":[]}\n'
           '{"type":"CityJSONFeature","id":"B1","CityObjects":{"B1":{"type":"Building","geometry":'
           '[{"type":"Solid","lod":"2.2","boundaries":[]}]}},"vertices":[]}\n')
    s = sniff_file(write(tmp_path, "x.jsonl", seq))
    assert s.format == "CityJSONSeq" and s.lods == ("2.2",)
    one = sniff_file(write(tmp_path, "f.json", seq.splitlines()[1]))
    assert one.format == "CityJSONSeq"


def test_sniff_gml_unchanged(tmp_path):
    gml = ('<?xml version="1.0"?><core:CityModel xmlns:core="http://www.opengis.net/citygml/3.0">'
           '<core:cityObjectMember/><core:cityObjectMember/></core:CityModel>')
    s = sniff_file(write(tmp_path, "a.json", gml))   # the extension does not decide
    assert (s.format, s.is_citygml, s.version, s.features_estimate, s.lods) == ("CityGML", True, "3.0", 2, ())
    assert s.label().startswith("CityGML 3.0") and s.label().endswith("2 features")


def test_sniff_broken_json_and_empty_file(tmp_path):
    broken = sniff_file(write(tmp_path, "b.json", '﻿  {"type": "CityJSON", "CityObjects": {"B1": {"ty'))
    assert broken.format == "CityJSON"         # the reader refuses it with a clear message
    with pytest.raises(json.JSONDecodeError):
        cj.load(broken.path)
    other = sniff_file(write(tmp_path, "o.json", '{"type": "FeatureCollection", "features": []}'))
    assert other.format == "JSON"
    empty = sniff_file(write(tmp_path, "e.json", b""))
    assert (empty.format, empty.is_citygml, empty.features_estimate) == ("", False, 0)


def test_sniff_token_cut_by_chunk_boundary(tmp_path, monkeypatch):
    from shared import citygml_sniff
    monkeypatch.setattr(citygml_sniff, "_JSON_CHUNK", 7)
    monkeypatch.setattr(citygml_sniff, "_JSON_OVERLAP", 40)
    s = sniff_file(write(tmp_path, "m.json", MULTI.read_text(encoding="utf-8")))
    assert (s.format, s.features_estimate, s.lods) == ("CityJSON", 3, ("1.2", "2.2"))


# -- dispatch and thresholds ------------------------------------------------------------------

def summary(fmt="CityJSON", n=3, mb=1.0):
    return SniffSummary("x", size=int(mb * 2**20), is_citygml=fmt == "CityGML", features_estimate=n,
                        exact=True, format=fmt)


def test_dispatch_decision():
    assert co.open_decision(summary("CityGML")) == ("citygml", "")
    assert co.open_decision(summary("")) == ("citygml", "")       # unknown: the GML path reports it
    assert co.open_decision(None) == ("citygml", "")
    assert co.open_decision(summary()) == ("cityjson", "")
    kind, text = co.open_decision(summary("CityJSONSeq"))
    assert kind == "browse" and "CityJSONSeq" in text        # CJ-05: Browse first, no longer refused
    assert co.open_decision(summary("JSON"))[0] == "error"


def test_thresholds():
    assert co.open_decision(summary(n=30)) == ("cityjson", "")
    kind, text = co.open_decision(summary(n=31))
    assert kind == "browse" and "31 buildings" in text       # CJ-05: above the threshold Browse first
    assert co.open_decision(summary(n=2000, mb=200.0))[0] == "browse"
    assert co.open_decision(summary(n=2001))[0] == "browse"  # the Load-all count limit is load_all_refusal's
    kind, text = co.open_decision(summary(mb=200.5))         # a single document is indexed in memory
    assert kind == "error" and "citydb import cityjson" in text and "3DCityDB v5" in text
    assert co.open_decision(summary(n=31), threshold_features=40) == ("cityjson", "")


# -- LoD -------------------------------------------------------------------------------------

def test_lod_choices_default_first():
    assert co.lod_choices(["1.2", "2.2", "2.0", "1.3"]) == ["2.2", "1.2", "1.3", "2.0"]
    assert co.lod_choices(["3", "1"]) == ["3", "1"]
    assert co.lod_choices(["2"]) == ["2"] and co.lod_choices([]) == []


def test_chosen_lod_skips_buildings_without_it():
    doc = cj.load(MULTI)
    warnings, skipped = [], []
    roots = cj.read_document(doc, warnings, skipped, lod="2.2")
    kept, missing = co.with_geometry(roots)
    assert [r.extra.get("cgml3_cityjson_id", r.gml_id) for r in kept] == ["B.1", "B3"] and missing == 1
    assert co.missing_lod_notice("2.2", missing) == "no LoD2.2: 1"
    assert all(g.extra["cityjson_lod"] == "2.2" for r in kept for b in r.boundaries
               for g in b.geometries.values())
    kept12, missing12 = co.with_geometry(cj.read_document(doc, lod="1.2"))
    assert len(kept12) == 2 and missing12 == 1          # B3 has only 2.2; never filled from it
    counts = co.skipped_counts(skipped)
    assert counts == {"BuildingRoom": 1, "WaterBody": 1}
    unloaded = co.unloaded_lods(doc, "2.2")
    assert unloaded == {"1.2": 2}
    items = co.not_loaded_items(counts, unloaded, "2.2", missing)
    assert items == ["1 BuildingRoom", "1 WaterBody", "2 LoD1.2 geometries", "no LoD2.2: 1"]
    text = format_summary(ImportSummary(feature_counts={"Building": 2}, not_loaded=items))
    assert "not loaded: 1 BuildingRoom, 1 WaterBody, 2 LoD1.2 geometries, no LoD2.2: 1" in text
    assert format_summary(ImportSummary(not_loaded=["no LoD2.2: 3"])).endswith("not loaded: no LoD2.2: 3")
    assert "BuildingRoom" in bp.scope_notice([f"{n} {k}" for k, n in counts.items()])


def test_surface_polygons_share_one_multisurface_id():
    # like the GML reader: without it the 3.0 writer mints an id per polygon and splits the surface
    root = cj.read_document(cj.load(MULTI), lod="2.2")[0]
    wall = next(b for b in root.boundaries if b.surface_type == "WallSurface")
    g = wall.geometries["lod2MultiSurface"]
    assert len(g.polygons) == 4 and g.gml_id == f"{wall.gml_id}_lod2"
    assert {(p.multisurface_id, p.is_multisurface_member) for p in g.polygons} == {(g.gml_id, True)}


def test_skipped_counts_kinds():
    assert co.skipped_counts(["2 BuildingRoom", "BuildingRoom R9", "InteriorWallSurface of Building 'B'",
                              "inner shell of Building 'B'", "1 TINRelief"]) == {
        "BuildingRoom": 3, "InteriorWallSurface": 1, "inner shell": 1, "TINRelief": 1}


# -- georeference ---------------------------------------------------------------------------

def test_georeference_from_extent_or_vertices():
    doc = cj.load(MULTI)
    crs, origin = co.georeference(doc)
    assert crs == "https://www.opengis.net/def/crs/EPSG/0/25832"
    # no geographicalExtent: vertex bounds 564800..564850 / 5934300..5934310 / z 10..20
    assert origin == (564800.0, 5934300.0, 10.0)
    doc["metadata"]["geographicalExtent"] = [564719.04, 5934183.51, 17.23, 564909.456, 5934436.156, 53.755]
    assert co.georeference(doc)[1] == (564800.0, 5934300.0, 17.23)
    assert co.georeference({"transform": {"scale": [1, 1, 1], "translate": [0, 0, 0]}, "vertices": []}) == (
        "", (0.0, 0.0, 0.0))


def test_georeference_matches_gml_envelope_of_sample():
    path = SAMPLES / "buildings_lod2_cityjson_2_0.city.json"
    if not path.exists():
        pytest.skip(f"sample data not in {SAMPLES}")
    from shared.import_origin import file_origin
    crs, origin = co.georeference(cj.load(path))
    # gml:Envelope of buildings_lod2_citygml_3_0.gml
    assert origin == file_origin((564719.040, 5934183.510, 17.230), (564909.456, 5934436.156, 53.755))
    assert crs.endswith("EPSG/0/25832")


# -- builder payload: address and original ids ------------------------------------------------

def test_cityjson_address_reaches_the_scene_properties():
    root = [r for r in cj.read_document(cj.load(MULTI), lod="2.2") if r.gml_id == "B.1"][0]
    props = bp.importer_properties(root, schema="3.0")
    assert {k: v for k, v in props.items() if k.startswith("bldg:address:")} == {
        "bldg:address:country": "Germany", "bldg:address:locality": "Hamburg",
        "bldg:address:street": "Pilatuspool", "bldg:address:streetNumber": "11",
        "bldg:address:postCode": "20355"}


def test_measured_height_becomes_the_3_0_height():
    doc = {"type": "CityJSON", "version": "2.0", "transform": {"scale": [1, 1, 1], "translate": [0, 0, 0]},
           "vertices": [], "CityObjects": {"B": {"type": "Building", "attributes": {"measuredHeight": 12.5}}}}
    props = bp.importer_properties(cj.read_document(doc)[0], schema="3.0")
    assert "bldg:measuredHeight" not in props
    assert {k: v for k, v in props.items() if k.startswith("con:height")} == {
        "con:height": "", "con:height:value": 12.5, "con:height:uom": "m", "con:height:status": "measured",
        "con:height:highReference": "highestRoofEdge", "con:height:lowReference": "lowestGroundPoint"}
    assert bp.importer_properties(cj.read_document(doc)[0], schema="2.0")["bldg:measuredHeight"] == 12.5


def test_gml_address_path_unchanged():
    from shared.citygml_ir import Address, CityObjectAttrs
    # a 3.0 xAL fragment that is not an <xAL:Address>: fields are not used (as before CJ-02)
    xal = '<xAL:AddressDetails xmlns:xAL="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0"/>'
    attrs = CityObjectAttrs(addresses=(Address(street="S", xal=xal),))
    assert bp.address_properties(attrs, "3.0") == {}
    assert bp.address_properties(attrs, "2.0") == {"bldg:address:street": "S"}


def test_valid_cityjson_ids_are_the_gml_ids():
    """K2 fix: "B.1" / "W.B1" are valid gml:ids, so nothing is mapped and nothing extra is stored."""
    root = [r for r in cj.read_document(cj.load(MULTI), lod="2.2") if r.gml_id == "B.1"][0]
    assert "W.B1" in [s.gml_id for s in root.boundaries]
    props = bp.feature_payload(root, (0.0, 0.0, 0.0), schema="3.0").properties
    assert "cgml3_cityjson_id" not in props and "cgml3_cityjson_surface_ids" not in props


def test_original_cityjson_ids_are_kept_on_the_object():
    """Only an id that is not a valid gml:id is mapped; the object keeps the file's own."""
    doc = cj.load(MULTI)
    doc["CityObjects"]["1B"] = doc["CityObjects"].pop("B.1")
    next(s for g in doc["CityObjects"]["1B"]["geometry"] for s in (g.get("semantics") or {}).get("surfaces", ())
         if s.get("id") == "W.B1")["id"] = "w:1"
    root = [r for r in cj.read_document(doc, lod="2.2") if r.extra.get("cgml3_cityjson_id")][0]
    assert root.gml_id == cj.gml_id_for("1B") and root.gml_id.startswith("ID_1B_")
    props = bp.feature_payload(root, (0.0, 0.0, 0.0), schema="3.0").properties
    assert props["cgml3_cityjson_id"] == "1B"
    assert json.loads(props["cgml3_cityjson_surface_ids"]) == {cj.gml_id_for("w:1"): "w:1"}
    plain = [r for r in cj.read_document(doc, lod="2.2") if r.gml_id == "B3"][0]
    plain_props = bp.importer_properties(plain, schema="3.0")
    assert "cgml3_cityjson_id" not in plain_props and "cgml3_cityjson_surface_ids" not in plain_props
