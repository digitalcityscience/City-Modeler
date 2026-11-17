"""CAC-P5.5: legacy Door passthrough merge safety. Pure Python, no bpy.

Scope: ``domain/ir_to_groups.py::merge_door_passthrough`` only - the second
half of the P5.5 seam (``blender/door_passthrough.py::extract_door_
passthrough`` is the bpy-dependent extraction half; see
``scripts/headless/test_cac_p5_5_door_passthrough.py`` for that side, which
needs a real Blender process this environment does not have).

``merge_door_passthrough`` takes an ``ExportBuildingGroups`` already built
by ``groups_from_building_ir`` (P5.4) plus a tuple of legacy-Door
``ExportOpening`` entries, and folds the latter onto the matching host
surface. These tests build both inputs directly as plain dataclasses -
door_passthrough.py's own bpy-side extraction is not exercised here.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.ir_to_groups import (  # noqa: E402
    DoorPassthroughMergeError, ExportBuildingGroups, ExportOpening,
    ExportPolygon, ExportRing, ExportSurface, groups_from_building_ir,
)
from shared.semantic_tree import BuildingNode, OpeningNode, PolygonNode, RingNode, SurfaceNode  # noqa: E402

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
DOOR_FILL = ((1.0, 0.0, 0.0), (1.8, 0.0, 0.0), (1.8, 0.0, 2.0), (1.0, 0.0, 2.0))
WIN_FILL = ((2.5, 0.0, 0.5), (3.3, 0.0, 0.5), (3.3, 0.0, 1.3), (2.5, 0.0, 1.3))


def wall_polygon_node(polygon_id="P_WALL"):
    return PolygonNode(
        polygon_id=polygon_id,
        exterior=RingNode(ring_id=f"{polygon_id}_0_", polygon_id=polygon_id, role="exterior", coords=WORLD_EXT),
    )


def window_opening_node(gml_id="WIN_A", host_id="WALL1", polygon_id="P_WIN_A"):
    return OpeningNode(
        gml_id=gml_id, semantic_type="Window", host_id=host_id, parent_id=host_id,
        polygons=(PolygonNode(
            polygon_id=polygon_id,
            exterior=RingNode(ring_id=f"{polygon_id}_0_", polygon_id=polygon_id, role="exterior", coords=WIN_FILL),
        ),),
    )


def building_one_wall(openings=()):
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=2, lod_origin="source",
        polygons=(wall_polygon_node(),), openings=openings,
    )
    return BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,))


def door_polygon(polygon_id, coords=DOOR_FILL):
    return ExportPolygon(
        polygon_id=polygon_id,
        exterior=ExportRing(ring_id=f"{polygon_id}_0_", role="exterior", coords=coords),
    )


def door_opening(gml_id="DOOR_A", host_id="WALL1", polygon_id="DOOR_A_door_poly0"):
    return ExportOpening(
        gml_id=gml_id, semantic_type="Door", host_id=host_id,
        lod=2, lod_origin="legacy_door_passthrough",
        polygons=(door_polygon(polygon_id),),
    )


# 1. Empty door_openings is a no-op, returns the same object ------------------


def test_empty_door_openings_returns_same_groups_object():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    assert merge_door_passthrough(groups, ()) is groups


# 2. A Door merges onto its host surface's openings ----------------------------


def test_door_merges_onto_matching_host_surface():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    merged = merge_door_passthrough(groups, (door_opening(),))
    assert [o.gml_id for o in merged.surfaces[0].openings] == ["DOOR_A"]
    assert merged.surfaces[0].openings[0].semantic_type == "Door"


# 3. Door + a record-backed Window on the same host survive together ----------


def test_door_and_record_backed_window_coexist_on_same_host():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall(openings=(window_opening_node(),)))
    merged = merge_door_passthrough(groups, (door_opening(),))
    ids = sorted(o.gml_id for o in merged.surfaces[0].openings)
    assert ids == ["DOOR_A", "WIN_A"]
    kinds = {o.gml_id: o.semantic_type for o in merged.surfaces[0].openings}
    assert kinds == {"DOOR_A": "Door", "WIN_A": "Window"}


# 4. Deterministic ordering: Door + Window openings sorted by gml_id ----------


def test_merged_openings_are_sorted_by_gml_id_regardless_of_input_order():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall(openings=(window_opening_node(gml_id="ZZZ_WIN"),)))
    merged = merge_door_passthrough(groups, (door_opening(gml_id="AAA_DOOR"),))
    assert [o.gml_id for o in merged.surfaces[0].openings] == ["AAA_DOOR", "ZZZ_WIN"]


# 5. Two Doors on one host remain distinct and sorted --------------------------


def test_two_doors_on_one_host_remain_distinct_and_sorted():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    merged = merge_door_passthrough(groups, (
        door_opening(gml_id="DOOR_B", polygon_id="DOOR_B_door_poly0"),
        door_opening(gml_id="DOOR_A", polygon_id="DOOR_A_door_poly0"),
    ))
    assert [o.gml_id for o in merged.surfaces[0].openings] == ["DOOR_A", "DOOR_B"]


# 6. Doors on different hosts land on their own surface ------------------------


def test_doors_on_different_hosts_land_on_their_own_surface():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    wall2 = SurfaceNode(
        gml_id="WALL2", semantic_type="WallSurface", parent_id="BLD1", lod=2, lod_origin="source",
        polygons=(wall_polygon_node("P_WALL2"),),
    )
    building = BuildingNode(gml_id="BLD1", boundary_surfaces=(
        SurfaceNode(gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=2, lod_origin="source",
                    polygons=(wall_polygon_node(),)),
        wall2,
    ))
    groups = groups_from_building_ir(building)
    merged = merge_door_passthrough(groups, (
        door_opening(gml_id="DOOR_A", host_id="WALL1", polygon_id="DOOR_A_door_poly0"),
        door_opening(gml_id="DOOR_B", host_id="WALL2", polygon_id="DOOR_B_door_poly0"),
    ))
    by_surface = {s.gml_id: [o.gml_id for o in s.openings] for s in merged.surfaces}
    assert by_surface == {"WALL1": ["DOOR_A"], "WALL2": ["DOOR_B"]}


# 7. Original groups object is never mutated -----------------------------------


def test_original_groups_untouched_after_merge():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    merge_door_passthrough(groups, (door_opening(),))
    assert groups.surfaces[0].openings == ()


# 8. Collision: Door gml_id equals an existing surface gml_id -----------------


def test_door_gml_id_colliding_with_surface_id_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (door_opening(gml_id="WALL1"),))


# 9. Collision: Door gml_id equals an existing opening gml_id -----------------


def test_door_gml_id_colliding_with_opening_id_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall(openings=(window_opening_node(),)))
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (door_opening(gml_id="WIN_A"),))


# 10. Collision: duplicate Door gml_id among the Doors themselves -------------


def test_duplicate_door_gml_id_among_doors_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (
            door_opening(gml_id="DOOR_A", polygon_id="DOOR_A_door_poly0"),
            door_opening(gml_id="DOOR_A", polygon_id="DOOR_A_door_poly1"),
        ))


# 11. Collision: Door polygon_id equals an existing IR polygon_id -------------


def test_door_polygon_id_colliding_with_ir_polygon_id_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall(openings=(window_opening_node(),)))
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (door_opening(polygon_id="P_WIN_A"),))


# 12. Collision: Door ring_id equals an existing IR ring_id -------------------


def test_door_ring_id_colliding_with_ir_ring_id_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    colliding = ExportOpening(
        gml_id="DOOR_A", semantic_type="Door", host_id="WALL1",
        polygons=(ExportPolygon(
            polygon_id="DOOR_A_door_poly0",
            exterior=ExportRing(ring_id="P_WALL_0_", role="exterior", coords=DOOR_FILL),
        ),),
    )
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (colliding,))


# 13. A Door with no matching host surface in the IR refuses -------------------


def test_door_with_unknown_host_surface_refuses():
    from shared.authoring_core.domain.ir_to_groups import merge_door_passthrough
    groups = groups_from_building_ir(building_one_wall())
    with pytest.raises(DoorPassthroughMergeError):
        merge_door_passthrough(groups, (door_opening(host_id="WALL_NOT_IN_IR"),))


# 14. P5.4 groups_from_building_ir itself never sees a Door -------------------


def test_groups_from_building_ir_alone_has_no_door_semantic_type():
    groups = groups_from_building_ir(building_one_wall(openings=(window_opening_node(),)))
    kinds = {o.semantic_type for s in groups.surfaces for o in s.openings}
    assert "Door" not in kinds
