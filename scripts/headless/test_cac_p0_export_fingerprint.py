"""CAC-P0.4: lock the current CityGML export behaviour as a fingerprint.

For each fixture x use_semantic_objects {on, off} x export version {2.0, 3.0}
(same-version and cross-version, as ticket 12): import, export twice, and
compare the fingerprint of the normalised XML (element counts, feature ids,
polygon ids, interior rings per polygon, opening -> surface nesting) plus
the real xmllint result with golden/cac/export_fp_<case>.json.

The golden records *current* behaviour: an export error or an xmllint
failure is part of the fingerprint, not a test failure. Imported ring ids
are renumbered on export today; that is locked as-is (decision D-3).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p0_export_fingerprint.py -- <out_dir> [--update-goldens]
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


def export_case(path, version, semantic, target, source_ids, out):
    """Fingerprint of one import -> export run; errors are part of the result."""
    h.import_fixture(path, version, use_semantic_objects=semantic)
    out_path = out / target
    try:
        text = h.export_scene(out_path, target_version(target))
    except Exception as exc:  # current behaviour is locked, including failures
        return {"export_error": f"{type(exc).__name__}: {exc}".splitlines()[0]}
    fp = h.export_fingerprint(text, source_ids)
    fp["xmllint"] = h.xmllint(out_path, target_version(target))
    return fp


def target_version(target):
    return "2.0" if "_to_v2" in target else "3.0"


def main():
    R = h.Results(__file__)
    h.update_goldens_requested()
    out = h.out_dir()
    for name, path, version in FIXTURES:
        source_ids = {i for ids in h.collect_ids(path.read_text(encoding="utf-8")).values() for i in ids}
        for semantic in (True, False):
            for target_v in ("2.0", "3.0"):
                case = f"{name}_{'sem' if semantic else 'merged'}_to_v{target_v[0]}"
                runs = [export_case(path, version, semantic, f"{case}_{n}.gml", source_ids, out)
                        for n in range(2)]
                R.check(f"{case}_deterministic", *h.compare_json(runs[0], runs[1]))
                R.check(f"{case}_golden", *h.compare_golden(f"export_fp_{case}", runs[0]))
    R.finish()


main()
