# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.3 (RoofSurface derived display regeneration
+ transactional synchronization, docs/agent/authoring-core/phase6-roof-design-note.md).

``shared/authoring_core/blender/display_regen.py``'s Roof-specific functions
(``regenerate_roof_display``, ``regenerate_or_restore_roof_display``,
``snapshot_single_display``/``restore_single_display_snapshot``) and their
wiring into ``shared/authoring_core/blender/roof_edit_session.py``'s three
commit entry points need a real Blender scene for everything that touches
``bpy.data``/mesh writes/undo-redo/save-reload - the same split
``roof_edit_session.py``/``test_cac_p6_2_roof_edit_session.py`` established,
extended with the display side (pure-Python builder/checksum parts covered
instead by ``scripts/tests/unit/test_cac_p6_3_roof_display.py``).

Primary fixture: ``test_files/Building/CitySmith/CS1_lod3.gml`` (same P6.0-
P6.2 fixture - 33 RoofSurface polygons, 2 hole-bearing), promoted through the
unmodified ``cgml.promote_building`` operator (CAC-P6.1).

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_3

NOT RUN IN THIS ENVIRONMENT (no Blender executable available here - the same
standing limitation every CAC phase has recorded since P5.3/STATUS.md's own
environment row). Needs a real-Blender run before P6.3 is considered fully
proven end-to-end.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

CS1 = h.FIXTURES / "Building" / "CitySmith" / "CS1_lod3.gml"


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _select(objects):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def _building_object():
    import bpy

    for obj in bpy.data.objects:
        if _kind_of(obj) == "Building" and str(obj.get("gml_id") or "").strip():
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


def _strip_unsupported():
    """Same fixture-limitation workaround P6.1/P6.2's own headless tests
    use: CS1's plain-imported Windows carry no real ``cgml_opening_link``,
    so promotion refuses the whole Building on their missing host_id before
    it ever reaches RoofSurface. RoofSurface itself is left untouched."""
    import bpy

    from city_modeler.shared.authoring_core.domain.export_eligibility import (
        ALLOWED_IR_KINDS, PASSTHROUGH_KINDS,
    )

    keep_kinds = (set(ALLOWED_IR_KINDS) | set(PASSTHROUGH_KINDS)) - {"Window"}
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


def _prepare_promoted_cs1():
    """Import CS1, promote its Building, return every record-backed
    RoofSurface object (CAC-P6.1's own, unmodified, promotion path)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as _rs

    _rs._SESSIONS.clear()

    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    result, error = _promote(building)
    if result != {'FINISHED'}:
        raise RuntimeError(f"CS1 promotion did not succeed: result={result} error={error!r}")
    from city_modeler.shared.authoring_core.blender import storage

    roofs = [
        o for o in bpy.data.objects
        if _kind_of(o) == "RoofSurface" and storage.has_record(o)
    ]
    return building, roofs


def _hole_bearing(roofs):
    from city_modeler.shared.authoring_core.blender import storage

    for roof in roofs:
        record_set = storage.read_record(roof)
        feature = record_set.feature(str(roof.get("gml_id")))
        if feature is None:
            continue
        for p in record_set.polygons_of(feature.gml_id):
            if p.interiors and p.geometry_state == "committed":
                return roof
    return None


def _op(idname, gml_id):
    import bpy

    ns = bpy.ops
    for part in idname.split("."):
        ns = getattr(ns, part)
    return ns('EXEC_DEFAULT', object_gml_id=gml_id)


def _begin_op(roof):
    _select([roof])
    return _op("cgml.feature_edit_begin", str(roof.get("gml_id") or ""))


def _commit_op(roof):
    _select([roof])
    return _op("cgml.feature_edit_commit", str(roof.get("gml_id") or ""))


def _translate(obj, dx=0.0, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _display_object(roof):
    import bpy

    return bpy.data.objects.get(f"{roof.name}.cgml_display")


def _display_freshness(roof, feature_gml_id):
    from city_modeler.shared.authoring_core.blender import display_regen, storage

    record_set = storage.read_record(roof)
    return display_regen.roof_display_freshness(_display_object(roof), record_set, feature_gml_id)


def simple_roof_commit_regenerates_display_fresh(R):
    """Promote -> edit -> commit -> display regenerated -> checksum fresh
    (P6.3 S15 "Simple RoofSurface")."""
    from city_modeler.shared.authoring_core.domain import display_checksum

    _, roofs = _prepare_promoted_cs1()
    roof = next((r for r in roofs if r is not _hole_bearing(roofs)), roofs[0] if roofs else None)
    if roof is None:
        R.check("simple_roof_commit_regenerates_display_fresh", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))

    _begin_op(roof)
    _translate(roof, dx=0.2, dy=0.1)
    result = _commit_op(roof)

    display_obj = _display_object(roof)
    is_display_only = display_obj is not None and bool(display_obj.get("_cgml_display_only", False))
    has_no_gml_id = display_obj is not None and not str(display_obj.get("gml_id") or "").strip()
    freshness = _display_freshness(roof, gml_id)

    ok = (
        result == {"FINISHED"}
        and display_obj is not None
        and len(display_obj.data.polygons) > 0
        and is_display_only
        and has_no_gml_id
        and freshness == display_checksum.FRESH
    )
    R.check("simple_roof_commit_regenerates_display_fresh", ok,
            f"result={result} display_present={display_obj is not None} "
            f"display_only={is_display_only} no_gml_id={has_no_gml_id} freshness={freshness}")


def hole_bearing_roof_display_contains_correct_holes(R):
    """Promote -> committed exterior + interior rings -> derived display
    contains the hole(s) -> save/reload -> checksum fresh (P6.3 S15 "Hole-
    bearing RoofSurface")."""
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.domain import display_checksum

    building, roofs = _prepare_promoted_cs1()
    roof = _hole_bearing(roofs)
    if roof is None:
        R.check("hole_bearing_roof_display_contains_correct_holes", False,
                "no committed hole-bearing RoofSurface found in CS1 (P6.1a gap?)")
        return
    gml_id = str(roof.get("gml_id"))
    record_set = storage.read_record(roof)
    feature = record_set.feature(gml_id)
    polygon_with_hole = next(p for p in record_set.polygons_of(gml_id) if p.interiors)
    expected_vertex_count = len(polygon_with_hole.exterior.coords) + sum(
        len(r.coords) for r in polygon_with_hole.interiors
    )

    # Native G/R/S drift on the Building root (roof_edit_session._geometry_
    # scope's documented reason: a hole-bearing CS1 polygon's exterior and
    # interior-ring faces live on separate sibling objects under the
    # Building after mesh explosion).
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs

    _translate(building, dx=0.1, dy=0.1)
    result = rs.commit_transform_drift(gml_id)

    display_obj = _display_object(roof)
    display_has_hole_vertex_count = (
        display_obj is not None and len(display_obj.data.vertices) >= expected_vertex_count
    )
    freshness = _display_freshness(roof, gml_id)

    path = h.out_dir() / "p6_3_roof_hole_display_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))

    reloaded_roof = registry.resolve(gml_id)
    freshness_after_reload = (
        _display_freshness(reloaded_roof, gml_id) if reloaded_roof is not None else "missing"
    )

    ok = (
        result.status == "committed"
        and display_has_hole_vertex_count
        and freshness == display_checksum.FRESH
        and reloaded_roof is not None
        and freshness_after_reload == display_checksum.FRESH
    )
    R.check("hole_bearing_roof_display_contains_correct_holes", ok,
            f"result={result} vertex_count_ok={display_has_hole_vertex_count} "
            f"freshness={freshness} freshness_after_reload={freshness_after_reload}")


def display_build_failure_rolls_back_record_and_mesh(R):
    """A ``DisplayBuildError`` during display topology planning must refuse
    the whole commit (record + mesh rolled back) - never a partial commit
    with a stale/missing display next to already-written records (P6.3
    S12)."""
    from city_modeler.shared.authoring_core.blender import (
        display_builder, roof_edit_session as rs, storage,
    )

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("display_build_failure_rolls_back_record_and_mesh", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_before = tuple(tuple(v.co) for v in roof.data.vertices)

    real_build = display_builder.build_roof_display

    def _boom(record_set, surface_id):
        raise display_builder.DisplayBuildError("injected display-build failure")

    display_builder.build_roof_display = _boom
    refused = False
    try:
        rs.begin(gml_id)
        _translate(roof, dx=0.15)
        try:
            rs.commit(gml_id)
        except rs.RoofEditSessionError:
            refused = True
    finally:
        display_builder.build_roof_display = real_build
        if rs.active_session(gml_id) is not None:
            rs.cancel(gml_id)

    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_after = tuple(tuple(v.co) for v in roof.data.vertices)
    ok = refused and record_before == record_after and mesh_before == mesh_after
    R.check("display_build_failure_rolls_back_record_and_mesh", ok,
            f"refused={refused} record_unchanged={record_before == record_after} "
            f"mesh_unchanged={mesh_before == mesh_after}")


def mesh_apply_failure_rolls_back_record_and_display(R):
    """A failure writing the planned ``DisplayMesh`` onto the Roof's display
    object (``DisplayApplyError``) rolls back the record and restores the
    display object to its exact pre-attempt state - no orphan display
    object left behind for a surface that had none before (P6.3 S12)."""
    from city_modeler.shared.authoring_core.blender import (
        display_regen, roof_edit_session as rs, storage,
    )

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("mesh_apply_failure_rolls_back_record_and_display", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    display_before = _display_object(roof)
    display_existed_before = display_before is not None

    real_write = display_regen._write_display_mesh

    def _boom(obj, display_mesh):
        raise RuntimeError("injected mesh-apply failure")

    display_regen._write_display_mesh = _boom
    refused = False
    try:
        rs.begin(gml_id)
        _translate(roof, dx=0.15)
        try:
            rs.commit(gml_id)
        except rs.RoofEditSessionError:
            refused = True
    finally:
        display_regen._write_display_mesh = real_write
        if rs.active_session(gml_id) is not None:
            rs.cancel(gml_id)

    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    display_after = _display_object(roof)
    no_orphan_display = display_existed_before or display_after is None
    ok = refused and record_before == record_after and no_orphan_display
    R.check("mesh_apply_failure_rolls_back_record_and_display", ok,
            f"refused={refused} record_unchanged={record_before == record_after} "
            f"no_orphan_display={no_orphan_display}")


def verification_failure_rolls_back(R):
    """A post-application verification mismatch (``DisplayVerificationError``)
    rolls back the record and restores the display object exactly, same as
    the mesh-apply and topology-build seams (P6.3 S12)."""
    from city_modeler.shared.authoring_core.blender import (
        display_regen, roof_edit_session as rs, storage,
    )

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("verification_failure_rolls_back", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_before = tuple(tuple(v.co) for v in roof.data.vertices)

    real_verify = display_regen._verify_display_mesh

    def _boom(obj, display_mesh):
        raise display_regen.DisplayVerificationError("injected verification failure")

    display_regen._verify_display_mesh = _boom
    refused = False
    try:
        rs.begin(gml_id)
        _translate(roof, dx=0.15)
        try:
            rs.commit(gml_id)
        except rs.RoofEditSessionError:
            refused = True
    finally:
        display_regen._verify_display_mesh = real_verify
        if rs.active_session(gml_id) is not None:
            rs.cancel(gml_id)

    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_after = tuple(tuple(v.co) for v in roof.data.vertices)
    ok = refused and record_before == record_after and mesh_before == mesh_after
    R.check("verification_failure_rolls_back", ok,
            f"refused={refused} record_unchanged={record_before == record_after} "
            f"mesh_unchanged={mesh_before == mesh_after}")


def validation_failure_leaves_old_display_untouched(R):
    """A commit refused by topology validation (before display regeneration
    is even attempted) must leave any pre-existing display object exactly
    as it was (P6.3 S12 test 10)."""
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("validation_failure_leaves_old_display_untouched", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))

    # First, a real successful commit so a display object exists to protect.
    _begin_op(roof)
    _translate(roof, dx=0.1)
    _commit_op(roof)
    display_before = tuple(tuple(v.co) for v in _display_object(roof).data.vertices)

    rs.begin(gml_id)
    first = roof.data.vertices[0].co.copy()
    for v in roof.data.vertices:
        v.co = first
    roof.data.update()
    refused = False
    try:
        rs.commit(gml_id)
    except rs.RoofEditSessionError:
        refused = True
    finally:
        if rs.active_session(gml_id) is not None:
            rs.cancel(gml_id)

    display_after = tuple(tuple(v.co) for v in _display_object(roof).data.vertices)
    ok = refused and display_before == display_after
    R.check("validation_failure_leaves_old_display_untouched", ok,
            f"refused={refused} display_unchanged={display_before == display_after}")


def undo_redo_restores_roof_record_and_display(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("undo_redo_restores_roof_record_and_display", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    roof_name = roof.name
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    bpy.ops.ed.undo_push(message="cac: before roof edit begin")
    begin_result = _begin_op(roof)
    _translate(roof, dx=0.2)
    bpy.ops.ed.undo_push(message="cac: after roof geometry edit")
    commit_result = _commit_op(roof)
    bpy.ops.ed.undo_push(message="cac: after roof commit")
    record_after_commit = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    display_after_commit = tuple(tuple(v.co) for v in _display_object(roof).data.vertices)

    bpy.ops.ed.undo()
    roof_after_undo = bpy.data.objects[roof_name]
    record_after_undo = json.dumps(storage.read_record(roof_after_undo).to_dict(), sort_keys=True)
    undo_restored_record = record_after_undo == record_before

    bpy.ops.ed.redo()
    roof_after_redo = bpy.data.objects[roof_name]
    record_after_redo = json.dumps(storage.read_record(roof_after_redo).to_dict(), sort_keys=True)
    display_after_redo = tuple(tuple(v.co) for v in _display_object(roof_after_redo).data.vertices)
    redo_reapplied_record = record_after_redo == record_after_commit
    redo_reapplied_display = display_after_redo == display_after_commit

    ok = (
        begin_result == {"FINISHED"} and commit_result == {"FINISHED"}
        and undo_restored_record and redo_reapplied_record and redo_reapplied_display
    )
    R.check("undo_redo_restores_roof_record_and_display", ok,
            f"begin={begin_result} commit={commit_result} undo_restored_record={undo_restored_record} "
            f"redo_reapplied_record={redo_reapplied_record} redo_reapplied_display={redo_reapplied_display}")


def save_reload_simple_roof_display_stays_fresh(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.domain import display_checksum

    _, roofs = _prepare_promoted_cs1()
    roof = next((r for r in roofs if r is not _hole_bearing(roofs)), roofs[0] if roofs else None)
    if roof is None:
        R.check("save_reload_simple_roof_display_stays_fresh", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    _begin_op(roof)
    _translate(roof, dx=0.25)
    _commit_op(roof)
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    path = h.out_dir() / "p6_3_roof_display_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))

    reloaded = registry.resolve(gml_id)
    record_after = json.dumps(storage.read_record(reloaded).to_dict(), sort_keys=True) if reloaded else ""
    freshness_after = _display_freshness(reloaded, gml_id) if reloaded is not None else "missing"
    ok = (
        reloaded is not None
        and record_before == record_after
        and freshness_after == display_checksum.FRESH
    )
    R.check("save_reload_simple_roof_display_stays_fresh", ok,
            f"resolved={reloaded is not None} record_equal={record_before == record_after} "
            f"freshness_after={freshness_after}")


def display_only_object_excluded_from_registry_and_export(R):
    """Reconfirm a Roof display-only object carries the single marker both
    CityGML writers (``io/writer/exporter.py``/``io_v2/writer/exporter.py``,
    each own ``_should_export_obj``) check to exclude it from export, and
    that ``registry.py``'s own identity predicate already treats it as a
    non-entity - the exact pattern GS.2's own headless suite proved for the
    wall/Window display object (``test_cac_gs2_display_regen.py::gs2_
    display_object_has_no_semantic_identity``), reused unmodified for the
    RoofSurface display object (P6.3 S13/S16; no writer change needed)."""
    from city_modeler.shared.authoring_core.blender import display_regen, registry, storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("display_only_object_excluded_from_registry_and_export", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    _begin_op(roof)
    _translate(roof, dx=0.1)
    _commit_op(roof)

    display_obj = _display_object(roof)
    is_display_only = display_obj is not None and display_regen.is_display_only(display_obj)
    no_gml_id = display_obj is not None and not str(display_obj.get("gml_id") or "").strip()
    no_record = display_obj is not None and not storage.has_record(display_obj)
    ident_gml_id, ident_kind, has_record = (
        registry.identity_of(display_obj, use_records=True) if display_obj is not None else ("x", "x", True)
    )
    not_an_identity = not (ident_gml_id or ident_kind or has_record)

    registry.REGISTRY.rebuild()
    not_registered = display_obj is not None and registry.REGISTRY.get(display_obj.name) is None

    ok = is_display_only and no_gml_id and no_record and not_an_identity and not_registered
    R.check("display_only_object_excluded_from_registry_and_export", ok,
            f"is_display_only={is_display_only} no_gml_id={no_gml_id} no_record={no_record} "
            f"identity=({ident_gml_id!r},{ident_kind!r},{has_record}) not_registered={not_registered}")


def repeated_commit_is_deterministic_display(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("repeated_commit_is_deterministic_display", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))

    rs.begin(gml_id)
    _translate(roof, dx=0.1)
    rs.commit(gml_id)
    once = tuple(tuple(v.co) for v in _display_object(roof).data.vertices)

    result_twice = rs.commit_transform_drift(gml_id)  # no live change since last commit
    twice = tuple(tuple(v.co) for v in _display_object(roof).data.vertices)

    ok = once == twice and result_twice.status == "no_op"
    R.check("repeated_commit_is_deterministic_display", ok, f"result_twice={result_twice} equal={once == twice}")


def main():
    R = h.Results(__file__)
    checks = [
        simple_roof_commit_regenerates_display_fresh,
        hole_bearing_roof_display_contains_correct_holes,
        display_build_failure_rolls_back_record_and_mesh,
        mesh_apply_failure_rolls_back_record_and_display,
        verification_failure_rolls_back,
        validation_failure_leaves_old_display_untouched,
        undo_redo_restores_roof_record_and_display,
        save_reload_simple_roof_display_stays_fresh,
        display_only_object_excluded_from_registry_and_export,
        repeated_commit_is_deterministic_display,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
