"""Geometry Findings panel: check details carry their points; rows map them into the scene."""
from shared import geometry_checks as gc
from shared import validation_findings as vf

O = (1000.0, 2000.0, 10.0)  # the scene's export offset


def cube_building(bent=0.0, drop_top=False):
    v = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1 + bent)]
    v = [(x + O[0], y + O[1], z + O[2]) for x, y, z in v]
    faces = [(0, 3, 2, 1), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    if not drop_top:
        faces.append((4, 5, 6, 7))
    polys = [{"id": f"P{i}", "exterior": [v[k] for k in f], "interiors": []} for i, f in enumerate(faces)]
    return {"surfaces": [{"type": "WallSurface", "id": "S", "polygons": polys, "fillings": []}], "solid": True}


def last(bldg):
    c = gc.check_building(bldg)
    return {"buildings": {"B1": {"checks": c["codes"], "details": c["details"], "citydoctor": {"X": 2}}}}


def test_non_planar_row_points_land_on_the_scene_face():
    rows = vf.rows(last(cube_building(bent=0.05)))
    bent = [r for r in rows if r["code"] == "GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE"]
    assert bent and all(r["building"] == "B1" and r["value"].endswith("mm") for r in bent)
    pts = vf.targets(bent[0], O)
    assert all(-1e-6 <= c <= 1.05 + 1e-6 for p in pts for c in p)  # back in scene coordinates
    assert len(vf.segments(bent[0], O)) == 2 * len(bent[0]["rings"][0])  # one segment per ring side


def test_open_shell_row_carries_its_edges():
    rows = vf.rows(last(cube_building(drop_top=True)))
    shell = [r for r in rows if r["code"] == "GE_S_NOT_CLOSED"]
    assert len(shell) == 1 and len(shell[0]["edges"]) == 4 and shell[0]["value"].startswith("4 edges")
    assert len(vf.segments(shell[0], O)) == 8


def test_citydoctor_count_is_a_row_without_points():
    rows = vf.rows(last(cube_building()))
    cd = [r for r in rows if r["label"] == "CityDoctor X"]
    assert cd and cd[0]["value"] == "x2" and vf.targets(cd[0], O) == []


def test_no_check_no_rows():
    assert vf.rows({}) == [] and vf.rows(None) == []
