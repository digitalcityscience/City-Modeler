# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.2 (interactive RoofSurface edit/commit seam).

``shared/authoring_core/blender/roof_edit_session.py`` (pure-Python parts
covered by ``scripts/tests/unit/test_cac_p6_2_roof_edit_session.py``) needs a
real Blender scene for everything that touches ``registry.resolve``, mesh
vertex reads/writes, undo/redo and save/reload - exactly the same split
``edit_session.py``/``test_cac_p4_edit_commit.py`` already established for
the Window+Wall pair transaction.

Primary fixture: ``test_files/Building/CitySmith/CS1_lod3.gml`` (same P6.0/
P6.1/P6.1a fixture - 33 RoofSurface polygons, 2 hole-bearing), promoted first
through the unmodified ``cgml.promote_building`` operator (CAC-P6.1) so every
check here starts from an already record-backed, already-``committed``
RoofSurface - the P6.2 "supported slice" (design note §1: existing
record-backed RoofSurface only, no creation).

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_2

NOT RUN IN THIS ENVIRONMENT (no Blender executable available here - the same
standing limitation every CAC phase has recorded since P5.3/STATUS.md's own
environment row). Needs a real-Blender run before P6.2 is considered fully
proven end-to-end, exactly like P6.1a's own headless counterpart did until
its verification follow-up.
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
    """Same fixture-limitation workaround P6.1's own headless test uses
    (``test_cac_p6_1_roof_promotion.py::_strip_unsupported``): CS1's
    plain-imported Windows carry no real ``cgml_opening_link``, so
    promotion refuses the whole Building on their missing host_id before it
    ever reaches RoofSurface. RoofSurface itself is left untouched."""
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

    _rs._SESSIONS.clear()  # CS1's gml_ids are deterministic across re-imports; never carry a
    # leftover open session from a previous (possibly failed) check into this one.

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


def _cancel_op(roof):
    _select([roof])
    return _op("cgml.feature_edit_cancel", str(roof.get("gml_id") or ""))


def _translate(obj, dx=0.0, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _world_verts(obj):
    return tuple(tuple(obj.matrix_world @ v.co) for v in obj.data.vertices)


def translated_roof_geometry_commits_and_preserves_ids(R):
    from city_modeler.shared.authoring_core.blender import storage

    building, roofs = _prepare_promoted_cs1()
    roof = next((r for r in roofs if r is not _hole_bearing(roofs)), roofs[0] if roofs else None)
    if roof is None:
        R.check("translated_roof_geometry_commits_and_preserves_ids", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    before = storage.read_record(roof).feature(gml_id)
    before_polys = storage.read_record(roof).polygons_of(gml_id)
    ring_ids_before = tuple(p.exterior.ring_id for p in before_polys) + tuple(
        r.ring_id for p in before_polys for r in p.interiors
    )

    _begin_op(roof)
    _translate(roof, dx=0.2, dy=0.1, dz=0.0)
    result = _commit_op(roof)

    after_feature = storage.read_record(roof).feature(gml_id)
    after_polys = storage.read_record(roof).polygons_of(gml_id)
    ring_ids_after = tuple(p.exterior.ring_id for p in after_polys) + tuple(
        r.ring_id for p in after_polys for r in p.interiors
    )
    live = _world_verts(roof)
    coords_match_live = all(
        c in live for p in after_polys for c in p.exterior.coords
    )

    ok = (
        result == {"FINISHED"}
        and after_feature.gml_id == gml_id
        and after_feature.polygon_ids == before.polygon_ids
        and ring_ids_after == ring_ids_before
        and after_feature.lod == before.lod
        and any(p.exterior.coords != bp.exterior.coords for p, bp in zip(after_polys, before_polys))
        and coords_match_live
    )
    R.check("translated_roof_geometry_commits_and_preserves_ids", ok,
            f"result={result} ids_preserved={ring_ids_after == ring_ids_before} "
            f"lod_preserved={after_feature.lod == before.lod} coords_match_live={coords_match_live}")


def no_geometry_change_commit_is_noop(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("no_geometry_change_commit_is_noop", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    rs.begin(gml_id)
    result = rs.commit(gml_id)
    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    ok = result.status == "no_op" and record_before == record_after and rs.active_session(gml_id) is None
    R.check("no_geometry_change_commit_is_noop", ok, f"result={result}")


def invalid_topology_refused_without_mutation(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("invalid_topology_refused_without_mutation", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_before = tuple(tuple(v.co) for v in roof.data.vertices)

    rs.begin(gml_id)
    # Collapse every vertex onto the first - degenerate exterior, <3 distinct
    # points; _commit_polygon refuses (returns the same PolygonRecord back).
    first = roof.data.vertices[0].co.copy()
    for v in roof.data.vertices:
        v.co = first
    roof.data.update()

    refused = False
    error = ""
    try:
        rs.commit(gml_id)
    except rs.RoofEditSessionError as e:
        refused = True
        error = str(e)

    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_after = tuple(tuple(v.co) for v in roof.data.vertices)
    session_still_open = rs.active_session(gml_id) is not None

    ok = refused and record_before == record_after and session_still_open
    R.check("invalid_topology_refused_without_mutation", ok,
            f"refused={refused} error={error!r} record_unchanged={record_before == record_after} "
            f"mesh_unchanged_note=(mesh stays edited until cancel, only the record is asserted) "
            f"session_still_open={session_still_open}")
    if session_still_open:
        rs.cancel(gml_id)
        mesh_restored = tuple(tuple(v.co) for v in roof.data.vertices) == mesh_before
        R.check("cancel_after_refused_commit_restores_mesh", mesh_restored, "")


def record_write_failure_rolls_back(R):
    """Inject a failure inside the write step itself (not just a validation
    refusal) and prove the mesh and the record both end up exactly as they
    were before the commit attempt (P6.2 §7)."""
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("record_write_failure_rolls_back", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_before = tuple(tuple(v.co) for v in roof.data.vertices)

    real_write = storage.write_record
    calls = {"n": 0}

    def _boom(obj, record_set):
        # Fail only the commit's own first write, not the rollback-restore
        # write that must follow it - a mock that also blocks the rollback
        # itself would prove nothing about rollback, only about a second,
        # unrelated failure.
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected record-write failure")
        return real_write(obj, record_set)

    storage.write_record = _boom
    refused = False
    try:
        rs.begin(gml_id)
        _translate(roof, dx=0.15)
        try:
            rs.commit(gml_id)
        except rs.RoofEditSessionError:
            refused = True
    finally:
        storage.write_record = real_write
        if rs.active_session(gml_id) is not None:
            rs.cancel(gml_id)

    record_after = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)
    mesh_after = tuple(tuple(v.co) for v in roof.data.vertices)
    ok = refused and record_before == record_after and mesh_before == mesh_after
    R.check("record_write_failure_rolls_back", ok,
            f"refused={refused} record_unchanged={record_before == record_after} "
            f"mesh_unchanged={mesh_before == mesh_after}")


def transform_drift_commits_and_preserves_ids(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("transform_drift_commits_and_preserves_ids", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    before = storage.read_record(roof).feature(gml_id)

    _translate(roof, dx=0.1, dy=0.0, dz=0.05)  # no begin(): native G/R/S-style drift
    result = rs.commit_transform_drift(gml_id)

    after = storage.read_record(roof).feature(gml_id)
    ok = result.status == "committed" and after.gml_id == before.gml_id and after.polygon_ids == before.polygon_ids
    R.check("transform_drift_commits_and_preserves_ids", ok, f"result={result}")


def hole_bearing_roof_translate_preserves_ring_identity(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    building, roofs = _prepare_promoted_cs1()
    roof = _hole_bearing(roofs)
    if roof is None:
        R.check("hole_bearing_roof_translate_preserves_ring_identity", False,
                 "no committed hole-bearing RoofSurface found in CS1 (P6.1a gap?)")
        return
    gml_id = str(roof.get("gml_id"))
    before_polys = storage.read_record(roof).polygons_of(gml_id)
    ring_ids_before = tuple(r.ring_id for p in before_polys for r in p.interiors)

    # A real CS1 structural fact (see roof_edit_session._geometry_scope's
    # docstring): this polygon's exterior and its two interior-ring holes
    # live on separate sibling objects under the Building, not all on the
    # RoofSurface's own mesh object (CGML_OT_split_surfaces gives a
    # topologically disconnected hole face its own object). Translating the
    # RoofSurface leaf object alone would move only the exterior and leave
    # the holes behind, geometrically inconsistent - so this moves their
    # common Building ancestor instead (native G/R/S drift, no begin()
    # session needed) and commits through commit_transform_drift, exactly
    # the "move the whole building" case D-2/checksum.py's own docstring
    # names as the reason a commit must be checksummed from matrix_world.
    _translate(building, dx=0.1, dy=0.1)
    result = rs.commit_transform_drift(gml_id)

    after_polys = storage.read_record(roof).polygons_of(gml_id)
    ring_ids_after = tuple(r.ring_id for p in after_polys for r in p.interiors)
    geometry_moved = (
        [r.coords for p in after_polys for r in p.interiors]
        != [r.coords for p in before_polys for r in p.interiors]
    )
    ok = result.status == "committed" and ring_ids_after == ring_ids_before and geometry_moved
    R.check("hole_bearing_roof_translate_preserves_ring_identity", ok,
            f"result={result} ring_ids_preserved={ring_ids_after == ring_ids_before} "
            f"geometry_moved={geometry_moved}")


def undo_redo_restores_roof_record(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    building, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("undo_redo_restores_roof_record", False, "no promoted RoofSurface found")
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

    bpy.ops.ed.undo()
    roof_after_undo = bpy.data.objects[roof_name]
    record_after_undo = json.dumps(storage.read_record(roof_after_undo).to_dict(), sort_keys=True)
    undo_restored = record_after_undo == record_before

    bpy.ops.ed.redo()
    roof_after_redo = bpy.data.objects[roof_name]
    record_after_redo = json.dumps(storage.read_record(roof_after_redo).to_dict(), sort_keys=True)
    redo_reapplied = record_after_redo == record_after_commit

    ok = (begin_result == {"FINISHED"} and commit_result == {"FINISHED"}
          and undo_restored and redo_reapplied)
    R.check("undo_redo_restores_roof_record", ok,
            f"begin={begin_result} commit={commit_result} undo_restored={undo_restored} "
            f"redo_reapplied={redo_reapplied}")


def save_reload_preserves_committed_roof_edit(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, storage

    building, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("save_reload_preserves_committed_roof_edit", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))
    _begin_op(roof)
    _translate(roof, dx=0.25)
    _commit_op(roof)
    record_before = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    path = h.out_dir() / "p6_2_roof_edit_save_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))

    reloaded = registry.resolve(gml_id)
    record_after = json.dumps(storage.read_record(reloaded).to_dict(), sort_keys=True) if reloaded else ""
    still_committed = (
        reloaded is not None
        and all(p.geometry_state == "committed" for p in storage.read_record(reloaded).polygons_of(gml_id))
    )
    ok = reloaded is not None and record_before == record_after and still_committed
    R.check("save_reload_preserves_committed_roof_edit", ok,
            f"resolved={reloaded is not None} record_equal={record_before == record_after} "
            f"still_committed={still_committed}")


def repeated_commit_is_deterministic(R):
    from city_modeler.shared.authoring_core.blender import roof_edit_session as rs
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_cs1()
    if not roofs:
        R.check("repeated_commit_is_deterministic", False, "no promoted RoofSurface found")
        return
    roof = roofs[0]
    gml_id = str(roof.get("gml_id"))

    rs.begin(gml_id)
    _translate(roof, dx=0.1)
    rs.commit(gml_id)
    once = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    result_twice = rs.commit_transform_drift(gml_id)  # no live change since last commit
    twice = json.dumps(storage.read_record(roof).to_dict(), sort_keys=True)

    ok = once == twice and result_twice.status == "no_op"
    R.check("repeated_commit_is_deterministic", ok, f"result_twice={result_twice} equal={once == twice}")


def main():
    R = h.Results(__file__)
    checks = [
        translated_roof_geometry_commits_and_preserves_ids,
        no_geometry_change_commit_is_noop,
        invalid_topology_refused_without_mutation,
        record_write_failure_rolls_back,
        transform_drift_commits_and_preserves_ids,
        hole_bearing_roof_translate_preserves_ring_identity,
        undo_redo_restores_roof_record,
        save_reload_preserves_committed_roof_edit,
        repeated_commit_is_deterministic,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
