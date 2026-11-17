"""Render the roof gallery icons (B06): ui/icons/roof_<code>.png, 256 px.

Builds each roof type with the add-on's own shapes on a 12 x 8 x 4 m block
and renders it with Workbench (roof red, walls light grey, transparent
background). Re-run after changing shared/roof_shapes.py:

    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/make_roof_icons.py
"""
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "shared"))
import building_shapes  # noqa: E402
import roof_shapes  # noqa: E402

OUT = REPO / "ui" / "icons"
COLORS = {"RoofSurface": (0.72, 0.22, 0.18, 1), "WallSurface": (0.86, 0.85, 0.82, 1),
          "GroundSurface": (0.5, 0.5, 0.5, 1)}


def material(label):
    mat = bpy.data.materials.get(label) or bpy.data.materials.new(label)
    mat.diffuse_color = COLORS[label]
    return mat


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
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
    cam.data.ortho_scale = 16
    cam.location = Vector((0, 0, 3.5)) + Euler((math.radians(62), 0, math.radians(35))).to_matrix() @ Vector((0, 0, 40))
    cam.rotation_euler = Euler((math.radians(62), 0, math.radians(35)))

    rect = building_shapes.rectangle((0.0, 0.0, 0.0), 12.0, 8.0)
    round_ = [(5 * math.cos(math.pi * k / 12), 5 * math.sin(math.pi * k / 12), 0.0) for k in range(24)]
    only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []  # codes; none = all
    for code, name in roof_shapes.ROOF_TYPES.items():
        if only and code not in only:
            continue
        pitch = {"1000": 0.0, "2100": 12.0, "2200": 18.0, "3600": 50.0, "3700": 45.0, "4000": 65.0}.get(code, 35.0)
        ring = round_ if code in roof_shapes.RADIAL else rect  # B87: the round roofs on a round tower
        verts, faces, labels = roof_shapes.roof_solid(ring, 0.0, 4.0, code, pitch)
        mesh = bpy.data.meshes.new(code)
        mesh.from_pydata(verts, [], faces)
        for label in COLORS:
            mesh.materials.append(material(label))
        for poly, label in zip(mesh.polygons, labels):
            poly.material_index = list(COLORS).index(label)
        obj = bpy.data.objects.new(code, mesh)
        scene.collection.objects.link(obj)
        scene.render.filepath = str(OUT / f"roof_{code}.png")
        bpy.ops.render.render(write_still=True)
        bpy.data.objects.remove(obj)
        print(f"wrote roof_{code}.png ({name})")


main()
