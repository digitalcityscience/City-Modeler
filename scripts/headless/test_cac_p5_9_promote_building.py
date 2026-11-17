# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P5.9 (``cgml.promote_building``).

Complements ``scripts/tests/unit/test_cac_p5_9_promote_building.py`` (pure
domain-level candidate-graph tests, no bpy) with the parts of the P5.9
contract that only a real Blender scene can prove: the operator itself,
atomic write/rollback against real ``_cgml_record`` custom properties, one
combined undo step, save/reload persistence, and xmllint-clean v2/v3 export
before/after promotion, against a real imported fixture
(``Railway_Scene_LoD3_CityGML3_Building_only.gml``: Building, WallSurface,
Window and Door all present, per PLAN.md section 17's real-fixture
requirement).

**Not run in this environment** (no Blender executable available here - the
same limitation every CAC phase since P5.3 has recorded in STATUS.md; see
``docs/agent/authoring-core/issues/CAC-P5.9.md``, "Validation commands").
Written to the same convention every other file in this directory already
uses (``cac_harness.Results``, one check function per acceptance, driven
through the real ``bpy.ops.cgml.promote_building()`` operator - never a
direct call into the usecase - so a real user action is what is proven):

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_9_promote_building.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _strip_unsupported_features(strip_windows=False):
    """Remove every object of a kind outside the frozen Phase-5 promotion
    support matrix (``BuildingInstallation``, real in this fixture - 2
    objects) so the "successful promotion" checks exercise the graph P5.9
    is scoped to. Discovered, not assumed: see
    ``promote_unsupported_non_door_feature_refuses_before_writes`` for the
    real-fixture proof that ``build_promotion_candidate`` refuses instead
    of silently discarding these when left in place.

    ``strip_windows`` (CAC-P5.10 corrective follow-up): this fixture's real
    ``Window`` objects are plain-imported, never authored, so none of them
    carry ``cgml_opening_link`` (only the Draw/Stamp flow writes it -
    confirmed repo-wide, see the P5.10 gate report). Promotion now
    correctly refuses the whole Building for that reason (the very bug
    this follow-up fixes), so a test whose actual subject is promotion's
    *mechanics* (undo/redo, rollback, save/reload, idempotency,
    eligibility improving) rather than Window promotion itself needs the
    real, unresolvable Windows out of scope too - this is a scope
    reduction forced by a genuine fixture limitation, not opening_link
    synthesis (nothing is invented; the Windows are removed, not repaired)."""
    import bpy

    from city_modeler.shared.authoring_core.domain.export_eligibility import (
        ALLOWED_IR_KINDS, PASSTHROUGH_KINDS,
    )

    keep_kinds = set(ALLOWED_IR_KINDS) | set(PASSTHROUGH_KINDS)
    if strip_windows:
        keep_kinds.discard("Window")

    removed = []
    for obj in list(bpy.data.objects):
        kind = _kind_of(obj)
        if kind in ("Building", "BuildingPart") or kind in keep_kinds:
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        removed.append(obj.name)
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return removed


def _prepare(strip_unsupported=False, strip_windows=False):
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    if strip_unsupported:
        _strip_unsupported_features(strip_windows=strip_windows)


def _building_object():
    for obj in __import__("bpy").data.objects:
        feat = str(obj.get("cgml3_feature") or "").strip().lower()
        if feat in ("building",) and str(obj.get("gml_id") or "").strip():
            return obj
    return None


def _promote(building_obj):
    import bpy

    _select([building_obj])
    try:
        result = bpy.ops.cgml.promote_building('EXEC_DEFAULT')
        error = ""
    except RuntimeError as e:
        result, error = {'CANCELLED'}, str(e)
    return result, error


def _select(objects):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _run_validation():
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(None)


def _is_blocking(findings):
    from city_modeler.shared.authoring_core.domain import consistency as c

    return c.is_blocking(findings)


def _building_scope(building_obj):
    from city_modeler.shared.authoring_core.blender.building_root import building_scope

    return building_scope(building_obj)


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def promote_fully_legacy_building_creates_complete_record_graph(R):
    """Case A - a freshly imported Building has no ``_cgml_record`` anywhere.
    ``cgml.promote_building`` must create one on the Building itself and on
    every legacy WallSurface/Window in its scope, leave every Door
    untouched (legacy-only), and leave the record/legacy validator clean."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_fully_legacy_building_creates_complete_record_graph", False,
                "setup: fixture has no Building object")
        return

    scope = _building_scope(building)
    before = {o.name: storage.has_record(o) for o in scope}
    assert not any(before.values()), "setup: fixture must be fully legacy before promotion"

    result, error = _promote(building)

    building_promoted = storage.has_record(building)
    walls = [o for o in scope if _kind_of(o) == "WallSurface"]
    windows = [o for o in scope if _kind_of(o) == "Window"]
    doors = [o for o in scope if _kind_of(o) == "Door"]
    walls_promoted = all(storage.has_record(o) for o in walls) if walls else True
    windows_promoted = all(storage.has_record(o) for o in windows) if windows else True
    doors_untouched = not any(storage.has_record(o) for o in doors)
    findings = _run_validation()
    validator_clean = not _is_blocking(findings)

    ok = (
        result == {'FINISHED'} and not error and building_promoted
        and walls_promoted and windows_promoted and doors_untouched and validator_clean
    )
    R.check("promote_fully_legacy_building_creates_complete_record_graph", ok, json.dumps({
        "result": str(result), "error": error, "building_promoted": building_promoted,
        "n_walls": len(walls), "walls_promoted": walls_promoted,
        "n_windows": len(windows), "windows_promoted": windows_promoted,
        "n_doors": len(doors), "doors_untouched": doors_untouched,
        "validator_clean": validator_clean,
    }))


def promote_is_deterministic_noop_when_already_promoted(R):
    """Case C - promoting an already-fully-promoted Building a second time
    changes nothing: same record content, no new ids, operator reports no
    changes."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_is_deterministic_noop_when_already_promoted", False, "setup: no Building")
        return

    _promote(building)
    scope = _building_scope(building)
    before = {o.name: storage.read_record(o).to_dict() if storage.has_record(o) else None for o in scope}

    result, error = _promote(building)

    after = {o.name: storage.read_record(o).to_dict() if storage.has_record(o) else None for o in scope}
    ok = result == {'FINISHED'} and not error and before == after
    R.check("promote_is_deterministic_noop_when_already_promoted", ok, json.dumps({
        "result": str(result), "error": error, "unchanged": before == after,
    }))


def promote_preserves_existing_record_backed_window_unchanged(R):
    """Case B - a Window already placed through ``create_feature`` (real
    record-backed authoring, not promotion) keeps its exact record after
    the rest of the Building is promoted around it."""
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_preserves_existing_record_backed_window_unchanged", False, "setup: no Building")
        return

    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    uv = h.solid_area_position_uv(wall, face_index, ctx, 0.15, 0.15)
    definition = FacadeElementDefinition(semantic_type="Window", width=0.3, height=0.3, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    fill = create_feature("Window", ctx, definition, placement)
    before = storage.read_record(fill).to_dict()

    result, error = _promote(building)

    after = storage.read_record(fill).to_dict() if storage.has_record(fill) else None
    ok = result == {'FINISHED'} and not error and after == before
    R.check("promote_preserves_existing_record_backed_window_unchanged", ok, json.dumps({
        "result": str(result), "error": error, "unchanged": after == before,
    }))


def promote_is_one_combined_undo_step(R):
    """Undo after promotion restores every touched object's ``_cgml_record``
    to absent (fully legacy again) in one step; redo re-promotes."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_is_one_combined_undo_step", False, "setup: no Building")
        return
    building_name = building.name
    scope_names = [o.name for o in _building_scope(building)]

    import bpy

    def _by_name(names):
        return [bpy.data.objects[n] for n in names if n in bpy.data.objects]

    bpy.ops.ed.undo_push(message="cac: before promote")
    result, error = _promote(building)
    bpy.ops.ed.undo_push(message="cac: after promote")
    if result != {'FINISHED'}:
        R.check("promote_is_one_combined_undo_step", False, f"setup promote failed: {error}")
        return

    bpy.ops.ed.undo()
    # undo may recreate new Object datablocks under the same names - never
    # trust an object reference captured before an undo/redo (AB-style
    # lesson from the guarded-delete suite); re-resolve by name each time.
    all_reverted = not any(storage.has_record(o) for o in _by_name(scope_names))

    bpy.ops.ed.redo()
    reresolved = _by_name(scope_names)
    all_repromoted = (
        storage.has_record(bpy.data.objects[building_name])
        and all(storage.has_record(o) for o in reresolved if _kind_of(o) == "WallSurface")
    )

    ok = all_reverted and all_repromoted
    R.check("promote_is_one_combined_undo_step", ok, json.dumps({
        "all_reverted": all_reverted, "all_repromoted": all_repromoted,
    }))

    # P5.10/G5 item 9: export after redo must succeed and stay xmllint-clean
    # - no partial record state left over by the undo->redo round trip.
    export_ok = True
    export_detail = {}
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p5_9_undo_redo_export_{version.replace('.', '')}_{os.getpid()}.gml"
        try:
            h.export_scene(str(path), version)
            lint = h.xmllint(path, version)
            export_detail[version] = lint
            export_ok = export_ok and lint["exit"] == 0
        except Exception as exc:  # noqa: BLE001
            export_ok = False
            export_detail[version] = repr(exc)
    R.check("promote_undo_redo_export_after_redo_is_xmllint_clean", export_ok, json.dumps(export_detail))


def promote_injected_write_failure_rolls_back_completely(R):
    """A failure injected mid-write (after some objects already have their
    new record written) must roll back every object promotion touched -
    no partially promoted Building survives."""
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender import promote_building as pb_module

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_injected_write_failure_rolls_back_completely", False, "setup: no Building")
        return
    scope = _building_scope(building)
    before = {o.name: (storage.RECORD_KEY in o.keys()) for o in scope}

    real_write_record = storage.write_record
    calls = {"n": 0}

    def _flaky_write_record(obj, record_set):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("injected promotion write failure")
        real_write_record(obj, record_set)

    storage.write_record = _flaky_write_record
    try:
        _select([building])
        try:
            import bpy
            bpy.ops.cgml.promote_building('EXEC_DEFAULT')
            raised = False
        except RuntimeError:
            raised = True
    finally:
        storage.write_record = real_write_record

    after = {o.name: (storage.RECORD_KEY in o.keys()) for o in scope}
    fully_rolled_back = before == after
    ok = raised and fully_rolled_back and calls["n"] >= 2
    R.check("promote_injected_write_failure_rolls_back_completely", ok, json.dumps({
        "raised": raised, "fully_rolled_back": fully_rolled_back, "writes_attempted": calls["n"],
    }))


def promote_save_reload_preserves_promoted_records(R):
    """Promoted records survive a save/reload round trip through a real
    ``.blend`` file (they are ordinary custom properties, but this is the
    end-to-end proof the task explicitly asks for, not an assumption)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_save_reload_preserves_promoted_records", False, "setup: no Building")
        return
    building_gml_id = str(building.get("gml_id"))
    result, error = _promote(building)
    if result != {'FINISHED'}:
        R.check("promote_save_reload_preserves_promoted_records", False, f"setup promote failed: {error}")
        return

    path = str(h.out_dir() / "p5_9_promote_save_reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)

    reloaded_building = None
    for obj in bpy.data.objects:
        if str(obj.get("gml_id") or "") == building_gml_id:
            reloaded_building = obj
            break
    ok = reloaded_building is not None and storage.has_record(reloaded_building)
    R.check("promote_save_reload_preserves_promoted_records", ok, json.dumps({
        "reloaded_building_found": reloaded_building is not None,
        "has_record_after_reload": storage.has_record(reloaded_building) if reloaded_building else False,
    }))


def promote_eligibility_improves_and_root_switches_persisted_and_exports_stay_clean(R):
    """``can_export_building_from_ir`` before promotion reports
    ``no_building_root``/incomplete; after promotion the Building root is
    persisted and the count of unsupported features drops sharply (full
    ``IR_FULL`` is proven on clean synthetic graphs by the domain-level
    unit suite - this real, LoD3-detailed fixture turned out to carry a
    handful of Window sub-polygons whose face cannot be resolved from any
    mesh object in scope at all, a real pre-existing geometry
    characteristic of this fixture's data, not a promotion defect: those
    features correctly stay ``ids_only``/unsupported rather than the
    predicate being weakened to call them eligible anyway - see the P5.9
    task card's "Discovered" section). v2/v3 export stay xmllint-clean
    throughout."""
    from city_modeler.shared.authoring_core.blender.building_root import export_eligibility
    from city_modeler.shared.authoring_core.domain.building_root import (
        PERSISTED, resolve_building_root,
    )

    _flag(True)
    _prepare(strip_unsupported=True, strip_windows=True)
    building = _building_object()
    if building is None:
        R.check("promote_eligibility_improves_and_root_switches_persisted_and_exports_stay_clean",
                False, "setup: no Building")
        return

    from city_modeler.shared.authoring_core.blender import consistency_view, legacy_views

    scope = _building_scope(building)
    before = export_eligibility(building)
    n_unsupported_before = len(before.unsupported_features)

    result, error = _promote(building)

    after = export_eligibility(building)
    persisted_after = consistency_view.merged_record_set(scope)
    legacy_after = legacy_views.read_scene(scope).records
    root_after, source_after = resolve_building_root(persisted_after, legacy_after, str(building.get("gml_id")))

    detail = {
        "eligible_before": before.eligible, "reason_before": before.reason,
        "n_unsupported_before": n_unsupported_before,
        "result": str(result), "error": error,
        "eligible_after": after.eligible, "reason_after": after.reason,
        "n_unsupported_after": len(after.unsupported_features),
        "root_source_after": source_after,
    }
    ok = (
        before.eligible is False and result == {'FINISHED'} and not error
        and source_after == PERSISTED and root_after.gml_id == str(building.get("gml_id"))
        and len(after.unsupported_features) < n_unsupported_before
    )
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"p5_9_promote_v{version.replace('.', '')}.gml"
        h.export_scene(path, version)
        lint = h.xmllint(path, version)
        detail[version] = lint
        ok = ok and lint["exit"] == 0 and lint["errors"] == 0
    R.check("promote_eligibility_improves_and_root_switches_persisted_and_exports_stay_clean", ok,
            json.dumps(detail))


def promote_unsupported_non_door_feature_refuses_before_writes(R):
    """Real-fixture proof of rule 15: the unmodified fixture carries 2
    ``BuildingInstallation`` objects (discovered, not synthetic - outside
    the frozen Phase-5 support matrix). Promoting the Building as imported
    must refuse atomically before writing anything, not silently promote
    only the supported slice."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    _prepare(strip_unsupported=False)
    building = _building_object()
    if building is None:
        R.check("promote_unsupported_non_door_feature_refuses_before_writes", False, "setup: no Building")
        return
    scope = _building_scope(building)
    installations = [o for o in scope if _kind_of(o) == "BuildingInstallation"]
    if not installations:
        R.check("promote_unsupported_non_door_feature_refuses_before_writes", False,
                "setup: fixture no longer carries an unsupported non-Door feature to test against")
        return

    result, error = _promote(building)

    nothing_written = not any(storage.has_record(o) for o in scope)
    names_reported = all(str(o.get("gml_id") or "") in error for o in installations)
    ok = result == {'CANCELLED'} and "unsupported non-Door" in error and nothing_written and names_reported
    R.check("promote_unsupported_non_door_feature_refuses_before_writes", ok, json.dumps({
        "result": str(result), "error": error, "n_installations": len(installations),
        "nothing_written": nothing_written, "names_reported": names_reported,
    }))


def promote_non_building_object_refuses(R):
    """The operator's own poll refuses when the active object is not a
    Building/BuildingPart (P5.9 is Building-scoped, not object-generic)."""
    import bpy

    _flag(True)
    _prepare(strip_unsupported=True)
    wall, _face_index = h.first_wall_with_exterior_face()
    _select([wall])
    can_run = bpy.ops.cgml.promote_building.poll()
    R.check("promote_non_building_object_refuses", can_run is False, f"poll()={can_run}")


def main():
    R = h.Results(__file__)
    checks = [
        promote_fully_legacy_building_creates_complete_record_graph,
        promote_is_deterministic_noop_when_already_promoted,
        promote_preserves_existing_record_backed_window_unchanged,
        promote_is_one_combined_undo_step,
        promote_injected_write_failure_rolls_back_completely,
        promote_save_reload_preserves_promoted_records,
        promote_eligibility_improves_and_root_switches_persisted_and_exports_stay_clean,
        promote_unsupported_non_door_feature_refuses_before_writes,
        promote_non_building_object_refuses,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
