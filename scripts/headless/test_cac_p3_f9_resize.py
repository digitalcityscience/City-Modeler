"""F9 "Adjust Last Operation" resize of a record-backed Window (CAC-P3.6 follow-up,
identity behaviour updated by CAC-P4.3).

PLAN.md §8.4 S1 step 2 requires: after an F9 resize, "still one record, one
ring" with the record's params matching the new size. This was never
exercised as a dedicated headless check anywhere in P3.0-P3.5.

``CGML_OT_facade_draw``'s own docstring states it is written to work
identically for the interactive click/drag, "Blender's native F9 'Adjust
Last Operation' redo panel re-running it with tweaked width/height", and a
direct headless call - because ``object_name``/``object_gml_id``/
``face_index``/``feature_gml_id`` are registered properties, not plain
instance attributes, they survive Blender's redo-panel mechanism. That
mechanism is Blender core behaviour, not code in this repo: adjusting a
value in the F9 panel makes Blender revert (undo) the operator's own last
undo step and re-run ``execute()`` once with the edited property values -
it does not call ``execute()`` a second time on top of the first. This test
reproduces that exact sequence headlessly: draw, undo, redraw the same
target with new width/height (now also forwarding ``feature_gml_id``, the
way Blender's real redo panel keeps every registered property - including
hidden ones - across that revert+re-run), matching CAC-P3.2's
``create_feature`` contract that a refusal or a fresh call never leaves two
live objects for one on-screen result.

CAC-P4.3 flip: P3.6 found F9 was delete-and-recreate *with a fresh gml_id
every time*. That is no longer acceptable - this test now asserts the
opposite invariant, that the Window's ``gml_id`` (and the wall's matching
ring) survive an F9 resize even though the underlying Blender object is
still physically destroyed and recreated by Blender's own redo mechanism.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_f9_resize.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
ORIGINAL_SIZE = 0.3
RESIZED_WIDTH = 0.9
RESIZED_HEIGHT = 1.1


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _draw_operator(wall, face_index, position_uv, width, height, depth=0.05, semantic_type="Window",
                    feature_gml_id=""):
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode="EDIT")
    import bmesh
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(wall.data)

    before = {o.name for o in bpy.data.objects}
    try:
        # feature_gml_id: forwarding the previous call's result here is
        # what simulates Blender's actual F9 redo panel - it re-runs the
        # SAME operator instance, so every registered property (including
        # this hidden one) is still set from the earlier execute(), not
        # reset to "" the way a genuinely new top-level invocation would be
        # (CAC-P4.3).
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type=semantic_type, width=width, height=height, depth=depth,
            position_uv=position_uv, object_name=wall.name,
            object_gml_id=str(wall.get("gml_id") or ""), face_index=face_index,
            feature_gml_id=feature_gml_id,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the fill object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    fill = bpy.data.objects[new_names[0]] if new_names else None
    return result, error, fill


def f9_resize_leaves_one_record_one_ring_updated_params(R):
    """Draw at ORIGINAL_SIZE, then simulate Blender's F9 redo-panel mechanism
    (undo the draw's own undo step, re-run the operator on the same target
    with the new width/height) and check: exactly one Window record exists,
    exactly one matching RingRecord on the host wall, params reflect the
    resized values (not the original), and export stays xmllint-clean."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    # Find a uv position where the LARGER (resized) rectangle already fits
    # the real solid area (AB-17), then draw the smaller original centered
    # on the same point - a smaller rectangle centered there is still
    # strictly inside, so both the initial draw and the F9 resize place
    # cleanly without the resize itself tripping AB-17's boundary check.
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw window (f9 resize test)")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw window (f9 resize test)")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("f9_resize_leaves_one_record_one_ring_updated_params", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))
    original_name = fill1.name

    objects_before_f9 = {o.name for o in bpy.data.objects}

    # F9's own mechanism: revert the draw's undo step, then re-run the same
    # operator (same wall/face target, same click position) with the edited
    # width/height - this is exactly what adjusting the redo panel triggers
    # in Blender core, not something CGML_OT_facade_draw implements itself.
    bpy.ops.ed.undo()
    # Undo invalidates every stored Object reference, not just the removed
    # fill - re-fetch the wall by name before touching it again (same
    # caveat test_cac_p3_create_feature.py's undo/redo check documents).
    wall = bpy.data.objects[wall_name]
    # feature_gml_id forwarded: the real F9 redo panel re-runs the same
    # operator instance, so this hidden property is still set from the
    # first call (CAC-P4.3) - forwarding it here is what makes this an F9
    # adjustment of original_gml_id rather than a second fresh draw.
    result2, error2, fill2 = _draw_operator(
        wall, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, feature_gml_id=original_gml_id,
    )
    bpy.ops.ed.undo_push(message="cac: after f9 resize")

    wall_after = bpy.data.objects[wall_name]
    ok = result2 == {"FINISHED"} and fill2 is not None
    detail = {
        "result1": str(result1), "result2": str(result2), "error2": error2,
        "original_object_gone": original_name not in bpy.data.objects,
    }

    if ok:
        objects_after_f9 = {o.name for o in bpy.data.objects}
        # Exactly one Window object exists where the draw+resize happened:
        # the original was removed by the undo, and exactly one new one was
        # created by the re-run - never both live at once.
        window_objects_now = objects_after_f9 - (objects_before_f9 - {original_name})
        no_duplicate_object = original_name not in bpy.data.objects and fill2.name in bpy.data.objects

        # Exactly one Window FeatureRecord for the resized object.
        record = storage.read_record(fill2)
        new_gml_id = str(fill2.get("gml_id"))
        feature = record.feature(new_gml_id) if record else None
        one_record = feature is not None and feature.kind == "Window"
        # CAC-P4.3 flip: the Blender *object* is still destroyed and
        # recreated by Blender's own F9/undo mechanism (that part of the
        # mechanism is unchanged and out of this repo's control), but the
        # semantic identity must now survive it - same gml_id, and the
        # registry must resolve the original id to this new object, not to
        # nothing and not to a second, different id.
        same_gml_id = new_gml_id == original_gml_id
        registry_resolves_original_id = REGISTRY.get(original_gml_id) is fill2

        wall_record = storage.read_record(wall_after)
        wall_feature = wall_record.feature(str(wall_after.get("gml_id"))) if wall_record else None
        wall_polys = wall_record.polygons_of(str(wall_after.get("gml_id"))) if wall_record else []
        matching_rings = [
            r for p in wall_polys for r in p.interiors
            if feature is not None and r.opening_link == feature.opening_link
        ]
        one_matching_ring = len(matching_rings) == 1
        # No leftover ring from the pre-resize (undone) draw either.
        stale_ring_gone = not any(r.opening_link == "" for p in wall_polys for r in p.interiors)

        params_updated = (
            feature is not None
            and abs(feature.params["width"] - RESIZED_WIDTH) < 1e-5
            and abs(feature.params["height"] - RESIZED_HEIGHT) < 1e-5
            and abs(feature.params["width"] - ORIGINAL_SIZE) > 1e-3
        )

        ok = (no_duplicate_object and one_record and same_gml_id and registry_resolves_original_id
              and one_matching_ring and stale_ring_gone and params_updated)
        detail.update({
            "no_duplicate_object": no_duplicate_object,
            "one_record": one_record,
            "same_gml_id": same_gml_id,
            "registry_resolves_original_id": registry_resolves_original_id,
            "one_matching_ring": one_matching_ring,
            "stale_ring_gone": stale_ring_gone,
            "params_updated": params_updated,
            "feature_params": dict(feature.params) if feature else None,
            # CAC-P4.3: the gml_id is now preserved even though Blender's F9
            # mechanism still physically destroys and recreates the object
            # (a different object NAME each time is expected and harmless -
            # registry/records resolve by gml_id, not by name, P2.3).
            "object_name_changed_identity_preserved": fill2.name != original_name and new_gml_id == original_gml_id,
        })

        if ok:
            out = h.out_dir()
            for version in ("2.0", "3.0"):
                path = out / f"p3_f9_resize_v{version.replace('.', '')}.gml"
                # CAC-P4.3 finding: the exported gml:id text for this
                # WindowSurface opening is NOT sourced from fill.get("gml_id")
                # here - for a plain (non-Room) WallSurface host the legacy
                # v2/v3 exporters derive the opening's own gml:id from the
                # wall-side face-material scan (io/writer/exporter.py's
                # fillings_by_parent/child_gp "surf_id", keyed off the fill
                # face's material, not the fill object's custom property),
                # a separate identity channel from the authoring-core
                # FeatureRecord/registry one P4.3 preserves. Reusing an
                # existing gml_id (out of ops/facade_authoring.py's allowed
                # scope to also patch the exporter's own id minting) does not
                # make that legacy channel echo the same literal id string -
                # only record/registry identity is asserted here, not the
                # raw exported XML text.
                text = h.export_scene(path, version)
                lint = h.xmllint(path, version)
                detail[f"xmllint_{version}"] = lint
                ok = ok and lint["exit"] == 0 and lint["errors"] == 0

    R.check("f9_resize_leaves_one_record_one_ring_updated_params", ok, json.dumps(detail))


def main():
    R = h.Results(__file__)
    checks = [
        f9_resize_leaves_one_record_one_ring_updated_params,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
