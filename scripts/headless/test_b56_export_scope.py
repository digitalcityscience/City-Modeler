"""B56 (agent plan A1): explicit export scope and the written/skipped record.

Three authored buildings, a wall of the second one left selected:
- scope SCENE (script default) writes all 3, the selection stays as it was
- scope SELECTION writes 1, the report says "1 feature(s) written, 2 skipped"
- scope IDS writes the listed one; an unknown id cancels and writes nothing
- scene["cgml_last_export"] is JSON with the written/skipped ids
- DB export (citydb-tool faked, no database): the pre-delete ids are exactly the written ids,
  not every feature of the scene

Old code: no ``scope`` property (TypeError), the selection exports 1 building silently.

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b56_export_scope.py -- <out_dir>
"""
import json
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def expect(cond, label):
    check(label, cond)


def buildings():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    out = []
    for i in range(3):
        bpy.ops.cgml.building_new(points=footprint(centre=(i * 30.0, 0.0, 0.0)))
        out.append(bpy.context.active_object)
    return out


def ids_in(path):
    from importlib import import_module as im
    return im(ADDON + ".shared.export_scope").written_feature_ids(path)


def main():
    global ADDON
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b56_")
    os.makedirs(out, exist_ok=True)
    ok, ADDON = ensure_addon_enabled()
    if not ok:
        raise SystemExit(ADDON)
    es = import_module(ADDON + ".shared.export_scope")

    bs = buildings()
    bids = [str(b["gml_id"]) for b in bs]
    wall = next(c for c in bs[1].children_recursive if c.type == 'MESH')
    bpy.ops.object.select_all(action='DESELECT')
    wall.select_set(True)
    bpy.context.view_layer.objects.active = wall

    p = os.path.join(out, "scene.gml")
    r = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0")
    expect(r == {'FINISHED'}, f"SCENE export finished ({r})")
    expect(sorted(ids_in(p)) == sorted(bids), f"SCENE writes all 3 buildings ({ids_in(p)})")
    expect([o.name for o in bpy.context.selected_objects] == [wall.name], "selection restored after SCENE export")
    rec = json.loads(bpy.context.scene["cgml_last_export"])
    expect(rec["written_count"] == 3 and rec["skipped_count"] == 0 and rec["scope"] == "SCENE",
          f"record SCENE 3/0 ({rec['written_count']}/{rec['skipped_count']})")
    expect(rec["written_by_type"] == {"Building": 3}, f"record by type ({rec['written_by_type']})")

    p = os.path.join(out, "selection.gml")
    r = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0", scope='SELECTION')
    rec = es.last_export(bpy.context.scene)
    expect(ids_in(p) == [bids[1]], f"SELECTION writes the selected building only ({ids_in(p)})")
    expect(rec["written"] == [bids[1]] and sorted(rec["skipped"]) == sorted([bids[0], bids[2]]),
          "record SELECTION: 1 written, 2 skipped with ids")
    expect(es.summary(rec).startswith("1 feature(s) written, 2 skipped"), f"summary ({es.summary(rec)})")

    p = os.path.join(out, "ids.gml")
    r = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="2.0", scope='IDS', scope_gml_ids=bids[2])
    expect(r == {'FINISHED'} and ids_in(p) == [bids[2]], f"IDS 2.0 writes the listed building ({ids_in(p)})")
    expect([o.name for o in bpy.context.selected_objects] == [wall.name], "selection restored after IDS export")

    p = os.path.join(out, "bad.gml")
    try:
        r = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0", scope='IDS', scope_gml_ids="NOPE")
    except RuntimeError:
        r = {'CANCELLED'}
    expect(r == {'CANCELLED'} and not os.path.exists(p), "unknown id cancels, no file")

    bpy.ops.object.select_all(action='DESELECT')
    try:
        r = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version="3.0", scope='SELECTION')
    except RuntimeError:
        r = {'CANCELLED'}
    expect(r == {'CANCELLED'} and not os.path.exists(p), "SELECTION with nothing selected cancels")

    # DB: fake citydb-tool, record the pre-delete ids
    addon = sys.modules[ADDON]
    cli = import_module(ADDON + ".ops.citydb_cli")
    seen = {}
    addon._resolve_db_target = lambda op, ctx: ("x", "disable")
    cli.CityDBTool.delete_features_from_db = lambda self, **kw: (seen.setdefault("delete", kw["feature_ids"]), (True, ""))[1]
    cli.CityDBTool.import_citygml_to_db = lambda self, gml, **kw: (seen.setdefault("import", ids_in(gml)), (True, ""))[1]
    wall.select_set(True)
    r = bpy.ops.cgml3.export_to_db(db_host="h", db_port="1", db_name="d", db_user="u", scope='SELECTION')
    expect(r == {'FINISHED'}, f"DB export finished ({r})")
    expect(seen.get("delete") == [bids[1]] == seen.get("import"),
          f"DB pre-delete ids = written ids ({seen.get('delete')} / {seen.get('import')})")
    expect(es.last_export(bpy.context.scene)["target"] == "db", "record target db")

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


ADDON = None
main()
