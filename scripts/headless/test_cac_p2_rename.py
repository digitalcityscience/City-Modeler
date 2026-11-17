"""Semantic lookups after an object rename (CAC-P2.3, AB-10 characterisation).

Every check renames a CityGML object *after* some code captured its name
(an operator's stored properties, a SurfaceContext, the Stamp source, a
pending edit-exit conflict) and then asks that code to act again. Identity
lives in ``gml_id``, so a rename must not break any of them. A check that
fails today is registered as AB-10 in scripts/tests/known_arch_bugs.json.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p2_rename.py -- <out_dir>
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _gid(obj):
    return str(obj.get("gml_id") or "")


def _object_mode():
    if bpy.context.view_layer.objects.active is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')


def _edit(obj):
    _object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')


def _setup():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face = h.first_wall_with_exterior_face()
    return wall, face


def _window_on(wall, face, position_uv=None):
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )
    ctx = h.surface_context(wall, face)
    if position_uv is None:
        # AB-17 fix: a fixed (u, 0.5) can land outside this fixture's real
        # solid area (Railway arched cut-out) - find one that actually places.
        position_uv = h.solid_area_position_uv(wall, face, ctx, 0.1, 0.1)
    return place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.05),
        ctx, PlacementRequest(position_uv=position_uv, source="manual_draw"))


def _windows_of(wall):
    return sorted(c.name for c in wall.children
                  if str(c.get("cgml3_feature") or "").lower() in ("window", "windowsurface"))


def _plain(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _run_capturing_properties(op_class, call):
    """Run ``call()`` and return (result, the operator's registered property
    values at the end of ``execute``). That is what Blender's F9 redo panel
    re-executes with; headless there is no operator log to read it from."""
    captured = {}
    original = op_class.execute

    def spy(self, context):
        result = original(self, context)
        captured.update({k: _plain(getattr(self, k)) for k in self.properties.bl_rna.properties.keys()
                         if k != "rna_type"})
        return result

    op_class.execute = spy
    try:
        return call(), captured
    finally:
        op_class.execute = original


def rename_keeps_export_ids():
    _setup()
    before = h.collect_ids(h.normalise_xml(h.export_scene(h.out_dir() / "cac_p2_rename_before.gml", "3.0")))
    for obj in list(bpy.data.objects):
        if _gid(obj):
            obj.name = "cac_renamed_" + obj.name
    after = h.collect_ids(h.normalise_xml(h.export_scene(h.out_dir() / "cac_p2_rename_after.gml", "3.0")))
    ok, diff = h.compare_json(before, after)
    return ok, diff or f"{sum(len(v) for v in after.values())} ids identical after renaming every object"


def ab10_draw_redo_after_wall_rename():
    """F9-style re-execution with the stored operator properties after a rename."""
    from city_modeler.ops.facade_authoring import CGML_OT_facade_draw

    wall, face = _setup()
    gid = _gid(wall)
    ctx = h.surface_context(wall, face)
    first_uv = h.solid_area_position_uv(wall, face, ctx, 0.1, 0.1)
    second_uv = h.solid_area_position_uv(
        wall, face, ctx, 0.1, 0.1,
        avoid=[(first_uv[0] * ctx.width, first_uv[1] * ctx.height, 0.1, 0.1)])
    _edit(wall)
    first, stored = _run_capturing_properties(CGML_OT_facade_draw, lambda: bpy.ops.cgml.facade_draw(
        'EXEC_DEFAULT', semantic_type='Window', object_name=wall.name,
        face_index=face, position_uv=first_uv, width=0.2, height=0.2))
    _object_mode()
    stored["position_uv"] = second_uv  # a second, non-overlapping Window elsewhere in the solid area
    wall.name = "cac_renamed_wall"
    _edit(wall)
    try:
        second = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', **stored)
    except RuntimeError as e:
        second = f"RuntimeError: {e}"
    _object_mode()
    windows = _windows_of(wall)
    return (first == {'FINISHED'} and second == {'FINISHED'} and len(windows) == 2
            and _gid(wall) == gid), (
        f"first={first} second={second} stored object_name={stored.get('object_name')!r} "
        f"windows on renamed wall={windows}")


def ab10_stamp_after_source_rename():
    wall, face = _setup()
    ctx = h.surface_context(wall, face)
    first_uv = h.solid_area_position_uv(wall, face, ctx, 0.1, 0.1)
    second_uv = h.solid_area_position_uv(
        wall, face, ctx, 0.1, 0.1,
        avoid=[(first_uv[0] * ctx.width, first_uv[1] * ctx.height, 0.1, 0.1)])
    source = _window_on(wall, face, first_uv)
    _object_mode()
    bpy.ops.object.select_all(action='DESELECT')
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    set_result = bpy.ops.cgml.set_semantic_stamp_source()
    source.name = "cac_renamed_stamp_source"
    _edit(wall)
    try:
        result = bpy.ops.cgml.semantic_stamp(object_name=wall.name, face_index=face, position_uv=second_uv)
    except RuntimeError as e:
        result = f"RuntimeError: {e}"
    _object_mode()
    return set_result == {'FINISHED'} and result == {'FINISHED'} and len(_windows_of(wall)) == 2, (
        f"set_source={set_result} stamp={result} windows={_windows_of(wall)}")


def ab10_place_with_context_captured_before_rename():
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )
    wall, face = _setup()
    ctx = h.surface_context(wall, face)
    position_uv = h.solid_area_position_uv(wall, face, ctx, 0.1, 0.1)
    wall.name = "cac_renamed_wall"
    try:
        fill = place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.05),
            ctx, PlacementRequest(position_uv=position_uv, source="detector"))
    except (ValueError, RuntimeError) as e:
        return False, f"placement refused after rename: {e}"
    return fill.parent == wall, f"fill {fill.name} parent={getattr(fill.parent, 'name', None)}"


def ab10_alignment_overlay_after_wall_rename():
    from city_modeler.ops import facade_authoring

    wall, face = _setup()
    _window_on(wall, face)  # a neighbour, so the overlay has something to align to
    ctx = h.surface_context(wall, face)
    old_name = wall.name
    wall.name = "cac_renamed_wall"
    overlay = facade_authoring._wall_alignment_overlay(ctx, old_name, (0.7, 0.5), 0.2, 0.2, "Window")
    return overlay is not None, f"overlay after rename: {overlay!r}"


def ab10_resolve_ambiguous_after_rename():
    """The pending edit-exit conflict dialog runs after the user renamed the wall."""
    from city_modeler.ops import edit_exit_validation as eev

    wall, face = _setup()
    opening = _window_on(wall, face)
    pair = eev._find_pair(wall, opening)
    if pair is None:
        return False, "precondition: drawn Window has no ring pair"
    eev.reset_state()
    eev._flag_ambiguous(pair)  # background: records the conflict, no dialog
    conflict = eev.PENDING_AMBIGUOUS[-1]
    wall.name = "cac_renamed_wall"
    op_props = bpy.ops.cgml.resolve_ambiguous_sync.get_rna_type().properties.keys()
    kwargs = {k: v for k, v in conflict._asdict().items() if k in op_props}
    try:
        result = bpy.ops.cgml.resolve_ambiguous_sync('EXEC_DEFAULT', **kwargs)
    except RuntimeError as e:
        result = f"RuntimeError: {e}"
    return result == {'FINISHED'}, f"dialog execute after rename: {result} (props {sorted(kwargs)})"


def edit_exit_sync_after_rename_in_edit_mode():
    """Rename a Window while it is in Edit Mode, move it, exit: the edit-exit
    handler must still auto-sync the wall ring (regression guard for the
    P2.3 handler change; the rename happens between entry and exit)."""
    import bmesh
    from mathutils import Vector
    from city_modeler.ops import edit_exit_validation as eev
    from city_modeler.shared.ring_opening_sync import pairs_for_wall_children

    wall, face = _setup()
    fill = _window_on(wall, face)
    eev.reset_state()
    _edit(fill)
    bpy.context.view_layer.update()
    fill.name = "cac_renamed_in_edit_mode"
    bpy.context.view_layer.update()
    bm = bmesh.from_edit_mesh(fill.data)
    for v in bm.verts:
        v.co += Vector((0.3, 0.0, 0.0))
    bmesh.update_edit_mesh(fill.data)
    bpy.ops.object.mode_set(mode='OBJECT')
    pair = next((p for p in pairs_for_wall_children(wall) if p.opening is fill), None)
    return pair is not None and pair.in_sync and not eev.PENDING_AMBIGUOUS, (
        f"pair={'found' if pair else None} in_sync={getattr(pair, 'in_sync', None)} "
        f"distance={getattr(pair, 'distance', None)} pending={eev.PENDING_AMBIGUOUS}")


CHECKS = [
    rename_keeps_export_ids,
    edit_exit_sync_after_rename_in_edit_mode,
    ab10_draw_redo_after_wall_rename,
    ab10_stamp_after_source_rename,
    ab10_place_with_context_captured_before_rename,
    ab10_alignment_overlay_after_wall_rename,
    ab10_resolve_ambiguous_after_rename,
]


def main():
    R = h.Results(__file__)
    os.environ.pop("CGML_AUTHORING_CORE", None)
    for check in CHECKS:
        try:
            ok, detail = check()
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-8:])
        R.check(check.__name__, ok, detail)
    R.finish()


main()
