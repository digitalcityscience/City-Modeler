"""CAC-P5.6: v2/v3 writer adapter + per-Building IR dispatch, real XML export.

Scope: the actual wiring in ``io/writer/exporter.py`` and ``io_v2/writer/
exporter.py`` (``_write_ir_building_surfaces`` + the per-Building dispatch
around ``cac_export_dispatch.building_export_plan``) - real Blender export,
real XML, real xmllint validation. Builds the same minimal hand-committed
scene as ``test_cac_p5_6_export_dispatch.py`` (see that file's docstring
for why a real fixture import isn't used here) plus a second, deliberately
ineligible Building, so a single export exercises the mixed-scene case.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_6_writer_dispatch.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))
from shared.authoring_core.blender import storage  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
DOOR_COORDS = ((2.0, 0.0, 0.0), (2.8, 0.0, 0.0), (2.8, 0.0, 2.0), (2.0, 0.0, 2.0))

LEGACY_WORLD_EXT = ((20.0, 0.0, 0.0), (24.0, 0.0, 0.0), (24.0, 3.0, 0.0), (20.0, 3.0, 0.0))


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _building_object(name, **props):
    """A Building object shaped the way the semantic-hierarchy explode
    (ticket 06/07, ``io/writer/exporter.py::_collect_semantic_hierarchy_
    children``) needs to recognize it as a Building whose geometry comes
    from its WallSurface/Window/Door child objects: a MESH object (an
    empty/no-polygon mesh is fine - its own faces are never used once it
    has semantic children) marked ``cgml_hierarchy_model=
    "semantic_objects"``. Without this the writer treats each child mesh
    as its own independent top-level feature instead of a Building."""
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml_hierarchy_model"] = "semantic_objects"
    for key, value in props.items():
        obj[key] = value
    return obj


def _mesh_object(name, coords, parent=None, polygon_id=None, **props):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(coords), [], [list(range(len(coords)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    if polygon_id is not None:
        mat = bpy.data.materials.new(f"{name}_mat")
        mat["gml_polygon_id"] = polygon_id
        mesh.materials.append(mat)
    for key, value in props.items():
        obj[key] = value
    return obj


def build_ir_building(with_door=False):
    """Building1/WALL1/WIN_A - committed records, IR-eligible with the flag on."""
    building_obj = _building_object("Building1", gml_id="BLD1", cgml3_feature="Building")
    wall_obj = _mesh_object(
        "Wall1", WORLD_EXT, parent=building_obj, polygon_id="P_WALL",
        gml_id="WALL1", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    window_obj = _mesh_object(
        "Window1", WIN_EXT, parent=wall_obj, polygon_id="P_WIN_A",
        gml_id="WIN_A", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL1", cgml_opening_link="LINK_A",
    )

    storage.write_record(building_obj, RecordSet(features=(FeatureRecord(gml_id="BLD1", kind="Building"),)))

    wall_record = FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord("P_WALL_0_", "exterior", WORLD_EXT),
        interiors=(RingRecord("P_WALL_1_", "interior", RING_A, opening_link="LINK_A"),),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))

    window_record = FeatureRecord(
        gml_id="WIN_A", kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link="LINK_A", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN_A",),
    )
    window_polygon = PolygonRecord(
        polygon_id="P_WIN_A", feature_id="WIN_A", geometry_state="committed",
        exterior=RingRecord("P_WIN_A_0_", "exterior", WIN_EXT),
    )
    storage.write_record(window_obj, RecordSet(features=(window_record,), polygons=(window_polygon,)))

    door_obj = None
    if with_door:
        door_obj = _mesh_object(
            "Door1", DOOR_COORDS, parent=wall_obj,
            gml_id="DOOR_A", cgml3_feature="Door", structure_part="opening",
            filling_parent_surface_id="WALL1", cgml3_lod=3,
        )

    return building_obj, wall_obj, window_obj, door_obj


RESIZED_WIN_EXT = ((0.5, 0.5, 0.0), (2.0, 0.5, 0.0), (2.0, 2.0, 0.0), (0.5, 2.0, 0.0))


def resize_window_keep_id(window_obj, new_coords=RESIZED_WIN_EXT):
    """Mirror an F9 resize at the record level (CAC-P4.3): same Window
    ``gml_id``/``polygon_id``/``ring_id``, new committed geometry - the
    scenario item 21/AB-18 asks P5.6 to prove stays stable through the IR
    export path (unlike the legacy exporter's own material/face identity
    scan, which is the AB-18 bug this Building's IR eligibility sidesteps)."""
    mesh = bpy.data.meshes.new(window_obj.name + "_resized")
    mesh.from_pydata(list(new_coords), [], [list(range(len(new_coords)))])
    mesh.update()
    mat = bpy.data.materials.new(window_obj.name + "_resized_mat")
    mat["gml_polygon_id"] = "P_WIN_A"
    mesh.materials.append(mat)
    old_mesh = window_obj.data
    window_obj.data = mesh
    bpy.data.meshes.remove(old_mesh)

    window_polygon = PolygonRecord(
        polygon_id="P_WIN_A", feature_id="WIN_A", geometry_state="committed",
        exterior=RingRecord("P_WIN_A_0_", "exterior", new_coords),
    )
    window_record = FeatureRecord(
        gml_id="WIN_A", kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link="LINK_A", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN_A",),
    )
    storage.write_record(window_obj, RecordSet(features=(window_record,), polygons=(window_polygon,)))
    bpy.context.view_layer.update()


def build_legacy_building():
    """Building2/WALL2 - a plain legacy WallSurface with no stored record at
    all: always ineligible, always the legacy export path, flag on or off."""
    building_obj = _building_object("Building2", gml_id="BLD2", cgml3_feature="Building")
    wall_obj = _mesh_object(
        "Wall2", LEGACY_WORLD_EXT, parent=building_obj, polygon_id="P_WALL2",
        gml_id="WALL2", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_obj


def build_scene(with_door=False, with_legacy_building=False):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    ir = build_ir_building(with_door=with_door)
    legacy = build_legacy_building() if with_legacy_building else None
    bpy.context.view_layer.update()
    return ir, legacy


def _export(version):
    out = h.out_dir() / f"p5_6_{version.replace('.', '')}_{os.getpid()}.gml"
    text = h.export_scene(str(out), version)
    return out, text


def _run_matrix(r, version):
    tag = f"v{version.split('.')[0]}"
    # CityGML 3 nests openings as con:WindowSurface/con:DoorSurface; CityGML 2
    # uses the plain vocabulary bldg:Window/bldg:Door - the writer's own
    # element-tag translation (_write_inline_surface_opening's per-version
    # implementation), not something this test's ids need to second-guess.
    window_tag = "WindowSurface" if version == "3.0" else "Window"
    door_tag = "DoorSurface" if version == "3.0" else "Door"

    # 1. flag OFF: eligible-shaped Building still exports via the legacy path
    _flag(False)
    build_scene()
    out_off, text_off = _export(version)
    r.check(
        f"{tag} flag off: WindowSurface gml:id is not the bare record id "
        "(legacy minting still runs, item 2)",
        'gml:id="WIN_A"' not in text_off,
        text_off[:200],
    )
    lint_off = h.xmllint(out_off, version)
    r.check(f"{tag} flag off: export is xmllint-clean", lint_off["exit"] == 0, str(lint_off))

    # 2. flag ON, eligible Building: WallSurface/WindowSurface carry record ids
    _flag(True)
    build_scene()
    out_on, text_on = _export(version)
    r.check(f"{tag} flag on: WallSurface gml:id equals the record id", 'gml:id="WALL1"' in text_on, text_on[:400])
    r.check(f"{tag} flag on: WindowSurface gml:id equals the record id", 'gml:id="WIN_A"' in text_on)
    lint_on = h.xmllint(out_on, version)
    r.check(f"{tag} flag on: export is xmllint-clean", lint_on["exit"] == 0, str(lint_on))

    ids = h.collect_ids(text_on)
    wall_ids = ids.get("WallSurface", ())
    window_ids = ids.get(window_tag, ())
    r.check(f"{tag} exactly one WallSurface with the record id", list(wall_ids) == ["WALL1"], str(wall_ids))
    r.check(f"{tag} exactly one {window_tag} with the record id", list(window_ids) == ["WIN_A"], str(window_ids))

    # 2b. AB-18 regression (task item 21): an F9-style resize (same record
    # gml_id, new committed geometry) of an IR-eligible Window exports the
    # *same* XML gml:id before and after - unlike the legacy exporter's own
    # material/face identity scan (known_arch_bugs.json "AB-18"), which this
    # Building's IR eligibility bypasses entirely.
    ir, _legacy = build_scene()
    _building_obj, _wall_obj, window_obj, _door_obj = ir
    _, text_before_resize = _export(version)
    ids_before = list(h.collect_ids(text_before_resize).get(window_tag, ()))
    resize_window_keep_id(window_obj)
    _, text_after_resize = _export(version)
    ids_after = list(h.collect_ids(text_after_resize).get(window_tag, ()))
    r.check(
        f"{tag} AB-18: exported {window_tag} gml:id is stable across an F9-style resize",
        ids_before == ["WIN_A"] and ids_after == ["WIN_A"],
        f"before={ids_before} after={ids_after}",
    )

    # 3. mixed scene: IR Building + legacy Building both present, distinct ids
    _flag(True)
    build_scene(with_legacy_building=True)
    out_mixed, text_mixed = _export(version)
    lint_mixed = h.xmllint(out_mixed, version)
    r.check(f"{tag} mixed scene: export is xmllint-clean", lint_mixed["exit"] == 0, str(lint_mixed))
    r.check(
        f"{tag} mixed scene: IR Building's WallSurface uses the record id",
        'gml:id="WALL1"' in text_mixed,
    )
    r.check(
        f"{tag} mixed scene: legacy Building's WallSurface never uses the record id "
        "(it has none - always legacy minting)",
        'gml:id="WALL2"' not in text_mixed,
        text_mixed[:400],
    )
    mixed_ids = h.collect_ids(text_mixed)
    r.check(
        f"{tag} mixed scene: two distinct WallSurface entries (one IR, one legacy)",
        len(mixed_ids.get("WallSurface", ())) == 2,
        str(mixed_ids.get("WallSurface")),
    )

    # 4. Door passthrough: eligible IR Building + legacy Door, both present
    _flag(True)
    build_scene(with_door=True)
    out_door, text_door = _export(version)
    lint_door = h.xmllint(out_door, version)
    r.check(f"{tag} Door passthrough export is xmllint-clean", lint_door["exit"] == 0, str(lint_door))
    r.check(f"{tag} Door passthrough: {window_tag} still present with its record id", 'gml:id="WIN_A"' in text_door)
    door_ids = h.collect_ids(text_door)
    r.check(
        f"{tag} Door passthrough: exactly one {door_tag}, host wall unaffected",
        len(door_ids.get(door_tag, ())) == 1,
        str(door_ids.get(door_tag)),
    )

    # 5. P5.10/G5 item 6 combined proof: IR Building + Door passthrough and a
    # legacy-fallback Building together in one export. Two Buildings is the
    # minimal scene that exercises this - a third, bare-IR (no Door) Building
    # would dispatch through the exact same per-Building `building_export_
    # plan` branch already proven independent of Door presence by check 2
    # above, so it would not add coverage, only a redundant object.
    _flag(True)
    build_scene(with_door=True, with_legacy_building=True)
    out_combo, text_combo = _export(version)
    lint_combo = h.xmllint(out_combo, version)
    r.check(f"{tag} combined scene: export is xmllint-clean", lint_combo["exit"] == 0, str(lint_combo))
    combo_ids = h.collect_ids(text_combo)
    r.check(
        f"{tag} combined scene: both Buildings appear exactly once each",
        combo_ids.get("Building", ()) == ["BLD1", "BLD2"],
        str(combo_ids.get("Building")),
    )
    # The legacy Building's WallSurface id is legacy-minted (no record, same
    # fact the "mixed scene" check above already documents) - it is never
    # literally "WALL2", so this only asserts count and the IR id, not the
    # legacy-minted id's exact text.
    r.check(
        f"{tag} combined scene: two distinct WallSurfaces, no duplication",
        len(combo_ids.get("WallSurface", ())) == 2 and "WALL1" in combo_ids["WallSurface"],
        str(combo_ids.get("WallSurface")),
    )
    r.check(
        f"{tag} combined scene: exactly one {window_tag} (IR Building only)",
        len(combo_ids.get(window_tag, ())) == 1 and combo_ids[window_tag][0] == "WIN_A",
        str(combo_ids.get(window_tag)),
    )
    r.check(
        f"{tag} combined scene: exactly one {door_tag}, not duplicated onto the legacy Building",
        len(combo_ids.get(door_tag, ())) == 1,
        str(combo_ids.get(door_tag)),
    )
    r.check(
        f"{tag} combined scene: no id appears under more than one feature type "
        "(ids are Building-scoped correctly, no cross-Building leakage)",
        len({i for ids in combo_ids.values() for i in ids}) == sum(len(ids) for ids in combo_ids.values()),
        str(combo_ids),
    )


def main():
    r = h.Results(__file__)
    h.load_addon()

    _run_matrix(r, "3.0")
    _run_matrix(r, "2.0")

    r.finish()


if __name__ == "__main__":
    main()
