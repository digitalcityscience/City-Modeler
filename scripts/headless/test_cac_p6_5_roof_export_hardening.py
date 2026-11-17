# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.5 (RoofSurface IR/export production
hardening).

P6.0-P6.4 already proved promotion, interactive edit/commit, derived
display and duplicate/delete lifecycle. P6.5 proves the *export* half of
the same slice end to end: record-backed RoofSurface -> IR -> Building-
level D-6 dispatch -> CityGML 2/3 XML, reusing the existing generic
surface IR/writer machinery with zero RoofSurface-specific branches
(confirmed by audit before this file was written: ``ExportSurface``/
``SurfaceNode`` are already kind-agnostic, and ``export_eligibility.
ALLOWED_IR_KINDS`` already includes ``RoofSurface``).

Fixture choice matters here and is itself a P6.5 finding (PLAN.md point
15, "mixed record-backed/legacy Building behavior"): ``CS1_lod3.gml``'s
Building is **not** Building-level D-6 eligible, because several of its
WallSurfaces were split into same-gml_id Blender fragments
(``..._1``/``..._2``) the Phase-5 promotion matrix never promotes - a
pre-existing WallSurface completeness gap, unrelated to RoofSurface. CS1's
own RoofSurfaces all promote and commit cleanly (P6.1/P6.1a), but since
D-6 is an all-or-nothing *Building*-level decision and GS.3.2's per-
surface fallback is scoped to WallSurface+Window only (``blender/flag.py::
gs_surface_dispatch_enabled`` - "RoofSurface ... always stay legacy
regardless of this flag"), CS1's Building keeps exporting through the
pure legacy path, which mints fresh ``ID_<hex>`` gml:ids for every
RoofSurface rather than reusing the committed record's own gml_id. CS1 is
therefore kept here only for the legacy-path characterization check
(``cs1_building_exports_without_mutating_records``) and
is the wrong fixture for every identity-preservation proof. (B64 later made
CS1's Building eligible; the characterization check now asserts that.)

The Railway LoD3 v2/v3 Building-only pair (same fixture P6.1 already uses
for v2/v3 parity) **is** Building-level D-6 eligible after the same
unsupported-feature stripping P6.1 applies, and - usefully for P6.5 -
each of its 3 RoofSurfaces already carries multiple PolygonRecords (4, 6
and 8 respectively) with two real interior rings (holes) among them. It is
therefore the fixture for every export-identity/duplicate/delete/edit/
fault-injection proof below.

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_5
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

CS1 = h.FIXTURES / "Building" / "CitySmith" / "CS1_lod3.gml"
RAILWAY_V2 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML2_Building_only.gml"
RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _select(objects):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def _kind_of(obj):
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    return neutral_kind(obj.get("cgml3_feature"))


def _building_object():
    import bpy

    for obj in bpy.data.objects:
        if _kind_of(obj) == "Building" and str(obj.get("gml_id") or "").strip():
            return obj
    return None


def _promote(building_obj):
    import bpy

    _select([building_obj])
    try:
        result = bpy.ops.cgml.promote_building('EXEC_DEFAULT')
        error = ""
    except RuntimeError as e:
        result, error = {'CANCELLED'}, str(e)
    return result, error


def _strip_unsupported():
    import bpy

    from city_modeler.shared.authoring_core.domain.export_eligibility import (
        ALLOWED_IR_KINDS, PASSTHROUGH_KINDS,
    )

    keep_kinds = (set(ALLOWED_IR_KINDS) | set(PASSTHROUGH_KINDS)) - {"Window"}
    for obj in list(bpy.data.objects):
        kind = _kind_of(obj)
        if kind in ("Building", "BuildingPart") or kind in keep_kinds:
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _prepare_promoted(fixture_path, version):
    import bpy

    from city_modeler.shared.authoring_core.blender import roof_edit_session as _rs

    _rs._SESSIONS.clear()
    _flag(True)
    h.import_fixture(fixture_path, version, use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    result, error = _promote(building)
    if result != {'FINISHED'}:
        raise RuntimeError(f"{fixture_path}: promotion did not succeed: result={result} error={error!r}")
    from city_modeler.shared.authoring_core.blender import storage

    roofs = [
        o for o in bpy.data.objects
        if _kind_of(o) == "RoofSurface" and storage.has_record(o)
    ]
    return building, roofs


def _prepare_promoted_railway(version="2.0"):
    path = RAILWAY_V2 if version == "2.0" else RAILWAY_V3
    return _prepare_promoted(path, version)


def _hole_bearing_polygon(roof):
    """``(PolygonRecord, FeatureRecord)`` for the first committed polygon
    of ``roof`` that carries at least one interior ring, or ``(None, None)``."""
    from city_modeler.shared.authoring_core.blender import storage

    gml_id = str(roof.get("gml_id"))
    record = storage.read_record(roof)
    feature = record.feature(gml_id)
    for poly in record.polygons_of(gml_id):
        if poly.interiors and poly.geometry_state == "committed":
            return poly, feature
    return None, feature


def _hole_bearing_roof(roofs):
    for roof in roofs:
        poly, _ = _hole_bearing_polygon(roof)
        if poly is not None:
            return roof
    return None


def _multi_polygon_roof(roofs):
    from city_modeler.shared.authoring_core.blender import storage

    best = None
    for roof in roofs:
        gml_id = str(roof.get("gml_id"))
        n = len(storage.read_record(roof).polygons_of(gml_id))
        if best is None or n > best[1]:
            best = (roof, n)
    return best[0] if best else None


def _native_duplicate(roof):
    import bpy

    _select([roof])
    bpy.ops.object.duplicate(linked=False)
    return bpy.context.view_layer.objects.active


def _begin_op(roof):
    import bpy

    _select([roof])
    return bpy.ops.cgml.feature_edit_begin('EXEC_DEFAULT', object_gml_id=str(roof.get("gml_id") or ""))


def _commit_op(roof):
    import bpy

    _select([roof])
    return bpy.ops.cgml.feature_edit_commit('EXEC_DEFAULT', object_gml_id=str(roof.get("gml_id") or ""))


def _translate(obj, dx=0.0, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _export_both(stem):
    v2_path = h.out_dir() / f"{stem}_v2.gml"
    v3_path = h.out_dir() / f"{stem}_v3.gml"
    v2_text = h.export_scene(str(v2_path), "2.0")
    v3_text = h.export_scene(str(v3_path), "3.0")
    return (v2_path, v2_text), (v3_path, v3_text)


def _root_of(xml_text):
    import xml.etree.ElementTree as ET

    return ET.fromstring(xml_text.encode("utf-8"))


# ---------------------------------------------------------------------------
# CS1: characterize the mixed record-backed/legacy case (point 15)
# ---------------------------------------------------------------------------

def cs1_building_exports_without_mutating_records(R):
    """CS1's Building has record-backed, committed RoofSurfaces (P6.1/
    P6.1a). Until B64 its id-less WallSurfaces were split into one Blender
    fragment per polygon that the Phase-5 promotion matrix never promotes,
    which kept the Building legacy (PLAN.md point 15). B64 gives an id-less
    wall with openings a stable id, so its polygons form one surface and its
    windows sit on it: the Building is now Building-level eligible
    (``ir_full``). Either way the export must be xmllint-clean and must not
    corrupt or touch the committed Roof records."""
    from city_modeler.shared.authoring_core.blender import export_dispatch, storage

    building, roofs = _prepare_promoted(CS1, "2.0")
    if not roofs:
        R.check("cs1_building_exports_without_mutating_records", False,
                "no promoted RoofSurface found")
        return

    plan = export_dispatch.building_export_plan(building)
    records_before = {str(r.get("gml_id")): json.dumps(storage.read_record(r).to_dict(), sort_keys=True)
                      for r in roofs}

    out_path = h.out_dir() / "p6_5_cs1_legacy.gml"
    text = h.export_scene(str(out_path), "2.0")
    lint = h.xmllint(out_path, "2.0")

    records_after = {str(r.get("gml_id")): json.dumps(storage.read_record(r).to_dict(), sort_keys=True)
                      for r in roofs}

    ids = h.collect_ids(text)
    n_exported_roofs = len(ids.get("RoofSurface", ()))

    ok = (
        plan.eligibility.eligible  # B64: was not eligible (wall fragments), see the docstring
        and lint["exit"] == 0 and lint["errors"] == 0
        and n_exported_roofs > 0
        and records_before == records_after
    )
    R.check("cs1_building_exports_without_mutating_records", ok, json.dumps({
        "eligible": plan.eligibility.eligible, "reason": plan.eligibility.reason,
        "lint": lint, "n_record_backed_roofs": len(roofs), "n_exported_roofs": n_exported_roofs,
        "records_untouched": records_before == records_after,
    }))


# ---------------------------------------------------------------------------
# A/2. Railway: simple Building-level D-6 Roof export, v2/v3 xmllint-clean,
# record identity preserved end to end
# ---------------------------------------------------------------------------

def railway_roof_export_is_d6_eligible_and_preserves_record_identity(R):
    from city_modeler.shared.authoring_core.blender import export_dispatch, storage

    building, roofs = _prepare_promoted_railway("2.0")
    if not roofs:
        R.check("railway_roof_export_is_d6_eligible_and_preserves_record_identity", False, "no promoted RoofSurface")
        return
    plan = export_dispatch.building_export_plan(building)
    roof_ids = {str(r.get("gml_id")) for r in roofs}
    poly_ids = {p.polygon_id for r in roofs for p in storage.read_record(r).polygons_of(str(r.get("gml_id")))}

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_railway_simple")
    lint2 = h.xmllint(v2_path, "2.0")
    lint3 = h.xmllint(v3_path, "3.0")
    ids2 = h.collect_ids(v2_text)
    ids3 = h.collect_ids(v3_text)

    ok = (
        plan.eligibility.eligible
        and lint2["exit"] == 0 and lint2["errors"] == 0
        and lint3["exit"] == 0 and lint3["errors"] == 0
        and roof_ids == set(ids2.get("RoofSurface", ())) == set(ids3.get("RoofSurface", ()))
        and poly_ids <= set(ids2.get("Polygon", ())) and poly_ids <= set(ids3.get("Polygon", ()))
    )
    R.check("railway_roof_export_is_d6_eligible_and_preserves_record_identity", ok, json.dumps({
        "eligible": plan.eligibility.eligible, "reason": plan.eligibility.reason,
        "lint2": lint2, "lint3": lint3, "n_roofs": len(roof_ids), "n_polys": len(poly_ids),
        "roof_ids_match_v2": roof_ids == set(ids2.get("RoofSurface", ())),
        "roof_ids_match_v3": roof_ids == set(ids3.get("RoofSurface", ())),
    }))


# ---------------------------------------------------------------------------
# B. edited Roof export reflects committed coordinates, not stale geometry
# ---------------------------------------------------------------------------

def edited_roof_export_reflects_committed_coordinates(R):
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("edited_roof_export_reflects_committed_coordinates", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))

    _, (before_v2_path, before_v2_text) = _export_both("p6_5_edit_state_a")
    before_digests = h.coordinate_digests(_root_of(before_v2_text))

    _begin_op(roof)
    _translate(roof, dx=0.37, dy=-0.21, dz=0.0)
    commit_result = _commit_op(roof)

    after_polys = storage.read_record(roof).polygons_of(gml_id)

    _, (after_v2_path, after_v2_text) = _export_both("p6_5_edit_state_b")
    after_digests = h.coordinate_digests(_root_of(after_v2_text))

    after_struct = h.export_structure(after_v2_path)
    moved_in_xml = all(
        p.polygon_id in after_struct["polygons"]
        and h.same_ring(h.to_export_coords(p.exterior.coords), after_struct["polygons"][p.polygon_id]["exterior"])
        for p in after_polys
    )

    ok = commit_result == {"FINISHED"} and before_digests != after_digests and moved_in_xml
    R.check("edited_roof_export_reflects_committed_coordinates", ok, json.dumps({
        "commit_result": str(commit_result),
        "digests_changed": before_digests != after_digests,
        "moved_coords_present_in_xml": moved_in_xml,
    }))


# ---------------------------------------------------------------------------
# C. generic interior-ring export: structure + no opening_link/Window leak
# ---------------------------------------------------------------------------

def generic_interior_ring_exported_with_correct_nesting(R):
    _, roofs = _prepare_promoted_railway("2.0")
    roof = _hole_bearing_roof(roofs)
    if roof is None:
        R.check("generic_interior_ring_exported_with_correct_nesting", False,
                "no committed hole-bearing RoofSurface in Railway fixture")
        return
    gml_id = str(roof.get("gml_id"))
    poly, _ = _hole_bearing_polygon(roof)
    expected_coords = h.to_export_coords(poly.interiors[0].coords)

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_hole_bearing")
    lint2 = h.xmllint(v2_path, "2.0")
    lint3 = h.xmllint(v3_path, "3.0")

    def _nesting_ok(path):
        struct = h.export_structure(path)
        entry = struct["polygons"].get(poly.polygon_id)
        if entry is None or entry["host"] != gml_id or len(entry["interiors"]) != 1:
            return False
        return h.same_ring(expected_coords, entry["interiors"][0])

    ok2 = _nesting_ok(v2_path)
    ok3 = _nesting_ok(v3_path)
    no_opening_link_artifact = "opening_link" not in v2_text and "opening_link" not in v3_text

    ok = (
        ok2 and ok3
        and lint2["exit"] == 0 and lint2["errors"] == 0
        and lint3["exit"] == 0 and lint3["errors"] == 0
        and no_opening_link_artifact
    )
    R.check("generic_interior_ring_exported_with_correct_nesting", ok, json.dumps({
        "gml_id": gml_id, "polygon_id": poly.polygon_id,
        "nesting_ok_v2": ok2, "nesting_ok_v3": ok3,
        "lint2": lint2, "lint3": lint3, "no_opening_link_artifact": no_opening_link_artifact,
    }))


# ---------------------------------------------------------------------------
# Multi-polygon RoofSurface: every polygon exported, no omission/duplication
# ---------------------------------------------------------------------------

def multi_polygon_roof_exported_fully(R):
    from city_modeler.shared.authoring_core.blender import storage

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("multi_polygon_roof_exported_fully", False, "no promoted RoofSurface found")
        return
    gml_id = str(roof.get("gml_id"))
    record_polys = storage.read_record(roof).polygons_of(gml_id)
    expected_poly_ids = [p.polygon_id for p in record_polys]

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_multi_polygon")
    struct2 = h.export_structure(v2_path)
    struct3 = h.export_structure(v3_path)

    def _all_present_once_correct_host(struct):
        for pid in expected_poly_ids:
            entry = struct["polygons"].get(pid)
            if entry is None or entry["host"] != gml_id:
                return False
        # no duplicate polygon ids anywhere in the document
        return len(struct["polygons"]) == len(set(struct["polygons"]))

    ok = (
        len(expected_poly_ids) > 1
        and _all_present_once_correct_host(struct2) and _all_present_once_correct_host(struct3)
    )
    R.check("multi_polygon_roof_exported_fully", ok, json.dumps({
        "gml_id": gml_id, "n_polygons": len(expected_poly_ids),
        "all_present_v2": _all_present_once_correct_host(struct2),
        "all_present_v3": _all_present_once_correct_host(struct3),
    }))


# ---------------------------------------------------------------------------
# D. duplicate export: distinct ids, no collisions, both present
# ---------------------------------------------------------------------------

def duplicate_export_distinct_ids_no_duplicates(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("duplicate_export_distinct_ids_no_duplicates", False, "no promoted RoofSurface found")
        return
    old_gml_id = str(roof.get("gml_id"))

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_duplicate")
    ids2 = h.collect_ids(v2_text)
    ids3 = h.collect_ids(v3_text)

    all_ids_v2 = [i for ids in ids2.values() for i in ids]
    all_ids_v3 = [i for ids in ids3.values() for i in ids]
    no_dupes_v2 = len(all_ids_v2) == len(set(all_ids_v2))
    no_dupes_v3 = len(all_ids_v3) == len(set(all_ids_v3))

    both_present_v2 = old_gml_id in ids2.get("RoofSurface", ()) and new_gml_id in ids2.get("RoofSurface", ())
    both_present_v3 = old_gml_id in ids3.get("RoofSurface", ()) and new_gml_id in ids3.get("RoofSurface", ())

    new_polys = storage.read_record(replacement).polygons_of(new_gml_id)
    old_polys = storage.read_record(roof).polygons_of(old_gml_id)
    poly_ids_distinct = set(p.polygon_id for p in new_polys).isdisjoint(set(p.polygon_id for p in old_polys))
    ring_ids_distinct = (
        {r.ring_id for p in new_polys for r in (p.exterior,) + p.interiors}
        .isdisjoint({r.ring_id for p in old_polys for r in (p.exterior,) + p.interiors})
    )

    ok = (
        old_gml_id != new_gml_id and no_dupes_v2 and no_dupes_v3
        and both_present_v2 and both_present_v3
        and poly_ids_distinct and ring_ids_distinct
    )
    R.check("duplicate_export_distinct_ids_no_duplicates", ok, json.dumps({
        "old": old_gml_id, "new": new_gml_id,
        "no_dupes_v2": no_dupes_v2, "no_dupes_v3": no_dupes_v3,
        "both_present_v2": both_present_v2, "both_present_v3": both_present_v3,
        "poly_ids_distinct": poly_ids_distinct, "ring_ids_distinct": ring_ids_distinct,
    }))


def duplicate_hole_bearing_export_preserves_topology_distinct_ids(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases, storage

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _hole_bearing_roof(roofs)
    if roof is None:
        R.check("duplicate_hole_bearing_export_preserves_topology_distinct_ids", False,
                "no committed hole-bearing RoofSurface in Railway fixture")
        return
    old_gml_id = str(roof.get("gml_id"))

    duplicate = _native_duplicate(roof)
    replacement = roof_usecases.initialize_roof_duplicate(duplicate)
    new_gml_id = str(replacement.get("gml_id"))

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_duplicate_hole")
    lint2 = h.xmllint(v2_path, "2.0")
    lint3 = h.xmllint(v3_path, "3.0")

    new_polys = storage.read_record(replacement).polygons_of(new_gml_id)
    old_polys = storage.read_record(roof).polygons_of(old_gml_id)
    new_interior_rings = {r.ring_id for p in new_polys for r in p.interiors}
    old_interior_rings = {r.ring_id for p in old_polys for r in p.interiors}

    ok = (
        lint2["exit"] == 0 and lint2["errors"] == 0
        and lint3["exit"] == 0 and lint3["errors"] == 0
        and len(new_interior_rings) == len(old_interior_rings) > 0
        and new_interior_rings.isdisjoint(old_interior_rings)
    )
    R.check("duplicate_hole_bearing_export_preserves_topology_distinct_ids", ok, json.dumps({
        "old": old_gml_id, "new": new_gml_id, "lint2": lint2, "lint3": lint3,
        "n_new_interior_rings": len(new_interior_rings), "n_old_interior_rings": len(old_interior_rings),
    }))


# ---------------------------------------------------------------------------
# E. delete export: absence + sibling preservation
# ---------------------------------------------------------------------------

def delete_export_absence_and_sibling_preservation(R):
    from city_modeler.shared.authoring_core.blender import roof_usecases

    _, roofs = _prepare_promoted_railway("2.0")
    if len(roofs) < 2:
        R.check("delete_export_absence_and_sibling_preservation", False, "need >=2 promoted RoofSurfaces")
        return
    victim = _multi_polygon_roof(roofs)
    victim_gml_id = str(victim.get("gml_id"))
    sibling_gml_ids = {str(r.get("gml_id")) for r in roofs if r is not victim}

    roof_usecases.delete_roof(victim_gml_id)

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_delete")
    lint2 = h.xmllint(v2_path, "2.0")
    lint3 = h.xmllint(v3_path, "3.0")

    ids2 = set(h.collect_ids(v2_text).get("RoofSurface", ()))
    ids3 = set(h.collect_ids(v3_text).get("RoofSurface", ()))

    ok = (
        victim_gml_id not in ids2 and victim_gml_id not in ids3
        and sibling_gml_ids <= ids2 and sibling_gml_ids <= ids3
        and lint2["exit"] == 0 and lint2["errors"] == 0
        and lint3["exit"] == 0 and lint3["errors"] == 0
    )
    R.check("delete_export_absence_and_sibling_preservation", ok, json.dumps({
        "victim": victim_gml_id, "n_siblings": len(sibling_gml_ids),
        "victim_absent_v2": victim_gml_id not in ids2, "victim_absent_v3": victim_gml_id not in ids3,
        "siblings_present_v2": sibling_gml_ids <= ids2, "siblings_present_v3": sibling_gml_ids <= ids3,
        "lint2": lint2, "lint3": lint3,
    }))


# ---------------------------------------------------------------------------
# Legacy-mirror fault injection: consistency gate refuses, never corrupted export
# ---------------------------------------------------------------------------

def legacy_mirror_corruption_refuses_ir_eligibility(R):
    from city_modeler.shared.authoring_core.blender import export_dispatch

    building, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("legacy_mirror_corruption_refuses_ir_eligibility", False, "no promoted RoofSurface found")
        return
    original_gml_id = str(roof.get("gml_id"))

    plan_before = export_dispatch.building_export_plan(building)
    eligible_before = plan_before.eligibility.eligible

    # Corrupt the live object's own semantic-identity mirror (its ``gml_id``
    # custom property) without touching the stored record - the "object
    # property id" mirror point 16 names.
    roof["gml_id"] = original_gml_id + "_CORRUPTED"

    plan_after = export_dispatch.building_export_plan(building)
    eligible_after = plan_after.eligibility.eligible

    # A genuine record/legacy mismatch is a BLOCKING consistency finding
    # (domain/consistency.py::BLOCKING), so the whole export operation
    # refuses outright rather than silently falling back to a legacy
    # export that might leak the corrupted value - stricter, and safer,
    # than a mere Building-level IR-ineligibility.
    export_refused = False
    corrupted_id_exported = False
    out_path = h.out_dir() / "p6_5_legacy_mirror_fault.gml"
    try:
        text = h.export_scene(str(out_path), "2.0")
        corrupted_id_exported = (original_gml_id + "_CORRUPTED") in text
    except RuntimeError:
        export_refused = True

    roof["gml_id"] = original_gml_id

    ok = eligible_before and not eligible_after and (export_refused or not corrupted_id_exported)
    R.check("legacy_mirror_corruption_refuses_ir_eligibility", ok, json.dumps({
        "eligible_before": eligible_before, "eligible_after": eligible_after,
        "reason_after": plan_after.eligibility.reason,
        "export_refused": export_refused, "corrupted_id_exported": corrupted_id_exported,
    }))


# ---------------------------------------------------------------------------
# Shared-material duplicate hazard: record ids win, never shared-material identity
# ---------------------------------------------------------------------------

def shared_material_duplicate_hazard_never_exports_source_identity(R):
    from city_modeler.shared.authoring_core.blender import (
        export_dispatch, roof_usecases, storage,
    )

    building, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("shared_material_duplicate_hazard_never_exports_source_identity", False,
                "no promoted RoofSurface found")
        return
    source_gml_id = str(roof.get("gml_id"))

    # Raw Shift+D-equivalent only: material slots still linked to the exact
    # same datablocks, and the record copied verbatim (same gml_id) - the
    # P6.4 hazard, before roof_usecases fixes identity.
    raw_duplicate = _native_duplicate(roof)
    materials_shared = any(
        slot.material is not None and slot.material in [s.material for s in roof.material_slots]
        for slot in raw_duplicate.material_slots
    )

    plan_with_raw_duplicate = export_dispatch.building_export_plan(building)
    eligible_with_raw_duplicate = plan_with_raw_duplicate.eligibility.eligible

    exported_source_id_twice = False
    if eligible_with_raw_duplicate:
        out_path = h.out_dir() / "p6_5_shared_material_raw.gml"
        text = h.export_scene(str(out_path), "2.0")
        ids = h.collect_ids(text).get("RoofSurface", ())
        exported_source_id_twice = ids.count(source_gml_id) > 1

    replacement = roof_usecases.initialize_roof_duplicate(raw_duplicate)
    new_gml_id = str(replacement.get("gml_id"))
    materials_independent = not any(
        slot.material is not None and slot.material in [s.material for s in roof.material_slots]
        for slot in replacement.material_slots
    )

    plan_after_fix = export_dispatch.building_export_plan(building)
    eligible_after_fix = plan_after_fix.eligibility.eligible

    out_path2 = h.out_dir() / "p6_5_shared_material_fixed.gml"
    text2 = h.export_scene(str(out_path2), "2.0")
    ids2 = h.collect_ids(text2).get("RoofSurface", ())

    ok = (
        materials_shared  # the hazard precondition actually existed
        and not exported_source_id_twice  # never silently exported source ids for the duplicate
        and new_gml_id != source_gml_id
        and materials_independent
        and eligible_after_fix
        and ids2.count(source_gml_id) == 1 and ids2.count(new_gml_id) == 1
    )
    R.check("shared_material_duplicate_hazard_never_exports_source_identity", ok, json.dumps({
        "source": source_gml_id, "new": new_gml_id,
        "materials_shared_before_fix": materials_shared,
        "eligible_with_raw_duplicate": eligible_with_raw_duplicate,
        "exported_source_id_twice": exported_source_id_twice,
        "materials_independent_after_fix": materials_independent,
        "eligible_after_fix": eligible_after_fix,
    }))


# ---------------------------------------------------------------------------
# Read-only export
# ---------------------------------------------------------------------------

def export_is_read_only_for_roof_state(R):
    from city_modeler.shared.authoring_core.blender import registry

    _, roofs = _prepare_promoted_railway("2.0")
    if not roofs:
        R.check("export_is_read_only_for_roof_state", False, "no promoted RoofSurface found")
        return

    object_keys = ("gml_id", "_cgml_record")
    before = h.snapshot_objects(object_keys=object_keys, material_keys=("gml_polygon_id",))
    registry_before = sorted(getattr(registry.REGISTRY, "_by_gml_id", {}).keys())

    _export_both("p6_5_readonly")

    after = h.snapshot_objects(object_keys=object_keys, material_keys=("gml_polygon_id",))
    registry_after = sorted(getattr(registry.REGISTRY, "_by_gml_id", {}).keys())

    ok = before == after and registry_before == registry_after
    R.check("export_is_read_only_for_roof_state", ok, json.dumps({
        "snapshot_equal": before == after, "registry_equal": registry_before == registry_after,
    }))


# ---------------------------------------------------------------------------
# Save/reload export parity
# ---------------------------------------------------------------------------

def save_reload_then_export_parity(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import registry, roof_usecases

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("save_reload_then_export_parity", False, "no promoted RoofSurface found")
        return

    duplicate = _native_duplicate(roof)
    roof_usecases.initialize_roof_duplicate(duplicate)

    (_, before_v2), (_, before_v3) = _export_both("p6_5_save_reload_before")
    fp_before_v2 = h.export_fingerprint(before_v2)
    fp_before_v3 = h.export_fingerprint(before_v3)

    path = str(h.out_dir() / "p6_5_roof_export_save_reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    registry.REGISTRY.mark_dirty()

    (_, after_v2), (_, after_v3) = _export_both("p6_5_save_reload_after")
    fp_after_v2 = h.export_fingerprint(after_v2)
    fp_after_v3 = h.export_fingerprint(after_v3)

    ok = fp_before_v2 == fp_after_v2 and fp_before_v3 == fp_after_v3
    R.check("save_reload_then_export_parity", ok, json.dumps({
        "v2_equal": fp_before_v2 == fp_after_v2, "v3_equal": fp_before_v3 == fp_after_v3,
    }))


# ---------------------------------------------------------------------------
# Undo/redo export parity
# ---------------------------------------------------------------------------

def undo_redo_export_tracks_authoritative_record_state(R):
    import bpy

    _, roofs = _prepare_promoted_railway("2.0")
    roof = _multi_polygon_roof(roofs)
    if roof is None:
        R.check("undo_redo_export_tracks_authoritative_record_state", False, "no promoted RoofSurface found")
        return

    (_, state_a_v2), _ = _export_both("p6_5_undo_state_a")
    fp_a = h.export_fingerprint(state_a_v2)

    bpy.ops.ed.undo_push(message="cac: before p6.5 roof edit")
    _begin_op(roof)
    _translate(roof, dx=0.42, dy=0.08, dz=0.0)
    commit_result = _commit_op(roof)
    bpy.ops.ed.undo_push(message="cac: after p6.5 roof edit")

    (_, state_b_v2), _ = _export_both("p6_5_undo_state_b")
    fp_b = h.export_fingerprint(state_b_v2)

    bpy.ops.ed.undo()
    (_, undone_v2), _ = _export_both("p6_5_undo_undone")
    fp_undone = h.export_fingerprint(undone_v2)

    bpy.ops.ed.redo()
    (_, redone_v2), _ = _export_both("p6_5_undo_redone")
    fp_redone = h.export_fingerprint(redone_v2)

    ok = (
        commit_result == {"FINISHED"}
        and fp_a != fp_b
        and fp_undone == fp_a
        and fp_redone == fp_b
    )
    R.check("undo_redo_export_tracks_authoritative_record_state", ok, json.dumps({
        "commit_result": str(commit_result),
        "a_ne_b": fp_a != fp_b, "undone_eq_a": fp_undone == fp_a, "redone_eq_b": fp_redone == fp_b,
    }))


# ---------------------------------------------------------------------------
# Cross-version (v2/v3) normalized semantic parity for the whole Roof slice
# ---------------------------------------------------------------------------

def v2_v3_roof_semantic_fingerprint_parity(R):
    _, roofs = _prepare_promoted_railway("2.0")
    if not roofs:
        R.check("v2_v3_roof_semantic_fingerprint_parity", False, "no promoted RoofSurface found")
        return

    (v2_path, v2_text), (v3_path, v3_text) = _export_both("p6_5_parity")
    ids2 = h.collect_ids(v2_text)
    ids3 = h.collect_ids(v3_text)

    roofs2 = set(ids2.get("RoofSurface", ()))
    roofs3 = set(ids3.get("RoofSurface", ()))
    polys2 = set(ids2.get("Polygon", ()))
    polys3 = set(ids3.get("Polygon", ()))
    rings2 = set(ids2.get("LinearRing", ()))
    rings3 = set(ids3.get("LinearRing", ()))

    ok = roofs2 == roofs3 and polys2 == polys3 and rings2 == rings3 and len(roofs2) > 0
    R.check("v2_v3_roof_semantic_fingerprint_parity", ok, json.dumps({
        "roofs_equal": roofs2 == roofs3, "n_roofs": len(roofs2),
        "polys_equal": polys2 == polys3, "n_polys": len(polys2),
        "rings_equal": rings2 == rings3, "n_rings": len(rings2),
    }))


def main():
    R = h.Results(__file__)
    checks = [
        cs1_building_exports_without_mutating_records,
        railway_roof_export_is_d6_eligible_and_preserves_record_identity,
        edited_roof_export_reflects_committed_coordinates,
        generic_interior_ring_exported_with_correct_nesting,
        multi_polygon_roof_exported_fully,
        duplicate_export_distinct_ids_no_duplicates,
        duplicate_hole_bearing_export_preserves_topology_distinct_ids,
        delete_export_absence_and_sibling_preservation,
        legacy_mirror_corruption_refuses_ir_eligibility,
        shared_material_duplicate_hazard_never_exports_source_identity,
        export_is_read_only_for_roof_state,
        save_reload_then_export_parity,
        undo_redo_export_tracks_authoritative_record_state,
        v2_v3_roof_semantic_fingerprint_parity,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
