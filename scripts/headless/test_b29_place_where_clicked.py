"""B29: a gallery type does not place a balcony / chimney by itself; the user clicks where it goes.

- B70: the gallery's type buttons only set the Edit Facade tool's type; the tool header says to drag (B73)
- the region fallback (a header / sidebar button) keeps the chosen type
- in Edit Facade the mouse ray finds the wall / roof (candidate_at works on edit-mode meshes)
- the click point is kept: the balcony floor is at the clicked height and position on the
  wall, not the face centre + 3 m; the chimney stands where the roof was clicked
- Corner by click: the wall end nearer the click
- after placing: back in Edit Facade, the new installation active (B33)
- scripted EXEC without a click keeps the B28 face rule (test_b28)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b29_place_where_clicked.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES, activate  # noqa: E402
from test_b28_balcony_chimney_gallery import PanelRec  # noqa: E402


class CtxRec(PanelRec):
    """Also records the operator_context the button runs with."""

    def row(self, *a, **k):
        return CtxRec(self.out, self)

    def column(self, *a, **k):
        return self.row()

    def operator(self, idname, text="", depress=False, **k):
        p = super().operator(idname, text, depress)
        self.out[-1].append(self.__dict__.get("operator_context", ""))
        return p


def gallery(panel_cls):
    out = []
    ia = import_module(f"{panel_cls.__module__.rsplit('.', 2)[0]}.ops.installation_authoring")
    holder = type("H", (), {"layout": CtxRec(out), "TYPES": getattr(ia, panel_cls.TYPES_NAME), "OP": panel_cls.OP,
                            "PREFIX": panel_cls.PREFIX, "HINT": panel_cls.HINT})()  # B70: a type picker
    panel_cls.draw(holder, bpy.context)
    return out


def edit_one_face(obj):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    activate(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    for i, f in enumerate(bm.faces):
        f.select_set(i == 0)
    bmesh.update_edit_mesh(obj.data)


def fake(op_cls, kind):
    return type("F", (), {"kind": kind, "place_from_hit": op_cls.place_from_hit, "WANT": op_cls.WANT,
                          "MISS": op_cls.MISS})()


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")
    import inspect

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m, centre at the origin
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    roof = next(o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface")
    front = min(walls, key=lambda o: min((o.matrix_world @ v.co).y for v in o.data.vertices))
    wall_y = min((front.matrix_world @ v.co).y for v in front.data.vertices)

    edit_one_face(front)
    for panel, n in ((tools.CGML_PT_balcony_gallery, 3), (tools.CGML_PT_chimney_gallery, 5)):  # B80: 5 chimneys
        out = gallery(panel)
        ops = [e[1] for e in out if e[0] == "op"]
        check(f"{panel.bl_label}: type buttons only set the tool's type (B70)",
              len(ops) == n and set(ops) == {"cgml.tool_type_set"}, str(ops))
    out = []
    tools.draw_installation_settings("balcony", bpy.context, CtxRec(out), None)
    check("tool header says to drag on the selected face (B73)",
          any(e[0] == "label" and "Drag on the selected face" in e[1] for e in out), str(out))
    check("region fallback keeps the type",
          "invoke_in_viewport_region(context, self.IDNAME, kind=self.kind)" in inspect.getsource(ia._ClickPlace.invoke))

    # a click at x=2.5, z=1.7 on the front wall (ray from the front, in Edit Facade)
    origin, direction = Vector((2.5, wall_y - 20, 1.7)), Vector((0, 1, 0))
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_balcony_add, 'PROJECTING'), bpy.context, origin, direction)
    check("Edit Mode: the mouse ray finds the wall", cand is not None and cand["host"] == b.name, str(cand))
    known = set(bpy.data.objects.keys())  # the wall stays active (B97)
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    bal = next(o for o in bpy.data.objects if o.name not in known)
    zs = [(bal.matrix_world @ v.co).z for v in bal.data.vertices]
    xs = [(bal.matrix_world @ v.co).x for v in bal.data.vertices]
    check("balcony where clicked: floor at 1.7 m, centred at x = 2.5",
          res == {'FINISHED'} and abs(min(zs) - (1.7 - 0.2)) < 1e-3 and abs((min(xs) + max(xs)) / 2 - 2.5) < 1e-3,
          f"z {min(zs):.3f}, x {(min(xs) + max(xs)) / 2:.3f}")
    check("after placing: back in Edit Facade (B33), the wall face still selected (B97)",
          bpy.context.mode == 'EDIT_MESH' and bal.get("cgml_semantic_type") == "Balcony"
          and bpy.context.view_layer.objects.active == front and front.data.total_face_sel == 1)

    edit_one_face(front)
    origin = Vector((4.0, wall_y - 20, 4.0))
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_balcony_add, 'CORNER'), bpy.context, origin, direction)
    check("Corner by click: the nearer wall end", cand is not None and cand.get("corner_side") in ('END', 'START')
          and abs(abs(cand["point"][0]) - 5.0) < 1e-3, str(cand))

    edit_one_face(roof)
    rz = max((roof.matrix_world @ v.co).z for v in roof.data.vertices)
    ry = sum((roof.matrix_world @ v.co).y for v in roof.data.vertices) / len(roof.data.vertices)
    origin, down = Vector((-3.0, ry, rz + 20)), Vector((0, 0, -1))
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_chimney_add, 'ROUND'), bpy.context, origin, down)
    check("Edit Mode: the mouse ray finds the roof", cand is not None, str(cand))
    if cand is not None:
        known = set(bpy.data.objects.keys())  # the wall stays active (B97)
        res = bpy.ops.cgml.chimney_add('EXEC_DEFAULT', kind='ROUND', **cand)
        ch = next(o for o in bpy.data.objects if o.name not in known)
        xs = [(ch.matrix_world @ v.co).x for v in ch.data.vertices]
        check("chimney where clicked (x = -3)", res == {'FINISHED'} and abs((min(xs) + max(xs)) / 2 + 3.0) < 1e-3,
              f"{(min(xs) + max(xs)) / 2:.3f}")

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":  # B36 imports edit_one_face
    main()
