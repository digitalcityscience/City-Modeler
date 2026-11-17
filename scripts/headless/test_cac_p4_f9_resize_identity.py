"""CAC-P4.3: F9 resize of a record-backed Window preserves its gml_id.

Supersedes ``test_cac_p3_f9_resize.py``'s P3.6 finding (delete-and-recreate,
a fresh gml_id every F9 adjustment - kept there only as a flipped,
identity-preserving regression now). This file is the dedicated P4.3
regression suite the task card asks for.

Mechanism (traced before any code changed, CAC-P4.3):

- ``CGML_OT_facade_draw`` (``ops/facade_authoring.py``) is one operator for
  interactive click/drag, Blender's F9 "Adjust Last Operation" redo panel,
  and a direct headless call - ``object_name``/``object_gml_id``/
  ``face_index`` are registered properties (not plain instance attributes)
  so they survive the redo panel.
- Its ``execute()`` always called ``usecases.create_feature(...)`` for a
  flag-on Window, which always minted a brand new ``gml_id`` (via
  ``place_semantic_element`` -> ``shared/facade_placement.py``'s internal
  ``uuid.uuid4()`` mint) - there was no way for execute() to tell "this is
  the first draw" from "this is an F9 adjustment of what I just drew".
- Blender's actual F9 mechanism (core, not this repo) reverts the scene to
  before the operator's own last ``execute()`` (undoing the object, its
  mesh, and both dual-written records - all part of that one undo step)
  and then re-runs ``execute()`` with the edited property values. So by the
  time execute() runs again, the previous fill object and its records are
  already gone from the scene - only the operator's own RNA properties
  (registered, including hidden ones) survive that revert, since they are
  window-manager state, not scene/undo state.
- CAC-P4.3 fix: a new hidden ``feature_gml_id`` operator property records
  the Window's own gml_id after a successful create. Empty means "first
  draw" (mint a fresh id, unchanged behaviour); non-empty means "this is an
  F9 adjustment of that feature" - execute() then calls
  ``create_feature(..., existing_gml_id=self.feature_gml_id)``, which
  places fresh geometry (still required - Blender already destroyed the
  old geometry before execute() re-ran) but stamps the *preserved* id onto
  the new fill object before deriving its record, instead of minting one.
  This is the same single ``create_feature`` code path a first draw uses
  (no second F9-specific mutation path), just with one extra optional
  keyword.

Finding worth carrying forward (recorded here, not fixed - out of
``ops/facade_authoring.py``/``usecases.py`` scope): the *record*/registry
identity channel (``fill["gml_id"]``, ``storage``, ``GmlRegistry``) is now
preserved end-to-end, but the *legacy exporter's own* id minting for a
WindowSurface filling a plain (non-Room) WallSurface is a separate channel
(``io/writer/exporter.py``'s wall-side face-material scan, keyed off the
fill face's material rather than the fill object's ``gml_id`` custom
property) that a reused id does not automatically re-label. Export stays
valid (xmllint-clean) either way; only the literal exported gml:id text can
differ from the preserved record identity. See the inline comment in
``test_cac_p3_f9_resize.py``.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p4_f9_resize_identity.py -- <out_dir>
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
RESIZED_AGAIN_WIDTH = 0.5
RESIZED_AGAIN_HEIGHT = 0.7
INVALID_WIDTH = 50.0  # far larger than the host wall - trips the AB-17 boundary check
INVALID_HEIGHT = 50.0


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _draw_operator(wall, face_index, position_uv, width, height, depth=0.05, semantic_type="Window",
                    feature_gml_id=""):
    """One F9-cycle step: re-runs ``cgml.facade_draw`` as a fresh top-level
    call, forwarding ``feature_gml_id`` the way Blender's real redo panel
    keeps every registered operator property (including hidden ones)
    across its implicit undo+re-execute."""
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
        result = bpy.ops.cgml.facade_draw(
            'EXEC_DEFAULT', semantic_type=semantic_type, width=width, height=height, depth=depth,
            position_uv=position_uv, object_name=wall.name,
            object_gml_id=str(wall.get("gml_id") or ""), face_index=face_index,
            feature_gml_id=feature_gml_id,
        )
        error = ""
    except RuntimeError as e:
        result, error = {"ERROR"}, str(e)
    # A successful record-backed create/F9 also creates its wall's/its own
    # ``.cgml_display`` auxiliary object(s) (CAC-P3b.3) - new too, but never
    # the fill object itself.
    new_names = [n for n in ({o.name for o in bpy.data.objects} - before) if not n.endswith(".cgml_display")]
    fill = bpy.data.objects[new_names[0]] if new_names else None
    return result, error, fill


def _f9_step(wall_name, face_index, uv, width, height, feature_gml_id):
    """Blender's actual F9 mechanism: revert the previous execute()'s own
    undo step, then re-run the operator with edited properties. Returns
    ``(result, error, fill, wall)`` with ``wall`` re-fetched by name (undo
    invalidates every stored Object reference, not just the removed fill)."""
    import bpy

    bpy.ops.ed.undo()
    wall = bpy.data.objects[wall_name]
    result, error, fill = _draw_operator(
        wall, face_index, uv, width, height, feature_gml_id=feature_gml_id,
    )
    bpy.ops.ed.undo_push(message="cac: after f9 step")
    return result, error, fill, bpy.data.objects[wall_name]


def _record_and_ring(fill, wall, gml_id):
    """``(feature, wall_feature, matching_rings)`` for ``gml_id``, or
    ``(None, None, [])`` pieces if unresolvable."""
    from city_modeler.shared.authoring_core.blender import storage

    record = storage.read_record(fill) if fill is not None else None
    feature = record.feature(gml_id) if record else None

    wall_record = storage.read_record(wall) if wall is not None else None
    wall_feature = wall_record.feature(str(wall.get("gml_id"))) if wall_record else None
    wall_polys = wall_record.polygons_of(str(wall.get("gml_id"))) if wall_record else []
    matching_rings = [
        r for p in wall_polys for r in p.interiors
        if feature is not None and r.opening_link == feature.opening_link
    ]
    return feature, wall_feature, matching_rings, wall_polys


def initial_draw_creates_new_gml_id(R):
    """Acceptance 1: a plain (non-F9) Draw Window still mints a fresh
    gml_id - the first-execution/create path is unaffected by CAC-P4.3."""
    import bpy

    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    uv = h.solid_area_position_uv(wall, face_index, ctx, ORIGINAL_SIZE / 2, ORIGINAL_SIZE / 2)
    result, error, fill = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)

    ok = result == {"FINISHED"} and fill is not None
    gml_id = str(fill.get("gml_id")) if ok else ""
    ok = ok and gml_id and REGISTRY.get(gml_id) is fill
    # A fresh-mint id, not left empty and not something the operator invented.
    ok = ok and gml_id.startswith("ID_")
    R.check("initial_draw_creates_new_gml_id", ok, json.dumps({
        "result": str(result), "error": error, "gml_id": gml_id,
    }))


def first_f9_resize_preserves_gml_id(R):
    """Acceptances 2, 4, 5, 6, 9: one F9 resize keeps the drawn Window's
    gml_id, leaves exactly one Window record and one matching wall
    RingRecord, updates width/height, and the registry still resolves the
    original id (to the new, resized object)."""
    import bpy

    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("first_f9_resize_preserves_gml_id", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))

    result2, error2, fill2, wall_after = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    ok = result2 == {"FINISHED"} and fill2 is not None
    detail = {"result2": str(result2), "error2": error2}
    if ok:
        new_gml_id = str(fill2.get("gml_id"))
        feature, wall_feature, matching_rings, wall_polys = _record_and_ring(fill2, wall_after, new_gml_id)
        same_gml_id = new_gml_id == original_gml_id
        registry_resolves_original_id = REGISTRY.get(original_gml_id) is fill2
        one_record = feature is not None and feature.kind == "Window"
        one_matching_ring = len(matching_rings) == 1
        params_updated = (
            feature is not None
            and abs(feature.params["width"] - RESIZED_WIDTH) < 1e-5
            and abs(feature.params["height"] - RESIZED_HEIGHT) < 1e-5
        )
        ok = same_gml_id and registry_resolves_original_id and one_record and one_matching_ring and params_updated
        detail.update({
            "same_gml_id": same_gml_id,
            "registry_resolves_original_id": registry_resolves_original_id,
            "one_record": one_record,
            "one_matching_ring": one_matching_ring,
            "params_updated": params_updated,
            "feature_params": dict(feature.params) if feature else None,
        })
    R.check("first_f9_resize_preserves_gml_id", ok, json.dumps(detail))


def sequential_f9_resizes_preserve_gml_id(R):
    """Acceptances 3, 4, 5, 7, 8: two F9 adjustments in a row still resolve
    to one Window record, one matching ring (following the final size), and
    the same gml_id throughout - not just across a single resize."""
    import bpy

    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("sequential_f9_resizes_preserve_gml_id", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))

    result2, error2, fill2, wall_after2 = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    if result2 != {"FINISHED"} or fill2 is None:
        R.check("sequential_f9_resizes_preserve_gml_id", False,
                f"first F9 step failed: result={result2} error={error2!r}")
        return
    gml_id_after_first = str(fill2.get("gml_id"))

    result3, error3, fill3, wall_after3 = _f9_step(
        wall_name, face_index, uv, RESIZED_AGAIN_WIDTH, RESIZED_AGAIN_HEIGHT, gml_id_after_first,
    )
    ok = result3 == {"FINISHED"} and fill3 is not None
    detail = {"result2": str(result2), "result3": str(result3), "error3": error3}
    if ok:
        final_gml_id = str(fill3.get("gml_id"))
        feature, wall_feature, matching_rings, wall_polys = _record_and_ring(fill3, wall_after3, final_gml_id)

        same_gml_id_throughout = final_gml_id == gml_id_after_first == original_gml_id
        registry_resolves_original_id = REGISTRY.get(original_gml_id) is fill3
        one_record = feature is not None and feature.kind == "Window"
        one_matching_ring = len(matching_rings) == 1
        # No leftover ring from either of the two undone intermediate draws.
        no_stale_rings = len(wall_polys[0].interiors) == 1 if wall_polys else False
        params_updated = (
            feature is not None
            and abs(feature.params["width"] - RESIZED_AGAIN_WIDTH) < 1e-5
            and abs(feature.params["height"] - RESIZED_AGAIN_HEIGHT) < 1e-5
        )
        # The wall ring's own coordinate extent follows the *final* resize,
        # not either earlier one - a coarse bbox check on the one matching
        # ring's world coords (acceptance 8).
        ring_follows_geometry = False
        if matching_rings:
            coords = matching_rings[0].coords
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            zs = [c[2] for c in coords]
            span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
            # Loosely: the ring's largest planar extent should track the
            # final (smaller, second-resize) footprint, not the first,
            # larger RESIZED_WIDTH/HEIGHT one.
            ring_follows_geometry = span < RESIZED_WIDTH and span > 0.05

        ok = (same_gml_id_throughout and registry_resolves_original_id and one_record
              and one_matching_ring and no_stale_rings and params_updated and ring_follows_geometry)
        detail.update({
            "same_gml_id_throughout": same_gml_id_throughout,
            "registry_resolves_original_id": registry_resolves_original_id,
            "one_record": one_record,
            "one_matching_ring": one_matching_ring,
            "no_stale_rings": no_stale_rings,
            "params_updated": params_updated,
            "ring_follows_geometry": ring_follows_geometry,
            "feature_params": dict(feature.params) if feature else None,
        })
    R.check("sequential_f9_resizes_preserve_gml_id", ok, json.dumps(detail))


def f9_resize_exports_v2_and_v3_clean(R):
    """Acceptances 10, 11: after an F9 resize, both CityGML 2 and CityGML 3
    export xmllint-clean (the record-identity preservation does not break
    the legacy dual-write export path)."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("f9_resize_exports_v2_and_v3_clean", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))

    result2, error2, fill2, wall_after = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    ok = result2 == {"FINISHED"} and fill2 is not None
    detail = {"result2": str(result2), "error2": error2}
    if ok:
        out = h.out_dir()
        for version in ("2.0", "3.0"):
            path = out / f"p4_f9_resize_v{version.replace('.', '')}.gml"
            h.export_scene(path, version)
            lint = h.xmllint(path, version)
            detail[f"xmllint_{version}"] = lint
            ok = ok and lint["exit"] == 0 and lint["errors"] == 0
    R.check("f9_resize_exports_v2_and_v3_clean", ok, json.dumps(detail))


def undo_redo_around_f9_resize_preserve_identity(R):
    """Acceptances 12, 13, characterised (not assumed) against the actual
    Blender undo stack: F9's "Adjust Last Operation" panel does not create
    a separate undo checkpoint per tweak - it keeps adjusting the *same*
    pending step, exactly like it does for any other redo-panel operator.
    So a single undo after an F9 resize does not land on "the Window at its
    original size" (there never was a separate undo step for that
    intermediate state to land on); it undoes the *whole* draw+adjust
    gesture back to "no Window at all" - itself a valid, uncorrupted scene
    state (no dangling record, no orphan ring). Redo then reapplies the
    resize, restoring the Window with its preserved gml_id and the resized
    params, with no semantic duplication (exactly one Window, one ring)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("undo_redo_around_f9_resize_preserve_identity", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))

    result2, error2, fill2, wall_after = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    ok = result2 == {"FINISHED"} and fill2 is not None
    detail = {"result2": str(result2), "error2": error2}
    if not ok:
        R.check("undo_redo_around_f9_resize_preserve_identity", False, json.dumps(detail))
        return

    # Undo the whole draw+F9-adjust gesture (one pending step, per the
    # docstring above) -> the scene as it was before the Window existed.
    bpy.ops.ed.undo()
    window_after_undo = REGISTRY.get(original_gml_id)
    undo_restores_clean_state = window_after_undo is None
    detail["undo_restores_clean_state"] = undo_restores_clean_state
    detail["window_after_undo"] = window_after_undo.name if window_after_undo else None

    # Redo -> forward again to the resized state, same gml_id, no duplicate.
    bpy.ops.ed.redo()
    window_after_redo = REGISTRY.get(original_gml_id)
    record_after_redo = storage.read_record(window_after_redo) if window_after_redo else None
    feature_after_redo = record_after_redo.feature(original_gml_id) if record_after_redo else None
    windows_now = [
        o for o in bpy.data.objects
        if str(o.get("cgml3_feature") or "").endswith("WindowSurface") and str(o.get("gml_id") or "") == original_gml_id
    ]
    redo_restores_resize = (
        window_after_redo is not None
        and feature_after_redo is not None
        and abs(feature_after_redo.params["width"] - RESIZED_WIDTH) < 1e-5
        and abs(feature_after_redo.params["height"] - RESIZED_HEIGHT) < 1e-5
        and len(windows_now) == 1
    )
    detail["redo_restores_resize"] = redo_restores_resize
    detail["redo_params"] = dict(feature_after_redo.params) if feature_after_redo else None
    detail["windows_now_count"] = len(windows_now)

    ok = undo_restores_clean_state and redo_restores_resize
    R.check("undo_redo_around_f9_resize_preserve_identity", ok, json.dumps(detail))


def invalid_f9_resize_does_not_mutate_state(R):
    """Acceptance 14: an F9 resize that fails validation (AB-17's rectangle-
    vs-host-face check, driven far outside the wall) leaves nothing
    partially written - redoing back past the failed attempt cleanly
    restores the still-valid pre-resize Window (same gml_id, original
    params, no orphan record/object)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, ORIGINAL_SIZE / 2, ORIGINAL_SIZE / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("invalid_f9_resize_does_not_mutate_state", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))
    fill1_name = fill1.name
    objects_before_attempt = {o.name for o in bpy.data.objects}

    # F9's own mechanism reverts the draw before re-running with the
    # (invalid, oversized) properties - exactly as a real F9 adjustment
    # that turns out to be invalid would.
    bpy.ops.ed.undo()
    wall = bpy.data.objects[wall_name]
    result2, error2, fill2 = _draw_operator(
        wall, face_index, uv, INVALID_WIDTH, INVALID_HEIGHT, feature_gml_id=original_gml_id,
    )

    refused_cleanly = result2 != {"FINISHED"} and fill2 is None
    objects_after_attempt = {o.name for o in bpy.data.objects}
    # The undo above also removes fill1's own display object(s) (CAC-P3b.3:
    # create_feature now regenerates them in the same undo step) - filter
    # both sides down to non-display objects so this stays a check on
    # semantic state only, same as before display objects existed.
    non_display = lambda names: {n for n in names if not n.endswith(".cgml_display")}
    no_object_created = refused_cleanly and non_display(objects_after_attempt) == non_display(
        objects_before_attempt - {fill1_name}
    )
    no_stale_registry_entry = REGISTRY.get(original_gml_id) is None
    detail = {
        "result2": str(result2), "error2": error2,
        "refused_cleanly": refused_cleanly,
        "no_object_created": no_object_created,
        "no_stale_registry_entry": no_stale_registry_entry,
    }

    # Nothing new was pushed for the refused attempt (Blender does not push
    # an undo step for a cancelled operator) - redo restores exactly what
    # the F9 mechanism's own implicit undo above removed: the still-valid,
    # original-size Window.
    bpy.ops.ed.redo()
    restored = REGISTRY.get(original_gml_id)
    restored_record = storage.read_record(restored) if restored else None
    restored_feature = restored_record.feature(original_gml_id) if restored_record else None
    state_restored = (
        restored is not None
        and restored_feature is not None
        and abs(restored_feature.params["width"] - ORIGINAL_SIZE) < 1e-5
        and abs(restored_feature.params["height"] - ORIGINAL_SIZE) < 1e-5
    )
    detail["state_restored_after_redo"] = state_restored
    detail["restored_params"] = dict(restored_feature.params) if restored_feature else None

    ok = refused_cleanly and no_object_created and no_stale_registry_entry and state_restored
    R.check("invalid_f9_resize_does_not_mutate_state", ok, json.dumps(detail))


def _exported_window_gml_ids(version):
    """``gml:id`` text of every WindowSurface/Window element in a fresh
    export of the current scene, for ``version`` ("2.0"/"3.0"). Exactly one
    is expected in every check that calls this (a single-Window fixture
    scene), asserted by the caller - this just does the raw ElementTree
    read, deliberately not ``cac_harness.export_fingerprint``'s id-
    normalising comparison (see CAC-P3.5's own note on why that would
    defeat an exact-id check by construction)."""
    import xml.etree.ElementTree as ET

    out = h.out_dir()
    path = out / f"p4_f9_identity_channels_v{version.replace('.', '')}.gml"
    xml_text = h.export_scene(path, version)
    root = ET.fromstring(xml_text)
    ids = []
    for elem in root.iter():
        if h.local_name(elem.tag) not in ("WindowSurface", "Window"):
            continue
        for gid_key in h.GML_IDS:
            if gid_key in elem.attrib:
                ids.append(elem.attrib[gid_key])
                break
    return ids


def _identity_snapshot(fill, wall, gml_id):
    """One row per identity channel (CAC-P4.3 follow-up §1/§2), captured at
    a single point in time: the record/registry/relationship channels this
    repo controls, the fill object's own material-property channel
    (``_unify_opening_ids``'s AB-09 invariant), and the two independently-
    derived exported gml:id channels (CityGML 2 legacy writer, CityGML 3
    writer)."""
    from city_modeler.shared.authoring_core.blender.registry import REGISTRY

    feature, wall_feature, matching_rings, wall_polys = _record_and_ring(fill, wall, gml_id)
    material_opening_gml_id = None
    material_con_surface_id = None
    if fill is not None and fill.data is not None and fill.data.materials:
        mat0 = fill.data.materials[0]
        if mat0 is not None:
            material_opening_gml_id = str(mat0.get("opening_gml_id") or "") or None
            material_con_surface_id = str(mat0.get("con_surface_id") or "") or None
    return {
        "feature_record_gml_id": feature.gml_id if feature else None,
        "object_gml_id": str(fill.get("gml_id")) if fill else None,
        "registry_resolved": REGISTRY.get(gml_id) is fill,
        "host_relation": feature.host_id if feature else None,
        "opening_link": feature.opening_link if feature else None,
        "ring_opening_link": matching_rings[0].opening_link if matching_rings else None,
        "material_opening_gml_id": material_opening_gml_id,
        "material_con_surface_id": material_con_surface_id,
        "exported_v2": _exported_window_gml_ids("2.0"),
        "exported_v3": _exported_window_gml_ids("3.0"),
    }


def f9_resize_identity_channels_characterised(R):
    """CAC-P4.3 follow-up items 1/2/3/4: capture every identity channel
    named in the follow-up prompt before and after one valid F9 resize.

    Asserted stable (the record/registry/relationship identity this repo
    owns end-to-end, plus the fill object's own material-property channel
    that ``_unify_opening_ids`` documents as required to equal the fill's
    ``gml_id`` - AB-09/P2.5.5): ``FeatureRecord.gml_id``, the Blender
    object's ``gml_id``, registry resolution, host relation, and the fill's
    own material ``opening_gml_id``/``con_surface_id`` properties.

    NOT asserted stable, by design, with evidence kept in the JSON detail:

    - ``opening_link``: this file's own module docstring and the trace in
      ``ring_opening_sync.py``/``facade_placement.py::_place_interior_ring_opening``
      establish it as an intentionally ephemeral *implementation* pairing
      key (never exported - confirmed absent from every ``io``/``io_v2``
      writer), re-minted together with the wall's ring every time Blender's
      F9 mechanism destroys and recreates that ring. The invariant asserted
      here is the *pairing* (the Window's ``opening_link`` still names its
      own single matching wall ring, both freshly matching after the
      resize), not literal value stability across the resize.
    - the exported CityGML 2/3 WindowSurface ``gml:id`` text: confirmed by
      direct instrumentation (CAC-P4.3 follow-up investigation) to be
      unaffected by the material-property fix above - the legacy writers'
      per-export scene-flattening pass derives this id from a further-
      downstream, decoupled mechanism outside ``usecases.py``/
      ``ops/facade_authoring.py``'s reach. This is the same known,
      documented gap the original P4.3 acceptance already carried forward
      (only export *validity*, not id-text equality, was ever the
      accepted contract - see ``f9_resize_exports_v2_and_v3_clean``).
      Closing it would require tracing and changing that flattening pass,
      which is outside this follow-up's allowed small-fix scope; recorded
      here as evidence, not asserted."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("f9_resize_identity_channels_characterised", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))

    before = _identity_snapshot(fill1, wall, original_gml_id)

    result2, error2, fill2, wall_after = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    ok = result2 == {"FINISHED"} and fill2 is not None
    detail = {"result2": str(result2), "error2": error2, "before": before}
    if ok:
        new_gml_id = str(fill2.get("gml_id"))
        after = _identity_snapshot(fill2, wall_after, new_gml_id)
        detail["after"] = after

        stable = {
            "feature_record_gml_id": before["feature_record_gml_id"] == after["feature_record_gml_id"],
            "object_gml_id": before["object_gml_id"] == after["object_gml_id"],
            "registry_resolved": before["registry_resolved"] and after["registry_resolved"],
            "host_relation": before["host_relation"] == after["host_relation"],
        }
        # The fill's own material-property channel must track its (now
        # preserved) object gml_id, both before and after - the AB-09
        # invariant this follow-up's fix restores for the F9 case.
        material_matches_object_before = (
            after["object_gml_id"] is not None
            and before["material_opening_gml_id"] == before["object_gml_id"]
            and before["material_con_surface_id"] == before["object_gml_id"]
        )
        material_matches_object_after = (
            after["object_gml_id"] is not None
            and after["material_opening_gml_id"] == after["object_gml_id"]
            and after["material_con_surface_id"] == after["object_gml_id"]
        )
        # opening_link: not asserted stable by value (see docstring) - only
        # that the pairing itself holds, both before and after.
        pairing_holds_before = before["opening_link"] is not None and before["opening_link"] == before["ring_opening_link"]
        pairing_holds_after = after["opening_link"] is not None and after["opening_link"] == after["ring_opening_link"]
        # Recorded as evidence only - NOT asserted (see docstring): the
        # exported gml:id text is a known, documented, pre-existing gap.
        exported_stable = {
            "exported_v2": before["exported_v2"] == after["exported_v2"],
            "exported_v3": before["exported_v3"] == after["exported_v3"],
        }
        detail["stable"] = stable
        detail["material_matches_object_before"] = material_matches_object_before
        detail["material_matches_object_after"] = material_matches_object_after
        detail["pairing_holds_before"] = pairing_holds_before
        detail["pairing_holds_after"] = pairing_holds_after
        detail["exported_stable_evidence_only"] = exported_stable

        ok = (
            all(stable.values())
            and material_matches_object_before
            and material_matches_object_after
            and pairing_holds_before
            and pairing_holds_after
        )
    R.check("f9_resize_identity_channels_characterised", ok, json.dumps(detail))


def exported_window_gml_id_changes_after_f9_resize(R):
    """AB-18 (known architectural bug, registered strict xfail,
    ``known_arch_bugs.json`` / PLAN.md §7): the legacy v2/v3 exporters'
    literal exported WindowSurface ``gml:id``, for an authored Window
    filling a plain (non-Room) WallSurface host, changes across an F9
    resize even though ``FeatureRecord.gml_id``, the Blender object's own
    ``gml_id``, registry identity and host relation are all preserved
    (CAC-P4.3), and even after the CAC-P4.3 follow-up's own fix keeps the
    fill object's material ``opening_gml_id``/``con_surface_id``
    properties in sync with its ``gml_id`` (see
    ``f9_resize_identity_channels_characterised``'s
    ``material_matches_object_*`` assertions, which do pass).

    Root cause (CAC-P4.3 follow-up investigation, direct runtime
    instrumentation of both writers, since reverted): a further-downstream,
    per-export scene-flattening pass in both ``io/writer/exporter.py`` and
    ``io_v2/writer/exporter.py`` derives this id from a source decoupled
    from both the fill object's own ``gml_id`` and its own material
    properties - the two channels ``usecases.py::create_feature``'s
    ``existing_gml_id`` override (and this follow-up's fix) actually
    control. This is not an XML-validity defect (export stays
    xmllint-clean either way - see ``f9_resize_exports_v2_and_v3_clean``)
    and is not a reopened AB-09 (AB-09 was the fill object/material
    identity split, fixed at P2.5.5 and confirmed still fixed by this same
    follow-up); it is a distinct, downstream exporter identity-source
    issue, expected to be resolved by Phase 5 (export adapter/IR - PLAN.md
    §6), which replaces this legacy scene-flattening writer path outright.
    Not a P4.4 target; the commit that fixes it in P5 removes this entry
    from ``known_arch_bugs.json`` in the same commit (PLAN.md §7 rule)."""
    import bpy

    _flag(True)
    wall, face_index, ctx = _prepare()
    wall_name = wall.name
    uv = h.solid_area_position_uv(wall, face_index, ctx, RESIZED_WIDTH / 2, RESIZED_HEIGHT / 2)

    bpy.ops.ed.undo_push(message="cac: before draw")
    result1, error1, fill1 = _draw_operator(wall, face_index, uv, ORIGINAL_SIZE, ORIGINAL_SIZE)
    bpy.ops.ed.undo_push(message="cac: after draw")
    if result1 != {"FINISHED"} or fill1 is None:
        R.check("exported_window_gml_id_changes_after_f9_resize", False,
                f"setup (initial draw) failed: result={result1} error={error1!r}")
        return
    original_gml_id = str(fill1.get("gml_id"))
    before_v2 = _exported_window_gml_ids("2.0")
    before_v3 = _exported_window_gml_ids("3.0")

    result2, error2, fill2, wall_after = _f9_step(
        wall_name, face_index, uv, RESIZED_WIDTH, RESIZED_HEIGHT, original_gml_id,
    )
    if result2 != {"FINISHED"} or fill2 is None:
        R.check("exported_window_gml_id_changes_after_f9_resize", False,
                f"F9 step failed: result={result2} error={error2!r}")
        return
    after_v2 = _exported_window_gml_ids("2.0")
    after_v3 = _exported_window_gml_ids("3.0")

    # ok=True would mean the bug is gone (the exported id stayed stable) -
    # deliberately the inverse of every other check in this file. A
    # passing run here means AB-18 is fixed and this entry must be removed
    # from known_arch_bugs.json in the same commit (PLAN.md §7 rule).
    exported_id_unchanged = before_v2 == after_v2 and before_v3 == after_v3
    R.check("exported_window_gml_id_changes_after_f9_resize", exported_id_unchanged, json.dumps({
        "before_v2": before_v2, "after_v2": after_v2,
        "before_v3": before_v3, "after_v3": after_v3,
    }))


def main():
    R = h.Results(__file__)
    checks = [
        initial_draw_creates_new_gml_id,
        first_f9_resize_preserves_gml_id,
        sequential_f9_resizes_preserve_gml_id,
        f9_resize_exports_v2_and_v3_clean,
        undo_redo_around_f9_resize_preserve_identity,
        invalid_f9_resize_does_not_mutate_state,
        f9_resize_identity_channels_characterised,
        exported_window_gml_id_changes_after_f9_resize,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
