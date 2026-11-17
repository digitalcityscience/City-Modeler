# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P6.1a end-to-end verification (real Blender, real fixture).

Complements ``test_cac_p6_1_roof_promotion.py`` (which already tightened
its ``n_hole_bearing_still_ids_only == 0`` assertion for CAC-P6.1a) with
the deeper proof the P6.1a verification directive asked for and that file
does not attempt: exact before/after capture of the two real hole-bearing
RoofSurface polygons in ``CS1_lod3.gml``, positional ring<->coordinate
association stability across a fresh ``geometry_fact`` re-derivation and
across save/reload, an actual ``ir_adapter -> ir_to_groups`` conversion of
the committed candidate (not just ``RecordSet.validate()``), and an
injected write failure targeted at one of the two real hole-bearing Roofs
specifically.

Verification only - no production code is exercised here that
``test_cac_p6_1_roof_promotion.py``/the unit suite do not already exercise
by a different angle; nothing in this file changes what gets promoted or
how.

Run (same convention as every other CAC headless test)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_1a
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

CS1 = h.FIXTURES / "Building" / "CitySmith" / "CS1_lod3.gml"


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


def _building_scope(building_obj):
    from city_modeler.shared.authoring_core.blender.building_root import building_scope

    return building_scope(building_obj)


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


def _setup():
    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    return _building_object()


def _roof_legacy_snapshot(scope):
    """Every RoofSurface's legacy ``(feature, polygons)`` pairs, and the two
    hole-bearing polygons among them, read fresh - the same oracle
    ``promote_building`` itself reads from."""
    from city_modeler.shared.authoring_core.blender import legacy_views

    legacy = legacy_views.read_scene(scope).records
    roofs = [f for f in legacy.features if f.kind == "RoofSurface"]
    all_polys = []
    hole_bearing = []
    for f in roofs:
        for p in legacy.polygons_of(f.gml_id):
            all_polys.append((f, p))
            if p.interiors:
                hole_bearing.append((f, p))
    return roofs, all_polys, hole_bearing


def _generic_rings_snapshot(scope):
    from city_modeler.shared.authoring_core.blender import consistency_view

    geometry = consistency_view.geometry_fact(scope)
    return geometry, dict(geometry.generic_rings)


def verify_cs1_hole_bearing_count_matches_p6_1(R):
    """CS1_lod3.gml really has 2 hole-bearing Roof polygons - the exact
    P6.1 finding, re-confirmed independently before trusting anything
    downstream. (The load-bearing figure is the hole-bearing count; a
    re-count of the *total* polygon figure here is reported for the
    record, not asserted - P6.1's narrative text said "33 Roof polygons",
    but a fresh, direct count finds 33 RoofSurface *features* and 29
    polygons total across them; the docstring conflated the two. The
    2-hole-bearing figure it also reported is exact.)"""
    building = _setup()
    if building is None:
        R.check("verify_cs1_hole_bearing_count_matches_p6_1", False, "setup: no Building")
        return
    scope = _building_scope(building)
    roofs, all_polys, hole_bearing = _roof_legacy_snapshot(scope)
    ok = len(hole_bearing) == 2
    R.check("verify_cs1_hole_bearing_count_matches_p6_1", ok, json.dumps({
        "n_roof_features": len(roofs), "n_roof_polygons": len(all_polys),
        "n_hole_bearing": len(hole_bearing),
        "hole_bearing_ids": [(f.gml_id, p.polygon_id) for f, p in hole_bearing],
    }))


def verify_before_after_promotion_on_the_two_real_hole_bearing_polygons(R):
    """Full before/after capture on CS1's real two hole-bearing Roof
    polygons: identity (gml_id/polygon_id/ring ids/LoD/owner-parent)
    unchanged, exterior and every interior ring reach
    ``geometry_state == 'committed'``, coordinates are exactly the live
    Blender-world-frame loop ``geometry_fact`` read (D-2, no CRS/origin
    transform), and no polygon is left partially committed."""
    from city_modeler.shared.authoring_core.blender import storage

    building = _setup()
    if building is None:
        R.check("verify_before_after_promotion_on_the_two_real_hole_bearing_polygons", False, "setup: no Building")
        return
    scope = _building_scope(building)
    roofs, _all_polys, hole_bearing = _roof_legacy_snapshot(scope)
    geometry, generic_rings = _generic_rings_snapshot(scope)

    before = {}
    for feature, polygon in hole_bearing:
        want_exterior_coords = geometry.polygons.get(polygon.polygon_id)
        want_interior_coords = generic_rings.get(polygon.polygon_id, ())
        before[(feature.gml_id, polygon.polygon_id)] = {
            "feature": feature, "polygon": polygon,
            "exterior_ring_id": polygon.exterior.ring_id,
            "interior_ring_ids": tuple(r.ring_id for r in polygon.interiors),
            "geometry_state_before": polygon.geometry_state,
            "want_exterior_coords": want_exterior_coords,
            "want_interior_coords": want_interior_coords,
        }

    result, error = _promote(building)

    ok = result == {'FINISHED'} and not error
    detail = {"result": str(result), "error": error, "polygons": {}}
    for (fgid, pid), snap in before.items():
        obj = next((o for o in scope if str(o.get("gml_id") or "") == fgid), None)
        record_set = storage.read_record(obj) if obj is not None else None
        entry = {
            "geometry_state_before": snap["geometry_state_before"],
            "n_interior_rings": len(snap["interior_ring_ids"]),
        }
        if record_set is None:
            ok = False
            entry["error"] = "no record after promotion"
            detail["polygons"][pid] = entry
            continue
        feature_after = record_set.feature(fgid)
        poly_after = next((p for p in record_set.polygons if p.polygon_id == pid), None)
        if feature_after is None or poly_after is None:
            ok = False
            entry["error"] = "feature/polygon missing after promotion"
            detail["polygons"][pid] = entry
            continue

        identity_ok = (
            feature_after.gml_id == snap["feature"].gml_id
            and feature_after.lod == snap["feature"].lod
            and feature_after.parent_id == snap["feature"].parent_id
            and feature_after.owner_id == snap["feature"].owner_id
            and poly_after.polygon_id == pid
            and poly_after.exterior.ring_id == snap["exterior_ring_id"]
            and tuple(r.ring_id for r in poly_after.interiors) == snap["interior_ring_ids"]
        )
        committed_ok = (
            poly_after.geometry_state == "committed"
            and len(set(poly_after.exterior.coords)) >= 3
            and all(len(set(r.coords)) >= 3 for r in poly_after.interiors)
        )
        coords_ok = (
            snap["want_exterior_coords"] is not None
            and poly_after.exterior.coords == tuple(tuple(c) for c in snap["want_exterior_coords"])
            and tuple(r.coords for r in poly_after.interiors) == snap["want_interior_coords"]
        )
        no_partial = not (
            (poly_after.geometry_state == "committed") ^ bool(poly_after.exterior.coords)
        ) and all(bool(r.coords) == (poly_after.geometry_state == "committed") for r in poly_after.interiors)

        entry.update({
            "identity_ok": identity_ok, "committed_ok": committed_ok,
            "coords_ok": coords_ok, "no_partial": no_partial,
            "geometry_state_after": poly_after.geometry_state,
        })
        ok = ok and identity_ok and committed_ok and coords_ok and no_partial
        detail["polygons"][pid] = entry

    R.check("verify_before_after_promotion_on_the_two_real_hole_bearing_polygons", ok, json.dumps(detail))


def verify_positional_ring_association_is_stable_across_rederivation_and_reload(R):
    """The generic-ring fix matches interior rings to live mesh faces
    positionally, not by a stable key. Prove that two independent
    ``geometry_fact`` derivations of the *same, unpromoted* scene - one
    right after import, one after a save/reload round trip - produce the
    exact same ring-index -> coordinate association for both real
    hole-bearing polygons, and that the already-promoted record's own
    ring<->coordinate pairing survives the same round trip unchanged."""
    from city_modeler.shared.authoring_core.blender import storage

    building = _setup()
    if building is None:
        R.check("verify_positional_ring_association_is_stable_across_rederivation_and_reload", False,
                "setup: no Building")
        return
    building_gml_id = str(building.get("gml_id") or "").strip()
    scope = _building_scope(building)
    _roofs, _all_polys, hole_bearing = _roof_legacy_snapshot(scope)
    _geometry_a, generic_rings_a = _generic_rings_snapshot(scope)

    result, error = _promote(building)
    if result != {'FINISHED'}:
        R.check("verify_positional_ring_association_is_stable_across_rederivation_and_reload", False,
                f"setup promote failed: {error}")
        return

    promoted = {}
    for feature, polygon in hole_bearing:
        obj = next((o for o in scope if str(o.get("gml_id") or "") == feature.gml_id), None)
        record_set = storage.read_record(obj)
        poly_after = next(p for p in record_set.polygons if p.polygon_id == polygon.polygon_id)
        promoted[polygon.polygon_id] = tuple(r.coords for r in poly_after.interiors)

    import bpy

    path = str(h.out_dir() / "p6_1a_positional_stability.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)

    from city_modeler.shared.authoring_core.blender import registry

    registry.REGISTRY.mark_dirty()

    ok = True
    detail = {}
    for feature, polygon in hole_bearing:
        obj = registry.resolve(feature.gml_id)
        after_reload_record_set = storage.read_record(obj) if obj is not None else None
        poly_reloaded = None
        if after_reload_record_set is not None:
            poly_reloaded = next(
                (p for p in after_reload_record_set.polygons if p.polygon_id == polygon.polygon_id), None,
            )
        reloaded_coords = tuple(r.coords for r in poly_reloaded.interiors) if poly_reloaded is not None else None
        stable = reloaded_coords == promoted[polygon.polygon_id]
        ok = ok and obj is not None and poly_reloaded is not None and stable
        detail[polygon.polygon_id] = {
            "promoted_interior_coords_hash": hash(promoted[polygon.polygon_id]),
            "reloaded_interior_coords_hash": hash(reloaded_coords) if reloaded_coords is not None else None,
            "stable_across_reload": stable,
        }

    # A fresh geometry_fact derivation on the reloaded (still-unmodified)
    # mesh must associate the same live faces with the same polygon in the
    # same order - the positional key (ExteriorPolyId + ascending face
    # index) does not depend on anything the promotion itself touched.
    reloaded_scope = _building_scope(registry.resolve(building_gml_id))
    _geometry_b, generic_rings_b = _generic_rings_snapshot(reloaded_scope)
    for _feature, polygon in hole_bearing:
        rederived_stable = generic_rings_b.get(polygon.polygon_id) == generic_rings_a.get(polygon.polygon_id)
        ok = ok and rederived_stable
        detail.setdefault(polygon.polygon_id, {})["rederivation_stable"] = rederived_stable

    R.check("verify_positional_ring_association_is_stable_across_rederivation_and_reload", ok, json.dumps(detail))


def verify_repeat_promotion_is_idempotent_no_churn(R):
    """Promoting the same, already-promoted Building a second time must be
    a reported no-op with zero id churn, zero ring reshuffling and zero
    coordinate reassignment on the two real hole-bearing Roof polygons."""
    from city_modeler.shared.authoring_core.blender import storage

    building = _setup()
    if building is None:
        R.check("verify_repeat_promotion_is_idempotent_no_churn", False, "setup: no Building")
        return
    scope = _building_scope(building)
    _roofs, _all_polys, hole_bearing = _roof_legacy_snapshot(scope)

    result1, error1 = _promote(building)
    snapshot1 = {}
    for feature, polygon in hole_bearing:
        obj = next((o for o in scope if str(o.get("gml_id") or "") == feature.gml_id), None)
        snapshot1[polygon.polygon_id] = storage.read_record(obj).polygon(polygon.polygon_id) if obj is not None else None

    result2, error2 = _promote(building)
    snapshot2 = {}
    for feature, polygon in hole_bearing:
        obj = next((o for o in scope if str(o.get("gml_id") or "") == feature.gml_id), None)
        snapshot2[polygon.polygon_id] = storage.read_record(obj).polygon(polygon.polygon_id) if obj is not None else None

    ok = result1 == {'FINISHED'} and not error1 and result2 == {'FINISHED'} and not error2
    for pid in snapshot1:
        a, b = snapshot1[pid], snapshot2[pid]
        same = (
            a is not None and b is not None
            and a.exterior.ring_id == b.exterior.ring_id
            and tuple(r.ring_id for r in a.interiors) == tuple(r.ring_id for r in b.interiors)
            and a.exterior.coords == b.exterior.coords
            and tuple(r.coords for r in a.interiors) == tuple(r.coords for r in b.interiors)
            and a.geometry_state == b.geometry_state == "committed"
        )
        ok = ok and same

    R.check("verify_repeat_promotion_is_idempotent_no_churn", ok, json.dumps({
        "result1": str(result1), "result2": str(result2),
    }))


def verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes(R):
    """The actual conversion path - not just ``RecordSet.validate()`` - of
    the committed candidate: ``ir_adapter.building_ir_from_root`` then
    ``ir_to_groups.groups_from_building_ir``, run against the real,
    just-promoted CS1 record set. The resulting generic
    ``ExportSurface``/``ExportPolygon``/``ExportRing`` graph must carry
    both real hole-bearing RoofSurfaces, with kind, ids, LoD and every
    ring's coordinates preserved - through the exact same generic
    conversion every other boundary surface already uses, no
    RoofSurface-specific branch anywhere in it."""
    from city_modeler.shared.authoring_core.blender import consistency_view, storage
    from city_modeler.shared.authoring_core.domain.ir_adapter import building_ir_from_root
    from city_modeler.shared.authoring_core.domain.ir_to_groups import groups_from_building_ir
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    building = _setup()
    if building is None:
        R.check("verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes", False, "setup: no Building")
        return
    scope = _building_scope(building)
    _roofs, _all_polys, hole_bearing = _roof_legacy_snapshot(scope)

    result, error = _promote(building)
    if result != {'FINISHED'}:
        R.check("verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes", False,
                f"setup promote failed: {error}")
        return

    building_gml_id = str(building.get("gml_id") or "").strip()
    merged = consistency_view.merged_record_set(scope)
    building_feature = FeatureRecord(gml_id=building_gml_id, kind="Building")

    ok = True
    detail = {}
    try:
        ir = building_ir_from_root(merged, building_feature)
        groups = groups_from_building_ir(ir)
    except Exception as exc:  # noqa: BLE001 - report, don't hide
        R.check("verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes", False,
                f"conversion raised: {exc}")
        return

    surfaces_by_id = {s.gml_id: s for s in groups.surfaces}
    for feature, polygon in hole_bearing:
        surface = surfaces_by_id.get(feature.gml_id)
        if surface is None:
            ok = False
            detail[feature.gml_id] = "surface missing from ExportBuildingGroups"
            continue
        poly = next((p for p in surface.polygons if p.polygon_id == polygon.polygon_id), None)
        if poly is None:
            ok = False
            detail[feature.gml_id] = "polygon missing from ExportSurface.polygons"
            continue

        want_record = next(p for p in merged.polygons if p.polygon_id == polygon.polygon_id)
        # ir_adapter.building_ir_from_root deterministically re-sorts a
        # polygon's interiors by ring_sort_key (plain ring_id string sort,
        # semantic_tree.py) - by design, unrelated to CAC-P6.1a - so rings
        # are compared here by identity (ring_id -> coords), not position.
        got_by_id = {r.ring_id: r.coords for r in poly.interiors}
        want_by_id = {r.ring_id: r.coords for r in want_record.interiors}
        kind_ok = surface.semantic_type == "RoofSurface"
        lod_ok = surface.lod == feature.lod
        ring_ids_ok = (
            poly.exterior.ring_id == want_record.exterior.ring_id
            and set(got_by_id) == set(want_by_id)
        )
        coords_ok = (
            poly.exterior.coords == want_record.exterior.coords
            and got_by_id == want_by_id
        )
        holes_present = len(poly.interiors) == len(want_record.interiors) > 0
        this_ok = kind_ok and lod_ok and ring_ids_ok and coords_ok and holes_present
        ok = ok and this_ok
        detail[feature.gml_id] = {
            "kind_ok": kind_ok, "lod_ok": lod_ok, "ring_ids_ok": ring_ids_ok,
            "coords_ok": coords_ok, "holes_present": holes_present,
            "n_interiors": len(poly.interiors),
        }

    R.check("verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes", ok, json.dumps(detail))


def verify_injected_failure_during_a_hole_bearing_roof_write_rolls_back_atomically(R):
    """Inject a write failure timed to land on one of the two real
    hole-bearing RoofSurface writes specifically (not just "the Nth write"
    generically) and prove the whole Building promotion is refused with
    every touched object's ``_cgml_record`` restored exactly - the
    exterior is never left committed while an interior ring of the same
    polygon is not, and no other object gains a record either."""
    from city_modeler.shared.authoring_core.blender import storage

    building = _setup()
    if building is None:
        R.check("verify_injected_failure_during_a_hole_bearing_roof_write_rolls_back_atomically", False,
                "setup: no Building")
        return
    scope = _building_scope(building)
    _roofs, _all_polys, hole_bearing = _roof_legacy_snapshot(scope)
    target_gml_id = hole_bearing[0][0].gml_id

    before = {o.name: (storage.RECORD_KEY in o.keys(), dict(o.get(storage.RECORD_KEY) or {}) if storage.RECORD_KEY in o.keys() else None) for o in scope}

    real_write_record = storage.write_record
    calls = {"n": 0, "hit_target": False}

    def _flaky(obj, record_set):
        calls["n"] += 1
        if str(obj.get("gml_id") or "") == target_gml_id:
            calls["hit_target"] = True
            raise RuntimeError("injected P6.1a generic-interior-ring write failure")
        real_write_record(obj, record_set)

    storage.write_record = _flaky
    try:
        _select([building])
        try:
            import bpy
            bpy.ops.cgml.promote_building('EXEC_DEFAULT')
            raised = False
        except RuntimeError:
            raised = True
    finally:
        storage.write_record = real_write_record

    after = {o.name: (storage.RECORD_KEY in o.keys(), dict(o.get(storage.RECORD_KEY) or {}) if storage.RECORD_KEY in o.keys() else None) for o in scope}

    fully_rolled_back = before == after
    ok = raised and calls["hit_target"] and fully_rolled_back
    R.check("verify_injected_failure_during_a_hole_bearing_roof_write_rolls_back_atomically", ok, json.dumps({
        "raised": raised, "hit_target": calls["hit_target"], "writes_attempted": calls["n"],
        "fully_rolled_back": fully_rolled_back,
    }))


def main():
    R = h.Results(__file__)
    checks = [
        verify_cs1_hole_bearing_count_matches_p6_1,
        verify_before_after_promotion_on_the_two_real_hole_bearing_polygons,
        verify_positional_ring_association_is_stable_across_rederivation_and_reload,
        verify_repeat_promotion_is_idempotent_no_churn,
        verify_ir_adapter_to_ir_to_groups_represents_committed_roof_holes,
        verify_injected_failure_during_a_hole_bearing_roof_write_rolls_back_atomically,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
