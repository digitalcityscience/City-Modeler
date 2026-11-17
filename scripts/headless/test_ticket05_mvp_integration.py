"""copy-paste-object-behaviour ticket 05 (MVP integration and release
validation).

Ticket 05 is deliberately not new logic - tickets 01-04 (alignment geometry
engine, Draw overlay, Stamp overlay, Align Objects operator) are already each
individually tested (``test_ticket02_alignment_overlay.py``,
``test_ticket03_stamp_overlay.py``, ``test_ticket04_align_objects.py``) and
``shared/alignment_geometry.py`` has its own pure-Python unit suite
(``scripts/tests/unit/test_alignment_geometry.py``). This file instead proves
the one thing none of those did: that the *composition* holds when Align
Objects is run against openings that came from the real, interactive
operators (``cgml.facade_draw`` / ``cgml.semantic_stamp``), not just the
placement kernel called directly - and that Align Objects also behaves
correctly (works, or fails with a clear message) against an *imported*
opening that only has the legacy vertex-identity ring pairing, never an
authored ``cgml_opening_link``.

Checklist covered (docs/agent/copy-paste-object-behavioir_ilkSira/issues/
05-mvp-integration-and-regression.md):

1. "Existing test_facade* headless tests still pass unmodified" - not
   re-asserted here (that's a whole-suite regression run, not a single
   headless script's job); every existing test_facade*.py and
   test_ticket0{2,3,4}*.py was run unmodified against this diff (each
   ``blender --background --factory-startup --python scripts/headless/
   <file>.py``) plus the full ``scripts/tests/unit`` pytest suite (189
   passed). Three pre-existing failures were found and are unchanged before
   and after this diff - all outside this feature's own files (verified via
   ``git log 2df8a9a..HEAD~0 -- <file>`` showing no copy-paste-object-
   behaviour commit ever touched them), so left alone rather than fixed here:
   ``test_facade01_placement_kernel.py``'s "export/re-import preserves
   readable Window glass transparency", ``test_facade04_edit_exit_
   validation.py``'s two ring/opening auto-sync delta checks, and
   ``test_facade06_draw_door_floating.py``'s stale "ground-touching Door
   raises NotImplementedError" expectation (ground-notch Door support has
   since landed elsewhere, on top of an unrelated in-progress branch of
   work).
2. Two Windows placed via the real ``cgml.facade_draw`` operator, then
   ``cgml.align_objects`` between them - both the opening geometry and the
   wall's own interior ring stay paired afterwards.
3. The same, but the two Windows come from the real ``cgml.semantic_stamp``
   operator (captured-source repeated-click placement) instead.
4. Align Objects against a synthetic "imported" pair: two Windows placed via
   the placement kernel (geometrically identical to an authored pair) with
   their ``cgml_opening_link`` stripped from both the fill objects and the
   wall's ring materials, forcing pairing through the legacy vertex-identity
   fallback (``shared/ring_opening_sync.py`` pass 2) - proving both the
   underlying ``align_source_to_target`` call and the real
   ``cgml.align_objects`` operator either work (and re-sync via that
   fallback) or fail with a clear, specific message, never a silent no-op.

Not automated here (manual-check items, per the ticket's own instruction not
to pad this with brittle tests):

- The overlay's actual GPU/``blf`` look-and-feel while drawing, and how it
  *feels* to click through Draw -> Stamp -> Align Objects in one live
  session - none of this is exercisable in ``--background`` mode (no real
  3D viewport region); ticket 02/03's own headless tests already made the
  same call for the overlay's visual half. Recommend a manual pass in
  Blender: draw two Windows near each other, watch the alignment guides
  update live, then right-click -> Align Objects and confirm the snap feels
  right and the context-menu entry is where you'd expect it.

Usage:
    SP=/tmp/cgml-probe-ticket05
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket05_mvp_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import addon_utils
import bmesh
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


def select_face_in_edit_mode(obj, face_index):
    """Select exactly one face and leave ``obj`` in Edit Mode - the same
    headless-reliable pattern ``test_facade03_draw_window_ui.py`` uses."""
    bpy.context.view_layer.objects.active = obj
    for o in bpy.data.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.polygons[face_index].select = True
    bpy.ops.object.mode_set(mode='EDIT')


def top_z(obj):
    return max((obj.matrix_world @ v.co).z for v in obj.data.vertices)


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
        pairs_for_wall_children,
    )

    def check_top_edges_match(label, source, target):
        check(
            label,
            abs(top_z(source) - top_z(target)) < 1e-4,
            f"source top={top_z(source)} target top={top_z(target)}",
        )

    def check_pair_synced(label, wall, opening):
        pair = next((p for p in pairs_for_wall_children(wall) if p.opening is opening), None)
        check(label, pair is not None and pair.in_sync, f"pair={pair}")
        return pair

    # ======================================================================
    # 1. Two Draw-placed Windows -> Align Objects (real cgml.facade_draw +
    #    cgml.align_objects operators, not the kernel called directly).
    # ======================================================================
    draw_wall = make_wall("draw_wall", [(0, 0, 0), (10, 0, 0), (10, 0, 4), (0, 0, 4)])

    select_face_in_edit_mode(draw_wall, 0)
    objs_before = {o.name for o in bpy.data.objects}
    result = bpy.ops.cgml.facade_draw(
        'EXEC_DEFAULT', semantic_type='Window', position_uv=(0.30, 0.50), width=1.0, height=1.0,
    )
    check("Draw Window #1 finishes", result == {'FINISHED'}, str(result))
    draw_source = next((o for o in bpy.data.objects if o.name not in objs_before), None)
    check("Draw Window #1 created a fill object", draw_source is not None)
    bpy.ops.object.mode_set(mode='OBJECT')

    select_face_in_edit_mode(draw_wall, 0)
    objs_before = {o.name for o in bpy.data.objects}
    result = bpy.ops.cgml.facade_draw(
        'EXEC_DEFAULT', semantic_type='Window', position_uv=(0.60, 0.575), width=1.0, height=0.6,
    )
    check("Draw Window #2 finishes", result == {'FINISHED'}, str(result))
    draw_target = next((o for o in bpy.data.objects if o.name not in objs_before), None)
    check("Draw Window #2 created a fill object", draw_target is not None)
    bpy.ops.object.mode_set(mode='OBJECT')

    if draw_source is not None and draw_target is not None:
        check("Draw Window #1 carries an opening link", bool(draw_source.get(OPENING_LINK_KEY)))
        check("Draw Window #2 carries an opening link", bool(draw_target.get(OPENING_LINK_KEY)))

        select_only(draw_source, draw_target)  # target active (Blender's snap-to-active convention)
        check("Align Objects poll() accepts the two Draw-placed openings",
              bpy.ops.cgml.align_objects.poll())

        op_result = bpy.ops.cgml.align_objects('EXEC_DEFAULT')
        check("Align Objects finishes against the Draw-placed pair", op_result == {'FINISHED'}, str(op_result))

        check_top_edges_match(
            "Draw pair: source's top edge now matches target's top edge (opening geometry moved)",
            draw_source, draw_target,
        )
        check_pair_synced(
            "Draw pair: source's wall ring was found and stayed paired (re-cut in sync)",
            draw_wall, draw_source,
        )

    # ======================================================================
    # 2. Two Stamp-placed Windows -> Align Objects (real
    #    cgml.set_semantic_stamp_source + cgml.semantic_stamp +
    #    cgml.align_objects operators).
    # ======================================================================
    stamp_wall = make_wall("stamp_wall", [(-4, 5, 0), (4, 5, 0), (4, 5, 4), (-4, 5, 4)])  # width 8, height 4

    stamp_source_mesh = bpy.data.meshes.new("stamp_source_mesh")
    verts = [
        (-0.5, -0.1, -0.3), (0.5, -0.1, -0.3), (0.5, -0.1, 0.3), (-0.5, -0.1, 0.3),
        (-0.5, 0.1, -0.3), (0.5, 0.1, -0.3), (0.5, 0.1, 0.3), (-0.5, 0.1, 0.3),
    ]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    stamp_source_mesh.from_pydata(verts, [], faces)
    stamp_source = bpy.data.objects.new("stamp_source_window", stamp_source_mesh)
    bpy.context.scene.collection.objects.link(stamp_source)
    stamp_source["cgml3_feature"] = "Window"
    stamp_source["gml_id"] = "stamp_source_window_ID"
    stamp_source["width"] = 1.0
    stamp_source["height"] = 0.6
    stamp_source_material = bpy.data.materials.new("stamp_source_window_material")
    stamp_source_material["gml_polygon_id"] = "stamp_source_window_POLY"
    stamp_source_mesh.materials.append(stamp_source_material)

    stamp_source.select_set(True)
    bpy.context.view_layer.objects.active = stamp_source
    check("set_semantic_stamp_source captures the source Window",
          bpy.ops.cgml.set_semantic_stamp_source() == {'FINISHED'})

    select_only(stamp_wall)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(stamp_wall.data)
    for face in bm.faces:
        face.select = True
    bmesh.update_edit_mesh(stamp_wall.data)

    # Side-by-side (same v, u offset by the source's own width plus a 0.2 m gap:
    # since AB-17 an opening may not touch another one's ring) - mirrors
    # test_ticket03_stamp_overlay.py: predominantly horizontal displacement ->
    # "beside" -> vertical match, top/bottom tie broken to top.
    first_uv = (0.30, 0.50)
    second_uv = (0.30 + (1.0 + 0.2) / 8.0, 0.50)

    stamp_result_1 = bpy.ops.cgml.semantic_stamp(position_uv=first_uv)
    check("Semantic Stamp click #1 finishes", stamp_result_1 == {'FINISHED'}, str(stamp_result_1))
    stamp_result_2 = bpy.ops.cgml.semantic_stamp(position_uv=second_uv)
    check("Semantic Stamp click #2 finishes", stamp_result_2 == {'FINISHED'}, str(stamp_result_2))
    bpy.ops.object.mode_set(mode='OBJECT')

    stamped = [
        o for o in bpy.data.objects
        if o is not stamp_source and o.parent is stamp_wall and o.get("cgml_source") == "semantic_stamp"
    ]
    check("both Semantic Stamp clicks produced independent copies", len(stamped) == 2, f"got {len(stamped)}")

    if len(stamped) == 2:
        stamp_a, stamp_b = stamped
        # Sort by world X so "source"/"target" is deterministic regardless of
        # dict/creation order.
        stamp_source_opening, stamp_target_opening = sorted(
            (stamp_a, stamp_b), key=lambda o: o.matrix_world.translation.x,
        )

        select_only(stamp_source_opening, stamp_target_opening)
        check("Align Objects poll() accepts the two Stamp-placed openings",
              bpy.ops.cgml.align_objects.poll())

        op_result = bpy.ops.cgml.align_objects('EXEC_DEFAULT')
        check("Align Objects finishes against the Stamp-placed pair", op_result == {'FINISHED'}, str(op_result))

        check_top_edges_match(
            "Stamp pair: source's top edge now matches target's top edge (opening geometry moved)",
            stamp_source_opening, stamp_target_opening,
        )
        check_pair_synced(
            "Stamp pair: source's wall ring was found and stayed paired (re-cut in sync)",
            stamp_wall, stamp_source_opening,
        )

    # ======================================================================
    # 3. Align Objects against an "imported" pair - geometrically identical
    #    to an authored beside pair, but with cgml_opening_link stripped from
    #    both fill objects and the wall's ring materials, so pairing can only
    #    succeed through the legacy vertex-identity fallback (the same path
    #    a real CityGML import - which never mints that link - relies on).
    # ======================================================================
    def strip_opening_links(wall, *fills):
        """Simulate a genuine CityGML import, which never mints
        cgml_opening_link at all (ADR 0004 D2 only applies to authoring) -
        clears the link from both a set of fill objects and every ring
        material on their host wall."""
        for fill in fills:
            if OPENING_LINK_KEY in fill.keys():
                del fill[OPENING_LINK_KEY]
        for mat in wall.data.materials:
            if mat.get(OPENING_LINK_KEY):
                del mat[OPENING_LINK_KEY]

    def place_legacy_pair(wall_name):
        wall = make_wall(wall_name, [(0, 0, 0), (10, 0, 0), (10, 0, 4), (0, 0, 4)])
        ctx = resolve_surface_context(wall, 0)
        source = place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=1.0, height=1.0),
            ctx, PlacementRequest(position_uv=(0.30, 0.50)),
        )
        target = place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=1.0, height=0.6),
            ctx, PlacementRequest(position_uv=(0.60, 0.575)),
        )
        strip_opening_links(wall, source, target)
        return wall, source, target

    # -- 3a. The underlying align_source_to_target call directly, so a
    #    failure's message text can be inspected (an operator's self.report()
    #    text isn't retrievable from a headless EXEC_DEFAULT call). --------
    legacy_wall, legacy_source, legacy_target = place_legacy_pair("legacy_wall")

    check(
        "legacy fixture: neither fill carries an opening link (genuinely link-less, not vacuous)",
        not legacy_source.get(OPENING_LINK_KEY) and not legacy_target.get(OPENING_LINK_KEY),
    )
    check_pair_synced(
        "legacy fixture: the vertex-identity fallback alone still pairs the source with its ring",
        legacy_wall, legacy_source,
    )

    legacy_source_top_before = top_z(legacy_source)
    try:
        legacy_result = align_source_to_target(legacy_source, legacy_target)
    except ValueError as e:
        message = str(e)
        check(
            "legacy (link-less) pair: align_source_to_target fails with a clear, specific "
            "message (not a silent no-op)",
            bool(message.strip()) and (
                legacy_source.name in message or legacy_target.name in message
                or "diagonal" in message or "WallSurface" in message
            ),
            message,
        )
    else:
        check("legacy (link-less) pair: align_source_to_target returns a real result, not None",
              legacy_result is not None)
        check(
            "legacy (link-less) pair: the source opening's geometry actually moved (not a silent no-op)",
            abs(top_z(legacy_source) - legacy_source_top_before) > 1e-6,
            f"top before={legacy_source_top_before} after={top_z(legacy_source)}",
        )
        check_top_edges_match(
            "legacy (link-less) pair: source's top edge now matches target's top edge",
            legacy_source, legacy_target,
        )
        check("legacy (link-less) pair: align_source_to_target reports the ring as synced", legacy_result.synced)
        check_pair_synced(
            "legacy (link-less) pair: still paired (via the same vertex-identity fallback) and in sync after align",
            legacy_wall, legacy_source,
        )
        check(
            "legacy (link-less) pair: still no opening link was minted by the align (stays a genuine legacy pair)",
            not legacy_source.get(OPENING_LINK_KEY) and not legacy_target.get(OPENING_LINK_KEY),
        )

    # -- 3b. The same link-less scenario through the real cgml.align_objects
    #    operator (poll + EXEC_DEFAULT) - proves the user-facing operator
    #    itself (not just the function it wraps) handles an imported opening,
    #    matching how parts 1/2 above exercise the real operators. ---------
    op_legacy_wall, op_legacy_source, op_legacy_target = place_legacy_pair("op_legacy_wall")

    select_only(op_legacy_source, op_legacy_target)
    check(
        "Align Objects poll() accepts a link-less (imported-style) opening pair",
        bpy.ops.cgml.align_objects.poll(),
    )
    op_result = bpy.ops.cgml.align_objects('EXEC_DEFAULT')
    check(
        "cgml.align_objects operator finishes cleanly against a link-less pair "
        "(FINISHED, not a silent no-op or crash)",
        op_result == {'FINISHED'}, str(op_result),
    )
    check_top_edges_match(
        "operator run: link-less source's top edge now matches target's top edge",
        op_legacy_source, op_legacy_target,
    )
    check_pair_synced(
        "operator run: link-less source's wall ring was found and stayed paired (re-cut in sync)",
        op_legacy_wall, op_legacy_source,
    )

    if FAILURES:
        print(f"\n[FAIL] ticket 05 (MVP integration and regression) NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 05 (MVP integration and regression) satisfied")


main()
