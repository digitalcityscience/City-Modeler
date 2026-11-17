"""B101: flat balcony railing, installation surface rows on the card, card in Edit Mode.

User feedback 2026-11-16: balconies had too much geometry (4 closed boxes, 24 faces, 5 cm railing
panels); a material set on one balcony face left the others undefined; the card vanished in Edit
Mode and did not say whether the building or the clicked element was edited.

- Projecting balcony: slab box (6 faces) + 3 flat railing faces = 9; the railing faces carry
  ``cgml3_part`` = railing and one surface id of their own
- card surface rows: Railing, Wall, Outer floor, Outer ceiling (no Closure); "All surfaces" writes
  every face, one row writes every face of its surface only, mixed values read "differs"
- 2.0 and 3.0 export: the railing WallSurface carries material=glass, the slab's surfaces
  material=concrete, nothing else; XSD valid
- Edit Mode: the panel polls, only the active object is edited (not every object Edit Facade holds)
- path line "Building › Wall › Window"; the panel draws for balcony and window in both modes

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b101_flat_railing_card.py -- <out_dir>
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402
from test_b88_element_semantics import Recorder, element_xml, generics, select  # noqa: E402


def draws(card, label):
    panel = Recorder()
    panel.layout = Recorder()
    try:
        card.CGML3_PT_Attributes.draw(panel, bpy.context)
        card.CGML3_PT_Attributes.draw_header(panel, bpy.context)
        check(f"panel draws: {label}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"panel draws: {label}", False, repr(exc))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b101_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    card = import_module(f"{addon}.ui.attribute_card")
    wm = bpy.context.window_manager

    b = author_everything()
    kids = list(b.children_recursive)
    balcony = next(o for o in kids if o.get("cgml_semantic_type") == "Balcony")
    window = next(o for o in kids if o.get("cgml3_feature") == "WindowSurface")
    mats = balcony.data.materials
    railing = [p for p in balcony.data.polygons if mats[p.material_index].get("cgml3_part") == "railing"]
    check("balcony: slab box + 3 flat railing faces", len(balcony.data.polygons) == 9 and len(railing) == 3,
          f"{len(balcony.data.polygons)} faces, {len(railing)} railing")
    rail_ids = {mats[p.material_index]["con_surface_id"] for p in railing}
    check("railing: one surface of its own", len(rail_ids) == 1
          and not rail_ids & {m["con_surface_id"] for m in mats if m.get("cgml3_part") != "railing"}, str(rail_ids))

    select(balcony)
    rows = [(g[0], g[3]) for g in card.surface_groups(balcony)]
    check("card rows: Railing, Wall, Outer floor, Outer ceiling (no Closure)",
          sorted(rows) == sorted([("Railing", 3), ("Wall", 3), ("Outer floor", 1), ("Outer ceiling", 1)]), str(rows))
    wm.cgml_isurf_0 = "concrete"
    check("All surfaces: every row concrete", all(
        getattr(wm, f"cgml_isurf_{i}") == "concrete" for i in range(1, len(rows) + 1)))
    i_rail = 1 + [g[0] for g in card.surface_groups(balcony)].index("Railing")
    setattr(wm, f"cgml_isurf_{i_rail}", "glass")
    check("railing row: glass on its 3 faces only; the closure (on the wall) takes none",
          sorted(card._store_read(m, "material") for m in mats) == [""] + ["concrete"] * 5 + ["glass"] * 3,
          str(sorted(card._store_read(m, "material") for m in mats)))
    check("All surfaces now reads 'differs'", wm.cgml_isurf_0 == "__mixed__", str(wm.cgml_isurf_0))

    floor_id = next(m["con_surface_id"] for m in mats if m["SurfaceTyp"] == "OuterFloorSurface")
    rail_id = rail_ids.pop()
    for v, wall_tag, floor_tag in (("2.0", "bldg:WallSurface", "bldg:OuterFloorSurface"),
                                   ("3.0", "con:WallSurface", "con:OuterFloorSurface")):
        p = os.path.join(out, f"b101_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        rail_xml, floor_xml = element_xml(text, wall_tag, rail_id), element_xml(text, floor_tag, floor_id)
        check(f"{v}: railing surface written", bool(rail_xml), rail_id)
        check(f"{v}: railing material=glass", generics(rail_xml) == {"material": "glass"}, str(generics(rail_xml)))
        if v == "3.0":  # 2.0 has no OuterFloorSurface: the writer maps it to another class
            check(f"{v}: floor material=concrete", generics(floor_xml) == {"material": "concrete"},
                  str(generics(floor_xml)))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    check("path line", card._path(window, b) == "Building › Wall › Window", card._path(window, b))
    for o in (balcony, window):
        select(o)
        draws(card, f"{o.name} Object Mode")

    wall = window.parent
    select(balcony, wall, b)
    bpy.ops.object.mode_set(mode='EDIT')
    check("Edit Mode: panel shows", card.CGML3_PT_Attributes.poll(bpy.context), bpy.context.mode)
    check("Edit Mode: only the active object is edited", card.elements(bpy.context) == [balcony]
          and card.targets(bpy.context) == [b], f"{card.elements(bpy.context)} {card.targets(bpy.context)}")
    draws(card, "balcony Edit Mode")
    bpy.ops.object.mode_set(mode='OBJECT')

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
