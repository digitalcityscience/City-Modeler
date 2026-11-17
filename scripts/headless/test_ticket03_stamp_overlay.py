"""copy-paste-object-behaviour ticket 03 (Semantic Stamp overlay: alignment
guides + neighbour distance, reusing ticket 02's render helper as-is).

Ticket 03 is wiring, not new logic: ``ops/facade_authoring.py``'s
``SEMANTIC_OT_stamp`` now calls the exact same
``shared/facade_alignment_overlay.compute_alignment_overlay`` ticket 02 wired
into Draw, with the Stamp source's own measured subtype/size as the candidate.
That shared function's own classification behaviour is already regression-
tested by ``test_ticket02_alignment_overlay.py``; this test instead proves the
one behavioural claim specific to Stamp's repeated-click flow: each click's
overlay must be computed against the wall's *current* opening set, including
copies stamped earlier in the same session - since ``place_semantic_element``
parents every stamped copy onto the target wall (``fill.parent = wall``) and
``gather_same_wall_neighbours`` reads a wall's children live, this falls out
of composing existing, already-tested pieces rather than needing new
mouse-to-screen-projection logic (which needs a real 3D viewport region and so
isn't exercisable in ``--background`` mode - see that test's docstring for why
the GPU/``blf`` draw itself is a manual-check item, same here for Stamp).

Usage:
    SP=/tmp/cgml-probe-ticket03
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket03_stamp_overlay.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import addon_utils
import bpy

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


def source_window(name="source_window"):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    verts = [
        (-0.5, -0.1, -0.3), (0.5, -0.1, -0.3), (0.5, -0.1, 0.3), (-0.5, -0.1, 0.3),
        (-0.5, 0.1, -0.3), (0.5, 0.1, -0.3), (0.5, 0.1, 0.3), (-0.5, 0.1, 0.3),
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "Window"
    obj["gml_id"] = f"{name}_ID"
    obj["width"] = 1.0
    obj["height"] = 0.6
    material = bpy.data.materials.new(f"{name}_material")
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def main():
    addon_utils.enable("city_modeler", default_set=False)

    from city_modeler.shared.facade_alignment_overlay import (
        compute_alignment_overlay,
        gather_same_wall_neighbours,
    )
    from city_modeler.shared.facade_placement import (
        definition_from_source_object,
        place_semantic_element,
        surface_uv_from_world_point,
        surface_world_point_from_uv,
    )
    from city_modeler.shared.facade_surface_resolver import resolve_surface_context

    target = wall("target_wall", 0)
    source = source_window()
    ctx = resolve_surface_context(target, 0)
    definition = definition_from_source_object(source)

    # --- click 1: an empty wall has no neighbours yet -----------------------
    first_uv = (0.3, 0.5)
    overlay_before_any = compute_alignment_overlay(
        ctx, target, first_uv, definition.width, definition.height, subtype=definition.semantic_type,
    )
    check(
        "before any stamp, the wall has no neighbours",
        len(overlay_before_any.neighbours) == 0,
        f"got {len(overlay_before_any.neighbours)}",
    )

    first_point = surface_world_point_from_uv(ctx, first_uv)
    first_copy = place_semantic_element(source_object=source, surface_context=ctx, position_world=first_point)
    check("first stamp placed", first_copy is not None)

    # Since AB-17 an opening may not touch another one's ring (a shared edge makes the wall
    # polygon invalid), so the stamps sit GAP_M apart instead of flush.
    GAP_M = 0.2

    # --- click 2: beside the first stamp - must see it as a green-
    # aligned same-subtype neighbour, proving "current opening set" includes
    # copies placed earlier in this same session (not just pre-existing ones).
    second_uv = (first_uv[0] + (definition.width + GAP_M) / ctx.width, first_uv[1])
    second_point = surface_world_point_from_uv(ctx, second_uv)
    second_candidate_uv = surface_uv_from_world_point(ctx, second_point)
    overlay_after_first = compute_alignment_overlay(
        ctx, target, second_candidate_uv, definition.width, definition.height, subtype=definition.semantic_type,
    )
    check(
        "second click's overlay sees the first stamp as a neighbour",
        len(overlay_after_first.neighbours) == 1,
        f"got {len(overlay_after_first.neighbours)}",
    )
    check(
        "that neighbour is tagged with the source's own subtype (Window)",
        overlay_after_first.neighbours and overlay_after_first.neighbours[0].subtype == definition.semantic_type,
    )
    # Side-by-side at the same height (not stacked), so it's the top/bottom
    # edges - not left/right - that line up with the first stamp.
    green_row = [
        m for m in overlay_after_first.matches
        if m.edge in ("top", "bottom") and m.state == "green"
    ]
    check(
        "second click beside it gets a green top/bottom-edge match",
        bool(green_row),
        f"matches={overlay_after_first.matches}",
    )
    check("gap is measured against the first stamp", overlay_after_first.gap is not None)

    second_copy = place_semantic_element(source_object=source, surface_context=ctx, position_world=second_point)
    check("second stamp placed", second_copy is not None)

    # --- click 3: overlay must now see BOTH earlier stamps, not just the most
    # recent one - "current opening set", cumulative across the session.
    third_uv = (second_uv[0] + (definition.width + GAP_M) / ctx.width, second_uv[1])
    third_point = surface_world_point_from_uv(ctx, third_uv)
    third_candidate_uv = surface_uv_from_world_point(ctx, third_point)
    neighbours_at_third = gather_same_wall_neighbours(ctx, target)
    check(
        "third click's neighbour set includes both earlier stamps",
        len(neighbours_at_third) == 2,
        f"got {len(neighbours_at_third)}",
    )
    overlay_after_second = compute_alignment_overlay(
        ctx, target, third_candidate_uv, definition.width, definition.height, subtype=definition.semantic_type,
    )
    check(
        "third click's overlay carries both earlier stamps as neighbours",
        len(overlay_after_second.neighbours) == 2,
        f"got {len(overlay_after_second.neighbours)}",
    )

    if FAILURES:
        print(f"\n[FAIL] ticket 03 (stamp overlay wiring) NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 03 (stamp overlay: alignment guides + neighbour distance) satisfied")


main()
