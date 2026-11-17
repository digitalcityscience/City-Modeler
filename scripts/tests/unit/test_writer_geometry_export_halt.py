"""Export-error ticket 04: the shared serializer (``write_polygon_with_ring_ids``
/ ``write_compositesurface_with_polygons`` in ``shared/writer_geometry.py``)
is the last, unavoidable choke point before ring coordinates become XML for
both the v2 and v3 exporters (``io/writer/geometry.py`` and
``io_v2/writer/geometry.py`` are both thin re-export wrappers around it).
These tests drive the two shared functions directly - independent of any
full export run - with hand-built degenerate rings and assert a loud halt,
plus confirm valid rings still emit byte-identical output.

Ticket 04's own checklist still says "either pattern from ticket 01" halts,
but ticket 02 already resolved that only Pattern A (too-few-points) is
degenerate - Pattern B (duplicated start vertex) collapses to a valid ring
and must NOT halt. This file follows ticket 02's resolved contract: Pattern
A + a collinear (zero-area) ring exercise the halt, Pattern B is exercised
as an acceptance case (``test_write_polygon_accepts_valid_pattern_b_exterior_ring``)."""
import importlib.util
import sys
import pytest
from pathlib import Path
from xml.etree.ElementTree import Element, tostring


ROOT = Path(__file__).resolve().parents[3]


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


wg = load_module("shared/writer_geometry.py", "writer_geometry_export_halt_under_test")


def qname(prefix: str, local: str) -> str:
    return f"{prefix}:{local}"


def fmt(v: float) -> str:
    return f"{v:.3f}"


VALID_QUAD = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 0.0, 1.0),
    (0.0, 0.0, 1.0),
]
VALID_QUAD_CLOSED = VALID_QUAD + [VALID_QUAD[0]]

# Pattern A from ticket 01: 2 unique vertices, A -> B -> B -> A.
DEGENERATE_TOO_FEW_POINTS = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0),
]

# 3 distinct but collinear points - zero area.
DEGENERATE_ZERO_AREA = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (2.0, 0.0, 0.0),
]

# Pattern B from ticket 01: duplicated start vertex but 4 correct unique
# corners once collapsed - must NOT raise (ticket 01's resolved contract).
VALID_PATTERN_B = [VALID_QUAD[0]] + VALID_QUAD_CLOSED


def test_write_polygon_raises_on_degenerate_exterior_ring():
    parent = Element("surfaceMembers")

    with pytest.raises(ValueError) as excinfo:
        wg.write_polygon_with_ring_ids(
            parent,
            poly_gid="POLY_1",
            exterior_ring=DEGENERATE_TOO_FEW_POINTS,
            interior_rings=None,
            srs_name=None,
            exterior_ring_id="POLY_1_ext",
            interior_ring_ids=None,
            qname=qname,
            fmt=fmt,
        )

    msg = str(excinfo.value)
    assert "POLY_1" in msg
    assert "POLY_1_ext" in msg
    assert "exterior ring" in msg
    # No XML must have been emitted for a halted polygon.
    assert len(parent) == 0


def test_write_polygon_raises_on_degenerate_interior_ring():
    parent = Element("surfaceMembers")

    with pytest.raises(ValueError) as excinfo:
        wg.write_polygon_with_ring_ids(
            parent,
            poly_gid="POLY_2",
            exterior_ring=VALID_QUAD_CLOSED,
            interior_rings=[DEGENERATE_ZERO_AREA],
            srs_name=None,
            exterior_ring_id="POLY_2_ext",
            interior_ring_ids=["POLY_2_int_0"],
            qname=qname,
            fmt=fmt,
        )

    msg = str(excinfo.value)
    assert "POLY_2" in msg
    assert "POLY_2_int_0" in msg
    assert "interior ring #0" in msg
    assert len(parent) == 0


def test_write_polygon_accepts_valid_pattern_b_exterior_ring():
    parent = Element("surfaceMembers")

    wg.write_polygon_with_ring_ids(
        parent,
        poly_gid="POLY_3",
        exterior_ring=VALID_PATTERN_B,
        interior_rings=None,
        srs_name=None,
        exterior_ring_id="POLY_3_ext",
        interior_ring_ids=None,
        qname=qname,
        fmt=fmt,
    )

    assert len(parent) == 1


def test_write_polygon_valid_ring_output_is_unchanged():
    """A valid ring must be emitted exactly as before the ticket 04 guard -
    byte-identical output, no regression."""
    parent = Element("surfaceMembers")

    wg.write_polygon_with_ring_ids(
        parent,
        poly_gid="POLY_4",
        exterior_ring=VALID_QUAD_CLOSED,
        interior_rings=[VALID_QUAD_CLOSED],
        srs_name=None,
        exterior_ring_id="POLY_4_ext",
        interior_ring_ids=["POLY_4_int_0"],
        qname=qname,
        fmt=fmt,
    )

    xml_text = tostring(parent, encoding="unicode")
    expected_poslist = wg.format_poslist(VALID_QUAD_CLOSED, fmt)
    assert xml_text.count(expected_poslist) == 2  # exterior + interior
    assert 'gml:id="POLY_4"' in xml_text
    assert 'gml:id="POLY_4_ext"' in xml_text
    assert 'gml:id="POLY_4_int_0"' in xml_text


def test_write_compositesurface_raises_on_degenerate_polygon_and_writes_nothing():
    parent = Element("surfaceMembers")
    polygons = [
        {
            "poly_gid": "POLY_5",
            "ext_id": "POLY_5_ext",
            "ext_xyz": VALID_QUAD_CLOSED,
            "int_xyz": None,
            "int_ids": None,
        },
        {
            "poly_gid": "POLY_6",
            "ext_id": "POLY_6_ext",
            "ext_xyz": DEGENERATE_TOO_FEW_POINTS,
            "int_xyz": None,
            "int_ids": None,
        },
    ]

    with pytest.raises(ValueError) as excinfo:
        wg.write_compositesurface_with_polygons(
            parent,
            cs_id="CS_1",
            polygons=polygons,
            srs_name=None,
            qname=qname,
            fmt=fmt,
        )

    msg = str(excinfo.value)
    assert "POLY_6" in msg
    assert "exterior ring" in msg
    # Halt happens in a validate-first pass: not even the first (valid)
    # polygon's CompositeSurface wrapper should have been written.
    assert len(parent) == 0


def test_write_compositesurface_raises_on_degenerate_interior_ring():
    parent = Element("surfaceMembers")
    polygons = [
        {
            "poly_gid": "POLY_7",
            "ext_id": "POLY_7_ext",
            "ext_xyz": VALID_QUAD_CLOSED,
            "int_xyz": [DEGENERATE_ZERO_AREA],
            "int_ids": ["POLY_7_int_0"],
        },
    ]

    with pytest.raises(ValueError) as excinfo:
        wg.write_compositesurface_with_polygons(
            parent,
            cs_id="CS_2",
            polygons=polygons,
            srs_name=None,
            qname=qname,
            fmt=fmt,
        )

    msg = str(excinfo.value)
    assert "POLY_7" in msg
    assert "POLY_7_int_0" in msg
    assert "interior ring #0" in msg
    assert len(parent) == 0


def test_write_compositesurface_accepts_valid_polygons_unchanged():
    parent = Element("surfaceMembers")
    polygons = [
        {
            "poly_gid": "POLY_8",
            "ext_id": "POLY_8_ext",
            "ext_xyz": VALID_QUAD_CLOSED,
            "int_xyz": [VALID_QUAD_CLOSED],
            "int_ids": ["POLY_8_int_0"],
        },
        {
            "poly_gid": "POLY_9",
            "ext_id": "POLY_9_ext",
            "ext_xyz": VALID_QUAD_CLOSED,
            "int_xyz": None,
            "int_ids": None,
        },
    ]

    wg.write_compositesurface_with_polygons(
        parent,
        cs_id="CS_3",
        polygons=polygons,
        srs_name=None,
        qname=qname,
        fmt=fmt,
    )

    xml_text = tostring(parent, encoding="unicode")
    assert 'gml:id="CS_3"' in xml_text
    assert 'gml:id="POLY_8"' in xml_text
    assert 'gml:id="POLY_9"' in xml_text
    assert 'gml:id="POLY_8_int_0"' in xml_text
