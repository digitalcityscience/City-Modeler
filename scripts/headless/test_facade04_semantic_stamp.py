"""UI-path regression for Window Semantic Stamp's copy contract."""
import addon_utils
import bmesh
import bpy
from mathutils import Vector


def wall(name, y):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([(-4, y, 0), (4, y, 0), (4, y, 4), (-4, y, 4)], [], [[0, 1, 2, 3]])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "WallSurface"
    obj["gml_id"] = f"{name}_ID"
    material = bpy.data.materials.new(f"{name}_material")
    material["con_surface_id"] = obj["gml_id"]
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def source_window(source_wall, *, orphaned_host=False):
    mesh = bpy.data.meshes.new("source_window_mesh")
    verts = [
        (-1, -0.1, -0.5), (1, -0.1, -0.5), (1, -0.1, 0.5), (-1, -0.1, 0.5),
        (-1, 0.1, -0.5), (1, 0.1, -0.5), (1, 0.1, 0.5), (-1, 0.1, 0.5),
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new("source_window", mesh)
    bpy.context.scene.collection.objects.link(obj)
    # Mirror the imported hierarchy from the failing scene: opening is below
    # a Building, while its true host wall is referenced by opening metadata.
    building = bpy.data.objects.new("source_building", None)
    bpy.context.scene.collection.objects.link(building)
    obj.parent = building
    obj["cgml3_feature"] = "Window"
    obj["gml_id"] = "SOURCE_WINDOW_ID"
    # Real-world reproduction (Window11.gml / d53ac31a bug report): the
    # opening's recorded filling_parent_surface_id points at a WallSurface
    # gml:id that has no matching object anywhere in the scene (its true
    # host wall was never imported/exploded, or its id diverged). Any source
    # resolution that depends on finding a live WallSurface object must not
    # be required for Stamp to work.
    host_id = "ID_d53ac31a-d3e9-4835-8709-4f9f2674199c" if orphaned_host else source_wall["gml_id"]
    obj["filling_parent_surface_id"] = host_id
    obj["opening_surface_id"] = host_id
    obj["width"] = 2.0
    obj["height"] = 1.0
    obj["frame_thickness"] = 0.07
    obj.display_type = 'WIRE'
    obj.show_name = True
    obj.modifiers.new("source_bevel", 'BEVEL').width = 0.01
    material = bpy.data.materials.new("source_window_material")
    material.diffuse_color = (0.2, 0.5, 0.8, 1.0)
    material["gml_polygon_id"] = "SOURCE_WINDOW_POLY"
    mesh.materials.append(material)
    return obj


def main():
    addon_utils.enable("city_modeler", default_set=False)
    source_wall = wall("source_wall", 0)
    target_wall = wall("target_wall", 5)
    source = source_window(source_wall, orphaned_host=True)

    # This is the actual user path: remember source in Object Mode, activate
    # a target WallSurface, enter Edit Mode, select face, invoke Stamp.
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    assert bpy.ops.cgml.set_semantic_stamp_source() == {'FINISHED'}
    target_wall.select_set(True)
    bpy.context.view_layer.objects.active = target_wall
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(target_wall.data)
    for face in bm.faces:
        face.select = True
    bmesh.update_edit_mesh(target_wall.data)
    assert bpy.ops.cgml.semantic_stamp() == {'FINISHED'}

    stamped = next(
        obj for obj in bpy.data.objects
        if obj is not source and obj.parent is target_wall and obj.get("cgml_source") == "semantic_stamp"
    )

    assert stamped.data is not source.data
    assert len(stamped.data.vertices) == len(source.data.vertices)
    assert len(stamped.data.polygons) == len(source.data.polygons)
    assert stamped.parent is target_wall
    assert stamped["gml_id"] != source["gml_id"]
    assert stamped["cgml_parent_id"] == target_wall["gml_id"]
    assert stamped["semantic_type"] == "Window"
    assert stamped["width"] == source["width"]
    assert stamped.display_type == source.display_type and stamped.show_name == source.show_name
    assert stamped.modifiers.get("source_bevel") is not None
    assert all(abs(v - 1.0) < 1e-6 for v in stamped.scale)
    assert all(mat.get("gml_polygon_id") != "SOURCE_WINDOW_POLY" for mat in stamped.data.materials)

    # Aligned to the target face centre/normal (requirement 5), independent of
    # wherever the source's real host wall is or isn't.
    target_centre = Vector((0.0, 5.0, 2.0))
    assert (stamped.matrix_world.translation - target_centre).length < 1e-6, stamped.matrix_world.translation
    stamped_normal = (stamped.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    assert abs(abs(stamped_normal.y) - 1.0) < 1e-6, stamped_normal

    # The user must be able to choose *where* a stamp lands, not just the face
    # centre - a second stamp at a different position_uv must land somewhere
    # else on the same wall plane (still y == 5, the wall's own plane).
    off_centre = bpy.ops.cgml.semantic_stamp(position_uv=(0.15, 0.85))
    assert off_centre == {'FINISHED'}
    second = next(
        obj for obj in bpy.data.objects
        if obj not in (source, stamped) and obj.parent is target_wall and obj.get("cgml_source") == "semantic_stamp"
    )
    assert abs(second.matrix_world.translation.y - 5.0) < 1e-6, second.matrix_world.translation
    assert (second.matrix_world.translation - target_centre).length > 0.5, (
        "a custom position_uv must not silently re-centre the stamp"
    )

    # Repeated clicks in one session must each be fully independent - no
    # shared mesh-data block or reused polygon/gml id between the two stamps.
    assert second.data is not stamped.data
    assert second["gml_id"] != stamped["gml_id"]

    print("SEMANTIC_STAMP_UI_COPY_PASS")


main()
