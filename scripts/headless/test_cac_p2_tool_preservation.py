"""Existing authoring tools vs the record storage (tool preservation audit).

Desired-behaviour checks for tools written before the record model
(existing-tool compatibility audit, STATUS.md log 2026-09-18). A check
that fails today is listed in scripts/tests/known_arch_bugs.json (strict
xfail); the commit that fixes it removes the entry.

AB-14 (fixed in P3.2a): Semantic Stamp used to copy every source custom
property except a fixed identity list
(``facade_placement._STAMP_IDENTITY_OVERRIDE_KEYS``), so a record-backed
source handed its ``_cgml_record`` -- the *source's* gml_id, host and
polygon ids -- to every stamped copy. ``_STAMP_IDENTITY_OVERRIDE_KEYS`` now
also excludes the record key (defense in depth), and the operator routes a
flag-on Stamp through ``create_feature`` so a stamped Window gets a fresh
record of its own instead. The checks below assert the outcome, not a
mechanism: the copy carries either no record (this file calls
``place_semantic_element`` directly, not the operator) or one that
describes the copy itself.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p2_tool_preservation.py -- <out_dir>
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _stamp_record_backed_source():
    """Import, Draw a Window, give it a record, Stamp it; returns (source, stamped, wall)."""
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import (
        FeatureRecord, RecordSet,
    )
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
        surface_world_point_from_uv,
    )

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    # AB-17 fix: (0.3, 0.5) and (0.7, 0.5) land outside this fixture's real
    # solid area (Railway arched cut-out) - find two non-overlapping
    # positions that actually place.
    source_uv = h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1)
    target_uv = h.solid_area_position_uv(
        wall, face_index, ctx, 0.1, 0.1,
        avoid=[(source_uv[0] * ctx.width, source_uv[1] * ctx.height, 0.1, 0.1)])
    source = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.05),
        ctx, PlacementRequest(position_uv=source_uv, source="manual_draw"))
    wall_id = str(wall.get("gml_id") or "")
    # Stand-in for what P3.2's create use case will write on a drawn Window.
    storage.write_record(source, RecordSet(features=(FeatureRecord(
        gml_id=str(source["gml_id"]), kind="Window", parent_id=wall_id, host_id=wall_id,
        provenance="authored", params={"width": 0.2, "height": 0.2, "depth": 0.05}),)))
    target = surface_world_point_from_uv(ctx, target_uv)
    stamped = place_semantic_element(source_object=source, surface_context=ctx, position_world=target)
    return source, stamped, wall


def ab14_stamp_does_not_copy_source_record(state):
    from city_modeler.shared.authoring_core.blender import storage

    source, stamped, _wall = state
    own_id = str(stamped.get("gml_id") or "")
    source_id = str(source.get("gml_id") or "")
    if not own_id or own_id == source_id:
        return False, f"precondition: stamped gml_id {own_id!r} not re-minted (source {source_id!r})"
    if storage.RECORD_KEY not in stamped.keys():
        return True, "stamped copy has no record"
    record_set = storage.read_record(stamped)
    ids = [f.gml_id for f in record_set.features] if record_set else None
    return bool(ids) and source_id not in ids and own_id in ids, (
        f"stamped {stamped.name} (gml_id {own_id}) carries a record for gml_ids {ids}; "
        f"source {source.name} is {source_id}")


def ab14_stamp_ui_view_shows_own_gml_id(state):
    from city_modeler.shared.authoring_core.blender.accessor import read_feature_view

    _source, stamped, _wall = state
    view = read_feature_view(stamped)
    own_id = str(stamped.get("gml_id") or "")
    return view is not None and view.gml_id == own_id, (
        f"panel view of {stamped.name}: origin={getattr(view, 'origin', None)} "
        f"gml_id={getattr(view, 'gml_id', None)}, object gml_id={own_id}")


def stamp_leaves_source_record_unchanged(state):
    """Control: the source's own record is not touched by stamping from it."""
    from city_modeler.shared.authoring_core.blender import storage

    source, _stamped, _wall = state
    record_set = storage.read_record(source)
    ids = [f.gml_id for f in record_set.features] if record_set else None
    return ids == [str(source["gml_id"])], f"source {source.name} record gml_ids {ids}"


CHECKS = [
    ab14_stamp_does_not_copy_source_record,
    ab14_stamp_ui_view_shows_own_gml_id,
    stamp_leaves_source_record_unchanged,
]


def main():
    R = h.Results(__file__)
    # The panel accessor reads records only with the experimental flag on.
    os.environ["CGML_AUTHORING_CORE"] = "1"
    try:
        state = _stamp_record_backed_source()
    except Exception:
        detail = "setup exception:\n" + "".join(traceback.format_exc().splitlines(True)[-6:])
        for check in CHECKS:
            R.check(check.__name__, False, detail)
        R.finish()
    for check in CHECKS:
        try:
            ok, detail = check(state)
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-6:])
        R.check(check.__name__, ok, detail)
    R.finish()


main()
