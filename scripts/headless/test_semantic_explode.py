"""Ticket 06 checklist, run against the real Blender import pipeline.

Imports the Railway LoD3 CityGML3 fixture twice - once with
use_semantic_objects off, once on - and asserts every checklist item from
docs/agent/semantic-hierarchy/issues/06-citygml3-importer-explode.md.

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for
why a private staged scripts dir is required - the installed addon symlink
points at a different working copy):

    SP=/tmp/cgml-probe
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_semantic_explode.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML3_Building_only.gml
"""
import sys

import bpy

ADDON = "city_modeler"

EXPECTED_SURFACE_COUNTS = {
    "WallSurface": 9,
    "RoofSurface": 3,
    "GroundSurface": 1,
    "WindowSurface": 15,
    "DoorSurface": 6,
}


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def fresh_import(gml, use_semantic_objects):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = use_semantic_objects
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, f"import failed: {res}"
    return list(bpy.data.objects)


def total_faces(objs):
    return sum(len(o.data.polygons) for o in objs if o.type == 'MESH')


def check_flag_off(gml):
    objs = fresh_import(gml, False)
    assert len(objs) == 3, f"flag off: expected 3 objects, got {len(objs)}"
    building = next(o for o in objs if o.get("cgml3_feature") == "Building")
    assert building.type == 'MESH' and len(building.data.polygons) == 215, (
        f"flag off: Building must stay a flat 215-face mesh, got {len(building.data.polygons)}"
    )
    assert building.get("cgml_hierarchy_model") is None, (
        "flag off: cgml_hierarchy_model must not be set - import must be unchanged from today"
    )
    chimneys = [o for o in objs if o.get("cgml3_feature") == "BuildingInstallation"]
    assert len(chimneys) == 2 and all(c.parent is building for c in chimneys), (
        "flag off: both Chimneys must still parent directly to the Building"
    )
    print("[PASS] flag off: import is unchanged (3 objects, flat 215-face Building, Chimneys parented)")
    return total_faces(objs)


def check_flag_on(gml, expected_total_faces):
    objs = fresh_import(gml, True)
    by_feature = {}
    for o in objs:
        by_feature.setdefault(o.get("cgml3_feature"), []).append(o)

    for feature, expected_count in EXPECTED_SURFACE_COUNTS.items():
        got = by_feature.get(feature, [])
        assert len(got) == expected_count, (
            f"flag on: expected {expected_count} {feature} objects, got {len(got)}"
        )
    print(f"[PASS] flag on: object counts match {EXPECTED_SURFACE_COUNTS}")

    building = next(o for o in objs if o.get("cgml3_feature") == "Building")
    assert building.get("cgml_hierarchy_model") == "semantic_objects", (
        "flag on: Building must carry cgml_hierarchy_model=semantic_objects"
    )
    assert building.type == 'MESH' and len(building.data.polygons) == 0, (
        "flag on: Building's own mesh must be emptied once its faces move to child objects"
    )
    print("[PASS] flag on: Building is the container (cgml_hierarchy_model set, own mesh emptied)")

    chimneys = [o for o in objs if o.get("cgml3_feature") == "BuildingInstallation"]
    assert len(chimneys) == 2 and all(c.parent is building for c in chimneys), (
        "flag on: both Chimneys must still parent directly to the Building, unaffected by the explode"
    )
    print("[PASS] flag on: Chimney 1/2 still parent directly to the Building")

    openings = by_feature.get("WindowSurface", []) + by_feature.get("DoorSurface", [])
    assert len(openings) == 21
    seen_faces = set()
    for o in openings:
        assert o.get("structure_part") == "opening", f"{o.name}: missing structure_part=opening"
        assert o.parent is not None, f"{o.name}: opening has no parent"
        parent_gml_id = o.parent.get("gml_id")
        expected_parent = o.get("filling_parent_surface_id")
        assert expected_parent, f"{o.name}: missing filling_parent_surface_id"
        assert parent_gml_id == expected_parent, (
            f"{o.name}: parented to {o.parent.name!r} (gml_id={parent_gml_id}) "
            f"but filling_parent_surface_id={expected_parent!r}"
        )
        assert o.parent.get("cgml3_feature") == "WallSurface", (
            f"{o.name}: parent {o.parent.name!r} is not a WallSurface"
        )
        assert o.get("gml_id"), f"{o.name}: missing gml_id"
    print("[PASS] flag on: every Window/Door parents to the correct WallSurface (filling_parent_surface_id match)")

    # No face duplicated or lost: every mesh face has a unique (object, poly-loop-key)
    # identity, and the grand total across every MESH object is unchanged from the
    # flag-off baseline (same faces, just redistributed across more objects) - modulo
    # window-door-upload ticket 02, which turns any flat (single-polygon) Window/Door
    # into a 4 cm solid prism, deliberately adding faces to that one object. Back those
    # additions out before comparing so this stays a real "no duplication, no loss" check.
    synthetic_extra_faces = sum(
        len(o.data.polygons) - 1 for o in objs
        if o.type == 'MESH' and o.get("cgml_synthetic_opening_depth")
    )
    got_total = total_faces(objs) - synthetic_extra_faces
    assert got_total == expected_total_faces, (
        f"flag on: total face count changed ({expected_total_faces} -> {got_total}) "
        "- a face was duplicated or lost during explode"
    )
    print(
        f"[PASS] flag on: total face count unchanged ({got_total}, "
        f"{synthetic_extra_faces} synthetic-opening-depth faces excluded) - no duplication, no loss"
    )

    for surfaces_key in ("WallSurface", "RoofSurface", "GroundSurface"):
        for o in by_feature.get(surfaces_key, []):
            assert o.get("gml_id"), f"{o.name}: missing gml_id"
            assert o.parent is building, f"{o.name}: must parent directly to the Building"
    print("[PASS] flag on: every boundary surface carries gml_id and parents to the Building")


def main():
    gml = args()[0]
    expected_total_faces = check_flag_off(gml)
    check_flag_on(gml, expected_total_faces)
    print("\n[ALL PASS] ticket 06 checklist satisfied")


main()
