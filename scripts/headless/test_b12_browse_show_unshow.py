"""B12: Browse -> see-through boxes in view -> pick -> Show -> Unshow.

A  the view is framed on the boxes as soon as they exist (no empty screen)
B  unshown buildings are a wireframe box + see-through fill, never real geometry
C  Show / Unshow: the gesture behind the S / U keys and the right-click menu,
   Unshow from the building selected in the viewport, sidebar block in 1 · Open

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-exit-code 1 \\
      --python scripts/headless/test_b12_browse_show_unshow.py -- <buildings_dir>
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
FILE = "buildings_lod3_citygml_3_0.gml"


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Recorder:
    """Layout stand-in that records operator ids (menus and panels)."""

    def __init__(self, ops):
        object.__setattr__(self, "ops", ops)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **kw):
        return Recorder(self.ops)

    row = column = box = _child

    def operator(self, idname, **kw):
        self.ops.append(idname)
        return type("P", (), {})()

    def label(self, *a, **kw):
        pass

    def separator(self, *a, **kw):
        pass


def roots():
    return [o for o in bpy.data.objects if o.get("cgml3_feature_id")]


def main():
    buildings = sys.argv[sys.argv.index("--") + 1]
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bm = sys.modules[f"{addon}.ops.browse_gml"]
    path = os.path.join(buildings, FILE)

    print("\n[B12] --- A: boxes are framed at once ---")
    res = bpy.ops.cgml3.browse_gml_file(filepath=path, use_cache=False)
    s = bm.get_session(path)
    check(res == {'FINISHED'} and s is not None and s.complete, "file indexed", str(res))
    check(getattr(s, "framed", "") == "complete", "view framed on the complete box set", repr(getattr(s, "framed", None)))
    secs = s.stats.get("framed_s")
    check(secs is not None and secs <= 1.0, "framed within 1 s of choosing the file", f"{secs}")

    print("\n[B12] --- B: cheap, see-through preview ---")
    p = s.proxy()
    check(p.display_type == 'WIRE', "proxy drawn as wireframe (not a shaded building)", p.display_type)
    n_faces = len(s.arrays.face_feature)
    check(len(p.data.polygons) == n_faces and set(s.arrays.face_feature) == {0, 1, 2},
          "flat faces of the 3 buildings, nothing else", str(len(p.data.polygons)))
    check(not roots(), "browse loads no real geometry by itself", str(len(roots())))
    fill = getattr(bm, "fill_points", None)
    n_fill = len(fill(s)) if fill else 0
    check(n_fill > 0, "see-through fill of the footprints", str(n_fill))

    print("\n[B12] --- C: Show / Unshow ---")
    check({'S', 'RET', 'U'} <= getattr(bm.CGML3_OT_browse_pick, "SHOW_KEYS", set()), "pick mode keys S / Enter / U")
    hint = getattr(bm, "PICK_HINT", "")
    check(all(t in hint for t in ("S / Enter Show", "U Unshow", "right-click", "Picking button")), "header lists the keys", hint)
    src = inspect.getsource(bm.CGML3_OT_browse_pick.modal)
    check("'ESC'" not in src, "B73: Esc does not stop picking (a tool's Esc turned it off)")
    menu_funcs = bpy.types.VIEW3D_MT_object_context_menu._dyn_ui_initialize()
    check(any(getattr(f, "__name__", "") == "draw_context_menu" for f in menu_funcs), "right-click menu hook registered")

    gid = "DEHHALKAJ000179L"
    bm.set_selection(s, [s.id_to_index[gid]])
    ops = []
    bm.draw_context_menu(type("M", (), {"layout": Recorder(ops)})(), bpy.context)
    check(ops[:2] == ["cgml3.browse_load", "cgml3.browse_unload"], "menu offers Show and Unshow", str(ops))
    ops = []
    bpy.types.CGML3_PT_Main.draw(type("Pn", (), {"layout": Recorder(ops)})(), bpy.context)
    check("cgml3.browse_load" in ops and "cgml3.browse_unload" in ops, "1 · Open shows Show / Unshow", str(ops))

    res = bm.run_show(True)  # = S key
    check(res == {'FINISHED'} and [o.get("cgml3_feature_id") for o in roots()] == [gid], "S shows the picked building",
          f"{res} {[o.name for o in roots()]}")
    dg = bpy.context.evaluated_depsgraph_get()
    own = sum(1 for f in s.arrays.face_feature if f in s.loaded)
    check(own and len(p.evaluated_get(dg).data.polygons) == n_faces - own, "its box is hidden")
    check(len(fill(s)) < n_fill, "its box fill is gone")

    bm.set_selection(s, [], 'CLEAR')  # the user now clicks the real building, not the box
    wall = next(o for o in roots()[0].children_recursive if o.get("cgml3_feature") == "WallSurface")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall.select_set(True)
    res = bm.run_show(False)  # = U key
    check(res == {'FINISHED'} and not roots() and not s.loaded, "U unshows the building selected in the viewport",
          f"{res} {len(roots())}")
    check(len(p.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons) == n_faces, "its box is back")
    res = bm.run_show(False)
    check(res == {'CANCELLED'}, "U with nothing shown only warns", str(res))

    if FAILURES:
        print(f"\n[FAIL] B12 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B12 browse Show / Unshow")


main()
