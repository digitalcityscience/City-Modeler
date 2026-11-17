"""Render the balcony / chimney gallery icons (B28): ui/icons/balcony_<KIND>.png, chimney_<KIND>.png.

Same look as make_roof_icons.py (Workbench, 256 px, transparent). The shapes
come from the add-on's own ops/installation_authoring.py (balcony_boxes,
chimney_boxes, _boxes_mesh), so the icons match what the buttons build.
Re-run after changing those shapes (the add-on must be installed in the
profile, as for the headless tests):

    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup --python-use-system-env \\
      --python scripts/make_installation_icons.py
"""
import math
import os
import sys
from importlib import import_module
from pathlib import Path

import bpy
from mathutils import Euler, Vector

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "tests"))
from addon_test_utils import ensure_addon_enabled  # noqa: E402

OUT = REPO / "ui" / "icons"
COLORS = {"wall": (0.86, 0.85, 0.82, 1), "roof": (0.72, 0.22, 0.18, 1), "part": (0.25, 0.45, 0.75, 1)}


def material(label):
    mat = bpy.data.materials.get(label) or bpy.data.materials.new(label)
    mat.diffuse_color = COLORS[label]
    return mat


def add(name, mesh, label):
    mesh.materials.clear()
    mesh.materials.append(material(label))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def block(name, verts, faces, label):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return add(name, mesh, label)


def box_verts(x0, x1, y0, y1, z0, z1):
    v = [(x, y, z) for z in (z0, z1) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    f = [[3, 2, 1, 0], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return v, f


def render(name):
    bpy.context.scene.render.filepath = str(OUT / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    for o in list(bpy.context.scene.objects):
        if o.type == 'MESH':
            bpy.data.objects.remove(o)
    print(f"wrote {name}.png")


def main():
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    ia = import_module(f"{addon}.ops.installation_authoring")
    scene = bpy.context.scene
    for o in list(scene.objects):  # the startup file's cube, light, camera
        bpy.data.objects.remove(o)
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_object_outline = True
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 256
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = 'ORTHO'
    rot = Euler((math.radians(65), 0, math.radians(30)))
    cam.rotation_euler = rot

    # balconies: a 6 m wide wall corner (front wall y=0 facing -y, side wall x=3 facing +x)
    cam.data.ortho_scale = 8
    cam.location = Vector((0.5, -1, 1.5)) + rot.to_matrix() @ Vector((0, 0, 40))
    for kind, _n, _d in ia.BALCONY_TYPES:
        v, f = box_verts(-3, 3, 0, 4, -1, 4)
        block("wall", v, f, "wall")
        n = Vector((0, -1, 0))
        if kind == 'CORNER':
            base = Vector((3, 0, 1))  # the right end of the front wall seen from outside
        elif kind == 'FRENCH':
            v, f = box_verts(-0.6, 0.6, -0.01, 0.01, 1, 3.2)
            block("opening", v, f, "part")
            base = Vector((0, 0, 1))
        else:
            base = Vector((0, 0, 1))
        width = 2.4 if kind != 'FRENCH' else 1.4
        add("balcony", ia._boxes_mesh("balcony", ia.balcony_boxes(base, n, kind, width, 1.4, 1.0)), "part")
        render(f"balcony_{kind}")

    # chimneys: a gable roof piece
    cam.data.ortho_scale = 7
    cam.location = Vector((0, 0, 3)) + rot.to_matrix() @ Vector((0, 0, 40))
    for kind, _n, _d in ia.CHIMNEY_TYPES:
        root = bpy.data.objects.new("root", None)
        scene.collection.objects.link(root)
        roof = block("roof", [(-3, -2, 2), (3, -2, 2), (3, 0, 3.5), (-3, 0, 3.5), (3, 2, 2), (-3, 2, 2)],
                     [[0, 1, 2, 3], [3, 2, 4, 5]], "roof")
        roof.parent = root
        roof["cgml3_feature"] = "RoofSurface"
        v, f = box_verts(-3, 3, -2, 2, 0, 2)
        block("walls", v, f, "wall")
        normal = Vector((0, -1.5, 2)).normalized()
        boxes = ia.chimney_boxes(root, (0, -1, 2.75), normal, 0.9, 0.8, kind, 0.6)
        mesh = ia._boxes_mesh("c", boxes)
        add("chimney", mesh, "part")
        render(f"chimney_{kind}")
        bpy.data.objects.remove(root)


main()
