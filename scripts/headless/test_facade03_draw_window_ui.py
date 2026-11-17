"""Facade-authoring ticket 03: Edit-Mode "Draw Window" UI (thin adapter).

Drives the real select-face -> menu -> place path end to end through
``cgml.facade_draw`` (``ops/facade_authoring.py``), not through the headless
placement kernel directly (that's ticket 01's test) or the resolver alone
(ticket 02's test):

  1. Select a face on a real imported, exploded LoD3 WallSurface, enter Edit
     Mode, and invoke the operator exactly as the "CityGML Facade > Draw
     Window" menu item does (``semantic_type='Window'``) - the single-click
     default-size path (no explicit position/size).
  2. The wall stays in Edit Mode across the whole call. This is the one path
     ticket 01/02's tests never exercised (their kernel/resolver calls always
     ran with the wall in Object Mode) and it exposed a real bug: the same
     "Blender refuses a plain bmesh write while the mesh is in Edit Mode"
     class of bug ticket 02 found in ``split_into_semantic_surfaces``, this
     time in the placement kernel's own fill-extraction step
     (``shared/facade_placement.py``'s ``_place_interior_ring_opening``,
     fixed by forcing Object Mode for the whole placement).
  3. The click-drag "exact rectangle" arithmetic (``position_uv``/``width``/
     ``height`` computed from two mouse corners) is proven by calling the
     operator with those already-resolved numbers directly - exactly what
     the modal's LEFTMOUSE-release handler and Blender's F9 redo panel both
     do; a real mouse drag needs a live 3D viewport region background mode
     doesn't have.
  4. A merged (un-exploded) building face transparently splits first (D6,
     ticket 02) and still places correctly through the same operator call.
  5. A non-WallSurface face (RoofSurface) is refused with CANCELLED, not a
     crash or a silent no-op.
  6. Export to CityGML 2.0 and 3.0 both stay schema-valid (xmllint) after an
     Edit-Mode-authored placement.

Not covered here: "placement is a single undo step". Verified instead by
code inspection (see the comment above ``CGML_OT_facade_draw.modal()``'s
``return self.execute(context)`` in ``ops/facade_authoring.py``) rather than
a headless assertion, because Blender's Edit-Mode undo is itself unreliable
in --background mode independent of anything this ticket touches: a control
probe (fresh mesh, Edit Mode, ``bpy.ops.mesh.extrude_region_move``, then
``bpy.ops.ed.undo()``) showed the built-in extrude operator's own edit
silently not applying and/or not reverting in the same run, so a failed
undo-based assertion here would not be trustworthy signal either way.

Usage:
    SP=/tmp/cgml-probe-facade03
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_facade03_draw_window_ui.py -- "$SP/out"
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # cac_harness

import bpy

import cac_harness  # noqa: E402

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_V3 = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def ok(label):
    print(f"[PASS] {label}")


def check(label, condition, detail=""):
    if condition:
        ok(label)
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def fresh_import(gml, use_semantic_objects):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=str(gml))
    assert res == {'FINISHED'}, f"import failed: {res}"


def find_exterior_face(feature, clean=False):
    """A single exploded surface object of ``feature`` + a non-hole face on
    it, told apart from a ``feature`` with none available."""
    found = list_exterior_faces(feature, clean)
    return found[0] if found else (None, None)


def list_exterior_faces(feature, clean=False):
    """Every exploded surface object of ``feature`` + one non-hole face on
    it - so tests needing *distinct* objects (e.g. proving a call ignores
    the current selection) don't accidentally land on the same wall twice."""
    found = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or str(obj.get("cgml3_feature") or "") != feature:
            continue
        for face in obj.data.polygons:
            mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id") \
                    and (not clean or cac_harness.is_clean_face(obj, face)):
                found.append((obj, face.index))
                break
    return found


def find_merged_building_wall_face():
    """A merged Building/BuildingPart object + a face whose material says
    WallSurface (use_semantic_objects OFF: nothing was exploded)."""
    for obj in bpy.data.objects:
        if obj.type != "MESH" or str(obj.get("cgml3_feature") or "") not in ("Building", "BuildingPart"):
            continue
        for face in obj.data.polygons:
            mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
            surface_type = str(mat.get("SurfaceTyp") or mat.get("surface_type") or "").strip().lower() if mat else ""
            if surface_type == "wallsurface":
                return obj, face.index
    return None, None


def select_face_in_edit_mode(obj, face_index):
    """Select exactly one face and leave ``obj`` in Edit Mode - matches
    ticket 09's headless-reliable pattern (toggle to Object Mode to set the
    polygon selection flag directly, then re-enter Edit Mode)."""
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.polygons[face_index].select = True
    bpy.ops.object.mode_set(mode='EDIT')


def fill_rect_edge_lengths(fill):
    mesh = fill.data
    poly = mesh.polygons[0]
    mw = fill.matrix_world
    verts = [mw @ mesh.vertices[vi].co for vi in poly.vertices]
    n = len(verts)
    return [(verts[(i + 1) % n] - verts[i]).length for i in range(n)]


def check_rect_size(label, fill, width, height, tol=1e-4):
    edges = sorted(fill_rect_edge_lengths(fill))
    expected = sorted([width, width, height, height])
    close = len(edges) == 4 and all(abs(a - b) < tol for a, b in zip(edges, expected))
    check(label, close, f"edges={edges} expected={expected}")


def export_result(path, version):
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    except Exception as e:
        return False, str(e)
    return (res == {'FINISHED'}), ("" if res == {'FINISHED'} else f"result={res}")


def run_xmllint(label, out_path, version):
    # cac_harness adds the local schema mirror: libxml2 >= 2.14 cannot fetch the http imports itself
    try:
        lint = cac_harness.xmllint(out_path, version)
    except AssertionError as e:
        check(f"xmllint {label} exit 0", False, str(e))
        return
    print(f"[facade03][{label}] xmllint {lint}")
    check(f"xmllint {label} exit 0", lint["exit"] == 0, f"exit {lint['exit']}, {lint['errors']} error(s)")

def main():
    a = args()
    out_dir = a[0] if a else "/tmp/facade03-out"
    os.makedirs(out_dir, exist_ok=True)

    from city_modeler.shared.facade_placement import OPENING_LINK_KEY
    from city_modeler.ops.facade_authoring import (
        DEFAULT_WINDOW_DEPTH,
        DEFAULT_WINDOW_WIDTH,
        DEFAULT_WINDOW_HEIGHT,
    )

    # --- 1. Single click (no drag): default-sized Window, wall stays in
    #        Edit Mode across the whole call (the bug this ticket found). ---
    fresh_import(FIXTURE_V3, use_semantic_objects=True)
    wall, face_index = find_exterior_face("WallSurface", clean=True)
    check("fixture has an exploded WallSurface with an exterior face", wall is not None)
    if wall is not None:
        host_plane_point = wall.matrix_world @ wall.data.vertices[wall.data.polygons[face_index].vertices[0]].co
        host_normal = (wall.matrix_world.to_3x3() @ wall.data.polygons[face_index].normal).normalized()
        select_face_in_edit_mode(wall, face_index)
        check("wall is in Edit Mode before drawing", wall.mode == 'EDIT')

        objs_before = {o.name for o in bpy.data.objects}
        result = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window')
        check("operator finishes on an already-exploded WallSurface in Edit Mode",
              result == {'FINISHED'}, f"result={result}")
        check("wall is still in Edit Mode after drawing (no crash, mode restored)",
              wall.mode == 'EDIT', f"mode={wall.mode}")

        new_objs = [o for o in bpy.data.objects if o.name not in objs_before]
        check("exactly one new object (the fill) was created", len(new_objs) == 1, f"new={new_objs}")
        fill = new_objs[0] if new_objs else None
        if fill is not None:
            check("fill is parented under the wall", fill.parent is wall)
            check("fill carries an opening link", bool(fill.get(OPENING_LINK_KEY)))
            check("fill carries opening structure metadata",
                  str(fill.get("structure_part")) == "opening")
            check("fill provenance is manual_draw", str(fill.get("cgml_source")) == "manual_draw")
            check_rect_size("single-click default size matches DEFAULT_WINDOW_WIDTH/HEIGHT",
                             fill, DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
            signed_depths = [
                ((fill.matrix_world @ vertex.co) - host_plane_point).dot(host_normal)
                for vertex in fill.data.vertices
            ]
            check("single-click Draw Window uses the 0.04 m centred depth default",
                  abs(max(signed_depths) - 0.02) < 1e-5
                  and abs(min(signed_depths) + 0.02) < 1e-5
                  and abs(max(signed_depths) - min(signed_depths) - DEFAULT_WINDOW_DEPTH) < 1e-5,
                  f"signed range=({min(signed_depths):.6f}, {max(signed_depths):.6f})")

        # Leave Edit Mode so the next selection starts clean.
        bpy.ops.object.mode_set(mode='OBJECT')

    # --- 2. Click-drag: exact rectangle via explicit position/size (what the
    #        modal's LEFTMOUSE-release and the F9 redo panel both compute). --
    fresh_import(FIXTURE_V3, use_semantic_objects=True)  # each step on an untouched wall
    drag_wall, drag_wall_face = find_exterior_face("WallSurface", clean=True)
    if drag_wall is not None:
        select_face_in_edit_mode(drag_wall, drag_wall_face)
        objs_before = {o.name for o in bpy.data.objects}
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type='Window',
            position_uv=(0.3, 0.6), width=2.0, height=0.8,
        )
        check("operator finishes with explicit drag-computed size", result == {'FINISHED'}, f"result={result}")
        new_objs = [o for o in bpy.data.objects if o.name not in objs_before]
        check("exactly one new fill object from the drag call", len(new_objs) == 1, f"new={new_objs}")
        if new_objs:
            check_rect_size("click-drag exact rectangle matches the requested width/height",
                             new_objs[0], 2.0, 0.8)
        bpy.ops.object.mode_set(mode='OBJECT')

    # --- 3. Redo-panel-shaped call: explicit object_name/face_index target a
    #        DIFFERENT wall than whatever face happens to be selected - proves
    #        execute() trusts the explicit properties over live selection
    #        (exactly what Blender's F9 redo re-run and a scripted/DL caller
    #        both rely on), not just that some object is in Edit Mode. -------
    fresh_import(FIXTURE_V3, use_semantic_objects=True)
    redo_wall, redo_wall_face = find_exterior_face("WallSurface", clean=True)
    walls = [w for w in list_exterior_faces("WallSurface") if w[0] is not redo_wall]
    check("fixture has at least two distinct WallSurface objects", redo_wall is not None and walls,
          f"count={len(walls) + (redo_wall is not None)}")
    if redo_wall is not None and walls:
        selected_wall, selected_face = walls[0]
        # Edit-Mode selection points at `selected_wall` - poll() only needs
        # *some* valid Edit-Mode mesh; it must not affect which wall is used.
        select_face_in_edit_mode(selected_wall, selected_face)
        objs_before = {o.name for o in bpy.data.objects}
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type='Window',
            object_name=redo_wall.name, face_index=redo_wall_face,
            position_uv=(0.5, 0.5), width=0.9, height=0.9,
        )
        check("redo-shaped call (explicit object_name/face_index) finishes",
              result == {'FINISHED'}, f"result={result}")
        new_objs = [o for o in bpy.data.objects if o.name not in objs_before]
        check("exactly one new fill object from the redo-shaped call", len(new_objs) == 1, f"new={new_objs}")
        if new_objs:
            check("the redo-shaped call placed on the explicit target wall, not the selected one",
                  new_objs[0].parent is redo_wall,
                  f"parent={new_objs[0].parent} expected={redo_wall.name} selected={selected_wall.name}")
        bpy.ops.object.mode_set(mode='OBJECT')

    # --- 4. Wrong surface type: RoofSurface is refused, not placed. -------
    roof, roof_face = find_exterior_face("RoofSurface")
    check("fixture has an exploded RoofSurface with an exterior face", roof is not None)
    if roof is not None:
        select_face_in_edit_mode(roof, roof_face)
        objs_before = {o.name for o in bpy.data.objects}
        result = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window')
        check("drawing a Window on a RoofSurface face is refused",
              result == {'CANCELLED'}, f"result={result}")
        check("nothing was created for the refused RoofSurface draw",
              {o.name for o in bpy.data.objects} == objs_before)
        bpy.ops.object.mode_set(mode='OBJECT')

    # --- 5. Merged (un-exploded) building: the menu item still works via
    #        the transparent one-click split (D6). ------------------------
    fresh_import(FIXTURE_V3, use_semantic_objects=False)
    building, building_face = find_merged_building_wall_face()
    check("fixture has a merged Building with a WallSurface-tagged face", building is not None)
    if building is not None:
        select_face_in_edit_mode(building, building_face)
        objs_before = {o.name for o in bpy.data.objects}
        result = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window')
        check("operator finishes on a merged building face (transparent split)",
              result == {'FINISHED'}, f"result={result}")
        new_objs = [o for o in bpy.data.objects if o.name not in objs_before]
        check("split produced at least a new WallSurface child and a fill",
              len(new_objs) >= 2, f"new={new_objs}")
        fill = next((o for o in new_objs if o.get("structure_part") == "opening"), None)
        check("the fill landed on a real split WallSurface, not the merged building",
              fill is not None and fill.parent is not None and fill.parent is not building,
              f"fill={fill} parent={fill.parent if fill else None}")

    # --- 6. Export + schema validation after an Edit-Mode-authored
    #        placement. ------------------------------------------------
    for version in ("3.0", "2.0"):
        out_path = os.path.join(out_dir, f"facade03_authored_v{version.replace('.', '')}.gml")
        succeeded, err = export_result(out_path, version)
        check(f"export CityGML {version} succeeds after Edit-Mode Draw Window", succeeded, err)
        if succeeded:
            run_xmllint(f"v{version}", Path(out_path), version)

    if FAILURES:
        print(f"\n[FAIL] facade ticket 03 NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] facade ticket 03 Draw Window UI satisfied")


main()
