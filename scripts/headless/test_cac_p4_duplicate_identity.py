"""CAC-P4.7 native Shift+D policy (AB-15 / D-4).

Native Blender duplication is intentionally characterised before repair: it
copies the object id and the complete per-object record.  Export pre-flight
therefore blocks the temporary collision.  ``cgml.initialize_duplicate`` is
the explicit, undoable repair: it rebuilds the copy through ``create_feature``
and leaves two independently paired Windows.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _flag():
    os.environ["CGML_AUTHORING_CORE"] = "1"


def _draw_window():
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    context = h.surface_context(wall, face_index)
    position = h.solid_area_position_uv(wall, face_index, context, 0.15, 0.15)
    fill = create_feature(
        "Window", context,
        FacadeElementDefinition(semantic_type="Window", width=0.3, height=0.3, depth=0.05),
        PlacementRequest(position_uv=position, source="manual_draw"),
    )
    return wall, fill, context, position


def _duplicate(fill):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    fill.select_set(True)
    bpy.context.view_layer.objects.active = fill
    result = bpy.ops.object.duplicate()
    if result != {"FINISHED"}:
        raise AssertionError(f"native duplicate failed: {result}")
    return bpy.context.active_object


def _ring_links(wall):
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(wall)
    return {
        ring.opening_link
        for polygon in record.polygons
        for ring in polygon.interiors
        if ring.opening_link
    }


def native_duplicate_copies_identity_and_is_blocked(R):
    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight, run_validation
    from city_modeler.shared.authoring_core.blender import storage

    _flag()
    _wall, original, _context, _position = _draw_window()
    duplicate = _duplicate(original)
    original_record = storage.read_record(original)
    duplicate_record = storage.read_record(duplicate)
    same_record = duplicate_record is not None and duplicate_record.to_dict() == original_record.to_dict()
    blocked = export_preflight()
    duplicate_finding = any(f.code in {"duplicate_gml_id", "duplicate_record"} for f in run_validation())
    R.check(
        "native_duplicate_copies_identity_and_is_blocked",
        str(original.get("gml_id")) == str(duplicate.get("gml_id")) and same_record and blocked and duplicate_finding,
        f"original={original.get('gml_id')} duplicate={duplicate.get('gml_id')} same_record={same_record} blocked={blocked}",
    )


def initialized_duplicate_has_independent_identity_and_ring(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.blender.consistency_view import export_preflight, run_validation

    _flag()
    wall, original, context, position = _draw_window()
    original_id = str(original.get("gml_id"))
    original_link = storage.read_record(original).feature(original_id).opening_link
    duplicate = _duplicate(original)
    # Shift+D then place the copy at a separate, valid solid-area location.
    # The duplicate's pre-repair identity remains the same regardless of move.
    from mathutils import Vector
    target_uv = h.solid_area_position_uv(
        wall, context.face_indices[0], context, 0.15, 0.15,
        avoid=((position[0] * context.width, position[1] * context.height, 0.15, 0.15),),
    )
    from city_modeler.shared.facade_placement import surface_world_point_from_uv
    target = surface_world_point_from_uv(context, target_uv)
    centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
    duplicate.location += target - centre
    try:
        result = bpy.ops.cgml.initialize_duplicate()
    except RuntimeError:
        result = {"CANCELLED"}
    replacement = bpy.context.active_object
    replacement_id = str(replacement.get("gml_id"))
    replacement_record = storage.read_record(replacement)
    replacement_link = replacement_record.feature(replacement_id).opening_link
    links = _ring_links(wall)
    registry.REGISTRY.rebuild()
    findings = run_validation()
    clean = not any(f.severity == "error" for f in findings) and not export_preflight()
    ok = (
        result == {"FINISHED"}
        and replacement is not duplicate
        and replacement_id != original_id
        and registry.REGISTRY.get(original_id) is original
        and registry.REGISTRY.get(replacement_id) is replacement
        and replacement_link != original_link
        and {original_link, replacement_link}.issubset(links)
        and replacement_record.feature(replacement_id).params == storage.read_record(original).feature(original_id).params
        and clean
    )
    R.check("initialized_duplicate_has_independent_identity_and_ring", ok,
            f"result={result} ids=({original_id}, {replacement_id}) links={sorted(links)} clean={clean} findings={findings}")


def invalid_duplicate_refuses_without_harming_original(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag()
    wall, original, _context, _position = _draw_window()
    original_id = str(original.get("gml_id"))
    original_links = _ring_links(wall)
    duplicate = _duplicate(original)
    duplicate.location.x += 10000.0
    try:
        result = bpy.ops.cgml.initialize_duplicate()
    except RuntimeError:
        result = {"CANCELLED"}
    ok = (
        result == {"CANCELLED"}
        and original.name in bpy.data.objects
        and duplicate.name in bpy.data.objects
        and str(original.get("gml_id")) == original_id
        and _ring_links(wall) == original_links
        and storage.read_record(original).feature(original_id) is not None
    )
    R.check("invalid_duplicate_refuses_without_harming_original", ok,
            f"result={result} original={original_id} links={sorted(_ring_links(wall))}")


def delete_one_resolved_window_keeps_the_other_pair(R):
    """Each direction gets a fresh scene: delete must cascade only its own ring."""
    import bpy
    from mathutils import Vector
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.usecases import delete_feature
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    def resolved_pair():
        wall, original, context, pos = _draw_window()
        original_id = str(original.get("gml_id"))
        duplicate = _duplicate(original)
        uv = h.solid_area_position_uv(wall, context.face_indices[0], context, .15, .15,
                                     avoid=((pos[0] * context.width, pos[1] * context.height, .15, .15),))
        target = surface_world_point_from_uv(context, uv)
        centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
        duplicate.location += target - centre
        if bpy.ops.cgml.initialize_duplicate() != {"FINISHED"}:
            raise AssertionError("initialization failed")
        replacement = bpy.context.active_object
        replacement_id = str(replacement.get("gml_id"))
        return wall, original, original_id, replacement, replacement_id

    _flag()
    wall, original, original_id, replacement, replacement_id = resolved_pair()
    original_record = storage.read_record(original).to_dict()
    original_link = storage.read_record(original).feature(original_id).opening_link
    delete_feature(replacement_id)
    first = (original.name in bpy.data.objects and storage.read_record(original).to_dict() == original_record
             and original_link in _ring_links(wall))

    _flag()
    wall, original, original_id, replacement, replacement_id = resolved_pair()
    replacement_record = storage.read_record(replacement).to_dict()
    replacement_link = storage.read_record(replacement).feature(replacement_id).opening_link
    delete_feature(original_id)
    second = (replacement.name in bpy.data.objects and storage.read_record(replacement).to_dict() == replacement_record
              and replacement_link in _ring_links(wall))
    R.check("delete_one_resolved_window_keeps_the_other_pair", first and second,
            f"delete_duplicate={first} delete_original={second}")


def initialize_duplicate_undo_redo_sequence(R):
    import bpy
    from mathutils import Vector
    from city_modeler.shared.authoring_core.blender import registry, storage
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    _flag()
    wall, original, context, pos = _draw_window()
    original_id = str(original.get("gml_id"))
    # Background Blender has only the checkpoints a script pushes (P0.0
    # probe, P3.6 lifecycle QA, P4.2 tests): each operator is bracketed, and
    # the walk-back asserts states rather than a fixed ``undo()`` count.
    bpy.ops.ed.undo_push(message="cac p47 before duplicate")
    duplicate = _duplicate(original)
    uv = h.solid_area_position_uv(wall, context.face_indices[0], context, .15, .15,
                                 avoid=((pos[0] * context.width, pos[1] * context.height, .15, .15),))
    target = surface_world_point_from_uv(context, uv)
    centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
    duplicate.location += target - centre
    bpy.ops.ed.undo_push(message="cac p47 before initialize")
    if bpy.ops.cgml.initialize_duplicate() != {"FINISHED"}:
        raise AssertionError("initialization failed")
    duplicate_id = str(bpy.context.active_object.get("gml_id"))
    bpy.ops.ed.undo_push(message="cac p47 after initialize")

    def state():
        registry.REGISTRY.rebuild()
        def is_window(o):
            record = storage.read_record(o) if storage.has_record(o) else None
            feature = record.feature(str(o.get("gml_id") or "")) if record is not None else None
            return feature is not None and feature.kind == "Window"

        wins = [o for o in bpy.data.objects if is_window(o)]
        ids = sorted(str(o.get("gml_id")) for o in wins)
        findings = run_validation()
        errors = [f.code for f in findings if f.severity == "error"]
        duplicate = sorted({f.code for f in findings if f.code in {"duplicate_gml_id", "duplicate_record"}})
        return {"count": len(wins), "ids": ids, "errors": errors, "duplicate": duplicate,
                "resolved": {g: registry.REGISTRY.get(g) is not None for g in ids}}

    def is_resolved(s):
        return (s["count"] == 2 and s["ids"] == sorted((original_id, duplicate_id))
                and not s["errors"] and not s["duplicate"])

    def is_collision(s):
        return s["count"] == 2 and s["ids"] == [original_id, original_id] and bool(s["duplicate"])

    def is_original_only(s):
        return s["count"] == 1 and s["ids"] == [original_id] and not s["errors"] and not s["duplicate"]

    def walk(step, target, max_steps=6):
        trail = []
        for _ in range(max_steps):
            op = bpy.ops.ed.undo if step == "undo" else bpy.ops.ed.redo
            if not op.poll():
                trail.append(f"{step}.poll=False")
                return False, trail
            op()
            s = state()
            trail.append(s)
            if target(s):
                return True, trail
        return False, trail

    after_init = state()
    to_collision, t1 = walk("undo", is_collision)
    to_original, t2 = walk("undo", is_original_only)
    back_to_collision, t3 = walk("redo", is_collision)
    back_to_resolved, t4 = walk("redo", is_resolved)
    ok = (is_resolved(after_init) and duplicate_id != original_id
          and to_collision and to_original and back_to_collision and back_to_resolved)
    R.check("initialize_duplicate_undo_redo_sequence", ok,
            f"after={after_init} undo->collision={to_collision} {t1} undo->original={to_original} {t2} "
            f"redo->collision={back_to_collision} {t3} redo->resolved={back_to_resolved} {t4}")


def _ab15_evidence(base, after):
    """Both Windows present and distinct; the host wall gains exactly one independent ring."""
    problems = []
    base_ids = [g for g, _host in base["openings"]]
    ids = [g for g, _host in after["openings"]]
    if len(ids) != len(base_ids) + 1:
        problems.append(f"openings {len(base_ids)} -> {len(ids)} (expected +1)")
    if len(set(ids)) != len(ids):
        problems.append(f"repeated opening gml:id {sorted(i for i in ids if ids.count(i) > 1)}")
    new_hosts = {host for _g, host in after["openings"]} - {None}
    base_hosts = {host for _g, host in base["openings"]} - {None}
    added_hosts = [host for g, host in after["openings"] if g not in base_ids]
    if not added_hosts or added_hosts[0] not in base_hosts:
        problems.append(f"new opening host {added_hosts} is not an existing host {sorted(base_hosts)}")
    grown = []
    for key, poly in base["polygons"].items():
        now = after["polygons"].get(key)
        if now is None:
            problems.append(f"polygon {key} missing after initialization")
        elif now["exterior"] != poly["exterior"]:
            problems.append(f"polygon {key} exterior changed")
        elif now["interiors"] != poly["interiors"]:
            if len(now["interiors"]) == len(poly["interiors"]) + 1 and all(r in now["interiors"] for r in poly["interiors"]):
                grown.append(key)
            else:
                problems.append(f"polygon {key} interiors {len(poly['interiors'])} -> {len(now['interiors'])} not a pure +1")
    if len(grown) != 1:
        problems.append(f"expected exactly one host polygon to gain one ring, got {grown}")
    exteriors = {p["exterior"] for p in after["polygons"].values()}
    for key in grown:
        rings = after["polygons"][key]["interiors"]
        if len(set(rings)) != len(rings):
            problems.append(f"{key}: two interior rings share coordinates")
        if any(r in exteriors for r in rings):
            problems.append(f"{key}: an interior ring equals a surface outline (AB-15 v2 corruption)")
        if after["polygons"][key]["host"] not in new_hosts:
            problems.append(f"{key}: grown polygon is not on an opening host")
    return problems, {"openings": len(ids), "hosts": sorted(new_hosts), "grown": grown}


def initialized_duplicate_exports_both_windows_v2_v3(R):
    """AB-15 evidence for the D-4 workflow: unresolved copy refused, resolved copy exported."""
    import bpy
    from mathutils import Vector
    from city_modeler.shared.facade_placement import surface_world_point_from_uv

    out = h.out_dir()
    _flag()
    wall, original, context, pos = _draw_window()
    original_id = str(original.get("gml_id"))
    base = {}
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p47_ab15_single_v{major}.gml"
        h.export_scene(path, version)
        base[major] = h.export_structure(path)

    duplicate = _duplicate(original)
    unresolved = {}
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p47_ab15_unresolved_v{major}.gml"
        try:
            h.export_scene(path, version)
            unresolved[major] = "exported"
        except (AssertionError, RuntimeError) as exc:
            unresolved[major] = f"refused: {str(exc).strip().splitlines()[0][:120]}"
    uv = h.solid_area_position_uv(wall, context.face_indices[0], context, .15, .15,
                                 avoid=((pos[0] * context.width, pos[1] * context.height, .15, .15),))
    target = surface_world_point_from_uv(context, uv)
    centre = sum((duplicate.matrix_world @ v.co for v in duplicate.data.vertices), Vector()) / len(duplicate.data.vertices)
    duplicate.location += target - centre
    bpy.context.view_layer.objects.active = duplicate
    if bpy.ops.cgml.initialize_duplicate() != {"FINISHED"}:
        raise AssertionError("initialization failed")
    duplicate_id = str(bpy.context.active_object.get("gml_id"))

    ok = duplicate_id != original_id and all(v.startswith("refused") for v in unresolved.values())
    details = [f"ids=({original_id}, {duplicate_id}) unresolved={unresolved}"]
    for major, version in (("2", "2.0"), ("3", "3.0")):
        path = out / f"p47_ab15_resolved_v{major}.gml"
        h.export_scene(path, version)
        problems, summary = _ab15_evidence(base[major], h.export_structure(path))
        lint = h.xmllint(path, version)
        ok = ok and not problems and lint["exit"] == 0
        details.append(f"v{major}: {summary} problems={problems} xmllint={lint}")
    R.check("initialized_duplicate_exports_both_windows_v2_v3", ok, " | ".join(details))


def main():
    R = h.Results(__file__)
    for check in (
        native_duplicate_copies_identity_and_is_blocked,
        initialized_duplicate_has_independent_identity_and_ring,
        invalid_duplicate_refuses_without_harming_original,
        delete_one_resolved_window_keeps_the_other_pair,
        initialize_duplicate_undo_redo_sequence,
        initialized_duplicate_exports_both_windows_v2_v3,
    ):
        try:
            check(R)
        except Exception:
            R.check(check.__name__, False, traceback.format_exc())
    R.finish()


main()
