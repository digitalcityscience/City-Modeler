"""Facade-authoring ticket 04: Edit-exit validation & Sync safety, run
against a real Blender import + a real ``cgml.facade_draw`` (ticket 03)
placement, exercising the automatic mode-change handler
(``ops/edit_exit_validation.py``) through genuine Edit-Mode entry/exit -
never calling the pure diff functions directly.

Checklist covered (docs/agent/facade-authoring/issues/
04-edit-exit-validation-and-sync-safety.md):

1. Opening-only edit (move the freshly-drawn Window's own mesh in Edit
   Mode) -> auto Sync Opening -> Ring: the wall's ring recuts to match the
   moved Window; the Window itself is untouched by the sync.
2. Ring-only edit (move the wall's mesh in Edit Mode) -> auto Sync
   Ring -> Opening: the Window moves back onto the wall's new ring
   position; the wall is untouched by the sync.
3. Neither changed (enter/exit Edit Mode with no edit) -> no-op: geometry
   unchanged, no ambiguous conflict recorded.
4. Both changed (multi-object Edit Mode: select the Window AND its wall
   together, edit both) -> halted as ambiguous, neither side auto-synced;
   the explicit ``cgml.resolve_ambiguous_sync`` operator then resolves it.
5. Editing an unrelated object (a GroundSurface) is an immediate no-op: no
   entry snapshot is ever taken for it (checked via the handler's own
   tracked state, white-box, matching this repo's existing test style).
6. The two explicit wide-scope commands (``cgml.validate_building_openings``
   / ``cgml.validate_scene_openings``) detect a desync introduced entirely
   outside any Edit-Mode session (so the automatic handler never saw it)
   without silently fixing it.
7. Pairing prefers the opening-link id (ADR 0004 D2): the Window drawn by
   ``cgml.facade_draw`` carries ``cgml_opening_link``, and
   ``pairs_for_wall_children`` finds it by that id.

Usage:
    SP=/tmp/cgml-probe-facade04
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_facade04_edit_exit_validation.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # cac_harness

import bmesh
import bpy

import cac_harness  # noqa: E402  (is_clean_face)
from mathutils import Vector

ADDON = "city_modeler"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE_V3 = os.path.join(REPO_ROOT, "test_files", "Building", "Railway_Scene_LoD3_CityGML3_Building_only.gml")
HARNESS_V2 = Path(REPO_ROOT) / "scripts" / "headless" / "xmllint_v2"
HARNESS_V3 = Path(REPO_ROOT) / "scripts" / "headless" / "xmllint_v3"

FAILURES = []


def ok(label):
    print(f"[PASS] {label}")


def check(label, condition, detail=""):
    if condition:
        ok(label)
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def fresh_import(gml):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, f"import failed: {res}"


def find_exterior_face(feature):
    for obj in bpy.data.objects:
        if obj.type != "MESH" or str(obj.get("cgml3_feature") or "") != feature:
            continue
        for face in obj.data.polygons:
            mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id") \
                    and (feature != "WallSurface" or cac_harness.is_clean_face(obj, face)):
                return obj, face.index
    return None, None


def select_face_in_edit_mode(obj, face_index):
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.polygons[face_index].select = True
    bpy.ops.object.mode_set(mode='EDIT')


def draw_window(wall, face_index):
    select_face_in_edit_mode(wall, face_index)
    objs_before = {o.name for o in bpy.data.objects}
    result = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window')
    assert result == {'FINISHED'}, f"draw failed: {result}"
    bpy.ops.object.mode_set(mode='OBJECT')
    new_objs = [o for o in bpy.data.objects if o.name not in objs_before]
    assert len(new_objs) == 1, f"expected exactly one new fill object, got {new_objs}"
    return new_objs[0]


def edit_translate(obj, delta, other_obj=None, other_delta=None):
    """Enter Edit Mode on ``obj`` (plus ``other_obj`` too, if given - a
    multi-object Edit-Mode session), translate every vertex of each mesh by
    its own delta, exit. Drives the real ``bpy.ops.object.mode_set``
    transitions the handler listens for - never calls the handler directly.
    """
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    if other_obj is not None:
        other_obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')

    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.co += delta
    bmesh.update_edit_mesh(obj.data)

    if other_obj is not None:
        bm2 = bmesh.from_edit_mesh(other_obj.data)
        for v in bm2.verts:
            v.co += other_delta
        bmesh.update_edit_mesh(other_obj.data)

    bpy.ops.object.mode_set(mode='OBJECT')


def edit_noop(obj):
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')


def wall_axis(wall, face_index):
    """Horizontal in-plane direction of a vertical wall face."""
    n = (wall.matrix_world.to_3x3() @ wall.data.polygons[face_index].normal).normalized()
    return Vector((0.0, 0.0, 1.0)).cross(n).normalized()


def world_center(obj):
    mesh = obj.data
    mw = obj.matrix_world
    verts = [mw @ v.co for v in mesh.vertices]
    total = Vector((0.0, 0.0, 0.0))
    for v in verts:
        total += v
    return total / len(verts)


def ring_world_center(wall, opening):
    from city_modeler.shared.ring_opening_sync import pairs_for_wall_children
    pair = next((p for p in pairs_for_wall_children(wall) if p.opening is opening), None)
    assert pair is not None, "expected a ring/opening pair for this wall/opening"
    face = wall.data.polygons[pair.ring_face_index]
    mw = wall.matrix_world
    verts = [mw @ wall.data.vertices[vi].co for vi in face.vertices]
    total = Vector((0.0, 0.0, 0.0))
    for v in verts:
        total += v
    return total / len(verts)


def export_result(path, version):
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    except Exception as e:
        return False, str(e)
    return (res == {'FINISHED'}), ("" if res == {'FINISHED'} else f"result={res}")


def run_xmllint(label, out_path, harness_dir=None, wrapper_name=None):
    # cac_harness adds the local schema mirror: libxml2 >= 2.14 cannot fetch the http imports itself
    version = "3.0" if "3" in label.split()[0] else "2.0"
    try:
        lint = cac_harness.xmllint(out_path, version)
    except AssertionError as e:
        check(f"xmllint {label} exit 0", False, str(e))
        return
    check(f"xmllint {label} exit 0", lint["exit"] == 0, f"exit {lint['exit']}, {lint['errors']} error(s)")


def main():
    from city_modeler.ops import edit_exit_validation as eev
    from city_modeler.shared.ring_opening_sync import (
        OPENING_LINK_KEY,
        check_ring_opening_sync,
        pairs_for_wall_children,
    )

    # --- Scenario 1: opening-only edit -> auto Sync Opening -> Ring -------
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    wall, face_index = find_exterior_face("WallSurface")
    check("fixture has an exploded WallSurface with an exterior face", wall is not None)
    fill = draw_window(wall, face_index)
    check("freshly-drawn Window carries an opening link", bool(fill.get(OPENING_LINK_KEY)))

    ring_before = ring_world_center(wall, fill)
    fill_before = world_center(fill)
    delta = wall_axis(wall, face_index) * 0.3  # stays in the wall plane
    edit_translate(fill, delta)
    fill_after = world_center(fill)
    check("opening-only edit: the Window itself moved by the edit",
          (fill_after - (fill_before + delta)).length < 1e-5)  # float32 vertices

    ring_after = ring_world_center(wall, fill)
    check("opening-only edit: the wall's ring auto-recut to follow the Window",
          (ring_after - (ring_before + delta)).length < 1e-4,
          f"ring_before={ring_before} ring_after={ring_after} expected_delta={delta}")
    pair = next(p for p in pairs_for_wall_children(wall) if p.opening is fill)
    check("opening-only edit: pair is back in sync after the auto-sync", pair.in_sync, f"distance={pair.distance}")
    check("opening-only edit: no ambiguous conflict recorded", len(eev.PENDING_AMBIGUOUS) == 0)

    out_dir = "/tmp/facade04-out"
    os.makedirs(out_dir, exist_ok=True)
    for version, harness, wrapper in (
        ("3.0", HARNESS_V3, "wrapper_v3.xsd"),
        ("2.0", HARNESS_V2, "wrapper_v2.xsd"),
    ):
        out_path = os.path.join(out_dir, f"facade04_autosync_v{version.replace('.', '')}.gml")
        succeeded, err = export_result(out_path, version)
        check(f"export CityGML {version} succeeds after an auto-sync", succeeded, err)
        if succeeded:
            run_xmllint(f"v{version} auto-sync", Path(out_path), harness, wrapper)

    # --- Scenario 2: ring-only edit -> auto Sync Ring -> Opening ----------
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    wall, face_index = find_exterior_face("WallSurface")
    fill = draw_window(wall, face_index)

    ring_before = ring_world_center(wall, fill)
    fill_before = world_center(fill)
    delta = Vector((0.0, 0.0, -0.25))
    edit_translate(wall, delta)
    ring_after = ring_world_center(wall, fill)
    check("ring-only edit: the wall's ring moved by the edit",
          (ring_after - (ring_before + delta)).length < 1e-4,
          f"ring_before={ring_before} ring_after={ring_after}")

    fill_after = world_center(fill)
    check("ring-only edit: the Window auto-moved to follow the wall's new ring",
          (fill_after - (fill_before + delta)).length < 1e-4,
          f"fill_before={fill_before} fill_after={fill_after} expected_delta={delta}")
    pair = next(p for p in pairs_for_wall_children(wall) if p.opening is fill)
    check("ring-only edit: pair is back in sync after the auto-sync", pair.in_sync, f"distance={pair.distance}")
    check("ring-only edit: no ambiguous conflict recorded", len(eev.PENDING_AMBIGUOUS) == 0)

    # --- Scenario 3: neither changed -> no-op ------------------------------
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    wall, face_index = find_exterior_face("WallSurface")
    fill = draw_window(wall, face_index)
    fill_before = world_center(fill)
    ring_before = ring_world_center(wall, fill)
    edit_noop(fill)
    check("no-relevant-change edit: Window geometry unchanged",
          (world_center(fill) - fill_before).length < 1e-9)
    check("no-relevant-change edit: ring geometry unchanged",
          (ring_world_center(wall, fill) - ring_before).length < 1e-9)
    check("no-relevant-change edit: no ambiguous conflict recorded", len(eev.PENDING_AMBIGUOUS) == 0)

    # --- Scenario 4: both changed (multi-object Edit Mode) -> ambiguous ---
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    wall, face_index = find_exterior_face("WallSurface")
    fill = draw_window(wall, face_index)

    fill_before = world_center(fill)
    ring_before = ring_world_center(wall, fill)
    opening_delta = wall_axis(wall, face_index) * 0.2
    ring_delta = Vector((0.0, 0.2, 0.0))
    edit_translate(fill, opening_delta, other_obj=wall, other_delta=ring_delta)

    check("both-changed edit: exactly one ambiguous conflict recorded",
          len(eev.PENDING_AMBIGUOUS) == 1, f"pending={eev.PENDING_AMBIGUOUS}")
    if eev.PENDING_AMBIGUOUS:
        conflict = eev.PENDING_AMBIGUOUS[0]
        check("both-changed edit: conflict names the right Window", conflict.opening_name == fill.name)
        check("both-changed edit: conflict names the right wall", conflict.wall_name == wall.name)

    pair = next(p for p in pairs_for_wall_children(wall) if p.opening is fill)
    check("both-changed edit: halted, not guessed - pair is still desynced", not pair.in_sync)
    check("both-changed edit: the Window kept its own edit (not silently reverted/synced)",
          (world_center(fill) - (fill_before + opening_delta)).length < 1e-5)
    check("both-changed edit: the wall's ring kept its own edit (not silently reverted/synced)",
          (ring_world_center(wall, fill) - (ring_before + ring_delta)).length < 1e-4)

    resolve_result = bpy.ops.cgml.resolve_ambiguous_sync(
        'EXEC_DEFAULT', wall_name=wall.name, opening_name=fill.name, authoritative='OPENING',
    )
    check("resolve_ambiguous_sync (Opening authoritative) finishes", resolve_result == {'FINISHED'})
    pair = next(p for p in pairs_for_wall_children(wall) if p.opening is fill)
    check("both-changed edit: resolved pair is back in sync", pair.in_sync, f"distance={pair.distance}")
    check("both-changed edit: resolving clears the pending conflict", len(eev.PENDING_AMBIGUOUS) == 0)

    # --- Scenario 5: unrelated-object edit is an immediate no-op ----------
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    ground = next(
        (o for o in bpy.data.objects if o.type == "MESH" and str(o.get("cgml3_feature") or "") == "GroundSurface"),
        None,
    )
    check("fixture has a GroundSurface object", ground is not None)
    if ground is not None:
        bpy.context.view_layer.objects.active = ground
        for o in bpy.data.objects:
            o.select_set(False)
        ground.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        check("GroundSurface edit-entry never snapshotted (out of scope, no full scan)",
              ground.name not in eev._entry_snapshots)
        bpy.ops.object.mode_set(mode='OBJECT')
        check("GroundSurface edit-exit stays a no-op", len(eev.PENDING_AMBIGUOUS) == 0)

    # --- Scenario 6: explicit wide-scope validate commands -----------------
    fresh_import(FIXTURE_V3)
    eev.reset_state()
    wall, face_index = find_exterior_face("WallSurface")
    fill = draw_window(wall, face_index)
    building = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")

    # Desync introduced entirely in Object Mode - the handler's Edit-Mode
    # hook never fires for this, exactly the "drift from an earlier
    # unvalidated session" case the explicit commands exist for.
    fill.matrix_world.translation += Vector((0.5, 0.0, 0.0))
    bpy.context.view_layer.update()

    desynced_before = check_ring_opening_sync(building)
    check("object-mode drift is a real desync the automatic handler never saw",
          len(desynced_before) >= 1, f"desynced={desynced_before}")

    bpy.context.view_layer.objects.active = building
    result = bpy.ops.cgml.validate_building_openings('EXEC_DEFAULT')
    check("cgml.validate_building_openings runs cleanly", result == {'FINISHED'})
    desynced_after_validate = check_ring_opening_sync(building)
    check("cgml.validate_building_openings only reports, never auto-fixes",
          len(desynced_after_validate) == len(desynced_before))

    result = bpy.ops.cgml.validate_scene_openings('EXEC_DEFAULT')
    check("cgml.validate_scene_openings runs cleanly", result == {'FINISHED'})
    desynced_after_scene_validate = check_ring_opening_sync(building)
    check("cgml.validate_scene_openings only reports, never auto-fixes",
          len(desynced_after_scene_validate) == len(desynced_before))

    if FAILURES:
        print(f"\n[FAIL] facade ticket 04 NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] facade ticket 04 edit-exit validation & sync safety satisfied")


main()
