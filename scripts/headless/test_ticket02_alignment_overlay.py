"""copy-paste-object-behaviour ticket 02 (Draw overlay: alignment guides,
neighbour distance, live size) - headless regression.

Proves the *wiring* half of ticket 02 (real Blender objects -> ticket 01's
pure ``shared/alignment_geometry.py`` engine), against a real wall with real
placed Window/Door children:

- ``gather_same_wall_neighbours`` finds exactly the wall's own opening
  children, tagged with the right subtype.
- ``compute_alignment_overlay`` end-to-end: a candidate footprint that
  shares a real neighbour's edge classifies green; one placed outside the
  wall's own bounds classifies red/bounds.
- ``nearest_same_subtype_gap`` only ever measures against a same-subtype
  neighbour, even when a different-subtype one sits closer.

The GPU/``blf`` drawing itself (``draw_guides``/``draw_alignment_overlay``)
is deliberately NOT asserted on here - line placement and label legibility
are a visual judgement call (see the ticket's own checklist item), not a
pixel-matching regression. Manual in-Blender check recommended for that half.

Usage:
    SP=/tmp/cgml-probe-ticket02
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket02_alignment_overlay.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # cac_harness

import bpy

import cac_harness  # noqa: E402  (is_clean_face)

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


def fresh_import(gml):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=str(gml))
    assert res == {'FINISHED'}, f"import failed: {res}"


def a_wall_with_exterior_face():
    for obj in bpy.data.objects:
        feat = str(obj.get("cgml3_feature") or "")
        if feat != "WallSurface" or obj.type != "MESH":
            continue
        for face in obj.data.polygons:
            mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id") \
                    and cac_harness.is_clean_face(obj, face):
                return obj, face.index
    raise AssertionError("fixture has no WallSurface with an exterior face")


def main():
    fresh_import(FIXTURE_V3)

    from city_modeler.shared.facade_alignment_overlay import (
        candidate_rect,
        compute_alignment_overlay,
        gather_same_wall_neighbours,
    )
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition,
        PlacementRequest,
        place_semantic_element,
    )
    from city_modeler.shared.facade_surface_resolver import resolve_surface_context

    wall, face_index = a_wall_with_exterior_face()
    ctx = resolve_surface_context(wall, face_index)

    # Scaled to the actual fixture wall so the test doesn't assume an
    # absolute size - a small, safely-interior slice of the wall's own
    # extent in both directions.
    win_w, win_h = ctx.width * 0.12, ctx.height * 0.22
    door_w, door_h = ctx.width * 0.10, ctx.height * 0.30

    window_uv = (0.25, 0.55)
    door_uv = (0.70, 0.30)

    window = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=win_w, height=win_h),
        ctx, PlacementRequest(position_uv=window_uv, source="manual_draw"),
    )
    door = place_semantic_element(
        FacadeElementDefinition(semantic_type="Door", width=door_w, height=door_h, door_touches_ground=False),
        ctx, PlacementRequest(position_uv=door_uv, source="manual_draw"),
    )
    check("Window placed", window is not None)
    check("Door placed (forced floating)", door is not None)

    # --- gather_same_wall_neighbours: right children, right subtypes -------
    neighbours = gather_same_wall_neighbours(ctx, wall)
    check("gather finds exactly the two placed openings", len(neighbours) == 2, f"got {len(neighbours)}")
    subtypes = sorted(n.subtype for n in neighbours)
    check("gathered subtypes are Window+Door", subtypes == ["Door", "Window"], f"got {subtypes}")

    window_rect = next((n for n in neighbours if n.subtype == "Window"), None)
    door_rect = next((n for n in neighbours if n.subtype == "Door"), None)
    check("a Window neighbour rect was found", window_rect is not None)
    check("a Door neighbour rect was found", door_rect is not None)

    # --- green edge match: a candidate flush-aligned with, and stacked
    # directly above, the Window (same u-span -> left/right/centre-u all
    # line up exactly; stacked with no gap -> no v-axis match, no collision).
    stacked_centre_v = (window_rect.v_max + win_h / 2.0) / ctx.height
    overlay = compute_alignment_overlay(
        ctx, wall, (window_uv[0], stacked_centre_v), win_w, win_h, "Window",
    )
    left_matches = [
        m for m in overlay.matches
        if m.edge == "left" and m.state == "green" and overlay.neighbours[m.neighbour_index].subtype == "Window"
    ]
    check(
        "candidate flush-aligned with the Window gets a green left match",
        bool(left_matches),
        f"matches={overlay.matches}",
    )

    # --- red bounds match: a candidate that overhangs the wall's own edge --
    overlay_oob = compute_alignment_overlay(ctx, wall, (0.0, 0.5), win_w, win_h, "Window")
    check(
        "candidate overhanging the wall boundary gets a red bounds match",
        any(m.edge == "bounds" and m.state == "red" for m in overlay_oob.matches),
        f"matches={overlay_oob.matches}",
    )

    # --- red collision match: a candidate placed exactly on the Window -----
    overlay_collision = compute_alignment_overlay(ctx, wall, window_uv, win_w, win_h, "Window")
    collisions = [m for m in overlay_collision.matches if m.edge == "collision"]
    check(
        "candidate placed on top of the Window gets a red collision match",
        len(collisions) == 1 and collisions[0].state == "red"
        and overlay_collision.neighbours[collisions[0].neighbour_index].subtype == "Window",
        f"matches={overlay_collision.matches}",
    )

    # --- same-subtype-only gap: nearer Door must not win a Window's gap -----
    # Candidate is a Window placed near the Door (closer to the Door than to
    # the Window) - the gap reported must still measure to the (further)
    # Window neighbour, never the closer Door.
    near_door_uv = (door_uv[0] - 0.03, door_uv[1])
    candidate = candidate_rect(ctx, near_door_uv, win_w, win_h, "Window")
    overlay_gap = compute_alignment_overlay(ctx, wall, near_door_uv, win_w, win_h, "Window")
    check("nearest_same_subtype_gap found a match", overlay_gap.gap is not None)
    if overlay_gap.gap is not None:
        matched = overlay_gap.neighbours[overlay_gap.gap.neighbour_index]
        check(
            "gap is measured against the Window neighbour, not the closer Door",
            matched.subtype == "Window",
            f"matched subtype={matched.subtype!r}",
        )
        # Sanity: the raw distance to the Door really is closer than to the
        # Window, proving this candidate placement is a meaningful test of
        # the same-subtype-only rule (not a vacuous pass).
        door_dist = min(abs(candidate.u_min - door_rect.u_max), abs(door_rect.u_min - candidate.u_max))
        window_dist = min(abs(candidate.u_min - window_rect.u_max), abs(window_rect.u_min - candidate.u_max))
        check(
            "the Door really is closer than the Window (test is non-vacuous)",
            door_dist < window_dist,
            f"door_dist={door_dist} window_dist={window_dist}",
        )

    if FAILURES:
        print(f"\n[FAIL] ticket 02 (draw overlay wiring) NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 02 (draw overlay: alignment wiring) satisfied")


main()
