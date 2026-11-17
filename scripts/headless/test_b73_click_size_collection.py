"""B73: Edit Facade balcony / chimney place by drag only, the balcony size is in the header again,
new installations go in the "BuildingInstallation" collection, Esc does not stop browse picking.

- drag_to_balcony: the header width while the drag is short, the drag's width when wider; header
  depth / railing; a door / window face fits the opening
- drag_to_chimney: a short drag gives the header size
- _FaceDraw: a click places nothing (no add operator on a click)
- the Balcony tool header shows Width / Depth / Railing height
- make_balcony: in a "BuildingInstallation" collection next to the building's, flagged like the
  importer's, still parented to the building
- browse pick modal: no Esc branch

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b73_click_size_collection.py
"""
import inspect
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ia = import_module(f"{addon}.ops.installation_authoring")
    tools = import_module(f"{addon}.ui.tools")
    bm = import_module(f"{addon}.ops.browse_gml")

    wall = {"host": "B", "normal": (0, -1, 0), "plane": (0, -4, 4), "lo": -5.0, "hi": 5.0,
            "zmin": 0.0, "zmax": 9.0, "opening": False}
    a = ia.drag_to_balcony(wall, (1, -4, 3), (1.1, -4, 3), 'PROJECTING', 2.5, 1.2, 1.1)
    check("short drag: header width / depth / railing", abs(a["width"] - 2.5) < 1e-6
          and abs(a["depth"] - 1.2) < 1e-6 and abs(a["railing"] - 1.1) < 1e-6 and abs(a["point"][2] - 3) < 1e-6,
          str(a))
    a = ia.drag_to_balcony(wall, (-1, -4, 3), (2, -4, 4), 'PROJECTING', 2.5, 1.2, 1.1)
    check("wide drag: its width, header depth", abs(a["width"] - 3.0) < 1e-6 and abs(a["depth"] - 1.2) < 1e-6,
          str(a))
    a = ia.drag_to_balcony(wall, (4.9, -4, 3), (4.9, -4, 3), 'PROJECTING', 2.0, 1.2, 1.0)
    check("short drag at the wall end: stays on the wall", abs(a["point"][0] - 4.0) < 1e-6, str(a))
    door = dict(wall, opening=True, lo=-0.5, hi=0.5, zmin=0.2)
    a = ia.drag_to_balcony(door, (3, -4, 2), (4, -4, 2.5), 'PROJECTING', 3.0, 1.2, 1.0)
    check("door face: fitted (its width + 0.8, floor at its bottom)", abs(a["width"] - 1.8) < 1e-6
          and abs(a["point"][0]) < 1e-6 and abs(a["point"][2] - 0.2) < 1e-6, str(a))
    roof = {"host": "B", "normal": (0, 0, 1)}
    c = ia.drag_to_chimney(roof, (0, 0, 9), (0.05, 0, 9), 'SQUARE', 0.9)
    check("short chimney drag: header size", abs(c["size"] - 0.9) < 1e-6, str(c))
    c = ia.drag_to_chimney(roof, (0, 0, 9), (1.0, 0, 9), 'SQUARE', 0.9)
    check("chimney drag: its size", abs(c["size"] - 1.0) < 1e-6, str(c))

    src = inspect.getsource(ia._FaceDraw.modal)
    check("a click places nothing", "B73" in src and "INVOKE_DEFAULT" not in src)
    hdr = inspect.getsource(tools.draw_installation_settings)
    check("balcony header: width / depth / railing", '"width", "depth", "railing"' in hdr)
    check("pick: Esc does not stop it", "'ESC'" not in inspect.getsource(bm.CGML3_OT_browse_pick.modal))

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    home = b.users_collection[0]
    bal = ia.make_balcony(bpy.context, b, (0.0, -4.0, 3.0), (0, -1, 0))
    coll = bal.users_collection[0]
    owner = next((c for c in (bpy.context.scene.collection, *bpy.data.collections) if home.name in c.children),
                 home)
    check("balcony in BuildingInstallation, next to the building's collection",
          coll.name == "BuildingInstallation" and coll.name in owner.children and coll is not home,
          f"{coll.name} home={home.name} owner={owner.name}")
    check("... flagged like the importer's", bool(coll.get("cgml3_auto_disabled_subfeatures")))
    check("... still parented to the building", bal.parent is b and bal["cgml_parent_id"] == b["gml_id"])
    ch = ia.make_chimney(bpy.context, b, (1.0, 0.0, 9.0), (0, 0, 1))
    check("chimney: the same collection", ch.users_collection[0] is coll)


main()
print(f"B73 click size collection: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILED'}")
if FAILURES:
    sys.exit(1)
