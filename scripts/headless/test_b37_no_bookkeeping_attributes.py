"""B37: the add-on's own bookkeeping is never written as a CityGML generic attribute.

The 3.0 writer wrote ModelType, CRS, cgml_source, cgml_hierarchy_model on the Building and
uuid, created_at, lod, Typ, cgml_opening_link, created_by_openings_cutter,
opening_cutter_interior on surfaces (material custom properties). The 2.0 writer already
skipped them.

- authored building + roof + window + door + balcony + chimney: 2.0 and 3.0 carry no
  generic attribute at all (nothing the user set)
- a user attribute set through the attribute card is still written (3.0)
- an imported Hamburg file keeps all its own generic attributes in a 3.0 round trip

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b37_no_bookkeeping_attributes.py -- <out_dir>
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
from test_b29_place_where_clicked import edit_one_face, fake  # noqa: E402

HH3 = os.path.join(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"), "buildings_lod2_citygml_3_0.gml")


def gen_names(path):
    """Generic attribute names: 3.0 <gen:name>, 2.0 <gen:xxxAttribute name="...">."""
    text = open(path, encoding="utf-8").read()
    return re.findall(r"<gen:name>([^<]*)</gen:name>", text) + re.findall(r'<gen:\w+Attribute name="([^"]*)"', text)


def export(out, tag):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    paths = {}
    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b37_{tag}_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{tag}: export {v}", res == {'FINISHED'}, str(res))
        paths[v] = p
    return paths


def author_everything():
    """New Building, gable roof, window + door on the front wall, balcony, chimney."""
    ia = import_module(f"{ensure_addon_enabled()[1]}.ops.installation_authoring")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())  # 10 x 8 x 9 m
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")

    def front():
        return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                    and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)

    edit_one_face(front())
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front().name, face_index=0,
                             position_uv=(0.25, 0.6))
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front())
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front().name, face_index=0, position_uv=(0.75, 0.15))
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front())
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_balcony_add, 'PROJECTING'), bpy.context,
                                       Vector((-2.5, -24.0, 5.0)), Vector((0, 1, 0)))
    r1 = bpy.ops.cgml.balcony_add('EXEC_DEFAULT', kind='PROJECTING', **cand)
    roof = next(o for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface")
    edit_one_face(roof)
    rz = max((roof.matrix_world @ v.co).z for v in roof.data.vertices)
    ry = sum((roof.matrix_world @ v.co).y for v in roof.data.vertices) / len(roof.data.vertices)
    cand = ia._ClickPlace.candidate_at(fake(ia.CGML_OT_chimney_add, 'SQUARE'), bpy.context,
                                       Vector((-3.0, ry, rz + 20)), Vector((0, 0, -1)))
    r2 = bpy.ops.cgml.chimney_add('EXEC_DEFAULT', kind='SQUARE', **cand)
    check("balcony and chimney placed", r1 == r2 == {'FINISHED'}, f"{r1} {r2}")
    bpy.ops.object.mode_set(mode='OBJECT')
    return b


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b37_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)

    b = author_everything()
    paths = export(out, "authored")
    for v, p in paths.items():
        names = gen_names(p)
        check(f"{v}: no add-on bookkeeping as generic attribute", not names, str(sorted(set(names))))

    card = import_module(f"{addon}.ui.attribute_card")
    card.set_generic(b, "Eigentuemer", "LGV")
    paths = export(out, "user_attr")
    names = gen_names(paths["3.0"])
    check("3.0: the user's own attribute is written", names == ["Eigentuemer"], str(names))
    n2 = open(paths["2.0"], encoding="utf-8").read().count('name="Eigentuemer"')
    check("2.0: the user's own attribute is written once", n2 == 1, str(n2))

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    hh2 = HH3.replace("_3_0.gml", "_2_0.gml")
    src2 = open(hh2, encoding="utf-8").read().count('name="DatenquelleBodenhoehe"')
    bpy.ops.cgml3.import_gml_file(filepath=hh2)
    rt2 = os.path.join(out, "b37_hh_rt_v2.gml")
    bpy.ops.cgml3.export_gml_file(filepath=rt2, citygml_version="2.0")
    n2 = open(rt2, encoding="utf-8").read().count('name="DatenquelleBodenhoehe"')
    check("Hamburg 2.0 round trip writes each attribute once", src2 == n2 == 3, f"source {src2}, export {n2}")

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    src = set(gen_names(HH3))
    check("import Hamburg LoD2 3.0", bpy.ops.cgml3.import_gml_file(filepath=HH3) == {'FINISHED'})
    rt = os.path.join(out, "b37_hh_rt_v3.gml")
    bpy.ops.cgml3.export_gml_file(filepath=rt, citygml_version="3.0")
    got = set(gen_names(rt))
    check("Hamburg round trip keeps its own generic attributes", src and got == src,
          f"missing {sorted(src - got)}, extra {sorted(got - src)}")

    # a 2.0 file to 3.0: the 2.0 reader keeps colours, LoD and opening data on materials
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    rw2 = os.path.join(HERE, "..", "..", "test_files", "Building", "Railway_Scene_LoD3_CityGML2_Building_only.gml")
    check("import Railway LoD3 2.0", bpy.ops.cgml3.import_gml_file(filepath=rw2) == {'FINISHED'})
    rt = os.path.join(out, "b37_railway2_to_v3.gml")
    bpy.ops.cgml3.export_gml_file(filepath=rt, citygml_version="3.0")
    got = set(gen_names(rt))
    # bldg:yearOf* are the file's own 2.0 data the 3.0 writer keeps as generic attributes (not bookkeeping)
    check("Railway 2.0 -> 3.0: only the file's own data", "Gemeindeschluessel" in got
          and got <= {"Gemeindeschluessel", "bldg:yearOfConstruction", "bldg:yearOfDemolition"}, str(sorted(got)))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":  # B40 imports author_everything
    main()
