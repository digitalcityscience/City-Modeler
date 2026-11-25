"""CJ-03: IR -> CityJSON 2.0 / CityJSONSeq (shared/cityjson_writer.py). No Blender.

Mapping rows of plan section 4.1 both ways (reader table inverted), wall with hole + window, two
geometries in one LoD, non-NCName ids written back, vertex de-duplication, the self-check on broken
documents, CityJSONSeq read by the CJ-05 index, and CityJSON -> IR -> CityJSON fingerprints."""
import copy
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from cj_fingerprint import diff, fingerprint  # noqa: E402
from shared import cityjson_open as co  # noqa: E402
from shared import cityjson_reader as cj  # noqa: E402
from shared import cityjson_writer as cw  # noqa: E402
from shared.browse import cityjson_index as cji  # noqa: E402
from shared.citygml_ir import (CityObjectAttrs, GenericAttribute, GeometryNode, SpaceNode,  # noqa: E402
                               ThematicSurfaceNode)
from shared.citygml_reader import read_document as read_gml  # noqa: E402
from shared.semantic_tree import PolygonNode, RingNode  # noqa: E402
from test_cityjson_reader import cube_doc  # noqa: E402

SAMPLES = Path(os.environ.get("CITY_MODELER_SAMPLES")
               or Path.home() / "Desktop" / "blender_test_files" / "buildings")
MULTI = ROOT / "test_files" / "CityJSON" / "cj02_multi_lod.city.json"


def roundtrip(doc, **kw):
    """doc -> IR -> CityJSON -> IR: (first IR, written doc, report, second IR)."""
    roots = cj.read_document(copy.deepcopy(doc), **kw)
    out, rep = cw.write_document(roots, "EPSG:25832")
    assert cw.validate_document(out) == []
    return roots, out, rep, cj.read_document(json.loads(json.dumps(out)))


def world(doc, i):
    s, t = doc["transform"]["scale"], doc["transform"]["translate"]
    return tuple(round(doc["vertices"][i][k] * s[k] + t[k], 3) for k in range(3))


# -- mapping rows, both ways -----------------------------------------------------------------

ATTRS = {**{name: f"v_{name}" for name in cj.THEMATIC}, "measuredHeight": 12.5, "storeysAboveGround": 3,
         "creationDate": "2024-01-01", "terminationDate": "2030-01-01", "name": "Haus",
         "count": 2, "ratio": 0.5, "text": "t", "missing": None, "list": [1, 2], "obj": {"a": 1}}
ADDRESS = {"ThoroughfareName": "Pilatuspool", "ThoroughfareNumber": "11", "PostalCode": "20355",
           "Locality": "Hamburg", "AdministrativeArea": "HH", "Country": "Germany", "extra": "kept",
           "location": {"type": "MultiPoint", "lod": "1", "boundaries": [8]}}


@pytest.mark.parametrize("name", sorted(ATTRS))
def test_attribute_row_both_ways(name):
    doc = cube_doc()
    doc["CityObjects"]["B1"]["attributes"] = {name: ATTRS[name]}
    _, out, _, _ = roundtrip(doc)
    assert out["CityObjects"]["B1"].get("attributes") == {name: ATTRS[name]}


@pytest.mark.parametrize("key", sorted(k for k in ADDRESS if k != "location"))
def test_address_row_both_ways(key):
    doc = cube_doc()
    doc["CityObjects"]["B1"]["address"] = [{key: ADDRESS[key]}]
    _, out, _, _ = roundtrip(doc)
    assert out["CityObjects"]["B1"]["address"] == [{key: ADDRESS[key]}]


def test_address_location_and_postcode_spelling():
    doc = cube_doc()
    doc["CityObjects"]["B1"]["address"] = [dict(ADDRESS, PostCode="1", PostalCode=None)]
    del doc["CityObjects"]["B1"]["address"][0]["PostalCode"]
    _, out, _, back = roundtrip(doc)
    (a,) = out["CityObjects"]["B1"]["address"]
    assert a["PostalCode"] == "1" and "PostCode" not in a   # the 2.0 spelling is written
    assert world(out, a["location"]["boundaries"][0]) == world(doc, 8)
    assert back[0].attrs.addresses[0].multi_point == ((1003.0, 2000.0, 13.0),)


def test_thematic_from_citygml_ir_list_and_unknown():
    node = SpaceNode(gml_id="B", feature_type="Building", extra={"thematic_attributes": {
        "bldg:function": [{"value": "1000", "code_space": "x"}, {"value": "2000"}],
        "bldg:roofType": [{"value": "3100"}], "core:relativeToTerrain": [{"value": "entirelyAboveTerrain"}]}},
        attrs=CityObjectAttrs(generic_attributes=(
            GenericAttribute("d", "date", "2020-01-01"), GenericAttribute("s", "set", members=(
                GenericAttribute("i", "int", 4),)))))
    a = cw.attributes(node)
    assert a == {"function": ["1000", "2000"], "roofType": "3100", "relativeToTerrain": "entirelyAboveTerrain",
                 "d": "2020-01-01", "s": {"i": 4}}


def test_con_height_and_xal_address_from_citygml3():
    gml = """<core:CityModel xmlns:core="http://www.opengis.net/citygml/3.0"
      xmlns:bldg="http://www.opengis.net/citygml/building/3.0" xmlns:gml="http://www.opengis.net/gml/3.2"
      xmlns:con="http://www.opengis.net/citygml/construction/3.0" xmlns:xAL="urn:oasis:names:tc:ciq:xal:3">
     <core:cityObjectMember><bldg:Building gml:id="B">
      <con:height><con:Height><con:highReference>highestRoofEdge</con:highReference>
        <con:lowReference>lowestGroundPoint</con:lowReference><con:status>measured</con:status>
        <con:value uom="m">9.5</con:value></con:Height></con:height>
      <bldg:address><core:Address><core:xalAddress><xAL:Address>
        <xAL:Country><xAL:NameElement>Germany</xAL:NameElement></xAL:Country>
        <xAL:Locality xAL:Type="Town"><xAL:NameElement>Hamburg</xAL:NameElement></xAL:Locality>
        <xAL:Thoroughfare xAL:Type="Street"><xAL:NameElement>Pilatuspool</xAL:NameElement>
          <xAL:Number>11</xAL:Number></xAL:Thoroughfare>
        <xAL:PostCode><xAL:Identifier>20355</xAL:Identifier></xAL:PostCode>
      </xAL:Address></core:xalAddress></core:Address></bldg:address>
     </bldg:Building></core:cityObjectMember></core:CityModel>"""
    (b,) = read_gml(ET.fromstring(gml))
    out, _ = cw.write_document([b])
    o = out["CityObjects"]["B"]
    assert o["attributes"] == {"measuredHeight": 9.5}
    assert o["address"] == [{"ThoroughfareName": "Pilatuspool", "ThoroughfareNumber": "11",
                             "PostalCode": "20355", "Locality": "Hamburg", "Country": "Germany"}]


def test_door_address_goes_to_the_building_once():
    adr = cj.Address(street="S", house_number="1")
    door = SpaceNode(gml_id="D", feature_type="DoorSurface", attrs=CityObjectAttrs(addresses=(adr,)),
                     geometries={"lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(sq("d", 0.2),))})
    wall = ThematicSurfaceNode(gml_id="W", surface_type="WallSurface", openings=(door,), geometries={
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(sq("w", 0),))})
    b = SpaceNode(gml_id="B", feature_type="Building", boundaries=(wall,), attrs=CityObjectAttrs(addresses=(adr,)))
    out, _ = cw.write_document([b])
    assert out["CityObjects"]["B"]["address"] == [{"ThoroughfareName": "S", "ThoroughfareNumber": "1"}]


# -- geometry ---------------------------------------------------------------------------------

def sq(pid, x0, y=0.0, size=1.0, hole=None):
    pts = [(x0, y, 0.0), (x0 + size, y, 0.0), (x0 + size, y, size), (x0, y, size)]
    ring = lambda i, p: RingNode(f"{pid}_{i}", pid, "exterior" if i == 0 else "interior", tuple(p + p[:1]))  # noqa: E731
    return PolygonNode(pid, ring(0, pts), (ring(1, hole),) if hole else ())


def test_wall_with_hole_and_window():
    roots, out, _, back = roundtrip(cube_doc())
    g = out["CityObjects"]["B1"]["geometry"][0]
    assert g["type"] == "Solid" and g["lod"] == "3"
    shell, vals = g["boundaries"][0], g["semantics"]["values"][0]
    sems = g["semantics"]["surfaces"]
    holed = [i for i, p in enumerate(shell) if len(p) == 2]
    assert len(holed) == 1 and sems[vals[holed[0]]]["id"] == "W1"
    win = next(i for i, s in enumerate(sems) if s["type"] == "Window")
    assert sems[win]["id"] == "WIN1" and sems[win]["parent"] == sems.index(
        next(s for s in sems if s.get("id") == "W1")) and win in sems[sems[win]["parent"]]["children"]
    assert vals.count(None) == 1          # the back wall stays unclassified
    assert diff(fingerprint(roots), fingerprint(back)) == {}


def test_two_geometries_in_one_lod_are_both_written():
    wall = ThematicSurfaceNode(gml_id="W", surface_type="WallSurface", geometries={
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(sq("w1", 0), sq("w2", 1)))})
    b = SpaceNode(gml_id="B", feature_type="Building", boundaries=(wall,), geometries={
        "lod2Solid": GeometryNode(gml_type="Solid", polygons=(sq("w1", 0),)),   # inline copy of w1
        "lod2MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(sq("w2", 1), sq("x", 5)))})
    out, _ = cw.write_document([b])
    geoms = out["CityObjects"]["B"]["geometry"]
    assert [(g["type"], g["lod"]) for g in geoms] == [("Solid", "2"), ("MultiSurface", "2")]
    assert len(geoms[0]["boundaries"][0]) == 2          # w1 once, not twice
    assert geoms[1]["semantics"]["values"] == [0, None]  # w2 keeps its class, x has none
    assert cw.validate_document(out) == []


def test_non_ncname_ids_are_written_back():
    doc = cube_doc()
    objs = doc["CityObjects"]
    objs["1{a.b}"] = objs.pop("B1")
    sems = objs["1{a.b}"]["geometry"][0]["semantics"]["surfaces"]
    sems[2]["id"], sems[3]["id"] = "w:1", "9win"
    roots, out, rep, _ = roundtrip(doc)
    assert roots[0].gml_id != "1{a.b}"                   # mapped in the IR (K2)
    assert rep["written"] == ["1{a.b}"] and list(out["CityObjects"]) == ["1{a.b}"]
    ids = {s["id"] for s in out["CityObjects"]["1{a.b}"]["geometry"][0]["semantics"]["surfaces"]}
    assert {"w:1", "9win"} <= ids


def test_annotate_puts_scene_ids_and_lod_text_on_pivot_ir():
    (b,) = cj.read_document(cube_doc())
    for g in [*b.geometries.values(), *(g for s in b.boundaries for g in s.geometries.values())]:
        g.extra.pop("cityjson_lod", None)     # what the GML pivot gives: no LoD text, mapped ids
    wall = next(s for s in b.boundaries if s.gml_id == "W1")
    cw.annotate([b], {b.gml_id: "B.1", "W1": "W.1", wall.openings[0].gml_id: "Win.1"}, {b.gml_id: {"3": "3.1"}})
    out, _ = cw.write_document([b])
    g = out["CityObjects"]["B.1"]["geometry"][0]
    assert g["lod"] == "3.1" and {"W.1", "Win.1"} <= {s["id"] for s in g["semantics"]["surfaces"]}
    b2 = cj.read_document(cube_doc())[0]
    for g in b2.geometries.values():
        g.extra.pop("cityjson_lod", None)
    for s in b2.boundaries:
        for g in s.geometries.values():
            g.extra.pop("cityjson_lod", None)
    out2, _ = cw.write_document([b2])
    assert out2["CityObjects"]["B1"]["geometry"][0]["lod"] == "3"   # no stored text: the IR LoD


def test_vertex_dedup_and_same_shape_elsewhere():
    doc = cube_doc()
    doc["vertices"].append(list(doc["vertices"][0]))          # a duplicate vertex in the source
    twin = copy.deepcopy(doc["CityObjects"]["B1"])
    for s in twin["geometry"][0]["semantics"]["surfaces"]:
        s.pop("id", None)
    n = len(doc["vertices"])
    doc["vertices"] += [[x + 50000, y, z] for x, y, z in doc["vertices"]]   # same local shape, 50 m east
    twin["geometry"][0]["boundaries"] = [[[[i + n for i in r] for r in p] for p in s]
                                         for s in twin["geometry"][0]["boundaries"]]
    doc["CityObjects"]["B2"] = twin
    roots, out, _, back = roundtrip(doc)
    assert len(out["vertices"]) == 24                        # 12 + 12, the duplicate merged
    assert len({tuple(v) for v in out["vertices"]}) == 24
    assert diff(fingerprint(roots), fingerprint(back)) == {}
    xs = sorted({world(out, i)[0] for i in range(24)})
    assert xs[0] == 1000.0 and xs[-1] == 1060.0


def test_points_closer_than_precision_collapse():
    p = PolygonNode("p", RingNode("r", "p", "exterior", ((0, 0, 0), (0.0001, 0, 0), (1, 0, 0), (1, 0, 1),
                                                         (0, 0, 1), (0, 0, 0))), ())
    b = SpaceNode(gml_id="B", feature_type="Building", geometries={
        "lod1MultiSurface": GeometryNode(gml_type="MultiSurface", polygons=(p,))})
    out, _ = cw.write_document([b])
    assert out["CityObjects"]["B"]["geometry"][0]["boundaries"] == [[[0, 1, 2, 3]]]


def test_parts_installations_scope_and_report():
    doc = cube_doc()
    objs = doc["CityObjects"]
    shell = copy.deepcopy(objs["B1"]["geometry"][0])
    for s in shell["semantics"]["surfaces"]:
        s.pop("id", None)
    objs["B1"]["children"] = ["P1", "I1", "R1"]
    objs["P1"] = {"type": "BuildingPart", "parents": ["B1"], "geometry": [copy.deepcopy(shell)]}
    objs["I1"] = {"type": "BuildingInstallation", "parents": ["B1"],
                  "geometry": [{"type": "MultiSurface", "lod": "3", "boundaries": [[[0, 1, 5, 4]]]}]}
    objs["R1"] = {"type": "BuildingRoom", "parents": ["B1"]}
    objs["T1"] = {"type": "TINRelief"}
    roots, out, rep, back = roundtrip(doc)
    o = out["CityObjects"]
    assert set(o) == {"B1", "P1", "I1"} and o["B1"]["children"] == ["P1", "I1"]
    assert o["P1"]["parents"] == ["B1"] and o["I1"]["parents"] == ["B1"]
    assert diff(fingerprint(roots), fingerprint(back)) == {}
    roots.append(SpaceNode(gml_id="T", feature_type="Tunnel"))   # Road is written since CJ-07
    _, rep = cw.write_document(roots)
    assert rep["not_written"] == {"Tunnel": 1} and rep["written"] == ["B1"]


def test_crs_url():
    assert cw.crs_url("EPSG:25832") == "https://www.opengis.net/def/crs/EPSG/0/25832"
    assert cw.crs_url("urn:ogc:def:crs:EPSG::25832") == "https://www.opengis.net/def/crs/EPSG/0/25832"
    assert cw.crs_url("https://www.opengis.net/def/crs/EPSG/0/7415").endswith("/EPSG/0/7415")
    assert cw.crs_url("urn:ogc:def:crs,crs:EPSG::3068,crs:EPSG::5783") == (
        "https://www.opengis.net/def/crs-compound?1=https://www.opengis.net/def/crs/EPSG/0/3068"
        "&2=https://www.opengis.net/def/crs/EPSG/0/5783")
    assert cw.crs_url("Unknown CRS") == ""


def test_transform_extent_and_world_coordinates():
    _, out, _, _ = roundtrip(cube_doc())
    assert out["transform"] == {"scale": [0.001] * 3, "translate": [1000.0, 2000.0, 10.0]}
    assert out["metadata"]["geographicalExtent"] == [1000.0, 2000.0, 10.0, 1010.0, 2010.0, 20.0]
    assert out["metadata"]["referenceSystem"].endswith("/EPSG/0/25832")
    assert min(min(v) for v in out["vertices"]) == 0


# -- self-check ----------------------------------------------------------------------------------

def broken(fn):
    _, out, _, _ = roundtrip(cube_doc())
    fn(out)
    return cw.validate_document(out)


@pytest.mark.parametrize("label,fn,needle", [
    ("index", lambda d: d["CityObjects"]["B1"]["geometry"][0]["boundaries"][0][0][0].append(99), "out of range"),
    ("depth", lambda d: d["CityObjects"]["B1"]["geometry"][0]["semantics"].update(values=[0, 1]), "semantic values"),
    ("value", lambda d: d["CityObjects"]["B1"]["geometry"][0]["semantics"]["values"][0].__setitem__(0, 9), "out of range"),
    ("sem parent", lambda d: d["CityObjects"]["B1"]["geometry"][0]["semantics"]["surfaces"][0].update(parent=1),
     "does not list it"),
    ("empty", lambda d: d["CityObjects"]["B1"]["geometry"][0].update(boundaries=[]), "empty Solid"),
    ("children", lambda d: d["CityObjects"]["B1"].update(children=["X"]), "child 'X'"),
    ("parents", lambda d: d["CityObjects"].update(P={"type": "BuildingPart", "parents": ["B1"]}), "does not list it"),
    ("transform", lambda d: d.pop("transform"), "transform"),
    ("float vertex", lambda d: d["vertices"].append([0.5, 0, 0]), "integers"),
])
def test_self_check_catches(label, fn, needle):
    problems = broken(fn)
    assert any(needle in p for p in problems), (label, problems)


def test_save_keeps_old_file_when_check_fails(tmp_path):
    target = tmp_path / "out.city.json"
    target.write_text("OLD", encoding="utf-8")
    _, out, _, _ = roundtrip(cube_doc())
    bad = copy.deepcopy(out)
    bad["CityObjects"]["B1"]["children"] = ["missing"]
    with pytest.raises(cj.CityJSONError):
        cw.save(str(target), bad)
    assert target.read_text(encoding="utf-8") == "OLD" and os.listdir(tmp_path) == ["out.city.json"]
    cw.save(str(target), out)
    assert json.loads(target.read_text(encoding="utf-8")) == out


# -- CityJSONSeq ---------------------------------------------------------------------------------

def sample_roots(name, lod=None):
    path = SAMPLES / name if not str(name).endswith("multi_lod.city.json") else MULTI
    if not path.exists():
        pytest.skip(f"{path} missing")
    return co.with_geometry(cj.read_document(cj.load(path), lod=lod))[0]


def test_seq_lines_are_indexed_and_read_back(tmp_path):
    roots = sample_roots("buildings_lod3_cityjson_2_0.city.json")
    doc, _ = cw.write_document(roots, "EPSG:25832")
    path = tmp_path / "out.city.jsonl"
    cw.save(str(path), doc, seq=True)
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "CityJSON" and lines[0]["CityObjects"] == {} and lines[0]["vertices"] == []
    assert [f["id"] for f in lines[1:]] == [r.gml_id for r in roots]
    assert all(max(_flat([g["boundaries"] for o in f["CityObjects"].values() for g in o["geometry"]]))
               == len(f["vertices"]) - 1 for f in lines[1:])   # own vertices, all used
    idx = cji.index_cityjson(str(path))
    assert [e.gml_id for e in idx.entries] == [r.gml_id for r in roots]
    sub, _ = cji.selection_document(idx, range(len(idx.entries)))
    assert diff(fingerprint(roots), fingerprint(cj.read_document(sub))) == {}


def _flat(b):
    return [x for y in b for x in (_flat(y) if isinstance(y, list) else [y])]


# -- fingerprints: CityJSON -> IR -> CityJSON -> IR, CityGML 3.0 -> IR -> CityJSON -> IR ---------

@pytest.mark.parametrize("name,lod", [("buildings_lod2_cityjson_2_0.city.json", None),
                                      ("buildings_lod3_cityjson_2_0.city.json", None),
                                      ("cj02_multi_lod.city.json", "2.2"), ("cj02_multi_lod.city.json", "1.2")])
def test_cityjson_fingerprint_round_trip(name, lod):
    roots = sample_roots(name, lod)
    doc, rep = cw.write_document(roots, "EPSG:25832")
    assert cw.validate_document(doc) == [] and rep["not_written"] == {}
    back = cj.read_document(json.loads(json.dumps(doc)))
    assert diff(fingerprint(roots), fingerprint(back)) == {}
    if lod:
        assert {g["lod"] for o in doc["CityObjects"].values() for g in o["geometry"]} == {lod}


@pytest.mark.parametrize("name", ["buildings_lod2_citygml_3_0.gml", "buildings_lod3_citygml_3_0.gml"])
def test_citygml3_ir_to_cityjson_fingerprint(name):
    if not (SAMPLES / name).exists():
        pytest.skip("sample missing")
    roots = read_gml(ET.parse(SAMPLES / name).getroot())
    doc, rep = cw.write_document(roots, "urn:ogc:def:crs:EPSG::25832")
    assert cw.validate_document(doc) == []
    a = fingerprint(roots, original_ids=False)
    b = fingerprint(cj.read_document(json.loads(json.dumps(doc))), original_ids=False)
    d = diff(a, b)
    # CityJSON's measuredHeight is a plain number in metres: the AdV uom URN is not kept
    for k, (x, y) in d.pop("props", {}).items():
        assert {p for p in set(map(tuple, x)) ^ set(map(tuple, y)) if p[0] != "con:height:uom"} == set(), k
    assert d == {}
