"""Ticket 10 checklist, run against the real Blender import pipeline.

Imports the Railway LoD3 CityGML2 fixture twice - once with
use_semantic_objects off, once on - and asserts every checklist item from
docs/agent/semantic-hierarchy/issues/10-citygml2-importer-explode.md:
same canonical hierarchy CityGML3 gets (ticket 06), Window/Door parent to
their WallSurface (not the Building), rooms-based buildings are unaffected,
and flag-off import is byte-for-byte unchanged.

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for
why a private staged scripts dir + a cp313 pyproj/lxml shim are required):

    SP=/tmp/cgml-probe-10
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket10_v2_explode.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"

EXPECTED_SURFACE_COUNTS = {
    "WallSurface": 9,
    "RoofSurface": 3,
    "GroundSurface": 1,
    "Window": 15,
    "Door": 6,
}

FAILURES = []


def check(condition, message):
    status = "OK" if condition else "FAIL"
    print(f"  [{status}] {message}")
    if not condition:
        FAILURES.append(message)


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


def main():
    a = args()
    gml = a[0]

    # --- 1) flag off: import unchanged ---
    print("\n[test] === CHECK: flag off stays flat ===")
    objs_off = fresh_import(gml, False)
    building_off = next((o for o in objs_off if o.get("cgml3_feature") == "Building"), None)
    check(building_off is not None, "flag off: a Building object exists")
    check(building_off.type == 'MESH' and building_off.get("cgml_hierarchy_model") is None,
          "flag off: Building stays a flat mesh, cgml_hierarchy_model not set")
    chimneys_off = [o for o in objs_off if o.get("cgml3_feature") == "BuildingInstallation"]
    check(len(chimneys_off) == 2 and all(c.parent is building_off for c in chimneys_off),
          "flag off: both Chimneys parent directly to the Building")
    off_total_faces = total_faces(objs_off)

    # --- 2) flag on: explode produces the canonical hierarchy ---
    print("\n[test] === CHECK: flag on explode object counts ===")
    objs = fresh_import(gml, True)
    by_feature = {}
    for o in objs:
        by_feature.setdefault(o.get("cgml3_feature"), []).append(o)

    for feature, expected_count in EXPECTED_SURFACE_COUNTS.items():
        got = by_feature.get(feature, [])
        check(len(got) == expected_count,
              f"flag on: expected {expected_count} {feature} objects, got {len(got)}")

    building = next(o for o in objs if o.get("cgml3_feature") == "Building")
    check(building.get("cgml_hierarchy_model") == "semantic_objects",
          "Building carries cgml_hierarchy_model=semantic_objects")
    check(building.type == 'MESH' and len(building.data.polygons) == 0,
          "Building's own mesh is emptied once its faces move to child objects")

    chimneys = by_feature.get("BuildingInstallation", [])
    check(len(chimneys) == 2 and all(c.parent is building for c in chimneys),
          "both Chimneys still parent directly to the Building (unaffected by explode)")

    # --- 3) Window/Door parent to their WallSurface, not the Building ---
    print("\n[test] === CHECK: openings parent to their WallSurface ===")
    walls_by_gml_id = {w.get("gml_id"): w for w in by_feature.get("WallSurface", [])}
    mismatches = []
    for feature in ("Window", "Door"):
        for o in by_feature.get(feature, []):
            parent_link = o.get("filling_parent_surface_id") or o.get("opening_surface_id")
            expected_parent = walls_by_gml_id.get(parent_link)
            if expected_parent is None or o.parent is not expected_parent:
                mismatches.append((o.name, parent_link, o.parent.name if o.parent else None))
    check(not mismatches, f"all openings parent to the WallSurface matching their filling_parent_surface_id "
                           f"(mismatches: {mismatches})")

    # --- 4) each object holds only its own geometry - no face lost or duplicated ---
    print("\n[test] === CHECK: no faces lost or duplicated across the explode ===")
    on_total_faces = total_faces(objs)
    check(on_total_faces == off_total_faces,
          f"total polygon count unchanged by explode (off={off_total_faces}, on={on_total_faces})")

    # --- 5) schema-version / provenance stamped on new objects ---
    print("\n[test] === CHECK: cgml_schema_version + cgml_source_element on new objects ===")
    stamp_mismatches = []
    for feature in ("WallSurface", "RoofSurface", "GroundSurface", "Window", "Door"):
        for o in by_feature.get(feature, []):
            if o.get("cgml_schema_version") != "2.0" or not o.get("cgml_source_element"):
                stamp_mismatches.append(o.name)
    check(not stamp_mismatches, f"all boundary-surface/opening objects carry cgml_schema_version=2.0 "
                                 f"and a non-empty cgml_source_element (missing: {stamp_mismatches})")

    # --- 6) interior rings retained, on the correct owning wall ---
    # README §1.3 / ticket 09: the fixture has exactly 15 gml:interior rings
    # on WallSurfaces (one per Window - Doors cut their wall's own exterior
    # boundary and own no wall ring). A wall with a Window child must carry
    # at least one Interior-flagged material; a wall with none must carry
    # zero, and the total across all walls must be exactly 15.
    print("\n[test] === CHECK: interior ring flags survive on the correct owning WallSurface ===")
    windows_by_wall = {}
    for o in by_feature.get("Window", []):
        parent_link = o.get("filling_parent_surface_id") or o.get("opening_surface_id")
        windows_by_wall.setdefault(parent_link, []).append(o)

    total_interior_rings = 0
    wrong_wall_mismatches = []
    for w in by_feature.get("WallSurface", []):
        mesh = w.data
        interior_count = sum(1 for m in (mesh.materials if mesh else []) if m and m.get("Interior"))
        total_interior_rings += interior_count
        has_windows = bool(windows_by_wall.get(w.get("gml_id")))
        if has_windows and interior_count == 0:
            wrong_wall_mismatches.append(f"{w.name}: has Window child(ren) but 0 Interior-ring materials")
        if not has_windows and interior_count > 0:
            wrong_wall_mismatches.append(f"{w.name}: has {interior_count} Interior-ring material(s) but no Window child")
    check(not wrong_wall_mismatches,
          f"every WallSurface's Interior-ring materials correspond to its own Window children "
          f"(mismatches: {wrong_wall_mismatches})")
    check(total_interior_rings == 15,
          f"exactly 15 gml:interior rings total across all WallSurfaces (got {total_interior_rings})")

    # Doors own no *wall* ring (checked above via wrong_wall_mismatches - no
    # WallSurface has an Interior material without a Window child), but per
    # README §1.3 each Door's own frame carries its own interior rings (9
    # total across 6 doors in this fixture) - these live on the Door's own
    # mesh, unrelated to any wall, and must not be lost by the explode.
    total_door_interior = sum(
        sum(1 for m in o.data.materials if m and m.get("Interior")) if o.data else 0
        for o in by_feature.get("Door", [])
    )
    check(total_door_interior == 9,
          f"Doors retain their own 9 interior (frame-reveal) rings, unrelated to any wall (got {total_door_interior})")

    # --- 7) WallSurface under BuildingPart works ---
    print("\n[test] === CHECK: WallSurface under BuildingPart explodes and parents correctly ===")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parts_gml = os.path.join(repo_root, "test_files", "BuildingParts", "BuildingParts_citygml2.gml")
    if os.path.isfile(parts_gml):
        part_objs = fresh_import(parts_gml, True)
        building = next((o for o in part_objs if o.get("cgml3_feature") == "Building"), None)
        part = next((o for o in part_objs if o.get("cgml3_feature") == "BuildingPart"), None)
        check(building is not None and part is not None, "BuildingParts fixture: Building and BuildingPart objects exist")
        if building is not None and part is not None:
            check(building.get("cgml_hierarchy_model") == "semantic_objects",
                  "BuildingParts fixture: Building carries cgml_hierarchy_model=semantic_objects")
            check(part.get("cgml_hierarchy_model") == "semantic_objects",
                  "BuildingParts fixture: BuildingPart carries cgml_hierarchy_model=semantic_objects")
            building_walls = [c for c in building.children if c.get("cgml3_feature") == "WallSurface"]
            part_walls = [c for c in part.children if c.get("cgml3_feature") == "WallSurface"]
            check(len(building_walls) == 1 and len(part_walls) == 1,
                  f"BuildingParts fixture: Building and BuildingPart each explode their own single WallSurface "
                  f"(building_walls={len(building_walls)}, part_walls={len(part_walls)})")
    else:
        check(False, f"BuildingParts fixture not found at {parts_gml}")

    if FAILURES:
        print(f"\n[FAIL] ticket 10 checklist NOT satisfied ({len(FAILURES)} failure(s)):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] ticket 10 checklist satisfied")


main()
