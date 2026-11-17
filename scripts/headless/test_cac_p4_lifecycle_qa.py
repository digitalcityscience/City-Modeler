"""CAC-P4.9 combined Phase 4 lifecycle QA (gate G4).

One wall, the real operators a user would run, in this order (CAC-P4.10:
same-wall multi-opening; no direct ``create_feature`` call, no second
Building):

    Draw Window A -> F9 resize -> Stamp B onto the SAME wall -> Align B to A
    -> G (move A) -> dirty detected -> commit_transform_drift(A) -> Manual Sync
    -> Shift+D -> unresolved duplicate blocked -> initialize_duplicate (C, same
    wall) -> guarded Delete (B) -> undo / redo -> save -> reload -> CityGML 2
    export -> CityGML 3 export

Each step is bracketed with explicit ``ed.undo_push`` checkpoints (background
Blender has no other undo steps, see test_cac_p3_lifecycle_qa.py), and every
checkpoint verifies: unique semantic gml_ids, registry resolution, records,
host relation, exactly one wall ring per Window, no orphan wall ring, no
duplicate-id finding (except while the native duplicate is unresolved) and no
``coords_stale`` / transform drift after an explicit commit.

    BLENDER_USER_RESOURCES=<profile>/resources CAC_ADDON_MODULE=bl_ext.user_default.city_modeler \\
      "$BLENDER_EXE" --background --factory-startup --python scripts/headless/test_cac_p4_lifecycle_qa.py -- <out_dir>
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3
RESIZED = (0.4, 0.5)
DUPLICATE_CODES = {"duplicate_gml_id", "duplicate_record"}
STEPS = []
SIDE_CHECKS = {}


class StepFailed(AssertionError):
    """A checkpoint failed; later steps would only report its consequences."""


def _flag():
    os.environ["CGML_AUTHORING_CORE"] = "1"


def _object_mode():
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _select(active, selected=None):
    import bpy

    _object_mode()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in (selected or [active]):
        o.select_set(True)
    bpy.context.view_layer.objects.active = active


def _edit_face(wall, face_index):
    import bmesh
    import bpy

    _select(wall)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(wall.data)


def _new_object(call):
    """``(result, error, new object)`` of an operator call that creates one
    semantic object - a successful record-backed create/F9 (CAC-P3b.3) also
    creates its wall's/its own ``.cgml_display`` auxiliary object(s), which
    are new too but never the object this helper's callers actually want."""
    import bpy

    before = {o.name for o in bpy.data.objects}
    try:
        result, error = call(), ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e).strip()
    _object_mode()
    new = sorted(
        n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")
    )
    return result, error, (bpy.data.objects[new[0]] if new else None)


def _draw(wall, face_index, uv, width, height, feature_gml_id=""):
    import bpy

    _edit_face(wall, face_index)
    return _new_object(lambda: bpy.ops.cgml.facade_draw(
        'EXEC_DEFAULT', semantic_type="Window", width=width, height=height, depth=0.05,
        position_uv=uv, object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
        face_index=face_index, feature_gml_id=feature_gml_id))


def _stamp(source, wall, face_index, uv):
    import bpy

    _edit_face(wall, face_index)
    return _new_object(lambda: bpy.ops.cgml.semantic_stamp(
        'EXEC_DEFAULT', source_object_name=source.name, source_gml_id=str(source.get("gml_id") or ""),
        object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
        face_index=face_index, position_uv=uv))


def _run(call):
    try:
        return call(), ""
    except RuntimeError as e:
        return {"ERROR"}, str(e).strip()


def _fire_drift_handler():
    # Same approach as test_cac_p4_transform_commit.py: run the handler's own
    # marking code instead of trusting background depsgraph timing.
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    td._on_depsgraph_update(None, None)


def _in_plane_offset(ctx, du_m):
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    return (surface_world_point_from_uv(ctx, (du_m / ctx.width, 0.0))
            - surface_world_point_from_uv(ctx, (0.0, 0.0)))


def _rect(uv, ctx, w, hgt):
    return (uv[0] * ctx.width, uv[1] * ctx.height, w / 2 + 0.05, hgt / 2 + 0.05)


def _surface_uv(ctx, obj):
    from city_modeler.shared.facade_placement import surface_uv_from_world_point
    from mathutils import Vector

    centre = sum((obj.matrix_world @ v.co for v in obj.data.vertices), Vector()) / len(obj.data.vertices)
    return surface_uv_from_world_point(ctx, centre)


def _windows():
    import bpy
    from city_modeler.shared.authoring_core.blender import storage

    out = []
    for o in bpy.data.objects:
        if not storage.has_record(o):
            continue
        feature = storage.read_record(o).feature(str(o.get("gml_id") or ""))
        if feature is not None and feature.kind == "Window":
            out.append((o, feature))
    return out


def checkpoint(label, wall_names, *, duplicate_expected=False):
    """``(ok, summary, ids)`` for the invariants listed in the module docstring."""
    import bpy
    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.blender import transform_drift as td
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    _object_mode()
    registry.REGISTRY.rebuild()
    windows = _windows()
    ids = [str(o.get("gml_id")) for o, _f in windows]
    unique = len(set(ids)) == len(ids)
    problems = []
    if not duplicate_expected and not unique:
        problems.append(f"repeated gml_id {ids}")
    links_by_wall = {name: [] for name in wall_names}
    for obj, feature in windows:
        gml_id = str(obj.get("gml_id"))
        if unique and registry.REGISTRY.get(gml_id) is not obj:
            problems.append(f"{gml_id}: registry does not resolve to its object")
        host = registry.resolve(feature.host_id, obj.parent.name if obj.parent else "")
        if host is None or obj.parent is not host or host.name not in links_by_wall:
            problems.append(f"{gml_id}: host {feature.host_id!r} / parent {getattr(obj.parent, 'name', None)} unresolved")
            continue
        links_by_wall[host.name].append(feature.opening_link)
    rings = 0
    for name, links in links_by_wall.items():
        wall = bpy.data.objects[name]
        mesh_links = [link for _idx, _verts, link in wall_interior_ring_faces(wall) if link]
        wall_record = storage.read_record(wall)
        record_links = ([r.opening_link for p in wall_record.polygons for r in p.interiors if r.opening_link]
                        if wall_record else [])
        rings += len(mesh_links)
        for link in set(links):
            if mesh_links.count(link) != 1:
                problems.append(f"{name}/{link}: {mesh_links.count(link)} wall ring faces")
            if link not in record_links:
                problems.append(f"{name}/{link}: no wall RingRecord")
        orphans = sorted((set(mesh_links) | set(record_links)) - set(links))
        if orphans:
            problems.append(f"{name}: orphan wall rings {orphans}")
    wall_ids = {str(bpy.data.objects[n].get("gml_id") or "") for n in wall_names}
    findings = [f for f in run_validation()
                if f.gml_id in set(ids) | wall_ids or f.code in DUPLICATE_CODES]
    duplicate = sorted({f.code for f in findings if f.code in DUPLICATE_CODES})
    stale = sorted({f.gml_id for f in findings if f.code == "coords_stale"})
    dirty = sorted(g for g in set(ids) if td.is_dirty(g))
    blocking = sorted({f"{f.code}:{f.gml_id}" for f in findings
                       if f.severity == "error" and f.code not in DUPLICATE_CODES})
    if blocking:
        problems.append(f"blocking findings {blocking}")
    if duplicate_expected != bool(duplicate):
        problems.append(f"duplicate findings {duplicate} (expected={duplicate_expected})")
    if stale:
        problems.append(f"coords_stale {stale}")
    if dirty:
        problems.append(f"transform drift {dirty}")
    summary = f"{label}: windows={len(ids)} unique={unique} rings={rings} problems={problems}"
    if problems:
        summary += " links=" + str({str(o.get("gml_id")): (o.parent.name if o.parent else None, f.opening_link)
                                    for o, f in windows})
    return not problems, summary, sorted(ids)


def _undo_walk(step, target, max_steps=6):
    import bpy

    trail = []
    for _ in range(max_steps):
        op = bpy.ops.ed.undo if step == "undo" else bpy.ops.ed.redo
        if not op.poll():
            trail.append(f"{step}.poll=False")
            return False, trail
        op()
        n = len(_windows())
        trail.append(n)
        if target(n):
            return True, trail
    return False, trail


def _export_openings(path):
    s = h.export_structure(path)
    ids = [g for g, _host in s["openings"]]
    exteriors = {p["exterior"] for p in s["polygons"].values()}
    corrupt = [k for k, p in s["polygons"].items() if any(r in exteriors for r in p["interiors"])]
    return s, ids, corrupt


def _record(ok, text):
    STEPS.append(("OK " if ok else "FAIL ") + text)
    if not ok:
        raise StepFailed(text)


def full_phase4_lifecycle():
    import bpy
    from city_modeler.shared.authoring_core.blender import transform_drift as td
    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight
    from city_modeler.shared.facade_placement import surface_world_point_from_uv
    from mathutils import Vector

    out = h.out_dir()
    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    wall_name = wall.name
    ctx = h.surface_context(wall, face)
    walls = (wall_name,)
    half = (RESIZED[0] / 2 + 0.05, RESIZED[1] / 2 + 0.05)

    base = {}
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p4_lifecycle_base_v{major}.gml"
        h.export_scene(path, version)
        base[major] = _export_openings(path)

    # Draw A -> F9 resize (P4.3: undo the draw step, re-run with the kept id).
    uv_a = h.solid_area_position_uv(wall, face, ctx, *half)
    bpy.ops.ed.undo_push(message="cac lifecycle: before draw")
    result, error, a = _draw(wall, face, uv_a, SIZE, SIZE)
    bpy.ops.ed.undo_push(message="cac lifecycle: after draw")
    a_id = str(a.get("gml_id")) if a else ""
    ok, text, _ = checkpoint("draw", walls)
    _record(result == {"FINISHED"} and a is not None and ok, f"{text} result={result} {error}")

    bpy.ops.ed.undo()
    wall = bpy.data.objects[wall_name]
    result, error, a = _draw(wall, face, uv_a, *RESIZED, feature_gml_id=a_id)
    bpy.ops.ed.undo_push(message="cac lifecycle: after f9")
    ok, text, ids = checkpoint("f9_resize", walls)
    _record(result == {"FINISHED"} and a is not None and ok and ids == [a_id], f"{text} id_kept={ids == [a_id]} {error}")
    a_name = a.name

    # Stamp B (source A) onto A's own, now record-backed wall (CAC-P4.10).
    wall = bpy.data.objects[wall_name]
    uv_b = h.solid_area_position_uv(wall, face, ctx, *half, avoid=(_rect(uv_a, ctx, *RESIZED),))
    result, error, b = _stamp(bpy.data.objects[a_name], wall, face, uv_b)
    bpy.ops.ed.undo_push(message="cac lifecycle: after stamp")
    b_id, b_name = (str(b.get("gml_id")), b.name) if b else ("", "")
    ok, text, ids = checkpoint("stamp_same_wall", walls)
    _record(result == {"FINISHED"} and b is not None and ok and len(ids) == 2 and b_id != a_id,
            f"{text} result={result} {error}")

    # Align B (source) to A (active target), both on the same wall.
    _select(bpy.data.objects[a_name], [bpy.data.objects[b_name], bpy.data.objects[a_name]])
    result, error = _run(lambda: bpy.ops.cgml.align_objects('EXEC_DEFAULT'))
    bpy.ops.ed.undo_push(message="cac lifecycle: after align")
    ok, text, _ = checkpoint("align", walls)
    _record(result == {"FINISHED"} and ok, f"{text} result={result} {error}")

    # G on A, which shares its wall with B: move -> dirty -> export refused ->
    # explicit commit (was AB-19: refused as a dangling sibling ring).
    a = bpy.data.objects[a_name]
    a.location += _in_plane_offset(ctx, 0.03)
    bpy.context.view_layer.update()
    _fire_drift_handler()
    dirty = td.is_dirty(a_id)
    blocked = bool(export_preflight())
    _select(a)
    result, error = _run(lambda: bpy.ops.cgml.commit_transform_drift('EXEC_DEFAULT', object_gml_id=a_id))
    _fire_drift_handler()
    bpy.ops.ed.undo_push(message="cac lifecycle: after commit_transform_drift")
    ok, text, _ = checkpoint("commit_transform_drift_beside_second_window", walls)
    SIDE_CHECKS["transform_commit_beside_second_window_on_wall"] = (
        result == {"FINISHED"} and ok, f"{text} result={result} {error[:400]}")
    _record(dirty and blocked and result == {"FINISHED"} and ok,
            f"{text} dirty_after_move={dirty} preflight_blocked={blocked} result={result} {error}")

    # Manual Sync (Opening -> Ring) on a moved A.
    a = bpy.data.objects[a_name]
    a.location += _in_plane_offset(ctx, -0.03)
    bpy.context.view_layer.update()
    _select(a)
    result, error = _run(lambda: bpy.ops.cgml3.sync_opening_to_ring())
    _fire_drift_handler()
    bpy.ops.ed.undo_push(message="cac lifecycle: after manual sync")
    ok, text, _ = checkpoint("manual_sync", walls)
    _record(result == {"FINISHED"} and ok, f"{text} result={result} {error}")

    # Shift+D of A -> unresolved collision blocks export -> initialize as C (same wall).
    _select(bpy.data.objects[a_name])
    result, error, dup = _new_object(lambda: bpy.ops.object.duplicate())
    ok, text, ids = checkpoint("shift_d_unresolved", walls, duplicate_expected=True)
    blocked = bool(export_preflight())
    _record(result == {"FINISHED"} and dup is not None and ok and blocked and ids.count(a_id) == 2,
            f"{text} preflight_blocked={blocked}")
    wall = bpy.data.objects[wall_name]
    occupied = tuple(_rect(_surface_uv(ctx, bpy.data.objects[n]), ctx, *RESIZED) for n in (a_name, b_name))
    uv_c = h.solid_area_position_uv(wall, face, ctx, *half, avoid=occupied)
    centre = sum((dup.matrix_world @ v.co for v in dup.data.vertices), Vector()) / len(dup.data.vertices)
    dup.location += surface_world_point_from_uv(ctx, uv_c) - centre
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="cac lifecycle: before initialize")
    _select(dup)
    result, error = _run(lambda: bpy.ops.cgml.initialize_duplicate())
    c_id = str(bpy.context.active_object.get("gml_id"))
    _fire_drift_handler()
    bpy.ops.ed.undo_push(message="cac lifecycle: after initialize")
    ok, text, ids = checkpoint("initialize_duplicate", walls)
    _record(result == {"FINISHED"} and ok and len(ids) == 3 and c_id not in (a_id, b_id),
            f"{text} result={result} {error}")

    # Guarded delete of B -> undo -> redo.
    _select(bpy.data.objects[b_name])
    bpy.ops.ed.undo_push(message="cac lifecycle: before guarded delete")
    result, error = _run(lambda: bpy.ops.cgml.guarded_delete('EXEC_DEFAULT'))
    bpy.ops.ed.undo_push(message="cac lifecycle: after guarded delete")
    survivors = sorted((a_id, c_id))
    ok, text, ids = checkpoint("guarded_delete", walls)
    _record(result == {"FINISHED"} and ok and ids == survivors, f"{text} result={result} {error}")

    undone, trail_u = _undo_walk("undo", lambda n: n == 3)
    ok, text, ids = checkpoint("undo_delete", walls)
    _record(undone and ok and ids == sorted((a_id, b_id, c_id)), f"{text} trail={trail_u}")
    redone, trail_r = _undo_walk("redo", lambda n: n == 2)
    ok, text, ids = checkpoint("redo_delete", walls)
    _record(redone and ok and ids == survivors, f"{text} trail={trail_r}")

    # Save -> reload.
    blend = out / "p4_lifecycle_qa.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    _flag()
    ok, text, ids = checkpoint("save_reload", walls)
    _record(ok and ids == survivors, text)

    # CityGML 2 and 3 export of the final scene: A and C, nothing else.
    blocked = bool(export_preflight())
    _record(not blocked, f"final preflight_blocked={blocked}")
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p4_lifecycle_final_v{major}.gml"
        h.export_scene(path, version)
        _s, opening_ids, corrupt = _export_openings(path)
        base_ids = base[major][1]
        lint = h.xmllint(path, version)
        ok = (len(opening_ids) == len(base_ids) + 2 and len(set(opening_ids)) == len(opening_ids)
              and not corrupt and lint["exit"] == 0)
        _record(ok, f"export_v{major}: openings {len(base_ids)} -> {len(opening_ids)} "
                    f"distinct={len(set(opening_ids)) == len(opening_ids)} "
                    f"hole_equals_outline={corrupt} xmllint={lint}")


def draw_refusal_on_wall_with_legacy_openings_is_atomic(R):
    """A Draw onto a wall polygon that carries imported (legacy) opening rings
    is refused before any mutation (CAC-P4.10 Case B; was AB-20, where
    create_feature placed the Window and cut its ring before refusing on the
    host wall record's validation)."""
    import bpy
    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight
    from city_modeler.shared.ring_opening_sync import is_window_opening

    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    walls = sorted((o for o in bpy.data.objects if str(o.get("cgml3_feature") or "") == "WallSurface"),
                   key=lambda o: o.name)
    wall = next(w for w in walls if any(is_window_opening(c) for c in w.children))
    face = max(wall.data.polygons, key=lambda p: p.area).index
    ctx = h.surface_context(wall, face)
    uv = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2)

    def snapshot():
        return (len(bpy.data.objects), len(wall.data.polygons),
                sorted(o.name for o in bpy.data.objects if storage.has_record(o)))

    before = snapshot()
    result, error, created = _draw(wall, face, uv, SIZE, SIZE)
    after = snapshot()
    registry.REGISTRY.rebuild()
    blocked = bool(export_preflight())
    refused_cleanly = result != {"FINISHED"} and after == before and created is None
    ok = refused_cleanly and not blocked
    R.check("draw_refusal_on_wall_with_legacy_openings_is_atomic", ok,
            f"result={result} error={error[:240]!r} objects/faces/records before={before} after={after} "
            f"created={getattr(created, 'name', None)} preflight_blocked={blocked}")


def main():
    R = h.Results(__file__)
    try:
        full_phase4_lifecycle()
        R.check("full_phase4_lifecycle", True, "\n".join(STEPS))
    except StepFailed:
        R.check("full_phase4_lifecycle", False, "\n".join(STEPS))
    except Exception:
        R.check("full_phase4_lifecycle", False, "\n".join(STEPS) + "\n" + traceback.format_exc())
    for name, (ok, detail) in SIDE_CHECKS.items():
        R.check(name, ok, detail)
    try:
        draw_refusal_on_wall_with_legacy_openings_is_atomic(R)
    except Exception:
        R.check("draw_refusal_on_wall_with_legacy_openings_is_atomic", False, traceback.format_exc())
    R.finish()


main()
