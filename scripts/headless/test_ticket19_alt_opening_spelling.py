"""Ticket 19: prove the SECOND CityGML 3 opening spelling is genuinely covered.

Ticket 06 requires both opening spellings the schema permits:

    con:fillingSurface/con:WindowSurface   (proven by test_semantic_explode.py)
    con:filling/con:Window                 (supported, but only proven here)

This imports test_files/Building/AltOpeningSpelling_LoD3_CityGML3.gml - a
Building whose one WallSurface carries a Window and a Door nested under
con:filling (the second spelling) - with use_semantic_objects on, and asserts
the openings explode into their own objects, carry structure_part=opening, and
parent to the correct WallSurface. It fails if con:filling/con:Window support
is removed (verified by temporarily dropping con:Window/con:Door from
_FILLING_TAGS), which is what makes this genuine coverage rather than a
tautology.

Usage (see docs/agent/2026-07-16-semantic-hierarchy-investigation.md for why a
private staged scripts dir is required):

    SP=/tmp/cgml-probe
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket19_alt_opening_spelling.py -- \\
      test_files/Building/AltOpeningSpelling_LoD3_CityGML3.gml
"""
import sys

import bpy

ADDON = "city_modeler"

# The fixture's one WallSurface, its one Window, and its one Door - all parsed
# from the con:filling/con:Window spelling. Openings surface as cgml3_feature
# "Window"/"Door" (the source local name verbatim), the second spelling's
# counterpart to "WindowSurface"/"DoorSurface".
WALL_GML_ID = "UUID_wall_altspelling_1"
EXPECTED_OPENINGS = {"Window": 1, "Door": 1}


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
    gml = args()[0]

    # Baseline: flag off leaves geometry flat, so nothing explodes. Records the
    # grand total face count to prove the explode neither duplicates nor loses
    # a face.
    objs_off = fresh_import(gml, False)
    baseline_faces = total_faces(objs_off)
    assert baseline_faces > 0, "flag off: fixture produced no geometry at all"
    print(f"[PASS] flag off: fixture imports flat ({baseline_faces} faces total)")

    objs = fresh_import(gml, True)
    by_feature = {}
    for o in objs:
        by_feature.setdefault(o.get("cgml3_feature"), []).append(o)

    # The Window and Door under con:filling must each become their own object.
    for feature, expected_count in EXPECTED_OPENINGS.items():
        got = by_feature.get(feature, [])
        assert len(got) == expected_count, (
            f"con:filling/con:{feature} spelling not exploded: "
            f"expected {expected_count} {feature!r} object(s), got {len(got)}. "
            f"Present features: { {k: len(v) for k, v in by_feature.items()} }"
        )
    print(f"[PASS] con:filling openings exploded into objects {EXPECTED_OPENINGS}")

    # The single WallSurface must exist as an object and own both openings.
    walls = by_feature.get("WallSurface", [])
    assert len(walls) == 1, f"expected 1 WallSurface object, got {len(walls)}"
    wall = walls[0]
    assert wall.get("gml_id") == WALL_GML_ID, (
        f"WallSurface gml_id mismatch: {wall.get('gml_id')!r} != {WALL_GML_ID!r}"
    )

    openings = by_feature.get("Window", []) + by_feature.get("Door", [])
    for o in openings:
        assert o.get("structure_part") == "opening", (
            f"{o.name}: con:filling opening missing structure_part=opening"
        )
        assert o.get("filling_parent_surface_id") == WALL_GML_ID, (
            f"{o.name}: filling_parent_surface_id={o.get('filling_parent_surface_id')!r} "
            f"but the con:filling opening fills {WALL_GML_ID!r}"
        )
        assert o.parent is wall, (
            f"{o.name}: parented to {o.parent.name if o.parent else None!r}, "
            f"not to its WallSurface {wall.name!r}"
        )
        assert o.get("gml_id"), f"{o.name}: missing gml_id"
    print("[PASS] every con:filling opening carries structure_part=opening and parents to the WallSurface")

    # No face duplicated or lost by the explode - modulo window-door-upload
    # ticket 02, which turns this fixture's flat (single-polygon) Window and
    # Door into 4 cm solid prisms, deliberately adding faces to those two
    # objects. Back those additions out before comparing.
    synthetic_extra_faces = sum(
        len(o.data.polygons) - 1 for o in objs
        if o.type == 'MESH' and o.get("cgml_synthetic_opening_depth")
    )
    got_faces = total_faces(objs) - synthetic_extra_faces
    assert got_faces == baseline_faces, (
        f"total face count changed ({baseline_faces} -> {got_faces}) - "
        "a face was duplicated or lost exploding the con:filling openings"
    )
    print(
        f"[PASS] total face count unchanged ({got_faces}, "
        f"{synthetic_extra_faces} synthetic-opening-depth faces excluded) - no duplication, no loss"
    )

    print("\n[ALL PASS] ticket 19: con:filling/con:Window spelling is genuinely covered")


main()
