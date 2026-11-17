"""Semantic Stamp record-backed create lifecycle (CAC-P3.2a, PLAN.md P3.2a).

``SEMANTIC_OT_stamp`` (``ops/facade_authoring.py``) routes a flag-on Window
stamp through ``create_feature`` (``_stamp_place_semantic_element``), the
same as Draw (P3.2): the stamped copy gets a **fresh** record of its own -
own gml_id, the *target* wall as host, ``provenance="authored"``,
``generator="semantic_stamp"`` - and the target wall gets its RingRecord.
The source's record is never copied (AB-14, removed by this task) and is
never mutated by stamping from it. The P3.0 guard no longer refuses a
record-backed *source* (it only reads dimensions, never touches records);
the target wall guard is unchanged (see test_cac_p3_guards.py).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_stamp_create_feature.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
# A second, unrelated Building (own gml_ids, own hierarchy) so the target
# wall sits outside the source Window's P3.0 LoD-promotion-root closure -
# that closure (a record-backed sibling anywhere in the same Building
# refuses) is a separate, unchanged P3.0 concern, not what P3.2a fixes.
ALT_BUILDING_V3 = h.FIXTURES / "Building" / "AltOpeningSpelling_LoD3_CityGML3.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _import_fixture_without_reset(path, version, use_semantic_objects):
    """Like ``h.import_fixture`` but does not call ``load_addon`` first, so a
    prior import in the same scene is kept - used to bring in a second,
    unrelated Building alongside the source Window's."""
    import bpy

    from cac_harness import detect_citygml_version
    from pathlib import Path

    detected = detect_citygml_version(Path(path).read_text(encoding="utf-8", errors="replace"))
    if detected != version:
        raise AssertionError(f"{path}: expected CityGML {version}, file is {detected}")
    bpy.context.scene.cgml3.use_semantic_objects = bool(use_semantic_objects)
    res = bpy.ops.cgml3.import_gml_file(filepath=str(path))
    if res != {"FINISHED"}:
        raise AssertionError(f"import failed for {path}: {res}")


def _ancestors(obj):
    chain = []
    seen = set()
    cur = obj.parent
    while cur is not None and cur.name not in seen:
        seen.add(cur.name)
        chain.append(cur)
        cur = cur.parent
    return chain


def _walls_with_exterior_face(objects):
    """Every (WallSurface, exterior face index) pair among ``objects``, name order (deterministic)."""
    pairs = []
    for obj in sorted(objects, key=lambda o: o.name):
        if str(obj.get("cgml3_feature") or "") != "WallSurface" or obj.type != "MESH":
            continue
        for face in obj.data.polygons:
            mats = obj.data.materials
            mat = mats[face.material_index] if 0 <= face.material_index < len(mats) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id"):
                pairs.append((obj, face.index))
                break
    return pairs


def _prepare_source_and_target():
    """Import the Railway Building, draw a record-backed source Window on its
    wall (flag on, through create_feature), then import a second, unrelated
    Building (own gml_ids/hierarchy) alongside it and resolve its wall as the
    Stamp target - still record-free, and outside the source Window's P3.0
    LoD-promotion-root closure. Returns (source, wall_a, wall_b, face_b, ctx_b)."""
    import bpy

    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    pairs = _walls_with_exterior_face(bpy.data.objects)
    if not pairs:
        raise AssertionError("Railway fixture has no WallSurface with an exterior face")
    wall_a, face_a = pairs[0]

    ctx_a = h.surface_context(wall_a, face_a)
    uv_a = h.solid_area_position_uv(wall_a, face_a, ctx_a, SIZE / 2, SIZE / 2)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv_a, source="manual_draw")
    source = create_feature("Window", ctx_a, definition, placement)

    objects_before_alt = set(bpy.data.objects)
    _import_fixture_without_reset(ALT_BUILDING_V3, "3.0", use_semantic_objects=True)
    alt_objects = set(bpy.data.objects) - objects_before_alt
    pairs_b = _walls_with_exterior_face(alt_objects)
    if not pairs_b:
        raise AssertionError("second fixture has no WallSurface with an exterior face")
    wall_b, face_b = pairs_b[0]

    ctx_b = h.surface_context(wall_b, face_b)
    return source, wall_a, wall_b, face_b, ctx_b


def _stamp_operator(source, wall, face_index, ctx, half=SIZE / 2, avoid=()):
    import bmesh
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    uv = h.solid_area_position_uv(wall, face_index, ctx, half, half, avoid=avoid)

    # SEMANTIC_OT_stamp.poll() requires an active MESH object in Edit Mode
    # with the target face selected - matches Draw's headless contract
    # (test_cac_p3_create_feature.py::_draw_operator).
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(wall.data)

    before = {o.name for o in bpy.data.objects}
    try:
        result = bpy.ops.cgml.semantic_stamp(
            'EXEC_DEFAULT', source_object_name=source.name, source_gml_id=str(source.get("gml_id") or ""),
            object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
            face_index=face_index, position_uv=uv,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the stamped object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    stamped = bpy.data.objects[new_names[0]] if new_names else None
    return result, error, stamped, uv


def flag_on_stamp_creates_fresh_record(R):
    """Flag on: stamping from a record-backed source creates a fresh record
    for the copy on the target wall; the source's own record is untouched."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    source, _wall_a, wall_b, face_b, ctx_b = _prepare_source_and_target()
    source_gml_id = str(source.get("gml_id") or "")
    result, error, stamped, _uv = _stamp_operator(source, wall_b, face_b, ctx_b)

    ok = result == {"FINISHED"} and stamped is not None and storage.has_record(stamped) and storage.has_record(wall_b)
    detail = f"result={result} error={error!r} stamped={getattr(stamped, 'name', None)}"
    if ok:
        stamped_gml_id = str(stamped.get("gml_id") or "")
        record = storage.read_record(stamped)
        feature = record.feature(stamped_gml_id)
        wall_b_gml_id = str(wall_b.get("gml_id") or "")
        ok = (
            feature is not None and stamped_gml_id != source_gml_id
            and feature.kind == "Window" and feature.provenance == "authored"
            and feature.generator == "semantic_stamp"
            and feature.host_id == wall_b_gml_id and feature.parent_id == wall_b_gml_id
            and feature.opening_link and len(feature.polygon_ids) >= 1
            and abs(feature.params["width"] - SIZE) < 1e-5
            and abs(feature.params["height"] - SIZE) < 1e-5
        )
        wall_b_record = storage.read_record(wall_b)
        ring = next((r for p in wall_b_record.polygons_of(wall_b_gml_id) for r in p.interiors
                     if r.opening_link == feature.opening_link), None)
        window_polys = record.polygons_of(stamped_gml_id)
        committed_window = any(p.geometry_state == "committed" and len(p.exterior.coords) >= 3 for p in window_polys)
        ok = ok and ring is not None and len(ring.coords) >= 3 and committed_window

        source_record = storage.read_record(source)
        source_feature = source_record.feature(source_gml_id) if source_record else None
        source_unchanged = (
            source_feature is not None and source_feature.gml_id == source_gml_id
            and source_feature.generator == "rectangular_facade_window"
        )
        ok = ok and source_unchanged
        detail += f" feature={feature} ring={ring} committed_window={committed_window} source_unchanged={source_unchanged}"
    R.check("flag_on_stamp_creates_fresh_record", ok, detail)


def dual_write_parity(R):
    """Record and legacy-derived state agree for the stamped Window."""
    from city_modeler.shared.authoring_core.blender import legacy_views, storage
    from city_modeler.shared.authoring_core.domain.legacy_reader import feature_record_from_legacy

    _flag(True)
    source, _wall_a, wall_b, face_b, ctx_b = _prepare_source_and_target()
    result, error, stamped, _uv = _stamp_operator(source, wall_b, face_b, ctx_b)
    ok = result == {"FINISHED"} and stamped is not None
    detail = f"result={result} error={error!r}"
    if ok:
        record = storage.read_record(stamped)
        feature = record.feature(str(stamped.get("gml_id")))
        legacy_feature = feature_record_from_legacy(
            legacy_views.object_view(stamped),
            [legacy_views.object_view(wall_b)] + [legacy_views.object_view(a) for a in _ancestors(wall_b)],
        )
        ok = (legacy_feature is not None and legacy_feature.gml_id == feature.gml_id
              and legacy_feature.host_id == feature.host_id
              and legacy_feature.parent_id == feature.parent_id
              and legacy_feature.opening_link == feature.opening_link)
        detail += f" record={feature} legacy={legacy_feature}"
    R.check("dual_write_parity", ok, detail)


def export_xmllint_clean_both_versions(R):
    _flag(True)
    source, _wall_a, wall_b, face_b, ctx_b = _prepare_source_and_target()
    result, error, stamped, _uv = _stamp_operator(source, wall_b, face_b, ctx_b)
    ok = result == {"FINISHED"} and stamped is not None
    detail = {}
    if ok:
        out = h.out_dir()
        gml_id = str(stamped.get("gml_id"))
        for version in ("2.0", "3.0"):
            path = out / f"p3_stamp_create_feature_v{version.replace('.', '')}.gml"
            text = h.export_scene(path, version)
            lint = h.xmllint(path, version)
            detail[version] = lint
            ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id in text
    R.check("export_xmllint_clean_both_versions", ok, json.dumps(detail))


def undo_redo_restores_stamped_object_and_records(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    source, _wall_a, wall_b, face_b, ctx_b = _prepare_source_and_target()
    wall_b_name = wall_b.name
    wall_b_record_before = storage.has_record(wall_b)
    face_count_before = len(wall_b.data.polygons)

    bpy.ops.ed.undo_push(message="cac: before stamp")
    result, error, stamped, _uv = _stamp_operator(source, wall_b, face_b, ctx_b)
    bpy.ops.ed.undo_push(message="cac: after stamp")
    if result != {"FINISHED"} or stamped is None:
        R.check("undo_redo_restores_stamped_object_and_records", False, f"setup failed: result={result} error={error!r}")
        return
    stamped_name = stamped.name

    bpy.ops.ed.undo()
    wall_b_after_undo = bpy.data.objects[wall_b_name]
    undo_object_gone = stamped_name not in bpy.data.objects
    undo_wall_record_restored = storage.has_record(wall_b_after_undo) == wall_b_record_before
    undo_face_count_restored = len(wall_b_after_undo.data.polygons) == face_count_before

    bpy.ops.ed.redo()
    redo_object_back = stamped_name in bpy.data.objects
    redo_stamped_has_record = redo_object_back and storage.has_record(bpy.data.objects[stamped_name])
    redo_wall_has_record = storage.has_record(bpy.data.objects[wall_b_name])

    ok = (undo_object_gone and undo_wall_record_restored and undo_face_count_restored
          and redo_object_back and redo_stamped_has_record and redo_wall_has_record)
    R.check("undo_redo_restores_stamped_object_and_records", ok,
            f"undo: object_gone={undo_object_gone} wall_record_restored={undo_wall_record_restored} "
            f"face_count_restored={undo_face_count_restored}; redo: object_back={redo_object_back} "
            f"stamped_has_record={redo_stamped_has_record} wall_has_record={redo_wall_has_record}")


def main():
    R = h.Results(__file__)
    checks = [
        flag_on_stamp_creates_fresh_record,
        dual_write_parity,
        export_xmllint_clean_both_versions,
        undo_redo_restores_stamped_object_and_records,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
