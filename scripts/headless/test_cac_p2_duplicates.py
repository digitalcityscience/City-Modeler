"""Shift+D duplicate gml_id (CAC-P2.4): lock today's export, then report it.

First step of P2.4 (moved from P0.6, A1): Railway v3 with semantic
objects, Shift+D (``bpy.ops.object.duplicate``) on one WallSurface, export
CityGML 3 and 2. The copy carries the same ``gml_id`` and shares the
materials (so the same polygon ids). The fingerprint golden records what
the exporter does with that today, dedup traps included; it is current
behaviour, not a verdict (policy D-4 is decided in P4).

Then the registry reports it (PLAN.md §2.6 C3): the duplicate gml_id, and
with the flag on the duplicate record (Shift+D of a record-backed object)
and a foreign record (a record describing another gml_id). Reporting does
not change the export.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p2_duplicates.py -- <out_dir> [--update-goldens]
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SOURCE_IDS = {i for ids in h.collect_ids(RAILWAY_V3.read_text(encoding="utf-8")).values() for i in ids}


def shift_d(obj):
    """Blender's Shift+D on ``obj`` in Object Mode (no move); returns the copy."""
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    before = set(bpy.data.objects)
    result = bpy.ops.object.duplicate()
    new = [o for o in bpy.data.objects if o not in before]
    if result != {'FINISHED'} or len(new) != 1:
        raise AssertionError(f"duplicate() -> {result}, new objects {[o.name for o in new]}")
    return new[0]


def duplicated_wall_scene():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, _face = h.first_wall_with_exterior_face()
    return wall, shift_d(wall)


def export_fp(target, out):
    version = "2.0" if "_to_v2" in target else "3.0"
    path = out / f"{target}.gml"
    try:
        text = h.export_scene(path, version)
    except Exception as exc:  # current behaviour is locked, including failures
        return {"export_error": f"{type(exc).__name__}: {exc}".splitlines()[0]}
    fp = h.export_fingerprint(text, SOURCE_IDS)
    fp["xmllint"] = h.xmllint(path, version)
    return fp


def check_copy_identity(R):
    wall, copy = duplicated_wall_scene()
    R.check("shiftd_copy_carries_same_gml_id",
            copy.get("gml_id") == wall.get("gml_id") and copy.name != wall.name,
            f"{wall.name}={wall.get('gml_id')} {copy.name}={copy.get('gml_id')}")


def check_export_lock(R, out):
    for target_v in ("2", "3"):
        case = f"railway_v3_shiftd_wall_to_v{target_v}"
        runs = []
        for n in range(2):
            duplicated_wall_scene()
            runs.append(export_fp(f"{case}_{n}", out))
        R.check(f"{case}_deterministic", *h.compare_json(runs[0], runs[1]))
        R.check(f"{case}_golden", *h.compare_golden(f"export_fp_{case}", runs[0]))


def _registry():
    from city_modeler.shared.authoring_core.blender import registry
    return registry.REGISTRY


def _codes(found, code):
    return [(f.gml_id, f.keys) for f in found if f.code == code]


def _record_backed_duplicate():
    """Shift+D of a WallSurface that carries a record (stand-in for a P3 writer)."""
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, _face = h.first_wall_with_exterior_face()
    gid = str(wall["gml_id"])
    storage.write_record(wall, RecordSet(features=(FeatureRecord(gml_id=gid, kind="WallSurface"),)))
    return gid, wall, shift_d(wall)


def check_findings(R):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain import registry_core as rc
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    wall, copy = duplicated_wall_scene()
    gid, keys = str(wall["gml_id"]), tuple(sorted((wall.name_full, copy.name_full)))
    found = _registry().findings()
    R.check("shiftd_reports_duplicate_gml_id", (gid, keys) in _codes(found, rc.DUPLICATE_GML_ID),
            f"expected {(gid, keys)} in {_codes(found, rc.DUPLICATE_GML_ID)}")

    os.environ["CGML_AUTHORING_CORE"] = "1"
    try:
        gid, wall, copy = _record_backed_duplicate()
        keys = tuple(sorted((wall.name_full, copy.name_full)))
        found = _registry().findings()
        R.check("shiftd_record_backed_reports_duplicate_id_and_record",
                (gid, keys) in _codes(found, rc.DUPLICATE_GML_ID)
                and (gid, keys) in _codes(found, rc.DUPLICATE_RECORD),
                f"duplicate ids {_codes(found, rc.DUPLICATE_GML_ID)}; "
                f"duplicate records {_codes(found, rc.DUPLICATE_RECORD)}")

        h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
        wall, _face = h.first_wall_with_exterior_face()
        storage.write_record(wall, RecordSet(features=(FeatureRecord(gml_id="CAC_OTHER_FEATURE", kind="Window"),)))
        found = _registry().findings()
        R.check("foreign_record_reported",
                (str(wall["gml_id"]), (wall.name_full,)) in _codes(found, rc.FOREIGN_RECORD),
                f"foreign records {_codes(found, rc.FOREIGN_RECORD)}")
    finally:
        os.environ.pop("CGML_AUTHORING_CORE", None)

    _record_backed_duplicate()
    found = _registry().findings()
    record_codes = [f.code for f in found if f.code in (rc.DUPLICATE_RECORD, rc.FOREIGN_RECORD)]
    R.check("flag_off_reports_no_record_findings", not record_codes and _codes(found, rc.DUPLICATE_GML_ID),
            f"record findings {record_codes}; duplicate ids {_codes(found, rc.DUPLICATE_GML_ID)}")


def check_export_unchanged_by_reporting(R, out):
    for target_v in ("2", "3"):
        case = f"railway_v3_shiftd_wall_to_v{target_v}"
        duplicated_wall_scene()
        _registry().findings()
        R.check(f"{case}_unchanged_after_findings",
                *h.compare_golden(f"export_fp_{case}", export_fp(f"{case}_reported", out), update=False))


def _baseline(target_v):
    """P0.4 golden of the same scene without the duplicate."""
    import json
    return json.loads((h.GOLDEN_DIR / f"export_fp_railway_v3_sem_to_v{target_v}.json").read_text(encoding="utf-8"))


def _export_or_refusal(target, out):
    try:
        return export_fp(target, out), None
    except AssertionError as exc:  # h.export_scene: operator did not finish
        return None, str(exc)


def check_ab15(R, out):
    """AB-15, desired behaviour, policy-neutral (D-4 decides re-mint vs refuse):
    export either refuses the duplicate or writes it without harming the
    original. It must never corrupt geometry (v2) or drop the copy silently (v3)."""
    duplicated_wall_scene()
    fp, refused = _export_or_refusal("ab15_to_v2", out)
    if refused:
        R.check("ab15_v2_duplicate_does_not_corrupt_original", True, f"refused: {refused}")
    else:
        base = _baseline("2")
        changed = sorted(p for p in base["coordinates"]
                         if fp["coordinates"].get(p) != base["coordinates"][p]
                         or fp["interior_rings"].get(p) != base["interior_rings"].get(p))
        R.check("ab15_v2_duplicate_does_not_corrupt_original", not changed,
                f"original polygons changed by the duplicate (rings/coordinates): {changed}")

    duplicated_wall_scene()
    fp, refused = _export_or_refusal("ab15_to_v3", out)
    if refused:
        R.check("ab15_v3_duplicate_not_silently_dropped", True, f"refused: {refused}")
    else:
        n_base, n_dup = len(_baseline("3")["polygon_ids"]), len(fp["polygon_ids"])
        R.check("ab15_v3_duplicate_not_silently_dropped", n_dup > n_base,
                f"export succeeded with {n_dup} polygons, same as without the duplicate ({n_base})")


def main():
    R = h.Results(__file__)
    h.update_goldens_requested()
    os.environ.pop("CGML_AUTHORING_CORE", None)
    out = h.out_dir()
    for step in (lambda: check_copy_identity(R), lambda: check_export_lock(R, out),
                 lambda: check_findings(R), lambda: check_export_unchanged_by_reporting(R, out),
                 lambda: check_ab15(R, out)):
        try:
            step()
        except Exception:
            R.check(f"step_exception_{len(R.checks)}", False, traceback.format_exc())
    R.finish()


main()
