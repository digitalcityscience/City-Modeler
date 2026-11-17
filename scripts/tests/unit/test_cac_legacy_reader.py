"""Legacy reader (CAC-P1.3): records from legacy object/material state,
on hand-built views shaped like golden/cac/import_ids_*.json. No Blender."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.legacy_reader import (  # noqa: E402
    MaterialView as M, ObjectView as O, feature_record_from_legacy, neutral_kind, read_legacy_scene,
)


def wall_mat(poly, surface="W1", lod=3):
    return M(f"Wall_{poly}", {"SurfaceTyp": "WallSurface", "con_surface_id": surface,
                              "gml_polygon_id": poly, "gml_ring_id": f"{poly}_0_", "lod": lod})


def ring_mat(host, n, surface="W1", link=None):
    props = {"Interior": True, "ExteriorPolyId": host, "SurfaceTyp": "WallSurface",
             "con_surface_id": surface, "gml_polygon_id": f"{host}_{n}", "gml_ring_id": f"{host}_{n}_", "lod": 3}
    if link:
        props["cgml_opening_link"] = link
    return M(f"InteriorWall_{host}_{n}", props)


def opening_mat(poly, opening, spelling="WindowSurface"):
    return M(f"{spelling}_{poly}", {"SurfaceTyp": spelling, "con_surface_id": opening, "opening_gml_id": opening,
                                   "gml_polygon_id": poly, "gml_ring_id": f"{poly}_0_", "lod": 3})


def exploded_scene(spelling="WindowSurface", link=None):
    building = O("Station", props={"cgml3_feature": "Building", "gml_id": "B1"})
    wall = O("Wall", parent="Station", props={"cgml3_feature": "WallSurface", "gml_id": "W1"},
             materials=(wall_mat("P1"), ring_mat("P1", 1, link=link), ring_mat("P1", 1, link=link)))
    window_props = {"cgml3_feature": spelling, "gml_id": "WIN1", "filling_parent_surface_id": "W1"}
    if link:
        window_props["cgml_opening_link"] = link
    window = O("Window", parent="Wall", props=window_props,
               materials=(opening_mat("PW1", "WIN1", spelling), opening_mat("PW2", "WIN1", spelling)))
    return [building, wall, window]


def test_exploded_building_wall_and_window():
    result = read_legacy_scene(exploded_scene())
    rs = result.records
    assert rs.validate() == [] and result.notes == ()
    wall, window = rs.feature("W1"), rs.feature("WIN1")
    assert (wall.kind, wall.parent_id, wall.owner_id, wall.host_id) == ("WallSurface", "B1", "B1", "")
    assert wall.polygon_ids == ("P1",) and wall.lod == 3 and wall.lod_origin == "inferred"
    assert (window.kind, window.parent_id, window.owner_id, window.host_id) == ("Window", "W1", "B1", "W1")
    assert window.source_element == "WindowSurface" and window.polygon_ids == ("PW1", "PW2")
    poly = rs.polygon("P1")
    assert poly.exterior.ring_id == "P1_0_" and poly.geometry_state == "ids_only"
    assert [r.ring_id for r in poly.interiors] == ["P1_1_"]    # repeated slot counted once
    assert rs.feature("B1").polygon_ids == () and rs.feature("B1").owner_id == ""


def test_v2_v3_and_alt_opening_spellings_give_the_same_kind():
    for spelling in ("Window", "WindowSurface", "con:Window", "bldg:Window"):
        window = read_legacy_scene(exploded_scene(spelling)).records.feature("WIN1")
        assert window.kind == "Window", spelling
        assert window.source_element == spelling
    assert neutral_kind("con:DoorSurface") == "Door" and neutral_kind("RoofSurface") == "RoofSurface"


def test_authored_window_keeps_opening_link_on_fill_and_ring():
    rs = read_legacy_scene(exploded_scene(link="OPENINGLINK_abc")).records
    assert rs.feature("WIN1").opening_link == "OPENINGLINK_abc"
    assert rs.polygon("P1").interiors[0].opening_link == "OPENINGLINK_abc"


def test_merged_building_material_only_surfaces_and_openings():
    building = O("Station", props={"cgml3_feature": "Building", "gml_id": "B1"}, materials=(
        wall_mat("P1", "W1"), ring_mat("P1", 1, "W1"), wall_mat("P2", "W2"),
        opening_mat("PW1", "WIN1"), M("Roof", {"SurfaceTyp": "RoofSurface", "con_surface_id": "R1",
                                                "gml_polygon_id": "PR", "gml_ring_id": "PR_0_", "lod": 2}),
    ))
    result = read_legacy_scene([building])
    rs = result.records
    assert rs.validate() == []
    assert {f.gml_id: (f.kind, f.parent_id, f.owner_id) for f in rs.features} == {
        "B1": ("Building", "", ""),
        "W1": ("WallSurface", "B1", "B1"),
        "W2": ("WallSurface", "B1", "B1"),
        "WIN1": ("Window", "", "B1"),          # host not recorded in merged legacy state
        "R1": ("RoofSurface", "B1", "B1"),
    }
    assert rs.feature("R1").lod == 2 and len(rs.polygon("P1").interiors) == 1
    assert any("WIN1" in n and "host surface not recorded" in n for n in result.notes)


def test_installation_polygons_without_surface_id_belong_to_the_object():
    building = O("B", props={"cgml3_feature": "Building", "gml_id": "B1", "lod": 3})
    chimney = O("Chimney", parent="B", props={
        "cgml3_feature": "BuildingInstallation", "cgml_semantic_type": "Chimney", "cgml_source_role": "outer",
        "cgml_parent_id": "B1", "gml_id": "I1"},
        materials=(M("inst", {"SurfaceTyp": "BuildingInstallation", "gml_polygon_id": "PI",
                              "gml_ring_id": "PI_0_", "lod": 3}),))
    rs = read_legacy_scene([building, chimney]).records
    inst = rs.feature("I1")
    assert (inst.kind, inst.semantic_type, inst.source_role, inst.parent_id) == \
        ("BuildingInstallation", "Chimney", "outer", "B1")
    assert inst.polygon_ids == ("PI",) and rs.validate() == []
    assert rs.feature("B1").lod == 3 and rs.feature("B1").lod_origin == "source"


def test_lod_sources():
    obj = O("X", props={"gml_id": "X", "cgml3_feature": "WallSurface", "cgml3_lod": "lod2"})
    assert (feature_record_from_legacy(obj).lod, feature_record_from_legacy(obj).lod_origin) == (2, "source")
    bare = O("Y", props={"gml_id": "Y", "cgml3_feature": "WallSurface"})
    assert (feature_record_from_legacy(bare).lod, feature_record_from_legacy(bare).lod_origin) == (None, "source")


def test_objects_without_gml_id_are_skipped_but_do_not_break_the_parent_chain():
    building = O("B", props={"cgml3_feature": "Building", "gml_id": "B1"})
    group = O("Group", parent="B")                     # e.g. an organising empty
    wall = O("Wall", parent="Group", props={"cgml3_feature": "WallSurface", "gml_id": "W1"})
    assert feature_record_from_legacy(group) is None
    rs = read_legacy_scene([building, group, wall]).records
    assert rs.feature("W1").parent_id == "B1" and rs.feature("W1").owner_id == "B1"
    assert len(rs.features) == 2


def test_building_part_owner_is_the_part():
    building = O("B", props={"cgml3_feature": "Building", "gml_id": "B1"})
    part = O("Part", parent="B", props={"cgml3_feature": "BuildingPart", "gml_id": "BP1", "cgml_parent_id": "B1"})
    wall = O("Wall", parent="Part", props={"cgml3_feature": "WallSurface", "gml_id": "W1"},
             materials=(wall_mat("P1"),))
    rs = read_legacy_scene([building, part, wall]).records
    assert rs.feature("BP1").owner_id == "B1"
    assert (rs.feature("W1").parent_id, rs.feature("W1").owner_id) == ("BP1", "BP1")


def test_inconsistent_legacy_state_is_reported_not_hidden():
    wall = O("Wall", props={"cgml3_feature": "WallSurface", "gml_id": "W1"},
             materials=(wall_mat("P1"), ring_mat("P_GONE", 1)))
    copy = O("Wall.001", props={"cgml3_feature": "WallSurface", "gml_id": "W1"}, materials=(wall_mat("P1"),))
    result = read_legacy_scene([wall, copy])
    assert any("also on another object" in n for n in result.notes)          # Shift+D duplicate
    assert any("names missing polygon 'P_GONE'" in n for n in result.notes)  # orphan ring material
    assert any("on more than one object" in n for n in result.notes)
