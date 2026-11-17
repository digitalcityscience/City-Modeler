"""Headless regression for export-error ticket 02 (Semantic Stamp copy guard).

``shared/facade_placement._apply_stamp_source_copy`` used to copy a
Semantic Stamp source's mesh verbatim with no validation - the findings doc
(``docs/agent/export-error/opening-ring-degeneracy-findings.md``) traced the
bulk of a 985-ring-error CityGML export to exactly one already-bad source
object stamped repeatedly. ``_validate_stamp_source_ring`` now checks every
polygon of the source mesh with ticket 01's shared ``validate_ring`` helper
*before* any wall/fill mutation happens.

Ticket 01 resolved that its own Pattern B (a ring with the right unique
corners but a duplicated start vertex, e.g. P1->P1->P2->P3->P4->P1) collapses
to a valid quad after cleanup and is therefore NOT degenerate - only
Pattern A (only 2 unique points) is. This test asserts both halves of that
contract at the Semantic Stamp operator level:

- Pattern A source -> ``cgml.semantic_stamp`` refuses (CANCELLED), no new
  object is created, and the target wall's mesh is left untouched.
- Pattern B source -> ``cgml.semantic_stamp`` succeeds exactly like a clean
  source (the guard must not over-reject cleanable geometry).

Usage:
    SP=/tmp/cgml-probe-export-error02
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_export_error02_stamp_copy_guard.py
"""
import addon_utils
import bmesh
import bpy


FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"[PASS] {label}")
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def wall(name, y):
    """Create a 4m x 4m WallSurface quad facing +Y."""
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


def _base_window_geometry():
    """The same 8-vertex/6-face box template used by the other Semantic
    Stamp headless tests (test_facade04/07) - a clean 2m x 1m x 0.2m prism,
    front cap = face 0."""
    verts = [
        (-1, -0.1, -0.5), (1, -0.1, -0.5), (1, -0.1, 0.5), (-1, -0.1, 0.5),
        (-1, 0.1, -0.5), (1, 0.1, -0.5), (1, 0.1, 0.5), (-1, 0.1, 0.5),
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return verts, faces


def source_window(name, *, corrupt=None):
    """A Window source, optionally carrying one extra corrupted face left
    over as if from an earlier buggy draw/stamp - the mesh's main geometry
    (used for the width/height/frame checks) stays perfectly valid, so only
    ticket 02's per-polygon guard can catch it.

    ``corrupt``:
      None       -> clean source (baseline, must stamp successfully)
      "pattern_a" -> extra face with only 2 distinct points (A,B,B,A)
      "pattern_b" -> extra face with a duplicated start vertex, 4 real
                     corners (P1,P1,P2,P3,P4) - collapses to a valid quad
                     per ticket 01's resolved semantics, must NOT refuse
    """
    verts, faces = _base_window_geometry()

    a = (-1, -0.1, -0.5)  # reuse an existing front-cap corner position
    b = (1, -0.1, -0.5)
    c = (1, -0.1, 0.5)
    d = (-1, -0.1, 0.5)

    if corrupt == "pattern_a":
        verts = verts + [a, b, b, a]
        faces = faces + [[8, 9, 10, 11]]
    elif corrupt == "pattern_b":
        verts = verts + [a, a, b, c, d]
        faces = faces + [[8, 9, 10, 11, 12]]
    elif corrupt is not None:
        raise ValueError(f"unknown corrupt mode {corrupt!r}")

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "Window"
    obj["gml_id"] = f"{name}_ID"
    obj["width"] = 2.0
    obj["height"] = 1.0
    obj.display_type = 'WIRE'
    material = bpy.data.materials.new(f"{name}_material")
    material.diffuse_color = (0.2, 0.5, 0.8, 1.0)
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def _stamp(source, target_wall):
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
    try:
        # An operator that self.report()'s an {'ERROR'} and returns
        # {'CANCELLED'} makes bpy.ops raise RuntimeError instead of just
        # returning - that's how a refusal actually surfaces to a caller.
        result = bpy.ops.cgml.semantic_stamp()
    except RuntimeError as e:
        result = {'CANCELLED'}
        _stamp.last_error = str(e)
    else:
        _stamp.last_error = None
    bpy.ops.object.mode_set(mode='OBJECT')
    return result


def test_pattern_a_source_is_refused():
    target_wall = wall("pa_target_wall", 0)
    source = source_window("pa_source", corrupt="pattern_a")

    objects_before = set(bpy.data.objects.keys())
    verts_before = len(target_wall.data.vertices)
    polys_before = len(target_wall.data.polygons)

    result = _stamp(source, target_wall)

    check("Pattern A source: operator reports CANCELLED", result == {'CANCELLED'}, f"result={result}")
    error = _stamp.last_error or ""
    check(
        "Pattern A source: error names the source object",
        source.name in error,
        f"error={error!r}",
    )
    check(
        "Pattern A source: error explains the degeneracy reason",
        "distinct point" in error,
        f"error={error!r}",
    )
    check(
        "Pattern A source: no new object created",
        set(bpy.data.objects.keys()) - objects_before == set(),
        f"new objects={set(bpy.data.objects.keys()) - objects_before}",
    )
    check(
        "Pattern A source: target wall vertex count unchanged",
        len(target_wall.data.vertices) == verts_before,
        f"before={verts_before} after={len(target_wall.data.vertices)}",
    )
    check(
        "Pattern A source: target wall polygon count unchanged",
        len(target_wall.data.polygons) == polys_before,
        f"before={polys_before} after={len(target_wall.data.polygons)}",
    )


def test_pattern_b_source_stamps_successfully():
    target_wall = wall("pb_target_wall", 5)
    source = source_window("pb_source", corrupt="pattern_b")

    result = _stamp(source, target_wall)

    check("Pattern B source: operator reports FINISHED", result == {'FINISHED'}, f"result={result}")
    stamped = next(
        (obj for obj in bpy.data.objects
         if obj is not source and obj.parent is target_wall
         and obj.get("cgml_source") == "semantic_stamp"),
        None,
    )
    check("Pattern B source: a stamped object was created", stamped is not None)
    if stamped is not None:
        check("Pattern B source: stamped mesh is independent from source", stamped.data is not source.data)
        check(
            "Pattern B source: stamped vertex count matches source",
            len(stamped.data.vertices) == len(source.data.vertices),
        )


def test_clean_source_still_stamps_successfully():
    """No-regression baseline: an unmodified source must be unaffected by
    the new guard."""
    target_wall = wall("clean_target_wall", 10)
    source = source_window("clean_source", corrupt=None)

    result = _stamp(source, target_wall)

    check("Clean source: operator reports FINISHED", result == {'FINISHED'}, f"result={result}")
    stamped = next(
        (obj for obj in bpy.data.objects
         if obj is not source and obj.parent is target_wall
         and obj.get("cgml_source") == "semantic_stamp"),
        None,
    )
    check("Clean source: a stamped object was created", stamped is not None)


def main():
    addon_utils.enable("city_modeler", default_set=False)

    test_pattern_a_source_is_refused()
    test_pattern_b_source_stamps_successfully()
    test_clean_source_still_stamps_successfully()

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} failure(s):")
        for m in FAILURES:
            print(f"  {m}")
        import sys
        sys.exit(1)
    print("\n[ALL PASS] Semantic Stamp copy guard (export-error ticket 02) satisfied")


main()
