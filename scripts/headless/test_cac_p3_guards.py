"""Legacy authoring tools refuse record-backed objects (CAC-P3.0, PLAN §2.6).

Per guarded tool: with a (stand-in) record inside the tool's mutation scope
the operator cancels with an ERROR report naming the tool, and the scene is
unchanged (object set, parents, custom props, world vertices, faces,
materials and their props). Without a record, flag on, the tool runs as
before. Also: the edit-exit handler skips a record-backed pair (C4), Draw
and Stamp include the LoD-promotion root (a record-backed sibling in the
same Building refuses), a malformed record fails closed, flag off is a
no-op (C6).

Records are written with ``storage.write_record`` as a stand-in for P3.2's
create use case, as in ``test_cac_p2_tool_preservation``.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_guards.py -- <out_dir>
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
ADDON = "city_modeler"
STATE = {}


# --------------------------------------------------------------------------
# Scene helpers
# --------------------------------------------------------------------------

def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _obj(key):
    import bpy

    return bpy.data.objects[STATE[key]]


def _give_record(obj):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    gml_id = str(obj.get("gml_id") or "") or f"TEST_{obj.name}"
    kind = str(obj.get("cgml3_feature") or "") or "Window"
    storage.write_record(obj, RecordSet(features=(FeatureRecord(
        gml_id=gml_id, kind=kind, provenance="authored"),)))
    assert storage.has_record(obj), f"stand-in record on {obj.name} does not read back"


def _object_mode():
    import bpy

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _select(objs, active):
    import bpy

    _object_mode()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active


def _edit_face(obj, face_index):
    """Edit Mode on ``obj`` alone with exactly ``face_index`` selected."""
    import bmesh
    import bpy

    _select([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    bm.faces[face_index].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def _state():
    """Everything a guarded tool could mutate, keyed by object name."""
    import bpy

    def props(idblock):
        return {k: repr(h._plain(idblock[k])) for k in idblock.keys()}

    _object_mode()
    bpy.context.view_layer.update()
    out = {}
    for o in bpy.data.objects:
        entry = {"parent": o.parent.name if o.parent else None, "props": props(o),
                 "matrix": [round(v, 5) for row in o.matrix_world for v in row]}
        if o.type == "MESH":
            me, mw = o.data, o.matrix_world
            entry["verts"] = [tuple(round(c, 5) for c in (mw @ v.co)) for v in me.vertices]
            entry["faces"] = [(tuple(p.vertices), p.material_index) for p in me.polygons]
            entry["materials"] = [(m.name, props(m)) if m else None for m in me.materials]
        out[o.name] = entry
    return out


def _diff(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(n for n in set(before) & set(after) if before[n] != after[n])
    return added, removed, changed


def _run(op, *args, **kwargs):
    """``(result, error_text)``; an ERROR report raises RuntimeError from bpy.ops."""
    try:
        return op(*args, **kwargs), ""
    except RuntimeError as e:
        return {"ERROR"}, str(e)


# --------------------------------------------------------------------------
# Base scenes (built once, reopened per scenario)
# --------------------------------------------------------------------------

def _semantic_scene():
    """Railway v3 (semantic objects) with two drawn Windows on the first wall."""
    import bpy
    from mathutils import Vector
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    # AB-17 fix: the old fixed (0.3, 0.5)/(0.6, 0.55) land outside this
    # fixture's real solid area (Railway arched cut-out) - find two
    # non-overlapping positions that actually place.
    uv1 = h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1)
    # Block the whole row (not just the footprint) so the second Window
    # lands in a different column, same row - a genuine "stacked side by
    # side" pair for sc_align's classifier.
    u1_m, v1_m = uv1[0] * ctx.width, uv1[1] * ctx.height
    uv2 = h.solid_area_position_uv(
        wall, face_index, ctx, 0.1, 0.1,
        avoid=[(u1_m, v1_m, ctx.width, 0.1)])
    placed = [place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2, depth=0.05),
        ctx, PlacementRequest(position_uv=uv, source="manual_draw")) for uv in (uv1, uv2)]
    # Nudge the second Window slightly off-row (a small, realistic near-miss,
    # same spirit as the old fixed (0.5)/(0.55) v mismatch) so sc_align has a
    # real edge to correct instead of two already-aligned Windows.
    placed[1].matrix_world.translation += Vector(ctx.vertical_axis_world) * 0.05
    bpy.context.view_layer.update()
    extra_uv = h.solid_area_position_uv(
        wall, face_index, ctx, 0.1, 0.1,
        avoid=[(uv1[0] * ctx.width, uv1[1] * ctx.height, 0.1, 0.1),
               (uv2[0] * ctx.width, uv2[1] * ctx.height, 0.1, 0.1)])
    return wall, face_index, placed[0], placed[1], extra_uv


def _build_bases(out):
    import bpy
    from city_modeler.shared.lod_helpers import lod_promotion_root
    from city_modeler.shared.ring_opening_sync import building_ancestor, is_window_opening

    _flag(False)
    wall, face_index, fill, fill2, extra_uv = _semantic_scene()
    building = building_ancestor(wall)
    root = lod_promotion_root(wall)
    sibling = next(o for o in sorted(bpy.data.objects, key=lambda o: o.name)
                   if is_window_opening(o) and o.parent is not None and o.parent is not wall
                   and lod_promotion_root(o) is root)
    STATE.update(wall=wall.name, face=face_index, fill=fill.name, fill2=fill2.name,
                 building=building.name, sibling=sibling.name, extra_uv=extra_uv)
    STATE["semantic_blend"] = str(out / "p3_guards_semantic.blend")
    bpy.ops.wm.save_as_mainfile(filepath=STATE["semantic_blend"])

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=False)
    from city_modeler.shared.facade_surface_resolver import surface_needs_split

    merged = next(o for o in sorted(bpy.data.objects, key=lambda o: o.name)
                  if o.type == "MESH" and str(o.get("cgml3_feature") or "") == "Building")
    merged_face = next(p.index for p in merged.data.polygons
                       if surface_needs_split(merged, p.index)
                       and str(merged.data.materials[p.material_index].get("SurfaceTyp") or "") == "WallSurface")
    STATE.update(merged=merged.name, merged_face=merged_face)
    STATE["merged_blend"] = str(out / "p3_guards_merged.blend")
    bpy.ops.wm.save_as_mainfile(filepath=STATE["merged_blend"])


def _open(which):
    import bpy

    bpy.ops.wm.open_mainfile(filepath=STATE[f"{which}_blend"])
    from city_modeler.ops import edit_exit_validation
    edit_exit_validation.reset_state()


def _register_attribute_ops():
    """The attribute panel module is deliberately not registered by the add-on
    (ui/__init__.py); its operators are guarded anyway (O-1) and tested here."""
    import bpy
    from city_modeler.ui import citygml_attributes_panel as panel

    for cls in panel.CLASSES:
        if cls.__name__.startswith("CGML3_OT_") and not cls.is_registered:
            bpy.utils.register_class(cls)


# --------------------------------------------------------------------------
# Scenarios: prepare(record) -> run() -> (result, error)
# Each one returns the object keys whose record makes the tool refuse.
# --------------------------------------------------------------------------

def _desync():
    fill = _obj("fill")
    fill.location = fill.location + fill.matrix_world.to_3x3().col[0].normalized() * 0.05


def sc_stamp(record_key):
    import bpy

    _open("semantic")
    wall, fill = _obj("wall"), _obj("fill")
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _edit_face(wall, STATE["face"])
    res = _run(bpy.ops.cgml.semantic_stamp, 'EXEC_DEFAULT',
               source_object_name=fill.name, source_gml_id=str(fill.get("gml_id") or ""),
               object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
               face_index=STATE["face"], position_uv=STATE["extra_uv"])
    return before, res


def sc_draw(record_key):
    import bpy

    _open("semantic")
    wall = _obj("wall")
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _edit_face(wall, STATE["face"])
    res = _run(bpy.ops.cgml.facade_draw, 'EXEC_DEFAULT', semantic_type="Window",
               width=0.2, height=0.2, depth=0.05, position_uv=STATE["extra_uv"],
               object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""),
               face_index=STATE["face"])
    return before, res


def sc_draw_implicit_split(record_key):
    import bpy

    _open("merged")
    merged = _obj("merged")
    if record_key:
        _give_record(merged)
    before = _state()
    _edit_face(merged, STATE["merged_face"])
    # AB-17 fix: (0.5, 0.5) can land outside this face's real solid area -
    # find a position that actually places.
    merged_ctx = h.surface_context(merged, STATE["merged_face"])
    merged_uv = h.solid_area_position_uv(merged, STATE["merged_face"], merged_ctx, 0.1, 0.1)
    res = _run(bpy.ops.cgml.facade_draw, 'EXEC_DEFAULT', semantic_type="Window",
               width=0.2, height=0.2, depth=0.05, position_uv=merged_uv)
    return before, res


def sc_split(record_key):
    import bpy

    _open("merged")
    merged = _obj("merged")
    if record_key:
        _give_record(merged)
    before = _state()
    _edit_face(merged, STATE["merged_face"])
    res = _run(bpy.ops.cgml.split_surfaces)
    return before, res


def sc_align(record_key):
    import bpy

    _open("semantic")
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _select([_obj("fill2"), _obj("fill")], _obj("fill"))
    res = _run(bpy.ops.cgml.align_objects)
    return before, res


def _sc_sync(op_name, record_key):
    import bpy

    _open("semantic")
    _desync()
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _select([_obj("fill")], _obj("fill"))
    res = _run(getattr(bpy.ops.cgml3, op_name))
    return before, res


def sc_sync_opening_to_ring(record_key):
    return _sc_sync("sync_opening_to_ring", record_key)


def sc_sync_ring_to_opening(record_key):
    return _sc_sync("sync_ring_to_opening", record_key)


def sc_resolve_ambiguous(record_key):
    import bpy

    _open("semantic")
    _desync()
    wall, fill = _obj("wall"), _obj("fill")
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    res = _run(bpy.ops.cgml.resolve_ambiguous_sync, 'EXEC_DEFAULT',
               wall_name=wall.name, opening_name=fill.name,
               wall_gml_id=str(wall.get("gml_id") or ""), opening_gml_id=str(fill.get("gml_id") or ""),
               authoritative='OPENING')
    return before, res


def sc_modeltyper_auto(record_key):
    import bpy

    _open("semantic")
    if record_key:
        _give_record(_obj(record_key))
    bpy.context.scene.cgml3_modeltyper_props.assignment_mode = "SELECTION"
    before = _state()
    _select([_obj("wall")], _obj("wall"))
    res = _run(bpy.ops.cgml3_modeltyper.auto_assign)
    return before, res


def sc_modeltyper_manual(record_key):
    import bpy

    _open("semantic")
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _edit_face(_obj("wall"), STATE["face"])
    res = _run(bpy.ops.cgml3_modeltyper.manual_assign)
    return before, res


def sc_modeltyper_mapping(record_key):
    import bpy

    _open("semantic")
    if record_key:
        _give_record(_obj(record_key))
    props = bpy.context.scene.cgml3_modeltyper_props
    props.assignment_mode = "SELECTION"
    row = props.property_mappings.add()
    row.enabled = True
    row.object_property = "gml_id"
    before = _state()
    _select([_obj("wall")], _obj("wall"))
    res = _run(bpy.ops.cgml3_modeltyper.apply_property_mapping)
    return before, res


def _sc_attr(op_name, key, record_key, **kwargs):
    import bpy

    _open("semantic")
    _register_attribute_ops()
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _select([_obj(key)], _obj(key))
    res = _run(getattr(bpy.ops.cgml3, op_name), 'EXEC_DEFAULT', **kwargs)
    return before, res


def sc_set_lod(record_key):
    return _sc_attr("set_lod", "wall", record_key, lod_level=1)


def sc_set_gml_id(record_key):
    return _sc_attr("set_gml_id", "fill", record_key, gml_id="TEST_NEW_ID")


def sc_set_feature_type(record_key):
    return _sc_attr("set_feature_type", "fill", record_key, feature_type="Door")


def _add_cube(name, center, size):
    import bpy
    from mathutils import Vector

    s = size / 2.0
    verts = [Vector((x, y, z)) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, me)
    obj.location = center
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def sc_assign_part(record_key):
    import bpy

    _open("semantic")
    building = _obj("building")
    cube = _add_cube("P3GuardPart", building.matrix_world.translation, 0.5)
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _select([cube, building], building)
    res = _run(bpy.ops.cgml3.assign_object_part)
    return before, res


def sc_detach_part(record_key):
    import bpy

    _open("semantic")
    _flag(True)
    building = _obj("building")
    cube = _add_cube("P3GuardPart", building.matrix_world.translation, 0.5)
    _select([cube, building], building)
    setup = _run(bpy.ops.cgml3.assign_object_part)
    if setup[0] != {"FINISHED"}:
        raise AssertionError(f"precondition: assign_object_part -> {setup}")
    if record_key:
        _give_record(cube)
    before = _state()
    _select([cube], cube)
    res = _run(bpy.ops.cgml3.detach_object_part)
    return before, res


def sc_join(record_key):
    import bpy

    _open("semantic")
    wall = _obj("wall")
    # Centred on a wall vertex: the face centre can fall inside a cut-out.
    corner = wall.data.vertices[wall.data.polygons[STATE["face"]].vertices[0]].co
    cube = _add_cube("P3GuardJoin", wall.matrix_world @ corner, 0.3)
    if record_key:
        _give_record(_obj(record_key))
    before = _state()
    _select([cube], cube)
    res = _run(bpy.ops.cgml3.join_object_parts, 'EXEC_DEFAULT')
    return before, res


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

# scenario, tool label in the refusal, object key carrying the record,
# expected legacy result (read from the first row of each scenario; None: any
# outcome except a guard refusal)
TOOLS = [
    # CAC-P3.2a: the source is no longer guarded here - create_feature only
    # reads it (own dimensions), never copies or mutates its record, so a
    # record-backed source Window is a valid Stamp source (see
    # test_cac_p3_stamp_create_feature.py).
    # CAC-P4.10: flag-on Window Draw/Stamp no longer refuses a record-backed
    # object anywhere in the Building (the P3.0 migration guard); the create
    # preflight decides. The stand-in record on a wall that still carries two
    # flag-off Windows is an inconsistent host (no polygon records, rings no
    # record claims), so both tools still refuse it, now via the preflight.
    # The retired "*_lod_sibling" rows (a record-backed Window on another
    # wall of the Building blocked placement) became the positive
    # P4_CONTRACT_CHECKS "*_beside_record_backed_sibling_allowed".
    ("stamp_target_wall", sc_stamp, "Semantic Stamp", "wall", {"FINISHED"}),
    ("draw_target_wall", sc_draw, "Draw Window", "wall", {"FINISHED"}),
    ("draw_implicit_split", sc_draw_implicit_split, "Draw Window", "merged", {"FINISHED"}),
    ("split_surfaces", sc_split, "Split into Surfaces", "merged", {"FINISHED"}),
    # CAC-P4.6: a record-backed Window source is aligned/synced through the
    # edit/commit lifecycle (positive coverage: test_cac_p4_align_sync.py).
    # What stays refused is a record-backed *target* with a legacy source -
    # no coherent record owner (row below) - and unsupported record kinds
    # (P4_CONTRACT_CHECKS).  The P3 "record-backed host wall under two legacy
    # Windows" row is retired: create_feature refuses to commit a host wall
    # whose polygon still carries legacy rings, so that closure has no
    # reachable record-backed state for this synthetic stand-in to model.
    ("align_objects_target_only", sc_align, "Align Objects", "fill", {"FINISHED"}),
    ("resolve_ambiguous_sync", sc_resolve_ambiguous, "Opening/Ring Both Changed", "fill", {"FINISHED"}),
    # cut_openings_display row retired: operator removed in B17.
    ("modeltyper_auto_assign", sc_modeltyper_auto, "Assign Surface Types (Auto)", "wall", {"FINISHED"}),
    ("modeltyper_manual_assign", sc_modeltyper_manual, "Assign Surface Type (Manual)", "sibling", {"FINISHED"}),
    ("modeltyper_property_mapping", sc_modeltyper_mapping, "Apply Property Mapping", "wall", {"FINISHED"}),
    ("set_lod", sc_set_lod, "Set LOD", "wall", {"FINISHED"}),
    ("set_gml_id", sc_set_gml_id, "Set GML ID", "fill", {"FINISHED"}),
    ("set_feature_type", sc_set_feature_type, "Set Feature Type", "fill", {"FINISHED"}),
    ("assign_object_part", sc_assign_part, "Assign Object Part", "fill", {"FINISHED"}),
    ("detach_object_part", sc_detach_part, "Detach Object Part", "cube", {"FINISHED"}),
    ("join_object_parts", sc_join, "Join Object Parts", "building", {"FINISHED"}),
]


# Scenarios no longer in TOOLS (their record-backed path is a P4 lifecycle,
# not a refusal) whose flag-on legacy behaviour must still be unchanged.
LEGACY_ONLY = [
    (sc_sync_opening_to_ring, {"FINISHED"}),
    (sc_sync_ring_to_opening, {"FINISHED"}),
]


def _give_kind_record(obj, kind):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    gml_id = str(obj.get("gml_id") or "") or f"TEST_{obj.name}"
    storage.write_record(obj, RecordSet(features=(FeatureRecord(
        gml_id=gml_id, kind=kind, provenance="authored"),)))
    assert storage.has_record(obj), f"stand-in {kind} record on {obj.name} does not read back"


def align_record_backed_non_window_target_refused():
    """P4.6 aligns a record-backed Window only against a Window target."""
    import bpy

    _flag(True)
    _open("semantic")
    _give_record(_obj("fill2"))
    _give_kind_record(_obj("fill"), "Door")
    before = _state()
    _select([_obj("fill2"), _obj("fill")], _obj("fill"))
    result, error = _run(bpy.ops.cgml.align_objects)
    added, removed, changed = _diff(before, _state())
    ok = (result == {"ERROR"} and "Align Objects: refused" in error and "Window only" in error
          and not (added or removed or changed))
    return ok, f"result={result} error={error.strip()!r}; added={added[:5]} removed={removed[:5]} changed={changed[:8]}"


def sync_record_backed_non_window_refused():
    """Manual Sync commits only a record-backed Window + host wall pair."""
    import bpy

    _flag(True)
    _open("semantic")
    _desync()
    _give_kind_record(_obj("fill"), "Door")
    before = _state()
    _select([_obj("fill")], _obj("fill"))
    result, error = _run(bpy.ops.cgml3.sync_opening_to_ring)
    added, removed, changed = _diff(before, _state())
    ok = result == {"ERROR"} and bool(error.strip()) and not (added or removed or changed)
    return ok, f"result={result} error={error.strip()!r}; added={added[:5]} removed={removed[:5]} changed={changed[:8]}"


def _clean_wall_beside(wall):
    """``(wall, face, uv)`` of another WallSurface of ``wall``'s Building that
    carries no interior ring and has room for a small Window."""
    import bpy
    from city_modeler.shared.lod_helpers import lod_promotion_root
    from city_modeler.shared.object_helpers import face_material
    from city_modeler.shared.ring_opening_sync import wall_interior_ring_faces

    root = lod_promotion_root(wall)
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        if (obj is wall or obj.type != "MESH" or str(obj.get("cgml3_feature") or "") != "WallSurface"
                or lod_promotion_root(obj) is not root or any(True for _ in wall_interior_ring_faces(obj))):
            continue
        faces = [p for p in obj.data.polygons
                 if (m := face_material(obj.data, p)) is not None and not m.get("Interior") and m.get("con_surface_id")]
        for poly in sorted(faces, key=lambda p: -p.area):
            ctx = h.surface_context(obj, poly.index)
            try:
                return obj, poly.index, h.solid_area_position_uv(obj, poly.index, ctx, 0.15, 0.15)
            except Exception:
                continue
    raise AssertionError("no second WallSurface without rings in the Building")


def _beside_sibling(place):
    """CAC-P4.10: a record-backed Window on one wall of the Building (the old
    ``*_lod_sibling`` stand-in) must not block a flag-on Window placement on
    another, clean wall of the same Building; the sibling is left untouched."""
    _flag(True)
    _open("semantic")
    sibling = _obj("sibling")
    _give_record(sibling)
    target, face, uv = _clean_wall_beside(_obj("wall"))
    sibling_before = dict(_state()[sibling.name])
    _edit_face(target, face)
    result, error = place(target, face, uv)
    after = _state()
    ok = (result == {"FINISHED"} and "refused" not in error and after[sibling.name] == sibling_before)
    return ok, f"target={target.name} result={result} error={error.strip()!r} sibling_unchanged={after[sibling.name] == sibling_before}"


def draw_beside_record_backed_sibling_allowed():
    import bpy

    return _beside_sibling(lambda wall, face, uv: _run(
        bpy.ops.cgml.facade_draw, 'EXEC_DEFAULT', semantic_type="Window", width=0.2, height=0.2, depth=0.05,
        position_uv=uv, object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""), face_index=face))


def stamp_beside_record_backed_sibling_allowed():
    import bpy

    # The source is resolved at call time: _beside_sibling reopens the file.
    return _beside_sibling(lambda wall, face, uv: _run(
        bpy.ops.cgml.semantic_stamp, 'EXEC_DEFAULT',
        source_object_name=_obj("fill").name, source_gml_id=str(_obj("fill").get("gml_id") or ""),
        object_name=wall.name, object_gml_id=str(wall.get("gml_id") or ""), face_index=face, position_uv=uv))


P4_CONTRACT_CHECKS = [
    ("align_record_backed_non_window_target_refused", align_record_backed_non_window_target_refused),
    ("sync_record_backed_non_window_refused", sync_record_backed_non_window_refused),
    ("draw_beside_record_backed_sibling_allowed", draw_beside_record_backed_sibling_allowed),
    ("stamp_beside_record_backed_sibling_allowed", stamp_beside_record_backed_sibling_allowed),
]


def _refused_check(scenario, label, record_key):
    _flag(True)
    before, (result, error) = scenario(record_key)
    added, removed, changed = _diff(before, _state())
    refused = result == {"ERROR"} and f"{label}: refused" in error
    ok = refused and not (added or removed or changed)
    return ok, (f"result={result} error={error.strip()!r}; "
                f"added={added[:5]} removed={removed[:5]} changed={changed[:8]}")


def _legacy_check(scenario, expected):
    _flag(True)
    before, (result, error) = scenario(None)
    added, removed, changed = _diff(before, _state())
    not_refused = "refused" not in error
    ok = not_refused and (expected is None or result == expected)
    if expected is not None:
        ok = ok and bool(added or removed or changed)
    return ok, (f"result={result} error={error.strip()!r}; "
                f"added={len(added)} removed={len(removed)} changed={len(changed)}")


# CAC-P4.10 (Case B, AB-20b): the base scene's wall carries two Windows drawn
# with the flag off - linked rings with no record. A flag-on Window Draw/Stamp
# runs through create_feature, whose preflight refuses that host before any
# mutation (committing it would record the two rings as dangling). The legacy
# behaviour these tools must keep is the flag-off one, checked separately.
CASE_B_PLACEMENT = (sc_draw, sc_stamp)


def _flag_off_legacy_check(scenario, expected):
    _flag(False)
    before, (result, error) = scenario(None)
    added, removed, changed = _diff(before, _state())
    ok = "refused" not in error and result == expected and bool(added or removed or changed)
    return ok, (f"result={result} error={error.strip()!r}; "
                f"added={len(added)} removed={len(removed)} changed={len(changed)}")


def _case_b_refused_check(scenario):
    _flag(True)
    before, (result, error) = scenario(None)
    added, removed, changed = _diff(before, _state())
    ok = (result == {"ERROR"} and "does not manage" in error and "carry no record" in error
          and not (added or removed or changed))
    return ok, (f"result={result} error={error.strip()!r}; "
                f"added={added[:5]} removed={removed[:5]} changed={changed[:8]}")


def edit_exit_handler_skips_record_backed_pair():
    import bmesh
    import bpy
    from city_modeler.ops import edit_exit_validation as eev

    results = {}
    for record in (False, True):
        _flag(True)
        # Fresh import, not _open(): the add-on's depsgraph handler is not
        # @persistent, so wm.open_mainfile unregisters it (pre-existing).
        _wall, _face, fill, _fill2, _extra_uv = _semantic_scene()
        eev.reset_state()
        wall = fill.parent
        if record:
            _give_record(fill)
        before = _state()
        _select([fill], fill)
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(fill.data)
        shift = fill.matrix_world.inverted().to_3x3() @ (fill.matrix_world.to_3x3().col[0].normalized() * 0.05)
        for v in bm.verts:
            v.co += shift
        bmesh.update_edit_mesh(fill.data)
        bpy.ops.object.mode_set(mode="OBJECT")
        _added, _removed, changed = _diff(before, _state())
        results[record] = (wall.name in changed, list(eev.SKIPPED_RECORD_BACKED))
    legacy_synced, legacy_skips = results[False]
    guarded_synced, guarded_skips = results[True]
    ok = (legacy_synced and not legacy_skips and not guarded_synced
          and len(guarded_skips) == 1 and "Edit-exit auto-sync: refused" in guarded_skips[0])
    return ok, f"legacy: wall synced={legacy_synced}; record-backed: wall synced={guarded_synced}, skips={guarded_skips}"


def malformed_record_fails_closed():
    import bpy

    _flag(True)
    _open("semantic")
    _register_attribute_ops()
    wall = _obj("wall")
    wall["_cgml_record"] = "{not json"
    before = _state()
    _select([wall], wall)
    result, error = _run(bpy.ops.cgml3.set_lod, 'EXEC_DEFAULT', lod_level=1)
    added, removed, changed = _diff(before, _state())
    ok = result == {"ERROR"} and "malformed" in error and not (added or removed or changed)
    return ok, f"result={result} error={error.strip()!r} changed={changed[:5]}"


def flag_off_guard_is_noop():
    _flag(False)
    before, (result, error) = sc_align("fill")
    added, removed, changed = _diff(before, _state())
    return result == {"FINISHED"} and bool(changed), f"result={result} error={error.strip()!r} changed={changed[:5]}"


def main():
    R = h.Results(__file__)
    out = h.out_dir()
    try:
        h.load_addon()
        _build_bases(out)
    except Exception:
        detail = "setup exception:\n" + "".join(traceback.format_exc().splitlines(True)[-8:])
        R.check("setup", False, detail)
        R.finish()

    checks = []
    for name, scenario, label, record_key, expected in TOOLS:
        checks.append((f"{name}_record_backed_refused",
                       lambda s=scenario, lb=label, rk=record_key: _refused_check(s, lb, rk)))
    seen = set()  # one legacy check per scenario; the first TOOLS row sets its expectation
    for _name, scenario, _label, _rk, expected in TOOLS:
        if scenario in seen:
            continue
        seen.add(scenario)
        if scenario in CASE_B_PLACEMENT:
            checks.append((f"{scenario.__name__[3:]}_legacy_unchanged_behaviour",
                           lambda s=scenario, e=expected: _flag_off_legacy_check(s, e)))
            checks.append((f"{scenario.__name__[3:]}_onto_unrecorded_linked_openings_refused",
                           lambda s=scenario: _case_b_refused_check(s)))
            continue
        checks.append((f"{scenario.__name__[3:]}_legacy_unchanged_behaviour",
                       lambda s=scenario, e=expected: _legacy_check(s, e)))
    for scenario, expected in LEGACY_ONLY:
        checks.append((f"{scenario.__name__[3:]}_legacy_unchanged_behaviour",
                       lambda s=scenario, e=expected: _legacy_check(s, e)))
    checks += P4_CONTRACT_CHECKS
    checks += [
        ("edit_exit_handler_skips_record_backed_pair", edit_exit_handler_skips_record_backed_pair),
        ("malformed_record_fails_closed", malformed_record_fails_closed),
        ("flag_off_guard_is_noop", flag_off_guard_is_noop),
    ]

    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception:
            ok, detail = False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-8:])
        R.check(name, ok, detail)
    _flag(False)
    R.finish()


main()
