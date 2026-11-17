"""CAC-P4.8: legacy wall-local pairing includes only ring-paired Doors.

Door intentionally remains outside the authoring-core record model. This test
exercises only legacy facade geometry: a Window and a supported floating Door
share a wall and retain independent ring pairs. A ground-touching Door remains
outside this abstraction.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402


RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.2


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    return wall, face_index, h.surface_context(wall, face_index)


def _place_pair(wall, face_index, ctx):
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )

    window_uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    window = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE),
        ctx, PlacementRequest(position_uv=window_uv, source="manual_draw"),
    )
    avoid = ((window_uv[0] * ctx.width, window_uv[1] * ctx.height, SIZE / 2, SIZE / 2),)
    door_uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    door = place_semantic_element(
        FacadeElementDefinition(semantic_type="Door", width=SIZE, height=SIZE, door_touches_ground=False),
        ctx, PlacementRequest(position_uv=door_uv, source="manual_draw"),
    )
    return window, door


def _world_vertices(obj):
    return tuple(tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices)


def local_pairing_keeps_window_and_floating_door_independent(R):
    import bpy
    from mathutils import Vector
    from city_modeler.shared.ring_opening_sync import (
        pairs_for_wall_children, sync_opening_to_ring,
    )

    wall, face_index, ctx = _prepare()
    window, door = _place_pair(wall, face_index, ctx)

    # An unlinked Door is the ground/notch form for this abstraction. It is a
    # wall child but must never be guessed onto either real opening ring.
    unrelated_mesh = bpy.data.meshes.new("AB07 unrelated Door mesh")
    unrelated = bpy.data.objects.new("AB07 unrelated ground Door", unrelated_mesh)
    bpy.context.scene.collection.objects.link(unrelated)
    unrelated.parent = wall
    unrelated["cgml3_feature"] = "Door"

    pairs = pairs_for_wall_children(wall)
    window_pairs = [pair for pair in pairs if pair.opening is window]
    door_pairs = [pair for pair in pairs if pair.opening is door]
    unrelated_pairs = [pair for pair in pairs if pair.opening is unrelated]
    initial_ok = (len(window_pairs) == 1 and window_pairs[0].in_sync
                  and len(door_pairs) == 1 and door_pairs[0].in_sync
                  and not unrelated_pairs)
    R.check("window_and_floating_door_have_independent_local_pairs", initial_ok,
            f"window={len(window_pairs)} door={len(door_pairs)} unrelated={len(unrelated_pairs)}")

    window_before = _world_vertices(window)
    door.location += Vector((0.04, 0.0, 0.0))
    moved_pair = next(pair for pair in pairs_for_wall_children(wall) if pair.opening is door)
    sync_opening_to_ring(moved_pair)
    after_pairs = pairs_for_wall_children(wall)
    window_after = _world_vertices(window)
    synced_door = next(pair for pair in after_pairs if pair.opening is door)
    synced_window = next(pair for pair in after_pairs if pair.opening is window)
    R.check("floating_door_sync_targets_its_own_ring_without_mutating_window",
            synced_door.in_sync and synced_window.in_sync and window_before == window_after,
            f"door_in_sync={synced_door.in_sync} window_in_sync={synced_window.in_sync} "
            f"window_unchanged={window_before == window_after}")


def ground_touching_door_remains_outside_pairing_abstraction(R):
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, door_touches_ground,
    )

    _wall, _face_index, ctx = _prepare()
    definition = FacadeElementDefinition(semantic_type="Door", width=SIZE, height=SIZE)
    placement = PlacementRequest(position_uv=(0.5, (SIZE / 2) / ctx.height), source="manual_draw")
    R.check("ground_touching_door_stays_notch_followup_not_ring_paired",
            door_touches_ground(ctx, placement, definition),
            "ground-touching Doors remain an exterior-notch follow-up, not an interior-ring candidate")


def main():
    R = h.Results(__file__)
    for check in (
        local_pairing_keeps_window_and_floating_door_independent,
        ground_touching_door_remains_outside_pairing_abstraction,
    ):
        try:
            check(R)
        except Exception:
            R.check(check.__name__, False, traceback.format_exc())
    R.finish()


if __name__ == "__main__":
    main()
