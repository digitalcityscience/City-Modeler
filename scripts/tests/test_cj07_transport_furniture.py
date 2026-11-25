"""CJ-07: Transportation (CityJSON core profile) and CityFurniture in the CityJSON reader, writer and
browse index. No Blender.

Core profile (K3): Road, Railway, TransportSquare with TrafficArea / AuxiliaryTrafficArea semantic
surfaces. Section, Intersection and the traffic spaces have no core counterpart: they are flattened into
their road and the report counts them, id by id. A Track has no core type and is not written."""
import copy
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared import cityjson_open as co  # noqa: E402
from shared import cityjson_reader as cj  # noqa: E402
from shared import cityjson_writer as cw  # noqa: E402
from shared.browse import cityjson_index as cji  # noqa: E402
from shared.builder_payload import (implicit_placement, importer_properties, skipped_features,  # noqa: E402
                                    unsupported_reasons)
from shared.citygml_ir import iter_features, validate  # noqa: E402
from shared.citygml_reader import read_document as read_gml  # noqa: E402
from shared.citygml_sniff import sniff_file  # noqa: E402

IMPLICIT = ROOT / "test_files" / "CityFurniture" / "Implicit_CityGML3.gml"
NS = ('xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:core="http://www.opengis.net/citygml/3.0" '
      'xmlns:tran="http://www.opengis.net/citygml/transportation/3.0"')


def _ms(pid, x, mid=""):
    """A 1 m square at x (y 0..1, z 0) as a lod2MultiSurface property."""
    return (f'<core:lod2MultiSurface><gml:MultiSurface{mid}><gml:surfaceMember><gml:Polygon gml:id="{pid}">'
            f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{x} 0 0 {x + 1} 0 0 {x + 1} 1 0 '
            f'{x} 1 0 {x} 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
            '</gml:MultiSurface></core:lod2MultiSurface>')


# the CityGML 3.0 shape (what the 3.0 writer gives): Road -> Section -> TrafficSpace -> TrafficArea,
# an Intersection with its own surface, and a Track and a Square beside it. Ids with a dot: valid NCNames.
GML3 = f'''<core:CityModel {NS}>
<core:cityObjectMember><tran:Road gml:id="R.1"><tran:function>1</tran:function>{_ms("R.1_p", 100)}
 <tran:section><tran:Section gml:id="S1">
  <tran:trafficSpace><tran:TrafficSpace gml:id="TS1"><tran:granularity>lane</tran:granularity>
   <core:boundary><tran:TrafficArea gml:id="TA.1"><tran:surfaceMaterial>asphalt</tran:surfaceMaterial>
    {_ms("TA.1_p", 101)}</tran:TrafficArea></core:boundary></tran:TrafficSpace></tran:trafficSpace>
  <tran:auxiliaryTrafficSpace><tran:AuxiliaryTrafficSpace gml:id="AS1">
   <core:boundary><tran:AuxiliaryTrafficArea gml:id="AA1">{_ms("AA1_p", 102)}</tran:AuxiliaryTrafficArea>
   </core:boundary></tran:AuxiliaryTrafficSpace></tran:auxiliaryTrafficSpace>
 </tran:Section></tran:section>
 <tran:intersection><tran:Intersection gml:id="I1">{_ms("I1_p", 103)}</tran:Intersection></tran:intersection>
</tran:Road></core:cityObjectMember>
<core:cityObjectMember><tran:Track gml:id="TR1">{_ms("TR1_p", 200)}</tran:Track></core:cityObjectMember>
<core:cityObjectMember><tran:Square gml:id="SQ1">{_ms("SQ1_p", 300)}</tran:Square></core:cityObjectMember>
<core:cityObjectMember><tran:Railway gml:id="RW1">{_ms("RW1_p", 400)}</tran:Railway></core:cityObjectMember>
</core:CityModel>'''


def gml_roots(text=GML3):
    return read_gml(ET.fromstring(text), upgrade=False)


def mm(p):
    return tuple(round(c * 1000) for c in p[:3])


def ir_coords(roots, skip=()):
    """1 mm coordinate set of every polygon below ``roots`` (features named in ``skip`` left out)."""
    out = set()

    def visit(n):
        if n.gml_id in skip:
            return
        for g in n.geometries.values():
            out.update(mm(c) for p in g.polygons for r in (p.exterior, *p.interiors) for c in r.coords)
        for c in (*getattr(n, "boundaries", ()), *getattr(n, "spaces", ())):
            visit(c)
    for r in roots:
        visit(r)
    return out


def doc_world(doc, i):
    s, t = doc["transform"]["scale"], doc["transform"]["translate"]
    return tuple(doc["vertices"][i][k] * s[k] + t[k] for k in range(3))


def doc_coords(doc):
    """1 mm coordinate set of every surface polygon and every placed template of ``doc``."""
    out = set()

    def idx(b):
        return [i for x in b for i in idx(x)] if isinstance(b, list) else [b]
    gt = doc.get("geometry-templates") or {}
    for o in doc["CityObjects"].values():
        for g in o["geometry"]:
            if g["type"] != "GeometryInstance":
                out.update(mm(doc_world(doc, i)) for i in idx(g["boundaries"]))
                continue
            m, ref = g["transformationMatrix"], doc_world(doc, g["boundaries"][0])
            for i in idx(gt["templates"][g["template"]]["boundaries"]):
                x, y, z = gt["vertices-templates"][i]
                out.add(mm([m[4 * k] * x + m[4 * k + 1] * y + m[4 * k + 2] * z + m[4 * k + 3] + ref[k]
                            for k in range(3)]))
    return out


def ids_by_type(roots):
    out = {}
    for r in roots:
        stack = [r]
        while stack:
            n = stack.pop()
            out[n.gml_id] = getattr(n, "feature_type", "") or n.surface_type
            stack += [*getattr(n, "boundaries", ()), *getattr(n, "spaces", ())]
    return out


def doc_ids(doc):
    ids = set(doc["CityObjects"])
    for o in doc["CityObjects"].values():
        for g in o["geometry"]:
            ids.update(s["id"] for s in (g.get("semantics") or {}).get("surfaces", ()))
    return ids


# -- writer: the core profile and its loss report --------------------------------------------------

def test_road_is_flattened_and_the_report_counts_exactly_what_is_missing():
    roots = gml_roots()
    doc, rep = cw.write_document(roots, "EPSG:25832")
    assert cw.validate_document(doc) == []
    o = doc["CityObjects"]
    assert {k: v["type"] for k, v in o.items()} == {"R.1": "Road", "SQ1": "TransportSquare", "RW1": "Railway"}
    (g,) = o["R.1"]["geometry"]
    assert (g["type"], g["lod"], len(g["boundaries"])) == ("MultiSurface", "2", 4)
    sems = g["semantics"]["surfaces"]
    assert sems == [{"type": "TrafficArea", "id": "TA.1", "surfaceMaterial": "asphalt"},
                    {"type": "AuxiliaryTrafficArea", "id": "AA1"}]
    assert sorted(v for v in g["semantics"]["values"] if v is not None) == [0, 1]
    assert g["semantics"]["values"].count(None) == 2          # the road's own surface and the intersection's
    assert o["R.1"]["attributes"] == {"function": "1"}
    assert "children" not in o["R.1"] and "semantics" not in o["SQ1"]["geometry"][0]

    # the report, against an independent count: ids of the IR that are not in the file, by type
    before = ids_by_type(roots)
    missing = Counter(t for i, t in before.items() if i not in doc_ids(doc))
    assert missing == Counter({"Section": 1, "TrafficSpace": 1, "AuxiliaryTrafficSpace": 1, "Intersection": 1,
                               "Track": 1})
    assert rep["reduced"] == {"Section": 1, "TrafficSpace": 1, "AuxiliaryTrafficSpace": 1, "Intersection": 1}
    assert rep["reduced_ids"] == {"Section": ["S1"], "TrafficSpace": ["TS1"], "AuxiliaryTrafficSpace": ["AS1"],
                                  "Intersection": ["I1"]}
    assert rep["not_written"] == {"Track": 1} and rep["written"] == ["R.1", "SQ1", "RW1"]
    assert dict(missing) == {**rep["reduced"], **rep["not_written"]}
    notes = cw.loss_notes(rep)
    assert len(notes) == 2 and "1 Intersection, 1 Section, 1 TrafficSpace" in notes[0] and "1 Track" in notes[1]
    # no coordinate is lost but the Track's
    assert doc_coords(doc) == ir_coords(roots, skip={"TR1"})
    assert ir_coords(roots) - doc_coords(doc) == ir_coords([r for r in roots if r.gml_id == "TR1"])


def test_no_transportation_no_loss_notes():
    doc, rep = cw.write_document([r for r in gml_roots() if r.gml_id in ("SQ1", "RW1")])
    assert rep["reduced"] == {} and rep["not_written"] == {} and cw.loss_notes(rep) == []


def test_a_road_repeating_its_area_polygons_keeps_their_class():
    """CityGML 2.0 shape: the areas are child spaces; the road's own surface repeats an area's polygon."""
    road = gml_roots(f'''<core:CityModel {NS}><core:cityObjectMember><tran:Road gml:id="R">{_ms("TA_p", 5)}
      <tran:trafficArea><tran:TrafficArea gml:id="TA">{_ms("TA_p", 5)}</tran:TrafficArea></tran:trafficArea>
      </tran:Road></core:cityObjectMember></core:CityModel>''')
    doc, rep = cw.write_document(road)
    (g,) = doc["CityObjects"]["R"]["geometry"]
    assert len(g["boundaries"]) == 1 and g["semantics"]["values"] == [0] and rep["reduced"] == {}


# -- reader ----------------------------------------------------------------------------------------

def test_cityjson_road_is_the_ir_of_a_citygml2_road():
    doc, _rep = cw.write_document(gml_roots(), "EPSG:25832")
    warnings, skipped = [], []
    roots = cj.read_document(json.loads(json.dumps(doc)), warnings, skipped)
    assert warnings == [] and skipped == [] and all(validate(r) == [] for r in roots)
    road, square, railway = roots
    assert [r.feature_type for r in roots] == ["Road", "Square", "Railway"]
    assert (road.gml_id, road.schema_version, road.boundaries) == ("R.1", "3.0", ())       # valid NCName: kept
    ta, aa = road.spaces
    assert (ta.gml_id, ta.feature_type, ta.source_role, ta.parent_id) == ("TA.1", "TrafficArea", "trafficArea", "R.1")
    assert (aa.gml_id, aa.feature_type, aa.source_role) == ("AA1", "AuxiliaryTrafficArea", "auxiliaryTrafficArea")
    assert len(road.geometries["lod2MultiSurface"].polygons) == 2 and len(ta.geometries["lod2MultiSurface"].polygons) == 1
    # the properties the builder writes: module keys, not bldg:
    assert importer_properties(road, schema="3.0")["tran:function"] == "1"
    assert importer_properties(ta, schema="3.0")["tran:surfaceMaterial"] == "asphalt"
    assert unsupported_reasons(roots, "3.0") == []          # the unified builder builds all of it
    # nothing more is lost on the way back: the second document is the first
    again, rep = cw.write_document(roots, "EPSG:25832")
    assert again == doc and rep["reduced"] == {} and rep["not_written"] == {}
    assert ir_coords(roots) == doc_coords(doc)


def test_unknown_semantics_on_a_road_and_children_are_counted():
    doc, _ = cw.write_document(gml_roots())
    road = doc["CityObjects"]["R.1"]
    road["geometry"][0]["semantics"]["surfaces"][1]["type"] = "TransportationMarking"
    road["children"] = ["X"]
    doc["CityObjects"]["X"] = {"type": "GenericCityObject", "parents": ["R.1"]}
    doc["CityObjects"]["T"] = {"type": "Tunnel"}
    warnings, skipped = [], []
    (r, _sq, _rw) = cj.read_document(doc, warnings, skipped)
    assert [s.feature_type for s in r.spaces] == ["TrafficArea"]
    assert len(r.geometries["lod2MultiSurface"].polygons) == 3 and any("TransportationMarking" in w for w in warnings)
    assert co.skipped_counts(skipped) == {"GenericCityObject": 1, "Tunnel": 1}


def test_wall_semantics_do_not_classify_a_road_nor_traffic_areas_a_building():
    doc, _ = cw.write_document(gml_roots())
    doc["CityObjects"]["R.1"]["geometry"][0]["semantics"]["surfaces"][0]["type"] = "WallSurface"
    doc["CityObjects"]["B"] = dict(copy.deepcopy(doc["CityObjects"]["RW1"]), type="Building")
    doc["CityObjects"]["B"]["geometry"][0]["semantics"] = {"surfaces": [{"type": "TrafficArea"}], "values": [0]}
    warnings = []
    roots = {r.gml_id: r for r in cj.read_document(doc, warnings)}
    assert [s.feature_type for s in roots["R.1"].spaces] == ["AuxiliaryTrafficArea"]
    assert roots["B"].boundaries == () and roots["B"].spaces == () and len(warnings) == 2


# -- CityFurniture: templates ------------------------------------------------------------------------

def placements(roots):
    out = {}
    for r in roots:
        (key, g), = r.geometries.items()
        out[r.gml_id] = (key, [[round(v, 6) for v in row] for row in implicit_placement(g, (0, 0, 0)).matrix],
                         len(g.polygons))
    return out


def test_implicit_furniture_becomes_geometry_instances_and_back():
    roots = gml_roots(IMPLICIT.read_text(encoding="utf-8"))
    assert len(roots) == 4 and unsupported_reasons(roots, "3.0") == []
    doc, rep = cw.write_document(roots, "EPSG:25832")
    assert cw.validate_document(doc) == [] and rep["not_written"] == {} and rep["warnings"] == []
    assert rep["written"] == [r.gml_id for r in roots]
    gt = doc["geometry-templates"]
    # equal templates are written once: the four furnitures use two different shapes at most
    assert 1 <= len(gt["templates"]) <= 2 and all(t["type"] == "MultiSurface" and t["lod"] == "3" for t in gt["templates"])
    for r in roots:
        o = doc["CityObjects"][r.gml_id]
        (g,) = o["geometry"]
        assert o["type"] == "CityFurniture" and g["type"] == "GeometryInstance" and len(g["boundaries"]) == 1
        assert g["transformationMatrix"][3::4] == [0.0, 0.0, 0.0, 1.0]     # the translation is the reference point
        assert set(o["attributes"]) <= {"class", "function", "usage"}
    back = cj.read_document(json.loads(json.dumps(doc)))
    assert unsupported_reasons(back, "3.0") == [] and not skipped_features(back, "3.0")[0]
    assert placements(back) == placements(roots)                           # where each one stands, to 1e-6
    def props(n):    # without the reader's bookkeeping of the LoD text
        return {k: v for k, v in importer_properties(n, schema="3.0").items() if k != "cgml3_cityjson_lods"}
    assert [props(b) for b in back] == [props(r) for r in roots]
    # the placed coordinates, computed from the document itself, are the placed coordinates of the GML
    want = set()
    for r in roots:
        (g,) = r.geometries.values()
        m = implicit_placement(g, (0, 0, 0)).matrix
        want.update(mm([sum(m[k][j] * c[j] for j in range(3)) + m[k][3] for k in range(3)])
                    for p in g.polygons for c in p.exterior.coords)
    assert doc_coords(doc) == want
    again, _ = cw.write_document(back, "EPSG:25832")
    assert again == doc


def test_furniture_with_plain_surfaces_and_lod_choice():
    doc, _ = cw.write_document(gml_roots(IMPLICIT.read_text(encoding="utf-8")))
    doc["CityObjects"]["F_PLAIN"] = {"type": "CityFurniture", "attributes": {"function": "1000", "note": "x"},
                                     "geometry": [{"type": "MultiSurface", "lod": "2", "boundaries": [[[0, 0, 0]]]}]}
    doc["vertices"] += [[0, 0, 0], [1000, 0, 0], [0, 1000, 0]]
    n = len(doc["vertices"])
    doc["CityObjects"]["F_PLAIN"]["geometry"][0]["boundaries"] = [[[n - 3, n - 2, n - 1]]]
    roots = {r.gml_id: r for r in cj.read_document(copy.deepcopy(doc))}
    plain = roots["F_PLAIN"]
    assert list(plain.geometries) == ["lod2MultiSurface"]
    assert plain.extra["thematic_attributes"] == {"frn:function": [{"value": "1000"}]}
    assert [(a.name, a.value) for a in plain.attrs.generic_attributes] == [("note", "x")]
    # the LoD choice of the Open dialog holds for instances too (their LoD is the template's)
    only2 = cj.read_document(copy.deepcopy(doc), lod="2")
    kept, missing = co.with_geometry(only2)
    assert [r.gml_id for r in kept] == ["F_PLAIN"] and missing == 4
    assert co.unloaded_lods(doc, "3") == {"2": 1}           # instances are not counted as LoD ''


@pytest.mark.parametrize("change, text", [
    (lambda g, gt: g.update(template=9), "template 9 not in geometry-templates"),
    (lambda g, gt: g.update(boundaries=[0, 1]), "one reference vertex"),
    (lambda g, gt: g.update(boundaries=[10 ** 6]), "one reference vertex"),
    (lambda g, gt: g.update(transformationMatrix=[1.0] * 12), "16 numbers"),
    (lambda g, gt: gt["templates"][0]["boundaries"][0][0].append(10 ** 6), "out of range"),
    (lambda g, gt: gt.update({"vertices-templates": [[0, 0]]}), "vertices-templates"),
])
def test_broken_instances_fail_the_self_check_and_the_reader(change, text):
    doc, _ = cw.write_document(gml_roots(IMPLICIT.read_text(encoding="utf-8")))
    first = next(iter(doc["CityObjects"].values()))["geometry"][0]
    assert first["template"] == 0
    change(first, doc["geometry-templates"])
    problems = cw.validate_document(doc)
    assert problems and any(text in p for p in problems), problems
    with pytest.raises(cj.CityJSONError, match=text):
        cj.read_document(doc)
    with pytest.raises(cj.CityJSONError):
        cw.save(str(Path(__file__).with_name("never_written.city.json")), doc)
    assert not Path(__file__).with_name("never_written.city.json").exists()


# -- Open and Browse first -----------------------------------------------------------------------------

@pytest.fixture()
def mixed(tmp_path):
    roots = gml_roots() + gml_roots(IMPLICIT.read_text(encoding="utf-8"))
    doc, _ = cw.write_document(roots, "EPSG:25832")
    doc["CityObjects"]["W"] = {"type": "WaterBody", "geometry": []}
    path, seq = tmp_path / "mixed.city.json", tmp_path / "mixed.city.jsonl"
    cw.save(str(path), doc)
    cw.save(str(seq), doc, seq=True)
    return doc, path, seq


def test_sniff_counts_the_new_types(mixed):
    doc, path, _seq = mixed
    s = sniff_file(str(path))
    assert (s.format, s.features_estimate) == ("CityJSON", 7)      # Road, Square, Railway, 4 CityFurniture
    assert co.open_decision(s)[0] == "cityjson" and co.open_decision(s, threshold_features=6)[0] == "browse"


@pytest.mark.parametrize("which", ["document", "seq"])
def test_index_boxes_and_selection_of_roads_and_furniture(mixed, which):
    doc, path, seq = mixed
    idx = cji.index_cityjson(str(path if which == "document" else seq))
    by_id = {e.gml_id: e for e in idx.entries}
    assert {e.gml_id: e.feature_type for e in idx.entries} == {
        "R.1": "Road", "SQ1": "TransportSquare", "RW1": "Railway",
        "F_A": "CityFurniture", "F_B": "CityFurniture", "F_C": "CityFurniture", "F_D": "CityFurniture"}
    assert idx.skipped_kinds == {"WaterBody": 1}
    assert all(e.bbox is not None for e in idx.entries)
    r = by_id["R.1"]
    assert r.bbox == pytest.approx((100, 0, 0, 104, 1, 0)) and r.lod_texts == ("2",) and r.lods == ("lod2MultiSurface",)
    # a furniture's box is the box of its placed template, not of its reference point
    roots = {x.gml_id: x for x in gml_roots(IMPLICIT.read_text(encoding="utf-8"))}
    for fid in ("F_A", "F_B", "F_C", "F_D"):
        (g,) = roots[fid].geometries.values()
        m = implicit_placement(g, (0, 0, 0)).matrix
        pts = [[sum(m[k][j] * c[j] for j in range(3)) + m[k][3] for k in range(3)]
               for p in g.polygons for c in p.exterior.coords]
        want = [min(p[k] for p in pts) for k in range(3)] + [max(p[k] for p in pts) for k in range(3)]
        assert by_id[fid].bbox == pytest.approx(want, abs=1e-3) and by_id[fid].lod_texts == ("3",)
        assert by_id[fid].bbox[3] - by_id[fid].bbox[0] > 0.1
    # Load: the small document of two picks keeps the templates and reads like the whole file
    picks = [i for i, e in enumerate(idx.entries) if e.gml_id in ("R.1", "F_B")]
    sub, _n = cji.selection_document(idx, picks)
    assert set(sub["CityObjects"]) == {"R.1", "F_B"} and cw.validate_document(sub) == []
    small = {x.gml_id: x for x in cj.read_document(sub)}
    whole = {x.gml_id: x for x in cj.read_document(copy.deepcopy(doc))}
    assert ir_coords(small.values()) == ir_coords([whole["R.1"], whole["F_B"]])
    assert placements([small["F_B"]]) == placements([whole["F_B"]])
    assert [s.gml_id for s in small["R.1"].spaces] == ["TA.1", "AA1"]


def test_every_feature_of_the_ir_has_an_id_the_writer_kept_or_reported():
    """No silent loss: each feature id of the IR is in the file or in the report."""
    roots = gml_roots() + gml_roots(IMPLICIT.read_text(encoding="utf-8"))
    doc, rep = cw.write_document(roots)
    reported = {i for ids in rep["reduced_ids"].values() for i in ids}
    top_not_written = {r.gml_id for r in roots if r.feature_type not in cw.TOP_TYPES}
    for r in roots:
        for f in iter_features(r):
            assert f.gml_id in doc_ids(doc) or f.gml_id in reported or f.gml_id in top_not_written, f.gml_id
    assert sum(rep["not_written"].values()) == len(top_not_written) == 1
