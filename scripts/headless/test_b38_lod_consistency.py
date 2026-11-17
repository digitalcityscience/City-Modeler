"""B38: one LoD per building after a window / door is drawn (2.0 and 3.0).

A window promotes the whole building to LoD 3 (lod_helpers.promote_opening_host_lod sets
lod=3 on every surface material). The writers merge the surface objects through their
evaluated meshes, whose materials are the depsgraph's copies: a custom property set after
the last evaluation is not on them. The 2.0 writer reads the LoD from the material, so it
wrote the walls, roof and ground as lod2MultiSurface next to a lod3 Window / Door.

- New Building + gable roof: every surface lod2 (2.0 and 3.0)
- + window and door, exported right away (no depsgraph update): every lodN element of
  the Building is LoD 3 in 2.0 and 3.0
- both files XSD-valid

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b38_lod_consistency.py -- <out_dir>
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


def lods(path):
    """Distinct N of every lodN* geometry property in the file."""
    return sorted(set(re.findall(r"<(?:bldg:)?lod(\d)(?:MultiSurface|Solid|Geometry)\b", open(path, encoding="utf-8").read())))


def export(out, tag):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    paths = {}
    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b38_{tag}_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{tag}: export {v}", res == {'FINISHED'}, str(res))
        paths[v] = p
    return paths


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b38_")
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
    for v, p in export(out, "roof").items():
        check(f"{v}: roofed building is LoD 2 only", lods(p) == ["2"], str(lods(p)))

    def front():
        return next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                    and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)

    edit_one_face(front())
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front().name, face_index=0,
                             position_uv=(0.25, 0.6))
    bpy.ops.object.mode_set(mode='OBJECT')
    edit_one_face(front())
    bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=front().name, face_index=0, position_uv=(0.75, 0.15))
    for v, p in export(out, "open").items():
        check(f"{v}: with window and door every surface is LoD 3", lods(p) == ["3"], str(lods(p)))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":  # B41 imports lods
    main()
