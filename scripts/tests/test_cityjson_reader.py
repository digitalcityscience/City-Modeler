"""CJ-01: CityJSON 2.0 / 1.1 -> SpaceNode IR (shared/cityjson_reader.py). No Blender.

The two sample CityJSON files must give the same surfaces (count, class) and the
same coordinates (1 mm) as the CityGML 3.0 copies of the same buildings read
by shared/citygml_reader.py."""
import copy
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import cityjson_reader as cj  # noqa: E402
from shared.builder_payload import scope_notice  # noqa: E402
from shared.citygml_ir import (SpaceNode, ThematicSurfaceNode, ir_from_dict, ir_to_dict,  # noqa: E402
                               iter_features, validate)
from shared.citygml_reader import read_document as read_gml  # noqa: E402

SAMPLES = Path(os.environ.get("CITY_MODELER_SAMPLES")
               or Path.home() / "Desktop" / "blender_test_files" / "buildings")
PAIRS = [("buildings_lod2_cityjson_2_0.city.json", "buildings_lod2_citygml_3_0.gml"),
         ("buildings_lod3_cityjson_2_0.city.json", "buildings_lod3_citygml_3_0.gml")]


def mm(p):
    return tuple(round(c * 1000) for c in p)


def fingerprint(roots):
    """Per feature id: class, polygon count, inner ring count and the 1 mm coordinate set."""
    out = {}
    for r in roots:
        for f in iter_features(r):
            polys = [p for g in f.geometries.values() for p in g.polygons]
            kind = f.surface_type if isinstance(f, ThematicSurfaceNode) else f.feature_type
            out[f.gml_id] = (kind, len(polys), sum(len(p.interiors) for p in polys),
                             frozenset(mm(c) for p in polys for ring in (p.exterior, *p.interiors)
                                       for c in ring.coords))
    return out


@pytest.mark.parametrize("cityjson,gml", PAIRS)
def test_samples_match_citygml_3_ir(cityjson, gml):
    if not (SAMPLES / cityjson).exists() or not (SAMPLES / gml).exists():
        pytest.skip(f"sample data not in {SAMPLES}")
    warnings, skipped = [], []
    mine = cj.read_document(cj.load(SAMPLES / cityjson), warnings, skipped)
    theirs = read_gml(ET.parse(SAMPLES / gml).getroot())
    assert warnings == [] and skipped == []
    assert all(validate(r) == [] for r in mine)
    a, b = fingerprint(mine), fingerprint(theirs)
    assert set(a) == set(b)
    assert Counter(v[0] for v in a.values()) == Counter(v[0] for v in b.values())
    diff = {k: (a[k][:3], b[k][:3]) for k in a if a[k] != b[k]}
    assert diff == {}
    assert all(r.schema_version == "3.0" for r in mine)


# -- small documents -------------------------------------------------------------------------

def cube_doc():
    """10 m cube; the front wall has a hole filled by a window, the back wall has no semantics."""
    m = 10000
    v = [[0, 0, 0], [m, 0, 0], [m, m, 0], [0, m, 0], [0, 0, m], [m, 0, m], [m, m, m], [0, m, m],
         [3000, 0, 3000], [7000, 0, 3000], [7000, 0, 7000], [3000, 0, 7000]]
    solid = {"type": "Solid", "lod": "3",
             "boundaries": [[[[0, 3, 2, 1]], [[4, 5, 6, 7]], [[0, 1, 5, 4], [8, 11, 10, 9]],
                             [[1, 2, 6, 5]], [[2, 3, 7, 6]], [[3, 0, 4, 7]], [[8, 9, 10, 11]]]],
             "semantics": {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"},
                                        {"type": "WallSurface", "id": "W1", "children": [3]},
                                        {"type": "Window", "id": "WIN1", "parent": 2}],
                           "values": [[0, 1, 2, 2, None, 2, 3]]}}
    return {"type": "CityJSON", "version": "2.0",
            "transform": {"scale": [0.001, 0.001, 0.001], "translate": [1000.0, 2000.0, 10.0]},
            "metadata": {"referenceSystem": "https://www.opengis.net/def/crs/EPSG/0/25832"},
            "vertices": v, "CityObjects": {"B1": {"type": "Building", "geometry": [solid]}}}


def read(doc, **kw):
    warnings, skipped = [], []
    roots = cj.read_document(doc, warnings, skipped, **kw)
    return roots, warnings, skipped


def surfaces(root):
    return {s.gml_id: s for s in root.boundaries}


def test_wall_with_hole_and_window():
    (b,), warnings, _ = read(cube_doc())
    assert warnings == [] and validate(b) == []
    s = surfaces(b)
    wall = s["W1"]
    assert wall.surface_type == "WallSurface" and set(wall.geometries) == {"lod3MultiSurface"}
    polys = wall.geometries["lod3MultiSurface"].polygons
    assert len(polys) == 3 and [len(p.interiors) for p in polys] == [1, 0, 0]   # inner ring kept
    hole = polys[0].interiors[0].coords
    assert hole[0] == hole[-1] and len(hole) == 5                                # closed ring
    (win,) = wall.openings
    assert (win.feature_type, win.gml_id, win.host_id, win.source_role) == \
        ("WindowSurface", "WIN1", "W1", "fillingSurface")
    assert len(win.geometries["lod3MultiSurface"].polygons) == 1
    assert sorted(x.surface_type for x in s.values()) == ["GroundSurface", "RoofSurface", "WallSurface"]


def test_world_coordinates_from_transform():
    (b,), _, _ = read(cube_doc())
    ground = next(x for x in b.boundaries if x.surface_type == "GroundSurface")
    assert ground.geometries["lod3MultiSurface"].polygons[0].exterior.coords[:2] == \
        ((1000.0, 2000.0, 10.0), (1000.0, 2010.0, 10.0))


def test_null_semantics_stay_unclassified_in_the_solid():
    (b,), _, _ = read(cube_doc())
    solid = b.geometries["lod3Solid"]
    assert solid.gml_type == "Solid" and len(solid.polygons) == 1          # the back wall only
    assert len(solid.extra["member_hrefs"]) == 6
    assert not any(s.surface_type == "ClosureSurface" for s in b.boundaries)   # no invented class


def test_null_semantics_in_a_multisurface():
    doc = cube_doc()
    g = doc["CityObjects"]["B1"]["geometry"][0]
    g.update(type="MultiSurface", boundaries=g["boundaries"][0])
    g["semantics"]["values"] = g["semantics"]["values"][0]
    (b,), _, _ = read(doc)
    assert len(b.geometries["lod3MultiSurface"].polygons) == 1
    assert "member_hrefs" not in b.geometries["lod3MultiSurface"].extra


def test_lod_text_kept_and_second_geometry_of_same_lod():
    doc = cube_doc()
    g = doc["CityObjects"]["B1"]["geometry"]
    g[0]["lod"] = "2.2"
    second = copy.deepcopy(g[0])
    second["lod"] = "2.1"
    g.append(second)
    (b,), warnings, _ = read(doc)
    assert b.geometries["lod2Solid"].extra["cityjson_lod"] == "2.2"
    assert surfaces(b)["W1"].geometries["lod2MultiSurface"].extra["cityjson_lod"] == "2.2"
    assert any("second LoD 2 geometry" in w for w in warnings)
    (b,), warnings, _ = read(doc, lod="2.1")
    assert b.geometries["lod2Solid"].extra["cityjson_lod"] == "2.1" and warnings == []


def test_invalid_ids_are_mapped_and_kept():
    doc = cube_doc()
    doc["CityObjects"] = {"1{a.b}": doc["CityObjects"]["B1"]}
    doc["CityObjects"]["1{a.b}"]["geometry"][0]["semantics"]["surfaces"][2]["id"] = "w:1"
    (b,), _, _ = read(doc)
    assert b.gml_id == cj.gml_id_for("1{a.b}") and b.gml_id.startswith("ID_1_a_b__")
    assert b.extra["cgml3_cityjson_id"] == "1{a.b}"
    wall = next(s for s in b.boundaries if s.surface_type == "WallSurface")
    assert wall.extra["cgml3_cityjson_id"] == "w:1" and wall.openings[0].host_id == wall.gml_id
    assert cj.gml_id_for("a b") != cj.gml_id_for("a:b")     # the hash keeps them apart after both became a_b
    assert validate(b) == []


def test_valid_gml_ids_are_never_renamed():
    """xs:ID = NCName: a letter or '_' first, then letters, digits, '.', '-', '_' (Unicode letters too)."""
    valid = ("B.1", "W.B1", "B.001", "NL.IMBAG.Pand.0503100000000010", "DEHH_x-1", "_a", "Gebäude.7",
             "B_1_349009fa")
    assert [cj.gml_id_for(i) for i in valid] == list(valid)
    for bad in ("1B", "a b", "a:b", "", ".a", "-a", "a/b", "{a}"):
        mapped = cj.gml_id_for(bad)
        assert mapped != bad and mapped == cj.mapped_id(bad) and cj.gml_id_for(mapped) == mapped, bad
    doc = cube_doc()
    doc["CityObjects"] = {"B.1": doc["CityObjects"]["B1"]}
    doc["CityObjects"]["B.1"]["geometry"][0]["semantics"]["surfaces"][2]["id"] = "W.B1"
    (b,), _, _ = read(doc)
    wall = next(s for s in b.boundaries if s.surface_type == "WallSurface")
    assert (b.gml_id, wall.gml_id) == ("B.1", "W.B1")
    assert "cgml3_cityjson_id" not in b.extra and "cgml3_cityjson_id" not in wall.extra
    assert wall.openings[0].host_id == "W.B1" and validate(b) == []


def test_attributes_and_address():
    doc = cube_doc()
    o = doc["CityObjects"]["B1"]
    o["attributes"] = {"function": "31001_1000", "roofType": "1000", "measuredHeight": 12.5,
                       "storeysAboveGround": 3, "creationDate": "2024-01-01", "name": "x",
                       "count": 2, "ratio": 0.5, "flag": True, "missing": None, "list": [1, 2]}
    o["address"] = [{"ThoroughfareName": "Pilatuspool", "ThoroughfareNumber": "11", "PostCode": "20355",
                     "Locality": "Hamburg", "Country": "Germany",
                     "location": {"type": "MultiPoint", "boundaries": [8]}}]
    (b,), _, _ = read(doc)
    th = b.extra["thematic_attributes"]
    assert th["bldg:function"] == [{"value": "31001_1000"}] and th["bldg:roofType"] == [{"value": "1000"}]
    assert th["bldg:measuredHeight"] == [{"value": 12.5}] and th["bldg:storeysAboveGround"] == [{"value": 3}]
    assert b.attrs.creation_date == "2024-01-01"
    assert b.attrs.names == ("x",)   # CJ-03: "name" <-> gml:name, both ways
    assert {(a.name, a.type, a.value) for a in b.attrs.generic_attributes} == {
        ("count", "int", 2), ("ratio", "double", 0.5), ("flag", "string", "true")}
    assert b.extra["cityjson_raw_attributes"] == {"missing": None, "list": [1, 2]}
    (a,) = b.attrs.addresses
    assert (a.street, a.house_number, a.postal_code, a.locality, a.country) == \
        ("Pilatuspool", "11", "20355", "Hamburg", "Germany")
    assert a.multi_point == ((1003.0, 2000.0, 13.0),)


def test_parts_installations_and_scope():
    doc = cube_doc()
    objs = doc["CityObjects"]
    shell = copy.deepcopy(objs["B1"]["geometry"][0])
    for s in shell["semantics"]["surfaces"]:
        s.pop("id", None)   # generated ids then
    objs["B1"]["children"] = ["P1", "I1", "R1"]
    objs["P1"] = {"type": "BuildingPart", "parents": ["B1"], "geometry": [copy.deepcopy(shell)]}
    objs["I1"] = {"type": "BuildingInstallation", "parents": ["B1"],
                  "attributes": {"relationToConstruction": "outside"},
                  "geometry": [{"type": "MultiSurface", "lod": "3", "boundaries": [[[0, 1, 5, 4]]],
                                "semantics": {"surfaces": [{"type": "WallSurface"}], "values": [0]}}]}
    objs["R1"] = {"type": "BuildingRoom", "parents": ["B1"]}
    objs["W1"] = {"type": "WaterBody"}   # Road is read since CJ-07
    (b,), warnings, skipped = read(doc)
    part, inst = b.spaces
    assert (part.feature_type, part.source_role, part.parent_id) == ("BuildingPart", "buildingPart", "B1")
    assert (inst.feature_type, inst.source_role, inst.parent_id) == \
        ("BuildingInstallation", "buildingInstallation", "B1")
    assert inst.extra["thematic_attributes"] == {"con:relationToConstruction": [{"value": "outside"}]}
    assert inst.boundaries[0].surface_type == "WallSurface"
    ids = [f.gml_id for f in iter_features(b)]
    assert len(ids) == len(set(ids))   # generated ids of the part do not collide with the building's
    assert part.boundaries[2].openings[0].host_id == part.boundaries[2].gml_id
    assert skipped == ["BuildingRoom R1", "1 WaterBody"]
    assert "3DCityDB v5" in scope_notice(skipped)


def test_inner_shell_reported():
    doc = cube_doc()
    g = doc["CityObjects"]["B1"]["geometry"][0]
    g["boundaries"].append([[[8, 9, 10, 11]]])
    g["semantics"]["values"].append([None])
    (b,), warnings, skipped = read(doc)
    assert any("inner shell" in w for w in warnings) and any("inner shell" in s for s in skipped)


def test_version_1_1_reads_and_dict_round_trip():
    doc = cube_doc()
    doc["version"] = "1.1"
    (b,), _, _ = read(doc)
    assert ir_from_dict(ir_to_dict(b)) == b


@pytest.mark.parametrize("change,message", [
    (lambda d: d.update(version="1.0"), "cjio"),
    (lambda d: d.update(version="3.0"), "not supported"),
    (lambda d: d.pop("transform"), "transform"),
    (lambda d: (d.pop("transform"), d["vertices"][0].__setitem__(0, 0.5)), "CityJSON 1.0 layout"),
    (lambda d: d.update(type="FeatureCollection"), "not a CityJSON"),
    (lambda d: d["CityObjects"]["B1"]["geometry"][0]["boundaries"][0][0][0].__setitem__(0, 99),
     "out of range"),
    (lambda d: d["CityObjects"]["B1"]["geometry"][0]["semantics"].__setitem__(
        "values", [0, 1, 2, 2, None, 2, 3]), "wrong depth"),
    (lambda d: d["CityObjects"]["B1"]["geometry"][0]["semantics"].__setitem__(
        "values", [[0, 1, 2, 2, None, 2, 7]]), "out of range"),
    (lambda d: d["CityObjects"]["B1"]["geometry"][0].__setitem__("boundaries", [[[0, 1, 2]]]),
     "wrong depth"),
])
def test_broken_documents_are_refused(change, message):
    doc = cube_doc()
    change(doc)
    with pytest.raises(cj.CityJSONError, match=message):
        cj.read_document(doc)
