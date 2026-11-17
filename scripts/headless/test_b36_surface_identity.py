"""B36: every authored surface has its own identity.

New Building / Roof gave all walls (and both roof planes) one shared material. A window
stamped its host ids on that material, so every wall carried the same ids: the export
moved the window into an extra WallSurface, the clicked wall got no hole. Surfaces had no
con_surface_id, so their gml_id was "WallSurface::2" (not an NCName) and the writer made
a new random id on every export.

- every surface object has its own material; no two share one
- 2.0 and 3.0: every gml:id in the file is unique; surface ids are the same in two exports
- window + door on the front wall: exactly 4 WallSurfaces + the 2 gables stay 6 boundary
  walls, the front wall (normal -Y) holds the fillings and has 2 interior rings

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b36_surface_identity.py -- <out_dir>
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402

GML_IDS = ("{http://www.opengis.net/gml}id", "{http://www.opengis.net/gml/3.2}id")
NCNAME_BAD = (":", " ")


def export(out, tag):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    paths = {}
    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b36_{tag}_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{tag}: export {v}", res == {'FINISHED'}, str(res))
        paths[v] = p
    return paths


def surfaces(path):
    """[(local tag, gml:id, element)] of the thematic surfaces, document order."""
    out = []
    for el in ET.parse(path).iter():
        tag = el.tag.split("}")[1]
        if tag in ("GroundSurface", "RoofSurface", "WallSurface", "WindowSurface", "DoorSurface", "Window", "Door"):
            out.append((tag, next((el.get(k) for k in GML_IDS if el.get(k)), ""), el))
    return out


def all_ids(path):
    return [i for el in ET.parse(path).iter() for i in (el.get(k) for k in GML_IDS) if i]


def front_wall(path):
    """The WallSurface whose first polygon faces -Y (the clicked front wall)."""
    for tag, gid, el in surfaces(path):
        if tag != "WallSurface":
            continue
        pl = next(e for e in el.iter() if e.tag.endswith("posList"))
        v = list(map(float, pl.text.split()))
        pts = [v[i:i + 3] for i in range(0, len(v), 3)]
        ny = sum((p[2] - q[2]) * (p[0] + q[0]) for p, q in zip(pts, pts[1:]))
        if ny < -1.0:
            return el
    return None


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b36_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    surf_objs = [o for o in b.children_recursive if o.get("cgml3_feature") in ("WallSurface", "RoofSurface",
                                                                             "GroundSurface")]
    mats = [m for o in surf_objs for m in o.data.materials]
    check("every surface object has its own material", len(mats) == len(set(mats)) == len(surf_objs),
          f"{len(surf_objs)} surfaces, {len(set(mats))} materials")
    check("surface gml_ids are NCNames", all(not any(c in o["gml_id"] for c in NCNAME_BAD) for o in surf_objs),
          str([o["gml_id"] for o in surf_objs]))

    first, second = export(out, "roof_a"), export(out, "roof_b")
    for v in ("2.0", "3.0"):
        ids = all_ids(first[v])
        dup = [i for i, n in Counter(ids).items() if n > 1]
        check(f"{v}: every gml:id unique", not dup, str(dup[:5]))
        a = [gid for _t, gid, _e in surfaces(first[v])]
        bb = [gid for _t, gid, _e in surfaces(second[v])]
        check(f"{v}: surface ids the same in a second export", a == bb and len(a) == 7, f"{a}\n{bb}")
    a2 = sorted(gid for _t, gid, _e in surfaces(first["2.0"]))
    a3 = sorted(gid for _t, gid, _e in surfaces(first["3.0"]))
    check("2.0 and 3.0 write the same surface ids", a2 == a3)

    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    front = next(o for o in walls if (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)
    edit_one_face(front)
    r1 = bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front.name, face_index=0,
                                  position_uv=(0.25, 0.6))
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front)
    r2 = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front.name, face_index=0, position_uv=(0.75, 0.15))
    check("window and door placed", r1 == r2 == {'FINISHED'}, f"{r1} {r2}")
    other = [m for o in walls if o is not front for m in o.data.materials]
    check("the other walls' materials carry no opening ids",
          not any(m.get("opening_surface_id") or m.get("cgml_opening_link") for m in other))

    paths = export(out, "open")
    for v in ("2.0", "3.0"):
        surf = surfaces(paths[v])
        walls_out = [gid for t, gid, _e in surf if t == "WallSurface"]
        check(f"{v}: still 4 WallSurfaces, no extra wall for the openings", len(walls_out) == 4, str(walls_out))
        dup = [i for i, n in Counter(all_ids(paths[v])).items() if n > 1]
        check(f"{v}: every gml:id unique", not dup, str(dup[:5]))
        fw = front_wall(paths[v])
        fills = [e for e in fw.iter() if e.tag.split("}")[1] in ("WindowSurface", "DoorSurface", "Window", "Door")] \
            if fw is not None else []
        holes = len([e for e in fw.iter() if e.tag.endswith("interior")]) if fw is not None else 0
        check(f"{v}: the clicked front wall holds the window and the door", len(fills) == 2, str(len(fills)))
        check(f"{v}: the front wall has 2 holes (interior rings)", holes >= 2, str(holes))
        fid = (fw.get(GML_IDS[0]) or fw.get(GML_IDS[1])) if fw is not None else None
        check(f"{v}: front wall written with its own gml_id", fid == front["gml_id"], f"{fid} / {front['gml_id']}")

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
