"""Characterise Draw (Window / floating Door) placement outside the host
face's solid area (CAC-P3.0.1, from the G2.5 report "Open points for the
reviewer" #2).

``shared/facade_placement.py::_place_interior_ring_opening`` (Window and
floating Door) computes the fill rectangle's world corners and hands them
straight to ``Openings_Cutter.create_joined_surfaces``, which writes the
exterior/interior faces unconditionally - no point-in-polygon test against
the host face's exterior ring or existing interior rings anywhere in that
call chain. Contrast ``_place_exterior_notch_opening`` (ground-touching
Door only), which calls ``_resolve_exterior_notch_geometry`` first and
rejects when the door's top edge is not inside the host face's real
(possibly concave) polygon.

This measures the host face's actual outline (not the harness's bounding-box
uv frame - see ``cac_harness.surface_context`` docstring) in the same
world-frame axes ``_resolve_exterior_notch_geometry`` uses, and drives Draw
through both ``bpy.ops.cgml.facade_draw`` and ``place_semantic_element``
directly for:

  1. Window entirely inside the exterior-ring cut-out (in the bbox, outside
     the solid polygon) - expect refuse.
  2. Window straddling the exterior boundary - expect refuse.
  3. Window overlapping an existing interior ring - expect refuse.
  4. Window inside an existing interior ring - expect refuse.
  5. Control: Window fully inside the solid area, clear of rings - expect
     succeed.
  6. Control: ground Door outside the face - expect refuse (documents the
     existing check).

Also reports whether the P0.5/P2.5/P3.0 fixed uv positions (0.5,0.5),
(0.3,0.5), (0.6,0.55), (0.8,0.5) fall inside the measured solid area - report
only, their tests/goldens are not touched.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_draw_placement.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3  # small rectangle so it fits inside the fixture's known-solid strips
MEASUREMENTS = {}


# --------------------------------------------------------------------------
# Geometry (pure, no bpy)
# --------------------------------------------------------------------------

def _point_in_polygon(pt, poly):
    """Standard even-odd ray-cast test; works for a concave polygon."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_at_y = (y - y1) * (x2 - x1) / (y2 - y1) + x1
            if x < x_at_y:
                inside = not inside
    return inside


def _rect_corners(u, v, half_w, half_h):
    return [(u + half_w, v + half_h), (u - half_w, v + half_h),
            (u - half_w, v - half_h), (u + half_w, v - half_h)]


def _all_inside(poly, corners):
    return all(_point_in_polygon(c, poly) for c in corners)


def _none_inside(poly, corners):
    return not any(_point_in_polygon(c, poly) for c in corners)


def _rects_overlap(c1, c2):
    us1, vs1 = [p[0] for p in c1], [p[1] for p in c1]
    us2, vs2 = [p[0] for p in c2], [p[1] for p in c2]
    return not (max(us1) <= min(us2) or max(us2) <= min(us1)
                or max(vs1) <= min(vs2) or max(vs2) <= min(vs1))


def _search_grid(poly, bbox, half_w, half_h, predicate, step=0.05):
    min_u, max_u, min_v, max_v = bbox
    u = min_u + half_w
    while u <= max_u - half_w + 1e-9:
        v = min_v + half_h
        while v <= max_v - half_h + 1e-9:
            corners = _rect_corners(u, v, half_w, half_h)
            if predicate(poly, corners):
                return u, v
            v += step
        u += step
    return None


def _search_straddling(poly, bbox, half_w, half_h, step=0.05):
    min_u, max_u, min_v, max_v = bbox
    u = min_u + half_w
    while u <= max_u - half_w + 1e-9:
        v = min_v + half_h
        while v <= max_v - half_h + 1e-9:
            corners = _rect_corners(u, v, half_w, half_h)
            inside_flags = [_point_in_polygon(c, poly) for c in corners]
            if any(inside_flags) and not all(inside_flags):
                return u, v
            v += step
        u += step
    return None


# --------------------------------------------------------------------------
# Blender-bound measurement
# --------------------------------------------------------------------------

def _face_polygon_uv(wall, face_index, origin, h_axis, v_axis):
    """The face's own vertex loop projected into (u, v) metres."""
    mesh = wall.data
    mw = wall.matrix_world
    poly = mesh.polygons[face_index]
    pts = []
    for vi in poly.vertices:
        wp = mw @ mesh.vertices[vi].co
        rel = wp - origin
        pts.append((rel.dot(h_axis), rel.dot(v_axis)))
    return pts


def _prepare():
    """Import once; return (wall, face_index, origin, h_axis, v_axis, ext_poly, bbox, width, height)."""
    from mathutils import Vector

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    origin = Vector(ctx.origin_world)
    h_axis = Vector(ctx.horizontal_axis_world).normalized()
    v_axis = Vector(ctx.vertical_axis_world).normalized()
    ext_poly = _face_polygon_uv(wall, face_index, origin, h_axis, v_axis)
    us = [p[0] for p in ext_poly]
    vs = [p[1] for p in ext_poly]
    bbox = (min(us), max(us), min(vs), max(vs))
    return wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox


def _uv_frac(ctx, u_m, v_m):
    return (u_m / ctx.width, v_m / ctx.height)


def _edit_face(obj, face_index):
    """Edit Mode on ``obj`` alone with exactly ``face_index`` selected -
    ``CGML_OT_facade_draw.poll()`` requires this (see test_cac_p3_guards.py)."""
    import bmesh
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def _draw_window(wall, face_index, ctx, position_uv, size=SIZE, expect_success=None):
    """Run through the real operator; returns (result_set, error_text, fill_or_None)."""
    import bpy

    _edit_face(wall, face_index)
    before = {o.name for o in bpy.data.objects}
    try:
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type="Window", width=size, height=size, depth=0.05,
            position_uv=position_uv, object_name=wall.name,
            object_gml_id=str(wall.get("gml_id") or ""), face_index=face_index,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the fill object itself.
    new_objs = [n for n in {o.name for o in bpy.data.objects} - before if not n.endswith(".cgml_display")]
    fill = None
    if new_objs:
        import bpy as _bpy
        fill = _bpy.data.objects[new_objs[0]]
    return result, error, fill


def _draw_ground_door(wall, face_index, position_uv, size=SIZE):
    import bpy

    _edit_face(wall, face_index)
    before = {o.name for o in bpy.data.objects}
    try:
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type="Door", door_topology="GROUND",
            width=size, height=size * 2, depth=0.05, position_uv=position_uv,
            object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
            face_index=face_index,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    new_objs = [n for n in {o.name for o in bpy.data.objects} - before]
    return result, error, bool(new_objs)


def _place_direct(ctx, position_uv, size=SIZE):
    """Same placement via place_semantic_element directly, for comparison."""
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )
    try:
        fill = place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=size, height=size, depth=0.05),
            ctx, PlacementRequest(position_uv=position_uv, source="manual_draw"),
        )
        return True, "", fill
    except (ValueError, RuntimeError) as e:
        return False, str(e), None


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _refused(result, error):
    return result != {"FINISHED"} and bool(error)


def window_inside_solid_area_succeeds():
    """Case 5 control: a Window fully clear of the cut-out and any ring must place."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, half, half, _all_inside)
    if found is None:
        return False, "no fully-solid location found in the host face bbox - fixture assumption failed"
    u_m, v_m = found
    uv = _uv_frac(ctx, u_m, v_m)
    MEASUREMENTS["case5_uv_metres"] = (u_m, v_m)
    MEASUREMENTS["case5_position_uv"] = uv
    result, error, fill = _draw_window(wall, face_index, ctx, uv)
    ok = result == {"FINISHED"} and fill is not None
    return ok, f"uv_metres=({u_m:.3f},{v_m:.3f}) position_uv={uv} result={result} error={error!r}"


def window_inside_cutout_refused():
    """Case 1 (desired: refuse; currently: no check exists)."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, half, half, _none_inside)
    if found is None:
        return False, "no fully-outside-polygon-but-in-bbox location found - fixture assumption failed"
    u_m, v_m = found
    uv = _uv_frac(ctx, u_m, v_m)
    MEASUREMENTS["case1_uv_metres"] = (u_m, v_m)
    MEASUREMENTS["case1_position_uv"] = uv
    result, error, fill = _draw_window(wall, face_index, ctx, uv)
    ok = _refused(result, error)
    MEASUREMENTS["case1_actual_result"] = str(result)
    return ok, f"uv_metres=({u_m:.3f},{v_m:.3f}) position_uv={uv} result={result} error={error!r} fill={fill.name if fill else None}"


def window_straddles_boundary_refused():
    """Case 2 (desired: refuse; currently: no check exists)."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_straddling(ext_poly, bbox, half, half)
    if found is None:
        return False, "no boundary-straddling location found - fixture assumption failed"
    u_m, v_m = found
    uv = _uv_frac(ctx, u_m, v_m)
    MEASUREMENTS["case2_uv_metres"] = (u_m, v_m)
    MEASUREMENTS["case2_position_uv"] = uv
    result, error, fill = _draw_window(wall, face_index, ctx, uv)
    ok = _refused(result, error)
    MEASUREMENTS["case2_actual_result"] = str(result)
    return ok, f"uv_metres=({u_m:.3f},{v_m:.3f}) position_uv={uv} result={result} error={error!r} fill={fill.name if fill else None}"


def window_overlaps_interior_ring_refused():
    """Case 3: draw a solid control Window (case 5's rectangle), then try a
    second Window whose rectangle half-overlaps the first's - desired:
    refuse; currently: no check exists."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, half, half, _all_inside)
    if found is None:
        return False, "no fully-solid location found for the first Window - fixture assumption failed"
    u0, v0 = found
    result1, error1, fill1 = _draw_window(wall, face_index, ctx, _uv_frac(ctx, u0, v0))
    if result1 != {"FINISHED"} or fill1 is None:
        return False, f"setup: first Window failed to place: result={result1} error={error1!r}"

    u2, v2 = u0 + SIZE * 0.5, v0
    overlap = _rects_overlap(_rect_corners(u0, v0, half, half), _rect_corners(u2, v2, half, half))
    uv2 = _uv_frac(ctx, u2, v2)
    MEASUREMENTS["case3_first_uv_metres"] = (u0, v0)
    MEASUREMENTS["case3_second_uv_metres"] = (u2, v2)
    MEASUREMENTS["case3_second_position_uv"] = uv2
    MEASUREMENTS["case3_rects_overlap"] = overlap
    result2, error2, fill2 = _draw_window(wall, face_index, ctx, uv2)
    ok = overlap and _refused(result2, error2)
    MEASUREMENTS["case3_actual_result"] = str(result2)
    return ok, (f"overlap={overlap} second_uv_metres=({u2:.3f},{v2:.3f}) position_uv={uv2} "
                f"result={result2} error={error2!r} fill={fill2.name if fill2 else None}")


def window_inside_interior_ring_refused():
    """Case 4: a smaller Window centred exactly inside a larger one's
    rectangle - desired: refuse (opening inside an opening); currently: no
    check exists."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    outer_half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, outer_half, outer_half, _all_inside)
    if found is None:
        return False, "no fully-solid location found for the outer Window - fixture assumption failed"
    u0, v0 = found
    result1, error1, fill1 = _draw_window(wall, face_index, ctx, _uv_frac(ctx, u0, v0), size=SIZE)
    if result1 != {"FINISHED"} or fill1 is None:
        return False, f"setup: outer Window failed to place: result={result1} error={error1!r}"

    inner_size = SIZE * 0.4
    inner_half = inner_size / 2.0
    nested = _all_inside(_rect_corners(u0, v0, outer_half, outer_half), _rect_corners(u0, v0, inner_half, inner_half))
    uv_inner = _uv_frac(ctx, u0, v0)
    MEASUREMENTS["case4_outer_uv_metres"] = (u0, v0)
    MEASUREMENTS["case4_inner_position_uv"] = uv_inner
    MEASUREMENTS["case4_nested"] = nested
    result2, error2, fill2 = _draw_window(wall, face_index, ctx, uv_inner, size=inner_size)
    ok = nested and _refused(result2, error2)
    MEASUREMENTS["case4_actual_result"] = str(result2)
    return ok, (f"nested={nested} inner_position_uv={uv_inner} inner_size={inner_size:.3f} "
                f"result={result2} error={error2!r} fill={fill2.name if fill2 else None}")


def ground_door_outside_face_refused():
    """Case 6 control: a ground Door drawn well outside the host face must
    already be refused (documents the existing check, not a bug)."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    min_u, max_u, min_v, max_v = bbox
    u_m, v_m = max_u + 5.0, min_v + (max_v - min_v) * 0.1
    uv = _uv_frac(ctx, u_m, v_m)
    MEASUREMENTS["case6_position_uv"] = uv
    result, error, created = _draw_ground_door(wall, face_index, uv)
    ok = _refused(result, error) and not created
    MEASUREMENTS["case6_actual_result"] = str(result)
    return ok, f"position_uv={uv} result={result} error={error!r} created={created}"


def direct_api_agrees_with_operator():
    """place_semantic_element (direct call) refuses case 1 the same way the
    operator does - the fix lives in the shared placement kernel, not in the
    operator layer, so both call paths must agree."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, half, half, _none_inside)
    if found is None:
        return False, "no fully-outside-polygon-but-in-bbox location found - fixture assumption failed"
    u_m, v_m = found
    uv = _uv_frac(ctx, u_m, v_m)
    succeeded, error, fill = _place_direct(ctx, uv)
    MEASUREMENTS["direct_api_case1_position_uv"] = uv
    MEASUREMENTS["direct_api_case1_succeeded"] = succeeded
    ok = (not succeeded) and fill is None and bool(error)
    return ok, f"position_uv={uv} succeeded={succeeded} error={error!r} (False here means the direct API refuses, same as the operator)"


def existing_fixed_positions_relative_to_solid_area():
    """Report-only: do the P0.5/P2.5/P3.0 fixed uv positions land inside the
    measured solid area? No test/golden change; informational check that
    always passes so the measurement lands in the result file."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    fixed = {
        "P0.5 (0.5,0.5)": (0.5, 0.5),
        "P2.5 (0.3,0.5)": (0.3, 0.5),
        "P3.0 draw (0.6,0.55)": (0.6, 0.55),
        "P3.0 stamp (0.8,0.5)": (0.8, 0.5),
    }
    report = {}
    for label, (u_frac, v_frac) in fixed.items():
        u_m, v_m = u_frac * ctx.width, v_frac * ctx.height
        corners = _rect_corners(u_m, v_m, SIZE / 2.0, SIZE / 2.0)
        report[label] = {
            "uv_metres": (round(u_m, 3), round(v_m, 3)),
            "centre_inside_solid": _point_in_polygon((u_m, v_m), ext_poly),
            "all_corners_inside_solid": _all_inside(ext_poly, corners),
        }
    MEASUREMENTS["existing_fixed_positions"] = report
    MEASUREMENTS["host_face_bbox_metres"] = bbox
    MEASUREMENTS["host_face_wall"] = wall.name
    MEASUREMENTS["host_face_index"] = face_index
    return True, json.dumps(report, indent=2)


def invalid_placement_exports_xmllint_clean():
    """Report-only (always passes): export a Window placed inside the
    cut-out for both CityGML versions and record whether xmllint accepts
    it. xmllint schema-validates XML structure only - it cannot detect an
    interior ring lying outside its polygon's exterior ring, or two
    interior rings of the same polygon overlapping, both of which are
    invalid gml:Polygon content regardless of xmllint's verdict."""
    h.load_addon()
    wall, face_index, ctx, origin, h_axis, v_axis, ext_poly, bbox = _prepare()
    half = SIZE / 2.0
    found = _search_grid(ext_poly, bbox, half, half, _none_inside)
    if found is None:
        return True, "no outside-polygon location found - skipped (fixture assumption failed elsewhere)"
    u_m, v_m = found
    uv = _uv_frac(ctx, u_m, v_m)
    result, error, fill = _draw_window(wall, face_index, ctx, uv)
    if result != {"FINISHED"} or fill is None:
        return True, f"placement itself was refused (result={result}); nothing to export"

    out = h.out_dir()
    report = {}
    for version in ("2.0", "3.0"):
        path = out / f"p3_draw_placement_invalid_v{version.replace('.', '')}.gml"
        try:
            h.export_scene(path, version)
            lint = h.xmllint(path, version)
            report[version] = lint
        except Exception as e:
            report[version] = {"error": str(e)}
    MEASUREMENTS["invalid_placement_export_xmllint"] = report
    return True, json.dumps(report)


def main():
    R = h.Results(__file__)
    out = h.out_dir()

    checks = [
        ("window_inside_solid_area_succeeds", window_inside_solid_area_succeeds),
        ("window_inside_cutout_refused", window_inside_cutout_refused),
        ("window_straddles_boundary_refused", window_straddles_boundary_refused),
        ("window_overlaps_interior_ring_refused", window_overlaps_interior_ring_refused),
        ("window_inside_interior_ring_refused", window_inside_interior_ring_refused),
        ("ground_door_outside_face_refused", ground_door_outside_face_refused),
        ("direct_api_agrees_with_operator", direct_api_agrees_with_operator),
        ("existing_fixed_positions_relative_to_solid_area", existing_fixed_positions_relative_to_solid_area),
        ("invalid_placement_exports_xmllint_clean", invalid_placement_exports_xmllint_clean),
    ]
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-10:])
        R.check(name, ok, detail)

    (out / "p3_draw_placement_measurements.json").write_text(
        json.dumps(MEASUREMENTS, indent=2, default=str), encoding="utf-8"
    )
    R.finish()


main()
