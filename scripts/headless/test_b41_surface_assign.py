"""B41: Assign Surface Class keeps the face's LoD and gives it stable ids.

The brush (cgml3_modeltyper.manual_assign, non-opening path) made a new material with
lod=2 always: on a LoD3 building (after a window) the 2.0 writer then wrote that surface
as lod2MultiSurface among lod3 ones. The new material had no gml_multisurface_id, so its
MultiSurface got a new random id on every export.

- gable roof + window (LoD3), back wall brushed to OuterCeilingSurface
- 2.0 and 3.0: the surface is there, every lodN element is LoD 3, XSD valid
- all gml:ids of surfaces and MultiSurfaces are the same in a second export

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b41_surface_assign.py -- <out_dir>
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b38_lod_consistency import lods  # noqa: E402


def wall(b, sign):
    return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                and sign * (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y > 0.5)


def ids(path):
    return re.findall(r'<[\w:]*(?:Surface|MultiSurface) gml:id="([^"]+)"', open(path, encoding="utf-8").read())


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b41_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    if not ok:
        sys.exit(1)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100")
    edit_one_face(wall(b, -1))
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=wall(b, -1).name, face_index=0,
                             position_uv=(0.25, 0.6))
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(wall(b, 1))
    bpy.context.scene.cgml3_modeltyper_props.category_enum = "OuterCeilingSurface"
    check("brush finishes", bpy.ops.cgml.surface_brush('EXEC_DEFAULT') == {'FINISHED'})
    bpy.ops.object.mode_set(mode='OBJECT')

    first = {}
    for run in ("a", "b"):
        for v in ("2.0", "3.0"):
            p = os.path.join(out, f"b41_{run}_v{v[0]}.gml")
            check(f"{run}: export {v}", bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v) == {'FINISHED'})
            if run == "b":
                check(f"{v}: surface and MultiSurface ids the same in a second export", ids(p) == first[v],
                      str(set(ids(p)) ^ set(first[v])))
                continue
            first[v] = ids(p)
            text = open(p, encoding="utf-8").read()
            check(f"{v}: brushed OuterCeilingSurface written", "OuterCeilingSurface gml:id" in text)
            check(f"{v}: every surface LoD 3", lods(p) == ["3"], str(lods(p)))
            valid, msg = xsd_valid(v, p)
            check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
