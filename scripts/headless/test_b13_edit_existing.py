"""B13: an imported building (Open Whole File or Browse -> Show) is editable at once.

For CityGML 2.0 / 3.0, LoD1 / LoD2 / LoD3 files, both ways in:
- the first imported building is the active object (selection unchanged),
  so "2 · Edit" draws the card and the editing tools, not only New Building
- Add Window / Door enters Edit Mode, a window is placed on a wall, Validate runs
- a box behind a shown building does not take the click (pick ray)
- picked but unshown boxes offer "Show ... to Edit" in 2 · Edit

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b13_edit_existing.py -- <buildings_dir>
"""
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
REPO = Path(__file__).resolve().parents[2]
LOD1 = REPO / "test_files" / "Building" / "CitySmith" / "CS1_lod1.gml"  # CityGML 2.0, no 3.0 LoD1 sample
# B21: balcony / chimney moved to the toolbar; the card keeps Edit Facade + Check Openings
EDIT_TOOLS = {"cgml.validate_building_openings"}  # B24: Edit Facade moved to the City Modeler row


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Recorder:
    def __init__(self, ops, labels):
        object.__setattr__(self, "ops", ops)
        object.__setattr__(self, "labels", labels)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **kw):
        return Recorder(self.ops, self.labels)

    row = column = box = _child

    def operator(self, idname, **kw):
        self.ops.append(idname)
        return type("P", (), {})()

    def label(self, text="", **kw):
        self.labels.append(text)

    def __getattr__(self, name):  # prop, template_icon_view, menu_contents, separator ...
        return lambda *a, **kw: None


class FakePanel:
    """Panel instance stand-in (as in test_b02): layout + the class's helper methods."""

    def __init__(self, cls, layout):
        self.layout = layout
        self._cls = cls

    def __getattr__(self, name):
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


def edit_panel():
    ops, labels = [], []
    cls = bpy.types.CGML3_PT_Edit
    cls.draw(FakePanel(cls, Recorder(ops, labels)), bpy.context)
    return set(ops), labels


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    return sys.modules[f"{addon}.ops.browse_gml"]


def wall_face(root):
    """Largest vertical face among the building's walls (or its own mesh at LoD1)."""
    meshes = [o for o in [root, *root.children_recursive] if o.type == 'MESH'
              and o.get("cgml3_feature") in ("WallSurface", "Building")]
    best = None
    for o in meshes:
        for p in o.data.polygons:
            n = o.matrix_world.to_3x3() @ p.normal
            if abs(n.z) < 0.2 and (best is None or p.area > best[2]):
                best = (o.name, p.index, p.area)
    return best


def openings(root):
    return [o for o in root.children_recursive if o.get("cgml3_feature") in ("Window", "WindowSurface")]


def edit_building(label, root):
    check(root is not None and root.get("cgml3_feature") == "Building",
          f"{label}: first building is active", repr(root and root.name))
    if root is None:
        return
    ops, labels = edit_panel()
    check(any(t.startswith("Building: ") for t in labels), f"{label}: 2 · Edit shows the building card", labels[:2])
    check(EDIT_TOOLS <= ops, f"{label}: 2 · Edit shows the editing tools", str(sorted(EDIT_TOOLS - ops)))
    face = wall_face(root)
    check(face is not None, f"{label}: building has a wall face")
    if face is None:
        return
    before = len(openings(root))
    res = bpy.ops.cgml.facade_edit_start()
    check(res == {'FINISHED'} and root.mode == 'EDIT', f"{label}: Add Window / Door enters Edit Mode", str(res))
    res = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=face[0], face_index=face[1],
                                   position_uv=(0.5, 0.5), width=1.0, height=1.2)
    bpy.ops.object.mode_set(mode='OBJECT')
    check(res == {'FINISHED'} and len(openings(root)) == before + 1, f"{label}: a window is added",
          f"{res} {before}->{len(openings(root))}")
    bpy.context.view_layer.objects.active = root
    check(bpy.ops.cgml.validate_building_openings() == {'FINISHED'}, f"{label}: Validate runs")


def whole_file(path):
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=str(path))
    check(not bpy.context.selected_objects, f"{path.name} whole file: selection unchanged (export scope)",
          str(len(bpy.context.selected_objects)))
    edit_building(f"{path.name} whole file", bpy.context.view_layer.objects.active)


def browse_show(path):
    bm = fresh()
    bpy.ops.cgml3.browse_gml_file(filepath=str(path), use_cache=False)
    s = bm.get_session(str(path))
    bm.set_selection(s, [0])
    ops, _labels = edit_panel()
    check("cgml3.browse_load" in ops, f"{path.name} browse: 2 · Edit offers Show for picked boxes", str(sorted(ops)))
    bm.run_show(True)
    root = bpy.context.view_layer.objects.active
    check(root is not None and root.get("cgml3_source_kind") == "file", f"{path.name} browse: shown building tagged")
    edit_building(f"{path.name} browse", root)
    return bm, s


def pick_ray(path):
    """A ray through a shown building towards a box behind it must not pick the box."""
    bm = fresh()
    bpy.ops.cgml3.browse_gml_file(filepath=str(path), use_cache=False)
    s = bm.get_session(str(path))
    near, far = "DEHHALKAJ000179L", "DEHHALKAJ0000zU7"  # far box lies west of the near building
    bm.set_selection(s, [s.id_to_index[near]])
    bm.run_show(True)
    bpy.context.view_layer.update()
    o = s.origin
    nb = s.entries[s.id_to_index[near]].bbox
    # the far footprint lies flat on the ground (B14): aim at one of its triangles through the shown building
    a, ff = s.arrays, s.id_to_index[far]
    f = list(a.face_feature).index(ff)
    mesh = s.proxy().data
    mesh.calc_loop_triangles()
    target = next(t.center for t in mesh.loop_triangles if t.polygon_index == f).copy()
    inside = Vector(((nb[0] + nb[3]) / 2 - o[0], (nb[1] + nb[4]) / 2 - o[1], (nb[2] + nb[5]) / 2 - o[2]))
    ray = (target - inside).normalized()
    start = inside - ray * 200.0
    hit, _loc, _n, _i, obj, _m = bpy.context.scene.ray_cast(bpy.context.evaluated_depsgraph_get(), start, ray)
    check(hit and not obj.get("cgml3_browse_path"), "test ray meets the shown building first", repr(obj and obj.name))
    sess, fi = bm.feature_on_ray(bpy.context, start, ray)
    check(sess is None, "box behind a shown building is not picked", repr(fi))
    sess, fi = bm.feature_on_ray(bpy.context, target + Vector((0.0, 0.0, 100.0)), Vector((0.0, 0.0, -1.0)))
    check(sess is s and fi == ff, "the same box is picked from its open side", repr(fi))


def main():
    buildings = Path(sys.argv[sys.argv.index("--") + 1])
    files = [buildings / f"buildings_lod{lod}_citygml_{v}.gml" for lod in (2, 3) for v in ("2_0", "3_0")]
    for path in [LOD1, *files]:
        print(f"\n[B13] --- {path.name} ---")
        whole_file(path)
        browse_show(path)
    print("\n[B13] --- pick ray ---")
    pick_ray(buildings / "buildings_lod3_citygml_3_0.gml")

    if FAILURES:
        print(f"\n[FAIL] B13 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B13 edit existing buildings")


main()
