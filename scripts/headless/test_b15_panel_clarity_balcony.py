"""B15: sidebar clarity + balcony / chimney placed by a click.

- header says "City Modeler" and shows the colour legend
- preview block: labelled picking toggle, Focus on Picked (button, F key,
  right-click menu) that centres the view on the picked buildings
- colours by function: Show / Add / Open / Export blue (depress), Unshow /
  Close / replace red (alert)
- roof gallery only on a building made here or one without a roof
- balcony: a click on a wall puts its floor at the click, on the outside;
  a click on a door fits it (width, floor at the door bottom); French type is
  a railing only; chimney: a click on a roof, rising above the ridge; misses
  (sky, wrong surface) place nothing

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b15_panel_clarity_balcony.py -- <buildings_dir>
"""
import os
import sys
import types
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []
REPO = Path(__file__).resolve().parents[2]
LOD1 = REPO / "test_files" / "Building" / "CitySmith" / "CS1_lod1.gml"


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


class Recorder:
    """Layout stand-in: records (idname, text, blue, red) per button and every label."""

    def __init__(self, out, labels, alert=False):
        object.__setattr__(self, "out", out)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "alert", alert)

    def __setattr__(self, name, value):
        if name == "alert":
            object.__setattr__(self, name, value)

    def _child(self, *a, **kw):
        return Recorder(self.out, self.labels, self.alert)

    row = column = box = _child

    def operator(self, idname, text="", depress=False, **kw):
        self.out.append((idname, text, bool(depress), bool(self.alert)))
        return types.SimpleNamespace()

    def label(self, text="", **kw):
        self.labels.append(text)

    def __getattr__(self, name):
        return lambda *a, **kw: None


class FakePanel:
    def __init__(self, cls, layout):
        self.layout = layout
        self._cls = cls

    def __getattr__(self, name):
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


def draw(cls_name):
    out, labels = [], []
    cls = getattr(bpy.types, cls_name)
    cls.draw(FakePanel(cls, Recorder(out, labels)), bpy.context)
    return out, labels


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    return addon


def mod(addon, name):
    return sys.modules[f"{addon}.{name}"]


def first_root():
    return next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building" and o.parent is None)


def by_text(out, part):
    return [b for b in out if part in b[1]]


def test_header_and_preview(addon, buildings):
    print("\n[B15] --- header, preview block, Focus ---")
    title = []  # B90: the title sits above Blender's active-tool block, the status strip below it
    mod(addon, "ui.header_panel").draw_title(type("P", (), {"layout": Recorder([], title)})(), bpy.context)
    check(title[:1] == ["City Modeler"], "header says City Modeler", str(title[:1]))
    _out, labels = draw("VCS3DCITYDB_PT_header")
    check(any("Blue" in t and "Red" in t for t in labels), "header shows the colour legend")

    bm = mod(addon, "ops.browse_gml")
    path = os.path.join(buildings, "buildings_lod2_citygml_2_0.gml")
    bpy.ops.cgml3.browse_gml_file(filepath=path, use_cache=False)
    s = bm.get_session(path)
    bm.set_selection(s, [1])
    out, _labels = draw("CGML3_PT_Main")
    pick = [b for b in out if b[0] == "cgml3.browse_pick"]
    check(pick and pick[0][1].startswith("Picking: "), "picking toggle says what it does", str(pick))
    check(any(b[0] == "cgml3.browse_frame_picked" for b in out), "preview block has Focus on Picked")
    show = [b for b in out if b[0] == "cgml3.browse_load"]
    check(show and all(b[2] and not b[3] for b in show), "Show buttons are blue", str(show))
    red = [b for b in out if b[0] in ("cgml3.browse_unload", "cgml3.browse_close")]
    check(red and all(b[3] and not b[2] for b in red), "Unshow / Close are red", str(red))
    check(all(b[2] for b in out if b[0] in ("cgml3.browse_gml_file", "cgml3.import_gml_file")),
          "Open buttons are blue")
    grey = [b for b in out if b[0] in ("cgml3.browse_frame_picked", "cgml3.browse_select")]
    check(grey and not any(b[2] or b[3] for b in grey), "Focus / Pick None stay grey", str(grey))
    check("F" in bm.CGML3_OT_browse_pick.FOCUS_KEYS and "F focus" in bm.PICK_HINT, "F key focuses (header hint)")
    menu = []
    bm.draw_context_menu(types.SimpleNamespace(layout=Recorder(menu, [])), bpy.context)
    check("cgml3.browse_frame_picked" in [b[0] for b in menu], "right-click menu has Focus on Picked")

    space = next(sp for scr in bpy.data.screens for a in scr.areas if a.type == 'VIEW_3D'
                 for sp in a.spaces if sp.type == 'VIEW_3D')
    space.region_3d.view_location = (5000.0, 5000.0, 0.0)
    res = bpy.ops.cgml3.browse_frame_picked()
    e, o = s.entries[1], s.origin
    want = Vector([(e.bbox[k] + e.bbox[k + 3]) / 2 - o[k] for k in range(3)])
    got = Vector(space.region_3d.view_location)
    check(res == {'FINISHED'} and (got - want).length < 0.01, "Focus centres the view on the picked building",
          f"{res} {got[:]} vs {want[:]}")
    size = max(e.bbox[3] - e.bbox[0], e.bbox[4] - e.bbox[1], e.bbox[5] - e.bbox[2], 10.0)
    check(abs(space.region_3d.view_distance - size * 1.6) < 0.01, "Focus zooms to the building's size",
          f"{space.region_3d.view_distance:.1f}")


def test_roof_card(addon, buildings):
    print("\n[B15] --- roof gallery only where it makes sense ---")
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(buildings, "buildings_lod2_citygml_2_0.gml"))
    # B21: the roof gallery, Balcony and Chimney moved to the toolbar (B19); the B15 roof
    # rule lives on in roof_editable(), which the Roof tool checks at the click.
    wp = mod(addon, "ui.workflow_panels")
    out, _l = draw("CGML3_PT_Edit")
    check(not any(b[0] in ("cgml.roof_add", "cgml.balcony_add", "cgml.chimney_add") for b in out),
          "2 · Edit card: no roof / balcony / chimney buttons (toolbar)")
    check(all(b[2] for b in out if b[0] == "cgml.facade_edit_start"), "Edit Facade is blue")
    check(not wp.roof_editable(first_root()), "imported LoD2 building with roofs: roof not editable")
    out, _l = draw("CGML3_PT_Save")
    check(any(b[0] == "cgml3.export_gml_file" and b[2] for b in out), "Export is blue")
    check(any("replace same IDs" in b[1] and b[3] for b in out), "Write to 3DCityDB (replace) is red")

    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=str(LOD1))
    check(wp.roof_editable(first_root()), "LoD1 building without a roof: roof editable")

    fresh()
    bpy.ops.cgml.building_new(points=footprint())
    check(wp.roof_editable(first_root()), "new building (LoD1 block): roof editable")
    bpy.context.view_layer.objects.active = first_root()
    bpy.ops.cgml.roof_add(roof_type="3100")
    bpy.context.view_layer.objects.active = first_root()
    check(wp.roof_editable(first_root()), "building made here with a roof: still editable (change roof)")


def biggest_face(objs, want):
    best = None
    for o in objs:
        for p in o.data.polygons:
            n = (o.matrix_world.to_3x3() @ p.normal).normalized()
            if want(n) and (best is None or p.area > best[2]):
                best = (o, p, p.area, o.matrix_world @ p.center, n)
    return best


def candidate(op_cls, origin, direction, **attrs):
    fake = types.SimpleNamespace(kind='PROJECTING', WANT=op_cls.WANT, MISS=op_cls.MISS, **attrs)
    fake.place_from_hit = types.MethodType(op_cls.place_from_hit, fake)
    return op_cls.candidate_at(fake, bpy.context, Vector(origin), Vector(direction).normalized())


def world_verts(obj):
    return [obj.matrix_world @ v.co for v in obj.data.vertices]


def test_balcony_chimney(addon, buildings):
    print("\n[B15] --- balcony / chimney by click ---")
    fresh()
    ia = mod(addon, "ops.installation_authoring")
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(buildings, "buildings_lod3_citygml_2_0.gml"))
    root = first_root()
    meshes = [o for o in [root, *root.children_recursive] if o.type == 'MESH']
    walls = [o for o in meshes if o.get("cgml3_feature") == "WallSurface"]
    _o, _p, _a, c, n = biggest_face(walls, lambda n: abs(n.z) < 0.2)
    hn = Vector((n.x, n.y, 0)).normalized()
    click = c - Vector((0, 0, 1.0))  # a little under the face centre
    cand = candidate(ia.CGML_OT_balcony_add, click + hn * 30, -hn)
    check(cand is not None and cand["host"] == root.name, "wall click: balcony candidate on this building",
          repr(cand and cand.get("host")))
    if cand:
        check((Vector(cand["point"]) - click).length < 0.05, "wall click: floor point = clicked point",
              f"{(Vector(cand['point']) - click).length:.3f} m")
        check(Vector(cand["normal"]).dot(hn) > 0.99, "wall click: balcony faces the viewer (outside)")
        before = {o.name for o in bpy.data.objects}
        res = bpy.ops.cgml.balcony_add(**cand)
        new = [o for o in bpy.data.objects if o.name not in before]
        check(res == {'FINISHED'} and len(new) == 1 and new[0].parent == root, "balcony added under the building",
              str(res))
        if new:
            vs = world_verts(new[0])
            top = max(v.z for v in vs if (v - click).dot(hn) > 0.5)  # slab + railing tops
            slab_top = sorted({round(v.z, 3) for v in vs})
            check(abs(max(v.z for v in vs) - (click.z + 1.0)) < 0.01, "railing top 1 m above the clicked floor",
                  f"{top:.2f} vs {click.z + 1.0:.2f}")
            check(any(abs(z - round(click.z, 3)) < 0.01 for z in slab_top), "slab top at the clicked height")
            check(min((v - click).dot(hn) for v in vs) > -0.01, "balcony lies outside the wall")
            h = Vector((-hn.y, hn.x, 0))
            mid = sum((v - click).dot(h) for v in vs) / len(vs)
            check(abs(mid) < 0.05, "balcony centred on the click along the wall", f"{mid:.3f} m")
            check(new[0].get("cgml_semantic_type") == "Balcony", "tagged Balcony")

    # a door drawn with Window / Door on the open wall of a fresh copy (the sample has none)
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(buildings, "buildings_lod3_citygml_2_0.gml"))
    root = first_root()
    wall2 = biggest_face([o for o in root.children_recursive if o.get("cgml3_feature") == "WallSurface"],
                         lambda n: abs(n.z) < 0.2)
    wall2_name, wall2_face = wall2[0].name, wall2[1].index  # read before Edit Mode invalidates the polygon
    bpy.context.view_layer.objects.active = root
    bpy.ops.cgml.facade_edit_start()
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Door', object_name=wall2_name,
                             face_index=wall2_face, position_uv=(0.5, 0.2), width=1.0, height=2.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    doors = [o for o in bpy.data.objects if o.type == 'MESH' and o.get("cgml3_feature") in ("Door", "DoorSurface")]
    check(bool(doors), "a door was drawn to click on", str(sorted({str(o.get("cgml3_feature")) for o in bpy.data.objects})))
    if doors:
        d = doors[0]
        dv = world_verts(d)
        dc = sum(dv, Vector()) / len(dv)
        _o, _p, _a, _c, dn = biggest_face([d], lambda n: abs(n.z) < 0.2)
        dhn = Vector((dn.x, dn.y, 0)).normalized()
        dg = bpy.context.evaluated_depsgraph_get()
        # the outside: the side from which the ray meets the door first
        sign = next((sg for sg in (1, -1) if bpy.context.scene.ray_cast(dg, dc + dhn * sg * 30, -dhn * sg)[4]
                     and bpy.context.scene.ray_cast(dg, dc + dhn * sg * 30, -dhn * sg)[4].original == d), 1)
        cand = candidate(ia.CGML_OT_balcony_add, dc + dhn * sign * 30, -dhn * sign)
        dh = Vector((-dhn.y, dhn.x, 0))
        span = max(v.dot(dh) for v in dv) - min(v.dot(dh) for v in dv)
        check(cand is not None and abs(cand.get("width", 0) - (span + 0.8)) < 0.01,
              "door click: balcony width = door width + 0.8 m",
              f"{cand and cand.get('width')} / {span + 0.8:.2f}, sign {sign}, ray hits "
              f"{bpy.context.scene.ray_cast(dg, dc + dhn * sign * 30, -dhn * sign)[4].name}, door {d.name}")
        check(cand is not None and abs(cand["point"][2] - min(v.z for v in dv)) < 0.01,
              "door click: floor at the door bottom")
        fake_fr = types.SimpleNamespace(kind='FRENCH', WANT='WALL', MISS="")
        fake_fr.place_from_hit = types.MethodType(ia.CGML_OT_balcony_add.place_from_hit, fake_fr)
        french = ia.CGML_OT_balcony_add.candidate_at(fake_fr, bpy.context, dc + dhn * sign * 30, -dhn * sign)
        res = bpy.ops.cgml.balcony_add(kind='FRENCH', **french) if french else None
        fr = bpy.context.view_layer.objects.active
        check(res == {'FINISHED'} and len(fr.data.polygons) == 1, "French balcony: one flat railing face (B101), no floor",
              f"{res} {len(fr.data.polygons) if fr else 0}")
        if res == {'FINISHED'}:
            gap = min((v - Vector(french["point"])).dot(Vector(french["normal"])) for v in world_verts(fr))
            check(0.1 < gap < 0.15, "French railing stands just in front of the door", f"{gap:.3f} m")

    roofs = [o for o in root.children_recursive if o.type == 'MESH' and o.get("cgml3_feature") == "RoofSurface"]
    _o, _p, _a, rc, rn = biggest_face(roofs, lambda n: n.z > 0.2)
    cand = candidate(ia.CGML_OT_chimney_add, rc + Vector((0, 0, 50)), (0, 0, -1))
    check(cand is not None and (Vector(cand["point"]).xy - rc.xy).length < 0.05, "roof click: chimney at the click")
    if cand:
        res = bpy.ops.cgml.chimney_add(**cand)
        ch = bpy.context.view_layer.objects.active
        ridge = max(v.z for o in roofs for v in world_verts(o))
        cv = world_verts(ch)
        check(res == {'FINISHED'} and abs(max(v.z for v in cv) - (ridge + 0.8)) < 0.01,
              "chimney rises 0.8 m above the ridge", f"{max(v.z for v in cv):.2f} vs {ridge + 0.8:.2f}")
        cx = sum(v.x for v in cv) / len(cv), sum(v.y for v in cv) / len(cv)
        check((Vector(cx) - rc.xy).length < 0.05, "chimney centred on the click")

    check(candidate(ia.CGML_OT_balcony_add, (0, 0, 5000), (0, 0, 1)) is None, "click into the sky: nothing")
    check(candidate(ia.CGML_OT_balcony_add, rc + Vector((0, 0, 50)), (0, 0, -1)) is None,
          "balcony on a roof click: refused")
    check(candidate(ia.CGML_OT_chimney_add, click + hn * 30, -hn) is None, "chimney on a wall click: refused")


def main():
    buildings = sys.argv[sys.argv.index("--") + 1]
    addon = fresh()
    test_header_and_preview(addon, buildings)
    test_roof_card(addon, buildings)
    test_balcony_chimney(addon, buildings)
    if FAILURES:
        print(f"\n[FAIL] B15 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B15 panel clarity, balcony / chimney by click")


main()
