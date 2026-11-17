"""CAC-P5.2: full records -> Building IR builder. Pure Python, no bpy.

Scope: ``domain/ir_adapter.py::building_ir_from_records`` only - the
records->IR traversal P5.1 explicitly left undone (see
``test_cac_p5_semantic_ir.py``'s module docstring and
``docs/agent/authoring-core/issues/CAC-P5.1.md``'s "Notes for P5.2"). No
Blender object, no writer, no CRS/origin transform.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.ir_adapter import (  # noqa: E402
    IRAdapterError, building_ir_from_records,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)
from shared.semantic_tree import BuildingNode, OpeningNode, PolygonNode, RingNode, SurfaceNode  # noqa: E402

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
RING_B = ((2.0, 0.5, 0.0), (3.0, 0.5, 0.0), (3.0, 1.5, 0.0), (2.0, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))


def building(**over):
    base = dict(gml_id="BLD1", kind="Building")
    base.update(over)
    return FeatureRecord(**base)


def wall(**over):
    base = dict(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL",),
    )
    base.update(over)
    return FeatureRecord(**base)


def window(gml_id="WIN_A", opening_link="LINK_A", host_id="WALL1", polygon_id="P_WIN_A", **over):
    base = dict(
        gml_id=gml_id, kind="Window", semantic_type="Window",
        parent_id=host_id, host_id=host_id, owner_id="BLD1",
        opening_link=opening_link, lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=(polygon_id,),
    )
    base.update(over)
    return FeatureRecord(**base)


def window_polygon(gml_id="WIN_A", polygon_id="P_WIN_A", coords=WIN_EXT):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=gml_id, geometry_state="committed",
        exterior=RingRecord(f"{polygon_id}_0_", "exterior", coords),
    )


def wall_polygon(links_and_coords=(), polygon_id="P_WALL", feature_id="WALL1", exterior_coords=WORLD_EXT):
    rings = tuple(
        RingRecord(f"{polygon_id}_{i}_", "interior", coords, opening_link=link)
        for i, (link, coords) in enumerate(links_and_coords, start=1)
    )
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=feature_id, geometry_state="committed",
        exterior=RingRecord(f"{polygon_id}_0_", "exterior", exterior_coords),
        interiors=rings,
    )


def one_wall_no_window():
    return RecordSet(
        features=(building(), wall()),
        polygons=(wall_polygon(),),
    )


def one_wall_one_window():
    return RecordSet(
        features=(building(), wall(), window()),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )


def one_wall_two_windows():
    return RecordSet(
        features=(building(), wall(), window(), window(gml_id="WIN_B", opening_link="LINK_B", polygon_id="P_WIN_B")),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A), ("LINK_B", RING_B))),
            window_polygon(),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B", coords=RING_B),
        ),
    )


# 1. one Building + one WallSurface --------------------------------------


def test_one_building_one_wall_surface():
    ir = building_ir_from_records(one_wall_no_window(), "BLD1")
    assert isinstance(ir, BuildingNode)
    assert ir.gml_id == "BLD1"
    assert [s.gml_id for s in ir.boundary_surfaces] == ["WALL1"]
    assert ir.boundary_surfaces[0].openings == ()


# 2. one WallSurface + one Window -----------------------------------------


def test_one_wall_one_window():
    ir = building_ir_from_records(one_wall_one_window(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    assert [o.gml_id for o in wall_node.openings] == ["WIN_A"]
    assert wall_node.openings[0].host_id == "WALL1"


# 3. one WallSurface + two Windows ------------------------------------------


def test_one_wall_two_windows():
    ir = building_ir_from_records(one_wall_two_windows(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    # both Window identities preserved, deterministically ordered
    assert [o.gml_id for o in wall_node.openings] == ["WIN_A", "WIN_B"]
    # host relation preserved for both
    assert all(o.host_id == "WALL1" for o in wall_node.openings)
    # both wall rings preserved
    assert {r.ring_id for r in wall_node.polygons[0].interiors} == {"P_WALL_1_", "P_WALL_2_"}
    # no ring/opening cross-pairing
    win_a, win_b = wall_node.openings
    ring_a = next(r for r in wall_node.polygons[0].interiors if r.opening_link == win_a.opening_link)
    ring_b = next(r for r in wall_node.polygons[0].interiors if r.opening_link == win_b.opening_link)
    assert ring_a.coords == RING_A
    assert ring_b.coords == RING_B


# 4. multiple wall polygons --------------------------------------------------


def test_multiple_wall_polygons():
    rs = RecordSet(
        features=(building(), wall(polygon_ids=("P_WALL_1", "P_WALL_2"))),
        polygons=(
            wall_polygon(polygon_id="P_WALL_2"),
            wall_polygon(polygon_id="P_WALL_1"),
        ),
    )
    ir = building_ir_from_records(rs, "BLD1")
    polygons = ir.boundary_surfaces[0].polygons
    assert [p.polygon_id for p in polygons] == ["P_WALL_1", "P_WALL_2"]


# 5. exterior + multiple interior rings --------------------------------------


def test_exterior_and_multiple_interior_rings():
    ir = building_ir_from_records(one_wall_two_windows(), "BLD1")
    polygon = ir.boundary_surfaces[0].polygons[0]
    assert polygon.exterior.role == "exterior"
    assert polygon.exterior.coords == WORLD_EXT
    assert len(polygon.interiors) == 2


# 6. FeatureRecord gml_ids preserved ------------------------------------------


def test_feature_gml_ids_preserved():
    ir = building_ir_from_records(one_wall_two_windows(), "BLD1")
    assert ir.gml_id == "BLD1"
    assert ir.boundary_surfaces[0].gml_id == "WALL1"
    assert {o.gml_id for o in ir.boundary_surfaces[0].openings} == {"WIN_A", "WIN_B"}


# 7. polygon/ring ids preserved -----------------------------------------------


def test_polygon_and_ring_ids_preserved():
    ir = building_ir_from_records(one_wall_one_window(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    assert wall_node.polygons[0].polygon_id == "P_WALL"
    assert wall_node.polygons[0].exterior.ring_id == "P_WALL_0_"
    assert wall_node.openings[0].polygons[0].polygon_id == "P_WIN_A"


# 8. host/parent relations preserved ------------------------------------------


def test_host_and_parent_relations_preserved():
    ir = building_ir_from_records(one_wall_one_window(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    window_node = wall_node.openings[0]
    assert wall_node.parent_id == "BLD1"
    assert window_node.host_id == "WALL1"
    assert window_node.parent_id == "WALL1"


# 9. opening nesting derives from records only, not geometry ------------------


def test_opening_nesting_is_by_host_id_not_geometry():
    # Both windows sit at the same local coordinates in their own polygon,
    # so a geometry-equality match would be unable to tell them apart -
    # host_id is the only thing this builder consults.
    rs = one_wall_two_windows()
    ir = building_ir_from_records(rs, "BLD1")
    wall_node = ir.boundary_surfaces[0]
    assert len(wall_node.openings) == 2
    assert all(o.host_id == wall_node.gml_id for o in wall_node.openings)


# 10. committed coordinates preserved exactly ---------------------------------


def test_committed_coordinates_preserved_exactly():
    ir = building_ir_from_records(one_wall_one_window(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    assert wall_node.polygons[0].exterior.coords == WORLD_EXT
    ring = wall_node.polygons[0].interiors[0]
    assert ring.coords == RING_A
    assert wall_node.openings[0].polygons[0].exterior.coords == WIN_EXT


# 11. LoD preserved exactly ----------------------------------------------------


def test_lod_preserved_exactly():
    rs = RecordSet(
        features=(building(), wall(lod=2, lod_origin="source"), window(lod=3, lod_origin="authored")),
        polygons=(wall_polygon(links_and_coords=(("LINK_A", RING_A),)), window_polygon()),
    )
    ir = building_ir_from_records(rs, "BLD1")
    wall_node = ir.boundary_surfaces[0]
    assert wall_node.lod == 2
    assert wall_node.lod_origin == "source"
    assert wall_node.openings[0].lod == 3
    assert wall_node.openings[0].lod_origin == "authored"


# 12. deterministic ordering independent of input construction order ----------


def test_deterministic_ordering_independent_of_input_order():
    forward = one_wall_two_windows()
    scrambled = RecordSet(
        features=tuple(reversed(forward.features)),
        polygons=tuple(reversed(forward.polygons)),
    )
    ir_forward = building_ir_from_records(forward, "BLD1")
    ir_scrambled = building_ir_from_records(scrambled, "BLD1")
    assert ir_forward == ir_scrambled


# 13. missing host refuses -----------------------------------------------------


def test_missing_host_refuses():
    rs = RecordSet(
        features=(building(), window(host_id="GHOST")),
        polygons=(window_polygon(),),
    )
    with pytest.raises(IRAdapterError, match="GHOST"):
        building_ir_from_records(rs, "BLD1")


def test_host_naming_non_boundary_surface_refuses():
    rs = RecordSet(
        features=(building(), window(host_id="BLD1")),
        polygons=(window_polygon(),),
    )
    with pytest.raises(IRAdapterError, match="not a boundary surface"):
        building_ir_from_records(rs, "BLD1")


# 14. ambiguous opening_link refuses --------------------------------------------


def test_ambiguous_opening_link_across_openings_refuses():
    rs = RecordSet(
        features=(building(), wall(), window(), window(gml_id="WIN_B", polygon_id="P_WIN_B")),
        polygons=(
            wall_polygon(links_and_coords=(("LINK_A", RING_A),)),
            window_polygon(),
            window_polygon(gml_id="WIN_B", polygon_id="P_WIN_B"),
        ),
    )
    with pytest.raises(IRAdapterError, match="ambiguous opening_link"):
        building_ir_from_records(rs, "BLD1")


def test_ambiguous_opening_link_across_rings_refuses():
    duplicate_link_polygon = PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord("P_WALL_0_", "exterior", WORLD_EXT),
        interiors=(
            RingRecord("P_WALL_1_", "interior", RING_A, opening_link="LINK_A"),
            RingRecord("P_WALL_2_", "interior", RING_B, opening_link="LINK_A"),
        ),
    )
    rs = RecordSet(
        features=(building(), wall(), window()),
        polygons=(duplicate_link_polygon, window_polygon()),
    )
    with pytest.raises(IRAdapterError, match="more than one interior ring"):
        building_ir_from_records(rs, "BLD1")


# 15. dangling ring/opening relation refuses -----------------------------------


def test_dangling_ring_refuses():
    rs = RecordSet(
        features=(building(), wall()),
        polygons=(wall_polygon(links_and_coords=(("LINK_NOBODY", RING_A),)),),
    )
    with pytest.raises(IRAdapterError, match="dangling ring"):
        building_ir_from_records(rs, "BLD1")


def test_opening_without_ring_pairing_refuses():
    rs = RecordSet(
        features=(building(), wall(), window()),
        polygons=(wall_polygon(), window_polygon()),
    )
    with pytest.raises(IRAdapterError, match="no interior ring"):
        building_ir_from_records(rs, "BLD1")


def test_unknown_building_gml_id_refuses():
    with pytest.raises(IRAdapterError, match="no feature"):
        building_ir_from_records(one_wall_no_window(), "NOT_THERE")


def test_non_building_kind_refuses():
    with pytest.raises(IRAdapterError, match="expected 'Building'"):
        building_ir_from_records(one_wall_no_window(), "WALL1")


def test_duplicate_gml_id_refuses():
    rs = RecordSet(
        features=(building(), wall(), wall()),
        polygons=(wall_polygon(),),
    )
    with pytest.raises(IRAdapterError, match="duplicate gml_id"):
        building_ir_from_records(rs, "BLD1")


# Surfaces and openings do not merge their polygon collections ---------------


def test_surface_and_opening_polygons_are_not_merged():
    ir = building_ir_from_records(one_wall_one_window(), "BLD1")
    wall_node = ir.boundary_surfaces[0]
    window_node = wall_node.openings[0]
    assert {p.polygon_id for p in wall_node.polygons} == {"P_WALL"}
    assert {p.polygon_id for p in window_node.polygons} == {"P_WIN_A"}


# Full expected-tree comparison, matching P5.1's exact fixture shape --------


def test_full_tree_matches_expected_shape():
    ir = building_ir_from_records(one_wall_two_windows(), "BLD1")

    expected_wall_polygon = PolygonNode(
        polygon_id="P_WALL",
        exterior=RingNode(ring_id="P_WALL_0_", polygon_id="P_WALL", role="exterior", coords=WORLD_EXT),
        interiors=(
            RingNode(ring_id="P_WALL_1_", polygon_id="P_WALL", role="interior", coords=RING_A, opening_link="LINK_A"),
            RingNode(ring_id="P_WALL_2_", polygon_id="P_WALL", role="interior", coords=RING_B, opening_link="LINK_B"),
        ),
    )
    expected_window_a = OpeningNode(
        gml_id="WIN_A", semantic_type="Window", host_id="WALL1", parent_id="WALL1",
        opening_link="LINK_A", lod=3, lod_origin="authored",
        polygons=(PolygonNode(
            polygon_id="P_WIN_A",
            exterior=RingNode(ring_id="P_WIN_A_0_", polygon_id="P_WIN_A", role="exterior", coords=WIN_EXT),
        ),),
    )
    expected_window_b = OpeningNode(
        gml_id="WIN_B", semantic_type="Window", host_id="WALL1", parent_id="WALL1",
        opening_link="LINK_B", lod=3, lod_origin="authored",
        polygons=(PolygonNode(
            polygon_id="P_WIN_B",
            exterior=RingNode(ring_id="P_WIN_B_0_", polygon_id="P_WIN_B", role="exterior", coords=RING_B),
        ),),
    )
    expected_wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=3, lod_origin="authored",
        polygons=(expected_wall_polygon,), openings=(expected_window_a, expected_window_b),
    )
    expected = BuildingNode(gml_id="BLD1", boundary_surfaces=(expected_wall,))

    assert ir == expected
