"""CAC-P4.10: several record-backed Windows on one WallSurface, and atomic create.

Same-wall multi-opening (policy PLAN.md §6 Phase 4 "P4.10", Case A), all
through the real operators on one Railway WallSurface:

- A  Draw A -> Draw B on the same wall: independent ids, records and rings
- B  Draw A -> F9 resize -> Stamp B onto the same wall; A unchanged
- C  Align B to A; A's record and RingRecord unchanged
- D  G on A -> commit_transform_drift(A), and the P4.2 session commit, beside
     B (AB-19): A updated, B's record and RingRecord byte-identical; a drifted
     sibling ring is not silently committed
- E  guarded Delete of A removes only Ring A; undo/redo touch only A
- H  F9 resize of B leaves A untouched
- undo/redo across Draw A, Draw B and a transform commit
- I  save/reload of a wall with three record-backed Windows
- J  CityGML 2 and 3 export of two Windows on one wall
- a record-backed Window on one wall does not block Draw/Stamp on another
  wall of the same Building

Atomic create (AB-20 / AB-20b):

- a record-backed Draw onto a wall polygon that carries imported (unmanaged)
  opening rings is refused before any mutation (Case B);
- the same for a wall carrying a flag-off authored Window (a linked ring with
  no record) - committing that wall would record a dangling ring (AB-20b);
- a failure injected after placement leaves the scene exactly as it was
  (placement rollback), both before and after the Window record is written.

"Exactly as it was" is ``scene_state()``: every object's parent, transform
and custom properties (records included), every mesh's vertices, faces and
material slots (with the materials' custom properties), the object, mesh and
material name sets, registry resolution and the full P3.3 finding list.

    BLENDER_USER_RESOURCES=<profile>/resources CAC_ADDON_MODULE=bl_ext.user_default.city_modeler \\
      "$BLENDER_EXE" --background --factory-startup --python scripts/headless/test_cac_p4_multi_opening.py -- <out_dir>
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
    """``(result, error, new object)`` of an operator call that creates at
    most one semantic object - a successful record-backed create/F9
    (CAC-P3b.3) also creates its wall's/its own ``.cgml_display`` auxiliary
    object(s), which are new too but never the object this helper's callers
    actually want."""
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


def _draw(wall, face_index, uv, width=SIZE, height=SIZE, feature_gml_id=""):
    import bpy

    _edit_face(wall, face_index)
    return _new_object(lambda: bpy.ops.cgml.facade_draw(
        'EXEC_DEFAULT', semantic_type="Window", width=width, height=height, depth=0.05,
        position_uv=uv, object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
        face_index=face_index, feature_gml_id=feature_gml_id))


def _rect(uv, ctx, w=SIZE, hgt=SIZE):
    return (uv[0] * ctx.width, uv[1] * ctx.height, w / 2 + 0.05, hgt / 2 + 0.05)


def scene_state():
    """Everything a create can touch, keyed by name (see module docstring)."""
    import bpy
    from city_modeler.shared.authoring_core.blender import registry
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    def props(idblock):
        return {k: repr(h._plain(idblock[k])) for k in idblock.keys()}

    _object_mode()
    bpy.context.view_layer.update()
    objects = {}
    for o in bpy.data.objects:
        entry = {"parent": o.parent.name if o.parent else None, "props": props(o),
                 "matrix": [round(v, 5) for row in o.matrix_world for v in row]}
        if o.type == "MESH":
            me = o.data
            entry["mesh"] = me.name
            entry["verts"] = [tuple(round(c, 5) for c in v.co) for v in me.vertices]
            entry["faces"] = [(tuple(p.vertices), p.material_index) for p in me.polygons]
            entry["materials"] = [(m.name, props(m)) if m else None for m in me.materials]
            entry["mesh_props"] = props(me)
        objects[o.name] = entry
    registry.REGISTRY.rebuild()
    ids = sorted({str(o.get("gml_id") or "") for o in bpy.data.objects} - {""})
    resolution = {gid: sorted(o.name for o in registry.REGISTRY.get_all(gid)) for gid in ids}
    findings = sorted((f.code, f.gml_id, tuple(f.related)) for f in run_validation())
    return {
        "objects": objects,
        "meshes": sorted(m.name for m in bpy.data.meshes),
        "materials": sorted(m.name for m in bpy.data.materials),
        "registry": resolution,
        "findings": findings,
    }


def state_diff(before, after):
    out = []
    for key in ("meshes", "materials", "registry", "findings"):
        if before[key] != after[key]:
            out.append(key)
    b, a = before["objects"], after["objects"]
    out += [f"+{n}" for n in sorted(set(a) - set(b))]
    out += [f"-{n}" for n in sorted(set(b) - set(a))]
    out += [f"~{n}" for n in sorted(set(a) & set(b)) if a[n] != b[n]]
    return out


def _wall_with_imported_openings():
    """A Railway WallSurface with imported Window children and the polygon their rings sit on."""
    import bpy
    from city_modeler.shared.ring_opening_sync import is_window_opening

    walls = sorted((o for o in bpy.data.objects if str(o.get("cgml3_feature") or "") == "WallSurface"),
                   key=lambda o: o.name)
    wall = next(w for w in walls if any(is_window_opening(c) for c in w.children))
    face = max(wall.data.polygons, key=lambda p: p.area).index
    return wall, face


# --------------------------------------------------------------------------
# Atomic create (AB-20, AB-20b, rollback)
# --------------------------------------------------------------------------

def draw_on_imported_opening_polygon_refused_atomically(R):
    """AB-20: Case B refusal happens before placement - zero mutation."""
    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = _wall_with_imported_openings()
    ctx = h.surface_context(wall, face)
    uv = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2)
    before = scene_state()
    result, error, created = _draw(wall, face, uv)
    after = scene_state()
    diff = state_diff(before, after)
    ok = (result == {"ERROR"} and created is None and not diff
          and "does not manage" in error and "imported opening ring" in error)
    R.check("draw_on_imported_opening_polygon_refused_atomically", ok,
            f"result={result} created={getattr(created, 'name', None)} diff={diff[:8]} error={error[:300]!r}")


def draw_beside_unrecorded_linked_opening_refused_atomically(R):
    """AB-20b: a flag-off drawn Window leaves a linked ring with no record;
    a record-backed Draw on that wall would commit it as a dangling ring."""
    _flag(False)
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face)
    uv_legacy = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2)
    result, error, legacy = _draw(wall, face, uv_legacy)
    if result != {"FINISHED"} or legacy is None:
        R.check("draw_beside_unrecorded_linked_opening_refused_atomically", False,
                f"precondition: flag-off Draw -> {result} {error}")
        return
    _flag()
    uv = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2, avoid=(_rect(uv_legacy, ctx),))
    before = scene_state()
    result, error, created = _draw(wall, face, uv)
    diff = state_diff(before, scene_state())
    ok = (result == {"ERROR"} and created is None and not diff
          and "does not manage" in error and "carry no record" in error)
    R.check("draw_beside_unrecorded_linked_opening_refused_atomically", ok,
            f"result={result} created={getattr(created, 'name', None)} diff={diff[:8]} error={error[:300]!r}")


def _injected(R, check, target, attribute, make_failing, *, second=False):
    """Inject a failure (``target.attribute``) into a Draw through the real
    operator; the scene must come back exactly, and the same Draw must succeed
    once the injection is removed. ``second``: the failing Draw is a second
    Window on a wall that is already record-backed (its wall record must come
    back unchanged); otherwise the first Draw on a clean wall (the Building's
    LoD promotion must come back unchanged)."""
    import bpy

    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    wall_name = wall.name
    ctx = h.surface_context(wall, face)
    uv = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2)
    if second:
        result, error, a = _draw(wall, face, uv)
        if result != {"FINISHED"} or a is None:
            R.check(check, False, f"precondition: Draw A -> {result} {error}")
            return
        uv = h.solid_area_position_uv(bpy.data.objects[wall_name], face, ctx, SIZE / 2, SIZE / 2,
                                      avoid=(_rect(uv, ctx),))
    before = scene_state()
    original = getattr(target, attribute)
    setattr(target, attribute, make_failing(original))
    try:
        result, error, created = _draw(bpy.data.objects[wall_name], face, uv)
    finally:
        setattr(target, attribute, original)
    diff = state_diff(before, scene_state())
    retry, retry_error, made = _draw(bpy.data.objects[wall_name], face, uv)
    ok = (result == {"ERROR"} and "injected" in error and created is None and not diff
          and retry == {"FINISHED"} and made is not None)
    R.check(check, ok, f"result={result} error={error[:200]!r} diff={diff[:8]} "
                       f"retry={retry} {retry_error[:200]}")


def rollback_after_placement_before_records(R):
    """Failure after the wall is cut, before any record is derived."""
    from city_modeler.shared.authoring_core.blender import usecases

    def failing(_original):
        def boom(*_args, **_kwargs):
            raise RuntimeError("injected failure after placement")
        return boom

    _injected(R, "rollback_after_placement_before_records", usecases, "_commit_window_polygon", failing)


def rollback_after_window_record_written(R):
    """Failure while writing the wall record - the Window record is already
    written at that point, and must not survive."""
    from city_modeler.shared.authoring_core.blender import usecases

    def failing(original):
        def write(obj, record_set):
            if str(obj.get("cgml3_feature") or "") == "WallSurface":
                raise RuntimeError("injected failure writing the host wall record")
            return original(obj, record_set)
        return write

    _injected(R, "rollback_after_window_record_written", usecases.storage, "write_record", failing)


def rollback_on_record_backed_wall(R):
    """G: the same injection into a second Window's Draw on a wall that is
    already record-backed - its wall record and Window A come back unchanged."""
    from city_modeler.shared.authoring_core.blender import usecases

    def failing(_original):
        def boom(*_args, **_kwargs):
            raise RuntimeError("injected failure after placement")
        return boom

    _injected(R, "rollback_on_record_backed_wall", usecases, "_commit_window_polygon", failing, second=True)


# --------------------------------------------------------------------------
# Same-wall multi-opening lifecycle (A-J)
# --------------------------------------------------------------------------

RESIZED = (0.4, 0.5)


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
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    td._on_depsgraph_update(None, None)


def _in_plane_offset(ctx, du_m, dv_m=0.0):
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    return (surface_world_point_from_uv(ctx, (du_m / ctx.width, dv_m / ctx.height))
            - surface_world_point_from_uv(ctx, (0.0, 0.0)))


def _obj(name):
    import bpy

    return bpy.data.objects.get(name)


def _record_text(obj):
    """The object's stored record, byte for byte."""
    from city_modeler.shared.authoring_core.blender import storage

    return str(obj.get(storage.RECORD_KEY) or "")


def _feature(obj):
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(obj)
    return record.feature(str(obj.get("gml_id") or "")) if record else None


def _wall_rings(wall):
    """``{opening_link: RingRecord}`` from the wall's stored record."""
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(wall)
    if record is None:
        return {}
    return {r.opening_link: r for p in record.polygons for r in p.interiors if r.opening_link}


def _mesh_ring_links(wall):
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    return sorted(link for _i, _v, link in wall_interior_ring_faces(wall) if link)


def _host_codes(wall):
    """Every P3.3 finding code about ``wall``'s host closure (errors and warnings)."""
    from city_modeler.shared.authoring_core.blender.consistency_view import host_closure_findings

    return sorted({f"{f.code}:{f.gml_id}" for f in host_closure_findings(wall)})


def _base():
    """Railway v3 with Window A drawn on the first exterior wall; ``(wall name,
    face, ctx, A name)``. Everything through the real Draw operator."""
    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face)
    uv_a = h.solid_area_position_uv(wall, face, ctx, SIZE / 2, SIZE / 2)
    result, error, a = _draw(wall, face, uv_a)
    if result != {"FINISHED"} or a is None:
        raise AssertionError(f"precondition: Draw A -> {result} {error}")
    return wall.name, face, ctx, a.name, uv_a


def _second_uv(wall_name, face, ctx, *occupied_uvs, size=(SIZE, SIZE)):
    wall = _obj(wall_name)
    return h.solid_area_position_uv(wall, face, ctx, size[0] / 2 + 0.05, size[1] / 2 + 0.05,
                                    avoid=tuple(_rect(uv, ctx, 0.5, 0.6) for uv in occupied_uvs))


def _two_windows():
    wall_name, face, ctx, a_name, uv_a = _base()
    uv_b = _second_uv(wall_name, face, ctx, uv_a)
    result, error, b = _draw(_obj(wall_name), face, uv_b)
    if result != {"FINISHED"} or b is None:
        raise AssertionError(f"precondition: Draw B on the same wall -> {result} {error}")
    return wall_name, face, ctx, a_name, b.name, uv_a, uv_b


def _clean_host(wall, allowed=()):
    """No error finding and no coords_stale about the wall's closure."""
    codes = _host_codes(wall)
    bad = [c for c in codes if c.split(":")[0] not in allowed
           and c.split(":")[0] in {"dangling_ring", "missing_ring_pairing", "orphan_opening", "unrecorded_host",
                                   "duplicate_gml_id", "duplicate_record", "coords_stale", "missing_object",
                                   "opening_link_mismatch", "polygon_ids_mismatch", "host_id_mismatch",
                                   "parent_id_mismatch", "kind_mismatch", "foreign_record"}]
    return not bad, codes


def same_wall_draw_creates_independent_windows(R):
    """A: Draw A -> Draw B on the SAME wall, both through the Draw operator."""
    from city_modeler.shared.authoring_core.blender import registry, storage

    wall_name, _face, _ctx, a_name, b_name, _ua, _ub = _two_windows()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    registry.REGISTRY.rebuild()
    fa, fb = _feature(a), _feature(b)
    wall_record = storage.read_record(wall)
    wall_id = str(wall.get("gml_id"))
    rings = _wall_rings(wall)
    clean, codes = _clean_host(wall)
    ok = (fa is not None and fb is not None and fa.gml_id != fb.gml_id
          and registry.REGISTRY.get(fa.gml_id) is a and registry.REGISTRY.get(fb.gml_id) is b
          and wall_record is not None and [f.gml_id for f in wall_record.features] == [wall_id]
          and fa.host_id == fb.host_id == wall_id and fa.opening_link != fb.opening_link
          and set(rings) == {fa.opening_link, fb.opening_link}
          and all(len(set(rings[link].coords)) >= 3 for link in rings)
          and rings[fa.opening_link].ring_id != rings[fb.opening_link].ring_id
          and _mesh_ring_links(wall) == sorted(rings) and clean)
    R.check("same_wall_draw_creates_independent_windows", ok,
            f"ids={getattr(fa, 'gml_id', None)},{getattr(fb, 'gml_id', None)} rings={sorted(rings)} "
            f"mesh={_mesh_ring_links(wall)} host={codes}")


def same_wall_stamp_after_f9_resize(R):
    """B: Draw A -> F9 resize -> Stamp B (source A) onto the SAME wall."""
    import bpy

    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    wall_name = wall.name
    ctx = h.surface_context(wall, face)
    uv_a = h.solid_area_position_uv(wall, face, ctx, RESIZED[0] / 2 + 0.05, RESIZED[1] / 2 + 0.05)
    bpy.ops.ed.undo_push(message="multi-opening: before draw")
    result, error, a = _draw(wall, face, uv_a)
    bpy.ops.ed.undo_push(message="multi-opening: after draw")
    a_id = str(a.get("gml_id")) if a else ""
    bpy.ops.ed.undo()  # F9: Blender reverts the step and re-runs execute() with the kept id
    result, error, a = _draw(_obj(wall_name), face, uv_a, *RESIZED, feature_gml_id=a_id)
    bpy.ops.ed.undo_push(message="multi-opening: after f9")
    if result != {"FINISHED"} or a is None or str(a.get("gml_id")) != a_id:
        R.check("same_wall_stamp_after_f9_resize", False, f"precondition: F9 -> {result} {error}")
        return
    a_name = a.name
    a_record, a_ring = _record_text(a), _wall_rings(_obj(wall_name))[_feature(a).opening_link]
    uv_b = _second_uv(wall_name, face, ctx, uv_a, size=RESIZED)
    result, error, b = _stamp(_obj(a_name), _obj(wall_name), face, uv_b)
    wall, a = _obj(wall_name), _obj(a_name)
    fb = _feature(b) if b is not None else None
    rings = _wall_rings(wall)
    clean, codes = _clean_host(wall)
    ok = (result == {"FINISHED"} and "refused" not in error and fb is not None
          and fb.gml_id not in ("", a_id) and fb.generator == "semantic_stamp"
          and fb.host_id == str(wall.get("gml_id")) and fb.opening_link != _feature(a).opening_link
          and fb.opening_link in rings and _record_text(a) == a_record
          and rings.get(_feature(a).opening_link) == a_ring and clean)
    R.check("same_wall_stamp_after_f9_resize", ok,
            f"result={result} error={error[:200]!r} b={getattr(fb, 'gml_id', None)} "
            f"a_record_unchanged={_record_text(a) == a_record} rings={sorted(rings)} host={codes}")


def align_second_window_keeps_sibling(R):
    """C: A + B on one wall -> Align B (source) to A (active target)."""
    import bpy

    wall_name, _face, ctx, a_name, b_name, _ua, _ub = _two_windows()
    b = _obj(b_name)
    b.location += _in_plane_offset(ctx, 0.0, 0.04)  # a near-miss for Align to correct
    bpy.context.view_layer.update()
    a, wall = _obj(a_name), _obj(wall_name)
    a_record, a_ring = _record_text(a), _wall_rings(wall)[_feature(a).opening_link]
    b_record = _record_text(b)
    _select(a, [b, a])
    result, error = _run(lambda: bpy.ops.cgml.align_objects('EXEC_DEFAULT'))
    _fire_drift_handler()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    rings = _wall_rings(wall)
    clean, codes = _clean_host(wall)
    ok = (result == {"FINISHED"} and _record_text(b) != b_record
          and _record_text(a) == a_record and rings.get(_feature(a).opening_link) == a_ring
          and _feature(b).opening_link in rings and clean)
    R.check("align_second_window_keeps_sibling", ok,
            f"result={result} error={error[:200]!r} b_committed={_record_text(b) != b_record} "
            f"a_unchanged={_record_text(a) == a_record} host={codes}")


def _move_and_commit_a(R, check, commit):
    """D: A + B on one wall -> move A -> ``commit(a)``; B must be byte-identical."""
    import bpy
    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight

    wall_name, _face, ctx, a_name, b_name, _ua, _ub = _two_windows()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    a_id = str(a.get("gml_id"))
    a_record, b_record = _record_text(a), _record_text(b)
    b_link = _feature(b).opening_link
    b_ring = _wall_rings(wall)[b_link]
    a_ring = _wall_rings(wall)[_feature(a).opening_link]
    result, error = commit(a, a_id, _in_plane_offset(ctx, 0.03))
    _fire_drift_handler()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    rings = _wall_rings(wall)
    clean, codes = _clean_host(wall)
    blocked = bool(export_preflight())
    ok = (result == {"FINISHED"} and _record_text(a) != a_record
          and rings.get(_feature(a).opening_link) not in (None, a_ring)
          and _record_text(b) == b_record and rings.get(b_link) == b_ring
          and clean and not blocked)
    R.check(check, ok,
            f"result={result} error={error[:300]!r} a_updated={_record_text(a) != a_record} "
            f"b_record_same={_record_text(b) == b_record} b_ring_same={rings.get(b_link) == b_ring} "
            f"host={codes} preflight_blocked={blocked}")


def transform_commit_beside_sibling(R):
    """D (AB-19): G on A -> cgml.commit_transform_drift(A)."""
    import bpy

    def commit(a, a_id, offset):
        a.location += offset
        bpy.context.view_layer.update()
        _fire_drift_handler()
        _select(a)
        return _run(lambda: bpy.ops.cgml.commit_transform_drift('EXEC_DEFAULT', object_gml_id=a_id))

    _move_and_commit_a(R, "transform_commit_beside_sibling", commit)


def feature_edit_commit_beside_sibling(R):
    """D (AB-19, P4.2 session path): begin(A) -> move -> cgml.feature_edit_commit."""
    import bpy

    def commit(a, a_id, offset):
        _select(a)
        begun, error = _run(lambda: bpy.ops.cgml.feature_edit_begin('EXEC_DEFAULT', object_gml_id=a_id))
        if begun != {"FINISHED"}:
            return begun, error
        a.location += offset
        bpy.context.view_layer.update()
        return _run(lambda: bpy.ops.cgml.feature_edit_commit('EXEC_DEFAULT', object_gml_id=a_id))

    _move_and_commit_a(R, "feature_edit_commit_beside_sibling", commit)


def sibling_ring_drift_is_not_absorbed(R):
    """Committing A must not silently commit B's own ring drift: B's stored
    RingRecord stays as it was and the drift stays visible (coords_stale)."""
    import bmesh
    import bpy
    from city_modeler.shared.object_helpers import face_material
    from city_modeler.shared.ring_opening_sync import OPENING_LINK_KEY

    wall_name, _face, ctx, a_name, b_name, _ua, _ub = _two_windows()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    b_link = _feature(b).opening_link
    b_ring = _wall_rings(wall)[b_link]
    # Drift B's ring face on the wall mesh (as a wall Edit-Mode move would).
    shift = wall.matrix_world.inverted().to_3x3() @ _in_plane_offset(ctx, 0.02)
    bm = bmesh.new()
    bm.from_mesh(wall.data)
    moved = set()
    for f in bm.faces:
        mat = face_material(wall.data, wall.data.polygons[f.index])
        if mat is not None and mat.get("Interior") and str(mat.get(OPENING_LINK_KEY) or "") == b_link:
            moved.update(f.verts)
    for v in moved:
        v.co += shift
    bm.to_mesh(wall.data)
    bm.free()
    wall.data.update()
    a_id = str(a.get("gml_id"))
    a.location += _in_plane_offset(ctx, 0.03)
    bpy.context.view_layer.update()
    _fire_drift_handler()
    _select(a)
    result, error = _run(lambda: bpy.ops.cgml.commit_transform_drift('EXEC_DEFAULT', object_gml_id=a_id))
    wall = _obj(wall_name)
    codes = _host_codes(wall)
    ok = (bool(moved) and result == {"FINISHED"} and _wall_rings(wall).get(b_link) == b_ring
          and any(c.startswith("coords_stale:") for c in codes))
    R.check("sibling_ring_drift_is_not_absorbed", ok,
            f"moved_verts={len(moved)} result={result} error={error[:200]!r} "
            f"b_ring_kept={_wall_rings(wall).get(b_link) == b_ring} host={codes}")


def delete_is_independent_with_undo_redo(R):
    """E: delete A -> only Ring A goes; undo/redo restore/remove only A."""
    import bpy

    wall_name, _face, _ctx, a_name, b_name, _ua, _ub = _two_windows()
    wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
    a_link, b_link = _feature(a).opening_link, _feature(b).opening_link
    a_record, b_record = _record_text(a), _record_text(b)
    rings_before = _wall_rings(wall)
    bpy.ops.ed.undo_push(message="multi-opening: before delete")
    _select(a)
    result, error = _run(lambda: bpy.ops.cgml.guarded_delete('EXEC_DEFAULT'))
    bpy.ops.ed.undo_push(message="multi-opening: after delete")

    def state():
        wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
        return {
            "a": a is not None, "b_record": _record_text(b) if b else None,
            "a_record": _record_text(a) if a else None,
            "rings": _wall_rings(wall), "mesh": _mesh_ring_links(wall),
        }

    deleted = state()
    bpy.ops.ed.undo()
    undone = state()
    bpy.ops.ed.redo()
    redone = state()

    def only_b(s):
        return (not s["a"] and s["b_record"] == b_record and set(s["rings"]) == {b_link}
                and s["rings"][b_link] == rings_before[b_link] and s["mesh"] == [b_link])

    both = (undone["a"] and undone["a_record"] == a_record and undone["b_record"] == b_record
            and undone["rings"] == rings_before and undone["mesh"] == sorted([a_link, b_link]))
    ok = result == {"FINISHED"} and only_b(deleted) and both and only_b(redone)
    R.check("delete_is_independent_with_undo_redo", ok,
            f"result={result} error={error[:200]!r} deleted_only_a={only_b(deleted)} "
            f"undo_restores_both={both} redo_only_a={only_b(redone)} "
            f"mesh after delete/undo/redo={deleted['mesh']}/{undone['mesh']}/{redone['mesh']}")


def f9_resize_of_second_window_keeps_first(R):
    """H: Draw A, Draw B, F9-resize B; A untouched, B keeps its id, one ring each."""
    import bpy

    wall_name, face, ctx, a_name, uv_a = _base()
    a = _obj(a_name)
    a_record = _record_text(a)
    a_link = _feature(a).opening_link
    a_ring = _wall_rings(_obj(wall_name))[a_link]
    uv_b = _second_uv(wall_name, face, ctx, uv_a, size=RESIZED)
    bpy.ops.ed.undo_push(message="multi-opening: before B")
    result, error, b = _draw(_obj(wall_name), face, uv_b)
    bpy.ops.ed.undo_push(message="multi-opening: after B")
    b_id = str(b.get("gml_id")) if b else ""
    bpy.ops.ed.undo()
    result, error, b = _draw(_obj(wall_name), face, uv_b, *RESIZED, feature_gml_id=b_id)
    wall, a = _obj(wall_name), _obj(a_name)
    fb = _feature(b) if b is not None else None
    rings = _wall_rings(wall)
    clean, codes = _clean_host(wall)
    ok = (result == {"FINISHED"} and fb is not None and fb.gml_id == b_id
          and abs(fb.params["width"] - RESIZED[0]) < 1e-6 and abs(fb.params["height"] - RESIZED[1]) < 1e-6
          and _record_text(a) == a_record and rings.get(a_link) == a_ring
          and set(rings) == {a_link, fb.opening_link} and _mesh_ring_links(wall) == sorted(rings) and clean)
    R.check("f9_resize_of_second_window_keeps_first", ok,
            f"result={result} error={error[:200]!r} b_id_kept={getattr(fb, 'gml_id', None) == b_id} "
            f"a_unchanged={_record_text(a) == a_record} rings={sorted(rings)} "
            f"mesh={_mesh_ring_links(wall)} host={codes}")


def undo_redo_multi_create_and_transform_commit(R):
    """Undo/redo across: Draw A, Draw B (same wall), G A + commit."""
    import bpy

    wall_name, face, ctx, a_name, uv_a = _base()
    bpy.ops.ed.undo_push(message="multi-opening: after A")
    uv_b = _second_uv(wall_name, face, ctx, uv_a)
    result_b, error_b, b = _draw(_obj(wall_name), face, uv_b)
    b_name = b.name if b else ""
    bpy.ops.ed.undo_push(message="multi-opening: after B")
    a = _obj(a_name)
    a_id = str(a.get("gml_id"))

    def snap():
        wall, a, b = _obj(wall_name), _obj(a_name), _obj(b_name)
        return (_record_text(a) if a else None, _record_text(b) if b else None,
                _record_text(wall), _mesh_ring_links(wall))

    after_b = snap()
    a.location += _in_plane_offset(ctx, 0.03)
    bpy.context.view_layer.update()
    _fire_drift_handler()
    _select(a)
    result, error = _run(lambda: bpy.ops.cgml.commit_transform_drift('EXEC_DEFAULT', object_gml_id=a_id))
    bpy.ops.ed.undo_push(message="multi-opening: after commit")
    after_commit = snap()
    trail = []
    for step in ("undo", "undo", "redo", "redo"):
        getattr(bpy.ops.ed, step)()
        trail.append(snap())
    after_a_only = trail[1]
    clean, codes = _clean_host(_obj(wall_name))
    ok = (result_b == {"FINISHED"} and result == {"FINISHED"} and after_commit != after_b
          and trail[0] == after_b and after_a_only[1] is None and len(after_a_only[3]) == 1
          and trail[2] == after_b and trail[3] == after_commit and clean)
    R.check("undo_redo_multi_create_and_transform_commit", ok,
            f"draw_b={result_b} {error_b[:120]} commit={result} {error[:120]} "
            f"undo1=after_b:{trail[0] == after_b} undo2_one_ring:{len(after_a_only[3]) == 1} "
            f"redo1=after_b:{trail[2] == after_b} redo2=commit:{trail[3] == after_commit} host={codes}")


def save_reload_keeps_multi_opening_wall(R):
    """I: a wall with three record-backed Windows (Draw, Draw, Stamp) survives save/reload."""
    import bpy
    from city_modeler.shared.authoring_core.blender import registry
    from city_modeler.shared.authoring_core.blender import transform_drift as td

    wall_name, face, ctx, a_name, b_name, uv_a, uv_b = _two_windows()
    uv_c = _second_uv(wall_name, face, ctx, uv_a, uv_b)
    result, error, c = _stamp(_obj(a_name), _obj(wall_name), face, uv_c)
    if result != {"FINISHED"} or c is None:
        R.check("save_reload_keeps_multi_opening_wall", False, f"precondition: Stamp C -> {result} {error}")
        return
    names = (a_name, b_name, c.name)
    before = {n: (_record_text(_obj(n)), str(_obj(n).get("gml_id")), _obj(n).parent.name) for n in names}
    wall_before = (_record_text(_obj(wall_name)), _mesh_ring_links(_obj(wall_name)))
    path = h.out_dir() / "p4_multi_opening_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    _flag()
    registry.REGISTRY.rebuild()
    _fire_drift_handler()
    wall = _obj(wall_name)
    after = {n: (_record_text(_obj(n)), str(_obj(n).get("gml_id")), _obj(n).parent.name) for n in names}
    resolved = all(registry.REGISTRY.get(before[n][1]) is _obj(n) for n in names)
    dirty = [before[n][1] for n in names if td.is_dirty(before[n][1])]
    clean, codes = _clean_host(wall)
    ok = (after == before and (_record_text(wall), _mesh_ring_links(wall)) == wall_before
          and len(wall_before[1]) == 3 and resolved and not dirty and clean)
    R.check("save_reload_keeps_multi_opening_wall", ok,
            f"records_same={after == before} wall_same={(_record_text(wall), _mesh_ring_links(wall)) == wall_before} "
            f"rings={len(wall_before[1])} resolved={resolved} dirty={dirty} host={codes}")


def _export_openings(path):
    s = h.export_structure(path)
    ids = [g for g, _host in s["openings"]]
    exteriors = {p["exterior"] for p in s["polygons"].values()}
    corrupt = [k for k, p in s["polygons"].items() if any(r in exteriors for r in p["interiors"])]
    return s, ids, corrupt


def export_v2_v3_two_windows_on_one_wall(R):
    """J: both Windows on one wall export in CityGML 2 and 3: two more openings,
    both hosted by that wall, two more interior rings on its polygons, no hole
    equal to an outline, distinct ids, xmllint clean. Counts, not literal ids
    (AB-18: the legacy writers' exported opening id text is a separate channel)."""
    _flag()
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, _face = h.first_wall_with_exterior_face()
    wall_id = str(wall.get("gml_id"))
    out = h.out_dir()

    def measure(path):
        s, opening_ids, corrupt = _export_openings(path)
        hosted = sum(1 for _g, host in s["openings"] if host == wall_id)
        rings = sum(len(p["interiors"]) for p in s["polygons"].values() if p["host"] == wall_id)
        return opening_ids, hosted, rings, corrupt

    base = {}
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p4_multi_opening_base_v{major}.gml"
        h.export_scene(path, version)
        base[major] = measure(path)
    _two_windows()
    details, ok = [], True
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p4_multi_opening_two_windows_v{major}.gml"
        h.export_scene(path, version)
        opening_ids, hosted, rings, corrupt = measure(path)
        base_ids, base_hosted, base_rings, _c = base[major]
        lint = h.xmllint(path, version)
        this = (len(opening_ids) == len(base_ids) + 2 and len(set(opening_ids)) == len(opening_ids)
                and hosted == base_hosted + 2 and rings == base_rings + 2
                and not corrupt and lint["exit"] == 0)
        ok = ok and this
        details.append(f"v{major}: openings {len(base_ids)}->{len(opening_ids)} hosted {base_hosted}->{hosted} "
                       f"wall rings {base_rings}->{rings} distinct={len(set(opening_ids)) == len(opening_ids)} "
                       f"corrupt={corrupt[:3]} xmllint={lint}")
    R.check("export_v2_v3_two_windows_on_one_wall", ok, "; ".join(details))


def sibling_elsewhere_in_building_does_not_block(R):
    """A record-backed Window on one wall must not block Draw/Stamp on another
    wall of the same Building (the retired P3.0 Building-wide refusal)."""
    import bpy
    from city_modeler.shared.lod_helpers import lod_promotion_root
    from city_modeler.shared.object_helpers import face_material
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    wall_name, _face, _ctx, a_name, _uv_a = _base()
    root = lod_promotion_root(_obj(wall_name))
    other = None
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        if (obj.name == wall_name or obj.type != "MESH" or str(obj.get("cgml3_feature") or "") != "WallSurface"
                or lod_promotion_root(obj) is not root or any(True for _ in wall_interior_ring_faces(obj))):
            continue
        faces = [p for p in obj.data.polygons
                 if (m := face_material(obj.data, p)) is not None and not m.get("Interior") and m.get("con_surface_id")]
        for poly in sorted(faces, key=lambda p: -p.area):
            ctx = h.surface_context(obj, poly.index)
            try:
                uv = h.solid_area_position_uv(obj, poly.index, ctx, SIZE / 2 + 0.05, SIZE / 2 + 0.05)
            except Exception:
                continue
            other = (obj.name, poly.index, uv)
            break
        if other:
            break
    if other is None:
        R.check("sibling_elsewhere_in_building_does_not_block", False, "no second clean wall in the Building")
        return
    a_record = _record_text(_obj(a_name))
    result_d, error_d, d = _draw(_obj(other[0]), other[1], other[2])
    ctx = h.surface_context(_obj(other[0]), other[1])
    uv_s = h.solid_area_position_uv(_obj(other[0]), other[1], ctx, SIZE / 2 + 0.05, SIZE / 2 + 0.05,
                                    avoid=(_rect(other[2], ctx, 0.5, 0.6),))
    result_s, error_s, s = _stamp(_obj(a_name), _obj(other[0]), other[1], uv_s)
    ok = (result_d == {"FINISHED"} and d is not None and _feature(d) is not None
          and result_s == {"FINISHED"} and s is not None and _feature(s) is not None
          and _record_text(_obj(a_name)) == a_record)
    R.check("sibling_elsewhere_in_building_does_not_block", ok,
            f"other_wall={other[0]} draw={result_d} {error_d[:160]} stamp={result_s} {error_s[:160]}")


CHECKS = [
    same_wall_draw_creates_independent_windows,
    same_wall_stamp_after_f9_resize,
    align_second_window_keeps_sibling,
    transform_commit_beside_sibling,
    feature_edit_commit_beside_sibling,
    sibling_ring_drift_is_not_absorbed,
    delete_is_independent_with_undo_redo,
    f9_resize_of_second_window_keeps_first,
    undo_redo_multi_create_and_transform_commit,
    save_reload_keeps_multi_opening_wall,
    export_v2_v3_two_windows_on_one_wall,
    sibling_elsewhere_in_building_does_not_block,
    draw_on_imported_opening_polygon_refused_atomically,
    draw_beside_unrecorded_linked_opening_refused_atomically,
    rollback_after_placement_before_records,
    rollback_after_window_record_written,
    rollback_on_record_backed_wall,
]


def main():
    R = h.Results(__file__)
    for fn in CHECKS:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, traceback.format_exc())
    os.environ.pop("CGML_AUTHORING_CORE", None)
    R.finish()


main()
