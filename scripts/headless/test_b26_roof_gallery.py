"""B26: the build row's Roof opens a large, named roof gallery.

- nothing selected: Roof is the greyed step button (tooltip says why)
- a building selected: Roof is a popover (CGML_PT_roof_gallery)
- the gallery lists the AdV roof types in ROOF_TYPES order, each a large
  thumbnail + a named roof_add button; the building's current type is pressed
- choosing a type puts that roof on the selected building at once (no second
  click on the building); choosing another swaps it (no leftover surfaces)
- the gallery says F9 adjusts pitch / ridge
- an imported building with its LoD2 roof gets no gallery (B15 rule)
- importing a file after New Building works (CRS without origin, fixed in
  unified_builder._prepare_world)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b26_roof_gallery.py
"""
import os
import sys
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import Rec, check, FAILURES, row, activate  # noqa: E402

BUILDINGS = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")


class PanelRec(Rec):
    def template_icon(self, icon_value=0, scale=1.0, **k):
        self.out.append(("icon", icon_value, self._on(), False, ""))

    def row(self, *a, **k):
        return PanelRec(self.out, self)

    def grid_flow(self, *a, **k):
        return self.row()


def gallery(tools):
    out = []
    holder = type("H", (), {"layout": PanelRec(out)})()
    tools.CGML_PT_roof_gallery.draw(holder, bpy.context)
    return out


def roof_entries(out):
    return [e for e in out if e[0] == "op" and e[1] == "cgml.roof_add"]


def surfaces(building):
    return sorted(str(o.get("cgml3_feature")) for o in building.children_recursive)


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    tools = import_module(f"{addon}.ui.tools")
    gal = import_module(f"{addon}.ui.gallery")
    from_shapes = import_module(f"{addon}.shared.roof_shapes")
    codes = list(from_shapes.ROOF_TYPES)

    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=win, area=area, region=area.regions[-1]):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)

        out = row(tools)
        roof = [e for e in out if e[0] == "op" and e[4] == "roof"]
        check("nothing selected: Roof is a greyed step", len(roof) == 1 and roof[0][1] == "cgml.tool_pick"
              and not roof[0][2], str(roof))

        bpy.ops.cgml.building_new(points=footprint())
        building = bpy.context.active_object
        roof = [e for e in row(tools) if e[0] == "op" and e[4] == "roof"]
        check("building selected: Roof opens the gallery",
              len(roof) == 1 and roof[0][1] == "CGML_PT_roof_gallery" and roof[0][2] and roof[0][5] == "Roof",
              str(roof))

        out = gallery(tools)
        ents = roof_entries(out)
        names = [e[5] for e in ents]
        check("every roof type named, in order", names == list(from_shapes.ROOF_TYPES.values()), str(names))
        check("one large thumbnail per type", sum(1 for e in out if e[0] == "icon") == len(codes))
        check("thumbnails loaded", all(f"roof_{c}" in gal._previews for c in codes))
        check("nothing pressed on a LoD1 block", not any(e[3] for e in ents))
        check("F9 hint", any(e[0] == "label" and "F9" in e[1] for e in out))

        before = surfaces(building)
        res = bpy.ops.cgml.roof_add('INVOKE_DEFAULT', roof_type="3100")  # what the gallery button runs
        check("gable on the selected building at once", res == {'FINISHED'}
              and building.get("bldg:roofType") == "3100", f"{res} {building.get('bldg:roofType')}")
        gable = surfaces(building)
        check("gable has more roof faces than the block", gable.count("RoofSurface") >= 2
              and gable != before, str(gable))
        pressed = [e[5] for e in roof_entries(gallery(tools)) if e[3]]
        check("current type pressed", pressed == ["Gable"], str(pressed))

        activate(building)
        res = bpy.ops.cgml.roof_add('INVOKE_DEFAULT', roof_type="3200")
        hip = surfaces(building)
        check("another type swaps the roof", res == {'FINISHED'} and building.get("bldg:roofType") == "3200"
              and hip.count("RoofSurface") == 4, str(hip))
        check("no leftover surfaces after the swap", hip.count("GroundSurface") == 1, str(hip))

        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        # the scene still has the CRS (no origin) of the drawn building: import must not fail (B26 fix)
        res = bpy.ops.cgml3.import_gml_file(filepath=os.path.join(BUILDINGS, "buildings_lod2_citygml_3_0.gml"))
        w = bpy.context.scene.world
        check("import after New Building works, origin written", res == {'FINISHED'}
              and "cgml_X-Origin" in w and float(w["cgml_X-Origin"]) > 500000, str(dict(w.items())))
        imported = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
        activate(imported)
        roof = [e for e in row(tools) if e[0] == "op" and e[4] == "roof"]
        check("imported LoD2 roof: no gallery", roof and roof[0][1] == "cgml.tool_pick" and not roof[0][2],
              str(roof))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
