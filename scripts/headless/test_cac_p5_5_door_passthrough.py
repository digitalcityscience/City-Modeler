"""CAC-P5.5/P5.6: legacy Door passthrough extraction (bpy side).

Scope: ``shared/authoring_core/blender/door_passthrough.py::
extract_door_passthrough`` only - the bpy-dependent extraction half of the
P5.5 seam. The pure merge half (``domain/ir_to_groups.py::merge_door_
passthrough``) is covered without bpy by
``scripts/tests/unit/test_cac_p5_5_door_passthrough_merge.py``.

This script builds its own minimal scene directly with bpy (two WallSurface
mesh objects with legacy Door/Window child meshes carrying the same custom
properties the importer/Openings Cutter stamp today) rather than importing
a fixture .gml, since P5.5 only needs to prove the extraction contract, not
a full round-trip.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_5_door_passthrough.py -- <out_dir>

Not run in this environment (no Blender executable available) - see the
P5.5 task card for what remains for later headless validation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))
from shared.authoring_core.blender import door_passthrough as dp  # noqa: E402
from shared.authoring_core.blender import storage  # noqa: E402
from shared.authoring_core.domain.ir_to_groups import ExportOpening  # noqa: E402


def _mesh_object(name, coords, **props):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(coords), [], [list(range(len(coords)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for key, value in props.items():
        obj[key] = value
    return obj


DOOR_COORDS = ((1.0, 0.0, 0.0), (1.8, 0.0, 0.0), (1.8, 0.0, 2.0), (1.0, 0.0, 2.0))
WINDOW_COORDS = ((2.5, 0.0, 0.5), (3.3, 0.0, 0.5), (3.3, 0.0, 1.3), (2.5, 0.0, 1.3))


def build_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    door_a = _mesh_object(
        "DoorA", DOOR_COORDS,
        gml_id="DOOR_A", cgml3_feature="Door", structure_part="opening",
        opening_surface_id="WALL1", cgml3_lod=2,
    )
    door_b = _mesh_object(
        "DoorB", DOOR_COORDS,
        gml_id="DOOR_B", cgml3_feature="Door", structure_part="opening",
        filling_parent_surface_id="WALL1", cgml3_lod=2,
    )
    # A record-backed Window: must never be extracted by the Door adapter.
    window = _mesh_object(
        "WindowA", WINDOW_COORDS,
        gml_id="WIN_A", cgml3_feature="Window", structure_part="opening",
        opening_surface_id="WALL1",
    )
    window[storage.RECORD_KEY] = "{}"  # malformed on purpose: RECORD_MALFORMED must still be skipped
    # A legacy Door with no host relation: extraction must refuse it.
    orphan = _mesh_object(
        "DoorOrphan", DOOR_COORDS,
        gml_id="DOOR_ORPHAN", cgml3_feature="Door", structure_part="opening",
    )
    bpy.context.view_layer.update()
    return door_a, door_b, window, orphan


def main():
    r = h.Results(__file__)
    h.load_addon()

    door_a, door_b, window, orphan = build_scene()

    # 1-2. legacy Door extracted into plain data; gml_id preserved -----------
    openings = dp.extract_door_passthrough(objects=[door_a, door_b])
    ids = [o.gml_id for o in openings]
    r.check("both legacy Doors extracted", ids == ["DOOR_A", "DOOR_B"], str(ids))
    r.check("every entry is a plain ExportOpening", all(isinstance(o, ExportOpening) for o in openings))

    # 3. Door host relation preserved (both key spellings) -------------------
    hosts = {o.gml_id: o.host_id for o in openings}
    r.check("host relation preserved for both key spellings", hosts == {"DOOR_A": "WALL1", "DOOR_B": "WALL1"}, str(hosts))

    # 4. Door polygon geometry preserved --------------------------------------
    door_a_opening = next(o for o in openings if o.gml_id == "DOOR_A")
    got_coords = door_a_opening.polygons[0].exterior.coords
    r.check(
        "Door polygon geometry preserved (world coords, closed ring)",
        len(got_coords) == len(DOOR_COORDS) + 1 and got_coords[0] == got_coords[-1],
        str(got_coords),
    )

    # 5. Door LoD preserved ----------------------------------------------------
    r.check("Door LoD preserved", door_a_opening.lod == 2, str(door_a_opening.lod))

    # 6. record-backed Window is never extracted -------------------------------
    all_openings = dp.extract_door_passthrough(objects=[door_a, door_b, window])
    r.check(
        "record-backed/malformed-record Window never extracted",
        {o.gml_id for o in all_openings} == {"DOOR_A", "DOOR_B"},
        str([o.gml_id for o in all_openings]),
    )

    # 10. no Blender object/material reference in output -----------------------
    door_dict = door_a_opening.to_dict()
    flat = str(door_dict)
    r.check(
        "no bpy object/material leaks into to_dict() output",
        "bpy.data" not in flat and "bpy.types" not in flat,
        flat[:200],
    )

    # 11. two Doors on one host remain distinct ---------------------------------
    r.check("two Doors on one host stay distinct entries", len(openings) == 2)

    # 13. deterministic ordering of multiple Doors -------------------------------
    reordered = dp.extract_door_passthrough(objects=[door_b, door_a])
    r.check(
        "extraction order is deterministic regardless of input order",
        [o.gml_id for o in reordered] == ["DOOR_A", "DOOR_B"],
    )

    # A Door with no host relation refuses rather than silently dropping -------
    raised = False
    try:
        dp.extract_door_passthrough(objects=[orphan])
    except dp.DoorPassthroughError:
        raised = True
    r.check("Door with no host relation refuses (DoorPassthroughError)", raised)

    # P5.6 item 8/11: allowed_ids restricts extraction to one Building's own
    # Door membership (ExportEligibility.door_passthrough_features), so a
    # scene-wide scan can never pull a Door belonging to another Building.
    only_a = dp.extract_door_passthrough(objects=[door_a, door_b], allowed_ids={"DOOR_A"})
    r.check(
        "allowed_ids restricts extraction to the given Door gml_ids",
        [o.gml_id for o in only_a] == ["DOOR_A"],
        str([o.gml_id for o in only_a]),
    )
    none_allowed = dp.extract_door_passthrough(objects=[door_a, door_b], allowed_ids=set())
    r.check("empty allowed_ids extracts nothing", none_allowed == ())
    unrestricted = dp.extract_door_passthrough(objects=[door_a, door_b], allowed_ids=None)
    r.check(
        "allowed_ids=None keeps pre-P5.6 unrestricted behaviour",
        [o.gml_id for o in unrestricted] == ["DOOR_A", "DOOR_B"],
    )

    r.finish()


if __name__ == "__main__":
    main()
