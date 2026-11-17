# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Guarded native delete for record-backed objects (CAC-P4.4, D-7a).

Blender has no pre-delete handler, so D-7a's mechanism is a keymap-level
guard: ``cgml.guarded_delete`` (``ops/authoring_core_ops.py``) is bound
ahead of Blender's own ``object.delete`` keymap items for ``X``/``DEL`` in
Object Mode (``register_keymaps``/``unregister_keymaps``, called from
``__init__.py::register``/``unregister``). Whether pressing X in the
viewport actually resolves to our operator ahead of Blender's own binding
cannot be proven headless (``--background`` has no real window manager/
keyconfig - ``register_keymaps`` degrades to a no-op there, verified by
``keymap_registration_is_a_safe_noop_headless`` below); what *is* fully
testable headless is the operator's own partition/route/block logic
(``guards.plan_guarded_delete``), driven directly via
``bpy.ops.cgml.guarded_delete()`` - the same "call the operator, not the
key event" convention every other CAC test file in this suite already
uses (F9's own redo-panel mechanism, P3.4's delete-feature operator, etc).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p4_guarded_delete.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _place_window(wall, face_index, ctx, avoid=(), generator=None):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    kwargs = {"generator": generator} if generator else {}
    fill = create_feature("Window", ctx, definition, placement, **kwargs)
    placed_rect = (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)
    return fill, placed_rect


def _select(objects):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _ring_links(wall):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    return sorted({link for _, _, link in wall_interior_ring_faces(wall) if link})


def _run_validation():
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(None)


def _is_blocking(findings):
    from city_modeler.shared.authoring_core.domain import consistency as c

    return c.is_blocking(findings)


def _legacy_object():
    """A legacy imported Window/Door object under the fixture's Building -
    real geometry that carries no ``_cgml_record`` (the importer never
    writes one), a genuine "no record at all" case distinct from a
    record-backed non-Window kind (the wall, used for the blocking check
    below)."""
    import bpy
    from city_modeler.shared.authoring_core.blender import storage

    for obj in bpy.data.objects:
        if obj.type != "MESH" or storage.has_record(obj):
            continue
        feat = str(obj.get("cgml3_feature") or "").strip().lower()
        if feat in ("window", "windowsurface", "door", "doorsurface"):
            return obj
    return None


def guarded_delete_routes_record_backed_window(R):
    """Acceptance 1: a selection of only record-backed Windows deletes each
    through ``delete_feature`` - same end state as ``cgml.delete_feature``
    itself (P3.4): object gone, ring gone, registry gone, validator clean."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link

    _select([fill])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')

    object_gone = fill_name not in bpy.data.objects
    ring_gone = opening_link not in _ring_links(wall)
    registry_gone = REGISTRY.get(gml_id) is None
    findings = _run_validation()
    validator_clean = not _is_blocking(findings)
    ok = (result == {"FINISHED"} and object_gone and ring_gone
          and registry_gone and validator_clean)
    R.check("guarded_delete_routes_record_backed_window", ok, json.dumps({
        "result": str(result), "object_gone": object_gone, "ring_gone": ring_gone,
        "registry_gone": registry_gone, "validator_clean": validator_clean,
    }))


def guarded_delete_blocks_whole_operation_for_unsupported_kind(R):
    """Acceptance 2: a selection containing a record-backed object with no
    delete use case (the host WallSurface itself - record-backed once a
    Window is placed on it, kind "WallSurface", no delete use case exists
    for it) blocks the *entire* operation, all-or-nothing - the Window in
    the same selection must survive too, not just the wall."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    fill_name, wall_name = fill.name, wall.name
    objects_before = {o.name for o in bpy.data.objects}
    assert storage.has_record(wall), "setup: wall must carry its own record (WallSurface kind)"

    _select([fill, wall])
    try:
        result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')
        error = ""
    except RuntimeError as e:
        # bpy.ops raises RuntimeError for an operator's own {'ERROR'} report
        # regardless of its return value - the refusal signal here, same
        # convention every other CAC test file already uses (e.g. F9's
        # invalid-resize refusal).
        result, error = {"CANCELLED"}, str(e)

    nothing_deleted = {o.name for o in bpy.data.objects} == objects_before
    both_survive = fill_name in bpy.data.objects and wall_name in bpy.data.objects
    ok = result == {"CANCELLED"} and nothing_deleted and both_survive and "WallSurface" in error
    R.check("guarded_delete_blocks_whole_operation_for_unsupported_kind", ok, json.dumps({
        "result": str(result), "error": error,
        "nothing_deleted": nothing_deleted, "both_survive": both_survive,
    }))


def guarded_delete_mixed_selection_routes_and_deletes_natively(R):
    """Acceptance 3: a mixed selection (one record-backed Window, one
    legacy object with no record) in a single guarded-delete call routes
    the Window through ``delete_feature`` and deletes the legacy object
    natively, in the same user action."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link

    legacy = _legacy_object()
    if legacy is None:
        R.check("guarded_delete_mixed_selection_routes_and_deletes_natively", False,
                "setup: no legacy (record-less) Window/Door object found in the fixture")
        return
    legacy_name = legacy.name

    _select([fill, legacy])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')

    fill_gone = fill_name not in bpy.data.objects
    legacy_gone = legacy_name not in bpy.data.objects
    ring_gone = opening_link not in _ring_links(wall)
    ok = result == {"FINISHED"} and fill_gone and legacy_gone and ring_gone
    R.check("guarded_delete_mixed_selection_routes_and_deletes_natively", ok, json.dumps({
        "result": str(result), "fill_gone": fill_gone, "legacy_gone": legacy_gone, "ring_gone": ring_gone,
    }))


def guarded_delete_is_one_combined_undo_step(R):
    """Acceptance 1 (undo-step choice, documented): ``cgml.guarded_delete``
    is one top-level operator call, so Blender's own undo model gives the
    *whole* guarded delete - the routed Window's ``delete_feature`` call
    and the legacy object's native delete together - exactly one undo
    step, not one per object. A single undo restores both; a single redo
    removes both again."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(gml_id).opening_link

    legacy = _legacy_object()
    if legacy is None:
        R.check("guarded_delete_is_one_combined_undo_step", False,
                "setup: no legacy (record-less) Window/Door object found in the fixture")
        return
    legacy_name = legacy.name

    bpy.ops.ed.undo_push(message="cac: before guarded delete")
    _select([fill, legacy])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')
    bpy.ops.ed.undo_push(message="cac: after guarded delete")
    if result != {"FINISHED"}:
        R.check("guarded_delete_is_one_combined_undo_step", False, f"setup delete failed: result={result}")
        return

    bpy.ops.ed.undo()
    wall_after_undo = bpy.data.objects[wall_name]
    both_restored = fill_name in bpy.data.objects and legacy_name in bpy.data.objects
    ring_restored = opening_link in _ring_links(wall_after_undo)

    bpy.ops.ed.redo()
    both_gone_again = fill_name not in bpy.data.objects and legacy_name not in bpy.data.objects

    ok = both_restored and ring_restored and both_gone_again
    R.check("guarded_delete_is_one_combined_undo_step", ok, json.dumps({
        "both_restored": both_restored, "ring_restored": ring_restored, "both_gone_again": both_gone_again,
    }))


def guarded_delete_flag_off_closes_hole(R):
    """Acceptance 4, revised by B55: with the experimental authoring core
    off, ``cgml.guarded_delete`` does no delete_feature routing, but it
    closes the wall's hole before the native ``object.delete`` (a
    record-backed object created while the flag was on, then the flag turned
    off - a scene a user might genuinely have): the object goes and its
    interior ring goes with it. Before B55 the ring was left dangling."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    fill_name = fill.name
    opening_link = storage.read_record(fill).feature(str(fill.get("gml_id"))).opening_link
    ring_before = opening_link in _ring_links(wall)

    _flag(False)
    _select([fill])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')

    object_gone = fill_name not in bpy.data.objects
    ring_left_dangling = opening_link in _ring_links(wall)
    ok = result == {"FINISHED"} and object_gone and ring_before and not ring_left_dangling
    R.check("guarded_delete_flag_off_closes_hole", ok, json.dumps({
        "result": str(result), "object_gone": object_gone,
        "ring_before": ring_before, "ring_left_dangling": ring_left_dangling,
    }))


def guarded_delete_no_record_backed_objects_is_native_passthrough(R):
    """Flag on, but nothing selected is record-backed: ordinary native
    delete, unaffected by the guard (the ``legacy``-only branch)."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    legacy = _legacy_object()
    if legacy is None:
        R.check("guarded_delete_no_record_backed_objects_is_native_passthrough", False,
                "setup: no legacy (record-less) Window/Door object found in the fixture")
        return
    legacy_name = legacy.name

    _select([legacy])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')

    ok = result == {"FINISHED"} and legacy_name not in bpy.data.objects
    R.check("guarded_delete_no_record_backed_objects_is_native_passthrough", ok,
            f"result={result}")


def guarded_delete_export_clean_after_mixed_delete(R):
    """Export v2/v3 stay xmllint-clean after a mixed guarded-delete
    (routed Window + native legacy delete) in one action."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    gml_id = str(fill.get("gml_id"))

    legacy = _legacy_object()
    if legacy is None:
        R.check("guarded_delete_export_clean_after_mixed_delete", False,
                "setup: no legacy (record-less) Window/Door object found in the fixture")
        return

    _select([fill, legacy])
    result = bpy.ops.cgml.guarded_delete('EXEC_DEFAULT')
    ok = result == {"FINISHED"}
    detail = {"result": str(result)}
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p4_guarded_delete_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        detail[version] = lint
        ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id not in text
    R.check("guarded_delete_export_clean_after_mixed_delete", ok, json.dumps(detail))


def keymap_registration_is_a_safe_noop_headless(R):
    """``register_keymaps``/``unregister_keymaps`` (``__init__.py``'s
    add-on enable/disable hooks) run without raising in ``--background``
    mode, where there is no real window manager/keyconfig - they degrade
    to a no-op rather than crashing add-on registration. This does NOT
    prove a real ``X`` keypress in the 3D viewport resolves to
    ``cgml.guarded_delete`` ahead of Blender's own binding (untestable
    headless, no window/keyconfig exists to press a key in) - only that
    the registration/deregistration code itself is safe to call from
    ``register()``/``unregister()`` in every environment, including this
    one."""
    from city_modeler.ops.authoring_core_ops import register_keymaps, unregister_keymaps

    error = ""
    try:
        register_keymaps()
        unregister_keymaps()
    except Exception as e:
        error = str(e)
    ok = not error
    R.check("keymap_registration_is_a_safe_noop_headless", ok, f"error={error!r}")


def main():
    R = h.Results(__file__)
    checks = [
        guarded_delete_routes_record_backed_window,
        guarded_delete_blocks_whole_operation_for_unsupported_kind,
        guarded_delete_mixed_selection_routes_and_deletes_natively,
        guarded_delete_is_one_combined_undo_step,
        guarded_delete_flag_off_closes_hole,
        guarded_delete_no_record_backed_objects_is_native_passthrough,
        guarded_delete_export_clean_after_mixed_delete,
        keymap_registration_is_a_safe_noop_headless,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
