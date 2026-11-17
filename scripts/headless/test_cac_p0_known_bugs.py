"""CAC-P0.7: known architectural bugs AB-01..AB-08 as desired-behaviour checks.

Every check asserts what the code *should* do (PLAN.md §7). A check that
fails today is listed in scripts/tests/known_arch_bugs.json, so the strict
runner reports it as xfail; the commit that fixes the bug must remove the
entry, otherwise the suite fails on xpass.

Each scenario starts from a fresh import and is isolated: an exception
fails only its own check, with the traceback tail as detail.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p0_known_bugs.py -- <out_dir>
"""
import os
import sys
import traceback
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V2 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML2_Building_only.gml"
RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
PARTS_V3 = h.FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml"
MIN_OPENING_DEPTH = 0.01  # m; imported/authored openings get 0.04 today
OPENING_FEATURES = ("Window", "Door", "WindowSurface", "DoorSurface")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def element_by_id(xml_text, gml_id):
    root = ET.fromstring(xml_text.encode("utf-8"))
    for elem in root.iter():
        if any(elem.get(a) == gml_id for a in h.GML_IDS):
            return elem
    return None


def rings_and_openings(xml_text, surface_id):
    """(interior ring count, nested opening count) under one exported surface."""
    elem = element_by_id(xml_text, surface_id)
    if elem is None:
        return None, None
    names = [h.local_name(e.tag) for e in elem.iter()]
    return names.count("interior"), sum(names.count(o) for o in h.OPENINGS)


def objects_with_feature(features):
    import bpy

    return [o for o in bpy.data.objects
            if o.type == "MESH" and str(o.get("cgml3_feature") or "") in features]


def building_of(obj):
    while obj is not None and str(obj.get("cgml3_feature") or "") not in ("Building", "BuildingPart"):
        obj = obj.parent
    return obj


def all_props():
    """Every custom property of every object, as plain JSON values."""
    import bpy

    return {o.name: {k: h._plain(o[k]) for k in sorted(o.keys())} for o in bpy.data.objects}


def world_bbox(objects):
    pts = [o.matrix_world @ v.co for o in objects for v in o.data.vertices]
    return [min(p[i] for p in pts) for i in range(3)] + [max(p[i] for p in pts) for i in range(3)]


# --------------------------------------------------------------------------
# scenarios: each returns (ok, detail) for the desired behaviour
# --------------------------------------------------------------------------

def ab01_triangulated_wall_has_no_bogus_interior_rings(out):
    import bmesh

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = next(o for o in objects_with_feature(("WallSurface",))
                if not any(m and m.get("Interior") for m in o.data.materials)
                and any(len(p.vertices) >= 4 for p in o.data.polygons))
    wall_id = str(wall["gml_id"])
    rings_before, _ = rings_and_openings(h.export_scene(out / "ab01_before.gml", "3.0"), wall_id)
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    import bpy
    wall = next(o for o in bpy.data.objects if str(o.get("gml_id") or "") == wall_id)
    bm = bmesh.new()
    bm.from_mesh(wall.data)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(wall.data)
    bm.free()
    try:
        text = h.export_scene(out / "ab01_after.gml", "3.0")
    except RuntimeError as exc:
        return False, f"wall {wall_id}: export refused after triangulation: {str(exc).splitlines()[0]}"
    rings_after, _ = rings_and_openings(text, wall_id)
    return rings_after == rings_before == 0, \
        f"wall {wall_id}: interior rings before={rings_before} after triangulation={rings_after}"


def ab02_deleted_window_leaves_no_orphan_ring(out):
    import bpy

    # Railway walls export 2 interior rings per nested opening (front and back
    # polygon), so "no orphan ring" means that ratio survives the delete.
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    text = h.export_scene(out / "ab02_before.gml", "3.0")
    windows = ("Window", "WindowSurface")
    for wall in objects_with_feature(("WallSurface",)):
        wall_id = str(wall.get("gml_id") or "")
        rings, openings = rings_and_openings(text, wall_id)
        children = [c for c in objects_with_feature(OPENING_FEATURES)
                    if str(c.get("filling_parent_surface_id") or "") == wall_id]
        if (openings and rings % openings == 0 and len(children) == openings
                and any(str(c.get("cgml3_feature")) in windows for c in children)):
            break
    else:
        return False, "precondition: no wall whose openings are all nested and ring-paired"
    per_opening = rings // openings
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    victim = next(c for c in objects_with_feature(windows)
                  if str(c.get("filling_parent_surface_id") or "") == wall_id)
    victim_name = victim.name
    bpy.data.objects.remove(victim)
    rings_after, openings_after = rings_and_openings(h.export_scene(out / "ab02_after.gml", "3.0"), wall_id)
    return (openings_after == openings - 1 and rings_after == per_opening * openings_after), (
        f"wall {wall_id}: before rings={rings} openings={openings}; after deleting "
        f"{victim_name}: rings={rings_after} openings={openings_after} "
        f"(expected rings={per_opening * (openings - 1)})")


def ab03_stamp_from_flat_source_has_depth(out):
    # A flat Window source is made the way a user can make one today: Draw
    # with depth 0 (AB-04). If AB-04 is fixed by refusing depth 0, this setup
    # must switch to another flat source (e.g. a flat CityGML 2 opening).
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    # AB-17 fix: (0.3, 0.5) on this wall/face lands outside its real solid
    # area (Railway arched cut-out) - find a position that actually places.
    source = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.0),
        ctx, PlacementRequest(position_uv=h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1),
                               source="manual_draw"))
    source_depth = h.thickness(source, ctx.normal_world)
    if source_depth >= MIN_OPENING_DEPTH:
        return False, f"precondition: stamp source is not flat (depth={source_depth:.4f})"
    target = (ctx.origin_world + ctx.horizontal_axis_world * (ctx.width * 0.7)
              + ctx.vertical_axis_world * (ctx.height / 2))
    fill = place_semantic_element(source_object=source, surface_context=ctx, position_world=target)
    depth = h.thickness(fill, ctx.normal_world)
    return depth >= MIN_OPENING_DEPTH, \
        f"flat source {source.name} depth={source_depth:.4f}; stamped {fill.name} depth={depth:.4f}"


def ab04_depth0_window_is_not_flat(out):
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    # AB-17 fix: (0.5, 0.5) on this wall/face lands outside its real solid
    # area (Railway arched cut-out) - find a position that actually places.
    try:
        fill = place_semantic_element(
            FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.0),
            ctx, PlacementRequest(position_uv=h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1),
                                   source="manual_draw"))
    except ValueError as exc:
        return True, f"refused: {exc}"  # refusing a flat Window is acceptable
    depth = h.thickness(fill, ctx.normal_world)
    return depth >= MIN_OPENING_DEPTH, f"Window drawn with depth 0 has depth {depth:.4f} (coplanar with wall)"


def ab05_citygml2_import_windows_have_depth(out):
    h.import_fixture(RAILWAY_V2, "2.0", use_semantic_objects=True)
    openings = objects_with_feature(("Window", "Door"))
    if not openings:
        return False, "precondition: CityGML 2 import produced no Window/Door objects"
    flat = {str(o.get("gml_id")): o.name for o in openings
            if h.thickness(o, h.face_normal_world(o)) < MIN_OPENING_DEPTH}
    # control: the same features imported from CityGML 3 get synthetic depth
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    v3_depth = {str(o.get("gml_id")): round(h.thickness(o, h.face_normal_world(o)), 4)
                for o in objects_with_feature(OPENING_FEATURES) if str(o.get("gml_id")) in flat}
    return not flat, (f"{len(flat)} of {len(openings)} CityGML 2 openings are flat: {sorted(flat.values())[:3]}; "
                      f"same ids imported from CityGML 3 have depth {v3_depth}")


def ab06_split_keeps_world_position_under_building_transform(out):
    import bpy
    from mathutils import Vector
    from city_modeler.shared.facade_surface_resolver import split_into_semantic_surfaces

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=False)
    building = objects_with_feature(("Building",))[0]
    building.location = building.location + Vector((100.0, 50.0, 0.0))
    bpy.context.view_layer.update()
    before = world_bbox([building])
    if not split_into_semantic_surfaces(building):
        return False, "precondition: split_into_semantic_surfaces exploded nothing"
    bpy.context.view_layer.update()
    after = world_bbox([o for o in bpy.data.objects if o.type == "MESH" and o.data.vertices])
    shift = max(abs(a - b) for a, b in zip(before, after))
    # openings gain +/- 2 cm synthetic depth, so allow a few cm
    return shift < 0.05, f"world bbox moved by up to {shift:.3f} m after split (before={before}, after={after})"


def ab07_wall_children_pairs_include_floating_door(out):
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )
    from city_modeler.shared.ring_opening_sync import (
        find_ring_opening_pairs, pairs_for_wall_children,
    )

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    # AB-17 fix: (0.5, 0.5) on this wall/face lands outside its real solid
    # area (Railway arched cut-out) - find a position that actually places.
    door = place_semantic_element(
        FacadeElementDefinition(semantic_type="Door", width=0.2, height=0.2, door_touches_ground=False),
        ctx, PlacementRequest(position_uv=h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1),
                               source="manual_draw"))
    in_building = any(p.opening is door for p in find_ring_opening_pairs(building_of(wall)))
    in_local = any(p.opening is door for p in pairs_for_wall_children(wall))
    return in_local, (f"floating Door {door.name}: building-wide pairing={in_building}, "
                      f"pairs_for_wall_children={in_local}")


def ab08_export_does_not_mutate_scene(out):
    # io_v2/writer/exporter.py derives a missing cgml_parent_id from the
    # Blender parent and writes it back onto the object. Fresh imports always
    # set it, so the scene is put in the state "Assign Object Part" leaves:
    # a parented BuildingPart without cgml_parent_id.
    changes = []
    for version in ("2.0", "3.0"):
        h.import_fixture(PARTS_V3, "3.0", use_semantic_objects=True)
        parts = [o for o in objects_with_feature(("BuildingPart",)) if o.parent is not None]
        if not parts:
            return False, "precondition: no parented BuildingPart in fixture"
        for part in parts:
            part.pop("cgml_parent_id", None)
        before = all_props()
        h.export_scene(out / f"ab08_to_v{version[0]}.gml", version)
        after = all_props()
        for name in sorted(set(before) | set(after)):
            b, a = before.get(name, {}), after.get(name, {})
            for key in sorted(set(b) | set(a)):
                if b.get(key, "<absent>") != a.get(key, "<absent>"):
                    changes.append(f"to v{version}: {name}.{key}: {b.get(key, '<absent>')!r} -> "
                                   f"{a.get(key, '<absent>')!r}")
    return not changes, f"{len(changes)} property changes during export, e.g. {changes[:4]}"


SCENARIOS = [
    ab01_triangulated_wall_has_no_bogus_interior_rings,
    ab02_deleted_window_leaves_no_orphan_ring,
    ab03_stamp_from_flat_source_has_depth,
    ab04_depth0_window_is_not_flat,
    ab05_citygml2_import_windows_have_depth,
    ab06_split_keeps_world_position_under_building_transform,
    ab07_wall_children_pairs_include_floating_door,
    ab08_export_does_not_mutate_scene,
]


def main():
    R = h.Results(__file__)
    out = h.out_dir()
    for scenario in SCENARIOS:
        try:
            ok, detail = scenario(out)
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-6:])
        R.check(scenario.__name__, ok, detail)
    R.finish()


main()
