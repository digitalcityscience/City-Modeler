"""B65: a door whose bottom is up to 1 cm above the wall's lower edge becomes a ground notch.

ADR 0004 D3 counts such a door as touching the ground, but the notch needs the door's sides to
cross the lower edge; a few millimetres above it the kernel silently made a floating ring that
hugs the ground. Gable wall (pentagon) of a 12 x 8 m building with a 3100 roof:
- kernel (place_semantic_element), bottom 4 mm above, AUTO: ground notch (old: floating ring)
- forced GROUND, bottom 5 mm above: ground notch (old: ValueError "Cannot force")
- bottom 2 cm above (outside the tolerance), AUTO: still a floating ring
- the Draw Door operator (Edit Mode, F9 numbers), bottom 3 mm above: ground notch
- the building's export is XSD valid and the shell closed (agent_api.validate)

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b65_ground_door_tolerance.py -- <out_dir>
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b29_place_where_clicked import edit_one_face  # noqa: E402


def gable_wall():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint(width=12.0, depth=8.0))
    b = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=30.0)
    walls = [o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"]
    return b, max(walls, key=lambda w: len(w.data.polygons[0].vertices))  # the pentagon


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b65_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    fp = import_module(addon + ".shared.facade_placement")
    fsr = import_module(addon + ".shared.facade_surface_resolver")
    api = import_module(addon + ".shared.agent_api")

    def door(above, force=None, u=0.7):
        b, wall = gable_wall()
        ctx = fsr.resolve_surface_context(wall, 0)
        check(f"gable wall is a pentagon ({len(wall.data.polygons[0].vertices)})", len(wall.data.polygons[0].vertices) == 5)
        d = fp.FacadeElementDefinition(semantic_type="Door", width=1.0, height=2.1, door_touches_ground=force)
        try:
            fill = fp.place_semantic_element(d, ctx, fp.PlacementRequest(position_uv=(u, (1.05 + above) / ctx.height)))
        except ValueError as e:
            return b, f"refused: {e}"
        return b, str(fill.get("cgml_authoring_topology"))

    _b, topo = door(0.004)
    check("4 mm above, AUTO: ground notch", topo == "ground_notch", topo)
    _b, topo = door(0.005, force=True)
    check("5 mm above, GROUND: ground notch", topo == "ground_notch", topo)
    b, topo = door(0.02)
    check("2 cm above, AUTO: floating ring", topo != "ground_notch" and not topo.startswith("refused"), topo)

    b, wall = gable_wall()
    ctx = fsr.resolve_surface_context(wall, 0)
    edit_one_face(wall)
    r = bpy.ops.cgml.door_draw('EXEC_DEFAULT', object_name=wall.name, face_index=0,
                               position_uv=(0.3, (1.05 + 0.003) / ctx.height), width=1.0, height=2.1)
    bpy.ops.object.mode_set(mode='OBJECT')
    doors = [o for o in wall.children if str(o.get("cgml3_feature")) in ("Door", "DoorSurface")]
    check("Draw Door, 3 mm above: ground notch", r == {'FINISHED'} and doors
          and doors[0].get("cgml_authoring_topology") == "ground_notch",
          str([d.get("cgml_authoring_topology") for d in doors]))
    res = api.validate(str(b["gml_id"]))
    bres = res["buildings"].get(str(b["gml_id"]), {})
    check("export XSD valid, shell closed", res["ok"] and res["xsd"] == "valid" and bres.get("closed") is True,
          f"{res['xsd']} {bres.get('checks')}")

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
