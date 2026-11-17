"""CAC-P1.6: lock UI semantic panel output for imported buildings.

Captures what ``ui/citygml_attributes_panel.py``'s helper functions
(get_feature_type, get_gml_id, get_semantic_type, get_lod_level,
get_feature_attributes, get_boundary_surfaces) return for every object of
the P0.2 scenes (semantic objects on), so the second CAC-P1.6 commit
(routing those helpers through read_feature_view) can be checked
byte-for-byte against golden/cac/ui_panel_v2.json / ui_panel_v3.json with
the experimental flag off (PLAN.md R6).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p1_ui_panel.py -- <out_dir> [--update-goldens]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

B = h.FIXTURES / "Building"
FIXTURES = {
    "2.0": [
        ("railway_v2", B / "Railway_Scene_LoD3_CityGML2_Building_only.gml"),
        ("installations_v2", B / "SemanticHierarchy_Installations_LoD3_CityGML2.gml"),
        ("parts_v2", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml"),
    ],
    "3.0": [
        ("railway_v3", B / "Railway_Scene_LoD3_CityGML3_Building_only.gml"),
        ("installations_v3", B / "SemanticHierarchy_Installations_LoD3_CityGML3.gml"),
        ("parts_v3", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml"),
        ("altopening_v3", B / "AltOpeningSpelling_LoD3_CityGML3.gml"),
    ],
}


def panel_snapshot():
    import bpy

    from city_modeler.ui import citygml_attributes_panel as panel

    objects = []
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        semantic_type, semantic_origin = panel.get_semantic_type(obj)
        objects.append({
            "name": obj.name,
            "feature_type": panel.get_feature_type(obj),
            "gml_id": panel.get_gml_id(obj),
            "semantic_type": semantic_type,
            "semantic_origin": semantic_origin,
            "lod_level": panel.get_lod_level(obj),
            "feature_attributes": panel.get_feature_attributes(obj),
            "boundary_surfaces": panel.get_boundary_surfaces(obj),
        })
    return {"objects": objects}


def main():
    R = h.Results(__file__)
    h.update_goldens_requested()  # fail early if the switch is misused
    for version, fixtures in FIXTURES.items():
        case = "v2" if version == "2.0" else "v3"
        source_ids = set()
        for _, path in fixtures:
            source = h.collect_ids(path.read_text(encoding="utf-8"))
            source_ids.update(i for ids in source.values() for i in ids)
        data = {}
        for name, path in fixtures:
            h.import_fixture(path, version, use_semantic_objects=True)
            data[name] = h.normalise_json(panel_snapshot(), source_ids)
        R.check(f"ui_panel_{case}_golden", *h.compare_golden(f"ui_panel_{case}", data))
    R.finish()


main()
