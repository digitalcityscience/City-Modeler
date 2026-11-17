"""T11b: shared GML geometry parser (shared/gml_geometry.py) - coordinate
format x GML version matrix, geometry types, xlink pass-through, broken
coordinates (warning + skip) and polygon counts on sample files against the
importer's face counts measured headless. No Blender."""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import gml_geometry as gg  # noqa: E402

GML311 = "http://www.opengis.net/gml"
GML32 = "http://www.opengis.net/gml/3.2"
XLINK = "http://www.w3.org/1999/xlink"
SQUARE = ((0.0, 0.0, 5.0), (1.0, 0.0, 5.0), (1.0, 1.0, 5.0), (0.0, 1.0, 5.0), (0.0, 0.0, 5.0))
SQUARE_2D = tuple((x, y, 0.0) for x, y, _ in SQUARE)


def doc(body, ns):
    return ET.fromstring(f'<root xmlns:gml="{ns}" xmlns:xlink="{XLINK}" '
                         f'xmlns:bldg="urn:test">{body}</root>')[0]


def ring(fmt, rid="R1", coords=SQUARE):
    if fmt == "posList":
        text = " ".join(f"{x} {y} {z}" for x, y, z in coords)
        inner = f'<gml:posList srsDimension="3">{text}</gml:posList>'
    elif fmt == "posList-2d":
        text = " ".join(f"{x} {y}" for x, y, _ in coords)
        inner = f'<gml:posList srsDimension="2">{text}</gml:posList>'
    elif fmt == "posList-nodim":
        inner = "<gml:posList>" + " ".join(f"{x} {y} {z}" for x, y, z in coords) + "</gml:posList>"
    elif fmt == "pos":
        inner = "".join(f"<gml:pos>{x} {y} {z}</gml:pos>" for x, y, z in coords)
    elif fmt == "coordinates":
        inner = "<gml:coordinates>" + " ".join(f"{x},{y},{z}" for x, y, z in coords) + "</gml:coordinates>"
    elif fmt == "coordinates-custom":
        text = ";".join(f"{x}|{y}|{z}".replace(".", ",") for x, y, z in coords)
        inner = f'<gml:coordinates cs="|" ts=";" decimal=",">{text}</gml:coordinates>'
    elif fmt == "pointProperty":
        inner = "".join(f"<gml:pointProperty><gml:Point><gml:pos>{x} {y} {z}</gml:pos></gml:Point>"
                        f"</gml:pointProperty>" for x, y, z in coords)
    else:
        raise AssertionError(fmt)
    return f'<gml:LinearRing gml:id="{rid}">{inner}</gml:LinearRing>'


def polygon(fmt, pid="P1", holes=0):
    interiors = "".join(f"<gml:interior>{ring(fmt, f'{pid}_h{i}')}</gml:interior>" for i in range(holes))
    return (f'<gml:Polygon gml:id="{pid}"><gml:exterior>{ring(fmt, f"{pid}_r")}</gml:exterior>'
            f"{interiors}</gml:Polygon>")


FORMATS = ["posList", "posList-nodim", "pos", "coordinates", "coordinates-custom", "pointProperty"]


@pytest.mark.parametrize("ns", [GML311, GML32])
@pytest.mark.parametrize("fmt", FORMATS)
def test_coordinate_format_matrix(fmt, ns):
    g = gg.parse_geometry(doc(f'<bldg:lod2MultiSurface><gml:MultiSurface gml:id="MS">'
                              f"<gml:surfaceMember>{polygon(fmt, holes=1)}</gml:surfaceMember>"
                              f"</gml:MultiSurface></bldg:lod2MultiSurface>", ns))
    assert g.gml_type == "MultiSurface" and g.gml_id == "MS"
    (p,) = g.polygons
    assert p.polygon_id == "P1" and p.multisurface_id == "MS" and p.is_multisurface_member
    assert p.exterior.ring_id == "P1_r" and p.exterior.role == "exterior"
    assert p.exterior.coords == SQUARE and p.exterior.polygon_id == "P1"
    assert [r.ring_id for r in p.interiors] == ["P1_h0"] and p.interiors[0].role == "interior"


@pytest.mark.parametrize("ns", [GML311, GML32])
def test_same_node_for_every_format_and_version(ns):
    nodes = {fmt: gg.parse_geometry(doc(polygon(fmt), ns)) for fmt in FORMATS}
    reference = gg.parse_geometry(doc(polygon("posList"), GML32))
    assert all(n == reference for n in nodes.values())


def test_two_dimensional_poslist_and_unclosed_ring_closed():
    g = gg.parse_geometry(doc(polygon("posList-2d"), GML32))
    assert g.polygons[0].exterior.coords == SQUARE_2D
    g = gg.parse_geometry(doc(f'<gml:Polygon><gml:exterior>{ring("pos", coords=SQUARE[:4])}'
                              f"</gml:exterior></gml:Polygon>", GML311))
    assert g.polygons[0].exterior.coords == SQUARE and g.polygons[0].polygon_id == ""


def test_srs_dimension_inherited_from_ancestor():
    text = " ".join(f"{x} {y}" for x, y, _ in SQUARE)
    g = gg.parse_geometry(doc(f'<gml:Polygon srsDimension="2"><gml:exterior><gml:LinearRing>'
                              f"<gml:posList>{text}</gml:posList></gml:LinearRing></gml:exterior>"
                              f"</gml:Polygon>", GML32))
    assert g.polygons[0].exterior.coords == SQUARE_2D


@pytest.mark.parametrize("ns", [GML311, GML32])
def test_solid_composite_surface_ids(ns):
    g = gg.parse_geometry(doc(
        '<bldg:lod2Solid><gml:Solid gml:id="S"><gml:exterior><gml:CompositeSurface gml:id="CS">'
        f'<gml:surfaceMember>{polygon("posList", "A")}</gml:surfaceMember>'
        f'<gml:surfaceMember>{polygon("posList", "B")}</gml:surfaceMember>'
        '<gml:surfaceMember xlink:href="#C"/>'
        "</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod2Solid>", ns))
    assert g.gml_type == "Solid" and g.gml_id == "S"
    assert [p.polygon_id for p in g.polygons] == ["A", "B"]
    assert {p.compositesurface_id for p in g.polygons} == {"CS"}
    assert not any(p.is_multisurface_member for p in g.polygons)
    assert g.extra == {"member_hrefs": ["#C"]}


def test_composite_solid_orientable_and_triangulated_surface():
    tri = (f'<gml:Triangle gml:id="T"><gml:exterior>{ring("posList", "TR", SQUARE[:3] + SQUARE[:1])}'
           f"</gml:exterior></gml:Triangle>")
    g = gg.parse_geometry(doc(
        '<gml:CompositeSolid><gml:solidMember><gml:Solid><gml:exterior><gml:Shell>'
        f'<gml:surfaceMember><gml:OrientableSurface orientation="-"><gml:baseSurface>{polygon("pos", "O")}'
        "</gml:baseSurface></gml:OrientableSurface></gml:surfaceMember>"
        f'<gml:surfaceMember><gml:TriangulatedSurface><gml:trianglePatches>{tri}</gml:trianglePatches>'
        "</gml:TriangulatedSurface></gml:surfaceMember>"
        "</gml:Shell></gml:exterior></gml:Solid></gml:solidMember></gml:CompositeSolid>", GML32))
    assert [p.polygon_id for p in g.polygons] == ["O", "T"]
    assert g.polygons[1].exterior.ring_id == "TR"


def test_surface_with_polygon_patches():
    patch = polygon("posList").replace("gml:Polygon", "gml:PolygonPatch")
    g = gg.parse_geometry(doc(f'<gml:Surface gml:id="SF"><gml:patches>{patch}</gml:patches></gml:Surface>',
                              GML311))
    assert g.gml_type == "Surface" and g.polygons[0].polygon_id == "P1"


@pytest.mark.parametrize("ns", [GML311, GML32])
def test_curves_and_points(ns):
    coords = "0 0 1 2 0 1 2 3 1"
    g = gg.parse_geometry(doc(
        "<gml:MultiCurve><gml:curveMember>"
        f'<gml:LineString><gml:posList srsDimension="3">{coords}</gml:posList></gml:LineString>'
        "</gml:curveMember><gml:curveMember><gml:LineString>"
        "<gml:coordinates>0,0,1 2,0,1</gml:coordinates></gml:LineString></gml:curveMember>"
        "<gml:curveMember><gml:Curve><gml:segments>"
        "<gml:LineStringSegment><gml:posList>0 0 1 2 0 1</gml:posList></gml:LineStringSegment>"
        "<gml:LineStringSegment><gml:posList>2 0 1 2 3 1</gml:posList></gml:LineStringSegment>"
        "</gml:segments></gml:Curve></gml:curveMember>"
        '<gml:curveMember xlink:href="#L9"/></gml:MultiCurve>', ns))
    line = ((0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 3.0, 1.0))
    assert g.curves == (line, line[:2], line) and g.extra["member_hrefs"] == ["#L9"]
    p = gg.parse_geometry(doc("<gml:Point><gml:pos>1 2 3</gml:pos></gml:Point>", ns))
    assert p.gml_type == "Point" and p.points == ((1.0, 2.0, 3.0),)
    p = gg.parse_geometry(doc("<gml:Point><gml:coordinates>1,2</gml:coordinates></gml:Point>", ns))
    assert p.points == ((1.0, 2.0, 0.0),)


def test_xlink_property_is_carried_not_resolved():
    g = gg.parse_geometry(doc('<bldg:lod2MultiSurface xlink:href="#MS-elsewhere"/>', GML32))
    assert g.href == "#MS-elsewhere" and g.polygons == () and g.gml_type == ""


def test_round_trips_through_ir_dict():
    g = gg.parse_geometry(doc(polygon("pos", holes=2), GML311))
    assert type(g).from_dict(g.to_dict()) == g


# -- broken input: warning + skip, never an exception ----------------------

BROKEN = {
    "not-multiple": '<gml:posList srsDimension="3">0 0 0 1 0 0 1 1 0 0 1</gml:posList>',
    "count-mismatch": '<gml:posList srsDimension="3" count="7">0 0 0 1 0 0 1 1 0 0 0 0</gml:posList>',
    "non-numeric": "<gml:posList>0 0 0 1 x 0 1 1 0 0 0 0</gml:posList>",
    "too-few": '<gml:posList srsDimension="3">0 0 0 1 0 0</gml:posList>',
    "short-pos": "<gml:pos>0 0 0</gml:pos><gml:pos>1 0</gml:pos><gml:pos>1 1 0</gml:pos><gml:pos>0 0 0</gml:pos>",
    "empty": "<gml:posList/>",
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_broken_exterior_skips_polygon_with_warning(case, caplog):
    bad = f'<gml:Polygon gml:id="BAD"><gml:exterior><gml:LinearRing>{BROKEN[case]}</gml:LinearRing></gml:exterior></gml:Polygon>'
    warnings = []
    g = gg.parse_geometry(doc(f"<gml:MultiSurface><gml:surfaceMember>{bad}</gml:surfaceMember>"
                              f'<gml:surfaceMember>{polygon("posList", "OK")}</gml:surfaceMember>'
                              "</gml:MultiSurface>", GML32), warnings)
    assert [p.polygon_id for p in g.polygons] == ["OK"]
    assert warnings and all("BAD" in w for w in warnings)
    assert "gml_geometry" in caplog.text


def test_broken_interior_drops_only_that_ring():
    warnings = []
    good = polygon("posList", holes=1)
    bad_hole = f'<gml:interior><gml:LinearRing gml:id="H">{BROKEN["not-multiple"]}</gml:LinearRing></gml:interior>'
    g = gg.parse_geometry(doc(good.replace("</gml:Polygon>", bad_hole + "</gml:Polygon>"), GML311), warnings)
    (p,) = g.polygons
    assert [r.ring_id for r in p.interiors] == ["P1_h0"] and len(warnings) == 1 and "H" in warnings[0]


def test_property_without_geometry_warns():
    warnings = []
    g = gg.parse_geometry(doc("<bldg:lod2MultiSurface/>", GML32), warnings)
    assert g == gg.GeometryNode() and warnings


# -- counts on real files vs. today's importer ----------------------------

SAMPLES = Path(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"))
FIXTURES = ROOT / "test_files"
# Blender faces after `cgml3.import_gml_file(load_mode='ALL')`, measured once
# headless with scripts/headless/t11a_import_snapshot.py (2026-10-24). The
# importer adds one face per interior ring, so faces = polygons + interiors.
IMPORTER_FACES = {
    SAMPLES / "buildings_lod2_citygml_2_0.gml": 39,
    SAMPLES / "buildings_lod2_citygml_3_0.gml": 39,
    SAMPLES / "buildings_lod3_citygml_2_0.gml": 225,
    SAMPLES / "buildings_lod3_citygml_3_0.gml": 225,
    FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML2.gml": 6,
    FIXTURES / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML3.gml": 6,
    FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml": 2,
    FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml": 2,
    FIXTURES / "Bridge" / "Bridge_citygml3.gml": 30,
    FIXTURES / "Building" / "CitySmith" / "CS1_lod2.gml": 57,
}


@pytest.mark.parametrize("path", list(IMPORTER_FACES), ids=lambda p: p.name)
def test_polygon_count_matches_importer(path):
    if not path.exists():
        pytest.skip(f"{path} not available")
    warnings = []
    geoms = [g for _, g in gg.iter_geometries(ET.parse(path).getroot(), warnings)]
    faces = sum(1 + len(p.interiors) for g in geoms for p in g.polygons)
    assert faces == IMPORTER_FACES[path] and warnings == []
