"""Record-backed Window create lifecycle (CAC-P3.2, PLAN.md P3.2).

``create_feature`` (``shared/authoring_core/blender/usecases.py``) wraps
``place_semantic_element`` unchanged and writes a Window ``FeatureRecord`` +
the host WallSurface's updated ring ``PolygonRecord`` in the same undo step,
derived from the exact legacy state ``place_semantic_element`` just wrote
(dual-write by construction). ``CGML_OT_facade_draw`` routes Window Draw
through it when ``experimental_authoring_core`` is on; Door and flag-off
stay on the legacy-only path.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_create_feature.py -- <out_dir>
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


def _outside_position_uv(wall, face_index, ctx):
    """A uv fraction inside the host face's bbox but outside its real solid
    area (AB-17), the same grid search ``test_cac_p3_draw_placement.py``
    uses, condensed for this file's single use."""
    from mathutils import Vector

    mesh = wall.data
    mw = wall.matrix_world
    poly = mesh.polygons[face_index]
    origin = Vector(ctx.origin_world)
    h_axis = Vector(ctx.horizontal_axis_world).normalized()
    v_axis = Vector(ctx.vertical_axis_world).normalized()
    ext_poly = []
    for vi in poly.vertices:
        wp = mw @ mesh.vertices[vi].co
        rel = wp - origin
        ext_poly.append((rel.dot(h_axis), rel.dot(v_axis)))

    def inside(pt):
        x, y = pt
        result = False
        n = len(ext_poly)
        for i in range(n):
            x1, y1 = ext_poly[i]
            x2, y2 = ext_poly[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                x_at_y = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x < x_at_y:
                    result = not result
        return result

    half = SIZE / 2.0
    us = [p[0] for p in ext_poly]
    vs = [p[1] for p in ext_poly]
    min_u, max_u, min_v, max_v = min(us), max(us), min(vs), max(vs)
    u = min_u + half
    while u <= max_u - half + 1e-9:
        v = min_v + half
        while v <= max_v - half + 1e-9:
            corners = [(u + half, v + half), (u - half, v + half),
                       (u - half, v - half), (u + half, v - half)]
            if not any(inside(c) for c in corners):
                return (u / ctx.width, v / ctx.height)
            v += 0.05
        u += 0.05
    raise AssertionError("no outside-solid-area location found - fixture assumption failed")


def _draw_operator(wall, face_index, position_uv, size=SIZE, semantic_type="Window"):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode="EDIT")
    import bmesh
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(wall.data)

    before = {o.name for o in bpy.data.objects}
    try:
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type=semantic_type, width=size, height=size, depth=0.05,
            position_uv=position_uv, object_name=wall.name,
            object_gml_id=str(wall.get("gml_id") or ""), face_index=face_index,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the fill object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    fill = bpy.data.objects[new_names[0]] if new_names else None
    return result, error, fill


def flag_off_draw_window_unchanged(R):
    """Flag off: Draw Window never writes a record on either object."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(False)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    result, error, fill = _draw_operator(wall, face_index, uv)
    ok = (result == {"FINISHED"} and fill is not None
          and not storage.has_record(fill) and not storage.has_record(wall))
    R.check("flag_off_draw_window_unchanged", ok,
            f"result={result} error={error!r} fill={getattr(fill, 'name', None)} "
            f"fill_record={storage.has_record(fill) if fill else None} wall_record={storage.has_record(wall)}")


def flag_on_draw_window_creates_record(R):
    """Flag on: the operator routes through create_feature and the fill
    object carries a fresh, committed Window record."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    result, error, fill = _draw_operator(wall, face_index, uv)
    ok = result == {"FINISHED"} and fill is not None and storage.has_record(fill) and storage.has_record(wall)
    detail = f"result={result} error={error!r} fill={getattr(fill, 'name', None)}"
    if ok:
        record = storage.read_record(fill)
        feature = record.feature(str(fill.get("gml_id")))
        ok = (
            feature is not None and feature.kind == "Window" and feature.provenance == "authored"
            and feature.generator == "rectangular_facade_window"
            and feature.host_id == str(wall.get("gml_id"))
            and feature.parent_id == str(wall.get("gml_id"))
            and feature.opening_link
            and len(feature.polygon_ids) >= 1
            and abs(feature.params["width"] - SIZE) < 1e-5
            and abs(feature.params["height"] - SIZE) < 1e-5
            and abs(feature.params["depth"] - 0.05) < 1e-5
        )
        wall_record = storage.read_record(wall)
        wall_feature = wall_record.feature(str(wall.get("gml_id")))
        wall_polys = wall_record.polygons_of(str(wall.get("gml_id")))
        ring = next((r for p in wall_polys for r in p.interiors if r.opening_link == feature.opening_link), None)
        window_polys = record.polygons_of(feature.gml_id)
        committed_window = any(p.geometry_state == "committed" and len(p.exterior.coords) >= 3 for p in window_polys)
        committed_ring = ring is not None and len(ring.coords) >= 3
        ok = ok and wall_feature is not None and ring is not None and committed_window and committed_ring
        detail += (f" feature={feature} ring={ring} committed_window={committed_window} "
                   f"committed_ring={committed_ring}")
    R.check("flag_on_draw_window_creates_record", ok, detail)


def accessor_reads_record_origin(R):
    from city_modeler.shared.authoring_core.blender.accessor import read_feature_view

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    _, _, fill = _draw_operator(wall, face_index, uv)
    view = read_feature_view(fill) if fill else None
    ok = fill is not None and view is not None and view.origin == "record" and view.feature_type == "Window"
    R.check("accessor_reads_record_origin", ok, f"fill={getattr(fill, 'name', None)} view={view}")


def registry_resolves_by_gml_id(R):
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    _, _, fill = _draw_operator(wall, face_index, uv)
    got = REGISTRY.get(str(fill.get("gml_id"))) if fill else None
    ok = fill is not None and got is not None and got.name == fill.name
    R.check("registry_resolves_by_gml_id", ok, f"fill={getattr(fill, 'name', None)} resolved={getattr(got, 'name', None)}")


def dual_write_parity(R):
    """Record and legacy-derived state agree for the created Window."""
    from city_modeler.shared.authoring_core.blender import legacy_views, storage
    from city_modeler.shared.authoring_core.domain.legacy_reader import feature_record_from_legacy

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    _, _, fill = _draw_operator(wall, face_index, uv)
    ok = fill is not None
    detail = ""
    if ok:
        record = storage.read_record(fill)
        feature = record.feature(str(fill.get("gml_id")))
        legacy_feature = feature_record_from_legacy(
            legacy_views.object_view(fill), [legacy_views.object_view(wall)] + [
                legacy_views.object_view(a) for a in _ancestors(wall)
            ],
        )
        ok = (legacy_feature is not None and legacy_feature.gml_id == feature.gml_id
              and legacy_feature.host_id == feature.host_id
              and legacy_feature.parent_id == feature.parent_id
              and legacy_feature.opening_link == feature.opening_link)
        detail = f"record={feature} legacy={legacy_feature}"
    R.check("dual_write_parity", ok, detail)


def _ancestors(obj):
    chain = []
    seen = set()
    cur = obj.parent
    while cur is not None and cur.name not in seen:
        seen.add(cur.name)
        chain.append(cur)
        cur = cur.parent
    return chain


def export_xmllint_clean_both_versions(R):
    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    result, error, fill = _draw_operator(wall, face_index, uv)
    ok = result == {"FINISHED"} and fill is not None
    detail = {}
    if ok:
        out = h.out_dir()
        gml_id = str(fill.get("gml_id"))
        for version in ("2.0", "3.0"):
            path = out / f"p3_create_feature_v{version.replace('.', '')}.gml"
            text = h.export_scene(path, version)
            lint = h.xmllint(path, version)
            detail[version] = lint
            ok = ok and lint["exit"] == 0 and lint["errors"] == 0 and gml_id in text
    R.check("export_xmllint_clean_both_versions", ok, json.dumps(detail))


def undo_redo_restores_object_and_records(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    wall_record_before = storage.has_record(wall)
    face_count_before = len(wall.data.polygons)

    bpy.ops.ed.undo_push(message="cac: before draw window")
    result, error, fill = _draw_operator(wall, face_index, uv)
    bpy.ops.ed.undo_push(message="cac: after draw window")
    if result != {"FINISHED"} or fill is None:
        R.check("undo_redo_restores_object_and_records", False, f"setup failed: result={result} error={error!r}")
        return
    fill_name = fill.name

    bpy.ops.ed.undo()
    # Undo can invalidate every stored Object reference, not just the newly
    # created one - re-fetch the wall by name rather than reuse the old ref.
    wall_after_undo = bpy.data.objects[wall_name]
    undo_object_gone = fill_name not in bpy.data.objects
    undo_wall_record_restored = storage.has_record(wall_after_undo) == wall_record_before
    undo_face_count_restored = len(wall_after_undo.data.polygons) == face_count_before

    bpy.ops.ed.redo()
    redo_object_back = fill_name in bpy.data.objects
    redo_fill_has_record = redo_object_back and storage.has_record(bpy.data.objects[fill_name])
    redo_wall_has_record = storage.has_record(bpy.data.objects[wall_name])

    ok = (undo_object_gone and undo_wall_record_restored and undo_face_count_restored
          and redo_object_back and redo_fill_has_record and redo_wall_has_record)
    R.check("undo_redo_restores_object_and_records", ok,
            f"undo: object_gone={undo_object_gone} wall_record_restored={undo_wall_record_restored} "
            f"face_count_restored={undo_face_count_restored}; redo: object_back={redo_object_back} "
            f"fill_has_record={redo_fill_has_record} wall_has_record={redo_wall_has_record}")


def invalid_ab17_placement_refused_before_mutation(R):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.usecases import CreateFeatureError, create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = _outside_position_uv(wall, face_index, ctx)
    face_count_before = len(wall.data.polygons)
    objects_before = {o.name for o in __import__("bpy").data.objects}
    wall_record_before = storage.has_record(wall)

    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    refused = False
    error = ""
    try:
        create_feature("Window", ctx, definition, placement)
    except (CreateFeatureError, ValueError) as e:
        refused = True
        error = str(e)

    import bpy
    unchanged = (
        len(wall.data.polygons) == face_count_before
        and {o.name for o in bpy.data.objects} == objects_before
        and storage.has_record(wall) == wall_record_before
    )
    ok = refused and unchanged
    R.check("invalid_ab17_placement_refused_before_mutation", ok,
            f"refused={refused} error={error!r} unchanged={unchanged}")


def main():
    R = h.Results(__file__)
    checks = [
        flag_off_draw_window_unchanged,
        flag_on_draw_window_creates_record,
        accessor_reads_record_origin,
        registry_resolves_by_gml_id,
        dual_write_parity,
        export_xmllint_clean_both_versions,
        undo_redo_restores_object_and_records,
        invalid_ab17_placement_refused_before_mutation,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
