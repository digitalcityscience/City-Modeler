"""T08: fragments cut by byte range and the CityGML 1.0 -> 2.0 rewrite."""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t08_fixtures as fx  # noqa: E402

from shared.browse.fragment import (  # noqa: E402
    NAMESPACES_1_TO_2,
    build_fragment,
    envelope_bounds,
    translate_1_to_2,
    untranslated_1_0_namespaces,
    write_translated_copy,
)
from shared.browse.gml_index import index_file  # noqa: E402
from shared.import_origin import center_origin  # noqa: E402

GML3_ID = "{http://www.opengis.net/gml/3.2}id"
GML_ID = "{http://www.opengis.net/gml}id"


def ids(root, attr):
    return {el.get(attr) for el in root.iter() if el.get(attr)}


def test_fragment_is_well_formed_and_holds_only_the_selection(tmp_path):
    idx = index_file(fx.write(tmp_path, "a.gml", fx.citygml30()))
    frag = build_fragment(idx, [1, 4])  # B, E
    root = ET.fromstring(frag.data)
    assert root.tag == "{http://www.opengis.net/citygml/3.0}CityModel"
    assert ids(root, GML3_ID) == {"B", "B_p1", "E", "E_p1"}
    assert frag.member_count == 2 and frag.appearance_count == 0
    assert frag.bytes_read == idx.root_open_end + sum(idx.entries[i].end - idx.entries[i].start for i in (1, 4))
    assert frag.data.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")


def test_fragment_takes_global_appearance_of_targeted_features(tmp_path):
    idx = index_file(fx.write(tmp_path, "a.gml", fx.citygml30()))
    frag = build_fragment(idx, [0])  # A is textured by APP1
    root = ET.fromstring(frag.data)
    assert "APP1" in ids(root, GML3_ID)
    assert frag.appearance_count == 1
    # relative texture path made absolute: the fragment is written to a temp folder
    uri = next(el.text for el in root.iter() if el.tag.endswith("imageURI"))
    assert os.path.isabs(uri) and uri.endswith(os.path.join("tex", "a.jpg"))


def test_fragment_envelope_keeps_session_origin(tmp_path):
    idx = index_file(fx.write(tmp_path, "a.gml", fx.citygml30()))
    origin = (1300.0, 2000.0, 5.0)
    frag = build_fragment(idx, [1], origin=origin)
    root = ET.fromstring(frag.data)
    env = root.find("{http://www.opengis.net/gml/3.2}boundedBy/{http://www.opengis.net/gml/3.2}Envelope")
    assert env.get("srsName") == "urn:ogc:def:crs:EPSG::25832"
    lower = [float(v) for v in env[0].text.split()]
    upper = [float(v) for v in env[1].text.split()]
    # importer rule: origin = rounded envelope centre, Z = min Z
    assert center_origin(lower, upper) == origin
    # the members still lie inside the envelope in X/Y
    b = idx.entries[1].bbox
    assert lower[0] <= b[0] and upper[0] >= b[3] and lower[1] <= b[1] and upper[1] >= b[4]


def test_envelope_bounds_without_origin_is_union():
    from shared.browse.gml_index import FeatureIndexEntry
    ents = [FeatureIndexEntry("a", "Building", bbox=(0, 0, 1, 2, 2, 3)),
            FeatureIndexEntry("b", "Building", bbox=(5, -1, 0, 6, 1, 9))]
    assert envelope_bounds(ents) == ((0, -1, 0), (6, 2, 9))


def test_cityGML_1_0_fragment_is_rewritten_to_2_0(tmp_path):
    idx = index_file(fx.write(tmp_path, "h.gml", fx.citygml10(4)))
    frag = build_fragment(idx, [0, 3])
    assert b"http://www.opengis.net/citygml/1.0" not in frag.data
    root = ET.fromstring(frag.data)
    assert root.tag == "{http://www.opengis.net/citygml/2.0}CityModel"
    assert ids(root, GML_ID) == {"HH0", "HH0_p", "HH3", "HH3_p"}
    blds = [el for el in root.iter() if el.tag == "{http://www.opengis.net/citygml/building/2.0}Building"]
    assert len(blds) == 2
    # the vegetation 1.0 module has no verified mapping: reported, not dropped silently
    assert frag.untranslated_namespaces == ["http://www.opengis.net/citygml/vegetation/1.0"]
    env = root.find("{http://www.opengis.net/gml}boundedBy/{http://www.opengis.net/gml}Envelope")
    assert env.get("srsName").startswith("urn:adv:crs:ETRS89_UTM32")


def test_translation_table_and_detection():
    src = b" ".join(old for old, _ in NAMESPACES_1_TO_2)
    out = translate_1_to_2(src)
    assert out == b" ".join(new for _, new in NAMESPACES_1_TO_2)
    assert untranslated_1_0_namespaces(out) == []
    assert untranslated_1_0_namespaces(b'"http://www.opengis.net/citygml/landuse/1.0"') == [
        "http://www.opengis.net/citygml/landuse/1.0"]


def test_translated_copy_streams_across_chunks(tmp_path):
    src = fx.write(tmp_path, "h.gml", fx.citygml10(5))
    expected = translate_1_to_2(Path(src).read_bytes())
    for chunk in (7, 33, 1 << 20):
        dst = str(tmp_path / f"out_{chunk}.gml")
        missing = write_translated_copy(src, dst, chunk_bytes=chunk)
        assert Path(dst).read_bytes() == expected, chunk
        assert missing == ["http://www.opengis.net/citygml/vegetation/1.0"]


def test_translated_copy_without_line_breaks(tmp_path):
    src = fx.write(tmp_path, "h.gml", fx.citygml10(5).replace("\n", " "))
    dst = str(tmp_path / "out.gml")
    write_translated_copy(src, dst, chunk_bytes=11)
    assert Path(dst).read_bytes() == translate_1_to_2(Path(src).read_bytes())
