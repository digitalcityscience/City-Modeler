"""GS.2: post-commit display regeneration, real Blender mesh writing.

Scope: ``shared/authoring_core/blender/display_regen.py`` - the bpy-bound
half (``regenerate_committed_display``, called once from
``edit_session.commit()``, gs-design-note.md S4 item 2). The bpy-free
orchestration decision (``plan_regeneration``) is covered by the unit test
instead (``scripts/tests/unit/test_cac_gs2_display_regen.py``).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_gs2_display_regen.py -- <out_dir>
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _place_window(wall, face_index, ctx, avoid=(), depth=0.0):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2, avoid=avoid)
    # depth=0.0: a flat (non-extruded) Window keeps a single PolygonRecord,
    # the gs-design-note.md S3 "rectangular Window only" shape
    # ``build_surface_display`` (GS.1) expects. See
    # ``gs2_display_regen_skips_depth_authored_window`` for the box-shaped
    # (depth > 0) case GS.2 deliberately skips instead of crashing on.
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=depth)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    fill = create_feature("Window", ctx, definition, placement)
    return fill


def _translate(obj, dx=0.05, dy=0.0, dz=0.0):
    import bpy

    obj.location.x += dx
    obj.location.y += dy
    obj.location.z += dz
    bpy.context.view_layer.update()


def _mesh_area(obj):
    total = 0.0
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    for poly in obj.data.polygons:
        idx = list(poly.vertices)
        a = verts[idx[0]]
        for i in range(1, len(idx) - 1):
            b, c = verts[idx[i]], verts[idx[i + 1]]
            total += (b - a).cross(c - a).length * 0.5
    return total


def gs2_commit_creates_wall_and_window_display_objects(R):
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.blender.display_regen import DISPLAY_SURFACE_KEY

    _flag(True)
    wall, face_index, ctx = _prepare()
    # Snapshot before placement, not just before commit: since CAC-P3b.3,
    # create_feature itself already regenerates both display objects, so
    # "new since this commit" alone would no longer include them.
    before_names = {o.name for o in bpy.data.objects}
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    new_names = {o.name for o in bpy.data.objects} - before_names
    wall_display = bpy.data.objects.get(f"{wall.name}.cgml_display")
    window_display = bpy.data.objects.get(f"{fill.name}.cgml_display")

    wall_ok = (
        wall_display is not None and wall_display.name in new_names
        and wall_display.get(DISPLAY_SURFACE_KEY) == wall_gml_id
        and wall_display.data is not None and len(wall_display.data.polygons) > 0
    )
    window_ok = (
        window_display is not None and window_display.name in new_names
        and window_display.get(DISPLAY_SURFACE_KEY) == window_gml_id
        and window_display.data is not None and len(window_display.data.polygons) > 0
    )

    # The wall's display mesh must actually leave a hole: its area is the
    # committed exterior area minus the committed opening's area, not the
    # solid exterior area.
    wall_record = storage.read_record(wall).feature(wall_gml_id)
    wall_polygon = storage.read_record(wall).polygons_of(wall_record.gml_id)[0]

    def _loop_area(coords):
        import mathutils

        n = mathutils.Vector((0.0, 0.0, 0.0))
        m = len(coords)
        for i in range(m):
            ax, ay, az = coords[i]
            bx, by, bz = coords[(i + 1) % m]
            n.x += (ay - by) * (az + bz)
            n.y += (az - bz) * (ax + bx)
            n.z += (ax - bx) * (ay + by)
        return n.length * 0.5

    expected_area = _loop_area(wall_polygon.exterior.coords) - sum(
        _loop_area(r.coords) for r in wall_polygon.interiors
    )
    got_area = _mesh_area(wall_display) if wall_display is not None else -1.0
    # Real-fixture wall polygons are not perfectly planar; the 2D projection
    # build_surface_display uses (docs/agent/authoring-core/gs-design-note.md
    # S4 item 1) introduces a small, expected discrepancy. Same relative
    # tolerance the old Cut Openings display path (removed in B17) used for its area-agreement check.
    area_tol = max(1e-4, 0.02 * expected_area)
    area_ok = wall_display is not None and abs(got_area - expected_area) < area_tol

    ok = result.status == "committed" and wall_ok and window_ok and area_ok
    R.check(
        "gs2_commit_creates_wall_and_window_display_objects", ok,
        f"result={result} wall_ok={wall_ok} window_ok={window_ok} area_ok={area_ok} "
        f"got_area={got_area} expected_area={expected_area}",
    )


def gs2_display_regen_does_not_touch_legacy_mesh_or_ring(R):
    """GS.2 is additive only: the wall's/Window's own mesh objects (the
    legacy mirror ``ring_opening_sync.py`` owns) must be exactly what
    ``commit()`` already produced before GS.2 existed - same vertex/ring
    shape, no extra faces from the display rebuild leaking onto them."""
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    wall_faces_before = len(wall.data.polygons)
    window_faces_before = len(fill.data.polygons)

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    wall_faces_after = len(wall.data.polygons)
    window_faces_after = len(fill.data.polygons)
    ring_links = [link for _idx, _verts, link in wall_interior_ring_faces(wall) if link]

    ok = (
        result.status == "committed"
        and wall_faces_after == wall_faces_before
        and window_faces_after == window_faces_before
        and len(ring_links) == 1
    )
    R.check(
        "gs2_display_regen_does_not_touch_legacy_mesh_or_ring", ok,
        f"result={result} wall_faces_before={wall_faces_before} wall_faces_after={wall_faces_after} "
        f"window_faces_before={window_faces_before} window_faces_after={window_faces_after} "
        f"ring_links={ring_links}",
    )


def gs2_display_regen_is_idempotent_across_commits(R):
    """A second commit on the same Window reuses the same display objects
    (rewritten in place) instead of accumulating duplicates."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    es.commit(window_gml_id)

    wall_display_name = f"{wall.name}.cgml_display"
    window_display_name = f"{fill.name}.cgml_display"
    count_after_first = sum(
        1 for o in bpy.data.objects if o.name in (wall_display_name, window_display_name)
    )
    window_verts_after_first = tuple(tuple(v.co) for v in bpy.data.objects[window_display_name].data.vertices)

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    count_after_second = sum(
        1 for o in bpy.data.objects if o.name in (wall_display_name, window_display_name)
    )
    window_verts_after_second = tuple(tuple(v.co) for v in bpy.data.objects[window_display_name].data.vertices)

    ok = (
        result.status == "committed"
        and count_after_first == 2
        and count_after_second == 2
        and window_verts_after_first != window_verts_after_second
    )
    R.check(
        "gs2_display_regen_is_idempotent_across_commits", ok,
        f"result={result} count_after_first={count_after_first} count_after_second={count_after_second} "
        f"verts_changed={window_verts_after_first != window_verts_after_second}",
    )


def gs2_no_op_commit_does_not_touch_display_objects(R):
    """Checksum-unchanged commit stays a clean no-op (unchanged commit()
    contract) - GS.2's regeneration call sits after the dual-write, so it
    must never run for a no-op."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    before_names = {o.name for o in bpy.data.objects}

    es.begin(window_gml_id)
    result = es.commit(window_gml_id)  # no edit in between -> no_op

    after_names = {o.name for o in bpy.data.objects}
    ok = result.status == "no_op" and after_names == before_names
    R.check(
        "gs2_no_op_commit_does_not_touch_display_objects", ok,
        f"result={result} new_objects={after_names - before_names}",
    )


def gs2_display_regen_skips_depth_authored_window(R):
    """A depth-authored Window (box in the viewport) is one PolygonRecord since
    B49 (its depth is display-only; the record view lists the exported polygon
    only), so it fits ``build_surface_display``'s single-polygon contract:
    commit succeeds, the wall's display regenerates and so does the Window's
    (before B49 the 6-polygon box was skipped)."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx, depth=0.05)
    window_gml_id = str(fill.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    wall_display = bpy.data.objects.get(f"{wall.name}.cgml_display")
    window_display = bpy.data.objects.get(f"{fill.name}.cgml_display")

    ok = (
        result.status == "committed"
        and wall_display is not None and len(wall_display.data.polygons) > 0
        and window_display is not None and len(window_display.data.polygons) > 0
    )
    R.check(
        "gs2_display_regen_skips_depth_authored_window", ok,
        f"result={result} wall_display={wall_display} window_display={window_display} polys={len(window_display.data.polygons) if window_display else None}",
    )


def gs2_display_object_is_scene_linked_and_viewport_visible(R):
    """Review follow-up: prove the display object is genuinely part of the
    active scene/ViewLayer, not just present in ``bpy.data.objects`` (an
    earlier version kept its collection deliberately unlinked to dodge
    export, which also made it invisible - see ``display_regen.py``'s
    module docstring, "Ownership model"). Also proves the mesh itself has
    the expected wall-with-hole topology (one polygon has more edges/verts
    than a plain rectangle would, and the wall's own hole-centre area check
    already covers exact geometry in the first check above)."""
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender.display_regen import (
        DISPLAY_ONLY_KEY,
    )

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    import bpy
    bpy.context.view_layer.update()
    wall_display = bpy.data.objects.get(f"{wall.name}.cgml_display")

    collection = wall_display.users_collection[0] if wall_display is not None else None
    collection_linked_to_scene = (
        collection is not None and collection.name in bpy.context.scene.collection.children
    )
    in_scene_objects = wall_display is not None and wall_display.name in bpy.context.scene.objects
    in_view_layer = wall_display is not None and wall_display.name in bpy.context.view_layer.objects
    visible = wall_display is not None and bool(wall_display.visible_get())
    has_hole_topology = (
        wall_display is not None and wall_display.data is not None
        and len(wall_display.data.vertices) > 4  # a plain rectangle has 4; a holed mesh has more
    )
    marked_display_only = wall_display is not None and bool(wall_display.get(DISPLAY_ONLY_KEY))

    ok = (
        result.status == "committed"
        and wall_display is not None
        and collection_linked_to_scene
        and in_scene_objects
        and in_view_layer
        and visible
        and has_hole_topology
        and marked_display_only
    )
    R.check(
        "gs2_display_object_is_scene_linked_and_viewport_visible", ok,
        f"result={result} collection={getattr(collection, 'name', None)} "
        f"collection_linked_to_scene={collection_linked_to_scene} in_scene_objects={in_scene_objects} "
        f"in_view_layer={in_view_layer} visible={visible} has_hole_topology={has_hole_topology} "
        f"marked_display_only={marked_display_only}",
    )


def gs2_display_object_has_no_semantic_identity(R):
    """The display object must never become record/registry authority: no
    ``gml_id``, no authoring record, and ``registry.py``'s own identity
    predicate (``identity_of``) already treats it as a non-entity."""
    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import registry, storage

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    import bpy
    wall_display = bpy.data.objects.get(f"{wall.name}.cgml_display")

    no_gml_id = wall_display is not None and not str(wall_display.get("gml_id") or "").strip()
    no_record = wall_display is not None and not storage.has_record(wall_display)
    gml_id, kind, has_record = (
        registry.identity_of(wall_display, use_records=True) if wall_display is not None else ("x", "x", True)
    )
    not_an_identity = not (gml_id or kind or has_record)

    registry.REGISTRY.rebuild()
    not_registered = wall_display is not None and registry.REGISTRY.get(wall_display.name) is None

    ok = result.status == "committed" and no_gml_id and no_record and not_an_identity and not_registered
    R.check(
        "gs2_display_object_has_no_semantic_identity", ok,
        f"result={result} no_gml_id={no_gml_id} no_record={no_record} "
        f"identity=({gml_id!r},{kind!r},{has_record}) not_registered={not_registered}",
    )


def gs2_display_object_excluded_from_v2_and_v3_export(R):
    """The display object is scene-visible (previous check) but must never
    appear in exported CityGML content, under either writer - proved by
    text search on the actual exported XML, not by construction."""
    from city_modeler.shared.authoring_core.blender import edit_session as es

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_gml_id = str(wall.get("gml_id"))

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)

    import bpy
    wall_display = bpy.data.objects.get(f"{wall.name}.cgml_display")
    window_display = bpy.data.objects.get(f"{fill.name}.cgml_display")

    detail = {}
    ok = result.status == "committed" and wall_display is not None and window_display is not None
    for version in ("2.0", "3.0"):
        path = h.out_dir() / f"gs2_display_isolation_v{version.replace('.', '')}.gml"
        text = h.export_scene(path, version)
        lint = h.xmllint(path, version)
        display_names_absent = wall_display.name not in text and window_display.name not in text
        display_only_key_absent = "_cgml_display_only" not in text
        semantic_ids_present = wall_gml_id in text and window_gml_id in text
        detail[version] = {
            "lint": lint, "display_names_absent": display_names_absent,
            "display_only_key_absent": display_only_key_absent, "semantic_ids_present": semantic_ids_present,
        }
        ok = (
            ok and lint["exit"] == 0 and lint["errors"] == 0
            and display_names_absent and display_only_key_absent and semantic_ids_present
        )
    R.check("gs2_display_object_excluded_from_v2_and_v3_export", ok, json.dumps(detail))


def gs2_display_object_survives_save_reload(R):
    """Since the display object is genuine scene content (not an orphan
    datablock), it persists across save/reload like any other object - no
    separate regeneration step is required for it to still be there."""
    import bpy

    from city_modeler.shared.authoring_core.blender import edit_session as es
    from city_modeler.shared.authoring_core.blender import registry

    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    window_gml_id = str(fill.get("gml_id"))
    wall_name = wall.name

    es.begin(window_gml_id)
    _translate(fill, dx=0.05)
    result = es.commit(window_gml_id)
    wall_display_name = f"{wall_name}.cgml_display"
    verts_before = tuple(tuple(v.co) for v in bpy.data.objects[wall_display_name].data.vertices)

    path = h.out_dir() / "gs2_display_regen_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    _flag(True)
    registry.REGISTRY.rebuild()

    wall_display = bpy.data.objects.get(wall_display_name)
    still_present = wall_display is not None
    still_linked = still_present and wall_display.name in bpy.context.scene.objects
    still_visible = still_present and bool(wall_display.visible_get())
    verts_after = tuple(tuple(v.co) for v in wall_display.data.vertices) if still_present else ()

    ok = (
        result.status == "committed"
        and still_present and still_linked and still_visible
        and verts_after == verts_before
    )
    R.check(
        "gs2_display_object_survives_save_reload", ok,
        f"result={result} still_present={still_present} still_linked={still_linked} "
        f"still_visible={still_visible} verts_unchanged={verts_after == verts_before}",
    )


def main():
    R = h.Results(__file__)
    checks = [
        gs2_commit_creates_wall_and_window_display_objects,
        gs2_display_regen_does_not_touch_legacy_mesh_or_ring,
        gs2_display_regen_is_idempotent_across_commits,
        gs2_no_op_commit_does_not_touch_display_objects,
        gs2_display_regen_skips_depth_authored_window,
        gs2_display_object_is_scene_linked_and_viewport_visible,
        gs2_display_object_has_no_semantic_identity,
        gs2_display_object_excluded_from_v2_and_v3_export,
        gs2_display_object_survives_save_reload,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
