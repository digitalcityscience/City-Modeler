"""window-door-upload ticket 02 checklist, run against the real Blender
import pipeline: a flat (single-polygon) CityGML 3 Window/Door imported
non-streaming with use_semantic_objects on must come out as a solid 4 cm
prism, not a coplanar face - z-fighting/occlusion is the whole point of the
plan (docs/agent/window-door-upload/README.md).

Uses SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML3.gml (appearance
ticket 03's fixture): its one Window and one Door are each a single flat
gml:Polygon at LoD3, no gml:interior rings - exactly the "flat single-
polygon opening object" shape ticket 02's helper assumes.

Usage:
    SP=/tmp/cgml-wdu02
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_wdu02_synthetic_depth.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy
from mathutils import Vector

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "test_files" / "Building" / "SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML3.gml"

FAILURES = []


def check(cond, msg):
    if cond:
        print(f"PASS: {msg}")
    else:
        print(f"FAIL: {msg}")
        FAILURES.append(msg)


def fresh_import():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=str(FIXTURE))
    assert res == {'FINISHED'}, f"import failed: {res}"


def world_bbox_depth_along(obj, axis: Vector) -> float:
    axis = axis.normalized()
    signed = [axis.dot(obj.matrix_world @ v.co) for v in obj.data.vertices]
    return max(signed) - min(signed)


def main():
    fresh_import()

    by_feature = {}
    for o in bpy.data.objects:
        by_feature.setdefault(o.get("cgml3_feature"), []).append(o)

    windows = by_feature.get("WindowSurface", [])
    doors = by_feature.get("DoorSurface", [])
    check(len(windows) == 1, f"exactly one WindowSurface object (got {len(windows)})")
    check(len(doors) == 1, f"exactly one DoorSurface object (got {len(doors)})")
    if not windows or not doors:
        print("\n[ABORT] fixture didn't produce the expected Window/Door - cannot continue")
        sys.exit(1)
    window, door = windows[0], doors[0]

    for label, obj in (("Window", window), ("Door", door)):
        mesh = obj.data
        # A centred prism (_make_centered_prism) duplicates the source
        # polygon's N vertices into an outward and an inward copy (2N total)
        # and adds N side faces to the 2 caps, so face_count == N + 2 is an
        # exact invariant regardless of what N (the source ring's own vertex
        # count) happens to be - independent of any rectangle assumption.
        base_vertex_count = len(mesh.vertices) // 2
        expected_faces = base_vertex_count + 2
        check(len(mesh.vertices) == 2 * base_vertex_count and len(mesh.polygons) == expected_faces,
              f"{label}: centred prism has 2*{base_vertex_count} vertices and "
              f"{expected_faces} faces (2 caps + {base_vertex_count} sides), "
              f"got {len(mesh.vertices)} vertices / {len(mesh.polygons)} faces")
        check(obj.get("cgml_synthetic_opening_depth") is True,
              f"{label}: stamped cgml_synthetic_opening_depth=True")

        normal = (obj.matrix_world.to_3x3() @ mesh.polygons[0].normal).normalized()
        depth = world_bbox_depth_along(obj, normal)
        check(abs(depth - 0.04) < 1e-4,
              f"{label}: bounding-box depth along its own normal is 0.04 m (got {depth:.5f})")

        # A synthetic depth solid must also stay a real volume, not a
        # degenerate flat prism (e.g. depth collapsed to 0 by a bad normal).
        bbox_world = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        xs, ys, zs = zip(*bbox_world)
        extents = sorted([max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)])
        check(extents[0] > 1e-4,
              f"{label}: object bounding box has positive extent on all 3 axes (smallest={extents[0]:.5f})")

    def base_color(obj):
        mat = obj.data.materials[0] if obj.data.materials else None
        return tuple(mat.diffuse_color[:3]) if mat else None

    window_rgb = base_color(window)
    check(window_rgb is not None and all(abs(window_rgb[i] - v) < 0.02 for i, v in enumerate((0.85, 0.55, 0.20))),
          f"Window: face 0 material is the amber authored-glass colour (got {window_rgb})")
    # Every face of the prism must match, not just the first - ticket 02
    # requires a uniformly amber solid, matching the authored Draw Window look.
    all_window_amber = all(
        all(abs(tuple(mat.diffuse_color[:3])[i] - v) < 0.02 for i, v in enumerate((0.85, 0.55, 0.20)))
        for mat in window.data.materials if mat is not None
    )
    check(all_window_amber, "Window: every one of the 6 prism faces is amber, not just the original face")

    door_rgb = base_color(door)
    check(door_rgb is not None and not all(abs(door_rgb[i] - v) < 0.02 for i, v in enumerate((0.85, 0.55, 0.20))),
          f"Door: material was NOT recoloured amber by this ticket (got {door_rgb})")

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} failure(s)")
        sys.exit(1)
    print("\n[ALL PASS] window-door-upload ticket 02 checklist satisfied")


main()
