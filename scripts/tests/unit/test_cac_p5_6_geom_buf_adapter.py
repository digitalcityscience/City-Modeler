"""CAC-P5.6: ExportPolygon/ExportOpening -> writer back-half argument
mapping. Pure Python, no bpy.

Scope: ``shared/writer_geometry_ir_adapter.py`` only - the small, shared
mapping the v2/v3 writer adapters call into
``write_polygon_with_ring_ids``/``_write_inline_surface_opening`` with. No
writer, no XML, no coordinate transform of its own (the transform is
injected, and these tests use an identity-plus-marker transform to prove
it is actually used rather than bypassed).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.ir_to_groups import (  # noqa: E402
    ExportOpening, ExportPolygon, ExportRing,
)
from shared.writer_geometry_ir_adapter import (  # noqa: E402
    geom_buf_opening_entries, transformed_polygon_kwargs,
)

EXT = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
HOLE = ((0.2, 0.2, 0.0), (0.4, 0.2, 0.0), (0.4, 0.4, 0.0), (0.2, 0.4, 0.0))


def _mark_transform(coords):
    """Records what it was called with and tags each point, so a test can
    tell the injected transform actually ran rather than being bypassed."""
    _mark_transform.calls.append(tuple(coords))
    return [(x + 100.0, y, z) for x, y, z in coords]


_mark_transform.calls = []


def setup_function(_fn):
    _mark_transform.calls.clear()


def test_wall_polygon_kwargs_use_the_injected_transform():
    polygon = ExportPolygon(
        polygon_id="P1",
        exterior=ExportRing(ring_id="P1_0_", role="exterior", coords=EXT),
    )
    kwargs = transformed_polygon_kwargs(polygon, _mark_transform)
    assert kwargs["poly_gid"] == "P1"
    assert kwargs["exterior_ring_id"] == "P1_0_"
    assert kwargs["exterior_ring"] == [(x + 100.0, y, z) for x, y, z in EXT]
    assert kwargs["interior_rings"] is None
    assert kwargs["interior_ring_ids"] is None
    assert _mark_transform.calls == [EXT]


def test_wall_polygon_kwargs_carry_interior_rings_as_the_hole_channel():
    polygon = ExportPolygon(
        polygon_id="P1",
        exterior=ExportRing(ring_id="P1_0_", role="exterior", coords=EXT),
        interiors=(ExportRing(ring_id="P1_1_", role="interior", coords=HOLE, opening_link="LINK_A"),),
    )
    kwargs = transformed_polygon_kwargs(polygon, _mark_transform)
    assert kwargs["interior_ring_ids"] == ["P1_1_"]
    assert kwargs["interior_rings"] == [[(x + 100.0, y, z) for x, y, z in HOLE]]
    assert _mark_transform.calls == [EXT, HOLE]


def test_polygon_ids_never_recomputed_from_geometry():
    """The ids in the kwargs are exactly ExportPolygon's own ids - never
    derived from the (transformed) coordinates themselves."""
    polygon = ExportPolygon(
        polygon_id="STABLE_ID",
        exterior=ExportRing(ring_id="STABLE_ID_0_", role="exterior", coords=EXT),
    )
    kwargs = transformed_polygon_kwargs(polygon, lambda coords: [(999.0, 999.0, 999.0)] * len(coords))
    assert kwargs["poly_gid"] == "STABLE_ID"
    assert kwargs["exterior_ring_id"] == "STABLE_ID_0_"


def _window(gml_id="WIN_A", host_id="WALL1", polygons=()):
    return ExportOpening(
        gml_id=gml_id, semantic_type="Window", host_id=host_id, polygons=polygons,
    )


def test_opening_entries_carry_opening_info_from_ir_only():
    polygon = ExportPolygon(
        polygon_id="P_WIN",
        exterior=ExportRing(ring_id="P_WIN_0_", role="exterior", coords=EXT),
    )
    opening = _window(polygons=(polygon,))
    entries = geom_buf_opening_entries(opening, _mark_transform)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["poly_gid"] == "P_WIN"
    assert entry["ext_id"] == "P_WIN_0_"
    assert entry["ext_xyz"] == [(x + 100.0, y, z) for x, y, z in EXT]
    assert entry["opening_info"] == {
        "opening_type": "Window",
        "filling_surface_type": "WindowSurface",
        "opening_id": "WIN_A",
        "opening_name": "",
    }
    # No material, no bpy-shaped value anywhere in the entry.
    assert "mat" not in entry


def test_door_entries_use_door_surface_type():
    polygon = ExportPolygon(
        polygon_id="P_DOOR",
        exterior=ExportRing(ring_id="P_DOOR_0_", role="exterior", coords=EXT),
    )
    opening = ExportOpening(gml_id="DOOR_A", semantic_type="Door", host_id="WALL1", polygons=(polygon,))
    entries = geom_buf_opening_entries(opening, _mark_transform)
    assert entries[0]["opening_info"]["filling_surface_type"] == "DoorSurface"
    assert entries[0]["opening_info"]["opening_type"] == "Door"


def test_two_windows_on_one_wall_keep_distinct_entries():
    p_a = ExportPolygon(polygon_id="P_A", exterior=ExportRing(ring_id="P_A_0_", role="exterior", coords=EXT))
    p_b = ExportPolygon(polygon_id="P_B", exterior=ExportRing(ring_id="P_B_0_", role="exterior", coords=EXT))
    entries_a = geom_buf_opening_entries(_window(gml_id="WIN_A", polygons=(p_a,)), _mark_transform)
    entries_b = geom_buf_opening_entries(_window(gml_id="WIN_B", polygons=(p_b,)), _mark_transform)
    assert entries_a[0]["opening_info"]["opening_id"] == "WIN_A"
    assert entries_b[0]["opening_info"]["opening_id"] == "WIN_B"
    assert entries_a[0]["poly_gid"] != entries_b[0]["poly_gid"]


def test_multisurface_and_compositesurface_ids_carried_through():
    polygon = ExportPolygon(
        polygon_id="P1",
        exterior=ExportRing(ring_id="P1_0_", role="exterior", coords=EXT),
        multisurface_id="MS1",
        compositesurface_id="CS1",
    )
    opening = _window(polygons=(polygon,))
    entries = geom_buf_opening_entries(opening, _mark_transform)
    assert entries[0]["ms_id"] == "MS1"
    assert entries[0]["cs_id"] == "CS1"


def test_empty_opening_geometry_yields_no_entries():
    entries = geom_buf_opening_entries(_window(polygons=()), _mark_transform)
    assert entries == []


def test_unsupported_semantic_type_refuses_rather_than_guessing():
    bad = ExportOpening(gml_id="X", semantic_type="Chimney", host_id="WALL1")
    with pytest.raises(ValueError, match="unsupported opening semantic_type"):
        geom_buf_opening_entries(bad, _mark_transform)
