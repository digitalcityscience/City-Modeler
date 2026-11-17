"""B84 end to end in Blender: the scene is a working copy, one Commit writes to the local 3DCityDB v5.

Scratch schema ``b84_commit`` (created here, dropped at the end):
buildings_lod2_citygml_3_0.gml (3 buildings).

Checks:
- loaded buildings are "synced"; entering and leaving Edit Mode does not make them "changed";
- editing, adding and deleting objects in Blender changes nothing in the database
  (md5 of feature / property / geometry_data), only the pending list;
- commit of one building writes only that one; commit of all adds the new building and
  terminates the deleted one (history kept); afterwards nothing is pending;
- Unload is not a delete; Keep in Database drops a pending delete;
- a new object whose gml:id already exists in the database is a conflict: nothing written;
  Keep My Version turns it into a replace;
- the Save panel shows the counts and the Commit button.

Usage (isolated profile, password only in the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python-exit-code 1 --python scripts/headless/test_b84_db_commit.py -- \
      <buildings_dir> <citydb_compose_dir> [--keep-schema]
"""
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []
ADDON = None
CONTAINER = "citydb-v5-dev"
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"
SCHEMA = "b84_commit"
EXTENT = (564700.0, 5934100.0, 565000.0, 5934500.0)
B1, B2 = "DEHHALKAJ0000yAZ", "DEHHALKAJ000179L"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label + (f" - {detail}" if detail else ""))
    return cond


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


def _run(cmd, env=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    return _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])[1].strip()


def schema_sql(script):
    return _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                 "-v", f"schema_name={SCHEMA}", "-f", script])


def import_file(compose_dir, path):
    folder, name = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    env = dict(os.environ, CITYDB_SCHEMA=SCHEMA, MSYS_NO_PATHCONV="1")
    rc, out = _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "-v", f"{folder}:/b84in",
                    "citydb-tool", "import", "citygml", f"/b84in/{name}"], env=env, cwd=compose_dir)
    if rc != 0 or "successfully completed" not in out:
        raise RuntimeError(f"citydb import {name} failed: {out[-600:]}")


def checksum():
    s = SCHEMA
    return psql(
        f"SELECT (SELECT count(*) FROM {s}.feature) || ' ' || md5("
        f" coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.feature t), '')"
        f" || coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.property t), '')"
        f" || coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.geometry_data t), ''))")


def rows(objectid):
    """[(feature id, terminated)] of the top-level rows with this objectid."""
    out = psql(f"SELECT f.id, f.termination_date IS NOT NULL FROM {SCHEMA}.feature f "
               f"JOIN {SCHEMA}.objectclass oc ON oc.id = f.objectclass_id "
               f"WHERE oc.is_toplevel = 1 AND f.objectid = '{objectid}' ORDER BY f.id")
    return [(int(a), b == "t") for a, b in (line.split("|") for line in out.splitlines() if line)]


def active(objectid):
    return [i for i, terminated in rows(objectid) if not terminated]


def set_target():
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("B84 local") or prefs.db_profiles.add()
    p.name, p.host, p.port, p.user = "B84 local", HOST, PORT, USER
    prefs.db_active_profile = p.name
    mod("citydb.credentials")._session[p.server().key] = SECRET
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", SCHEMA
    sc.db_target_label, sc.db_target_srid = "B84 commit", 25832


def root_of(objectid):
    return mod("shared.export_scope").top_level_feature_objects(bpy.context.scene).get(objectid)


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def kinds():
    cc = mod("ops.commit_citydb")
    cc._cache.clear()
    return {c.objectid: c.kind for c in cc.scan(bpy.context)[1]}


def load(bc, s, objectids):
    bpy.ops.cgml3.db_browse_select(action='SET',
                                   feature_ids=",".join(str(r.id) for r in s.rows if r.objectid in objectids))
    return bpy.ops.cgml3.db_browse_load(mode='LOD2')


def raise_roof(root, dz=2.0):
    moved = 0
    for o in (root, *root.children_recursive):
        if o.type == 'MESH' and "roof" in (str(o.get("cgml3_feature", "")) + " " + o.name).lower():
            for v in o.data.vertices:
                v.co.z += dz
            o.data.update()
            moved += len(o.data.vertices)
    return moved


def delete_feature(root):
    for o in (*root.children_recursive, root):
        bpy.data.objects.remove(o)


def call(op, **kw):
    try:
        return op(**kw)
    except RuntimeError as exc:
        return str(exc)


class Recorder:
    def __init__(self, out, labels):
        object.__setattr__(self, "out", out)
        object.__setattr__(self, "labels", labels)

    def __setattr__(self, name, value):
        pass

    def _child(self, *a, **kw):
        return Recorder(self.out, self.labels)

    row = column = box = _child

    def operator(self, idname, text="", **kw):
        self.out.append((idname, text))
        return types.SimpleNamespace()

    def label(self, text="", **kw):
        self.labels.append(text)

    def __getattr__(self, name):
        return lambda *a, **kw: None


def panel():
    out, labels = [], []
    mod("ops.commit_citydb")._cache.clear()
    mod("ui.db_browse_panel").draw_changes(Recorder(out, labels), bpy.context)
    return out, labels


def main():
    global ADDON
    a = args()
    buildings_dir, compose_dir = a[0], a[1]
    if not SECRET:
        print("Set CGML3_TEST_DB_PASSWORD")
        sys.exit(2)
    schema_sql("drop-schema.sql")
    rc, out = schema_sql("create-schema.sql")
    if rc != 0:
        raise RuntimeError(out[-400:])
    import_file(compose_dir, os.path.join(buildings_dir, "buildings_lod2_citygml_3_0.gml"))
    try:
        bpy.ops.wm.read_homefile(use_empty=True)
        ok, ADDON = ensure_addon_enabled()
        assert ok, ADDON
        bpy.context.scene.world = bpy.data.worlds.new("World")
        bpy.context.scene.cgml3.xsd_validate = False
        set_target()
        bc, cc = mod("ops.browse_citydb"), mod("ops.commit_citydb")
        bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
        s = bc.active_session(bpy.context)
        b3 = next(r.objectid for r in s.rows if r.objectid not in (B1, B2))
        res = load(bc, s, (B1, B2))
        check(res == {'FINISHED'} and root_of(B1) and root_of(B2), "B1 and B2 loaded")

        print("\n[B84] --- loaded = synced ---")
        check(kinds() == {B1: "synced", B2: "synced"}, "loaded buildings are synced", str(kinds()))
        mesh = next(o for o in root_of(B1).children_recursive if o.type == 'MESH')
        activate(mesh)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')
        check(kinds()[B1] == "synced", "Edit Mode in and out without an edit: still synced")
        res = call(bpy.ops.cgml3.db_commit)
        check(res != {'FINISHED'}, "nothing pending: commit refuses", str(res))

        print("\n[B84] --- edits stay in Blender until the commit ---")
        before = checksum()
        fid1, fid2 = active(B1)[0], active(B2)[0]
        check(raise_roof(root_of(B1)) > 0, "roof of B1 raised")
        bpy.ops.cgml.building_new(points=footprint(12, 8), height=6, epsg=25832)
        new_id = bpy.context.active_object["gml_id"]
        delete_feature(root_of(B2))
        k = kinds()
        check(k == {B1: "changed", new_id: "new", B2: "deleted"}, "changed / new / deleted are pending", str(k))
        check(checksum() == before, "the database is untouched by edit, add and delete in Blender")
        out, labels = panel()
        text = " | ".join(labels)
        check("New, not in database: 1" in text and "Changed here: 1" in text
              and "Deleted here, still in database: 1" in text, "panel lists the pending changes", text)
        check(any(i == "cgml3.db_commit" and "Commit 3 Changes" in t for i, t in out), "Commit 3 Changes button",
              str(out))

        print("\n[B84] --- commit one building ---")
        res = call(bpy.ops.cgml3.db_commit, objectids=B1)
        check(res == {'FINISHED'}, "commit of B1 finishes", f"{res} {s.last_update.get('message')}")
        r1 = rows(B1)
        check(len(r1) == 2 and r1[0] == (fid1, True) and not r1[1][1], "B1: old row terminated, new row active",
              str(r1))
        check(active(B2) == [fid2] and not rows(new_id), "B2 and the new building are not written yet")
        k = kinds()
        check(k == {B1: "synced", new_id: "new", B2: "deleted"}, "B1 synced, the rest still pending", str(k))
        check(int(root_of(B1)["cgml3_db_feature_id"]) == r1[1][0], "B1 root carries the new feature id")

        print("\n[B84] --- commit everything ---")
        res = call(bpy.ops.cgml3.db_commit)
        check(res == {'FINISHED'}, "commit finishes", f"{res} {s.last_update.get('message')}")
        check(len(active(new_id)) == 1, "new building is in the database", str(rows(new_id)))
        check(rows(B2) == [(fid2, True)], "deleted building is terminated (history kept)", str(rows(B2)))
        check(len(active(b3)) == 1 and len(rows(b3)) == 1, "untouched building untouched")
        k = kinds()
        check(k == {B1: "synced", new_id: "synced"}, "nothing pending after the commit", str(k))
        out, labels = panel()
        check(any(i == "cgml3.db_commit" and t == "Nothing to Commit" for i, t in out), "panel: Nothing to Commit")
        texts = "\n".join(t.as_string() for t in bpy.data.texts)
        check(SECRET not in texts + str(s.last_update), "no password in logs and messages")

        print("\n[B84] --- unload is not delete; keep in database ---")
        bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
        s = bc.active_session(bpy.context)
        load(bc, s, (b3,))
        check(kinds().get(b3) == "synced", "B3 loaded")
        bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=str(active(b3)[0]))
        bpy.ops.cgml3.db_browse_unload(all=False)
        check(b3 not in kinds() and root_of(b3) is None, "unloaded building is not a pending delete", str(kinds()))
        delete_feature(root_of(new_id))
        check(kinds().get(new_id) == "deleted", "deleted in Blender: pending delete")
        res = bpy.ops.cgml3.db_keep()
        check(res == {'FINISHED'} and new_id not in kinds() and len(active(new_id)) == 1,
              "Keep in Database: no pending delete, row still active")

        print("\n[B84] --- every surface deleted, root left: a deleted building ---")
        root = root_of(B1)
        for o in list(root.children_recursive):
            bpy.data.objects.remove(o)
        check(kinds().get(B1) == "deleted", "B1 without surfaces counts as deleted", str(kinds()))
        res = call(bpy.ops.cgml3.db_commit)
        check(res == {'FINISHED'} and not active(B1) and root_of(B1) is None,
              "commit terminates it and removes the empty root", f"{res} {rows(B1)} {s.last_update.get('message')}")

        print("\n[B84] --- same id already in the database: conflict ---")
        bpy.ops.cgml.building_new(points=footprint(10, 6), height=5, epsg=25832)
        twin = bpy.context.active_object
        twin["gml_id"] = b3
        before = checksum()
        res = call(bpy.ops.cgml3.db_commit)
        conflicts = s.last_update.get("conflicts", {})
        check(res != {'FINISHED'} and b3 in conflicts and checksum() == before, "conflict: nothing written",
              f"{res} {s.last_update.get('message')}")
        old = active(b3)[0]
        res = bpy.ops.cgml3.db_resolve(objectid=b3)
        check(res == {'FINISHED'} and kinds().get(b3) == "changed", "Keep My Version: now a pending replace")
        res = call(bpy.ops.cgml3.db_commit)
        r3 = rows(b3)
        check(res == {'FINISHED'} and (old, True) in r3 and len(active(b3)) == 1 and active(b3) != [old],
              "second commit replaces the database row", f"{res} {r3} {s.last_update.get('message')}")
    finally:
        if "--keep-schema" not in a:
            schema_sql("drop-schema.sql")
    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] B84 commit checks satisfied")


try:
    main()
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
