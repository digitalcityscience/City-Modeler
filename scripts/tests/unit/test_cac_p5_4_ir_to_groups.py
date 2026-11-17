"""CAC-P5.4: semantic IR -> plain export groups. Pure Python, no bpy.

Scope: ``domain/ir_to_groups.py::groups_from_building_ir`` only. No writer,
no geom_buf, no CRS/origin transform, no Door passthrough (P5.5).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.ir_adapter import building_ir_from_records  # noqa: E402
from shared.authoring_core.domain.ir_to_groups import (  # noqa: E402
    ExportBuildingGroups, ExportOpening, ExportPolygon, ExportRing, ExportSurface,
    IRToGroupsError, groups_from_building_ir,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)
from shared.semantic_tree import BuildingNode, OpeningNode, PolygonNode, RingNode, SurfaceNode  # noqa: E402

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
RING_B = ((2.0, 0.5, 0.0), (3.0, 0.5, 0.0), (3.0, 1.5, 0.0), (2.0, 1.5, 0.0))
# Deliberately different from RING_A/RING_B, so a window's own fill polygon
# is never confused with (or accidentally equal to) its host's hole ring.
WIN_A_FILL = ((0.6, 0.6, 0.0), (1.4, 0.6, 0.0), (1.4, 1.4, 0.0), (0.6, 1.4, 0.0))
WIN_B_FILL = ((2.1, 0.6, 0.0), (2.9, 0.6, 0.0), (2.9, 1.4, 0.0), (2.1, 1.4, 0.0))


# ---------------------------------------------------------------------------
# Plain BuildingNode fixtures (constructed directly, not via records)
# ---------------------------------------------------------------------------


def wall_polygon_node(polygon_id="P_WALL", exterior=WORLD_EXT, interiors=()):
    return PolygonNode(
        polygon_id=polygon_id,
        exterior=RingNode(ring_id=f"{polygon_id}_0_", polygon_id=polygon_id, role="exterior", coords=exterior),
        interiors=interiors,
    )


def opening_node(gml_id, host_id, opening_link, polygon_id, coords, lod=3, lod_origin="authored"):
    return OpeningNode(
        gml_id=gml_id, semantic_type="Window", host_id=host_id, parent_id=host_id,
        opening_link=opening_link, lod=lod, lod_origin=lod_origin,
        polygons=(PolygonNode(
            polygon_id=polygon_id,
            exterior=RingNode(ring_id=f"{polygon_id}_0_", polygon_id=polygon_id, role="exterior", coords=coords),
        ),),
    )


def building_one_wall_no_window():
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=2, lod_origin="source",
        polygons=(wall_polygon_node(),),
    )
    return BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,))


def building_one_wall_two_windows():
    interiors = (
        RingNode(ring_id="P_WALL_1_", polygon_id="P_WALL", role="interior", coords=RING_A, opening_link="LINK_A"),
        RingNode(ring_id="P_WALL_2_", polygon_id="P_WALL", role="interior", coords=RING_B, opening_link="LINK_B"),
    )
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=3, lod_origin="authored",
        polygons=(wall_polygon_node(interiors=interiors),),
        openings=(
            opening_node("WIN_A", "WALL1", "LINK_A", "P_WIN_A", WIN_A_FILL),
            opening_node("WIN_B", "WALL1", "LINK_B", "P_WIN_B", WIN_B_FILL),
        ),
    )
    return BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,))


# 1. Building -> one WallSurface ----------------------------------------------


def test_building_with_one_wall_surface():
    groups = groups_from_building_ir(building_one_wall_no_window())
    assert isinstance(groups, ExportBuildingGroups)
    assert groups.building_gml_id == "BLD1"
    assert [s.gml_id for s in groups.surfaces] == ["WALL1"]
    assert groups.surfaces[0].openings == ()


# 2. WallSurface polygon exterior ring ----------------------------------------


def test_wall_polygon_exterior_ring():
    groups = groups_from_building_ir(building_one_wall_no_window())
    polygon = groups.surfaces[0].polygons[0]
    assert isinstance(polygon, ExportPolygon)
    assert polygon.exterior.ring_id == "P_WALL_0_"
    assert polygon.exterior.role == "exterior"
    assert polygon.exterior.coords == WORLD_EXT


# 3. multiple interior rings ---------------------------------------------------


def test_multiple_interior_rings_preserved():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    polygon = groups.surfaces[0].polygons[0]
    assert [r.ring_id for r in polygon.interiors] == ["P_WALL_1_", "P_WALL_2_"]
    assert {r.coords for r in polygon.interiors} == {RING_A, RING_B}


# 4. one Window nested on WallSurface ------------------------------------------


def test_one_window_nested_on_wall():
    groups = groups_from_building_ir(building_one_wall_no_window())
    assert groups.surfaces[0].openings == ()

    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1",
        polygons=(wall_polygon_node(interiors=(
            RingNode(ring_id="P_WALL_1_", polygon_id="P_WALL", role="interior", coords=RING_A, opening_link="LINK_A"),
        )),),
        openings=(opening_node("WIN_A", "WALL1", "LINK_A", "P_WIN_A", WIN_A_FILL),),
    )
    groups = groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,)))
    assert [o.gml_id for o in groups.surfaces[0].openings] == ["WIN_A"]


# 5. two Windows on same WallSurface --------------------------------------------


def test_two_windows_on_same_wall_sorted_by_gml_id():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    assert [o.gml_id for o in groups.surfaces[0].openings] == ["WIN_A", "WIN_B"]
    assert all(isinstance(o, ExportOpening) for o in groups.surfaces[0].openings)


# 6. Window polygon geometry remains distinct from host hole ring --------------


def test_window_polygon_distinct_from_host_hole_ring():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    wall = groups.surfaces[0]
    hole_a = next(r for r in wall.polygons[0].interiors if r.opening_link == "LINK_A")
    window_a = next(o for o in wall.openings if o.gml_id == "WIN_A")
    fill_a = window_a.polygons[0].exterior

    assert hole_a.ring_id != fill_a.ring_id
    assert hole_a.coords == RING_A
    assert fill_a.coords == WIN_A_FILL
    assert hole_a.coords != fill_a.coords


# 7. Building/surface/Window ids preserved exactly ------------------------------


def test_ids_preserved_exactly():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    assert groups.building_gml_id == "BLD1"
    assert groups.surfaces[0].gml_id == "WALL1"
    assert {o.gml_id for o in groups.surfaces[0].openings} == {"WIN_A", "WIN_B"}


# 8. polygon ids preserved -------------------------------------------------------


def test_polygon_ids_preserved():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    wall = groups.surfaces[0]
    assert wall.polygons[0].polygon_id == "P_WALL"
    window_a = next(o for o in wall.openings if o.gml_id == "WIN_A")
    assert window_a.polygons[0].polygon_id == "P_WIN_A"


# 9. ring ids preserved -----------------------------------------------------------


def test_ring_ids_preserved():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    polygon = groups.surfaces[0].polygons[0]
    assert polygon.exterior.ring_id == "P_WALL_0_"
    assert {r.ring_id for r in polygon.interiors} == {"P_WALL_1_", "P_WALL_2_"}


# 10. LoD preserved -----------------------------------------------------------------


def test_lod_preserved():
    groups = groups_from_building_ir(building_one_wall_no_window())
    assert groups.surfaces[0].lod == 2
    assert groups.surfaces[0].lod_origin == "source"

    groups = groups_from_building_ir(building_one_wall_two_windows())
    window_a = next(o for o in groups.surfaces[0].openings if o.gml_id == "WIN_A")
    assert window_a.lod == 3
    assert window_a.lod_origin == "authored"


# 11. host/opening relationship preserved --------------------------------------------


def test_host_relationship_preserved():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    for opening in groups.surfaces[0].openings:
        assert opening.host_id == "WALL1"


# 12. committed coordinates preserved without CRS/origin transform ------------------


def test_coordinates_preserved_without_transform():
    groups = groups_from_building_ir(building_one_wall_two_windows())
    wall = groups.surfaces[0]
    assert wall.polygons[0].exterior.coords == WORLD_EXT
    window_a = next(o for o in wall.openings if o.gml_id == "WIN_A")
    assert window_a.polygons[0].exterior.coords == WIN_A_FILL


# 13. deterministic output under reversed/shuffled IR ordering ----------------------


def test_deterministic_under_reversed_construction_order():
    forward = building_one_wall_two_windows()
    wall = forward.boundary_surfaces[0]
    scrambled_wall = SurfaceNode(
        gml_id=wall.gml_id, semantic_type=wall.semantic_type, parent_id=wall.parent_id,
        lod=wall.lod, lod_origin=wall.lod_origin,
        polygons=(PolygonNode(
            polygon_id=wall.polygons[0].polygon_id,
            exterior=wall.polygons[0].exterior,
            interiors=tuple(reversed(wall.polygons[0].interiors)),
        ),),
        openings=tuple(reversed(wall.openings)),
    )
    scrambled = BuildingNode(gml_id="BLD1", boundary_surfaces=(scrambled_wall,))

    assert groups_from_building_ir(forward) == groups_from_building_ir(scrambled)


def test_deterministic_under_shuffled_surface_order():
    wall_a = SurfaceNode(gml_id="WALL_A", semantic_type="WallSurface", parent_id="BLD1",
                          polygons=(wall_polygon_node(polygon_id="P_A"),))
    wall_b = SurfaceNode(gml_id="WALL_B", semantic_type="WallSurface", parent_id="BLD1",
                          polygons=(wall_polygon_node(polygon_id="P_B"),))
    forward = BuildingNode(gml_id="BLD1", boundary_surfaces=(wall_a, wall_b))
    shuffled = BuildingNode(gml_id="BLD1", boundary_surfaces=(wall_b, wall_a))
    assert groups_from_building_ir(forward) == groups_from_building_ir(shuffled)


# 14. no Blender/Material/object references in output -------------------------------


def test_no_blender_or_material_fields_in_schema():
    import dataclasses
    forbidden = {"mat", "material", "obj", "object", "bpy"}
    for cls in (ExportBuildingGroups, ExportSurface, ExportOpening, ExportPolygon, ExportRing):
        names = {f.name.lower() for f in dataclasses.fields(cls)}
        assert names.isdisjoint(forbidden), f"{cls.__name__} carries a forbidden field: {names & forbidden}"


# 15. serialization/equality/canonical representation stable ------------------------


def test_equality_and_to_dict_stable_across_calls():
    building = building_one_wall_two_windows()
    first = groups_from_building_ir(building)
    second = groups_from_building_ir(building)
    assert first == second
    assert first.to_dict() == second.to_dict()


# 16. empty/no-opening wall works -----------------------------------------------------


def test_empty_no_opening_wall_works():
    groups = groups_from_building_ir(building_one_wall_no_window())
    assert groups.surfaces[0].openings == ()
    assert groups.surfaces[0].polygons[0].interiors == ()


# 17. invalid/ambiguous IR fails explicitly ------------------------------------------


def test_missing_building_gml_id_refuses():
    with pytest.raises(IRToGroupsError, match="no gml_id"):
        groups_from_building_ir(BuildingNode(gml_id=""))


def test_none_building_refuses():
    with pytest.raises(IRToGroupsError, match="no Building IR"):
        groups_from_building_ir(None)


def test_missing_surface_semantic_type_refuses():
    wall = SurfaceNode(gml_id="WALL1", semantic_type="", parent_id="BLD1",
                        polygons=(wall_polygon_node(),))
    with pytest.raises(IRToGroupsError, match="semantic_type"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,)))


def test_polygon_missing_exterior_ring_id_refuses():
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1",
        polygons=(PolygonNode(polygon_id="P_WALL", exterior=RingNode(ring_id="")),),
    )
    with pytest.raises(IRToGroupsError, match="missing exterior ring"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,)))


def test_opening_missing_host_id_refuses():
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1",
        polygons=(wall_polygon_node(),),
        openings=(OpeningNode(gml_id="WIN_A", semantic_type="Window", host_id=""),),
    )
    with pytest.raises(IRToGroupsError, match="no host_id"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,)))


def test_duplicate_surface_gml_id_refuses():
    wall = SurfaceNode(gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1",
                        polygons=(wall_polygon_node(),))
    other = SurfaceNode(gml_id="WALL1", semantic_type="RoofSurface", parent_id="BLD1",
                         polygons=(wall_polygon_node(polygon_id="P_ROOF"),))
    with pytest.raises(IRToGroupsError, match="duplicate surface gml_id"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall, other)))


def test_duplicate_polygon_id_across_surfaces_refuses():
    wall_a = SurfaceNode(gml_id="WALL_A", semantic_type="WallSurface", parent_id="BLD1",
                          polygons=(wall_polygon_node(polygon_id="P_SHARED"),))
    wall_b = SurfaceNode(gml_id="WALL_B", semantic_type="WallSurface", parent_id="BLD1",
                          polygons=(wall_polygon_node(polygon_id="P_SHARED"),))
    with pytest.raises(IRToGroupsError, match="duplicate polygon_id"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall_a, wall_b)))


def test_duplicate_ring_id_refuses():
    wall = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1",
        polygons=(PolygonNode(
            polygon_id="P_WALL",
            exterior=RingNode(ring_id="P_WALL_0_", polygon_id="P_WALL", role="exterior", coords=WORLD_EXT),
            interiors=(RingNode(ring_id="P_WALL_0_", polygon_id="P_WALL", role="interior", coords=RING_A),),
        ),),
    )
    with pytest.raises(IRToGroupsError, match="duplicate ring_id"):
        groups_from_building_ir(BuildingNode(gml_id="BLD1", boundary_surfaces=(wall,)))


# Representative tree: full records -> IR -> groups pipeline ------------------------
# Building
# `-- WallSurface
#     |-- Polygon (exterior, interior A, interior B)
#     |-- Window A -> Polygon A
#     `-- Window B -> Polygon B


def _record_building():
    return FeatureRecord(gml_id="BLD1", kind="Building")


def _record_wall():
    return FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL",),
    )


def _record_window(gml_id, opening_link, polygon_id):
    return FeatureRecord(
        gml_id=gml_id, kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link=opening_link, lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=(polygon_id,),
    )


def _record_window_polygon(gml_id, polygon_id, coords):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=gml_id, geometry_state="committed",
        exterior=RingRecord(f"{polygon_id}_0_", "exterior", coords),
    )


def _record_wall_polygon():
    return PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord("P_WALL_0_", "exterior", WORLD_EXT),
        interiors=(
            RingRecord("P_WALL_1_", "interior", RING_A, opening_link="LINK_A"),
            RingRecord("P_WALL_2_", "interior", RING_B, opening_link="LINK_B"),
        ),
    )


def _full_pipeline_record_set():
    return RecordSet(
        features=(
            _record_building(), _record_wall(),
            _record_window("WIN_A", "LINK_A", "P_WIN_A"),
            _record_window("WIN_B", "LINK_B", "P_WIN_B"),
        ),
        polygons=(
            _record_wall_polygon(),
            _record_window_polygon("WIN_A", "P_WIN_A", WIN_A_FILL),
            _record_window_polygon("WIN_B", "P_WIN_B", WIN_B_FILL),
        ),
    )


def test_full_records_to_ir_to_groups_pipeline_preserves_every_identity():
    ir = building_ir_from_records(_full_pipeline_record_set(), "BLD1")
    groups = groups_from_building_ir(ir)

    assert groups.building_gml_id == "BLD1"
    assert len(groups.surfaces) == 1
    wall = groups.surfaces[0]
    assert wall.gml_id == "WALL1"
    assert wall.polygons[0].polygon_id == "P_WALL"
    assert wall.polygons[0].exterior.coords == WORLD_EXT
    assert {r.ring_id for r in wall.polygons[0].interiors} == {"P_WALL_1_", "P_WALL_2_"}
    assert [o.gml_id for o in wall.openings] == ["WIN_A", "WIN_B"]
    for opening in wall.openings:
        assert opening.host_id == "WALL1"
    win_a = next(o for o in wall.openings if o.gml_id == "WIN_A")
    win_b = next(o for o in wall.openings if o.gml_id == "WIN_B")
    assert win_a.polygons[0].polygon_id == "P_WIN_A"
    assert win_a.polygons[0].exterior.coords == WIN_A_FILL
    assert win_b.polygons[0].polygon_id == "P_WIN_B"
    assert win_b.polygons[0].exterior.coords == WIN_B_FILL
    # host hole ring and window fill geometry stay distinct objects
    hole_a = next(r for r in wall.polygons[0].interiors if r.opening_link == "LINK_A")
    assert hole_a.coords == RING_A
    assert hole_a.coords != win_a.polygons[0].exterior.coords
