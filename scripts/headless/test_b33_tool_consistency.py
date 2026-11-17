"""B33: the authoring tools behave alike.

1. after placing, the mode the tool started in: Balcony / Chimney / Drag on Wall from Edit Facade
   come back to Edit Facade (the new installation active, its faces selected); from Object Mode
   (T tool) they stay in Object Mode
2. one status-bar format (shared/modal_hint.py) for every modal; Window / Door / Stamp / Assign
   CityGML Surface set it and clear it on every exit (also cancel and an exception)
3. the Balcony / Chimney T tools use the row's target rule: a building face of the wrong class
   places nothing and says why
4. Roof: popover and T tool share wm.cgml_roof_choice; the pitch resets only when the type changes
5. Stamp: the whole session is one undo step (no push per click), Esc keeps the copies
6. New Building / From Footprint / Edit Facade are gated by step_reason ("Now: ..." tooltip)
7. operator labels (F9 / undo) = the names the user sees; Balcony / Chimney say BuildingInstallation

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b33_tool_consistency.py
"""
import inspect
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES, activate  # noqa: E402
from test_b28_balcony_chimney_gallery import edit_face  # noqa: E402


class Ev:
    def __init__(self, type_, value='PRESS'):
        self.type, self.value = type_, value


def fake(op_cls, kind):
    return type("F", (), {"kind": kind, "place_from_hit": op_cls.place_from_hit, "WANT": getattr(op_cls, "WANT", None),
                          "MISS": getattr(op_cls, "MISS", "")})()


def new_building():
    """10 x 8 x 9 m gable building centred at the origin."""
    import json
    ring = [(-5, -4, 0), (5, -4, 0), (5, 4, 0), (-5, 4, 0)]
    try:
        bpy.ops.cgml.building_new(points=json.dumps(ring), height=9.0)
    except TypeError:  # before B27 there was no points; not expected here
        bpy.ops.cgml.building_new()
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    return b


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")
    fa = import_module(f"{addon}.ops.facade_authoring")
    ra = import_module(f"{addon}.ops.roof_authoring")
    ba = import_module(f"{addon}.ops.building_authoring")
    wp = import_module(f"{addon}.ui.workflow_panels")
    try:
        mh = import_module(f"{addon}.shared.modal_hint")
    except ImportError:
        mh = None
    check("2: shared status-bar helper", mh is not None)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    b = new_building()
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    roof = next(o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface")
    front = min(walls, key=lambda o: min((o.matrix_world @ v.co).y for v in o.data.vertices))
    wall_y = min((front.matrix_world @ v.co).y for v in front.data.vertices)
    fwd = Vector((0, 1, 0))

    # --- 1. mode after placing ---------------------------------------------------------
    edit_face(front, lambda n, c: -n.y)
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_balcony_add, 'PROJECTING'), bpy.context,
                                       Vector((2.0, wall_y - 20, 4.0)), fwd)
    known = set(bpy.data.objects.keys())  # the wall stays active (B97)
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    bal = next(o for o in bpy.data.objects if o.name not in known)
    check("1: balcony from Edit Facade: back in Edit Mode", res == {'FINISHED'} and bpy.context.mode == 'EDIT_MESH',
          bpy.context.mode)
    check("1: B97 the wall stays active with its face selected (like a window), no re-selecting",
          bal.get("cgml_semantic_type") == "Balcony" and bpy.context.view_layer.objects.active == front
          and front.mode == 'EDIT' and front.data.total_face_sel == 1,
          f"active {bpy.context.view_layer.objects.active.name} wall {front.data.total_face_sel}")

    edit_face(roof, lambda n, c: n.z)
    rz = max((roof.matrix_world @ v.co).z for v in roof.data.vertices)
    ry = sum((roof.matrix_world @ v.co).y for v in roof.data.vertices) / len(roof.data.vertices)
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_chimney_add, 'SQUARE'), bpy.context,
                                       Vector((-2.0, ry, rz + 20)), Vector((0, 0, -1)))
    known = set(bpy.data.objects.keys())  # the wall stays active (B97)
    res = bpy.ops.cgml.chimney_add('EXEC_DEFAULT', kind='SQUARE', **(cand or {}))
    ch = next(o for o in bpy.data.objects if o.name not in known)
    check("1: chimney from Edit Facade: back in Edit Mode, roof face kept (B97)",
          res == {'FINISHED'} and bpy.context.mode == 'EDIT_MESH' and ch.get("cgml_semantic_type") == "Chimney",
          f"{bpy.context.mode} {ch.name}")

    bpy.ops.object.mode_set(mode='OBJECT')
    activate(b)
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_balcony_add, 'PROJECTING'), bpy.context,
                                       Vector((-3.0, wall_y - 20, 5.0)), fwd)
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    check("1: from Object Mode (T tool) it stays Object Mode",
          res == {'FINISHED'} and bpy.context.mode == 'OBJECT' and bpy.context.selected_objects
          == [bpy.context.active_object])
    check("1: Drag on Wall hands over in Edit Mode (no forced Object Mode)",
          "mode_set" not in inspect.getsource(ia.CGML_OT_balcony_draw.modal))

    # --- 3. T tool target rule ------------------------------------------------------------
    ch_side = Vector(((min((ch.matrix_world @ v.co).x for v in ch.data.vertices)
                       + max((ch.matrix_world @ v.co).x for v in ch.data.vertices)) / 2, -20.0,
                      max((ch.matrix_world @ v.co).z for v in ch.data.vertices) - 0.2))
    op = fake(ia.CGML_OT_balcony_add, 'PROJECTING')
    cand = ia._ClickPlace.candidate_at(op, bpy.context, ch_side, fwd)
    why = getattr(op, "why", "")
    check("3: balcony T tool on a chimney side: nothing, and why", cand is None and "BuildingInstallation" in why,
          f"{cand} / {why!r}")
    op = fake(ia.CGML_OT_chimney_add, 'SQUARE')
    cand = ia._ClickPlace.candidate_at(op, bpy.context, Vector((0.0, wall_y - 20, 2.0)), fwd)
    check("3: chimney T tool on a wall: nothing, and why", cand is None and "WallSurface" in getattr(op, "why", ""),
          getattr(op, "why", ""))
    op = fake(ia.CGML_OT_chimney_add, 'SQUARE')
    cand = ia._ClickPlace.candidate_at(op, bpy.context, Vector((100.0, 100.0, 0.0)), Vector((0, 0, 1)))
    check("3: a click on nothing passes on to selection (no reason)", cand is None and getattr(op, "why", "x") == "")
    src = inspect.getsource(ia._ClickPlace.invoke)
    check("3: the tool click reports the reason and places nothing",
          "self.report({'WARNING'}, self.why)" in src and "return {'CANCELLED'}" in src)

    # --- 4. roof: one state, pitch reset only on a type change -------------------------
    wm = bpy.context.window_manager
    wm.cgml_roof_choice = "3100"
    holder = type("R", (), {"roof_type": "3100", "pitch": 20.0, "from_tool": False})()
    choose = getattr(ra.CGML_OT_roof_add, "choose", None)
    if choose is not None:
        choose(holder, bpy.context)
    check("4: same type again: the pitch stays", choose is not None and holder.pitch == 20.0, str(holder.pitch))
    holder.roof_type = "2100"
    if choose is not None:
        choose(holder, bpy.context)
    check("4: popover type change: default pitch, shared choice updated",
          holder.pitch == ra.DEFAULT_PITCH["2100"] and wm.cgml_roof_choice == "2100",
          f"{holder.pitch} {wm.cgml_roof_choice}")
    tool_holder = type("R", (), {"roof_type": "2100", "pitch": 12.0, "from_tool": True})()
    wm.cgml_roof_choice = "3100"
    if choose is not None:
        choose(tool_holder, bpy.context)
    check("4: T tool uses the shared choice (header change resets the pitch)",
          tool_holder.roof_type == "3100" and tool_holder.pitch == ra.DEFAULT_PITCH.get("3100", 35.0),
          f"{tool_holder.roof_type} {tool_holder.pitch}")
    check("4: invoke no longer resets the pitch unconditionally",
          "self.pitch = DEFAULT_PITCH" not in inspect.getsource(ra.CGML_OT_roof_add.invoke))

    # --- 2. status bar ------------------------------------------------------------------
    calls = []
    if mh is not None:
        real = mh.status
        mh.status = lambda context, text=None: calls.append(text)
        try:
            check("2: Blender-style format", mh.hint(("LMB", "Place"), ("Drag", "Size"), ("Esc/RMB", "Cancel"))
                  == "LMB Place · Drag Size · Esc/RMB Cancel")
            for cls in (fa.CGML_OT_facade_draw, fa.CGML_OT_door_draw, fa.SEMANTIC_OT_stamp):
                src = inspect.getsource(cls.invoke)
                check(f"2: {cls.bl_label} sets the status bar", "modal_hint.status(context, modal_hint.hint(" in src)
            for cls in (fa.CGML_OT_facade_draw, fa.SEMANTIC_OT_stamp):
                calls.clear()
                cls._finish(type("S", (), {"_handle": None})(), bpy.context)
                check(f"2: {cls.bl_label} clears it on exit", calls == [None], str(calls))
                check(f"2: {cls.bl_label} clears it on cancel", hasattr(cls, "cancel"))
            calls.clear()
            res = tools.CGML_OT_surface_brush.modal(object(), bpy.context, Ev('ESC'))
            check("2: Assign Surface Class clears it on Esc", res == {'CANCELLED'} and calls == [None], str(calls))
            check("2: Assign Surface Class sets it",
                  "modal_hint.status(context, modal_hint.hint(" in inspect.getsource(tools.CGML_OT_surface_brush.invoke))

            @mh.clears_status
            def boom(self, context, event):
                raise ValueError("x")
            calls.clear()
            try:
                boom(None, bpy.context, None)
            except ValueError:
                pass
            check("2: an exception clears it too", calls == [None], str(calls))
            for name, fn in (("facade draw", fa.CGML_OT_facade_draw.modal), ("stamp", fa.SEMANTIC_OT_stamp.modal),
                             ("brush", tools.CGML_OT_surface_brush.modal), ("click place", ia._ClickPlace.modal),
                             ("drag on wall", ia.CGML_OT_balcony_draw.modal), ("new building", ba.CGML_OT_building_new.modal)):
                check(f"2: {name} modal is guarded", getattr(fn, "__wrapped__", None) is not None)
        finally:
            mh.status = real

    # --- 5. stamp: one undo step ---------------------------------------------------------
    place_src = inspect.getsource(fa.SEMANTIC_OT_stamp._place_at_mouse)
    check("5: no undo push per stamped copy", "undo_push" not in place_src)
    end = getattr(fa.SEMANTIC_OT_stamp, "_end_session", None)
    res = None
    if end is not None:
        try:
            res = end(type("S", (), {"_placed_count": 3, "bl_label": "CityGML Stamp"})())
        except RuntimeError:
            res = "push failed"
    check("5: Esc ends the session: copies kept, one undo step, no F9 that would drop copies",
          res == {'CANCELLED'} and "undo_push" in inspect.getsource(end), str(res))

    # --- 6. greyed reasons ------------------------------------------------------------------
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = None
    check("6: Edit Facade without a building: greyed, says why",
          tools.step_reason(bpy.context, "edit_facade") == "Select a CityGML building"
          and "Now: Select a CityGML building" in wp.CGML_OT_facade_edit_start.description(bpy.context, None))
    activate(b)
    check("6: From Footprint on a CityGML building: says why",
          "Now: That is already a CityGML object" in ba.CGML_OT_building_from_footprint.description(bpy.context, None))
    edit_face(front, lambda n, c: -n.y)
    check("6: New Building in Edit Mode: says why",
          "Now: Back to Object Mode" in ba.CGML_OT_building_new.description(bpy.context, None))
    bpy.ops.object.mode_set(mode='OBJECT')
    row_src = inspect.getsource(tools.draw_build_row)
    check("6: the row gates all three", all(f'_op_button(grp, context, "{k}"' in row_src
                                            for k in ("new_building", "from_footprint", "edit_facade")))

    # --- 7. names ---------------------------------------------------------------------------
    labels = {"cgml.facade_draw": "Window", "cgml.door_draw": "Door",
              "cgml.semantic_stamp": "Paste", "cgml.roof_add": "Roof",
              "cgml.balcony_add": "Balcony", "cgml.chimney_add": "Chimney",
              "cgml.surface_brush": "Assign Surface Class", "cgml.building_new": "New Building",
              "cgml.building_from_footprint": "From Footprint", "cgml.facade_edit_start": "Edit Facade",
              "cgml.balcony_draw": "Drag on Wall"}
    for idname, want in labels.items():
        mod, name = idname.split(".")
        try:
            got = getattr(getattr(bpy.ops, mod), name).get_rna_type().name
        except (AttributeError, KeyError):
            got = None
        check(f"7: {idname} is called '{want}'", got == want, str(got))
    # B71: the Object Mode stamp tool is "Copy" (it runs no operator); the Edit one pastes
    tool_labels = {t.bl_idname: t.bl_label for t, _s in tools.TOOLS if t is not tools.StampToolObject}
    for tid, op in (("cgml.window_tool", "cgml.facade_draw"), ("cgml.door_tool", "cgml.door_draw"),
                    ("cgml.stamp_tool", "cgml.semantic_stamp"), ("cgml.roof_tool", "cgml.roof_add"),
                    ("cgml.balcony_tool", "cgml.balcony_add"), ("cgml.chimney_tool", "cgml.chimney_add"),
                    ("cgml.surface_brush_tool", "cgml.surface_brush")):
        check(f"7: tool {tool_labels[tid]} = its operator's label", tool_labels[tid] == labels[op])
    door = next(t for t, _s in tools.TOOLS if t.bl_idname == "cgml.door_tool" and t.bl_context_mode == 'EDIT_MESH')
    check("7: the Door tool runs CityGML Door", door.bl_keymap[0][0] == "cgml.door_draw", door.bl_keymap[0][0])
    for t in (tools.BalconyTool, tools.ChimneyTool):
        check(f"7: {t.bl_label} tooltip says BuildingInstallation", "BuildingInstallation" in t.bl_description)

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
