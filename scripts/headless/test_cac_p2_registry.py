"""GML registry in Blender (CAC-P2.2): index over real imports, rename,
delete, save/reload, undo/redo, handler idempotence, R4.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p2_registry.py -- <out_dir>
"""
import importlib
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

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
RAILWAY_V3 = FIXTURES[1][1]
MODULE = f"{h.ADDON}.shared.authoring_core.blender.registry"
HANDLER_LISTS = ("load_post", "undo_post", "redo_post", "depsgraph_update_post")


def reg():
    return importlib.import_module(MODULE)


def _gid(obj):
    return str(obj.get("gml_id") or "").strip()


def _handler_counts():
    return {name: sum(1 for fn in getattr(bpy.app.handlers, name)
                      if getattr(fn, "__module__", None) == MODULE)
            for name in HANDLER_LISTS}


def _first_wall():
    wall, _face = h.first_wall_with_exterior_face()
    return wall


def check_fixture_index(R, case, path, version, semantic):
    h.import_fixture(path, version, use_semantic_objects=semantic)
    registry = reg().REGISTRY
    index = registry.rebuild()
    with_id = [o for o in bpy.data.objects if _gid(o)]
    indexed = sum(len(index.keys_for(i)) for i in index.by_id)
    wrong = [o.name for o in with_id if o not in registry.get_all(_gid(o))]
    ok = with_id and indexed == len(with_id) and not wrong
    R.check(f"{case}_{'sem' if semantic else 'merged'}_indexes_every_gml_id", ok,
            f"objects with gml_id={len(with_id)} indexed={indexed} not resolvable={wrong[:5]} "
            f"duplicates={len(index.duplicates)}")


def rename_wall_still_resolves(R):
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = _first_wall()
    gid = _gid(wall)
    reg().REGISTRY.rebuild()
    wall.name = "cac_renamed_wall"
    got = reg().REGISTRY.get(gid)
    R.check("rename_wall_still_resolves", got is not None and got.name == "cac_renamed_wall",
            f"get({gid}) -> {getattr(got, 'name', None)}")


def delete_object_returns_none(R):
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = _first_wall()
    gid = _gid(wall)
    reg().REGISTRY.rebuild()
    bpy.data.objects.remove(wall)
    try:
        got, detail = reg().REGISTRY.get(gid), ""
    except Exception:
        got, detail = "raised", traceback.format_exc()
    R.check("delete_object_get_returns_none", got is None, f"get({gid}) -> {got!r} {detail}")


def save_reload_rebuilds(R):
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = _first_wall()
    gid, name = _gid(wall), wall.name
    path = str(h.out_dir() / "cac_p2_registry_reload.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    before = reg().REGISTRY.rebuild_count
    bpy.ops.wm.open_mainfile(filepath=path)
    after = reg().REGISTRY.rebuild_count
    dirty_after_load = reg().REGISTRY.dirty
    got = reg().REGISTRY.get(gid)
    R.check("save_reload_rebuilds_on_load_post",
            after > before and got is not None and got.name == name and _gid(got) == gid,
            f"rebuild_count {before}->{after} dirty={dirty_after_load} get -> {getattr(got, 'name', None)}")


def undo_redo_rebuilds(R):
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = _first_wall()
    gid, name = _gid(wall), wall.name
    bpy.ops.ed.undo_push(message="cac: before delete")
    bpy.data.objects.remove(wall)
    bpy.ops.ed.undo_push(message="cac: deleted")
    registry = reg().REGISTRY
    registry.rebuild()
    n0 = registry.rebuild_count
    bpy.ops.ed.undo()
    n_undo = registry.rebuild_count
    restored = registry.get(gid)
    restored_name = restored.name if restored is not None else None  # ref dies on redo
    bpy.ops.ed.redo()
    n_redo = registry.rebuild_count
    gone = registry.get(gid)
    R.check("undo_restores_resolvable_object",
            n_undo > n0 and restored_name == name,
            f"rebuild_count {n0}->{n_undo}; get after undo -> {restored_name}")
    R.check("redo_removes_it_again", n_redo > n_undo and gone is None,
            f"rebuild_count {n_undo}->{n_redo}; get after redo -> {getattr(gone, 'name', None)}")


def depsgraph_update_only_marks_dirty(R):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall = _first_wall()
    storage.write_record(wall, RecordSet(features=(FeatureRecord(gml_id=_gid(wall), kind="WallSurface"),)))
    before = {o.name: o.get(storage.RECORD_KEY) for o in bpy.data.objects}
    registry = reg().REGISTRY
    registry.rebuild()
    n0 = registry.rebuild_count
    wall.location.x += 1.0
    bpy.context.view_layer.update()
    after = {o.name: o.get(storage.RECORD_KEY) for o in bpy.data.objects}
    R.check("depsgraph_update_only_marks_dirty",
            registry.dirty and registry.rebuild_count == n0 and before == after,
            f"dirty={registry.dirty} rebuild_count {n0}->{registry.rebuild_count} "
            f"records_changed={[k for k in after if before.get(k) != after[k]]}")


def record_only_object_follows_flag(R):
    from city_modeler.shared.authoring_core.blender import storage
    from city_modeler.shared.authoring_core.domain.records import FeatureRecord, RecordSet

    h.load_addon()
    obj = bpy.data.objects.new("cac_record_only", bpy.data.meshes.new("cac_record_only"))
    bpy.context.scene.collection.objects.link(obj)
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id="CAC_RECORD_ONLY", kind="Window"),)))
    registry = reg().REGISTRY
    os.environ["CGML_AUTHORING_CORE"] = "1"
    try:
        registry.mark_dirty()
        on = registry.get("CAC_RECORD_ONLY")
    finally:
        os.environ.pop("CGML_AUTHORING_CORE", None)
    registry.mark_dirty()
    off = registry.get("CAC_RECORD_ONLY")
    R.check("record_only_object_indexed_only_with_flag_on", on is obj and off is None,
            f"flag on -> {getattr(on, 'name', None)}, flag off -> {getattr(off, 'name', None)}")


def handlers_registered_once(R):
    h.load_addon()
    after_enable = _handler_counts()
    for _ in range(2):
        bpy.ops.preferences.addon_disable(module=h.ADDON)
        bpy.ops.preferences.addon_enable(module=h.ADDON)
    after_toggles = _handler_counts()
    module = importlib.reload(reg())
    module.register()
    module.register()
    after_reload = _handler_counts()
    bpy.ops.preferences.addon_disable(module=h.ADDON)
    after_disable = _handler_counts()
    bpy.ops.preferences.addon_enable(module=h.ADDON)
    once = {n: 1 for n in HANDLER_LISTS}
    none = {n: 0 for n in HANDLER_LISTS}
    R.check("handlers_registered_once_after_reloads",
            after_enable == once and after_toggles == once and after_reload == once and after_disable == none,
            f"enable={after_enable} toggled_twice={after_toggles} reload+register_x2={after_reload} "
            f"disabled={after_disable}")


def main():
    R = h.Results(__file__)
    os.environ.pop("CGML_AUTHORING_CORE", None)
    steps = [lambda: [check_fixture_index(R, c, p, v, s) for c, p, v in FIXTURES for s in (True, False)],
             lambda: rename_wall_still_resolves(R),
             lambda: delete_object_returns_none(R),
             lambda: save_reload_rebuilds(R),
             lambda: undo_redo_rebuilds(R),
             lambda: depsgraph_update_only_marks_dirty(R),
             lambda: record_only_object_follows_flag(R),
             lambda: handlers_registered_once(R)]
    for i, step in enumerate(steps):
        try:
            step()
        except Exception:
            R.check(f"step{i}_exception", False, traceback.format_exc())
    R.finish()


main()
