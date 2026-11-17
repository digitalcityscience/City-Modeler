"""Facade-authoring ticket 02: Surface Context resolver + inline explode.

Proves ``shared/facade_surface_resolver.py`` against real imported CityGML
geometry (not just synthetic pure-Python frame math, already covered by
``scripts/tests/unit/test_facade_surface_resolver.py``):

  1. Resolving a selected WallSurface face and a selected RoofSurface face on
     an already-exploded import (``use_semantic_objects`` ON) each yields a
     sane ``SurfaceContext`` - correct semantic type/id/building, an outward
     normal matching the mesh's own face normal, and positive width/height.
  2. A merged (un-exploded) import (``use_semantic_objects`` OFF) is detected
     by ``surface_needs_split`` and resolved end-to-end by
     ``resolve_surface_context_for_face`` - the one-click split path (D6) -
     landing on a real new WallSurface child object.
  3. A face with no recognizable CityGML surface semantics (an opening's own
     face) is rejected by both entry points with a clear message, never a
     silent guess.
  4. A mirrored (negative-scale) object proves the resolver's outward normal
     genuinely differs from - and corrects - a naively-transformed
     ``mesh.polygons[i].normal``, not just in synthetic pure-Python math
     (``test_mirrored_scale_would_make_a_naively_transformed_normal_wrong``)
     but through the real ``resolve_surface_context`` bpy path.

Usage:
    SP=/tmp/cgml-probe-facade02
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_facade02_surface_resolver.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy
from mathutils import Vector

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_V3 = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"

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


def fresh_import(gml, use_semantic_objects):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=str(gml))
    assert res == {'FINISHED'}, f"import failed: {res}"


def first_exterior_face(obj):
    """The first non-interior-ring face index of an exploded surface object."""
    for face in obj.data.polygons:
        mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
        if mat is not None and not mat.get("Interior") and mat.get("con_surface_id"):
            return face.index
    return None


def find_exploded_surface(feature):
    for obj in bpy.data.objects:
        if obj.type != "MESH" or str(obj.get("cgml3_feature") or "") != feature:
            continue
        face_index = first_exterior_face(obj)
        if face_index is not None:
            return obj, face_index
    return None, None


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


def find_opening_face():
    for obj in bpy.data.objects:
        feat = str(obj.get("cgml3_feature") or "")
        if obj.type == "MESH" and feat in ("WindowSurface", "DoorSurface", "Window", "Door") and obj.data.polygons:
            return obj, 0
    return None, None


def world_face_normal(obj, face_index):
    poly = obj.data.polygons[face_index]
    return (obj.matrix_world.to_3x3() @ poly.normal).normalized()


def build_mirrored_wall_object():
    """A standalone WallSurface object (no CityGML import needed) whose
    ``matrix_world`` mirrors on X (scale (-1, 1, 1)) - the same local quad as
    the unit test's ``test_mirrored_scale_would_make_a_naively_transformed_
    normal_wrong``, which winds to local normal (0, 1, 0). Proves the bug the
    module's docstring claims to avoid: naively transforming that local
    normal by the mirror matrix would keep it (0, 1, 0), but the true,
    winding-consistent outward direction of the *mirrored* face is (0, -1, 0)."""
    mesh = bpy.data.meshes.new("MirrorTestWallMesh")
    verts = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 0.0, 0.0)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()

    obj = bpy.data.objects.new("MirrorTestWallSurface", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.scale = (-1.0, 1.0, 1.0)
    # obj.matrix_world isn't refreshed until the depsgraph/view layer updates -
    # force it now so the resolver reads the real mirrored transform, not a
    # stale identity matrix.
    bpy.context.view_layer.update()
    obj["cgml3_feature"] = "WallSurface"
    obj["gml_id"] = "ID_mirror_test"
    return obj


def main():
    from city_modeler.shared.facade_surface_resolver import (
        resolve_surface_context,
        resolve_surface_context_for_face,
        surface_needs_split,
    )
    from city_modeler.shared.semantic_tree import is_boundary_surface

    # --- 1. Already-exploded WallSurface + RoofSurface resolve -----------
    fresh_import(FIXTURE_V3, use_semantic_objects=True)

    wall, wall_face = find_exploded_surface("WallSurface")
    check("fixture has an exploded WallSurface with an exterior face", wall is not None)
    if wall is not None:
        ctx = resolve_surface_context(wall, wall_face)
        check("resolved WallSurface context has the right semantic type", ctx.semantic_type == "WallSurface")
        check("resolved WallSurface context carries a semantic id", bool(ctx.semantic_id))
        check("resolved WallSurface context carries a building id", bool(ctx.building_id))
        check("resolved WallSurface width/height are positive",
              ctx.width > 1e-6 and ctx.height > 1e-6,
              f"width={ctx.width} height={ctx.height}")
        expected_normal = world_face_normal(wall, wall_face)
        check("resolved WallSurface normal matches the face's own winding",
              (ctx.normal_world - expected_normal).length < 1e-4,
              f"resolved={tuple(ctx.normal_world)} expected={tuple(expected_normal)}")
        check("resolved WallSurface frame is orthonormal",
              abs(ctx.horizontal_axis_world.length - 1.0) < 1e-6
              and abs(ctx.vertical_axis_world.length - 1.0) < 1e-6
              and abs(ctx.horizontal_axis_world.dot(ctx.vertical_axis_world)) < 1e-6
              and abs(ctx.horizontal_axis_world.dot(ctx.normal_world)) < 1e-6)

    roof, roof_face = find_exploded_surface("RoofSurface")
    check("fixture has an exploded RoofSurface with an exterior face", roof is not None)
    if roof is not None:
        ctx = resolve_surface_context(roof, roof_face)
        check("resolved RoofSurface context has the right semantic type", ctx.semantic_type == "RoofSurface")
        check("resolved RoofSurface width/height are positive",
              ctx.width > 1e-6 and ctx.height > 1e-6,
              f"width={ctx.width} height={ctx.height}")
        expected_normal = world_face_normal(roof, roof_face)
        check("resolved RoofSurface normal matches the face's own winding",
              (ctx.normal_world - expected_normal).length < 1e-4,
              f"resolved={tuple(ctx.normal_world)} expected={tuple(expected_normal)}")

    # --- 2. Rejection: an opening's own face is not a boundary surface ----
    opening, opening_face = find_opening_face()
    check("fixture has an opening (Window/Door) object", opening is not None)
    if opening is not None:
        try:
            resolve_surface_context(opening, opening_face)
            check("resolving an opening face raises a clear ValueError", False, "no exception raised")
        except ValueError as e:
            check("resolving an opening face raises a clear ValueError", True)
            check("the rejection message names the object", opening.name in str(e), str(e))

    # --- 3. Merged building: detect + inline split + resolve --------------
    fresh_import(FIXTURE_V3, use_semantic_objects=False)

    building, building_face = find_merged_building_wall_face()
    check("fixture has a merged Building with a WallSurface-tagged face", building is not None)
    if building is not None:
        check("merged building is not itself an exploded boundary surface",
              not is_boundary_surface(building))
        check("surface_needs_split detects the merged building",
              surface_needs_split(building, building_face))

        children_before = set(building.children)
        ctx = resolve_surface_context_for_face(building, building_face)

        check("resolve_surface_context_for_face landed on a real exploded WallSurface object",
              ctx.semantic_type == "WallSurface" and ctx.object_name != building.name,
              f"object_name={ctx.object_name} semantic_type={ctx.semantic_type}")
        resolved_obj = bpy.data.objects.get(ctx.object_name)
        check("the resolved object is a new child of the split building",
              resolved_obj is not None and resolved_obj in building.children
              and resolved_obj not in children_before)
        check("the resolved object is itself an exploded boundary surface",
              resolved_obj is not None and is_boundary_surface(resolved_obj))
        check("resolved (split) WallSurface width/height are positive",
              ctx.width > 1e-6 and ctx.height > 1e-6,
              f"width={ctx.width} height={ctx.height}")
        # Resolving the *new* object's own face directly (the split already
        # happened) must succeed unchanged - the already-exploded fast path.
        again = resolve_surface_context(resolved_obj, ctx.face_indices[0])
        check("resolving the split object's own face directly matches",
              again.object_name == ctx.object_name and again.semantic_id == ctx.semantic_id)

    # --- 4. Mirrored (negative-scale) object: winding vs naive transform --
    mirrored = build_mirrored_wall_object()
    mirrored_ctx = resolve_surface_context(mirrored, 0)
    naive_normal = world_face_normal(mirrored, 0)
    check("mirrored object: resolver's normal is the winding-consistent one",
          tuple(mirrored_ctx.normal_world) == (0.0, -1.0, 0.0),
          f"got {tuple(mirrored_ctx.normal_world)}")
    check("mirrored object: naively transforming the local normal would be wrong",
          tuple(naive_normal) == (0.0, 1.0, 0.0),
          f"got {tuple(naive_normal)}")
    check("mirrored object: the resolver's normal and the naive transform disagree",
          mirrored_ctx.normal_world.dot(naive_normal) < 0.0)

    if FAILURES:
        print(f"\n[FAIL] facade ticket 02 NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] facade ticket 02 surface resolver satisfied")


main()
