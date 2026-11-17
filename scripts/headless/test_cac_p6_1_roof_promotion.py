# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Real-Blender proof for CAC-P6.1 (RoofSurface promotion reuse).

Complements ``scripts/tests/unit/test_cac_p6_1_roof_promotion.py`` (pure
domain-level proof, no bpy) with the parts only a real Blender scene and a
real fixture can prove: that the *unmodified* Phase-5 ``cgml.promote_building``
operator already promotes real, imported RoofSurface content - no
roof-specific promotion code was written for this, per the P6.0 §15
hypothesis this file exists to confirm or refute (see
``docs/agent/authoring-core/phase6-roof-design-note.md``).

Primary fixture: ``test_files/Building/CitySmith/CS1_lod3.gml`` (21
RoofSurface elements, LoD3, CityGML 2.0 - P6.0 §17). v2/v3 parity fixture:
the Railway LoD3 Building-only pair (3 RoofSurfaces each).

Run (same convention as every other CAC headless test, see
``scripts/headless/setup_cac_verification.ps1``)::

    setup_cac_verification.ps1 -SuiteOut <dir> -Only p6_1
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
    """Remove any feature kind outside the frozen Phase-5 promotion support
    matrix (P6.1 does not extend that matrix - Door/BuildingInstallation
    etc. stay exactly as P5.9 left them), and any Window without a real
    ``cgml_opening_link`` (plain-imported Windows never carry one - the
    same fixture limitation ``test_cac_p5_9_promote_building.py`` already
    documents). RoofSurface is left untouched: this is the feature under
    test and must reach ``build_promotion_candidate`` unmodified."""
    import bpy

    from city_modeler.shared.authoring_core.domain.export_eligibility import (
        ALLOWED_IR_KINDS, PASSTHROUGH_KINDS,
    )

    keep_kinds = (set(ALLOWED_IR_KINDS) | set(PASSTHROUGH_KINDS)) - {"Window"}
    removed = []
    for obj in list(bpy.data.objects):
        kind = _kind_of(obj)
        if kind in ("Building", "BuildingPart") or kind in keep_kinds:
            continue
        if not str(obj.get("gml_id") or "").strip():
            continue
        removed.append(obj.name)
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return removed


def _legacy_roof_facts(scope):
    """gml_id -> (polygon_ids, ring_ids, lod) read straight from legacy
    state (pre-promotion oracle), for the identity-preservation checks."""
    from city_modeler.shared.authoring_core.blender import legacy_views

    legacy = legacy_views.read_scene(scope).records
    out = {}
    for f in legacy.features:
        if f.kind != "RoofSurface":
            continue
        polys = legacy.polygons_of(f.gml_id)
        ring_ids = tuple(p.exterior.ring_id for p in polys) + tuple(
            r.ring_id for p in polys for r in p.interiors
        )
        out[f.gml_id] = (f.polygon_ids, ring_ids, f.lod)
    return out


def promote_real_roof_bearing_building_creates_roof_records(R):
    """CS1_lod3.gml: 33 real RoofSurface polygons (across ~20+ RoofSurface
    features), 2 of them hole-bearing. Promoting the Building must create a
    record-backed RoofSurface FeatureRecord for every one of them through
    the unmodified P5.9 operator, with ids/LoD preserved - the core
    promotion-reuse proof. Committed *geometry* is asserted separately per
    polygon shape: an exterior-only Roof polygon commits cleanly (the
    P6.1-supported slice), and - since CAC-P6.1a - so does a hole-bearing
    one: ``blender/promote_building.py::_commit_polygon`` used to only
    commit an interior ring when it carried a Window's ``opening_link``, so
    a Roof hole's interior ring (which has none) never did and left the
    whole polygon ``ids_only`` (see the P6.1 report / phase6-roof-design-
    note.md). ``_commit_generic_interior_rings`` (CAC-P6.1a) now commits it
    generically, matching the live mesh's interior-ring faces to the
    ``PolygonRecord``'s own interiors by deterministic face order rather
    than ``opening_link``."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    if building is None:
        R.check("promote_real_roof_bearing_building_creates_roof_records", False, "setup: no Building")
        return

    scope = _building_scope(building)
    roofs_before = [o for o in scope if _kind_of(o) == "RoofSurface"]
    legacy_facts = _legacy_roof_facts(scope)

    result, error = _promote(building)

    roofs = [o for o in scope if _kind_of(o) == "RoofSurface"]
    n_promoted = sum(1 for o in roofs if storage.has_record(o))
    identity_preserved = True
    n_exterior_only_committed = n_exterior_only_total = 0
    n_hole_bearing_still_ids_only = n_hole_bearing_total = 0
    for obj in roofs:
        gml_id = str(obj.get("gml_id") or "")
        record_set = storage.read_record(obj)
        if record_set is None:
            identity_preserved = False
            continue
        feature = record_set.feature(gml_id)
        want_polys, want_rings, want_lod = legacy_facts.get(gml_id, ((), (), None))
        if feature is None or feature.polygon_ids != want_polys or feature.lod != want_lod:
            identity_preserved = False
        for poly in record_set.polygons:
            got_rings = (poly.exterior.ring_id,) + tuple(r.ring_id for r in poly.interiors)
            if not set(got_rings) <= set(want_rings):
                identity_preserved = False
            if poly.interiors:
                n_hole_bearing_total += 1
                if poly.geometry_state != "committed":
                    n_hole_bearing_still_ids_only += 1
            else:
                n_exterior_only_total += 1
                if poly.geometry_state == "committed" and len(set(poly.exterior.coords)) >= 3:
                    n_exterior_only_committed += 1

    ok = (
        result == {'FINISHED'} and not error and len(roofs_before) >= 20
        and n_promoted == len(roofs) and identity_preserved
        and n_exterior_only_committed == n_exterior_only_total > 0
        # CAC-P6.1a: every hole-bearing Roof polygon in this fixture must
        # now commit fully too - the completeness gap P6.1 documented.
        and n_hole_bearing_still_ids_only == 0 and n_hole_bearing_total > 0
    )
    R.check("promote_real_roof_bearing_building_creates_roof_records", ok, json.dumps({
        "result": str(result), "error": error, "n_roofs": len(roofs), "n_promoted": n_promoted,
        "identity_preserved": identity_preserved,
        "exterior_only_committed": f"{n_exterior_only_committed}/{n_exterior_only_total}",
        "hole_bearing_still_ids_only": f"{n_hole_bearing_still_ids_only}/{n_hole_bearing_total}",
    }))


def promote_roof_injected_write_failure_rolls_back(R):
    """A failure injected mid-write during CS1's promotion must leave every
    object (Roof included) exactly as before - no partial Roof promotion."""
    from city_modeler.shared.authoring_core.blender import storage

    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    if building is None:
        R.check("promote_roof_injected_write_failure_rolls_back", False, "setup: no Building")
        return
    scope = _building_scope(building)
    before = {o.name: (storage.RECORD_KEY in o.keys()) for o in scope}

    real_write_record = storage.write_record
    calls = {"n": 0}

    def _flaky(obj, record_set):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("injected P6.1 roof promotion write failure")
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

    after = {o.name: (storage.RECORD_KEY in o.keys()) for o in scope}
    ok = raised and before == after and calls["n"] >= 3
    R.check("promote_roof_injected_write_failure_rolls_back", ok, json.dumps({
        "raised": raised, "fully_rolled_back": before == after, "writes_attempted": calls["n"],
    }))


def promote_roof_undo_redo_and_save_reload_round_trip(R):
    """Undo removes every promoted Roof record in one step, redo restores
    them, and a save/reload round trip preserves them and lets the
    registry resolve a Roof gml_id back to its (re-created) object."""
    from city_modeler.shared.authoring_core.blender import registry, storage

    _flag(True)
    h.import_fixture(CS1, "2.0", use_semantic_objects=True)
    _strip_unsupported()
    building = _building_object()
    if building is None:
        R.check("promote_roof_undo_redo_and_save_reload_round_trip", False, "setup: no Building")
        return

    import bpy

    scope_names = [o.name for o in _building_scope(building)]
    roof_names = [n for n in scope_names if _kind_of(bpy.data.objects[n]) == "RoofSurface"]

    def _by_name(names):
        return [bpy.data.objects[n] for n in names if n in bpy.data.objects]

    bpy.ops.ed.undo_push(message="cac: before p6.1 roof promote")
    result, error = _promote(building)
    bpy.ops.ed.undo_push(message="cac: after p6.1 roof promote")
    if result != {'FINISHED'}:
        R.check("promote_roof_undo_redo_and_save_reload_round_trip", False, f"setup promote failed: {error}")
        return

    bpy.ops.ed.undo()
    all_reverted = not any(storage.has_record(o) for o in _by_name(roof_names))
    bpy.ops.ed.redo()
    all_repromoted = all(storage.has_record(o) for o in _by_name(roof_names))

    roof_gml_ids = [str(bpy.data.objects[n].get("gml_id") or "") for n in roof_names]

    path = str(h.out_dir() / "p6_1_roof_promote_save_reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)

    registry.REGISTRY.mark_dirty()
    reload_records_preserved = True
    registry_resolves = True
    for gml_id in roof_gml_ids:
        obj = registry.resolve(gml_id)
        if obj is None or not storage.has_record(obj):
            reload_records_preserved = False
            registry_resolves = False
            continue
        if str(obj.get("gml_id") or "") != gml_id:
            registry_resolves = False

    ok = all_reverted and all_repromoted and reload_records_preserved and registry_resolves
    R.check("promote_roof_undo_redo_and_save_reload_round_trip", ok, json.dumps({
        "all_reverted": all_reverted, "all_repromoted": all_repromoted,
        "reload_records_preserved": reload_records_preserved, "registry_resolves": registry_resolves,
        "n_roofs": len(roof_names),
    }))


def promote_roof_v2_v3_parity_and_export_stays_xmllint_clean(R):
    """The Railway LoD3 v2/v3 Building-only pair (3 RoofSurfaces each):
    promoting either version's Building produces record-backed Roofs and
    the record-backed IR export path stays xmllint-clean, both versions -
    proving the generic IR/export reuse P6.0 §10 predicted, not just the
    promotion step."""
    _flag(True)
    detail = {}
    ok = True
    for version, path in (("2.0", RAILWAY_V2), ("3.0", RAILWAY_V3)):
        import bpy
        from city_modeler.shared.authoring_core.blender import storage

        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        h.import_fixture(path, version, use_semantic_objects=True)
        _strip_unsupported()
        building = _building_object()
        if building is None:
            ok = False
            detail[version] = "no Building object"
            continue
        scope = _building_scope(building)
        roofs = [o for o in scope if _kind_of(o) == "RoofSurface"]

        result, error = _promote(building)
        n_promoted = sum(1 for o in roofs if storage.has_record(o))

        out_path = h.out_dir() / f"p6_1_roof_railway_v{version.replace('.', '')}.gml"
        h.export_scene(str(out_path), version)
        lint = h.xmllint(out_path, version)

        version_ok = (
            result == {'FINISHED'} and not error and len(roofs) >= 3
            and n_promoted == len(roofs) and lint["exit"] == 0 and lint["errors"] == 0
        )
        ok = ok and version_ok
        detail[version] = {
            "result": str(result), "error": error, "n_roofs": len(roofs),
            "n_promoted": n_promoted, "lint": lint,
        }
    R.check("promote_roof_v2_v3_parity_and_export_stays_xmllint_clean", ok, json.dumps(detail))


def main():
    R = h.Results(__file__)
    checks = [
        promote_real_roof_bearing_building_creates_roof_records,
        promote_roof_injected_write_failure_rolls_back,
        promote_roof_undo_redo_and_save_reload_round_trip,
        promote_roof_v2_v3_parity_and_export_stays_xmllint_clean,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
