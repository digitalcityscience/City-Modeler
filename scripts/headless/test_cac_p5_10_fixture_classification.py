# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P5.10 / G5: real-fixture promotion + IR-eligibility classification.

Not a pass/fail suite in the usual sense - PLAN.md's frozen G5 contract
(task item 2) asks for a classification of each real Building fixture into
one of ``IR_FULL`` / ``LEGACY_FALLBACK`` / ``UNSUPPORTED_BY_PHASE5`` /
``INVALID_FIXTURE``, not for every fixture to become IR-eligible. This
script imports each candidate real ``test_files/Building/*.gml`` fixture,
finds every Building/BuildingPart object, attempts ``cgml.promote_building``
on it, and records the before/after ``can_export_building_from_ir`` result.
It writes a machine-readable JSON report (one row per Building) alongside
the usual ``cac_harness.Results`` pass/fail (one check per fixture: "did
classification complete without raising", never "did it reach IR_FULL").

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_10_fixture_classification.py -- <out_dir>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

# (relative path under test_files/, CityGML version) - the real fixtures
# PLAN.md's task item 3 asks to cover: simple WallSurface, Wall+Window,
# Wall+multiple Windows, Building+Door, BuildingPart, and the P5.9
# BuildingInstallation/unresolved-geometry case.
FIXTURES = [
    ("Building/Railway_Scene_LoD3_CityGML2_Building_only.gml", "2.0"),
    ("Building/Railway_Scene_LoD3_CityGML3_Building_only.gml", "3.0"),
    ("Building/AltOpeningSpelling_LoD3_CityGML3.gml", "3.0"),
    ("Building/SemanticHierarchy_Installations_LoD3_CityGML2.gml", "2.0"),
    ("Building/SemanticHierarchy_Installations_LoD3_CityGML3.gml", "3.0"),
    ("BuildingParts/BuildingParts_citygml2.gml", "2.0"),
    ("BuildingParts/BuildingParts_citygml3.gml", "3.0"),
    ("Building/CitySmith/CS1_lod1.gml", "2.0"),
    ("Building/CitySmith/CS1_lod3.gml", "2.0"),
    ("Building/ExportError_CityDoctorRegression_LoD3_CityGML3.gml", "3.0"),
]


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def _building_objects():
    import bpy

    out = []
    for obj in bpy.data.objects:
        if _kind_of(obj) in ("Building", "BuildingPart") and str(obj.get("gml_id") or "").strip():
            out.append(obj)
    return out


def _classify(building_obj):
    """One ``(gml_id, row)`` per Building - never raises; a promotion
    refusal or an eligibility miss is a classification outcome, not a
    script error."""
    from city_modeler.shared.authoring_core.blender.building_root import export_eligibility

    gml_id = str(building_obj.get("gml_id") or "")
    before = export_eligibility(building_obj)

    import bpy
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    building_obj.select_set(True)
    bpy.context.view_layer.objects.active = building_obj

    promote_error = None
    try:
        result = bpy.ops.cgml.promote_building('EXEC_DEFAULT')
        promote_result = str(result)
    except RuntimeError as exc:
        promote_result = "raised"
        promote_error = str(exc)

    after = export_eligibility(building_obj)

    if after.eligible:
        classification = "IR_FULL"
        reason = after.reason
    elif promote_error is not None or "CANCELLED" in promote_result:
        classification = "UNSUPPORTED_BY_PHASE5"
        reason = promote_error or after.message or after.reason
    else:
        classification = "LEGACY_FALLBACK"
        reason = after.message or after.reason

    return gml_id, {
        "eligible_before": before.eligible,
        "reason_before": before.reason,
        "promote_result": promote_result,
        "promote_error": promote_error,
        "eligible_after": after.eligible,
        "reason_after": after.reason,
        "message_after": after.message,
        "unsupported_features_after": list(after.unsupported_features),
        "classification": classification,
        "classification_reason": reason,
    }


def main():
    r = h.Results(__file__)
    h.load_addon()
    _flag(True)

    report = {}
    for rel_path, version in FIXTURES:
        path = h.FIXTURES / rel_path
        rows = {}
        try:
            if not path.exists():
                r.check(f"fixture exists: {rel_path}", False, str(path))
                continue
            h.import_fixture(path, version, use_semantic_objects=True)
            buildings = _building_objects()
            r.check(f"{rel_path}: at least one Building/BuildingPart found", len(buildings) > 0, str(path))
            for b in buildings:
                gml_id, row = _classify(b)
                rows[gml_id] = row
            r.check(f"{rel_path}: classification completed without raising", True)
        except Exception as exc:  # noqa: BLE001 - a classification run must never abort the whole script
            r.check(f"{rel_path}: classification completed without raising", False, repr(exc))
            rows["__error__"] = repr(exc)
        report[rel_path] = {"version": version, "buildings": rows}

    out_path = h.out_dir() / f"p5_10_fixture_classification_{os.getpid()}.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[p5.10] classification report: {out_path}")
    for rel_path, entry in report.items():
        for gml_id, row in entry["buildings"].items():
            if gml_id == "__error__":
                continue
            print(f"  {rel_path} :: {gml_id} -> {row['classification']} ({row['classification_reason']})")

    r.finish()


if __name__ == "__main__":
    main()
