"""P3b.1: transactional display regeneration / rollback hardening, real Blender.

Scope: ``shared/authoring_core/blender/edit_session.py::commit()`` +
``shared/authoring_core/blender/display_regen.py`` (``DisplayApplyError``,
``DisplayVerificationError``, ``snapshot_existing_display``,
``restore_display_snapshot``), per
``docs/agent/authoring-core/phase3b-design-note.md`` S10.

Proves: "dual-write legacy mesh -> write records -> regenerate display" is
one transaction. A failure at any of the three stages (topology planning,
mesh application, post-application verification) rolls the whole commit
back - legacy mesh, both records and display objects all restored to their
exact pre-commit state - rather than leaving valid records next to a
stale/broken display.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3b1_transactional_commit.py -- <out_dir>
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


def _place_window(wall, face_index, ctx, avoid=(), depth=0.0):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    # depth=0.0: a flat Window keeps a single PolygonRecord, the shape
    # build_surface_display (GS.1) expects - same convention GS.2's own
    # headless suite uses.
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=depth)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    fill = create_feature("Window", ctx, definition, placement)
    placed_rect = (uv[0] * ctx.width, uv[1] * ctx.height, SIZE / 2, SIZE / 2)
    return fill, placed_rect


def _translate(obj, dx=0.05, dy=0.0, dz=0.0):
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


def _op(idname, gml_id):
    import bpy

    ns = bpy.ops
    for part in idname.split("."):
        ns = getattr(ns, part)
    return ns('EXEC_DEFAULT', object_gml_id=gml_id)


def _begin_op(fill):
    _select_active(fill)
    return _op("cgml.feature_edit_begin", str(fill.get("gml_id") or ""))


def _commit_op(fill):
    _select_active(fill)
    return _op("cgml.feature_edit_commit", str(fill.get("gml_id") or ""))


def _ring_verts(wall, link):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    for _idx, verts, l in wall_interior_ring_faces(wall):
        if l == link:
            return tuple(tuple(v) for v in verts)
    return None


def _record_json(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record_set = storage.read_record(obj)
    return json.dumps(record_set.to_dict(), sort_keys=True) if record_set is not None else None


def _mesh_snapshot(obj):
    coords = tuple(tuple(v.co) for v in obj.data.vertices) if obj.data is not None else ()
    return coords, tuple(tuple(row) for row in obj.matrix_basis)


def _display_object(name):
    import bpy

    return bpy.data.objects.get(name)


def _mesh_verts(obj):
    return tuple(tuple(v.co) for v in obj.data.vertices) if obj is not None and obj.data is not None else None


def p3b1_successful_commit_regenerates_display_with_preserved_identity(R):
    """Baseline: a normal successful commit still regenerates the wall +
    Window display from the new records, and no semantic identity (gml_id,
    opening_link) is ever taken from the display mesh itself."""
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    window_after = storage.read_record(fill).feature(window_gml_id)
    wall_after = storage.read_record(wall).feature(wall_gml_id)
    ring = _ring_verts(wall, window_after.opening_link) if window_after else None

    wall_display = _display_object(f"{wall.name}.cgml_display")
    window_display = _display_object(f"{fill.name}.cgml_display")
    display_has_no_identity = (
        wall_display is not None and not str(wall_display.get("gml_id") or "").strip()
        and not storage.has_record(wall_display)
    )

    ok = (
        result.status == "committed"
        and window_after is not None and window_after.gml_id == window_gml_id
        and wall_after is not None and wall_after.gml_id == wall_gml_id
        and bool(window_after.opening_link) and ring is not None
        and wall_display is not None and len(wall_display.data.polygons) > 0
        and window_display is not None and len(window_display.data.polygons) > 0
        and display_has_no_identity
    )
    R.check(
        "p3b1_successful_commit_regenerates_display_with_preserved_identity", ok,
        f"result={result} window_gml_id_ok={window_after and window_after.gml_id == window_gml_id} "
        f"wall_gml_id_ok={wall_after and wall_after.gml_id == wall_gml_id} "
        f"opening_link={window_after.opening_link if window_after else None} ring_present={ring is not None} "
        f"display_has_no_identity={display_has_no_identity}",
    )


def p3b1_topology_build_failure_rolls_back_full_commit(R):
    """A ``DisplayBuildError`` during topology planning (before any Blender
    object is touched) must roll back the legacy mesh dual-write and both
    just-written records - not just fail to build a display next to
    already-committed records."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import display_builder, registry

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)

    window_record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    ring_link_before = None
    from city_modeler.shared.authoring_core.blender import storage
    feat = storage.read_record(fill).feature(window_gml_id)
    if feat is not None:
        ring_link_before = feat.opening_link
    ring_before = _ring_verts(wall, ring_link_before)
    names_before = {o.name for o in bpy.data.objects}

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: topology planning failure (P3b.1 test)")

    display_builder.build_surface_display = _always_fail
    raised = False
    try:
        es.commit(window_gml_id)
    except Exception:
        raised = True
    finally:
        display_builder.build_surface_display = original

    window_record_after = _record_json(fill)
    wall_record_after = _record_json(wall)
    ring_after = _ring_verts(wall, ring_link_before)
    names_after = {o.name for o in bpy.data.objects}

    registry.REGISTRY.rebuild()
    window_resolves = registry.resolve(window_gml_id) is fill
    wall_resolves = registry.resolve(wall_gml_id) is wall

    ok = (
        raised
        and window_record_after == window_record_before
        and wall_record_after == wall_record_before
        and ring_after == ring_before
        and names_after == names_before  # no orphan display objects
        and window_resolves and wall_resolves
        and es.active_session(window_gml_id) is not None  # session recoverable, not silently closed
    )
    R.check(
        "p3b1_topology_build_failure_rolls_back_full_commit", ok,
        f"raised={raised} record_window_unchanged={window_record_after == window_record_before} "
        f"record_wall_unchanged={wall_record_after == wall_record_before} "
        f"ring_unchanged={ring_after == ring_before} no_orphans={names_after == names_before} "
        f"window_resolves={window_resolves} wall_resolves={wall_resolves}",
    )
    es.cancel(window_gml_id)


def p3b1_mesh_application_failure_rolls_back_full_commit(R):
    """A failure while applying an already-planned ``DisplayMesh`` onto an
    *existing* display object (created by a prior successful commit) must
    restore that object's exact pre-attempt mesh - not leave it half-written
    next to rolled-back records."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import display_regen, storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    # Establish a real, existing display object first (normal successful commit).
    es.begin(window_gml_id)
    _translate(fill, dx=0.03)
    first = es.commit(window_gml_id)

    wall_display_name = f"{wall.name}.cgml_display"
    window_display_name = f"{fill.name}.cgml_display"
    wall_display_verts_before = _mesh_verts(_display_object(wall_display_name))
    window_display_verts_before = _mesh_verts(_display_object(window_display_name))
    window_record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    feat = storage.read_record(fill).feature(window_gml_id)
    ring_link = feat.opening_link if feat else None
    ring_before = _ring_verts(wall, ring_link)
    names_before = {o.name for o in bpy.data.objects}

    es.begin(window_gml_id)
    _translate(fill, dx=0.03)

    original = display_regen._write_display_mesh

    def _always_fail(_obj, _display_mesh):
        raise RuntimeError("injected: mesh application failure (P3b.1 test)")

    display_regen._write_display_mesh = _always_fail
    raised = False
    try:
        es.commit(window_gml_id)
    except Exception:
        raised = True
    finally:
        display_regen._write_display_mesh = original

    wall_display_after = _display_object(wall_display_name)
    window_display_after = _display_object(window_display_name)
    names_after = {o.name for o in bpy.data.objects}

    ok = (
        first.status == "committed"
        and raised
        and _record_json(fill) == window_record_before
        and _record_json(wall) == wall_record_before
        and _ring_verts(wall, ring_link) == ring_before
        and names_after == names_before  # no new/removed objects - existing ones just restored
        and _mesh_verts(wall_display_after) == wall_display_verts_before
        and _mesh_verts(window_display_after) == window_display_verts_before
    )
    R.check(
        "p3b1_mesh_application_failure_rolls_back_full_commit", ok,
        f"first={first} raised={raised} names_unchanged={names_after == names_before} "
        f"wall_display_restored={_mesh_verts(wall_display_after) == wall_display_verts_before} "
        f"window_display_restored={_mesh_verts(window_display_after) == window_display_verts_before}",
    )
    es.cancel(window_gml_id)


def p3b1_verification_failure_rolls_back_and_removes_orphan(R):
    """A display mesh that gets written but does not match what was planned
    (the post-application verification seam) must be caught, and - since
    this is the surface's first-ever display regeneration - the freshly
    created display object must be removed entirely, not left as an orphan
    with wrong content."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import display_regen

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)

    window_record_before = _record_json(fill)
    wall_record_before = _record_json(wall)
    names_before = {o.name for o in bpy.data.objects}

    original = display_regen._write_display_mesh

    def _write_wrong_mesh(obj, _display_mesh):
        # Writes a real but deliberately mismatched mesh (a single triangle)
        # so the genuine _verify_display_mesh seam - not a patched stand-in -
        # is what catches the discrepancy.
        mesh = obj.data
        mesh.clear_geometry()
        mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [[0, 1, 2]])
        mesh.update()

    display_regen._write_display_mesh = _write_wrong_mesh
    raised_verification_error = False
    try:
        es.commit(window_gml_id)
    except es.EditSessionError as exc:
        # commit() wraps every rollback-triggering failure into an
        # EditSessionError (design note S10); the original cause is chained
        # via `raise ... from exc`, which is what this checks.
        raised_verification_error = isinstance(exc.__cause__, display_regen.DisplayVerificationError)
    except Exception:
        raised_verification_error = False  # wrong exception type surfaced
    finally:
        display_regen._write_display_mesh = original

    names_after = {o.name for o in bpy.data.objects}
    no_orphans = names_after == names_before

    ok = (
        raised_verification_error
        and _record_json(fill) == window_record_before
        and _record_json(wall) == wall_record_before
        and no_orphans
    )
    R.check(
        "p3b1_verification_failure_rolls_back_and_removes_orphan", ok,
        f"raised_verification_error={raised_verification_error} "
        f"record_window_unchanged={_record_json(fill) == window_record_before} "
        f"record_wall_unchanged={_record_json(wall) == wall_record_before} no_orphans={no_orphans}",
    )
    es.cancel(window_gml_id)


def p3b1_sibling_opening_preserved_after_failed_commit(R):
    """A wall with two record-backed Windows (CAC-P4.10 multi-opening
    authoring): a failed commit on Window B must not disturb Window A's
    record, ring or display - even though the wall's own record (shared by
    both openings) is fully rolled back."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import display_builder, storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill_a, rect_a = _place_window(wall, face_index, ctx)
    a_gml_id = str(fill_a.get("gml_id"))

    es.begin(a_gml_id)
    _translate(fill_a, dx=0.03)
    result_a = es.commit(a_gml_id)

    fill_b, _ = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    b_gml_id = str(fill_b.get("gml_id"))

    a_feat = storage.read_record(fill_a).feature(a_gml_id)
    a_record_before = _record_json(fill_a)
    a_ring_before = _ring_verts(wall, a_feat.opening_link) if a_feat else None
    a_display_verts_before = _mesh_verts(_display_object(f"{fill_a.name}.cgml_display"))

    es.begin(b_gml_id)
    _translate(fill_b, dx=0.03)

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: sibling-preservation failure (P3b.1 test)")

    display_builder.build_surface_display = _always_fail
    raised = False
    try:
        es.commit(b_gml_id)
    except Exception:
        raised = True
    finally:
        display_builder.build_surface_display = original

    a_feat_after = storage.read_record(fill_a).feature(a_gml_id)
    a_record_after = _record_json(fill_a)
    a_ring_after = _ring_verts(wall, a_feat_after.opening_link) if a_feat_after else None
    a_display_verts_after = _mesh_verts(_display_object(f"{fill_a.name}.cgml_display"))

    ok = (
        result_a.status == "committed"
        and raised
        and a_record_after == a_record_before
        and a_ring_after == a_ring_before
        and a_display_verts_after == a_display_verts_before
    )
    R.check(
        "p3b1_sibling_opening_preserved_after_failed_commit", ok,
        f"result_a={result_a} raised={raised} a_record_unchanged={a_record_after == a_record_before} "
        f"a_ring_unchanged={a_ring_after == a_ring_before} "
        f"a_display_unchanged={a_display_verts_after == a_display_verts_before}",
    )
    es.cancel(b_gml_id)


def p3b1_three_sibling_openings_preserved_after_failed_commit(R):
    """P3b.2 (phase3b-design-note.md §7/§13): the N-Window generalization of
    ``p3b1_sibling_opening_preserved_after_failed_commit`` - a wall with
    three record-backed Windows (CAC-P4.10 multi-opening authoring). A
    failed commit on Window C must leave *both* siblings A and B (their
    records, rings and displays) untouched, and the wall's own rolled-back
    display must be the *whole* wall display (all three holes), not just
    the edited opening's slice."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import display_builder, storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill_a, rect_a = _place_window(wall, face_index, ctx)
    a_gml_id = str(fill_a.get("gml_id"))
    es.begin(a_gml_id)
    _translate(fill_a, dx=0.03)
    result_a = es.commit(a_gml_id)

    fill_b, rect_b = _place_window(wall, face_index, ctx, avoid=(rect_a,))
    b_gml_id = str(fill_b.get("gml_id"))
    es.begin(b_gml_id)
    _translate(fill_b, dx=0.03)
    result_b = es.commit(b_gml_id)

    fill_c, _ = _place_window(wall, face_index, ctx, avoid=(rect_a, rect_b))
    c_gml_id = str(fill_c.get("gml_id"))

    def _snapshot(fill, gml_id):
        feat = storage.read_record(fill).feature(gml_id)
        return (
            _record_json(fill),
            _ring_verts(wall, feat.opening_link) if feat else None,
            _mesh_verts(_display_object(f"{fill.name}.cgml_display")),
        )

    a_before = _snapshot(fill_a, a_gml_id)
    b_before = _snapshot(fill_b, b_gml_id)
    wall_display_before = _mesh_verts(_display_object(f"{wall.name}.cgml_display"))

    es.begin(c_gml_id)
    _translate(fill_c, dx=0.03)

    original = display_builder.build_surface_display

    def _always_fail(*_args, **_kwargs):
        raise display_builder.DisplayBuildError("injected: three-sibling-preservation failure (P3b.2 test)")

    display_builder.build_surface_display = _always_fail
    raised = False
    try:
        es.commit(c_gml_id)
    except Exception:
        raised = True
    finally:
        display_builder.build_surface_display = original

    a_after = _snapshot(fill_a, a_gml_id)
    b_after = _snapshot(fill_b, b_gml_id)
    wall_display_after = _mesh_verts(_display_object(f"{wall.name}.cgml_display"))

    ok = (
        result_a.status == "committed"
        and result_b.status == "committed"
        and raised
        and a_after == a_before
        and b_after == b_before
        and wall_display_after == wall_display_before
    )
    R.check(
        "p3b1_three_sibling_openings_preserved_after_failed_commit", ok,
        f"result_a={result_a} result_b={result_b} raised={raised} "
        f"a_unchanged={a_after == a_before} b_unchanged={b_after == b_before} "
        f"wall_display_unchanged={wall_display_after == wall_display_before}",
    )
    es.cancel(c_gml_id)


def p3b1_cancel_unaffected_by_transactional_commit_changes(R):
    """Reconfirm cancel() is unchanged by P3b.1: begin -> edit -> cancel
    never writes a record and never regenerates display, exactly as before."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    record_before = _record_json(fill)
    mesh_before = _mesh_snapshot(fill)
    names_before = {o.name for o in bpy.data.objects}

    es.begin(window_gml_id)
    _translate(fill, dx=0.05, dy=0.03)
    es.cancel(window_gml_id)

    record_after = _record_json(fill)
    mesh_after = _mesh_snapshot(fill)
    names_after = {o.name for o in bpy.data.objects}

    ok = (
        record_before == record_after
        and mesh_before == mesh_after
        and names_after == names_before
        and es.active_session(window_gml_id) is None
    )
    R.check(
        "p3b1_cancel_unaffected_by_transactional_commit_changes", ok,
        f"record_unchanged={record_before == record_after} mesh_unchanged={mesh_before == mesh_after} "
        f"no_display_created={names_after == names_before}",
    )


def p3b1_undo_redo_after_successful_commit(R):
    """A successful commit (real operator, real undo step) can be undone and
    redone as one clean step, its display mesh included.

    Since CAC-P3b.3, create_feature itself already regenerates the wall's
    display object (it exists before this commit even starts), so undoing
    the commit alone no longer removes the object - only its mesh, which
    this commit rewrote, reverts to what create_feature itself last wrote.
    """
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill, _ = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_name, fill_name = wall.name, fill.name
    wall_display_name = f"{wall_name}.cgml_display"

    display_before_commit = _mesh_verts(_display_object(wall_display_name))

    bpy.ops.ed.undo_push(message="p3b1: before edit begin")
    begin_result = _begin_op(fill)
    _translate(fill, dx=0.05)
    bpy.ops.ed.undo_push(message="p3b1: after geometry edit")
    commit_result = _commit_op(fill)
    bpy.ops.ed.undo_push(message="p3b1: after commit")

    display_after_commit = _display_object(wall_display_name)
    verts_after_commit = _mesh_verts(display_after_commit)
    committed_ok = (
        commit_result == {"FINISHED"} and display_after_commit is not None
        and verts_after_commit != display_before_commit
    )

    bpy.ops.ed.undo()  # undoes the commit's mesh rewrite in one step
    verts_after_undo = _mesh_verts(_display_object(wall_display_name))
    undo_restored_display_mesh = verts_after_undo == display_before_commit

    bpy.ops.ed.redo()
    verts_after_redo = _mesh_verts(_display_object(wall_display_name))
    redo_reapplied_display_mesh = verts_after_redo == verts_after_commit

    ok = begin_result == {"FINISHED"} and committed_ok and undo_restored_display_mesh and redo_reapplied_display_mesh
    R.check(
        "p3b1_undo_redo_after_successful_commit", ok,
        f"begin={begin_result} committed_ok={committed_ok} "
        f"undo_restored_display_mesh={undo_restored_display_mesh} "
        f"redo_reapplied_display_mesh={redo_reapplied_display_mesh}",
    )


def main():
    R = h.Results(__file__)
    checks = [
        p3b1_successful_commit_regenerates_display_with_preserved_identity,
        p3b1_topology_build_failure_rolls_back_full_commit,
        p3b1_mesh_application_failure_rolls_back_full_commit,
        p3b1_verification_failure_rolls_back_and_removes_orphan,
        p3b1_sibling_opening_preserved_after_failed_commit,
        p3b1_three_sibling_openings_preserved_after_failed_commit,
        p3b1_cancel_unaffected_by_transactional_commit_changes,
        p3b1_undo_redo_after_successful_commit,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
