"""CAC-P5.6: per-Building IR export dispatch (bpy side).

Scope: ``shared/authoring_core/blender/export_dispatch.py::
building_export_plan`` only - the one function a v2/v3 writer adapter
calls per Building. Builds a minimal, hand-committed scene directly with
bpy/``storage.write_record`` (same convention as
``test_cac_p5_5_door_passthrough.py`` and the P5.2 domain fixture
``one_wall_one_window`` it mirrors, ``scripts/tests/unit/
test_cac_p5_2_ir_builder.py``) rather than importing a fixture .gml - a
real-world fixture (see the Railway/CS1 exploration in the P5.6 task card)
is either too complex to become IR-eligible without extra scaffolding, or
uses a schema this contract doesn't need. Legacy object properties
(``gml_id``, ``cgml3_feature``, ``filling_parent_surface_id``) are set to
exactly what ``domain/legacy_reader.py::feature_record_from_legacy``
reads, so this scene's *legacy* view and its *persisted* records agree,
the way a real committed scene would.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_6_export_dispatch.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))
from shared.authoring_core.blender import door_passthrough as dp  # noqa: E402
from shared.authoring_core.blender import export_dispatch  # noqa: E402
from shared.authoring_core.blender import storage  # noqa: E402
from shared.authoring_core.domain import export_eligibility as elig  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
DOOR_COORDS = ((2.0, 0.0, 0.0), (2.8, 0.0, 0.0), (2.8, 0.0, 2.0), (2.0, 0.0, 2.0))


def _empty(name, **props):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    for key, value in props.items():
        obj[key] = value
    return obj


def _mesh_object(name, coords, parent=None, polygon_id=None, **props):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(coords), [], [list(range(len(coords)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    if polygon_id is not None:
        # A minimal legacy face material - just enough for
        # domain/legacy_reader.py::read_legacy_scene's material scan to
        # report the same polygon_id this object's own persisted record
        # carries, so consistency_view.run_validation (called from
        # blender/building_root.py::export_eligibility) finds no
        # POLYGON_IDS_MISMATCH/COORDS_STALE drift between the two.
        mat = bpy.data.materials.new(f"{name}_mat")
        mat["gml_polygon_id"] = polygon_id
        mesh.materials.append(mat)
    for key, value in props.items():
        obj[key] = value
    return obj


def build_scene(with_door=False):
    """Building > WallSurface(committed, one window hole) > Window(committed).

    Mirrors ``test_cac_p5_2_ir_builder.py``'s ``one_wall_one_window()``
    fixture exactly (same ids/coords), so this scene's persisted records
    are known-good IR input; only the legacy/bpy side is new here.
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    building_obj = _empty("Building1", gml_id="BLD1", cgml3_feature="Building")
    wall_obj = _mesh_object(
        "Wall1", WORLD_EXT, parent=building_obj, polygon_id="P_WALL",
        gml_id="WALL1", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    window_obj = _mesh_object(
        "Window1", WIN_EXT, parent=wall_obj, polygon_id="P_WIN_A",
        gml_id="WIN_A", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL1", cgml_opening_link="LINK_A",
    )

    building_record = FeatureRecord(gml_id="BLD1", kind="Building")
    storage.write_record(building_obj, RecordSet(features=(building_record,)))

    wall_record = FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord("P_WALL_0_", "exterior", WORLD_EXT),
        interiors=(RingRecord("P_WALL_1_", "interior", RING_A, opening_link="LINK_A"),),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))

    window_record = FeatureRecord(
        gml_id="WIN_A", kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link="LINK_A", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN_A",),
    )
    window_polygon = PolygonRecord(
        polygon_id="P_WIN_A", feature_id="WIN_A", geometry_state="committed",
        exterior=RingRecord("P_WIN_A_0_", "exterior", WIN_EXT),
    )
    storage.write_record(window_obj, RecordSet(features=(window_record,), polygons=(window_polygon,)))

    door_obj = None
    if with_door:
        door_obj = _mesh_object(
            "Door1", DOOR_COORDS, parent=wall_obj,
            gml_id="DOOR_A", cgml3_feature="Door", structure_part="opening",
            filling_parent_surface_id="WALL1", cgml3_lod=3,
        )

    bpy.context.view_layer.update()
    return building_obj, wall_obj, window_obj, door_obj


def main():
    r = h.Results(__file__)
    h.load_addon()

    # building_export_plan is flag-agnostic by design (PLAN.md P5.6 item 2/9):
    # the *writer* checks shared.authoring_core.blender.flag.is_enabled()
    # before ever calling this function, so this scene is built once with
    # the flag on and every check below exercises the eligibility/groups
    # contract itself, not the flag.
    os.environ["CGML_AUTHORING_CORE"] = "1"

    # 1. eligible IR-only Building -----------------------------------
    building_obj, wall_obj, window_obj, _door = build_scene()
    plan = export_dispatch.building_export_plan(building_obj)
    r.check(
        "committed one-wall-one-window Building is IR eligible",
        plan.eligibility.eligible and plan.eligibility.reason == elig.IR_FULL,
        str(plan.eligibility),
    )
    r.check("eligible plan carries groups", plan.groups is not None)
    if plan.groups is not None:
        r.check("groups building_gml_id preserved", plan.groups.building_gml_id == "BLD1")
        r.check("one WallSurface in groups", len(plan.groups.surfaces) == 1)
        wall_group = plan.groups.surfaces[0]
        r.check("wall gml_id preserved", wall_group.gml_id == "WALL1")
        r.check("one Window nested under the wall", [o.gml_id for o in wall_group.openings] == ["WIN_A"])
        r.check(
            "Window host_id points at the wall (record identity, not geometry)",
            wall_group.openings[0].host_id == "WALL1",
        )

    # 3. ineligible: an unsupported feature kind forces legacy fallback ------
    building_obj, wall_obj, window_obj, _door = build_scene()
    stray = _mesh_object(
        "Stray1", WORLD_EXT, parent=building_obj,
        gml_id="STRAY1", cgml3_feature="BuildingInstallation",
    )
    bpy.context.view_layer.update()
    plan_ineligible = export_dispatch.building_export_plan(building_obj)
    r.check(
        "unsupported feature kind under the Building forces ineligible + groups=None",
        not plan_ineligible.eligibility.eligible and plan_ineligible.groups is None,
        str(plan_ineligible.eligibility),
    )
    bpy.data.objects.remove(stray, do_unlink=True)

    # 4. eligible IR Building + legacy Door passthrough merged in ------------
    building_obj, wall_obj, window_obj, door_obj = build_scene(with_door=True)
    plan_with_door = export_dispatch.building_export_plan(building_obj, context=bpy.context)
    r.check(
        "Building with a legacy Door is eligible with Door passthrough noted",
        plan_with_door.eligibility.eligible
        and plan_with_door.eligibility.reason == elig.IR_WITH_DOOR_PASSTHROUGH
        and plan_with_door.eligibility.door_passthrough_features == ("DOOR_A",),
        str(plan_with_door.eligibility),
    )
    if plan_with_door.groups is not None:
        wall_group = plan_with_door.groups.surfaces[0]
        opening_ids = sorted(o.gml_id for o in wall_group.openings)
        r.check(
            "Door passthrough merged alongside the IR Window, not overriding it",
            opening_ids == ["DOOR_A", "WIN_A"],
            str(opening_ids),
        )
        door_opening = next(o for o in wall_group.openings if o.gml_id == "DOOR_A")
        r.check("merged Door keeps its own semantic_type", door_opening.semantic_type == "Door")
        win_opening = next(o for o in wall_group.openings if o.gml_id == "WIN_A")
        r.check(
            "merged Door never overwrites the IR Window's own entry",
            win_opening.semantic_type == "Window" and win_opening.host_id == "WALL1",
        )

    # 5. allowed_ids scoping: a Door belonging to another Building is never pulled in
    building_obj, wall_obj, window_obj, door_obj = build_scene(with_door=True)
    other_door = _mesh_object(
        "DoorOther", DOOR_COORDS, parent=None,
        gml_id="DOOR_OTHER", cgml3_feature="Door", structure_part="opening",
        filling_parent_surface_id="WALL_OTHER",
    )
    bpy.context.view_layer.update()
    scoped = dp.extract_door_passthrough(
        objects=export_dispatch.building_root.building_scope(building_obj) + [other_door],
        context=bpy.context,
        allowed_ids={"DOOR_A"},
    )
    r.check(
        "extract_door_passthrough(allowed_ids=...) excludes a Door from another Building",
        [o.gml_id for o in scoped] == ["DOOR_A"],
        str([o.gml_id for o in scoped]),
    )

    # 6. read-only: no scene mutation from building_export_plan --------------
    building_obj, wall_obj, window_obj, _door = build_scene()
    before_snapshot = h.snapshot_objects()
    export_dispatch.building_export_plan(building_obj)
    after_snapshot = h.snapshot_objects()
    r.check("building_export_plan performs no scene mutation", before_snapshot == after_snapshot)

    r.finish()


if __name__ == "__main__":
    main()
