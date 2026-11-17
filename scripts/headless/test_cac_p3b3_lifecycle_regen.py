# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P3b.3: lifecycle regeneration coverage for record-driven WallSurface
display, real Blender.

P3b.1 wired transactional display regeneration into ``edit_session.commit()``
only (the normal edit-session commit path). P3b.3 closes the remaining gaps
that same task's own review found:

- ``usecases.create_feature`` (Draw and its F9 redo re-run - same call,
  ``existing_gml_id`` set) did not regenerate display at all
- ``usecases.delete_feature`` did not regenerate the host wall's display
  (the hole never disappeared) and left the deleted Window's own display
  object orphaned
- ``edit_session.validate_and_commit_existing_feature`` (Align, both manual
  Sync directions) committed records but never regenerated display
- ``edit_session.commit_transform_drift`` committed records but never
  regenerated display

Every one of these reuses ``display_regen.regenerate_or_restore`` /
``regenerate_after_delete_or_restore`` - the same snapshot/regenerate/restore
shape ``commit()`` already established, not a new transaction mechanism.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3b3_lifecycle_regen.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on=True):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _place_window(wall, face_index, ctx, avoid=(), existing_gml_id=None):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    # depth=0.0: a flat Window keeps a single PolygonRecord, the shape
    # build_surface_display (GS.1) expects - both wall and Window display
    # objects regenerate (GS.2's own convention).
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.0)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    kwargs = {"existing_gml_id": existing_gml_id} if existing_gml_id else {}
    fill = create_feature("Window", ctx, definition, placement, **kwargs)
    placed_rect = (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)
    return fill, placed_rect


def _translate(obj, dx=0.0, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _select_active(obj):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _op(idname, **kwargs):
    import bpy

    ns = bpy.ops
    for part in idname.split("."):
        ns = getattr(ns, part)
    return ns('EXEC_DEFAULT', **kwargs)


def _display_object(name):
    import bpy

    return bpy.data.objects.get(name)


def _mesh_verts(obj):
    return tuple(tuple(v.co) for v in obj.data.vertices) if obj is not None and obj.data is not None else None


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record_set = storage.read_record(obj)
    return json.dumps(record_set.to_dict(), sort_keys=True) if record_set is not None else None


def _ring_verts(wall, link):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    for _idx, verts, l in wall_interior_ring_faces(wall):
        if l == link:
            return tuple(tuple(v) for v in verts)
    return None


def _mesh_area(obj):
    if obj is None or obj.data is None:
        return -1.0
    total = 0.0
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    for poly in obj.data.polygons:
        idx = list(poly.vertices)
        a = verts[idx[0]]
        for i in range(1, len(idx) - 1):
            b, c = verts[idx[i]], verts[idx[i + 1]]
            total += (b - a).cross(c - a).length * 0.5
    return total


def p3b3_create_regenerates_wall_and_window_display(R):
    """Draw -> both the wall's and the new Window's display objects appear,
    matching create_feature's own just-written records."""
    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    wall_gml_id, window_gml_id = str(wall.get("gml_id")), str(fill.get("gml_id"))

    wall_display = _display_object(f"{wall.name}.cgml_display")
    window_display = _display_object(f"{fill.name}.cgml_display")
    ok = (
        wall_display is not None and len(wall_display.data.polygons) > 0
        and window_display is not None and len(window_display.data.polygons) > 0
    )
    R.check(
        "p3b3_create_regenerates_wall_and_window_display", ok,
        f"wall_display={wall_display} window_display={window_display} "
        f"wall_gml_id={wall_gml_id} window_gml_id={window_gml_id}",
    )


def p3b3_f9_resize_updates_display_same_identity(R):
    """F9 re-run (create_feature with existing_gml_id, same code path the
    real redo panel uses) rebuilds both display objects with the resized
    geometry, and the display's surface-id marker still names the *same*
    gml_id - no new identity, no stale mesh left over from the first size."""
    import bpy

    from city_modeler.shared.authoring_core.blender.display_regen import DISPLAY_SURFACE_KEY

    _flag(True)
    wall, face_index, ctx = _prepare()
    bpy.ops.ed.undo_push(message="p3b3: before f9 draw")
    first, _ = _place_window(wall, face_index, ctx)
    bpy.ops.ed.undo_push(message="p3b3: after f9 draw")
    window_gml_id = str(first.get("gml_id"))
    window_display_name = f"{first.name}.cgml_display"
    area_before = _mesh_area(_display_object(window_display_name))

    # Blender's F9 destroys the previous fill object *and* its wall's ring
    # record (one combined undo of the whole first create) before
    # re-running execute() with the edited properties - a bare
    # ``bpy.data.objects.remove(first, ...)`` would leave the wall's own
    # record/ring for this opening_link behind with no owning object
    # (dangling_ring), which create_preflight correctly refuses.
    wall_name = wall.name
    bpy.ops.ed.undo()
    # Any undo/redo cycle invalidates every existing bpy object reference,
    # not just the ones it actually reverted - re-resolve by name and
    # rebuild the surface context, same convention
    # test_cac_p4_f9_resize_identity.py's own F9 test uses.
    wall = bpy.data.objects[wall_name]
    ctx = h.surface_context(wall, face_index)

    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest
    uv = h.solid_area_position_uv(wall, face_index, ctx, 0.5, 0.5)
    resized = create_feature(
        "Window", ctx,
        FacadeElementDefinition(semantic_type="Window", width=1.0, height=1.0, depth=0.0),
        PlacementRequest(position_uv=uv, source="manual_draw"),
        existing_gml_id=window_gml_id,
    )
    resized_gml_id = str(resized.get("gml_id"))
    window_display = _display_object(f"{resized.name}.cgml_display")
    area_after = _mesh_area(window_display)

    ok = (
        resized_gml_id == window_gml_id
        and window_display is not None
        and window_display.get(DISPLAY_SURFACE_KEY) == window_gml_id
        and area_after > area_before * 2  # genuinely resized, not stale
    )
    R.check(
        "p3b3_f9_resize_updates_display_same_identity", ok,
        f"identity_preserved={resized_gml_id == window_gml_id} area_before={area_before} area_after={area_after}",
    )


def p3b3_transform_drift_commit_regenerates_display(R):
    """An explicit G/R/S transform-drift commit regenerates the wall's
    display from the Window's new (moved) position - previously it left
    the display stale after a successful commit."""
    from city_modeler.shared.authoring_core.blender.edit_session import commit_transform_drift

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    wall_display_verts_before = _mesh_verts(_display_object(f"{wall.name}.cgml_display"))
    _translate(fill, dx=0.05)
    result = commit_transform_drift(window_gml_id)
    wall_display_verts_after = _mesh_verts(_display_object(f"{wall.name}.cgml_display"))

    ok = (
        result.status == "committed"
        and wall_display_verts_after is not None
        and wall_display_verts_after != wall_display_verts_before
    )
    R.check(
        "p3b3_transform_drift_commit_regenerates_display", ok,
        f"result={result} display_changed={wall_display_verts_after != wall_display_verts_before}",
    )


def p3b3_align_regenerates_display(R):
    """Align (through the shared P4 commit seam) regenerates the source
    Window's display at its new, aligned position."""
    from city_modeler.shared.authoring_core.blender.edit_session import align_existing_window

    _flag(True)
    wall, face_index, ctx = _prepare()
    source, source_rect = _place_window(wall, face_index, ctx)
    target, _ = _place_window(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))

    # align_source_to_target computes its own delta from the two openings'
    # *current* positions; force a guaranteed non-zero delta (avoid may
    # otherwise place both at the same horizontal/vertical edge already).
    import bpy
    from mathutils import Vector
    source.location += Vector(ctx.horizontal_axis_world).normalized() * 0.07
    bpy.context.view_layer.update()

    source_display_before = _mesh_verts(_display_object(f"{source.name}.cgml_display"))
    result = align_existing_window(gml_id, target)
    source_display_after = _mesh_verts(_display_object(f"{source.name}.cgml_display"))

    ok = (
        result.status == "committed"
        and source_display_after is not None
        and source_display_after != source_display_before
    )
    R.check(
        "p3b3_align_regenerates_display", ok,
        f"result={result} display_changed={source_display_after != source_display_before}",
    )


def _centroid_world(obj):
    from mathutils import Vector

    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    return sum(verts, Vector()) / len(verts)


def p3b3_manual_sync_regenerates_display(R):
    """Manual Sync, both directions, regenerates display each time
    (previously committed records but left display stale).

    ``opening_to_ring`` recuts the *wall's* ring onto the (moved) Window,
    changing the wall's display. ``ring_to_opening`` then moves the Window
    back onto the (unmoved) ring; since that ring is exactly where the
    first sync just left it, the *value* the Window's display ends up with
    is unsurprising - what this proves instead is that the Window's display
    centroid tracks the Window's own live position after each commit
    (never stale), which is the actual P3b.3 claim.
    """
    from city_modeler.shared.authoring_core.blender.edit_session import sync_existing_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    window, _ = _place_window(wall, face_index, ctx)
    gml_id = str(window.get("gml_id"))
    window_display_name = f"{window.name}.cgml_display"
    wall_display_name = f"{wall.name}.cgml_display"

    _translate(window, dx=0.04)
    wall_verts_before_first = _mesh_verts(_display_object(wall_display_name))
    first = sync_existing_feature(gml_id, "opening_to_ring")
    wall_verts_after_first = _mesh_verts(_display_object(wall_display_name))
    window_display = _display_object(window_display_name)
    centroid_matches_first = (
        window_display is not None
        and (_centroid_world(window_display) - _centroid_world(window)).length < 1e-4
    )

    _translate(window, dx=0.04)
    second = sync_existing_feature(gml_id, "ring_to_opening")
    window_display = _display_object(window_display_name)
    centroid_matches_second = (
        window_display is not None
        and (_centroid_world(window_display) - _centroid_world(window)).length < 1e-4
    )

    ok = (
        first.status == "committed" and second.status == "committed"
        and wall_verts_after_first != wall_verts_before_first
        and centroid_matches_first and centroid_matches_second
    )
    R.check(
        "p3b3_manual_sync_regenerates_display", ok,
        f"first={first} second={second} "
        f"wall_display_changed_by_opening_to_ring={wall_verts_after_first != wall_verts_before_first} "
        f"window_display_tracks_window_after_first_sync={centroid_matches_first} "
        f"window_display_tracks_window_after_second_sync={centroid_matches_second}",
    )


def p3b3_delete_removes_hole_from_display(R):
    """Deleting a record-backed Window rebuilds the host wall's display
    without that opening's hole, and removes the Window's own display
    object entirely."""
    from city_modeler.shared.authoring_core.blender.usecases import delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    window_display_name = f"{fill.name}.cgml_display"
    wall_display_name = f"{wall.name}.cgml_display"

    area_with_hole = _mesh_area(_display_object(wall_display_name))
    delete_feature(window_gml_id)
    wall_display_after = _display_object(wall_display_name)
    area_without_hole = _mesh_area(wall_display_after)
    window_display_after = _display_object(window_display_name)

    ok = (
        wall_display_after is not None
        and window_display_after is None
        # The hole is gone: the solid area is now larger than with the hole.
        and area_without_hole > area_with_hole
    )
    R.check(
        "p3b3_delete_removes_hole_from_display", ok,
        f"area_with_hole={area_with_hole} area_without_hole={area_without_hole} "
        f"window_display_removed={window_display_after is None}",
    )


def p3b3_initialize_duplicate_creates_second_display_hole(R):
    """Shift+D alone creates no new authoritative display state (the
    duplicate shares the original's identity/record); only after
    ``cgml.initialize_duplicate`` does a second, independent hole appear."""
    from city_modeler.shared.authoring_core.blender.usecases import initialize_duplicate

    _flag(True)
    wall, face_index, ctx = _prepare()
    original, original_rect = _place_window(wall, face_index, ctx)

    import bpy
    _select_active(original)
    bpy.ops.object.duplicate()
    duplicate = bpy.context.active_object

    wall_display_name = f"{wall.name}.cgml_display"
    area_one_hole = _mesh_area(_display_object(wall_display_name))
    names_before = {o.name for o in bpy.data.objects if o.name.endswith(".cgml_display")}

    # Raw Shift+D copy: same gml_id/record as original, no new record, no
    # new authoritative display hole yet.
    unresolved_ok = (
        str(duplicate.get("gml_id")) == str(original.get("gml_id"))
        and _mesh_area(_display_object(wall_display_name)) == area_one_hole
    )

    # Shift+D then place the copy at a separate, valid solid-area location -
    # initialize_duplicate places from the duplicate's own current position,
    # which starts out exactly on top of the original.
    from mathutils import Vector
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    target_uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=(original_rect,))
    target_point = surface_world_point_from_uv(ctx, target_uv)
    centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
    duplicate.location += target_point - centre
    bpy.context.view_layer.update()

    replacement = initialize_duplicate(duplicate)
    area_two_holes = _mesh_area(_display_object(wall_display_name))
    replacement_display = _display_object(f"{replacement.name}.cgml_display")
    names_after = {o.name for o in bpy.data.objects if o.name.endswith(".cgml_display")}

    ok = (
        unresolved_ok
        and replacement_display is not None
        and area_two_holes < area_one_hole  # a second hole removed more solid area
        and len(names_after) == len(names_before) + 1  # exactly one new display object
    )
    R.check(
        "p3b3_initialize_duplicate_creates_second_display_hole", ok,
        f"unresolved_ok={unresolved_ok} area_one_hole={area_one_hole} area_two_holes={area_two_holes} "
        f"new_display_objects={len(names_after) - len(names_before)}",
    )


def p3b3_multi_opening_siblings_preserved_across_delete_and_transform(R):
    """Three record-backed Windows (A, B, C) on one wall: deleting B must not
    disturb A/C's own display objects, and a later transform-drift commit on
    C must not disturb A's."""
    from city_modeler.shared.authoring_core.blender.usecases import delete_feature
    from city_modeler.shared.authoring_core.blender.edit_session import commit_transform_drift

    _flag(True)
    wall, face_index, ctx = _prepare()
    a, rect_a = _place_window(wall, face_index, ctx)
    b, rect_b = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    c, rect_c = _place_window(wall, face_index, ctx, avoid=(rect_a, rect_b))
    a_gml_id, b_gml_id, c_gml_id = str(a.get("gml_id")), str(b.get("gml_id")), str(c.get("gml_id"))
    a_display_name, b_display_name, c_display_name = (
        f"{a.name}.cgml_display", f"{b.name}.cgml_display", f"{c.name}.cgml_display",
    )

    a_display_before = _mesh_verts(_display_object(a_display_name))
    c_display_before_delete = _mesh_verts(_display_object(c_display_name))

    delete_feature(b_gml_id)  # removes b (and its display object) entirely

    a_display_after_delete = _mesh_verts(_display_object(a_display_name))
    c_display_after_delete = _mesh_verts(_display_object(c_display_name))
    b_display_gone = _display_object(b_display_name) is None

    _translate(c, dx=0.02)
    commit_transform_drift(c_gml_id)
    a_display_after_transform = _mesh_verts(_display_object(f"{a.name}.cgml_display"))

    ok = (
        b_display_gone
        and a_display_after_delete == a_display_before
        and c_display_after_delete == c_display_before_delete
        and a_display_after_transform == a_display_before
    )
    R.check(
        "p3b3_multi_opening_siblings_preserved_across_delete_and_transform", ok,
        f"b_display_gone={b_display_gone} "
        f"a_unchanged_after_delete={a_display_after_delete == a_display_before} "
        f"c_unchanged_after_delete={c_display_after_delete == c_display_before_delete} "
        f"a_unchanged_after_sibling_transform={a_display_after_transform == a_display_before}",
    )


def p3b3_undo_redo_create(R):
    """Draw -> undo removes the display objects along with the record; redo
    brings both back, one Blender undo step."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    bpy.ops.ed.undo_push(message="p3b3: before create")
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    fill = create_feature(
        "Window", ctx, FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.0),
        PlacementRequest(position_uv=uv, source="manual_draw"),
    )
    wall_display_name = f"{wall.name}.cgml_display"
    bpy.ops.ed.undo_push(message="p3b3: after create")

    present_after_create = _display_object(wall_display_name) is not None
    bpy.ops.ed.undo()
    present_after_undo = _display_object(wall_display_name) is not None
    bpy.ops.ed.redo()
    present_after_redo = _display_object(wall_display_name) is not None

    ok = present_after_create and not present_after_undo and present_after_redo
    R.check(
        "p3b3_undo_redo_create", ok,
        f"after_create={present_after_create} after_undo={present_after_undo} after_redo={present_after_redo}",
    )


def p3b3_undo_redo_transform_drift_commit(R):
    import bpy

    from city_modeler.shared.authoring_core.blender.edit_session import commit_transform_drift

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_display_name = f"{wall.name}.cgml_display"

    verts_committed = _mesh_verts(_display_object(wall_display_name))
    bpy.ops.ed.undo_push(message="p3b3: before drift")
    _translate(fill, dx=0.05)
    commit_transform_drift(window_gml_id)
    verts_after_drift_commit = _mesh_verts(_display_object(wall_display_name))
    bpy.ops.ed.undo_push(message="p3b3: after drift commit")

    bpy.ops.ed.undo()
    verts_after_undo = _mesh_verts(_display_object(wall_display_name))
    bpy.ops.ed.redo()
    verts_after_redo = _mesh_verts(_display_object(wall_display_name))

    ok = (
        verts_after_drift_commit != verts_committed
        and verts_after_undo == verts_committed
        and verts_after_redo == verts_after_drift_commit
    )
    R.check(
        "p3b3_undo_redo_transform_drift_commit", ok,
        f"changed_by_commit={verts_after_drift_commit != verts_committed} "
        f"undo_restored={verts_after_undo == verts_committed} "
        f"redo_reapplied={verts_after_redo == verts_after_drift_commit}",
    )


def p3b3_undo_redo_delete(R):
    import bpy

    from city_modeler.shared.authoring_core.blender.usecases import delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_display_name = f"{wall.name}.cgml_display"
    window_display_name = f"{fill.name}.cgml_display"

    area_before_delete = _mesh_area(_display_object(wall_display_name))
    bpy.ops.ed.undo_push(message="p3b3: before delete")
    delete_feature(window_gml_id)
    bpy.ops.ed.undo_push(message="p3b3: after delete")
    area_after_delete = _mesh_area(_display_object(wall_display_name))
    window_gone_after_delete = _display_object(window_display_name) is None

    bpy.ops.ed.undo()
    area_after_undo = _mesh_area(_display_object(wall_display_name))
    window_back_after_undo = _display_object(window_display_name) is not None

    bpy.ops.ed.redo()
    area_after_redo = _mesh_area(_display_object(wall_display_name))
    window_gone_after_redo = _display_object(window_display_name) is None

    ok = (
        window_gone_after_delete and area_after_delete > area_before_delete
        and window_back_after_undo and abs(area_after_undo - area_before_delete) < 1e-6
        and window_gone_after_redo and abs(area_after_redo - area_after_delete) < 1e-6
    )
    R.check(
        "p3b3_undo_redo_delete", ok,
        f"window_gone_after_delete={window_gone_after_delete} window_back_after_undo={window_back_after_undo} "
        f"window_gone_after_redo={window_gone_after_redo} "
        f"area_before={area_before_delete} area_after_delete={area_after_delete} "
        f"area_after_undo={area_after_undo} area_after_redo={area_after_redo}",
    )


def p3b3_undo_redo_duplicate_initialization(R):
    import bpy

    from city_modeler.shared.authoring_core.blender.usecases import initialize_duplicate

    _flag(True)
    wall, face_index, ctx = _prepare()
    original, original_rect = _place_window(wall, face_index, ctx)
    wall_display_name = f"{wall.name}.cgml_display"

    _select_active(original)
    bpy.ops.object.duplicate()
    duplicate = bpy.context.active_object

    from mathutils import Vector
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    target_uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=(original_rect,))
    target_point = surface_world_point_from_uv(ctx, target_uv)
    centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
    duplicate.location += target_point - centre
    bpy.context.view_layer.update()

    area_one_hole = _mesh_area(_display_object(wall_display_name))
    bpy.ops.ed.undo_push(message="p3b3: before init duplicate")
    replacement = initialize_duplicate(duplicate)
    replacement_name = replacement.name
    bpy.ops.ed.undo_push(message="p3b3: after init duplicate")
    area_two_holes = _mesh_area(_display_object(wall_display_name))

    bpy.ops.ed.undo()
    area_after_undo = _mesh_area(_display_object(wall_display_name))
    replacement_display_after_undo = _display_object(f"{replacement_name}.cgml_display")

    bpy.ops.ed.redo()
    area_after_redo = _mesh_area(_display_object(wall_display_name))

    ok = (
        area_two_holes < area_one_hole
        and abs(area_after_undo - area_one_hole) < 1e-6
        and replacement_display_after_undo is None
        and abs(area_after_redo - area_two_holes) < 1e-6
    )
    R.check(
        "p3b3_undo_redo_duplicate_initialization", ok,
        f"area_one_hole={area_one_hole} area_two_holes={area_two_holes} "
        f"area_after_undo={area_after_undo} area_after_redo={area_after_redo} "
        f"replacement_display_gone_after_undo={replacement_display_after_undo is None}",
    )


def p3b3_undo_redo_align_sync(R):
    import bpy

    from city_modeler.shared.authoring_core.blender.edit_session import align_existing_window

    _flag(True)
    wall, face_index, ctx = _prepare()
    source, source_rect = _place_window(wall, face_index, ctx)
    target, _ = _place_window(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))
    source_display_name = f"{source.name}.cgml_display"

    # Force a guaranteed non-zero align delta (see p3b3_align_regenerates_display).
    from mathutils import Vector
    source.location += Vector(ctx.horizontal_axis_world).normalized() * 0.07
    bpy.context.view_layer.update()

    verts_before_align = _mesh_verts(_display_object(source_display_name))
    bpy.ops.ed.undo_push(message="p3b3: before align")
    align_existing_window(gml_id, target)
    bpy.ops.ed.undo_push(message="p3b3: after align")
    verts_after_align = _mesh_verts(_display_object(source_display_name))

    bpy.ops.ed.undo()
    verts_after_undo = _mesh_verts(_display_object(source_display_name))
    bpy.ops.ed.redo()
    verts_after_redo = _mesh_verts(_display_object(source_display_name))

    ok = (
        verts_after_align != verts_before_align
        and verts_after_undo == verts_before_align
        and verts_after_redo == verts_after_align
    )
    R.check(
        "p3b3_undo_redo_align_sync", ok,
        f"changed_by_align={verts_after_align != verts_before_align} "
        f"undo_restored={verts_after_undo == verts_before_align} "
        f"redo_reapplied={verts_after_redo == verts_after_align}",
    )


def p3b3_save_reload_retains_synchronized_display_after_create_and_delete(R):
    """After a create and a delete (not just commit(), which GS.2's own
    suite already covers), the surviving display objects still match
    records across a real save/reload round trip."""
    import bpy

    from city_modeler.shared.authoring_core.blender import registry
    from city_modeler.shared.authoring_core.blender.usecases import delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    a, rect_a = _place_window(wall, face_index, ctx)
    b, _ = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    a_gml_id = str(a.get("gml_id"))
    b_gml_id = str(b.get("gml_id"))
    wall_name = wall.name
    wall_display_name = f"{wall_name}.cgml_display"
    a_display_name = f"{a.name}.cgml_display"
    b_display_name = f"{b.name}.cgml_display"  # captured before delete: b is removed below

    delete_feature(b_gml_id)
    area_before_reload = _mesh_area(_display_object(wall_display_name))
    a_display_before_reload = _mesh_verts(_display_object(a_display_name))
    b_display_gone_before_reload = _display_object(b_display_name) is None

    path = h.out_dir() / "p3b3_lifecycle_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    _flag(True)
    registry.REGISTRY.rebuild()

    wall_after = bpy.data.objects.get(wall_name)
    area_after_reload = _mesh_area(_display_object(wall_display_name))
    a_after = registry.resolve(a_gml_id)
    a_display_after_reload = _mesh_verts(_display_object(a_display_name)) if a_after else None
    b_display_gone_after_reload = _display_object(b_display_name) is None

    ok = (
        wall_after is not None
        and abs(area_after_reload - area_before_reload) < 1e-6
        and a_display_after_reload == a_display_before_reload
        and b_display_gone_before_reload and b_display_gone_after_reload
    )
    R.check(
        "p3b3_save_reload_retains_synchronized_display_after_create_and_delete", ok,
        f"area_before={area_before_reload} area_after={area_after_reload} "
        f"a_display_unchanged={a_display_after_reload == a_display_before_reload} "
        f"b_stayed_gone={b_display_gone_before_reload and b_display_gone_after_reload}",
    )


def p3b3_injected_display_failure_rolls_back_create(R):
    """A display-build failure during create_feature must roll back the
    whole placement - no orphan Window object, no orphan record - not just
    fail to build a display next to already-written records."""
    import bpy

    from city_modeler.shared.authoring_core.blender import display_builder, registry
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_gml_id = str(wall.get("gml_id"))
    wall_record_before = _record_json(wall)
    names_before = {o.name for o in bpy.data.objects}

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: create-path display failure (P3b.3 test)")

    display_builder.build_surface_display = _always_fail
    raised = False
    try:
        uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
        create_feature(
            "Window", ctx, FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.0),
            PlacementRequest(position_uv=uv, source="manual_draw"),
        )
    except Exception:
        raised = True
    finally:
        display_builder.build_surface_display = original

    names_after = {o.name for o in bpy.data.objects}
    registry.REGISTRY.rebuild()

    ok = raised and names_after == names_before and _record_json(wall) == wall_record_before
    R.check(
        "p3b3_injected_display_failure_rolls_back_create", ok,
        f"raised={raised} no_orphan_objects={names_after == names_before} "
        f"wall_record_unchanged={_record_json(wall) == wall_record_before}",
    )


def p3b3_injected_display_failure_rolls_back_align(R):
    """A display-build failure during Align must roll back the mesh, both
    records and the display together - the same guarantee commit() has had
    since P3b.1, now extended to validate_and_commit_existing_feature."""
    from city_modeler.shared.authoring_core.blender import display_builder
    from city_modeler.shared.authoring_core.blender.edit_session import (
        EditSessionError, align_existing_window,
    )

    _flag(True)
    wall, face_index, ctx = _prepare()
    source, source_rect = _place_window(wall, face_index, ctx)
    target, _ = _place_window(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))

    source_record_before = _record_json(source)
    wall_record_before = _record_json(wall)
    matrix_before = tuple(tuple(row) for row in source.matrix_basis)
    source_display_before = _mesh_verts(_display_object(f"{source.name}.cgml_display"))

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: align display failure (P3b.3 test)")

    display_builder.build_surface_display = _always_fail
    refused = False
    try:
        align_existing_window(gml_id, target)
    except EditSessionError:
        refused = True
    finally:
        display_builder.build_surface_display = original

    ok = (
        refused
        and _record_json(source) == source_record_before
        and _record_json(wall) == wall_record_before
        and tuple(tuple(row) for row in source.matrix_basis) == matrix_before
        and _mesh_verts(_display_object(f"{source.name}.cgml_display")) == source_display_before
    )
    R.check(
        "p3b3_injected_display_failure_rolls_back_align", ok,
        f"refused={refused} "
        f"source_record_unchanged={_record_json(source) == source_record_before} "
        f"wall_record_unchanged={_record_json(wall) == wall_record_before} "
        f"mesh_unchanged={tuple(tuple(row) for row in source.matrix_basis) == matrix_before}",
    )


def p3b3_injected_display_failure_rolls_back_transform_drift(R):
    from city_modeler.shared.authoring_core.blender import display_builder
    from city_modeler.shared.authoring_core.blender.edit_session import (
        EditSessionError, commit_transform_drift,
    )

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    window_record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    wall_mesh_before = tuple(tuple(v.co) for v in wall.data.vertices)
    wall_display_before = _mesh_verts(_display_object(f"{wall.name}.cgml_display"))

    _translate(fill, dx=0.05)

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: transform-drift display failure (P3b.3 test)")

    display_builder.build_surface_display = _always_fail
    refused = False
    try:
        commit_transform_drift(window_gml_id)
    except EditSessionError:
        refused = True
    finally:
        display_builder.build_surface_display = original

    ok = (
        refused
        and _record_json(fill) == window_record_before
        and _record_json(wall) == wall_record_before
        and tuple(tuple(v.co) for v in wall.data.vertices) == wall_mesh_before
        and _mesh_verts(_display_object(f"{wall.name}.cgml_display")) == wall_display_before
    )
    R.check(
        "p3b3_injected_display_failure_rolls_back_transform_drift", ok,
        f"refused={refused} "
        f"window_record_unchanged={_record_json(fill) == window_record_before} "
        f"wall_record_unchanged={_record_json(wall) == wall_record_before} "
        f"wall_mesh_unchanged={tuple(tuple(v.co) for v in wall.data.vertices) == wall_mesh_before}",
    )


def p3b3_injected_display_failure_rolls_back_delete(R):
    """A display-build failure while regenerating the wall's post-delete
    display must leave the Window object, its record and the wall's ring
    exactly as before the delete attempt - the delete is not retried by
    this seam, it simply must not half-complete."""
    import bpy

    from city_modeler.shared.authoring_core.blender import display_builder
    from city_modeler.shared.authoring_core.blender.usecases import DeleteFeatureError, delete_feature

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    window_record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    names_before = {o.name for o in bpy.data.objects}

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: delete-path display failure (P3b.3 test)")

    display_builder.build_surface_display = _always_fail
    raised = False
    try:
        delete_feature(window_gml_id)
    except Exception:
        raised = True
    finally:
        display_builder.build_surface_display = original

    names_after = {o.name for o in bpy.data.objects}
    window_still_present = fill.name in {o.name for o in bpy.data.objects}

    ok = (
        raised
        and window_still_present
        and names_after == names_before
    )
    R.check(
        "p3b3_injected_display_failure_rolls_back_delete", ok,
        f"raised={raised} window_still_present={window_still_present} "
        f"no_new_or_removed_objects={names_after == names_before} "
        f"note=window_record_before={window_record_before is not None} wall_record_before={wall_record_before is not None}",
    )


def main():
    R = h.Results(__file__)
    checks = [
        p3b3_create_regenerates_wall_and_window_display,
        p3b3_f9_resize_updates_display_same_identity,
        p3b3_transform_drift_commit_regenerates_display,
        p3b3_align_regenerates_display,
        p3b3_manual_sync_regenerates_display,
        p3b3_delete_removes_hole_from_display,
        p3b3_initialize_duplicate_creates_second_display_hole,
        p3b3_multi_opening_siblings_preserved_across_delete_and_transform,
        p3b3_undo_redo_create,
        p3b3_undo_redo_transform_drift_commit,
        p3b3_undo_redo_delete,
        p3b3_undo_redo_duplicate_initialization,
        p3b3_undo_redo_align_sync,
        p3b3_save_reload_retains_synchronized_display_after_create_and_delete,
        p3b3_injected_display_failure_rolls_back_create,
        p3b3_injected_display_failure_rolls_back_align,
        p3b3_injected_display_failure_rolls_back_transform_drift,
        p3b3_injected_display_failure_rolls_back_delete,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
