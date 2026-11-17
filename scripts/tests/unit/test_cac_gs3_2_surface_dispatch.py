"""GS.3.2: per-surface IR export-group builder. Pure Python, no bpy.
N-Window fold coverage added by P3b.2 (docs/agent/authoring-core/
phase3b-design-note.md §7/§13) - the underlying builders this module
stands on (``ir_adapter.surface_node_from_record``, ``ir_to_groups.
surface_group_from_node``) already fold any number of openings, so this
module needed no code change for N Windows; the added test below records
that as a checked fact rather than an assumption.

Scope: ``shared/authoring_core/domain/gs_surface_dispatch.py::
gs_surface_export_group`` and the two public seams it stands on
(``ir_adapter.surface_node_from_record``, ``ir_to_groups.
surface_group_from_node``). No flag plumbing, no writer wiring - see
``test_cac_gs3_2_flag.py`` and the headless writer-dispatch suite for
those.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import gs_surface_dispatch as gsd  # noqa: E402
from shared.authoring_core.domain import ir_adapter  # noqa: E402
from shared.authoring_core.domain import ir_to_groups  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WALL_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
WIN_RING = ((1.0, 1.0, 0.0), (1.0, 2.0, 0.0), (2.0, 2.0, 0.0), (2.0, 1.0, 0.0))
WIN_EXT = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))
WIN_RING2 = ((2.5, 0.3, 0.0), (2.5, 1.0, 0.0), (3.2, 1.0, 0.0), (3.2, 0.3, 0.0))
WIN_EXT2 = ((2.5, 0.3, 0.0), (3.2, 0.3, 0.0), (3.2, 1.0, 0.0), (2.5, 1.0, 0.0))


def _record_set_with_window():
    wall_ring = RingRecord(ring_id="P_WALL_1_", role="interior", coords=WIN_RING, opening_link="LINK1")
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=WALL_EXT),
        interiors=(wall_ring,), geometry_state="committed",
    )
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface", polygon_ids=("P_WALL",))
    window_polygon = PolygonRecord(
        polygon_id="P_WIN", feature_id="WIN1",
        exterior=RingRecord(ring_id="P_WIN_0_", role="exterior", coords=WIN_EXT),
        geometry_state="committed",
    )
    window = FeatureRecord(
        gml_id="WIN1", kind="Window", semantic_type="Window", parent_id="WALL1", host_id="WALL1",
        opening_link="LINK1", generator="facade.window.rect", polygon_ids=("P_WIN",),
    )
    return RecordSet(features=(wall, window), polygons=(wall_polygon, window_polygon))


def _record_set_windowless():
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=WALL_EXT),
        geometry_state="committed",
    )
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface", polygon_ids=("P_WALL",))
    return RecordSet(features=(wall,), polygons=(wall_polygon,))


def test_export_group_for_wall_with_window():
    group = gsd.gs_surface_export_group("WALL1", _record_set_with_window())
    assert group.gml_id == "WALL1"
    assert group.semantic_type == "WallSurface"
    assert len(group.polygons) == 1
    assert group.polygons[0].polygon_id == "P_WALL"
    assert len(group.polygons[0].interiors) == 1
    assert group.polygons[0].interiors[0].opening_link == "LINK1"
    assert len(group.openings) == 1
    opening = group.openings[0]
    assert opening.gml_id == "WIN1"
    assert opening.host_id == "WALL1"
    assert opening.opening_link == "LINK1"
    assert len(opening.polygons) == 1
    assert opening.polygons[0].polygon_id == "P_WIN"


def test_export_group_for_wall_with_two_windows():
    """P3b.2 (§7/§13 P3b.2): the surface-granular fold already handles any
    number of hosted Windows, since it delegates to the same whole-
    Building IR/export-group builders (see module docstring) - this checks
    that generalization actually holds rather than assuming it."""
    record_set = _record_set_with_window()
    second_ring = RingRecord(ring_id="P_WALL_2_", role="interior", coords=WIN_RING2, opening_link="LINK2")
    wall_polygon = record_set.polygon("P_WALL")
    wall_polygon = __import__("dataclasses").replace(wall_polygon, interiors=wall_polygon.interiors + (second_ring,))
    record_set = record_set.with_polygon(wall_polygon)
    second_window_polygon = PolygonRecord(
        polygon_id="P_WIN2", feature_id="WIN2",
        exterior=RingRecord(ring_id="P_WIN2_0_", role="exterior", coords=WIN_EXT2),
        geometry_state="committed",
    )
    second_window = FeatureRecord(
        gml_id="WIN2", kind="Window", semantic_type="Window", parent_id="WALL1", host_id="WALL1",
        opening_link="LINK2", generator="facade.window.rect", polygon_ids=("P_WIN2",),
    )
    record_set = record_set.with_feature(second_window).with_polygon(second_window_polygon)

    group = gsd.gs_surface_export_group("WALL1", record_set)

    assert group.gml_id == "WALL1"
    assert len(group.polygons[0].interiors) == 2
    assert {r.opening_link for r in group.polygons[0].interiors} == {"LINK1", "LINK2"}
    assert len(group.openings) == 2
    assert {o.gml_id for o in group.openings} == {"WIN1", "WIN2"}
    assert {o.opening_link for o in group.openings} == {"LINK1", "LINK2"}


def test_export_group_for_windowless_wall():
    group = gsd.gs_surface_export_group("WALL1", _record_set_windowless())
    assert group.gml_id == "WALL1"
    assert group.openings == ()
    assert len(group.polygons) == 1
    assert group.polygons[0].interiors == ()


def test_export_group_matches_building_level_fold():
    """Same wall, folded two ways: once through the whole-Building builder
    (as if the Building were fully IR-eligible), once through the GS.3.2
    surface-only seam. The two must agree field-for-field - this is the
    "no second semantic representation" proof (task item 6)."""
    record_set = _record_set_with_window()
    building = FeatureRecord(gml_id="BLD1", kind="Building")
    wall_with_parent = RecordSet(
        features=tuple(
            f if f.gml_id != "WALL1" else __import__("dataclasses").replace(f, parent_id="BLD1")
            for f in record_set.features
        ) + (building,),
        polygons=record_set.polygons,
    )
    building_ir = ir_adapter.building_ir_from_root(wall_with_parent, building)
    building_groups = ir_to_groups.groups_from_building_ir(building_ir)
    via_building = building_groups.surfaces[0]

    via_surface = gsd.gs_surface_export_group("WALL1", record_set)

    import dataclasses as _dc
    assert via_surface == _dc.replace(via_building, parent_id="")
