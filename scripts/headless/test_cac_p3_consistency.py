"""Record <-> legacy consistency validator (CAC-P3.3, PLAN.md P3.3).

``shared/authoring_core/domain/consistency.py`` (pure) compares a stored
record against a fresh read of live legacy state and live world-space
geometry; ``shared/authoring_core/blender/consistency_view.py`` builds those
facts from ``bpy.data.objects`` and wires ``export_preflight`` into
``cgml3.export_gml_file`` (flag on only - flag off is unchanged, C6).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_consistency.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
ALT_BUILDING_V3 = h.FIXTURES / "Building" / "AltOpeningSpelling_LoD3_CityGML3.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare_window():
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    fill = create_feature("Window", ctx, definition, placement)
    return wall, fill


def _walls_with_exterior_face(objects):
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


def _import_fixture_without_reset(path, version, use_semantic_objects):
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


def _prepare_stamped_window():
    """A source Window drawn on the Railway wall, stamped onto an unrelated
    Building's wall (mirrors test_cac_p3_stamp_create_feature.py)."""
    import bmesh
    import bpy

    wall_a, source = _prepare_window()

    objects_before_alt = set(bpy.data.objects)
    _import_fixture_without_reset(ALT_BUILDING_V3, "3.0", use_semantic_objects=True)
    alt_objects = set(bpy.data.objects) - objects_before_alt
    pairs_b = _walls_with_exterior_face(alt_objects)
    if not pairs_b:
        raise AssertionError("second fixture has no WallSurface with an exterior face")
    wall_b, face_b = pairs_b[0]
    ctx_b = h.surface_context(wall_b, face_b)
    uv = h.solid_area_position_uv(wall_b, face_b, ctx_b, SIZE / 2, SIZE / 2)

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall_b.select_set(True)
    bpy.context.view_layer.objects.active = wall_b
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(wall_b.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_b].select_set(True)
    bmesh.update_edit_mesh(wall_b.data)

    before = {o.name for o in bpy.data.objects}
    result = bpy.ops.cgml.semantic_stamp(
        'EXEC_DEFAULT', source_object_name=source.name, source_gml_id=str(source.get("gml_id") or ""),
        object_name=wall_b.name, object_gml_id=str(wall_b.get("gml_id") or ""),
        face_index=face_b, position_uv=uv,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"stamp failed: {result}")
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the stamped object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    stamped = bpy.data.objects[new_names[0]]
    return wall_a, source, wall_b, stamped


def _run(objects=None):
    from city_modeler.shared.authoring_core.blender.consistency_view import run_validation

    return run_validation(objects)


def _blocking_codes(findings):
    return sorted({f.code for f in findings if f.severity == "error"})


# -- 1, 2: clean creation lifecycles ------------------------------------------------

def fresh_draw_window_is_clean(R):
    _flag(True)
    _wall, fill = _prepare_window()
    findings = _run()
    ok = fill is not None and not findings
    R.check("fresh_draw_window_is_clean", ok, f"fill={getattr(fill, 'name', None)} findings={findings}")


def fresh_stamp_window_is_clean(R):
    _flag(True)
    _wall_a, _source, _wall_b, stamped = _prepare_stamped_window()
    findings = _run()
    ok = stamped is not None and not findings
    R.check("fresh_stamp_window_is_clean", ok, f"stamped={getattr(stamped, 'name', None)} findings={findings}")


# -- 3, 4: geometry drift (coords_stale, never blocking) ----------------------------

def g_move_reports_coords_stale(R):
    import bpy

    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    _wall, fill = _prepare_window()
    before = _run()
    fill.location.x += 0.5
    bpy.context.view_layer.update()
    after = _run()
    ok = (not before and any(f.code == c.COORDS_STALE and f.gml_id == str(fill.get("gml_id")) for f in after)
          and not c.is_blocking(after))
    R.check("g_move_reports_coords_stale", ok, f"before={before} after={after}")


def parent_building_transform_reports_coords_stale(R):
    import bpy

    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    wall, fill = _prepare_window()
    building = wall
    while building.parent is not None:
        building = building.parent
    before = _run()
    building.location.x += 1.0
    bpy.context.view_layer.update()
    after = _run()
    ok = (not before and any(f.code == c.COORDS_STALE for f in after) and not c.is_blocking(after))
    R.check("parent_building_transform_reports_coords_stale", ok,
            f"building={building.name} before={before} after={after}")


# -- 6, 7: identity ------------------------------------------------------------------

def object_gml_id_mismatch_is_foreign_record_finding(R):
    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    _wall, fill = _prepare_window()
    fill["gml_id"] = "ID_manually_retagged"
    findings = _run()
    ok = any(f.code == c.FOREIGN_RECORD for f in findings) and c.is_blocking(findings)
    R.check("object_gml_id_mismatch_is_foreign_record_finding", ok, f"findings={findings}")


def duplicate_record_after_shift_d_is_reported(R):
    import bpy

    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    _wall, fill = _prepare_window()
    bpy.ops.object.select_all(action="DESELECT")
    fill.select_set(True)
    bpy.context.view_layer.objects.active = fill
    bpy.ops.object.duplicate()
    findings = _run()
    ok = any(f.code in (c.DUPLICATE_RECORD, c.DUPLICATE_GML_ID) for f in findings) and c.is_blocking(findings)
    R.check("duplicate_record_after_shift_d_is_reported", ok, f"findings={_blocking_codes(findings)}")


# -- 8, 9: dangling / broken relationships -------------------------------------------

def missing_window_object_is_dangling_record(R):
    import bpy

    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    wall, fill = _prepare_window()
    gml_id = str(fill.get("gml_id"))
    mesh = fill.data
    bpy.data.objects.remove(fill, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    findings = _run()
    # The Window's own record is gone with its object (per-object storage,
    # ADR 0009); the dangling reference AB-02 leaves behind is the host
    # wall's RingRecord, still naming the deleted Window's opening_link.
    ok = any(f.code == c.DANGLING_RING for f in findings) and c.is_blocking(findings)
    R.check("missing_window_object_is_dangling_record", ok, f"gml_id={gml_id} findings={findings}")


def missing_wall_ring_pairing_after_broken_opening_link(R):
    from dataclasses import replace

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    wall, fill = _prepare_window()
    wall_record = storage.read_record(wall)
    wall_gml_id = str(wall.get("gml_id"))
    polygons = wall_record.polygons_of(wall_gml_id)
    corrupted = wall_record
    for polygon in polygons:
        if any(r.opening_link for r in polygon.interiors):
            new_interiors = tuple(
                replace(r, opening_link="OPENINGLINK_gone") if r.opening_link else r
                for r in polygon.interiors
            )
            corrupted = corrupted.with_polygon(replace(polygon, interiors=new_interiors))
    storage.write_record(wall, corrupted)
    findings = _run()
    ok = any(f.code == c.MISSING_RING_PAIRING for f in findings) and c.is_blocking(findings)
    R.check("missing_wall_ring_pairing_after_broken_opening_link", ok, f"findings={findings}")


# -- 10: semantics (LoD mismatch is a warning, not blocking) -------------------------

def lod_mismatch_is_warning_not_blocking(R):
    from city_modeler.shared.authoring_core.domain import consistency as c

    _flag(True)
    _wall, fill = _prepare_window()
    fill["lod"] = 2  # record says lod=3 (create_feature always authors LoD 3)
    findings = _run()
    ok = (any(f.code == c.LOD_MISMATCH and f.severity == c.WARNING for f in findings)
          and not any(f.gml_id == str(fill.get("gml_id")) and f.code != c.LOD_MISMATCH for f in findings))
    R.check("lod_mismatch_is_warning_not_blocking", ok, f"findings={findings}")


# -- 11, 12: export pre-flight wiring -------------------------------------------------

def clean_export_preflight_passes_both_versions(R):
    _flag(True)
    _wall, fill = _prepare_window()
    ok = fill is not None
    detail = {}
    if ok:
        out = h.out_dir()
        gml_id = str(fill.get("gml_id"))
        for version in ("2.0", "3.0"):
            path = out / f"p3_consistency_clean_v{version.replace('.', '')}.gml"
            text = h.export_scene(path, version)
            lint = h.xmllint(path, version)
            detail[version] = lint
            ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id in text
    R.check("clean_export_preflight_passes_both_versions", ok, json.dumps(detail))


def corrupted_scene_blocks_export_before_writing_file(R):
    import bpy

    _flag(True)
    wall, fill = _prepare_window()
    gml_id = str(fill.get("gml_id"))
    mesh = fill.data
    bpy.data.objects.remove(fill, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)

    out = h.out_dir()
    path = out / "p3_consistency_corrupted_v30.gml"
    if path.exists():
        path.unlink()
    # An operator that reports {'ERROR'} and returns {'CANCELLED'} makes
    # bpy.ops raise RuntimeError rather than just return the result set
    # (matches every other operator call in this suite, e.g. _draw_operator).
    try:
        result = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version="3.0")
    except RuntimeError as e:
        result = {"CANCELLED"}
        error = str(e)
    else:
        error = ""
    ok = result == {"CANCELLED"} and not path.exists()
    R.check("corrupted_scene_blocks_export_before_writing_file", ok,
            f"gml_id={gml_id} result={result} error={error!r} file_exists={path.exists()}")


def flag_off_export_preflight_is_a_no_op(R):
    """C6: with the flag off, export pre-flight never runs (byte-identical
    to pre-P3.3 export), even over a scene a flag-on run would block."""
    import bpy

    _flag(True)
    wall, fill = _prepare_window()
    gml_id = str(fill.get("gml_id"))
    mesh = fill.data
    bpy.data.objects.remove(fill, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)

    _flag(False)
    out = h.out_dir()
    path = out / "p3_consistency_flag_off_v30.gml"
    text = h.export_scene(path, "3.0")
    ok = path.exists() and gml_id not in text  # the dangling record isn't legacy state, so it can't appear anyway
    R.check("flag_off_export_preflight_is_a_no_op", ok, f"gml_id={gml_id} file_exists={path.exists()}")


def main():
    R = h.Results(__file__)
    checks = [
        fresh_draw_window_is_clean,
        fresh_stamp_window_is_clean,
        g_move_reports_coords_stale,
        parent_building_transform_reports_coords_stale,
        object_gml_id_mismatch_is_foreign_record_finding,
        duplicate_record_after_shift_d_is_reported,
        missing_window_object_is_dangling_record,
        missing_wall_ring_pairing_after_broken_opening_link,
        lod_mismatch_is_warning_not_blocking,
        clean_export_preflight_passes_both_versions,
        corrupted_scene_blocks_export_before_writing_file,
        flag_off_export_preflight_is_a_no_op,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
