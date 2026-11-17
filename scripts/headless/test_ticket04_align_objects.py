"""copy-paste-object-behaviour ticket 04 (Align Objects operator).

End-to-end regression against real ``cgml.align_objects`` selection/poll
wiring plus ``shared/align_objects.py``'s geometry, using synthetic
WallSurface/Window fixtures (mirroring ``test_ticket03_stamp_overlay.py``'s
style) placed through the real headless placement kernel
(``place_semantic_element``) so every Window carries a genuine
``cgml_opening_link`` and wall interior ring - not hand-built mesh data.

Checklist covered (docs/agent/copy-paste-object-behavioir_ilkSira/issues/
04-align-objects-operator.md):

1. Beside pair (same wall) -> vertical match, snaps to whichever of
   top/bottom was already closer.
2. Stacked pair (same wall) -> horizontal match, snaps to whichever of
   left/right was already closer.
3. Diagonal (ambiguous) pair -> halts and asks (operator reports an error,
   does not guess).
4. Horizontal-match attempt against a different WallSurface -> rejected;
   vertical-match attempt against a different WallSurface -> succeeds.
5. After an align: ``cgml_opening_link`` unchanged, target's own wall
   interior ring re-cut to match (no desync via ``pairs_for_wall_children``).
6. The opening's depth/normal offset is unchanged by the operator.

Usage:
    SP=/tmp/cgml-probe-ticket04
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket04_align_objects.py
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


_WALL_COUNTER = {"n": 0}


def make_wall(name, verts):
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(list(verts), [], [list(range(len(verts)))])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "WallSurface"
    obj["gml_id"] = f"{name}_ID"
    material = bpy.data.materials.new(f"{name}_material")
    material["con_surface_id"] = obj["gml_id"]
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def select_only(*objs):
    for o in bpy.data.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1]


def main():
    addon_utils.enable("city_modeler", default_set=False)

    from city_modeler.shared.align_objects import align_source_to_target
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition,
        PlacementRequest,
        place_semantic_element,
    )
    from city_modeler.shared.facade_surface_resolver import resolve_surface_context
    from city_modeler.shared.ring_opening_sync import (
        OPENING_LINK_KEY,
        find_ring_opening_pairs,
        pairs_for_wall_children,
    )

    # A real Building ancestor (production always has one) so the building-
    # wide ring lookup `shared/align_objects.py._sync_source_ring` uses for a
    # floating Door - which the edit-exit handler's Window-only
    # `pairs_for_wall_children` cannot find - has something to walk up to.
    building_a = bpy.data.objects.new("building_a", None)
    bpy.context.scene.collection.objects.link(building_a)
    building_a["cgml3_feature"] = "Building"
    building_a["gml_id"] = "building_a_ID"

    # wall_a: a y=0 plane, x in [0,10], z in [0,4] - h_axis along X, v_axis
    # always world Z for any vertical wall (see shared/align_objects.py's
    # module docstring).
    wall_a_verts = [(0, 0, 0), (10, 0, 0), (10, 0, 4), (0, 0, 4)]
    wall_a = make_wall("wall_a", wall_a_verts)
    wall_a.parent = building_a
    # wall_b: a perpendicular wall (x=20 plane), far away in X - proves a
    # vertical match can cross walls of a totally different orientation.
    wall_b = make_wall("wall_b", [(20, 0, 0), (20, 10, 0), (20, 10, 4), (20, 0, 4)])
    # wall_c: same orientation/x-range as wall_a but a separate object,
    # directly above it (z in [4,8]) - a real "different WallSurface" for
    # the horizontal-match rejection case.
    wall_c = make_wall("wall_c", [(0, 0, 4), (10, 0, 4), (10, 0, 8), (0, 0, 8)])

    ctx_a = resolve_surface_context(wall_a, 0)
    ctx_b = resolve_surface_context(wall_b, 0)
    ctx_c = resolve_surface_context(wall_c, 0)

    def fresh_wall_a():
        """wall_a without openings again: since AB-17 a new opening may not overlap or touch an
        existing ring, so each scenario starts from an untouched wall (same object and name)."""
        for child in list(wall_a.children):
            bpy.data.objects.remove(child, do_unlink=True)
        wall_a.data.clear_geometry()
        wall_a.data.from_pydata(wall_a_verts, [], [[0, 1, 2, 3]])
        wall_a.data.update()
        return resolve_surface_context(wall_a, 0)

    def place(ctx, u, v, width, height):
        return place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=width, height=height),
            ctx, PlacementRequest(position_uv=(u, v)),
        )

    def place_floating_door(ctx, u, v, width, height):
        return place_semantic_element(
            FacadeElementDefinition(
                semantic_type="Door", width=width, height=height, door_touches_ground=False,
            ),
            ctx, PlacementRequest(position_uv=(u, v)),
        )

    # ------------------------------------------------------------------
    # 1. Beside pair (same wall) -> vertical match, closer edge = top.
    # ------------------------------------------------------------------
    source1 = place(ctx_a, 0.30, 0.50, 1.0, 1.0)   # center (3.0, 2.0), z in [1.5, 2.5]
    target1 = place(ctx_a, 0.60, 0.575, 1.0, 0.6)  # center (6.0, 2.3), z in [2.0, 2.6]

    link_before = source1.get(OPENING_LINK_KEY)
    z_before = source1.matrix_world.translation.z

    result1 = align_source_to_target(source1, target1)
    check("beside pair classifies as vertical match", result1.axis == "vertical", result1.axis)
    check("beside pair snaps to the closer (top) edge", result1.edge == "top", result1.edge)

    source1_verts_z = [(source1.matrix_world @ v.co).z for v in source1.data.vertices]
    target1_verts_z = [(target1.matrix_world @ v.co).z for v in target1.data.vertices]
    check(
        "source1's top edge now matches target1's top edge",
        abs(max(source1_verts_z) - max(target1_verts_z)) < 1e-4,
        f"source top={max(source1_verts_z)} target top={max(target1_verts_z)}",
    )
    check(
        "align never touches the opening's own depth/normal offset (z-move only in-plane)",
        abs(source1.matrix_world.translation.z - z_before - result1.delta_m) < 1e-6,
    )
    check("cgml_opening_link is unchanged after the align", source1.get(OPENING_LINK_KEY) == link_before)

    pair1 = next((p for p in pairs_for_wall_children(wall_a) if p.opening is source1), None)
    check("source1's wall ring was found and re-cut to match", pair1 is not None and pair1.in_sync,
          f"pair={pair1}")
    check("align_source_to_target reports the ring as synced", result1.synced)

    # ------------------------------------------------------------------
    # 2. Stacked pair (same wall) -> horizontal match, closer edge = right.
    # ------------------------------------------------------------------
    ctx_a = fresh_wall_a()
    source2 = place(ctx_a, 0.10, 0.20, 1.0, 1.0)     # center (1.0, 0.8), x in [0.5, 1.5]
    target2 = place(ctx_a, 0.105, 0.80, 0.6, 1.0)    # center (1.05, 3.2), x in [0.75, 1.35]

    result2 = align_source_to_target(source2, target2)
    check("stacked pair classifies as horizontal match", result2.axis == "horizontal", result2.axis)
    check("stacked pair snaps to the closer (right) edge", result2.edge == "right", result2.edge)

    pair2 = next((p for p in pairs_for_wall_children(wall_a) if p.opening is source2), None)
    check("source2's wall ring was re-cut to match", pair2 is not None and pair2.in_sync, f"pair={pair2}")

    # ------------------------------------------------------------------
    # 3. Diagonal (ambiguous) pair -> halts and asks.
    # ------------------------------------------------------------------
    ctx_a = fresh_wall_a()
    source3 = place(ctx_a, 0.20, 0.25, 1.0, 1.0)   # center (2.0, 1.0)
    target3 = place(ctx_a, 0.32, 0.55, 1.0, 1.0)   # center (3.2, 2.2) - equal 1.2 m u/v displacement
    raised = False
    try:
        align_source_to_target(source3, target3)
    except ValueError:
        raised = True
    check("a diagonal (ambiguous) pair raises rather than guessing", raised)

    # ------------------------------------------------------------------
    # 4a. Vertical match against a *different* WallSurface succeeds.
    # ------------------------------------------------------------------
    ctx_a = fresh_wall_a()
    source4 = place(ctx_a, 0.30, 0.50, 1.0, 1.0)      # wall_a, z in [1.5, 2.5]
    target4 = place(ctx_b, 0.50, 0.575, 1.0, 0.6)     # wall_b, z in [2.0, 2.6]

    result4 = align_source_to_target(source4, target4)
    check("cross-wall pair (far apart in u) classifies as vertical match", result4.axis == "vertical", result4.axis)
    check("cross-wall vertical match still succeeds (no wall guard)", result4 is not None)

    source4_verts_z = [(source4.matrix_world @ v.co).z for v in source4.data.vertices]
    target4_verts_z = [(target4.matrix_world @ v.co).z for v in target4.data.vertices]
    check(
        "source4's edge now matches target4's edge across walls",
        abs(max(source4_verts_z) - max(target4_verts_z)) < 1e-4
        or abs(min(source4_verts_z) - min(target4_verts_z)) < 1e-4,
        f"source z=[{min(source4_verts_z)},{max(source4_verts_z)}] "
        f"target z=[{min(target4_verts_z)},{max(target4_verts_z)}]",
    )

    # ------------------------------------------------------------------
    # 4b. Horizontal match against a *different* WallSurface is rejected.
    # ------------------------------------------------------------------
    ctx_a = fresh_wall_a()
    source5 = place(ctx_a, 0.50, 0.50, 1.0, 1.0)     # wall_a, center (5.0, 2.0)
    target5 = place(ctx_c, 0.52, 0.50, 1.0, 1.0)     # wall_c, center (5.2, 6.0) - directly "above"

    raised_cross_wall = False
    message = ""
    try:
        align_source_to_target(source5, target5)
    except ValueError as e:
        raised_cross_wall = True
        message = str(e)
    check("horizontal match across different WallSurfaces is rejected", raised_cross_wall, message)
    check("the rejection message names both walls", "wall_a" in message and "wall_c" in message, message)

    # ------------------------------------------------------------------
    # 5. Floating Door (ring-paired, not Window) beside pair on wall_a.
    # Both the building-wide and wall-local pairing paths must find its ring.
    # ------------------------------------------------------------------
    ctx_a = fresh_wall_a()
    door_source = place_floating_door(ctx_a, 0.75, 0.50, 0.6, 1.0)    # center (7.5, 2.0), z in [1.5, 2.5]
    door_target = place_floating_door(ctx_a, 0.95, 0.575, 0.6, 0.6)   # center (9.5, 2.3), z in [2.0, 2.6]

    door_link_before = door_source.get(OPENING_LINK_KEY)
    result_door = align_source_to_target(door_source, door_target)
    check("floating Door pair classifies as vertical match", result_door.axis == "vertical", result_door.axis)
    check("floating Door align reports the ring as synced", result_door.synced)
    check(
        "floating Door's cgml_opening_link is unchanged after the align",
        door_source.get(OPENING_LINK_KEY) == door_link_before,
    )

    door_pairs = find_ring_opening_pairs(building_a)
    door_pair = next((p for p in door_pairs if p.opening is door_source), None)
    check(
        "floating Door's wall ring was found (building-wide) and re-cut to match",
        door_pair is not None and door_pair.in_sync,
        f"pair={door_pair}",
    )
    local_door_pair = next((p for p in pairs_for_wall_children(wall_a) if p.opening is door_source), None)
    check(
        "pairs_for_wall_children includes the floating Door on its own wall",
        local_door_pair is not None and local_door_pair.in_sync,
        f"pair={local_door_pair}",
    )

    # ------------------------------------------------------------------
    # Operator-level wiring: poll()/execute() via real selection state.
    # ------------------------------------------------------------------
    from city_modeler.ops.facade_authoring import CGML_OT_align_objects

    ctx_a = fresh_wall_a()
    source6 = place(ctx_a, 0.70, 0.50, 1.0, 1.0)
    target6 = place(ctx_a, 0.85, 0.575, 1.0, 0.6)
    select_only(source6, target6)
    check("operator poll() accepts a source + active-target opening pair", CGML_OT_align_objects.poll(bpy.context))

    op_result = bpy.ops.cgml.align_objects('EXEC_DEFAULT')
    check("cgml.align_objects operator finishes", op_result == {'FINISHED'}, str(op_result))

    source6_verts_z = [(source6.matrix_world @ v.co).z for v in source6.data.vertices]
    target6_verts_z = [(target6.matrix_world @ v.co).z for v in target6.data.vertices]
    check(
        "operator run actually moved source6 onto target6's edge",
        abs(max(source6_verts_z) - max(target6_verts_z)) < 1e-4,
        f"source top={max(source6_verts_z)} target top={max(target6_verts_z)}",
    )

    # poll() must reject a single-object / non-opening selection.
    select_only(wall_a)
    check("operator poll() rejects a single-object selection", not CGML_OT_align_objects.poll(bpy.context))

    if FAILURES:
        print(f"\n[FAIL] ticket 04 (Align Objects) NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 04 (Align Objects operator) satisfied")


main()
