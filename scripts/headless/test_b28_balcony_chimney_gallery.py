"""B28: Balcony / Chimney galleries on the face picked in Edit Facade, drag a balcony on the wall.

- galleries: Projecting / French / Corner and the B80 chimney types, each a thumbnail
  (loaded) + a named button that sets the Edit Facade tool's type (B70: no separate Drag on Wall,
  the tool drags; a door / window face is always a click, fitted)
- Edit Mode, one wall face selected: balcony_add places on that face (floor 3 m above
  the face bottom, centred), back in Object Mode, one BuildingInstallation
- Corner: one L slab (B91) + 4 flat railing faces (B101) at the right end of the wall, reaching past it
- on a door / window face: fitted to the opening (bottom, width); Corner refused there
- roof face: chimney_add Brick / Steel pipe (16-sided prism) / Double flue (block + two pots); B80 five types
- a wall face is not a roof: chimney refused with the reason
- drag_to_balcony: the dragged rectangle -> width, floor (bottom), railing (height);
  clipped to the face; Corner takes the nearer wall end
- export (2.0, repo LoD3 fixture with windows/doors) with the new installations: XSD valid, BuildingInstallation count up
- Object Mode scripted / click use still works (B08 / B15 / B19 tests)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b28_balcony_chimney_gallery.py -- <out_dir>
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from building_harness import check, count, export_checked, finish  # noqa: E402
from test_b24_header_tool_strip import Rec, activate  # noqa: E402

LOD3 = os.path.join(os.path.dirname(os.path.dirname(HERE)), "test_files", "Building",
                    "Railway_Scene_LoD3_CityGML2_Building_only.gml")  # 15 windows, 6 doors


class PanelRec(Rec):
    def row(self, *a, **k):
        return PanelRec(self.out, self)

    def grid_flow(self, *a, **k):
        return self.row()

    def template_icon(self, icon_value=0, scale=1.0, **k):
        self.out.append(("icon", icon_value, self._on(), False, ""))

    def operator(self, idname, text="", depress=False, **k):
        entry = ["op", idname, self._on(), depress, "", text]
        self.out.append(entry)

        class P:
            def __setattr__(s, name, value):
                if name == "kind":
                    entry[4] = value
                object.__setattr__(s, name, value)
        return P()


def gallery(panel_cls):
    """Draw a gallery panel into the recorder (a stand-in self: layout + the class settings)."""
    out = []
    ia = import_module(f"{panel_cls.__module__.rsplit('.', 2)[0]}.ops.installation_authoring")
    holder = type("H", (), {"layout": PanelRec(out), "TYPES": getattr(ia, panel_cls.TYPES_NAME), "OP": panel_cls.OP,
                            "PREFIX": panel_cls.PREFIX, "HINT": panel_cls.HINT})()  # B70: a type picker
    panel_cls.draw(holder, bpy.context)
    return out


def edit_face(obj, pick):
    """Edit Mode on ``obj`` with the one face for which ``pick(world_normal, centre)`` is best."""
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    activate(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    mw = obj.matrix_world
    best = max(bm.faces, key=lambda f: pick((mw.to_3x3() @ f.normal).normalized(), mw @ f.calc_center_median()))
    for f in bm.faces:
        f.select_set(f is best)
    bmesh.update_edit_mesh(obj.data)
    return [mw @ v.co for v in best.verts]


def click(op_cls, kind, origin, direction):
    """B34: the click's candidate (host / point / normal) for a ray, as the modal computes it."""
    f = type("F", (), {"kind": kind, "place_from_hit": op_cls.place_from_hit, "WANT": op_cls.WANT,
                       "MISS": op_cls.MISS})()
    return op_cls.candidate_at(f, bpy.context, Vector(origin), Vector(direction))


def installations(root):
    return [o for o in root.children_recursive if o.get("cgml3_feature") == "BuildingInstallation"]


def zrange(obj):
    zs = [(obj.matrix_world @ v.co).z for v in obj.data.vertices]
    return min(zs), max(zs)


def main():
    out_dir = sys.argv[sys.argv.index("--") + 1]
    os.makedirs(out_dir, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    ia = import_module(f"{addon}.ops.installation_authoring")
    gal = import_module(f"{addon}.ui.gallery")

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m at the origin
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    roofs = [o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface"]
    check("gable building", walls and roofs, str(len(walls)))
    front = min(walls, key=lambda o: sum((o.matrix_world @ v.co).y for v in o.data.vertices) / len(o.data.vertices))

    # --- galleries ----------------------------------------------------------------
    keys = [f"balcony_{t[0]}" for t in ia.BALCONY_TYPES] + [f"chimney_{t[0]}" for t in ia.CHIMNEY_TYPES]
    check("all thumbnails loaded", all(k in gal._previews for k in keys), str([k for k in keys if k not in gal._previews]))
    check("types", [t[1] for t in ia.BALCONY_TYPES] == ["Projecting", "French", "Corner"]
          and [t[1] for t in ia.CHIMNEY_TYPES] == ["Brick", "Cover plate", "Rain hood", "Double flue", "Steel pipe"])  # B80
    edit_face(front, lambda n, c: -n.y)
    out = gallery(tools.CGML_PT_balcony_gallery)
    places = [(e[5], e[4]) for e in out if e[0] == "op" and e[1] == "cgml.tool_type_set"]
    drags = [e for e in out if e[0] == "op" and e[1] == "cgml.balcony_draw"]
    check("balcony gallery: three named types", places == [("Projecting", "PROJECTING"), ("French", "FRENCH"),
                                                             ("Corner", "CORNER")], str(places))
    check("balcony gallery: thumbnails", sum(1 for e in out if e[0] == "icon") == 3)
    check("B70: no separate Drag on Wall (the tool drags)", not drags, str(drags))
    out = gallery(tools.CGML_PT_chimney_gallery)
    check("chimney gallery: five named types (B80)",
          [e[5] for e in out if e[0] == "op" and e[1] == "cgml.tool_type_set"] == ["Brick", "Cover plate", "Rain hood", "Double flue", "Steel pipe"])

    # --- balcony on the selected wall face -------------------------------------------
    pts = edit_face(front, lambda n, c: -n.y)
    zmin = min(p.z for p in pts)
    cx, fy = sum(p.x for p in pts) / len(pts), min(p.y for p in pts)
    cand = click(ia.CGML_OT_balcony_add, 'PROJECTING', (cx, fy - 20, zmin + 3.0), (0, 1, 0))  # B34: a click
    known = set(bpy.data.objects.keys())  # the wall stays active (B97)
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    bal = next(o for o in bpy.data.objects if o.name not in known)
    check("Projecting on the face: placed, back in Edit Facade (B33)", res == {'FINISHED'} and bpy.context.mode == 'EDIT_MESH'
          and bal.get("cgml_semantic_type") == "Balcony" and bal.parent is not None, str(res))
    lo, _hi = zrange(bal)
    check("floor at the click, 3 m above the face bottom", abs(lo - (zmin + 3.0 - 0.2)) < 1e-3, f"{lo} vs {zmin}")
    xs = [(bal.matrix_world @ v.co).x for v in bal.data.vertices]
    check("centred on the wall", abs((min(xs) + max(xs)) / 2 - sum(p.x for p in pts) / len(pts)) < 1e-3)

    pts = edit_face(front, lambda n, c: -n.y)
    cand = click(ia.CGML_OT_balcony_add, 'CORNER', (max(p.x for p in pts) - 1.0, fy - 20, zmin + 3.0), (0, 1, 0))
    known = set(bpy.data.objects.keys())  # the wall stays active (B97)
    res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='CORNER', **cand)
    cor = next(o for o in bpy.data.objects if o.name not in known)
    xs = [(cor.matrix_world @ v.co).x for v in cor.data.vertices]
    check("Corner: one L slab (8 faces, B91) + 4 flat railing faces (B101)",
          res == {'FINISHED'} and len(cor.data.polygons) == 8 + 4,
          str(len(cor.data.polygons)))
    face_x = [p.x for p in pts]
    check("Corner wraps past one end of the wall", max(xs) > max(face_x) + 1.0 or min(xs) < min(face_x) - 1.0,
          f"{min(xs)}..{max(xs)} vs {min(face_x)}..{max(face_x)}")

    # --- chimneys on the roof face ---------------------------------------------------
    n_before = len(installations(b))
    for kind, faces in (("SQUARE", 6), ("ROUND", 18), ("WIDE", 16)):  # B80: Double flue = block + 2 pots (B102: open bottoms)
        rpts = edit_face(roofs[0], lambda n, c: n.z)
        rc = sum(rpts, Vector()) / len(rpts)
        cand = click(ia.CGML_OT_chimney_add, kind, (rc.x + {"SQUARE": -3.0, "ROUND": 0.0, "WIDE": 3.0}[kind], rc.y, rc.z + 30),
                     (0, 0, -1))
        known = set(bpy.data.objects.keys())  # the wall stays active (B97)
        res = bpy.ops.cgml.chimney_add('EXEC_DEFAULT', kind=kind, **cand)
        ch = next(o for o in bpy.data.objects if o.name not in known)
        check(f"{kind} chimney on the roof face", res == {'FINISHED'} and ch.get("cgml_semantic_type") == "Chimney"
              and len(ch.data.polygons) == faces, f"{res} {len(ch.data.polygons)}")
    check("three chimneys added", len(installations(b)) == n_before + 3)
    edit_face(front, lambda n, c: -n.y)
    reason = tools.step_reason(bpy.context, "chimney")
    check("wall face: no chimney, says why", reason == "Select a roof face", reason)
    bpy.ops.object.mode_set(mode='OBJECT')

    # --- drag maths --------------------------------------------------------------------
    cand = {"host": b.name, "normal": (0, -1, 0), "opening": False, "lo": -5.0, "hi": 5.0, "zmin": 0.0,
            "zmax": 9.0, "plane": (0, -4, 4.5)}
    h = Vector((1, 0, 0))  # (-n.y, n.x)
    args = ia.drag_to_balcony(cand, (-1, -4, 3), (2, -4, 4.1), 'PROJECTING')
    check("drag: width, floor, railing", abs(args["width"] - 3) < 1e-6 and abs(args["point"][2] - 3) < 1e-6
          and abs(args["railing"] - 1.1) < 1e-6 and abs(Vector(args["point"]).dot(h) - 0.5) < 1e-6, str(args))
    args = ia.drag_to_balcony(cand, (3, -4, 3), (9, -4, 3.1), 'PROJECTING')
    check("drag clipped to the face, flat drag = default railing",
          abs(args["width"] - 2) < 1e-6 and args["railing"] == 1.0, str(args))
    args = ia.drag_to_balcony(cand, (-4.5, -4, 3), (-2, -4, 4), 'CORNER')
    check("drag Corner: nearer end", args["corner_side"] == 'START' and abs(args["point"][0] + 5) < 1e-6, str(args))
    args = ia.drag_to_chimney({"host": b.name, "normal": (0, 0, 1)}, (1, 1, 9), (1.5, 2.0, 9), 'SQUARE')
    check("B80 chimney drag: the rectangle, centred", abs(args["size"] - 0.5) < 1e-6 and abs(args["depth"] - 1.0) < 1e-6
          and Vector(args["point"]) == Vector((1.25, 1.5, 9)), str(args))
    args = ia.drag_to_chimney({"host": b.name, "normal": (0, 0, 1)}, (1, 1, 9), (1.05, 1, 9), 'SQUARE')
    check("B70 chimney drag: at least 0.2 m", abs(args["size"] - 0.2) < 1e-6 and abs(args["depth"] - 0.2) < 1e-6,
          str(args))

    # --- opening face: fit, no corner --------------------------------------------------
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml3.import_gml_file(filepath=LOD3)
    win = next((o for o in bpy.data.objects if o.type == 'MESH' and o.get("cgml3_feature") in ("Window", "Door", "WindowSurface", "DoorSurface")),
               None)
    check("LoD3 file has a window/door", win is not None,
          str(sorted({str(o.get("cgml3_feature")) for o in bpy.data.objects})))
    if win is not None:
        pts = edit_face(win, lambda n, c: abs(n.z) < 0.5)
        wc = sum(pts, Vector()) / len(pts)
        wn = (pts[1] - pts[0]).cross(pts[2] - pts[1]).normalized()

        def win_click(kind):
            dg = bpy.context.evaluated_depsgraph_get()
            for s in (1, -1):  # the side whose ray hits the window itself first
                hit = bpy.context.scene.ray_cast(dg, wc + wn * (0.5 * s), -wn * s)
                if hit[0] and hit[4].original == win:
                    return click(ia.CGML_OT_balcony_add, kind, wc + wn * (0.5 * s), -wn * s)
            print("window not hit from either side", win.name)
            return None
        check("Corner on an opening refused", win_click('CORNER') is None and win_click('FRENCH') is not None)
        f = type("F", (), {"_press": Vector((0, 0)), "_cand": {"opening": True}})()
        ev = type("E", (), {"mouse_region_x": 200, "mouse_region_y": 0})()
        check("B73 opening face: a drag fits, it is not a click", not ia._FaceDraw._is_click(f, ev))
        fc = win_click('FRENCH')
        known = set(bpy.data.objects.keys())  # the wall stays active (B97)
        res = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='FRENCH', **(fc or {}))
        fr = next(o for o in bpy.data.objects if o.name not in known)
        lo, _hi = zrange(fr)
        bottom = min((win.matrix_world @ v.co).z for v in win.data.vertices)  # a click fits the whole opening
        check("French fitted to the opening bottom", res == {'FINISHED'} and abs(lo - bottom) < 1e-3,
              f"{lo} vs {bottom}")
        root = fr.parent
        bpy.ops.object.mode_set(mode='OBJECT')
        for o in bpy.context.selected_objects:
            o.select_set(False)
        text = export_checked(os.path.join(out_dir, "b28_v20.gml"), "2.0")
        check("export has the new balcony", count(text, "bldg:BuildingInstallation") >= len(installations(root)),
              f"{count(text, 'bldg:BuildingInstallation')} vs {len(installations(root))}")

    finish("B28 balcony / chimney galleries")


if __name__ == "__main__":  # B29 imports the recorder
    main()
