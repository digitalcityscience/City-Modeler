"""B25: "Assign Surface Class" (the surface type brush's list).

- a building's list is the exterior shell in table order: WallSurface, RoofSurface,
  GroundSurface, OuterCeilingSurface, OuterFloorSurface, ClosureSurface (no
  Window/Door, no interior), the same in 2.0 and 3.0; WallSurface is the default
- each entry has a one-line description; the header shows it
- enum numbers do not depend on the LoD filter (stored values keep their meaning)
- Transportation: TrafficArea, AuxiliaryTrafficArea; Marking only in 3.0
- the header reads the feature type from the object (label); a plain mesh gets the
  feature type drop-down
- the build-row button says "Assign Surface Class"
- brushing OuterCeilingSurface exports bldg:/con:OuterCeilingSurface, XSD valid
- a railway (2.0) mesh face gets the Transportation list without Marking

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b25_surface_classes.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bmesh  # noqa: E402
import bpy  # noqa: E402

from building_harness import check, count, export_checked, finish  # noqa: E402

ADDON = "city_modeler"
BUILDINGS = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAILWAY = os.path.join(REPO, "test_files", "Railway", "Railway_citygml2.gml")
SHELL = ["WallSurface", "RoofSurface", "GroundSurface", "OuterCeilingSurface", "OuterFloorSurface",
         "ClosureSurface"]


class Rec:
    def __init__(self, out):
        object.__setattr__(self, "out", out)

    def __setattr__(self, name, value):
        pass

    def label(self, text="", **k):
        self.out.append(("label", text))

    def prop(self, data, name, **k):
        getattr(data, name)
        self.out.append(("prop", name))


def fresh():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def header():
    from city_modeler.ui import tools
    out = []
    tools.SurfaceBrushTool.draw_settings(bpy.context, Rec(out), None)
    return out


def edit_one_face(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[0].select = True
    bmesh.update_edit_mesh(obj.data)


def items():
    from city_modeler.modeltyper.ui.properties import get_category_items
    return [i[0] for i in get_category_items(None, bpy.context)]


def tables():
    from city_modeler.modeltyper.citygml import OPENING_TYPES, get_enum_items
    for v in ("2.0", "3.0"):
        names = [i[0] for i in get_enum_items("BUILDING", v, "1-3", exclude=OPENING_TYPES)]
        check(f"{v}: building list = exterior shell in order", names == SHELL, str(names))
        full = {i[0]: i[3] for i in get_enum_items("BUILDING", v, "4")}
        short = {i[0]: i[3] for i in get_enum_items("BUILDING", v, "1-3")}
        check(f"{v}: numbers independent of the LoD filter", all(full[k] == n for k, n in short.items()),
              f"{short} vs {full}")
        check(f"{v}: every entry has a description",
              all(i[2] and i[2] != i[0] for i in get_enum_items("BUILDING", v, "1-3")))
    t2 = [i[0] for i in get_enum_items("TRANSPORTATION", "2.0", "1-3", exclude=OPENING_TYPES)]
    t3 = [i[0] for i in get_enum_items("TRANSPORTATION", "3.0", "1-3", exclude=OPENING_TYPES)]
    check("2.0 transportation: no Marking", t2 == ["TrafficArea", "AuxiliaryTrafficArea"], str(t2))
    check("3.0 transportation: + Marking", t3 == ["TrafficArea", "AuxiliaryTrafficArea", "Marking"], str(t3))


def building(version, out_dir):
    from city_modeler.ui import tools
    fresh()
    src = os.path.join(BUILDINGS, f"buildings_lod2_citygml_{version.replace('.', '_')}.gml")
    bpy.ops.cgml3.import_gml_file(filepath=src)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    tag = ("con:" if version == "3.0" else "bldg:") + "OuterCeilingSurface"
    before = export_checked(os.path.join(out_dir, f"before_{version}.gml"), version)
    n = count(before, tag)

    wall = next(o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get("cgml3_feature") == "WallSurface")
    edit_one_face(wall)
    props = bpy.context.scene.cgml3_modeltyper_props
    check(f"{version}: list for a building", items() == SHELL, str(items()))
    out = header()
    check(f"{version}: header names the object's type and version",
          ("label", f"Building · CityGML {version}") in out and ("prop", "feature_type") not in out, str(out))
    check(f"{version}: default is WallSurface", props.category_enum == "WallSurface", props.category_enum)
    check(f"{version}: header shows the description",
          ("label", "Outer wall: part of the facade seen from outside") in out, str(out))
    props.category_enum = "OuterCeilingSurface"
    res = bpy.ops.cgml.surface_brush('EXEC_DEFAULT')
    check(f"{version}: brush finishes", res == {'FINISHED'}, str(res))
    bpy.ops.object.mode_set(mode='OBJECT')
    mat = wall.data.materials[wall.data.polygons[0].material_index]
    check(f"{version}: face is OuterCeilingSurface", mat.get("SurfaceTyp") == "OuterCeilingSurface",
          str(mat.get("SurfaceTyp")))
    for o in bpy.context.selected_objects:
        o.select_set(False)
    after = export_checked(os.path.join(out_dir, f"after_{version}.gml"), version)
    check(f"{version}: export has one more {tag}", count(after, tag) == n + 1, f"{count(after, tag)} vs {n}")
    props.category_enum = "WallSurface"  # next run starts from the default again
    check("build-row button label", tools._LABELS["surface"] == "Assign Surface Class")


def plain_mesh():
    fresh()
    bpy.ops.mesh.primitive_plane_add()
    bpy.ops.object.mode_set(mode='EDIT')
    out = header()
    check("plain mesh: feature type drop-down", ("prop", "feature_type") in out, str(out))
    bpy.ops.object.mode_set(mode='OBJECT')


def railway():
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=RAILWAY)
    from city_modeler.modeltyper.ui.properties import object_feature_type
    # the fixture's Railway has its own lod3MultiSurface, no TrafficArea children
    area = next((o for o in bpy.context.scene.objects if o.type == 'MESH' and len(o.data.polygons)
                 and object_feature_type(o) == "TRANSPORTATION"), None)
    check("railway has a Transportation mesh", area is not None,
          str({o.get("cgml3_feature") for o in bpy.context.scene.objects}))
    if area is None:
        return
    for o in bpy.context.selected_objects:
        o.select_set(False)
    edit_one_face(area)
    check("railway 2.0: Transportation list without Marking", items() == ["TrafficArea", "AuxiliaryTrafficArea"],
          str(items()))
    check("railway header", ("label", "Transportation · CityGML 2.0") in header(), str(header()))
    bpy.ops.object.mode_set(mode='OBJECT')


def main():
    out_dir = sys.argv[sys.argv.index("--") + 1]
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    tables()
    for version in ("2.0", "3.0"):
        building(version, out_dir)
    plain_mesh()
    railway()
    finish("B25 assign CityGML surface")


main()
