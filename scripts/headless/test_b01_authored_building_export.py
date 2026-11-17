"""B01: a building authored from scratch keeps its hierarchy on export.

Plain cube -> ModelTyper auto assign (Building) -> split into surfaces ->
Draw Window on a wall -> export 3.0 and 2.0. Before the fix ModelTyper wrote
cgml3_feature="BUILDING", the exporter did not see the parent as a Building
and wrote every surface and the window as its own con:OtherConstruction.

Usage (isolated profile, see tickets/README.md):
    SP=/tmp/cgml-b01
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_b01_authored_building_export.py -- "$SP/out"
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bmesh
import bpy

ADDON = "city_modeler"
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"[PASS] {label}")
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def select_only(obj):
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def select_face_in_edit_mode(obj, face_index):
    select_only(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.polygons[face_index].select = True
    bpy.ops.object.mode_set(mode='EDIT')


def build_cube_building():
    mesh = bpy.data.meshes.new("b01")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= 10.0
        v.co.y *= 8.0
        v.co.z = (v.co.z + 0.5) * 9.0
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("b01_building", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def south_wall_face(obj):
    for poly in obj.data.polygons:
        if abs(poly.normal.z) < 0.1 and poly.normal.y < -0.5:
            return poly.index
    return None


def xsd_valid(version, path):
    """The add-on's own validators (xmllint is not on every machine). They
    return ok=True with a note when no schema could be loaded, so a note
    counts as a failure: a skipped check must not pass."""
    if version == "3.0":
        from city_modeler.ops.validate import validate_citygml3_xsd as validate
    else:
        from city_modeler.ops.validate_citygml2 import validate_citygml2_xsd as validate
    ok, msg = validate(str(path))
    skipped = any(word in msg for word in ("Hinweis", "Warnung", "übersprungen", "skipped"))
    return ok and not skipped, msg


def check_xsd(version, out_path):
    valid, msg = xsd_valid(version, out_path)
    check(f"{version}: XSD valid", valid, msg[:400])
    # Negative control: the same check must reject a broken copy.
    bad = out_path.with_name(out_path.stem + "_broken.gml")
    text = out_path.read_text(encoding="utf-8")
    bad.write_text(re.sub(r"(<bldg:Building [^>]*>)", r"\1<bldg:notAnElement/>", text, count=1), encoding="utf-8")
    check(f"{version}: XSD check rejects a broken copy (negative control)", not xsd_valid(version, bad)[0])


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[0] if argv else "/tmp/b01-out"
    os.makedirs(out_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    building = build_cube_building()
    select_only(building)
    bpy.context.scene.cgml3_modeltyper_props.assignment_mode = 'SELECTION'
    check("auto assign finishes", bpy.ops.cgml3_modeltyper.auto_assign() == {'FINISHED'})
    check("cgml3_feature is the CityGML name 'Building'", building.get("cgml3_feature") == "Building",
          f"got {building.get('cgml3_feature')!r}")

    face = south_wall_face(building)
    select_face_in_edit_mode(building, face)
    check("split finishes", bpy.ops.cgml.split_surfaces() == {'FINISHED'})
    if bpy.context.object and bpy.context.object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    walls = [o for o in building.children if o.get("cgml3_feature") == "WallSurface"]
    check("split produced WallSurface children", len(walls) == 4, f"walls={len(walls)}")

    wall = walls[0]
    select_face_in_edit_mode(wall, 0)
    res = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=wall.name, face_index=0)
    check("draw window finishes", res == {'FINISHED'}, f"result={res}")
    if wall.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # 3.0: the writer puts openings as WallSurface/fillingSurface/WindowSurface
    # (same as for imported LoD3 windows); 2.0: bldg:opening/bldg:Window.
    expected_window = {"3.0": "con:WindowSurface", "2.0": "bldg:Window"}
    for version in ("3.0", "2.0"):
        out_path = Path(out_dir) / f"b01_authored_v{version.replace('.', '')}.gml"
        try:
            res = bpy.ops.cgml3.export_gml_file(filepath=str(out_path), citygml_version=version)
        except Exception as e:
            res = str(e)
        check(f"export {version} finishes", res == {'FINISHED'}, f"result={res}")
        if not out_path.exists():
            continue
        text = out_path.read_text(encoding="utf-8")
        check(f"{version}: exactly one top-level bldg:Building",
              len(re.findall(r"<bldg:Building[ >]", text)) == 1)
        check(f"{version}: no OtherConstruction", "OtherConstruction" not in text)
        check(f"{version}: no empty cityObjectMember", "<cityObjectMember/>" not in text
              and "<core:cityObjectMember/>" not in text)
        check(f"{version}: window written as {expected_window[version]}",
              f"<{expected_window[version]} " in text or f"<{expected_window[version]}>" in text)
        check_xsd(version, out_path)

    if FAILURES:
        print(f"\n[FAIL] B01 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B01 authored building export")


if __name__ == "__main__":  # B38 imports xsd_valid
    main()
