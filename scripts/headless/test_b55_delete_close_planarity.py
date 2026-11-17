"""B55: deleting a window/door closes the wall again; Check / Fix Planarity.

Delete (X / Delete -> cgml.guarded_delete, experimental flag off):
- authored window and floating door: the wall's interior-ring face goes with it, the other
  opening's hole stays, lod3Solid comes back
- ground-notch door: the wall's exterior ring is a rectangle again (5 points) and the two
  corners B54 welded into the GroundSurface are dissolved (5 points); lod3Solid, XSD valid
- imported LoD3 window (Railway scene, CityGML 3.0, link-less ring): exactly its own wall hole
  is removed, the export runs
Planarity:
- a building corner pushed 8 cm off the front wall's plane: Check finds the polygons, Fix
  flattens them (0 left), the shell stays closed (lod3Solid / lod2Solid still written)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b55_delete_close_planarity.py -- <out_dir>
"""
import os
import re
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402
from test_b49_opening_filling import element_blocks, front_wall, rings  # noqa: E402

RAILWAY3 = os.path.join(HERE, "..", "..", "test_files", "Building", "Railway_Scene_LoD3_CityGML3_Building_only.gml")


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)


def new_building():
    clear()
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8, 9 m
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    return b


def draw(b, op, **kw):
    edit_one_face(front_wall(b))
    r = getattr(bpy.ops.cgml, op)('EXEC_DEFAULT', object_name=front_wall(b).name, face_index=0, **kw)
    bpy.ops.object.mode_set(mode='OBJECT')
    return r


def delete(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return bpy.ops.cgml.guarded_delete()


def export(b, path, v="3.0"):
    bpy.ops.object.select_all(action='DESELECT')
    res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=v)
    text = open(path, encoding="utf-8").read()
    wall = [blk for gid, blk in element_blocks(text, "WallSurface") if gid == str(front_wall(b).get("gml_id"))][0]
    own = wall.split("<con:fillingSurface")[0].split("<bldg:opening")[0]
    ground = re.search(r"<(?:con|bldg):GroundSurface.*?</(?:con|bldg):GroundSurface>", text, re.S).group(0)
    return res, text, own, ground


def ring_faces(wall):
    return [f.index for f in wall.data.polygons
            if f.material_index < len(wall.data.materials) and (wall.data.materials[f.material_index] or {}).get("Interior")]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b55_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    wg = import_module(f"{addon}.shared.writer_geometry")
    flag = import_module(f"{addon}.shared.authoring_core.blender.flag")
    check("experimental authoring core is off (default path)", not flag.is_enabled())

    # --- authored openings ---
    b = new_building()
    w = front_wall(b)
    zs = [(w.matrix_world @ v.co).z for v in w.data.vertices]
    hh = max(zs) - min(zs)
    draw(b, "facade_draw", semantic_type='Window', position_uv=(0.2, 1.5 / hh), width=1.2, height=1.4)
    draw(b, "door_draw", position_uv=(0.8, 4.25 / hh), width=1.0, height=2.1)
    draw(b, "door_draw", position_uv=(0.5, 1.05 / hh), width=1.0, height=2.1)
    feats = {o.name: o for o in bpy.data.objects}
    win = next(o for o in feats.values() if o.get("cgml3_feature") == "WindowSurface")
    fdoor = next(o for o in feats.values() if o.get("cgml3_feature") == "DoorSurface"
                 and o.get("cgml_authoring_topology") != "ground_notch")
    gdoor = next(o for o in feats.values() if o.get("cgml_authoring_topology") == "ground_notch")

    check("delete window", delete(win) == {'FINISHED'})
    _r, text, own, _g = export(b, os.path.join(out, "b55_no_window.gml"))
    check("window deleted: one hole left (the door's)", own.count("<gml:interior>") == 1, str(own.count("<gml:interior>")))
    check("window deleted: lod3Solid back", "lod3Solid" in text)
    check("delete floating door", delete(fdoor) == {'FINISHED'})
    _r, text, own, _g = export(b, os.path.join(out, "b55_no_fdoor.gml"))
    check("floating door deleted: no hole left", own.count("<gml:interior>") == 0, str(own.count("<gml:interior>")))
    check("delete ground door", delete(gdoor) == {'FINISHED'})
    for v in ("3.0", "2.0"):
        p = os.path.join(out, f"b55_no_doors_v{v[0]}.gml")
        res, text, own, ground = export(b, p, v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        check(f"{v}: wall exterior is a rectangle again", len(rings(own)[0]) == 5, str(len(rings(own)[0])))
        check(f"{v}: GroundSurface back to 4 corners", len(rings(ground)[0]) == 5, str(len(rings(ground)[0])))
        check(f"{v}: lod solid written", re.search(r"lod[23]Solid", text) is not None)
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    # --- imported LoD3 window (link-less ring) ---
    clear()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.abspath(RAILWAY3))
    cand = [o for o in bpy.data.objects if str(o.get("cgml3_feature")) in ("Window", "WindowSurface")
            and o.parent is not None and o.parent.type == 'MESH' and ring_faces(o.parent)]
    check("imported: a window with a wall hole exists", bool(cand), str(len(cand)))
    if cand:
        iw = cand[0]
        wall = iw.parent
        before = len(ring_faces(wall))
        check("imported: delete window", delete(iw) == {'FINISHED'})
        check("imported: exactly one wall hole removed", len(ring_faces(wall)) == before - 1,
              f"{before} -> {len(ring_faces(wall))}")
        bpy.ops.object.select_all(action='DESELECT')
        p = os.path.join(out, "b55_railway.gml")
        check("imported: export runs", bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0") == {'FINISHED'})

    # --- planarity ---
    b = new_building()
    w = front_wall(b)
    mw = w.matrix_world
    corner = max((mw @ v.co for v in w.data.vertices), key=lambda p: (p.z, p.x))
    shift = Vector((0.0, -0.08, 0.0))  # ~2 cm off the best-fit plane of a 4-corner face
    for o in b.children_recursive:  # move that corner in every surface sharing it (closed shell kept)
        if o.type == 'MESH':
            for v in o.data.vertices:
                if ((o.matrix_world @ v.co) - corner).length < 1e-5:
                    v.co = o.matrix_world.inverted() @ (corner + shift)
            o.data.update()
    bpy.ops.object.select_all(action='DESELECT')
    b.select_set(True)
    bpy.context.view_layer.objects.active = b
    pl = import_module(f"{addon}.shared.planarity")
    scope = [o for o in (b, *b.children_recursive) if o.type == 'MESH' and o.get("cgml3_feature")]
    n_bad = sum(len(pl.non_planar_faces(o)) for o in scope)
    check("planarity: the pushed corner makes polygons non-planar", n_bad >= 1, str(n_bad))
    check("planarity: Check runs", bpy.ops.cgml.check_planarity() == {'FINISHED'})
    b.select_set(True)
    check("planarity: Fix runs", bpy.ops.cgml.fix_planarity() == {'FINISHED'})
    check("planarity: nothing left after Fix", sum(len(pl.non_planar_faces(o)) for o in scope) == 0)
    p = os.path.join(out, "b55_planarity.gml")
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    text = open(p, encoding="utf-8").read()
    solid = re.findall(r"<(\w+:)?lod(\d)Solid[^>]*>(.*?)</\1?lod\2Solid>", text, re.S)
    solid = [(lod, body) for _ns, lod, body in solid]
    check("planarity: solid still written after Fix", bool(solid))
    if solid:
        members = re.findall(r'xlink:href="#([^"]+)"', solid[0][1])
        polys = dict(element_blocks(text, "Polygon"))
        check("planarity: shell still closed",
              wg.shell_is_closed([r for m in members if m in polys for r in rings(polys[m])]))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
