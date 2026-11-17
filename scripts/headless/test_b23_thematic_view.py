"""B23: colour by attribute (viewport only).

- Function: objects of buildings with different functions get different
  colours, the same function the same colour; Solid views switch to Object colour
- Year built: older blue-ish, newer red-ish, unknown grey
- Missing: a building without height/year is red, a complete one green
- Off: every object colour and the views' colour type are back as before
- materials (names per slot) and the 2.0 / 3.0 export are unchanged by on + off
  (also while on: the writers never read object colours)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b23_thematic_view.py -- <out_dir>
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy  # noqa: E402

from building_harness import check, finish  # noqa: E402

ADDON = "city_modeler"
BUILDINGS = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")


def export_text(path, version):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
    text = open(path, encoding="utf-8").read()
    text = re.sub(r"<(core:)?creationDate>[^<]*<", "<creationDate>X<", text)
    return re.sub(r'(<app:\w+ gml:id=")[^"]*"', r'\1X"', text)  # appearance ids are minted per export


def state():
    return ({o.name: tuple(round(c, 6) for c in o.color) for o in bpy.context.scene.objects},
            {o.name: [m.name if m else None for m in o.data.materials] for o in bpy.context.scene.objects
             if o.type == 'MESH'})


def views():
    return [s for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type == 'VIEW_3D'
            for s in a.spaces if s.type == 'VIEW_3D']


def main():
    out_dir = sys.argv[sys.argv.index("--") + 1]
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings()  # keeps a 3D view for the shading check
    bpy.ops.preferences.addon_enable(module=ADDON)
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    from city_modeler.ui import attribute_card as card
    from city_modeler.ui import theme_view as tv

    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(BUILDINGS, "buildings_lod2_citygml_3_0.gml"))
    roots = sorted((o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "Building" and o.parent is None),
                   key=lambda o: o.name)
    b1, b2, b3 = roots[:3]
    b1["bldg:function"], b2["bldg:function"], b3["bldg:function"] = "31001_1010", "31001_1010", "31001_2100"
    card.set_year(b1, 1900)
    card.set_year(b2, 2000)
    for k in [k for k in b3.keys() if k.startswith("con:height") or k == "con:dateOfConstruction"]:
        del b3[k]
    card.set_height(b1, 10.0)
    card.set_height(b2, 10.0)

    colours0, mats0 = state()
    shading0 = [s.shading.color_type for s in views()]
    check("a 3D view exists for the shading check", bool(views()))
    text0 = {v: export_text(os.path.join(out_dir, f"before_{v}.gml"), v) for v in ("3.0", "2.0")}

    def colour_of(root):
        mesh = next(o for o in [root, *root.children_recursive] if o.type == 'MESH')
        return tuple(round(c, 4) for c in mesh.color)

    bpy.ops.cgml.theme_view(mode='FUNCTION')
    check("function: same function, same colour", colour_of(b1) == colour_of(b2))
    check("function: other function, other colour", colour_of(b1) != colour_of(b3))
    check("function: views show object colour", all(s.shading.color_type == 'OBJECT' for s in views()))
    check("function: legend names the codes", any("31001_1010" in t for _c, t in tv._legend), str(tv._legend))
    text_on = export_text(os.path.join(out_dir, "while_on.gml"), "3.0")
    check("export while coloured is unchanged", text_on == text0["3.0"])

    bpy.ops.cgml.theme_view(mode='YEAR')
    c1, c2, c3 = colour_of(b1), colour_of(b2), colour_of(b3)
    check("year: old bluer than new", c1[2] > c1[0] and c2[0] > c2[2], f"{c1} {c2}")
    check("year: unknown grey", c3[:3] == tuple(round(x, 4) for x in tv.GREY[:3]), str(c3))

    bpy.ops.cgml.theme_view(mode='MISSING')
    check("missing: incomplete red, complete green", colour_of(b3)[0] > 0.8 and colour_of(b1)[1] > 0.7,
          f"{colour_of(b3)} {colour_of(b1)}")

    bpy.ops.cgml.theme_view(mode='OFF')
    colours1, mats1 = state()
    check("off: object colours back", colours1 == colours0)
    check("off: views' colour type back", [s.shading.color_type for s in views()] == shading0)
    check("materials untouched", mats1 == mats0)
    for v in ("3.0", "2.0"):
        check(f"{v}: export unchanged after on + off", export_text(os.path.join(out_dir, f"after_{v}.gml"), v) == text0[v])
    finish("B23 thematic view")


main()
