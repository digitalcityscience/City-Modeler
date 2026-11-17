# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P5.10 / G5 task item 14: legacy vs IR writer parity, real fixtures.

**Corrective-followup update (CAC-P5.10.1):** this script originally
assumed ``AltOpeningSpelling_LoD3_CityGML3.gml`` and
``ExportError_CityDoctorRegression_LoD3_CityGML3.gml`` reach genuine IR
eligibility after promotion. Running it was what *found* the CAC-P5.10 bug
(``can_export_building_from_ir`` said eligible, ``building_ir_from_root``
then raised ``IRAdapterError`` for a missing ``opening_link``). Now that
``domain/export_eligibility.py``/``domain/promote_building.py`` carry the
fix, both fixtures correctly classify as ineligible (their Windows are
plain-imported, never authored, so they have no ``opening_link`` - see the
fix's own docstring) and fall back to the legacy path. This script now:

1. Regression-proves task items 5/6 for every fixture below: promoting and
   exporting with the flag on must never raise, and must stay
   xmllint-clean, whether or not the fixture ends up IR-eligible.
2. Runs the frozen-field parity diff (below) only for a fixture that
   genuinely has at least one IR-eligible Building after promotion -
   currently ``BuildingParts/BuildingParts_citygml2/3.gml``'s CS1-style
   sibling case is Window-only-blocked, but ``Building/CitySmith/
   CS1_lod3.gml``'s nested BuildingPart (no Window at all, so unaffected
   by the opening_link gap) does reach ``IR_FULL`` - real evidence for
   item 14, not synthetic.

For a genuinely IR-eligible fixture, exports the same promoted scene
twice - once with the experimental flag off (legacy mesh/material writer)
and once with it on (IR writer) - and compares
``cac_harness.export_fingerprint`` between the two on the frozen G5 fields
that are unambiguous across both writers:

  - Building / WallSurface / RoofSurface / GroundSurface / ClosureSurface /
    Window / Door gml:id sets (promotion preserves the Building's and every
    promoted feature's *original* gml_id - PLAN.md P5.0/P5.9 - so these
    must be identical whichever writer emits them, not merely stable
    within one writer);
  - polygon coordinate digests, keyed by polygon gml:id (same source
    geometry, same ``geometry_fact``/committed-record read used by both the
    legacy face walk and ``promote_building``'s geometry commit);
  - opening nesting *by gml:id pair* (host WallSurface gml:id, opening
    gml:id) - the semantic host relation, not the ring-level plumbing.

Deliberately out of scope for this comparison (documented, not silently
skipped): literal *ring* gml:id text. Ring ids are not part of the
"must remain stable" identity contract PLAN.md's G5 section freezes (only
Building/WallSurface/Window/polygon/ring *ids* as a *set*, checked via
``interior_rings`` role/count, are); the legacy writer and the record-backed
IR writer are not guaranteed to mint the same literal ring id text for a
ring that has no explicit source-file identity, and ring-level *identity*
stability across F9/save-reload is already proven within the IR path on
its own by ``test_cac_p5_6_writer_dispatch.py``/``test_cac_p5_9_promote_
building.py``, not against the legacy writer's independent minting scheme.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_10_writer_parity.py -- <out_dir>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

FROZEN_TAGS = (
    "Building", "BuildingPart", "WallSurface", "RoofSurface", "GroundSurface",
    "ClosureSurface", "Window", "WindowSurface", "Door", "DoorSurface",
)

# (relative path, source CityGML version) - real fixtures to regression-
# check (items 5/6: flag-on export must never raise, must stay xmllint-
# clean) and, where at least one Building genuinely reaches IR eligibility,
# to run the frozen-field parity diff on (item 14).
FIXTURES = [
    ("Building/AltOpeningSpelling_LoD3_CityGML3.gml", "3.0"),
    ("Building/ExportError_CityDoctorRegression_LoD3_CityGML3.gml", "3.0"),
    ("Building/CitySmith/CS1_lod3.gml", "2.0"),
]


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def _promote_all_buildings():
    """Promotes every Building/BuildingPart and returns
    ``[(gml_id, op_result, eligible_after)]`` - the eligibility read is the
    real ``can_export_building_from_ir`` result, not an assumption."""
    import bpy

    from city_modeler.shared.authoring_core.blender.building_root import (
        export_eligibility,
    )

    promoted = []
    for obj in list(bpy.data.objects):
        if _kind_of(obj) not in ("Building", "BuildingPart"):
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        # A refused promotion (CAC-P5.10's preflight, before any write) is
        # an expected, valid outcome here, not a script failure - Blender
        # raises RuntimeError from bpy.ops for an operator's own ERROR
        # report, so this must be caught, exactly like
        # test_cac_p5_10_fixture_classification.py's ``_classify`` already
        # does.
        try:
            result = str(bpy.ops.cgml.promote_building('EXEC_DEFAULT'))
        except RuntimeError as exc:
            result = f"raised: {exc}"
        eligibility = export_eligibility(obj)
        promoted.append((obj.get("gml_id"), result, eligibility.eligible, eligibility.reason))
    return promoted


def _frozen_feature_ids(fingerprint):
    return sorted(f for f in fingerprint["feature_ids"] if f.split(":", 1)[0] in FROZEN_TAGS)


def _parity_diff(legacy_fp, ir_fp):
    diffs = {}
    legacy_ids = _frozen_feature_ids(legacy_fp)
    ir_ids = _frozen_feature_ids(ir_fp)
    if legacy_ids != ir_ids:
        diffs["frozen_feature_ids"] = {"legacy_only": sorted(set(legacy_ids) - set(ir_ids)),
                                        "ir_only": sorted(set(ir_ids) - set(legacy_ids))}

    # Coordinates: compare only polygons whose gml:id is present in both
    # (real, source-derived polygon ids - never the "<no-id>#n" fallback
    # key, which is positional and not comparable across writers).
    common_polys = set(legacy_fp["coordinates"]) & set(ir_fp["coordinates"])
    common_polys = {k for k in common_polys if not k.startswith("<no-id>")}
    mismatched = {k: (legacy_fp["coordinates"][k], ir_fp["coordinates"][k])
                  for k in common_polys if legacy_fp["coordinates"][k] != ir_fp["coordinates"][k]}
    if mismatched:
        diffs["coordinate_digest_mismatch"] = mismatched
    diffs["_common_polygon_count"] = len(common_polys)

    # Opening nesting by gml:id pair (host, opening) - ignore entries with
    # no host id (floating/unresolvable) on either side.
    legacy_nest = {tuple(p) for p in legacy_fp["opening_nesting"] if p[0] and p[1]}
    ir_nest = {tuple(p) for p in ir_fp["opening_nesting"] if p[0] and p[1]}
    if legacy_nest != ir_nest:
        diffs["opening_nesting"] = {"legacy_only": sorted(legacy_nest - ir_nest),
                                     "ir_only": sorted(ir_nest - legacy_nest)}

    return diffs


def _run_one(r, rel_path, source_version, export_version):
    path = h.FIXTURES / rel_path
    tag = f"{rel_path} -> v{export_version.split('.')[0]}"
    if not path.exists():
        r.check(f"{tag}: fixture exists", False, str(path))
        return

    _flag(True)
    h.import_fixture(path, source_version, use_semantic_objects=True)
    promoted = _promote_all_buildings()
    r.check(f"{tag}: at least one Building promoted", len(promoted) > 0, str(promoted))
    any_eligible = any(p[2] for p in promoted)

    _flag(False)
    out_legacy = h.out_dir() / f"p5_10_parity_legacy_{export_version.replace('.', '')}_{os.getpid()}.gml"
    text_legacy = h.export_scene(str(out_legacy), export_version)
    lint_legacy = h.xmllint(out_legacy, export_version)
    r.check(f"{tag}: legacy export xmllint-clean", lint_legacy["exit"] == 0, str(lint_legacy))

    # Task items 5/6: the flag-on export of a real, plain-imported fixture
    # must never raise (the CAC-P5.10 bug) and must stay xmllint-clean,
    # whether or not this fixture ends up IR-eligible after the fix.
    _flag(True)
    out_ir = h.out_dir() / f"p5_10_parity_ir_{export_version.replace('.', '')}_{os.getpid()}.gml"
    try:
        text_ir = h.export_scene(str(out_ir), export_version)
    except Exception as exc:  # noqa: BLE001 - a raise here is exactly the bug this script exists to catch
        r.check(f"{tag}: flag-on export does not raise (no IRAdapterError escapes to the writer)",
                 False, repr(exc))
        return
    r.check(f"{tag}: flag-on export does not raise (no IRAdapterError escapes to the writer)", True)
    lint_ir = h.xmllint(out_ir, export_version)
    r.check(f"{tag}: flag-on export is xmllint-clean", lint_ir["exit"] == 0, str(lint_ir))

    if not any_eligible:
        r.check(
            f"{tag}: no Building reached IR eligibility - legacy fallback proven safe "
            f"(parity diff not applicable) [{promoted}]",
            True,
        )
        return

    legacy_fp = h.export_fingerprint(text_legacy)
    ir_fp = h.export_fingerprint(text_ir)
    diffs = _parity_diff(legacy_fp, ir_fp)
    real_diffs = {k: v for k, v in diffs.items() if not k.startswith("_")}
    r.check(
        f"{tag}: legacy vs IR writer parity on frozen G5 fields "
        f"(feature ids, polygon coordinates, opening nesting)",
        not real_diffs,
        json.dumps(diffs, default=list)[:2000],
    )


def main():
    r = h.Results(__file__)
    h.load_addon()

    for rel_path, source_version in FIXTURES:
        for export_version in ("2.0", "3.0"):
            _run_one(r, rel_path, source_version, export_version)

    r.finish()


if __name__ == "__main__":
    main()
