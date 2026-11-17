"""T03: synthetic tiled CityGML generator (scripts/perf/make_tiled_citygml.py)."""
import importlib.util
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GML3 = "http://www.opengis.net/gml/3.2"
GML2 = "http://www.opengis.net/gml"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
CORE3 = "http://www.opengis.net/citygml/3.0"
BLDG2 = "http://www.opengis.net/citygml/building/2.0"


def _load_generator():
    path = ROOT / "scripts" / "perf" / "make_tiled_citygml.py"
    spec = importlib.util.spec_from_file_location("make_tiled_citygml", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up
    spec.loader.exec_module(module)
    return module


gen = _load_generator()

SRC_V3 = f"""<?xml version='1.0' encoding='UTF-8'?>
<CityModel xmlns="{CORE3}" xmlns:gml="{GML3}" xmlns:xlink="http://www.w3.org/1999/xlink"
    xmlns:bldg="http://www.opengis.net/citygml/building/3.0"
    xmlns:con="http://www.opengis.net/citygml/construction/3.0"
    xmlns:app="http://www.opengis.net/citygml/appearance/3.0">
  <gml:name>tiny &amp; {{braces}}</gml:name>
  <gml:boundedBy><gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">
    <gml:lowerCorner>1000.00 2000.00 10.0</gml:lowerCorner>
    <gml:upperCorner>1010.00 2005.00 20.0</gml:upperCorner></gml:Envelope></gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="B1">
      <gml:boundedBy><gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">
        <gml:lowerCorner>1000.00 2000.00 10.0</gml:lowerCorner>
        <gml:upperCorner>1010.00 2005.00 20.0</gml:upperCorner></gml:Envelope></gml:boundedBy>
      <appearance><app:Appearance gml:id="A1"><app:surfaceData><app:X3DMaterial gml:id="M1">
        <app:target>#G1</app:target></app:X3DMaterial></app:surfaceData></app:Appearance></appearance>
      <boundary><con:GroundSurface gml:id="GS1"><lod2MultiSurface><gml:MultiSurface gml:id="MS1">
        <gml:surfaceMember><gml:Polygon gml:id="G1"><gml:exterior><gml:LinearRing gml:id="R1">
          <gml:posList srsDimension="3">1000.00 2000.00 10.0 1000.00 2005.00 10.0 1010.00 2005.00 10.0 1010.00 2000.00 10.0 1000.00 2000.00 10.0</gml:posList>
        </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
      </gml:MultiSurface></lod2MultiSurface></con:GroundSurface></boundary>
      <boundary><con:RoofSurface gml:id="RS1"><lod2MultiSurface><gml:MultiSurface>
        <gml:surfaceMember><gml:Polygon gml:id="P2"><gml:exterior><gml:LinearRing>
          <gml:posList>1000.00 2000.00 20.0 1010.00 2000.00 20.0 1010.00 2005.00 20.0 1000.00 2000.00 20.0</gml:posList>
        </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
      </gml:MultiSurface></lod2MultiSurface></con:RoofSurface></boundary>
      <lod2Solid><gml:Solid><gml:exterior><gml:Shell>
        <gml:surfaceMember xlink:href="#G1"/><gml:surfaceMember xlink:href="#P2"/>
        <gml:surfaceMember xlink:href="#elsewhere"/>
      </gml:Shell></gml:exterior></gml:Solid></lod2Solid>
    </bldg:Building>
  </cityObjectMember>
  <cityObjectMember>
    <bldg:Building gml:id="B2">
      <gml:boundedBy><gml:Envelope srsDimension="3">
        <gml:lowerCorner>1020.0 2000.0 0.0</gml:lowerCorner>
        <gml:upperCorner>1030.0 2010.0 5.0</gml:upperCorner></gml:Envelope></gml:boundedBy>
      <lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="P3"><gml:exterior><gml:LinearRing>
        <gml:posList srsDimension="3">1020.0 2000.0 0.0 1030.0 2000.0 5.0 1030.0 2010.0 5.0 1020.0 2000.0 0.0</gml:posList>
      </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod2MultiSurface>
    </bldg:Building>
  </cityObjectMember>
</CityModel>
"""

SRC_V2 = f"""<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:gml="{GML2}"
    xmlns:bldg="{BLDG2}" xmlns:xlink="http://www.w3.org/1999/xlink"
    xmlns:gen="http://www.opengis.net/citygml/generics/2.0">
  <core:cityObjectMember>
    <bldg:Building gml:id="B1">
      <gml:boundedBy><gml:Envelope srsName="EPSG:25832" srsDimension="3">
        <gml:lowerCorner>0 0 0</gml:lowerCorner><gml:upperCorner>4 4 3</gml:upperCorner></gml:Envelope></gml:boundedBy>
      <gen:stringAttribute name="a"><gen:value>x</gen:value></gen:stringAttribute>
      <bldg:measuredHeight uom="m">3</bldg:measuredHeight>
      <bldg:lod2Solid><gml:Solid><gml:exterior><gml:CompositeSurface>
        <gml:surfaceMember xlink:href="#G1"/></gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod2Solid>
      <bldg:boundedBy><bldg:GroundSurface gml:id="GS1"><bldg:lod2MultiSurface><gml:MultiSurface>
        <gml:surfaceMember><gml:Polygon gml:id="G1"><gml:exterior><gml:LinearRing>
          <gml:coordinates>0,0,0 0,4,0 4,4,0 4,0,0 0,0,0</gml:coordinates>
        </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>
      </gml:MultiSurface></bldg:lod2MultiSurface></bldg:GroundSurface></bldg:boundedBy>
    </bldg:Building>
  </core:cityObjectMember>
</core:CityModel>
"""


@pytest.fixture
def src_v3(tmp_path):
    p = tmp_path / "src3.gml"
    p.write_text(SRC_V3, encoding="utf-8")
    return p


@pytest.fixture
def src_v2(tmp_path):
    p = tmp_path / "src2.gml"
    p.write_text(SRC_V2, encoding="utf-8")
    return p


def _generate(src, tmp_path, count, **kw):
    out = tmp_path / "out.gml"
    stats = gen.generate(str(src), str(out), count, **kw)
    return ET.parse(out).getroot(), stats


def _ids(root, gml_ns):
    return [e.get(f"{{{gml_ns}}}id") for e in root.iter() if e.get(f"{{{gml_ns}}}id")]


def _buildings(root):
    return [e for e in root.iter() if e.tag.endswith("}Building")]


def _env(elem, gml_ns):
    env = elem.find(f"{{{gml_ns}}}boundedBy/{{{gml_ns}}}Envelope")
    lo = [float(v) for v in env.find(f"{{{gml_ns}}}lowerCorner").text.split()]
    hi = [float(v) for v in env.find(f"{{{gml_ns}}}upperCorner").text.split()]
    return lo, hi


def test_feature_count_and_partial_last_copy(src_v3, tmp_path):
    root, stats = _generate(src_v3, tmp_path, 5)
    assert len(_buildings(root)) == 5
    assert stats["features"] == 5
    assert stats["copies"] == 3


def test_ids_unique_and_internal_hrefs_resolve(src_v3, tmp_path):
    root, _ = _generate(src_v3, tmp_path, 8)
    ids = _ids(root, GML3)
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes
    assert len(ids) == 4 * len(set(_ids(ET.parse(src_v3).getroot(), GML3)))
    hrefs = [e.get(XLINK_HREF) for e in root.iter() if e.get(XLINK_HREF)]
    internal = [h for h in hrefs if h != "#elsewhere"]
    assert len(internal) == 4 * 2
    assert all(h[1:] in set(ids) for h in internal)
    # unknown targets are left alone
    assert hrefs.count("#elsewhere") == 4
    targets = [e.text for e in root.iter("{http://www.opengis.net/citygml/appearance/3.0}target")]
    assert sorted(targets) == sorted(f"#G1_t{k}" for k in range(4))


def test_each_copy_refers_to_its_own_ids(src_v3, tmp_path):
    root, _ = _generate(src_v3, tmp_path, 6)
    for b in _buildings(root):
        suffix = b.get(f"{{{GML3}}}id").rsplit("_", 1)[1]
        for e in b.iter():
            href = e.get(XLINK_HREF)
            if href and href != "#elsewhere":
                assert href.endswith("_" + suffix)


def test_coordinates_and_envelopes_shifted(src_v3, tmp_path):
    root, stats = _generate(src_v3, tmp_path, 8, dx=100.0, dy=50.0)
    assert stats["grid"] == [2, 2]
    for b in _buildings(root):
        k = int(b.get(f"{{{GML3}}}id").rsplit("_t", 1)[1])
        sx, sy = (k % 2) * 100.0, (k // 2) * 50.0
        lo, hi = _env(b, GML3)
        base_lo = [1000.0, 2000.0, 10.0] if b.get(f"{{{GML3}}}id").startswith("B1") else [1020.0, 2000.0, 0.0]
        assert lo == pytest.approx([base_lo[0] + sx, base_lo[1] + sy, base_lo[2]])
        # geometry moved by the same offset, Z untouched
        pos = [float(v) for v in next(b.iter(f"{{{GML3}}}posList")).text.split()]
        assert min(pos[0::3]) == pytest.approx(lo[0])
        assert min(pos[1::3]) == pytest.approx(lo[1])
        assert min(pos[2::3]) == pytest.approx(lo[2])
        assert all(lo[i] <= hi[i] for i in range(3))
    # decimals of the source are preserved
    text = next(_buildings(root)[0].iter(f"{{{GML3}}}posList")).text.split()
    assert text[0] == "1000.00" and text[2] == "10.0"


def test_citymodel_envelope_covers_all_copies(src_v3, tmp_path):
    root, _ = _generate(src_v3, tmp_path, 8, dx=100.0, dy=50.0)
    lo, hi = _env(root, GML3)
    assert lo == pytest.approx([1000.0, 2000.0, 0.0])
    assert hi == pytest.approx([1130.0, 2060.0, 20.0])
    env = root.find(f"{{{GML3}}}boundedBy/{{{GML3}}}Envelope")
    assert env.get("srsName") == "urn:ogc:def:crs:EPSG::25832"


def test_header_text_escaped_and_kept_once(src_v3, tmp_path):
    root, _ = _generate(src_v3, tmp_path, 4)
    names = root.findall(f"{{{GML3}}}name")
    assert [n.text for n in names] == ["tiny & {braces}"]


def test_strip_envelopes(src_v3, tmp_path):
    root, stats = _generate(src_v3, tmp_path, 4, strip_envelopes=True)
    assert not list(root.iter(f"{{{GML3}}}Envelope"))
    assert not list(root.iter(f"{{{GML3}}}boundedBy"))
    assert len(_buildings(root)) == 4
    assert stats["envelope"] is None


def test_with_lod0_v3_footprint_from_ground_surface(src_v3, tmp_path):
    root, stats = _generate(src_v3, tmp_path, 4, with_lod0=True)
    assert stats["footprints"] == 2  # B2 has no GroundSurface: no footprint, no bbox stand-in
    for b in _buildings(root):
        lod0 = b.find(f"{{{CORE3}}}lod0MultiSurface")
        if b.get(f"{{{GML3}}}id").startswith("B2"):
            assert lod0 is None
            continue
        assert lod0 is not None
        names = [c.tag.split("}")[1] for c in b]
        # XSD order: after boundary, before lod2Solid
        assert names.index("lod0MultiSurface") == names.index("lod2Solid") - 1
        assert max(i for i, n in enumerate(names) if n == "boundary") < names.index("lod0MultiSurface")
        polys = list(lod0.iter(f"{{{GML3}}}Polygon"))
        assert len(polys) == 1
        assert polys[0].get(f"{{{GML3}}}id").startswith("G1_lod0_t")
        pos = [float(v) for v in next(polys[0].iter(f"{{{GML3}}}posList")).text.split()]
        assert gen._signed_area_xy(pos, 3) > 0  # faces up
        lo, _ = _env(b, GML3)
        assert min(pos[0::3]) == pytest.approx(lo[0])
    ids = _ids(root, GML3)
    assert len(ids) == len(set(ids))
    assert "LoD0" not in (tmp_path / "out.gml").read_text(encoding="utf-8").replace("lod0", "")


def test_without_lod0_flag_no_footprint(src_v3, tmp_path):
    root, stats = _generate(src_v3, tmp_path, 2)
    assert stats["footprints"] == 0
    assert not list(root.iter(f"{{{CORE3}}}lod0MultiSurface"))


def test_citygml2_coordinates_ids_and_lod0(src_v2, tmp_path):
    root, stats = _generate(src_v2, tmp_path, 4, dx=10.0, dy=10.0, with_lod0=True)
    assert stats["citygml_version"] == "2.0"
    assert root.tag == "{http://www.opengis.net/citygml/2.0}CityModel"
    bs = _buildings(root)
    assert len(bs) == 4
    ids = _ids(root, GML2)
    assert len(ids) == len(set(ids))
    hrefs = [e.get(XLINK_HREF) for e in root.iter() if e.get(XLINK_HREF)]
    assert all(h[1:] in ids for h in hrefs)
    b3 = next(b for b in bs if b.get(f"{{{GML2}}}id") == "B1_t3")
    coords = next(b3.iter(f"{{{GML2}}}coordinates")).text.split()
    assert coords[0] == "10,10,0"
    names = [c.tag.split("}")[1] for c in b3]
    assert names.index("lod0FootPrint") == names.index("measuredHeight") + 1
    assert b3.find(f"{{{BLDG2}}}lod0FootPrint/{{{GML2}}}MultiSurface") is not None
    lo, hi = _env(root, GML2)
    assert lo == pytest.approx([0, 0, 0]) and hi == pytest.approx([14, 14, 3])


def test_real_lod0_is_not_duplicated(tmp_path):
    src = tmp_path / "with_lod0.gml"
    src.write_text(SRC_V3.replace(
        "<boundary><con:GroundSurface",
        "<lod0MultiSurface><gml:MultiSurface/></lod0MultiSurface><boundary><con:GroundSurface", 1),
        encoding="utf-8")
    root, stats = _generate(src, tmp_path, 2, with_lod0=True)
    assert len(list(root.iter(f"{{{CORE3}}}lod0MultiSurface"))) == 1


def test_default_step_leaves_a_gap(src_v3):
    source = gen.load_source(str(src_v3))
    plan = gen.make_plan(source, 100, gap=20.0)
    b = source.bounds
    assert plan.dx >= b.hi[0] - b.lo[0] + 20.0
    assert plan.dy >= b.hi[1] - b.lo[1] + 20.0
    assert plan.copies == 50 and plan.cols == math.ceil(math.sqrt(50))


SRC_V1 = SRC_V2.replace("citygml/2.0", "citygml/1.0").replace("building/2.0", "building/1.0") \
    .replace("generics/2.0", "generics/1.0")


@pytest.fixture
def src_v1(tmp_path):
    p = tmp_path / "src1.gml"
    p.write_text(SRC_V1, encoding="utf-8")
    return p


def test_citygml1_source_is_tiled_and_well_formed(src_v1, tmp_path):
    # Hamburg's official LoD2-DE data is CityGML 1.0.
    root, stats = _generate(src_v1, tmp_path, 3, dx=10.0, dy=10.0)
    assert stats["citygml_version"] == "1.0"
    assert root.tag == "{http://www.opengis.net/citygml/1.0}CityModel"
    ids = _ids(root, GML2)
    assert len(_buildings(root)) == 3 and len(ids) == len(set(ids))


def test_citygml1_with_lod0_is_refused(src_v1, tmp_path):
    with pytest.raises(ValueError, match="CityGML 1.0 has no LoD0"):
        gen.generate(str(src_v1), str(tmp_path / "x.gml"), 3, with_lod0=True)
    with pytest.raises(SystemExit):
        gen.main([str(src_v1), str(tmp_path / "x.gml"), "--count", "3", "--with-lod0"])


def test_invented_prefix_is_declared_on_root(src_v3):
    source = gen.load_source(str(src_v3))
    compiler = gen._Compiler(source.prefixes, set(), source.gml_ns, "3.0", False, False, 3)
    qname = compiler._qname("{urn:example:not-declared}thing")
    prefix = qname.split(":")[0]
    assert prefix not in dict(source.namespaces)
    assert gen._root_namespaces(source)[prefix] == "urn:example:not-declared"


def test_citygml_version_detection():
    assert gen.citygml_version("{http://www.opengis.net/citygml/1.0}CityModel", GML2) == "1.0"
    assert gen.citygml_version("{http://www.opengis.net/citygml/2.0}CityModel", GML2) == "2.0"
    assert gen.citygml_version("{http://www.opengis.net/citygml/3.0}CityModel", GML3) == "3.0"
    assert gen.citygml_version("CityModel", GML3) == "3.0"


def test_cli_writes_stats(src_v3, tmp_path):
    out, stats = tmp_path / "cli.gml", tmp_path / "stats.json"
    gen.main([str(src_v3), str(out), "--count", "3", "--stats-json", str(stats)])
    import json
    data = json.loads(stats.read_text())
    assert data["features"] == 3 and data["peak_rss_mb"] >= 0
