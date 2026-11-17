"""CAC-P0.2: lock the gml:id / semantic snapshot of CityGML 2 and 3 imports.

For each fixture x {use_semantic_objects on, off} the scene is imported
twice; the object/material snapshot must be identical both times and equal
to golden/cac/import_ids_<case>.json. Independently of the golden, every
WallSurface/RoofSurface/Window/Door (and CityGML 3 WindowSurface/DoorSurface)
gml:id of the source file must be found in the imported scene.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p0_import_ids.py -- <out_dir> [--update-goldens]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

B = h.FIXTURES / "Building"
FIXTURES = [
    ("railway_v2", B / "Railway_Scene_LoD3_CityGML2_Building_only.gml", "2.0"),
    ("railway_v3", B / "Railway_Scene_LoD3_CityGML3_Building_only.gml", "3.0"),
    ("installations_v2", B / "SemanticHierarchy_Installations_LoD3_CityGML2.gml", "2.0"),
    ("installations_v3", B / "SemanticHierarchy_Installations_LoD3_CityGML3.gml", "3.0"),
    ("parts_v2", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml", "2.0"),
    ("parts_v3", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml", "3.0"),
    ("altopening_v3", B / "AltOpeningSpelling_LoD3_CityGML3.gml", "3.0"),
]

OBJECT_KEYS = (
    "gml_id", "cgml3_feature", "cgml_semantic_type", "cgml_source_role",
    "filling_parent_surface_id", "cgml_parent_id", "cgml_opening_link", "lod", "cgml3_lod",
)
MATERIAL_KEYS = (
    "con_surface_id", "gml_polygon_id", "gml_ring_id", "SurfaceTyp",
    "opening_gml_id", "Interior", "ExteriorPolyId", "lod",
)
# Where an imported feature id may live: on its object, or (merged path) on materials.
OBJECT_ID_KEYS = ("gml_id",)
MATERIAL_ID_KEYS = ("con_surface_id", "opening_gml_id")
CHECKED_ELEMENTS = ("WallSurface", "RoofSurface", "Window", "Door", "WindowSurface", "DoorSurface")


def snapshot(path, version, semantic, source_ids):
    """Scene snapshot with import-minted uuid ids renumbered (source ids kept)."""
    h.import_fixture(path, version, use_semantic_objects=semantic)
    return h.normalise_json(h.snapshot_objects(OBJECT_KEYS, MATERIAL_KEYS), source_ids)


def scene_ids(snap):
    ids = set()
    for obj in snap["objects"]:
        ids.update(str(obj["props"][k]) for k in OBJECT_ID_KEYS if k in obj["props"])
        for mat in obj.get("materials") or ():
            if mat:
                ids.update(str(mat["props"][k]) for k in MATERIAL_ID_KEYS if k in mat["props"])
    return ids


def main():
    R = h.Results(__file__)
    h.update_goldens_requested()  # fail early if the switch is misused
    for name, path, version in FIXTURES:
        source = h.collect_ids(path.read_text(encoding="utf-8"))
        all_source_ids = {i for ids in source.values() for i in ids}
        expected = {i for el in CHECKED_ELEMENTS for i in source.get(el, ())}
        for semantic in (True, False):
            case = f"{name}_{'sem' if semantic else 'merged'}"
            first = snapshot(path, version, semantic, all_source_ids)
            second = snapshot(path, version, semantic, all_source_ids)
            R.check(f"{case}_deterministic", *h.compare_json(first, second))
            R.check(f"{case}_golden", *h.compare_golden(f"import_ids_{case}", first))
            missing = sorted(expected - scene_ids(first))
            R.check(
                f"{case}_source_ids",
                bool(expected) and not missing,
                f"{len(missing)} of {len(expected)} source ids not found: {missing[:10]}",
            )
    R.finish()


main()
