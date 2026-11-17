"""B57 (agent plan A2): the export message claims only the XSD; refused operators say why.

- export with XSD validation on: scene["cgml_last_export"] has xsd "valid" and geometry_checked
  false; the operator source no longer says "successfully validated"
- facade_draw / door_draw / split_surfaces / semantic_stamp / set_semantic_stamp_source in Object
  Mode fail with their poll message, not "context is incorrect"

Old code: no xsd field, the poll errors are the generic "context is incorrect".

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b57_honest_messages.py -- <out_dir>
"""
import inspect
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def poll_error(op):
    try:
        op()
    except RuntimeError as e:
        return str(e)
    return ""


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b57_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)

    bpy.ops.cgml.building_new(points=footprint())
    bpy.context.scene.cgml3.xsd_validate = True
    p = os.path.join(out, "b.gml")
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    rec = json.loads(bpy.context.scene["cgml_last_export"])
    check("record says XSD valid", rec.get("xsd") == "valid", str(rec.get("xsd")))
    check("record says geometry not checked", rec.get("geometry_checked") is False)
    src = inspect.getsource(sys.modules[addon].CGML3_OT_ExportGMLFile.execute)
    check("no 'successfully validated' claim", "successfully validated" not in src)
    check("message names what was not checked", "geometry not checked" in src)

    bpy.context.view_layer.objects.active = bpy.context.scene.objects[0]
    for name, word in (("facade_draw", "WallSurface"), ("door_draw", "WallSurface"),
                       ("split_surfaces", "split"), ("semantic_stamp", "stamp"),
                       ("set_semantic_stamp_source", "window, door")):
        msg = poll_error(getattr(bpy.ops.cgml, name))
        check(f"{name} poll says why", word in msg and "context is incorrect" not in msg, msg[:120])

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
