"""Ticket 09 checklist, run against the real Blender import/export pipeline.

Imports the Railway LoD3 CityGML3 fixture with use_semantic_objects ON and
exercises every item in docs/agent/semantic-hierarchy/issues/
09-opening-ring-desync-guard.md:

- an untouched import exports cleanly (no false positive)
- moving a Window out of alignment halts export and names both the Window
  and its parent WallSurface
- a move smaller than the documented tolerance does NOT halt export
- 'Sync Opening -> Ring' recuts the wall (Window untouched) and export
  succeeds again
- 'Sync Ring -> Opening' moves the Window back onto the (untouched) wall
  and export succeeds again
- moving a Door never triggers the check (Doors own no wall ring)
- Openings_Cutter still works on an exploded WallSurface object

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for
why a private staged scripts dir + a cp313 pyproj/lxml shim are required):

    SP=/tmp/cgml-probe-09
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket09_ring_opening_sync.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML3_Building_only.gml /tmp/out
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy
from mathutils import Vector

ADDON = "city_modeler"

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def ok(label):
    print(f"[PASS] {label}")


def fail(label, detail=""):
    msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
    print(msg)
    FAILURES.append(msg)


def fresh_import(gml):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, f"import failed: {res}"


def building_obj():
    return next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")


def export_result(path):
    """Returns (succeeded, error_text)."""
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=path)
    except Exception as e:
        return False, str(e)
    if res == {'FINISHED'}:
        return True, ""
    return False, f"result={res}"


def a_window_and_its_wall():
    building = building_obj()
    from city_modeler.shared.ring_opening_sync import find_ring_opening_pairs
    pairs = find_ring_opening_pairs(building)
    assert pairs, "expected at least one wall/Window ring pair in the fixture"
    # Prefer a wall with exactly one Window pair so moving it can't
    # incidentally re-pair with a sibling window on the same wall.
    from collections import Counter
    wall_counts = Counter(id(p.wall) for p in pairs)
    single = next((p for p in pairs if wall_counts[id(p.wall)] == 1), pairs[0])
    return single.opening, single.wall


def a_door():
    return next(o for o in bpy.data.objects if o.get("cgml3_feature") == "DoorSurface")


def a_window_with_siblings():
    """A (window, wall, sibling_windows) triple where ``wall`` has more
    than one Window child - to prove the nearest-distance pairing isolates
    the one that actually moved instead of cross-pairing with a sibling
    (docs/agent/semantic-hierarchy README.md section 6, open question 1:
    the pairing is provably not 1:1 across a whole Building)."""
    building = building_obj()
    from city_modeler.shared.ring_opening_sync import find_ring_opening_pairs
    from collections import Counter
    pairs = find_ring_opening_pairs(building)
    wall_counts = Counter(id(p.wall) for p in pairs)
    multi = [p for p in pairs if wall_counts[id(p.wall)] >= 2]
    assert multi, "expected the fixture to contain a wall with more than one Window"
    target = multi[0]
    siblings = [p.opening for p in pairs if id(p.wall) == id(target.wall) and id(p.opening) != id(target.opening)]
    return target.opening, target.wall, siblings


def test_untouched_export_is_clean(gml, out_dir):
    fresh_import(gml)
    succeeded, err = export_result(os.path.join(out_dir, "t1_untouched.gml"))
    if succeeded:
        ok("untouched import exports without triggering the desync check")
    else:
        fail("untouched import must export cleanly", err)


def test_moved_window_halts_export(gml, out_dir):
    fresh_import(gml)
    window, wall = a_window_and_its_wall()
    window.location.x += 0.5
    bpy.context.view_layer.update()
    succeeded, err = export_result(os.path.join(out_dir, "t2_moved.gml"))
    if succeeded:
        fail("moving a Window 0.5 m must halt export", "export succeeded instead")
        return
    if window.name in err and wall.name in err:
        ok("moved Window halts export and names both the Window and its wall")
    else:
        fail(
            "export error must name both the Window and its wall",
            f"window={window.name!r} wall={wall.name!r} err={err!r}",
        )


def test_tiny_move_is_within_tolerance(gml, out_dir):
    fresh_import(gml)
    window, _wall = a_window_and_its_wall()
    # Tolerance is documented as 1 cm (RING_OPENING_SYNC_TOLERANCE_M); 1 mm
    # must be absorbed as float/round-trip noise, not treated as a real edit.
    window.location.x += 0.001
    bpy.context.view_layer.update()
    succeeded, err = export_result(os.path.join(out_dir, "t3_tiny_move.gml"))
    if succeeded:
        ok("a sub-tolerance move (1 mm) does not halt export")
    else:
        fail("a sub-tolerance move must not halt export", err)


def test_sync_opening_to_ring(gml, out_dir):
    fresh_import(gml)
    window, wall = a_window_and_its_wall()
    original_loc = window.location.copy()
    window.location.x += 0.5
    bpy.context.view_layer.update()

    for o in bpy.data.objects:
        o.select_set(False)
    window.select_set(True)
    bpy.context.view_layer.objects.active = window
    res = bpy.ops.cgml3.sync_opening_to_ring()
    if res != {'FINISHED'}:
        fail("Sync Opening -> Ring must finish", str(res))
        return

    if window.location != Vector((original_loc.x + 0.5, original_loc.y, original_loc.z)):
        fail("Sync Opening -> Ring must leave the Window's own transform untouched")
        return

    succeeded, err = export_result(os.path.join(out_dir, "t4_opening_to_ring.gml"))
    if succeeded:
        ok("Sync Opening -> Ring recuts the wall (Window untouched) and export succeeds")
    else:
        fail("export must succeed after Sync Opening -> Ring", err)


def test_sync_ring_to_opening(gml, out_dir):
    fresh_import(gml)
    window, wall = a_window_and_its_wall()
    original_loc = window.location.copy()
    window.location.x += 0.5
    bpy.context.view_layer.update()

    for o in bpy.data.objects:
        o.select_set(False)
    window.select_set(True)
    bpy.context.view_layer.objects.active = window
    res = bpy.ops.cgml3.sync_ring_to_opening()
    if res != {'FINISHED'}:
        fail("Sync Ring -> Opening must finish", str(res))
        return

    moved_back = (window.location - original_loc).length < 0.02
    if not moved_back:
        fail(
            "Sync Ring -> Opening must move the Window back onto the (untouched) wall",
            f"location={tuple(window.location)} original={tuple(original_loc)}",
        )
        return

    succeeded, err = export_result(os.path.join(out_dir, "t5_ring_to_opening.gml"))
    if succeeded:
        ok("Sync Ring -> Opening moves the Window back onto the wall and export succeeds")
    else:
        fail("export must succeed after Sync Ring -> Opening", err)


def test_multi_window_wall_isolates_moved_window(gml, out_dir):
    fresh_import(gml)
    window, wall, siblings = a_window_with_siblings()
    window.location.x += 0.5
    bpy.context.view_layer.update()
    succeeded, err = export_result(os.path.join(out_dir, "t8_multi_window.gml"))
    if succeeded:
        fail("moving one Window on a multi-Window wall must still halt export")
        return
    if window.name not in err or wall.name not in err:
        fail(
            "error must name the moved Window and its true wall",
            f"window={window.name!r} wall={wall.name!r} err={err!r}",
        )
        return
    wrongly_named = [s.name for s in siblings if s.name in err]
    if wrongly_named:
        fail(
            "a sibling Window on the same wall must not be misreported as desynced",
            f"wrongly named: {wrongly_named}",
        )
        return
    ok(
        f"multi-Window wall ({len(siblings) + 1} windows): moving one Window "
        "isolates only that Window - siblings never cross-paired or flagged"
    )


def test_door_never_flagged(gml, out_dir):
    fresh_import(gml)
    door = a_door()
    door.location.x += 0.5
    bpy.context.view_layer.update()
    succeeded, err = export_result(os.path.join(out_dir, "t6_moved_door.gml"))
    if succeeded:
        ok("moving a Door never triggers the desync check (Doors own no wall ring)")
    else:
        fail("moving a Door must never halt export", err)


def test_openings_cutter_still_works(gml, out_dir):
    fresh_import(gml)
    wall = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "WallSurface")

    # Find a face on the wall's own exterior polygon (not one of its
    # gml:interior-ring hole faces) to draw a new opening on.
    ext_face_index = None
    for face in wall.data.polygons:
        mat = wall.data.materials[face.material_index] if 0 <= face.material_index < len(wall.data.materials) else None
        if mat is not None and not mat.get("Interior"):
            ext_face_index = face.index
            break
    assert ext_face_index is not None, "wall must have at least one non-hole face"

    bpy.context.view_layer.objects.active = wall
    for o in bpy.data.objects:
        o.select_set(False)
    wall.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    wall.data.polygons[ext_face_index].select = True
    bpy.ops.object.mode_set(mode='EDIT')

    from city_modeler.Openings_Cutter.Openings_Cutter import (
        get_selected_face_info,
        create_joined_surfaces,
    )

    world_center, world_normal, source_material, uv_json, uv_map_name, face_index = get_selected_face_info(
        bpy.context, wall
    )
    if world_center is None:
        fail("Openings_Cutter: get_selected_face_info found no selected face")
        bpy.ops.object.mode_set(mode='OBJECT')
        return

    n = world_normal.normalized()
    ref = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    u = n.cross(ref).normalized()
    v = n.cross(u).normalized()
    size = 0.2
    corners = [
        world_center + u * size + v * size,
        world_center - u * size + v * size,
        world_center - u * size - v * size,
        world_center + u * size - v * size,
    ]

    bpy.ops.object.mode_set(mode='OBJECT')
    faces_before = len(wall.data.polygons)
    try:
        create_joined_surfaces(
            obj=wall,
            world_corners=corners,
            opening_type="Window",
            plane_normal=world_normal,
            source_material=source_material,
            uv_mapping_json=uv_json,
            source_uv_map_name=uv_map_name,
            source_face_index=face_index,
            generate_interior=True,
        )
    except Exception as e:
        fail("Openings_Cutter.create_joined_surfaces raised on an exploded WallSurface", str(e))
        return

    faces_after = len(wall.data.polygons)
    if faces_after <= faces_before:
        fail("Openings_Cutter did not add any faces to the exploded WallSurface")
        return
    ok(f"Openings_Cutter.create_joined_surfaces adds faces to an exploded WallSurface ({faces_before} -> {faces_after})")

    succeeded, err = export_result(os.path.join(out_dir, "t7_openings_cutter.gml"))
    if succeeded:
        ok("export still succeeds after Openings_Cutter edits an exploded WallSurface")
    else:
        fail("export must still succeed after an Openings_Cutter edit", err)


def main():
    a = args()
    gml, out_dir = a[0], a[1]
    os.makedirs(out_dir, exist_ok=True)

    print("\n[test] --- untouched export ---")
    test_untouched_export_is_clean(gml, out_dir)

    print("\n[test] --- moved Window halts export ---")
    test_moved_window_halts_export(gml, out_dir)

    print("\n[test] --- sub-tolerance move is not flagged ---")
    test_tiny_move_is_within_tolerance(gml, out_dir)

    print("\n[test] --- Sync Opening -> Ring ---")
    test_sync_opening_to_ring(gml, out_dir)

    print("\n[test] --- Sync Ring -> Opening ---")
    test_sync_ring_to_opening(gml, out_dir)

    print("\n[test] --- multi-Window wall isolates the moved Window ---")
    test_multi_window_wall_isolates_moved_window(gml, out_dir)

    print("\n[test] --- moved Door is never flagged ---")
    test_door_never_flagged(gml, out_dir)

    print("\n[test] --- Openings_Cutter on an exploded WallSurface ---")
    test_openings_cutter_still_works(gml, out_dir)

    if FAILURES:
        print(f"\n[FAIL] ticket 09 checklist NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 09 checklist satisfied")


main()
