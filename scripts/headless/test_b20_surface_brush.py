"""B20: surface type brush (Edit Mode tool).

- the tool is registered in Edit Mode after Window / Door / Stamp; a click runs
  cgml.surface_brush, then Blender's selection
- header: feature type (B25: a label, read from the object) + surface type
- the brush (what a click / drag ends in) gives the selected faces the picked
  type: one wall face of an imported building becomes a ClosureSurface; the
  other faces keep theirs
- it follows the object's own CityGML version without losing the picked type
- export: 2.0 and 3.0 write one more ClosureSurface than the source, one wall
  fewer is not required (the wall object keeps its other faces), XSD valid

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b20_surface_brush.py -- <out_dir>
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


def tool_ids(mode):
    from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
    cls = ToolSelectPanelHelper._tool_class_from_space_type('VIEW_3D')
    return [t.idname if t is not None else None for t in ToolSelectPanelHelper._tools_flatten(cls._tools[mode])]


def fresh():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def run(version, out_dir):
    from city_modeler.ui import tools
    fresh()
    src = os.path.join(BUILDINGS, f"buildings_lod2_citygml_{version.replace('.', '_')}.gml")
    bpy.ops.cgml3.import_gml_file(filepath=src)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    before = export_checked(os.path.join(out_dir, f"before_{version}.gml"), version)
    # 3.0: ClosureSurface is a core (default namespace) class, WallSurface is con:
    tag = "ClosureSurface" if version == "3.0" else "bldg:ClosureSurface"
    wall_tag = "con:WallSurface" if version == "3.0" else "bldg:WallSurface"
    n_closure, n_wall = count(before, tag), count(before, wall_tag)

    walls = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.get("cgml3_feature") == "WallSurface"]
    wall, other_wall = walls[0], walls[1]

    def types_of(o):
        return [o.data.materials[p.material_index].get("SurfaceTyp") for p in o.data.polygons]
    nfaces, mats_before, other_before = len(wall.data.polygons), types_of(wall), types_of(other_wall)
    bpy.context.view_layer.objects.active = wall
    wall.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    bm.faces[0].select = True
    bmesh.update_edit_mesh(wall.data)

    props = bpy.context.scene.cgml3_modeltyper_props
    other = "3.0" if version == "2.0" else "2.0"
    props.citygml_version = other  # the brush must switch to the object's version
    props.feature_type = "BUILDING"
    props.category_enum = "ClosureSurface"
    out = []
    tools.SurfaceBrushTool.draw_settings(bpy.context, Rec(out), None)
    # B25: the object decides the feature type, so the header shows it as a label
    check(f"{version}: header has feature type + surface type",
          ("label", f"Building · CityGML {version}") in out and ("prop", "category_enum") in out, str(out))
    res = bpy.ops.cgml.surface_brush('EXEC_DEFAULT')
    check(f"{version}: brush finishes", res == {'FINISHED'}, str(res))
    check(f"{version}: brush used the object's version and kept the type",
          props.citygml_version == version and props.category_enum == "ClosureSurface",
          f"{props.citygml_version} {props.category_enum}")
    bpy.ops.object.mode_set(mode='OBJECT')
    types = types_of(wall)
    check(f"{version}: brushed face is ClosureSurface", types[0] == "ClosureSurface", f"{mats_before[:1]} -> {types[:1]}")
    check(f"{version}: unselected faces unchanged", types[1:] == mats_before[1:] and len(types) == nfaces
          and types_of(other_wall) == other_before, f"{types_of(other_wall)[:2]} vs {other_before[:2]}")

    for o in bpy.context.selected_objects:
        o.select_set(False)  # export writes the selection; compare whole scenes
    after = export_checked(os.path.join(out_dir, f"after_{version}.gml"), version)
    check(f"{version}: export has one more ClosureSurface", count(after, tag) == n_closure + 1,
          f"{count(after, tag)} vs {n_closure}")
    check(f"{version}: one wall fewer (the brushed one-face wall)", count(after, wall_tag) == n_wall - 1,
          f"{count(after, wall_tag)} (before {n_wall})")


def main():
    out_dir = sys.argv[sys.argv.index("--") + 1]
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    ids = [i for i in tool_ids('EDIT_MESH') if i and i.startswith("cgml.")]
    check("Edit Mode tools: Window, Door, Balcony, Chimney (B70), Surface Type, Paste (B71)",
          ids == ["cgml.window_tool", "cgml.door_tool", "cgml.balcony_tool", "cgml.chimney_tool",
                  "cgml.surface_brush_tool", "cgml.stamp_tool"], str(ids))
    from city_modeler.ui import tools
    km = tools.SurfaceBrushTool.bl_keymap
    check("click runs the brush, selection after",
          km[0][0] == "cgml.surface_brush" and "view3d.select" in [k[0] for k in km])
    for version in ("3.0", "2.0"):
        run(version, out_dir)
    finish("B20 surface type brush")


main()
