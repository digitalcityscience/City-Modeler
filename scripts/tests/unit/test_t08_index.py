"""T08: feature index (shared/browse/gml_index.py) - bbox from geometry,
envelope check, LoD0, byte ranges, risk flags, global appearances."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t08_fixtures as fx  # noqa: E402

from shared.browse.gml_index import (  # noqa: E402
    FeatureIndexEntry,
    FileIndex,
    GmlIndexError,
    IndexCancelled,
    check_encoding,
    citygml_version_from_namespaces,
    index_file,
    normalize_srs,
    tag_end,
)


@pytest.fixture
def idx30(tmp_path):
    return index_file(fx.write(tmp_path, "a.gml", fx.citygml30()))


def by_id(idx):
    return {e.gml_id: e for e in idx.entries}


def test_file_level_fields(idx30):
    assert idx30.version == "3.0"
    assert idx30.root_name == "CityModel"
    assert idx30.srs_name == "urn:ogc:def:crs:EPSG::25832"
    assert idx30.envelope == ((1000.0, 2000.0, 5.0), (1510.0, 2010.0, 20.0))
    assert [e.gml_id for e in idx30.entries] == ["A", "B", "C", "D", "E", "F"]
    with open(idx30.path, "rb") as fh:
        data = fh.read()
    # the comment before the root is not mistaken for the root tag
    assert data[idx30.root_start:idx30.root_start + 10] == b"<CityModel"
    assert data[idx30.root_open_end - 1:idx30.root_open_end] == b">"


def test_bbox_is_computed_from_coordinates(idx30):
    e = by_id(idx30)
    assert e["A"].bbox == (1000.0, 2000.0, 10.0, 1010.0, 2010.0, 20.0)
    # LoD0 pos elements (dimension inherited from the Polygon) + LoD2 solid
    assert e["C"].bbox == (1200.0, 2000.0, 5.0, 1210.0, 2010.0, 20.0)
    # 2D gml:coordinates: flat box at Z 0
    assert e["F"].bbox == (1500.0, 2000.0, 0.0, 1510.0, 2010.0, 0.0)


def test_bbox_ignores_the_feature_envelope_and_flags_mismatch(idx30):
    e = by_id(idx30)
    assert e["A"].has_envelope and not e["A"].envelope_mismatch
    assert e["B"].has_envelope and e["B"].envelope_mismatch       # envelope 1 m wider
    assert e["B"].bbox[3] == 1110.0                                # geometry, not the envelope
    assert not e["C"].has_envelope and not e["C"].envelope_mismatch


def test_envelope_tolerance_is_one_centimetre(tmp_path):
    text = fx.citygml30().replace("1111 2010 20", "1110.009 2010 20")
    e = by_id(index_file(fx.write(tmp_path, "t.gml", text)))
    assert not e["B"].envelope_mismatch


def test_missing_envelopes_bbox_from_geometry(tmp_path):
    e = by_id(index_file(fx.write(tmp_path, "h.gml", fx.citygml10(3))))
    assert all(not v.has_envelope for v in e.values())
    assert e["HH1"].bbox == (564020.0, 5934000.0, 13.0, 564030.0, 5934010.0, 25.0)


def test_names_lods_and_lod0(idx30):
    e = by_id(idx30)
    assert e["B"].name == "Bee"
    assert e["A"].lods == ("lod2Solid",) and e["A"].lod_levels == (2,)
    assert e["C"].lod_levels == (0, 2)
    assert e["C"].lod0 == [[1200.0, 2000.0, 1210.0, 2000.0, 1210.0, 2010.0, 1200.0, 2000.0]]
    assert not e["A"].lod0 and not e["A"].footprint and not e["C"].footprint


def test_ground_surface_footprint(tmp_path):
    """B14: the GroundSurface exterior rings (2.0 boundedBy, 3.0 boundary), not walls or interiors."""
    ring = "{x} 0 13 {x2} 0 13 {x2} 10 13 {x} 10 13 {x} 0 13"
    hole = "1 1 13 2 1 13 2 2 13 1 1 13"
    wall = "0 0 13 10 0 13 10 0 25 0 0 13"

    def poly(coords, inner=""):
        return (f'<gml:Polygon><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{coords}</gml:posList>'
                f'</gml:LinearRing></gml:exterior>{inner}</gml:Polygon>')

    def surf(kind, coords, inner=""):
        return (f'<bldg:boundedBy><bldg:{kind}><bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember>'
                f'{poly(coords, inner)}</gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface>'
                f'</bldg:{kind}></bldg:boundedBy>')

    inner = (f'<gml:interior><gml:LinearRing><gml:posList srsDimension="3">{hole}</gml:posList>'
             f'</gml:LinearRing></gml:interior>')
    text = fx.citygml10(1).replace(
        "</bldg:lod2Solid>",
        "</bldg:lod2Solid>" + surf("WallSurface", wall) + surf("GroundSurface", ring.format(x=0, x2=10), inner)
        + surf("GroundSurface", ring.format(x=10, x2=20)), 1)
    e = index_file(fx.write(tmp_path, "g.gml", text)).entries[0]
    assert e.footprint == [[0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0, 0.0, 0.0],
                           [10.0, 0.0, 20.0, 0.0, 20.0, 10.0, 10.0, 10.0, 10.0, 0.0]]
    assert not e.lod0
    assert type(e).from_list(e.to_list()) == e
    # old cache rows (13 fields) still load, without a footprint
    assert type(e).from_list(e.to_list()[:13]).footprint == []


def test_risk_flags(idx30):
    e = by_id(idx30)
    assert e["D"].implicit_geometry and e["D"].risky
    # only the reference point counts, the template's 0 0 0 does not
    assert e["D"].bbox == (1300.0, 2000.0, 7.0, 1300.0, 2000.0, 7.0)
    assert e["E"].external_xlink == 1 and "geometry referenced outside the feature" in e["E"].risks()
    assert e["F"].srs_mismatch
    assert not e["A"].risky and not e["A"].srs_mismatch
    counts = idx30.risk_counts()
    assert counts["implicit geometry"] == 1 and counts["srsName differs from the file CRS"] == 1


def test_global_appearance_targets_features(idx30):
    assert len(idx30.appearances) == 1
    assert idx30.appearances[0].feature_indices == (0,)  # A owns A_p1 (E only references it)


def test_byte_ranges_cut_whole_members(idx30):
    with open(idx30.path, "rb") as fh:
        data = fh.read()
    for e in idx30.entries:
        chunk = data[e.start:e.end]
        assert chunk.startswith(b"<cityObjectMember>") and chunk.endswith(b"</cityObjectMember>")
        wrapped = (b'<r xmlns="http://www.opengis.net/citygml/3.0" xmlns:gml="http://www.opengis.net/gml/3.2" '
                   b'xmlns:bldg="http://www.opengis.net/citygml/building/3.0" '
                   b'xmlns:xlink="http://www.w3.org/1999/xlink">' + chunk + b"</r>")
        root = ET.fromstring(wrapped)
        assert root[0][0].get("{http://www.opengis.net/gml/3.2}id") == e.gml_id
    a = idx30.appearances[0]
    assert data[a.start:a.end].startswith(b"<appearanceMember>")
    assert data[a.start:a.end].endswith(b"</appearanceMember>")


def test_byte_ranges_across_chunk_boundaries(tmp_path):
    path = fx.write(tmp_path, "a.gml", fx.citygml30())
    whole = index_file(path)
    tiny = index_file(path, chunk_bytes=7)
    assert [(e.start, e.end, e.bbox) for e in whole.entries] == [(e.start, e.end, e.bbox) for e in tiny.entries]
    assert (whole.root_start, whole.root_open_end) == (tiny.root_start, tiny.root_open_end)


def test_cityGML_1_0_and_prefixed_root(tmp_path):
    idx = index_file(fx.write(tmp_path, "h.gml", fx.citygml10(2)))
    assert idx.version == "1.0"
    assert idx.root_name == "core:CityModel"
    assert idx.srs_name.startswith("urn:adv:crs:ETRS89_UTM32")
    with open(idx.path, "rb") as fh:
        data = fh.read()
    assert data[idx.entries[0].start:idx.entries[0].end].startswith(b"<core:cityObjectMember>")


def test_progress_batches_and_cancel(tmp_path):
    path = fx.write(tmp_path, "h.gml", fx.citygml10(50))
    seen, progress = [], []
    index_file(path, chunk_bytes=2048, on_batch=lambda ents, part: seen.extend(e.gml_id for e in ents),
               progress=lambda d, t, n: progress.append((d, t, n)))
    assert seen == [f"HH{i}" for i in range(50)]
    assert progress[-1][0] == progress[-1][1]
    with pytest.raises(IndexCancelled):
        index_file(path, chunk_bytes=2048, cancel=lambda: True)


def test_serialisation_round_trip(idx30):
    back = FileIndex.from_dict(idx30.to_dict())
    assert back.to_dict() == idx30.to_dict()
    e = FeatureIndexEntry.from_list(idx30.entries[2].to_list())
    assert e == idx30.entries[2]


def test_encoding_and_errors(tmp_path):
    assert check_encoding(b"<?xml version='1.0' encoding='ISO-8859-1'?>") == "iso-8859-1"
    with pytest.raises(GmlIndexError):
        check_encoding(b"\xff\xfe<\x00?\x00")
    with pytest.raises(GmlIndexError):
        check_encoding(b'<?xml version="1.0" encoding="UTF-16"?>')
    with pytest.raises(GmlIndexError):
        index_file(fx.write(tmp_path, "bad.gml", "<CityModel><cityObjectMember></CityModel>"))


def test_helpers():
    assert normalize_srs("urn:ogc:def:crs:EPSG::25832") == normalize_srs("EPSG:25832") == "EPSG:25832"
    assert normalize_srs("http://www.opengis.net/def/crs/EPSG/0/4326") == "EPSG:4326"
    assert citygml_version_from_namespaces(["http://www.opengis.net/citygml/building/2.0"]) == "2.0"
    assert citygml_version_from_namespaces(["http://www.opengis.net/citygml/1.0"]) == "1.0"
    assert tag_end(b'<a b=">" c="x">rest', 0) == len(b'<a b=">" c="x">')
