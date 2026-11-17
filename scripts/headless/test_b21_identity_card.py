"""B21: "2 · Edit" is an identity card; CityGML right-click actions in one submenu.

- card: class + name, gml_id, LoD + source (file / drawn here), parts and
  surfaces/openings summary; a child shows "Selected ..." and "Part of ..."
- buttons by context (at most two): building -> Check Openings (B24: Edit Facade in the row);
  a part (balcony installation) -> Detach; a window -> none; two selected ->
  "Make Part of ..." menu; Edit Mode -> hint only (B24: Done in the row)
- no Add Window / Balcony / Chimney / roof gallery / Edit-Mode surface type box
- right-click: one "CityGML" entry (only for CityGML objects); its submenu
  holds the window actions (array, stamp source) and Join / Assign

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b21_identity_card.py -- <buildings_dir>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []
GONE = {"cgml.balcony_add", "cgml.chimney_add", "cgml.roof_add", "cgml3_modeltyper.manual_assign",
        "cgml.opening_array", "cgml.set_semantic_stamp_source", "cgml.align_objects", "cgml.delete_feature"}


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Rec:
    def __init__(self, out):
        object.__setattr__(self, "out", out)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **k):
        return Rec(self.out)

    row = column = box = split = _child

    def operator(self, idname, text="", **k):
        self.out.append(("op", idname, text))
        return type("P", (), {})()

    def menu(self, idname, text="", **k):
        self.out.append(("menu", idname, text))

    def label(self, text="", **k):
        self.out.append(("label", text))

    def prop(self, data, name, **k):
        self.out.append(("prop", name))

    def __getattr__(self, name):
        return lambda *a, **k: None


class FakePanel:
    def __init__(self, cls, layout):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "layout", layout)

    def __getattr__(self, name):
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


def card():
    out = []
    cls = bpy.types.CGML3_PT_Edit
    cls.draw(FakePanel(cls, Rec(out)), bpy.context)
    return out


CREATE = {"cgml.building_new", "cgml.building_from_footprint"}  # the Create box is always there


def ops(out):
    return [o[1] for o in out if o[0] in ("op", "menu") and o[1] not in CREATE]


def labels(out):
    return [o[1] for o in out if o[0] == "label"]


def select(*objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[-1] if objs else None


def roots():
    return [o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "Building" and o.parent is None]


def context_menu():
    out = []
    from city_modeler.ui import workflow_panels as wp
    wp.draw_object_context_menu(type("M", (), {"layout": Rec(out)})(), bpy.context)
    sub = []
    if out:
        wp.CGML_MT_object_context.draw(type("M", (), {"layout": Rec(sub)})(), bpy.context)
    return ops(out), ops(sub)


def main():
    buildings = sys.argv[sys.argv.index("--") + 1]
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)

    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(buildings, "buildings_lod3_citygml_2_0.gml"))
    root = roots()[0]
    select(root)
    out = card()
    lab = labels(out)
    check("card: class and name", any(t == f"Building: {root.name}" for t in lab), str(lab[:4]))
    check("card: gml_id", any(t.startswith("ID: ") and str(root.get("gml_id")) in t for t in lab))
    check("card: LoD and source", any("LoD3" in t and "from file" in t for t in lab), str(lab))
    # B24: Edit Facade and Create moved to the City Modeler row; the card keeps Check Openings
    check("building: Check Openings, nothing made here",
          ops(out)[:1] == ["cgml.validate_building_openings"]
          and not (GONE & set(ops(out))), str(ops(out)))
    check("row hint", any("City Modeler row" in t for t in lab))
    m, sub = context_menu()
    check("right-click: one CityGML entry", m == ["CGML_MT_object_context"], str(m))

    wall = next(o for o in root.children_recursive if o.get("cgml3_feature") == "WallSurface")
    select(wall)
    lab = labels(card())
    check("child: Selected + Part of", any(t.startswith("Selected: WallSurface") for t in lab)
          and f"Part of: {root.name}" in lab, str(lab))

    # a balcony = BuildingInstallation part
    _o = wall
    mw = wall.matrix_world
    p = max(wall.data.polygons, key=lambda q: q.area)
    n = (mw.to_3x3() @ p.normal).normalized()
    hn = Vector((n.x, n.y, 0)).normalized()
    c = mw @ p.center
    select(root)
    bpy.ops.cgml.balcony_add(host=root.name, point=c[:], normal=hn[:])
    bal = next((o for o in root.children_recursive if o.get("cgml_semantic_type") == "Balcony"), None)
    check("balcony placed for the part case", bal is not None)
    if bal is not None:
        select(bal)
        out = card()
        check("part: Detach only", ops(out) == ["cgml3.detach_object_part"], str(ops(out)))

    # window (drawn) -> no buttons; right-click submenu has its actions
    select(root)
    bpy.ops.cgml.facade_edit_start()
    wp = max(wall.data.polygons, key=lambda q: q.area)
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=wall.name, face_index=wp.index,
                             position_uv=(0.5, 0.6), width=0.8, height=0.8)
    out = card()
    check("Edit Mode: row hint, no buttons (Done is in the City Modeler row)",
          ops(out) == [] and any("City Modeler row" in t for t in labels(out)), str(out))
    bpy.ops.object.mode_set(mode='OBJECT')
    win = next((o for o in bpy.data.objects if o.get("cgml3_feature") in ("Window", "WindowSurface")), None)
    check("window drawn", win is not None)
    if win is not None:
        select(win)
        out = card()
        check("window: no buttons in the card", not ops(out), str(ops(out)))
        m, sub = context_menu()
        check("window right-click: CityGML submenu with array + stamp source",
              m == ["CGML_MT_object_context"] and {"cgml.opening_array", "cgml.set_semantic_stamp_source"} <= set(sub),
              str(sub))

    select(wall, root)
    out = card()
    check("two selected: Make Part of ... menu", ("CGML3_MT_assign_object_part_context" in ops(out)), str(ops(out)))
    m, sub = context_menu()
    check("two selected: Assign submenu inside CityGML", "CGML3_MT_assign_object_part_context" in sub, str(sub))

    plain = bpy.data.objects.new("plain", bpy.data.meshes.new("plain"))
    bpy.context.scene.collection.objects.link(plain)
    select(plain)
    check("plain mesh: Make CityGML", "cgml.make_citygml" in ops(card()))
    check("plain mesh: CityGML entry offers Join", context_menu()[0] == ["CGML_MT_object_context"])
    select()
    check("nothing selected: no CityGML right-click entry", context_menu()[0] == [])

    select()
    bpy.ops.cgml.building_new(points=footprint())
    lab = labels(card())
    check("new building: from drawn here, ID shown", any("from drawn here" in t for t in lab)
          and any(t.startswith("ID: ") for t in lab), str(lab))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
