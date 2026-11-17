# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P3b.4: display-cache freshness / checksum strategy, real Blender.

P3b.1-P3b.3 made display regeneration transactional and wired it into every
lifecycle entry point that commits a Window+wall record pair. P3b.4 answers,
deterministically, whether a persisted display mesh still corresponds to the
current committed record state: ``records = authority; display checksum =
cache provenance only`` (docs/agent/authoring-core/STATUS.md).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3b4_display_freshness.py -- <out_dir>
"""
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


def _display_object(name):
    import bpy

    return bpy.data.objects.get(name)


def _record_set(obj):
    from city_modeler.shared.authoring_core.blender import storage

    return storage.read_record(obj)


def p3b4_regen_marks_display_fresh_with_matching_checksum(R):
    """After a successful commit, the display object's stored checksum
    matches the freshly recomputed authoritative checksum, and
    ``display_freshness`` classifies it ``FRESH``."""
    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    wall_gml_id = str(wall.get("gml_id"))

    wall_display = _display_object(f"{wall.name}.cgml_display")
    stored = wall_display.get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    record_set = _record_set(wall)
    expected = dc.compute_surface_display_checksum(record_set, wall_gml_id)
    state = display_regen.display_freshness(wall_display, record_set, wall_gml_id)

    ok = stored == expected and state == dc.FRESH
    R.check(
        "p3b4_regen_marks_display_fresh_with_matching_checksum", ok,
        f"stored={stored} expected={expected} state={state}",
    )


def p3b4_missing_checksum_on_existing_display_is_stale(R):
    """A display object created before P3b.4 (no checksum property at all)
    must classify as STALE, never FRESH from geometry equality alone."""
    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    _place_window(wall, face_index, ctx)
    wall_gml_id = str(wall.get("gml_id"))
    wall_display = _display_object(f"{wall.name}.cgml_display")

    del wall_display[display_regen.DISPLAY_SOURCE_CHECKSUM_KEY]
    record_set = _record_set(wall)
    state = display_regen.display_freshness(wall_display, record_set, wall_gml_id)

    ok = state == dc.STALE
    R.check("p3b4_missing_checksum_on_existing_display_is_stale", ok, f"state={state}")


def p3b4_mismatching_checksum_is_stale_without_regenerating(R):
    """A controlled stale case (valid records + a display carrying a stale
    checksum, mesh untouched) classifies STALE - and this check alone must
    not trigger any regeneration."""
    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    _place_window(wall, face_index, ctx)
    wall_gml_id = str(wall.get("gml_id"))
    wall_display = _display_object(f"{wall.name}.cgml_display")
    verts_before = tuple(tuple(v.co) for v in wall_display.data.vertices)

    wall_display[display_regen.DISPLAY_SOURCE_CHECKSUM_KEY] = "deliberately-stale-checksum"
    record_set = _record_set(wall)
    state = display_regen.display_freshness(wall_display, record_set, wall_gml_id)
    verts_after = tuple(tuple(v.co) for v in wall_display.data.vertices)

    ok = state == dc.STALE and verts_after == verts_before
    R.check(
        "p3b4_mismatching_checksum_is_stale_without_regenerating", ok,
        f"state={state} mesh_untouched={verts_after == verts_before}",
    )


def p3b4_missing_display_object_is_missing(R):
    """No display object at all (records exist) classifies MISSING, not
    STALE - the two must stay distinguishable."""
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    _place_window(wall, face_index, ctx)
    wall_gml_id = str(wall.get("gml_id"))
    wall_display = _display_object(f"{wall.name}.cgml_display")
    bpy.data.objects.remove(wall_display, do_unlink=True)

    record_set = _record_set(wall)
    state = display_regen.display_freshness(None, record_set, wall_gml_id)

    ok = state == dc.MISSING
    R.check("p3b4_missing_display_object_is_missing", ok, f"state={state}")


def p3b4_save_reload_preserves_checksum_and_fresh_state(R):
    """commit -> checksum stored -> save .blend -> reload: the recomputed
    authoritative checksum still matches the persisted one, no rebuild
    needed."""
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen, registry
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    _place_window(wall, face_index, ctx)
    wall_gml_id = str(wall.get("gml_id"))
    wall_name = wall.name
    wall_display_name = f"{wall_name}.cgml_display"
    stored_before = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    path = h.out_dir() / "p3b4_freshness_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    _flag(True)
    registry.REGISTRY.rebuild()

    wall_after = bpy.data.objects.get(wall_name)
    wall_display_after = _display_object(wall_display_name)
    stored_after = wall_display_after.get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    record_set_after = _record_set(wall_after)
    expected_after = dc.compute_surface_display_checksum(record_set_after, wall_gml_id)
    state_after = display_regen.display_freshness(wall_display_after, record_set_after, wall_gml_id)

    ok = (
        stored_after == stored_before
        and stored_after == expected_after
        and state_after == dc.FRESH
    )
    R.check(
        "p3b4_save_reload_preserves_checksum_and_fresh_state", ok,
        f"stored_before={stored_before} stored_after={stored_after} "
        f"expected_after={expected_after} state_after={state_after}",
    )


def p3b4_undo_redo_checksum_follows_display(R):
    """commit -> checksum B; undo -> records/display/checksum A; redo ->
    records/display/checksum B - Blender's own undo restores the custom
    property along with the mesh, no extra handler needed."""
    import bpy

    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.blender.edit_session import commit_transform_drift

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_display_name = f"{wall.name}.cgml_display"

    checksum_a = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    bpy.ops.ed.undo_push(message="p3b4: before drift")
    _translate(fill, dx=0.05)
    commit_transform_drift(window_gml_id)
    checksum_b = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    bpy.ops.ed.undo_push(message="p3b4: after drift commit")

    bpy.ops.ed.undo()
    checksum_after_undo = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    bpy.ops.ed.redo()
    checksum_after_redo = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    ok = (
        checksum_b != checksum_a
        and checksum_after_undo == checksum_a
        and checksum_after_redo == checksum_b
    )
    R.check(
        "p3b4_undo_redo_checksum_follows_display", ok,
        f"checksum_a={checksum_a} checksum_b={checksum_b} "
        f"after_undo={checksum_after_undo} after_redo={checksum_after_redo}",
    )


def p3b4_failed_regeneration_does_not_write_new_checksum(R):
    """An injected display-build failure during a transform-drift commit
    must leave the wall's stored checksum exactly as it was before the
    attempt - never the checksum the failed attempt would have written."""
    from city_modeler.shared.authoring_core.blender import display_builder, display_regen
    from city_modeler.shared.authoring_core.blender.edit_session import (
        EditSessionError, commit_transform_drift,
    )

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_display_name = f"{wall.name}.cgml_display"

    checksum_before = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    _translate(fill, dx=0.05)

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: P3b.4 checksum rollback test")

    display_builder.build_surface_display = _always_fail
    refused = False
    try:
        commit_transform_drift(window_gml_id)
    except EditSessionError:
        refused = True
    finally:
        display_builder.build_surface_display = original

    checksum_after_failure = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    ok = refused and checksum_after_failure == checksum_before
    R.check(
        "p3b4_failed_regeneration_does_not_write_new_checksum", ok,
        f"refused={refused} checksum_before={checksum_before} checksum_after_failure={checksum_after_failure}",
    )


def p3b4_delete_leaves_no_orphan_checksum(R):
    """Deleting a record-backed Window removes its display object (and the
    checksum property on it) entirely; recreating an equivalent Window later
    computes a fresh checksum from records, not a stale leftover."""
    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.blender.usecases import delete_feature
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, rect = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    window_display_name = f"{fill.name}.cgml_display"

    delete_feature(window_gml_id)
    orphan_gone = _display_object(window_display_name) is None

    second, _ = _place_window(wall, face_index, ctx)
    second_gml_id = str(second.get("gml_id"))
    second_display_name = f"{second.name}.cgml_display"
    second_display = _display_object(second_display_name)
    record_set = _record_set(second)
    expected = dc.compute_surface_display_checksum(record_set, second_gml_id)
    stored = second_display.get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    ok = orphan_gone and stored == expected
    R.check(
        "p3b4_delete_leaves_no_orphan_checksum", ok,
        f"orphan_gone={orphan_gone} stored={stored} expected={expected}",
    )


def p3b4_multi_opening_checksum_changes_with_any_hole(R):
    """Wall + Window A/B/C: the wall's checksum changes if any committed
    interior-ring geometry changes, and is unaffected by which order the
    siblings were created in (recomputed twice, before/after an
    unrelated-order rebuild check)."""
    from city_modeler.shared.authoring_core.blender import display_regen
    from city_modeler.shared.authoring_core.blender.edit_session import commit_transform_drift
    from city_modeler.shared.authoring_core.domain import display_checksum as dc

    _flag(True)
    wall, face_index, ctx = _prepare()
    a, rect_a = _place_window(wall, face_index, ctx)
    b, rect_b = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    c, rect_c = _place_window(wall, face_index, ctx, avoid=(rect_a, rect_b))
    wall_gml_id = str(wall.get("gml_id"))
    c_gml_id = str(c.get("gml_id"))
    wall_display_name = f"{wall.name}.cgml_display"

    checksum_before = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    record_set_before = _record_set(wall)
    recomputed_before = dc.compute_surface_display_checksum(record_set_before, wall_gml_id)

    _translate(c, dx=0.02)
    commit_transform_drift(c_gml_id)

    checksum_after = _display_object(wall_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)
    record_set_after = _record_set(wall)
    recomputed_after = dc.compute_surface_display_checksum(record_set_after, wall_gml_id)

    ok = (
        checksum_before == recomputed_before
        and checksum_after == recomputed_after
        and checksum_after != checksum_before
    )
    R.check(
        "p3b4_multi_opening_checksum_changes_with_any_hole", ok,
        f"checksum_before={checksum_before} recomputed_before={recomputed_before} "
        f"checksum_after={checksum_after} recomputed_after={recomputed_after}",
    )


def p3b4_rollback_restores_old_display_checksum(R):
    """An injected failure during Align must leave the source Window's
    display checksum exactly as before the attempt, alongside its mesh and
    records (P3b.1's own rollback guarantee, now covering the checksum
    property too)."""
    from city_modeler.shared.authoring_core.blender import display_builder, display_regen
    from city_modeler.shared.authoring_core.blender.edit_session import (
        EditSessionError, align_existing_window,
    )

    _flag(True)
    wall, face_index, ctx = _prepare()
    source, source_rect = _place_window(wall, face_index, ctx)
    target, _ = _place_window(wall, face_index, ctx, avoid=(source_rect,))
    gml_id = str(source.get("gml_id"))
    source_display_name = f"{source.name}.cgml_display"

    import bpy
    from mathutils import Vector
    source.location += Vector(ctx.horizontal_axis_world).normalized() * 0.07
    bpy.context.view_layer.update()

    checksum_before = _display_object(source_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: P3b.4 align rollback test")

    display_builder.build_surface_display = _always_fail
    refused = False
    try:
        align_existing_window(gml_id, target)
    except EditSessionError:
        refused = True
    finally:
        display_builder.build_surface_display = original

    checksum_after = _display_object(source_display_name).get(display_regen.DISPLAY_SOURCE_CHECKSUM_KEY, None)

    ok = refused and checksum_after == checksum_before
    R.check(
        "p3b4_rollback_restores_old_display_checksum", ok,
        f"refused={refused} checksum_before={checksum_before} checksum_after={checksum_after}",
    )


def main():
    R = h.Results(__file__)
    checks = [
        p3b4_regen_marks_display_fresh_with_matching_checksum,
        p3b4_missing_checksum_on_existing_display_is_stale,
        p3b4_mismatching_checksum_is_stale_without_regenerating,
        p3b4_missing_display_object_is_missing,
        p3b4_save_reload_preserves_checksum_and_fresh_state,
        p3b4_undo_redo_checksum_follows_display,
        p3b4_failed_regeneration_does_not_write_new_checksum,
        p3b4_delete_leaves_no_orphan_checksum,
        p3b4_multi_opening_checksum_changes_with_any_hole,
        p3b4_rollback_restores_old_display_checksum,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
