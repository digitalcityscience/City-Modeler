"""Pure helpers of scripts/headless/cac_harness.py (CAC-P0.1). No Blender."""
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


h = load_module("scripts/headless/cac_harness.py", "cac_harness_under_test")

HEX_A = "e03b1de3558b4a1f8ea4ac4505cc4d61"
HEX_B = "d28c36606bbd4153855cda7ea68482dc"
SOURCE_ID = "UUID_0a1b2c3d-0000-4000-8000-00000000abcd"


def test_normalise_renumbers_fresh_ids_consistently():
    text = (
        f'<a gml:id="ID_{HEX_A}"><b gml:id="ID_{HEX_A}_0_"/>'
        f'<c xlink:href="#ID_{HEX_B}"/><d gml:id="ID_{HEX_B}"/></a>'
    )
    assert h.normalise_xml(text) == (
        '<a gml:id="ID_FRESH0"><b gml:id="ID_FRESH0_0_"/>'
        '<c xlink:href="#ID_FRESH1"/><d gml:id="ID_FRESH1"/></a>'
    )


def test_normalise_is_independent_of_the_actual_uuids():
    one = f'<a gml:id="ID_{HEX_A}"/><b gml:id="ID_{HEX_B}"/>'
    two = f'<a gml:id="ID_{HEX_B}"/><b gml:id="ID_{HEX_A}"/>'
    assert h.normalise_xml(one) == h.normalise_xml(two)


def test_normalise_keeps_preserved_source_ids_and_readable_ids():
    text = f'<a gml:id="{SOURCE_ID}"/><b gml:id="PolyID7_1_2_3"/><c gml:id="GMLID_BUI1_2_3"/>'
    assert h.normalise_xml(text, preserve_ids={SOURCE_ID}) == text


def test_normalise_renumbers_short_opening_ids_only():
    text = ('<a v="UUID_Window_9a8e81a510e2"/><b v="UUID_Door_4fda34f4d253"/>'
            '<c v="MADoorSurface_Opening_Exterior_674348828228"/>')
    assert h.normalise_xml(text) == (
        '<a v="UUID_Window_FRESH0"/><b v="UUID_Door_FRESH1"/>'
        '<c v="MADoorSurface_Opening_Exterior_674348828228"/>'
    )


def test_normalise_blanks_creation_date():
    text = "<core:creationDate>2026-09-18</core:creationDate><creationDate>x</creationDate>"
    assert h.normalise_xml(text) == "<core:creationDate>X</core:creationDate><creationDate>X</creationDate>"


def test_normalise_masks_only_the_bpy_repr_address():
    text = '<v>&lt;bpy id prop: owner="MA1", name="g", address=0x000002196E452758&gt;</v>'
    assert h.normalise_xml(text) == '<v>&lt;bpy id prop: owner="MA1", name="g", address=0xX&gt;</v>'


def test_collect_ids_groups_by_local_name():
    xml = (
        '<r xmlns:gml="http://www.opengis.net/gml" xmlns:b="urn:b">'
        '<b:WallSurface gml:id="w2"/><b:WallSurface gml:id="w1"/>'
        '<gml:Polygon gml:id="p1"/><b:Other/></r>'
    )
    assert h.collect_ids(xml) == {"Polygon": ["p1"], "WallSurface": ["w1", "w2"]}


def test_collect_ids_reads_gml_3_2_ids():
    xml = ('<r xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:c="urn:c">'
           '<c:WindowSurface gml:id="win1"/></r>')
    assert h.collect_ids(xml) == {"WindowSurface": ["win1"]}


def test_normalise_json_renumbers_fresh_ids_in_values():
    data = {"a": {"poly": f"ID_{HEX_A}", "ring": f"ID_{HEX_A}_0_"}, "src": SOURCE_ID}
    assert h.normalise_json(data, preserve_ids={SOURCE_ID}) == {
        "a": {"poly": "ID_FRESH0", "ring": "ID_FRESH0_0_"}, "src": SOURCE_ID,
    }


FP_XML = f"""<CityModel xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:con="urn:con" xmlns:bldg="urn:bldg">
  <bldg:Building gml:id="B1">
    <con:WallSurface gml:id="W1">
      <gml:Polygon gml:id="P1"><gml:exterior/><gml:interior/><gml:interior/></gml:Polygon>
      <con:opening><con:WindowSurface gml:id="WIN1">
        <gml:Polygon gml:id="ID_{HEX_A}"><gml:exterior/></gml:Polygon>
      </con:WindowSurface></con:opening>
    </con:WallSurface>
    <con:Door gml:id="D0"/>
  </bldg:Building>
</CityModel>"""


def test_export_fingerprint():
    fp = h.export_fingerprint(FP_XML, preserve_ids={"B1", "W1", "P1", "WIN1", "D0"})
    assert fp["feature_ids"] == ["Building:B1", "Door:D0", "WallSurface:W1", "WindowSurface:WIN1"]
    assert fp["polygon_ids"] == ["ID_FRESH0", "P1"]
    assert fp["interior_rings"] == {"P1": 2}
    assert fp["opening_nesting"] == [[None, "D0"], ["W1", "WIN1"]]  # D0 has no host surface
    assert fp["element_counts"]["Polygon"] == 2
    assert fp["element_counts"]["interior"] == 2


def _poly(gml_id, ext, ints=(), use_pos=False):
    def ring(pts):
        if use_pos:
            inner = "".join(f"<gml:pos>{x} {y} {z}</gml:pos>" for x, y, z in pts)
        else:
            inner = "<gml:posList>" + " ".join(f"{x} {y} {z}" for x, y, z in pts) + "</gml:posList>"
        return f"<gml:LinearRing>{inner}</gml:LinearRing>"
    body = f"<gml:exterior>{ring(ext)}</gml:exterior>" + "".join(
        f"<gml:interior>{ring(i)}</gml:interior>" for i in ints)
    return f'<gml:Polygon gml:id="{gml_id}">{body}</gml:Polygon>'


def _root(*polys):
    return ET.fromstring(
        '<r xmlns:gml="http://www.opengis.net/gml">' + "".join(polys) + "</r>")


SQ = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)]
HOLE = [(0.2, 0.2, 0), (0.4, 0.2, 0), (0.4, 0.4, 0), (0.2, 0.2, 0)]


def test_coordinate_digest_detects_moved_vertex_and_ignores_encoding():
    base = h.coordinate_digests(_root(_poly("P", SQ, [HOLE])))
    assert base == h.coordinate_digests(_root(_poly("P", SQ, [HOLE], use_pos=True)))
    moved = [(0, 0, 0), (1, 0, 0), (1, 1.001, 0), (0, 1, 0), (0, 0, 0)]
    assert base != h.coordinate_digests(_root(_poly("P", moved, [HOLE])))
    assert base != h.coordinate_digests(_root(_poly("P", SQ)))  # hole removed
    # below 0.1 mm is float noise, not a change
    noise = [(0, 0, 0), (1.00000001, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, -0.00000001)]
    assert base == h.coordinate_digests(_root(_poly("P", noise, [HOLE])))


def test_coordinate_digests_keys_missing_and_repeated_ids():
    xml = _root(_poly("P", SQ), _poly("P", SQ), '<gml:Polygon><gml:exterior/></gml:Polygon>')
    assert sorted(h.coordinate_digests(xml)) == ["<no-id>#2", "P", "P#1"]


def test_detect_citygml_version():
    assert h.detect_citygml_version('xmlns:bldg="http://www.opengis.net/citygml/building/2.0" '
                                    'xmlns="http://www.opengis.net/citygml/2.0"') == "2.0"
    assert h.detect_citygml_version('xmlns="http://www.opengis.net/citygml/3.0"') == "3.0"
    assert h.detect_citygml_version("<x/>") is None


def test_compare_json_reports_readable_diff():
    ok, diff = h.compare_json({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 3]})
    assert not ok
    assert "-    2" in diff and "+    3" in diff
    assert h.compare_json({"x": 1}, {"x": 1}) == (True, "")


def test_compare_golden_missing_update_and_match(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "GOLDEN_DIR", tmp_path)
    ok, detail = h.compare_golden("g", {"k": 1}, update=False)
    assert not ok and "golden missing" in detail
    assert h.compare_golden("g", {"k": 1}, update=True)[0]
    assert json.loads((tmp_path / "g.json").read_text(encoding="utf-8")) == {"k": 1}
    assert h.compare_golden("g", {"k": 1}, update=False) == (True, "")
    assert not h.compare_golden("g", {"k": 2}, update=False)[0]


def test_update_goldens_refused_without_env(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["blender", "--", "out", "--update-goldens"])
    monkeypatch.delenv(h.GOLDEN_UPDATE_ENV, raising=False)
    with pytest.raises(SystemExit, match="refused"):
        h.update_goldens_requested()
    monkeypatch.setenv(h.GOLDEN_UPDATE_ENV, "1")
    assert h.update_goldens_requested() is True


def test_results_write_file_and_exit_code(tmp_path):
    r = h.Results("test_cac_demo.py", directory=tmp_path)
    r.check("a", True)
    r.check("b", False, "why")
    with pytest.raises(SystemExit) as exc:
        r.finish()
    assert exc.value.code == 1
    data = json.loads((tmp_path / "test_cac_demo.result.json").read_text(encoding="utf-8"))
    assert data["checks"] == {
        "test_cac_demo::a": {"ok": True, "detail": ""},
        "test_cac_demo::b": {"ok": False, "detail": "why"},
    }
    with pytest.raises(ValueError):
        r.check("a", True)
