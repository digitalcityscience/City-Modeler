"""T10 end to end in Blender: write a loaded feature back to the local 3DCityDB v5.

Scratch schema ``t10_update`` (created here, dropped at the end; T02 fixtures
untouched): buildings_lod2_citygml_3_0.gml (3 buildings), later also the LoD3
file (same objectids as second active rows).

Checks (ticket acceptance):
- only DB-loaded roots are eligible; other objects say why;
- dry run writes nothing (row counts + md5 of feature/property/geometry_data);
- terminate / delete without confirmation (or with stale rows) write nothing;
- roof +2 m, terminate: only that feature changes - every other row is
  bit-identical, the old row is terminated, the new row keeps the objectid and
  the surfaces' gml:ids, its envelope follows the edit, no add-on properties
  reach the database; the box, the root id and the reloaded model follow;
- delete removes the old row; two active rows (LoD2 + LoD3) are listed and
  both must be confirmed;
- the password appears in no text log, custom property, message or .blend.

Usage (isolated profile, password only in the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python-exit-code 1 --python scripts/headless/test_t10_db_update.py -- \
      <buildings_dir> <citydb_compose_dir> [--keep-schema]
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
ADDON = None
CONTAINER = "citydb-v5-dev"
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"
SCHEMA = "t10_update"
EXTENT = (564700.0, 5934100.0, 565000.0, 5934500.0)
B1, B2 = "DEHHALKAJ0000yAZ", "DEHHALKAJ000179L"
ROOF_DZ = 2.0


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label + (f" - {detail}" if detail else ""))
    return cond


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


# --------------------------------------------------------------------------- database
def _run(cmd, env=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    _rc, out = _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])
    return out.strip()


def drop_schema():
    _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
          "-v", f"schema_name={SCHEMA}", "-f", "drop-schema.sql"])


def create_schema():
    drop_schema()
    rc, out = _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                    "-v", f"schema_name={SCHEMA}", "-f", "create-schema.sql"])
    if rc != 0:
        raise RuntimeError(f"create-schema failed: {out[-400:]}")


def import_file(compose_dir, path):
    folder, name = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    env = dict(os.environ, CITYDB_SCHEMA=SCHEMA, MSYS_NO_PATHCONV="1")
    rc, out = _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "-v", f"{folder}:/t10in",
                    "citydb-tool", "import", "citygml", f"/t10in/{name}"], env=env, cwd=compose_dir)
    if rc != 0 or "successfully completed" not in out:
        raise RuntimeError(f"citydb import {name} failed: {out[-600:]}")


def checksum(exclude=()):
    """md5 of every feature, property and geometry_data row (optionally without some feature ids)."""
    ex = "ARRAY[" + ",".join(str(int(i)) for i in exclude) + "]::bigint[]" if exclude else "ARRAY[]::bigint[]"
    s = SCHEMA
    return psql(
        f"SELECT (SELECT count(*) FROM {s}.feature) || ' ' || md5("
        f" coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.feature t WHERE t.id <> ALL({ex})), '')"
        f" || coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.property t"
        f"   WHERE t.feature_id <> ALL({ex})), '')"
        f" || coalesce((SELECT string_agg(t::text, '|' ORDER BY t.id) FROM {s}.geometry_data t"
        f"   WHERE t.feature_id IS NULL OR t.feature_id <> ALL({ex})), ''))")


def last_modified(exclude):
    ex = ",".join(str(int(i)) for i in exclude)
    return psql(f"SELECT string_agg(id || ':' || last_modification_date, ',' ORDER BY id) FROM {SCHEMA}.feature "
                f"WHERE id <> ALL(ARRAY[{ex}]::bigint[])")


def tree_ids(root_ids):
    """Root feature ids plus their sub-features (boundaries and their boundaries)."""
    ids = ",".join(str(int(i)) for i in root_ids)
    out = psql(f"WITH RECURSIVE t(id) AS (SELECT unnest(ARRAY[{ids}]::bigint[]) UNION "
               f"SELECT p.val_feature_id FROM {SCHEMA}.property p JOIN t ON p.feature_id = t.id "
               f"WHERE p.val_feature_id IS NOT NULL AND p.val_relation_type = 1) SELECT id FROM t")
    return sorted(int(x) for x in out.split() if x.strip())


def rows(objectid, active_only=True):
    cond = "AND f.termination_date IS NULL" if active_only else ""
    out = psql(f"SELECT f.id, f.termination_date IS NOT NULL, round(ST_ZMax(f.envelope)::numeric, 3) "
               f"FROM {SCHEMA}.feature f JOIN {SCHEMA}.objectclass oc ON oc.id = f.objectclass_id "
               f"WHERE oc.is_toplevel = 1 AND f.objectid = '{objectid}' {cond} ORDER BY f.id")
    return [(int(a), b == "t", float(c)) for a, b, c in (line.split("|") for line in out.splitlines() if line)]


def surface_ids(root_id):
    out = psql(f"SELECT f.objectid FROM {SCHEMA}.feature f WHERE f.id = ANY(ARRAY[{','.join(map(str, tree_ids([root_id])))}]"
               f"::bigint[]) AND f.id <> {int(root_id)} ORDER BY 1")
    return out.splitlines()


# --------------------------------------------------------------------------- scene
def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False


def set_target():
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("T10 local")
    if p is None:
        p = prefs.db_profiles.add()
        p.name = "T10 local"
    p.host, p.port, p.user = HOST, PORT, USER
    prefs.db_active_profile = p.name
    mod("citydb.credentials").store(p.server().key, SECRET)
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", SCHEMA
    sc.db_target_label, sc.db_target_srid = "T10 update", 25832


def db_root(objectid):
    roots = [o for o in bpy.data.objects
             if o.get("cgml3_source_kind") == "citydb" and o.get("cgml3_objectid") == objectid
             and o.get("cgml3_db_feature_id") is not None]
    return roots[0] if len(roots) == 1 else None


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def world_zmax(root):
    world = bpy.context.scene.world
    oz = float(world.get("cgml_Z-Origin", 0.0))
    z = -1e18
    for o in (root, *root.children_recursive):
        if o.type == 'MESH':
            mw = o.matrix_world
            for v in o.data.vertices:
                z = max(z, (mw @ v.co).z + oz)
    return z


def raise_roof(root, dz):
    """Raise the vertices of the roof faces (RoofSurface objects or faces with a roof material)."""
    moved = 0
    for o in (root, *root.children_recursive):
        if o.type != 'MESH':
            continue
        name = (str(o.get("cgml3_feature", "")) + " " + o.name).lower()
        whole = "roof" in name
        me = o.data
        if me.users > 1:
            o.data = me = me.copy()
        verts = set()
        for p in me.polygons:
            mat = o.material_slots[p.material_index].material if o.material_slots else None
            if whole or (mat is not None and "roof" in mat.name.lower()):
                verts.update(p.vertices)
        for i in verts:
            me.vertices[i].co.z += dz
        moved += len(verts)
        me.update()
    return moved


def call(op, **kw):
    try:
        return op(**kw)
    except RuntimeError as exc:
        return str(exc)


# --------------------------------------------------------------------------- cases
def case_eligibility(uc, bc):
    print("\n[T10] --- eligibility ---")
    root = db_root(B1)
    activate(root)
    _r, ok, reason = uc.status(bpy.context)
    check(ok, "DB-loaded root is eligible", reason)
    child = next((c for c in root.children_recursive if c.type == 'MESH'), None)
    if child is not None:
        activate(child)
        r, ok, _reason = uc.status(bpy.context)
        check(ok and r == root, "a child resolves to its root")
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    _r, ok, reason = uc.status(bpy.context)
    check(not ok and "Not loaded from 3DCityDB" in reason, "other object: disabled with reason", reason)
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=True)
    check(res != {'FINISHED'}, "operator refuses other objects", str(res)[:90])
    bpy.data.objects.remove(cube)


def case_dry_run_and_refusals(uc, bc, s):
    print("\n[T10] --- dry run and refusals write nothing ---")
    root = db_root(B1)
    fid = int(root["cgml3_db_feature_id"])
    raise_roof(root, ROOF_DZ)
    before = checksum()
    activate(root)
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=True, mode='TERMINATE')
    check(res == {'FINISHED'} and "would replace 1 row(s)" in s.message and "nothing written" in s.message,
          "dry run reports the rows it would replace", s.message)
    check(s.update_plan.get("ids") == [fid], "dry run lists the active row", str(s.update_plan.get("ids")))
    check(checksum() == before, "dry run: database unchanged (count + md5)")
    for mode in ('TERMINATE', 'DELETE'):
        res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode=mode)
        check(res != {'FINISHED'} and "not confirmed" in str(res), f"{mode.lower()} without confirmation refused",
              str(res).strip()[:90])
        res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode=mode, confirmed=True)
        check(res != {'FINISHED'}, f"{mode.lower()} confirmed but without rows refused", str(res).strip()[:90])
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode='TERMINATE', confirmed=True,
               expected_ids=str(fid + 100000))
    check("database changed" in s.message, "confirmation for other rows: refused after preview", s.message[:120])
    check(checksum() == before, "refusals: database unchanged")


def case_terminate(uc, bc, s):
    print("\n[T10] --- terminate: roof +2 m, only that feature changes ---")
    root = db_root(B1)
    fid = int(root["cgml3_db_feature_id"])
    old_rows = rows(B1)
    old_zmax = old_rows[0][2]
    edited_zmax = world_zmax(root)
    old_surfaces = surface_ids(fid)
    old_tree = tree_ids([fid])
    others_before = checksum(exclude=old_tree)
    lm_before = last_modified(exclude=old_tree)
    temp_before = set(os.listdir(bc._temp_dir)) if bc._temp_dir and os.path.isdir(bc._temp_dir) else set()
    activate(root)
    t = time.perf_counter()
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode='TERMINATE', confirmed=True,
               expected_ids=str(fid))
    print(f"  update + reload {time.perf_counter() - t:.1f} s: {s.message}")
    check(res == {'FINISHED'} and s.last_update.get("written"), "terminate written", s.message)
    new = rows(B1)
    allr = rows(B1, active_only=False)
    check(len(new) == 1 and new[0][0] != fid, "one active row with a new feature.id", str(new))
    check((fid, True, old_zmax) in allr, "old row kept and terminated", str(allr))
    new_id = new[0][0] if new else -1
    check(new and abs(new[0][2] - (old_zmax + ROOF_DZ)) < 0.01, "new envelope follows the roof (+2 m)",
          f"{old_zmax} -> {new[0][2] if new else '?'}; Blender {edited_zmax:.3f}")
    # the terminated old tree changed (termination_date); everything else must be bit-identical
    others_after = checksum(exclude=old_tree + tree_ids([new_id]))
    check(len(others_before.split(" ", 1)[-1]) == 32 and others_after.split(" ", 1)[-1] == others_before.split(" ", 1)[-1],
          "other features unchanged (md5 of all their feature/property/geometry rows)")
    lm_after = last_modified(exclude=old_tree + tree_ids([new_id]))
    check(lm_after == lm_before and lm_before.count(",") >= 20, "no other last_modification_date changed",
          f"{lm_before.count(',') + 1} other features")
    check(surface_ids(new_id) == old_surfaces, "objectid and surface gml:ids preserved",
          f"{len(old_surfaces)} surfaces")
    internal = psql(f"SELECT count(*) FROM {SCHEMA}.property WHERE name LIKE 'cgml3%' "
                    f"AND feature_id = ANY(ARRAY[{','.join(map(str, tree_ids([new_id])))}]::bigint[])")
    check(internal == "0", "no add-on properties written as attributes", internal)
    check(new_id in s.fid_to_index and fid not in s.fid_to_index, "box: old row replaced by the new one")
    r = db_root(B1)
    check(r is not None and int(r["cgml3_db_feature_id"]) == new_id, "reloaded root carries the new id",
          str(r["cgml3_db_feature_id"]) if r else "no root")
    if r is not None:
        z = world_zmax(r)
        check(abs(z - edited_zmax) < 0.01, "reloaded model has the edited roof", f"{z:.3f} vs {edited_zmax:.3f}")
    left = set(os.listdir(bc._temp_dir)) - temp_before if bc._temp_dir and os.path.isdir(bc._temp_dir) else set()
    check(not left, "temporary export removed", ", ".join(sorted(left)))


def case_delete(uc, bc, s):
    print("\n[T10] --- delete ---")
    bpy.ops.cgml3.db_browse_find(text=B2, mode='LOD2')
    root = db_root(B2)
    if not check(root is not None, "B2 loaded"):
        return
    fid = int(root["cgml3_db_feature_id"])
    activate(root)
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode='DELETE', confirmed=True, expected_ids=str(fid))
    allr = rows(B2, active_only=False)
    check(res == {'FINISHED'} and len(allr) == 1 and allr[0][0] != fid and not allr[0][1],
          "delete: old row gone, one new active row", f"{allr} · {str(res).strip()[:100]} · {s.message[:120]}")


def case_two_versions(uc, bc, s, buildings_dir, compose_dir):
    print("\n[T10] --- two active rows (LoD2 + LoD3) ---")
    import_file(compose_dir, os.path.join(buildings_dir, "buildings_lod3_citygml_3_0.gml"))
    bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
    root = db_root(B1)
    active = [r[0] for r in rows(B1)]
    check(len(active) == 2, "B1 has 2 active rows", str(active))
    activate(root)
    uc_rows = uc.known_versions(s, root)
    check(sorted(r.id for r in uc_rows) == active, "dialog lists both rows", str([r.id for r in uc_rows]))
    lines = mod("citydb.update").confirm_lines('TERMINATE', B1, uc_rows, "x")
    check("2 active rows" in " ".join(lines) and "ALL of them" in " ".join(lines), "dialog warns: all are replaced")
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=True)
    check("would replace 2 row(s)" in s.message and "all 2 versions" in s.message, "dry run warns", s.message)
    before = checksum()
    lod2 = int(root["cgml3_db_feature_id"])
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode='TERMINATE', confirmed=True,
               expected_ids=str(lod2))
    check(res != {'FINISHED'} and checksum() == before, "only one of two rows confirmed: nothing written",
          s.message[:120])
    res = call(bpy.ops.cgml3.db_update_feature, dry_run=False, mode='TERMINATE', confirmed=True,
               expected_ids=",".join(map(str, active)))
    after = rows(B1)
    check(res == {'FINISHED'} and len(after) == 1 and after[0][0] not in active,
          "both confirmed: both terminated, one new row", str(rows(B1, active_only=False)))


def case_secrets(bc):
    print("\n[T10] --- password ---")
    texts = "\n".join(t.as_string() for t in bpy.data.texts)
    names = [t.name for t in bpy.data.texts]
    check(any("citydb_update" in n for n in names) and SECRET not in texts, "text log written, no password in it",
          ", ".join(names))
    props = " ".join(str(o.get(k)) for o in bpy.data.objects for k in o.keys())
    check(SECRET not in props, "no password in custom properties")
    s = bc.active_session(bpy.context)
    check(SECRET not in (s.message + str(s.last_update)), "no password in messages")
    path = os.path.join(tempfile.mkdtemp(prefix="t10_"), "t10.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    with open(path, "rb") as fh:
        data = fh.read()
    check(SECRET.encode() not in data, "no password in the .blend")


def main():
    a = args()
    buildings_dir, compose_dir = a[0], a[1]
    if not SECRET:
        print("Set CGML3_TEST_DB_PASSWORD")
        sys.exit(2)
    t0 = time.perf_counter()
    print("[T10] creating scratch schema ...")
    create_schema()
    import_file(compose_dir, os.path.join(buildings_dir, "buildings_lod2_citygml_3_0.gml"))
    try:
        fresh_scene()
        set_target()
        bc, uc = mod("ops.browse_citydb"), mod("ops.update_citydb")
        res = bpy.ops.cgml3.db_browse_view(use_extent=True, extent=EXTENT)
        s = bc.active_session(bpy.context)
        check(res == {'FINISHED'} and len(s.rows) == 3, "3 boxes", str(len(s.rows)))
        bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=",".join(str(r.id) for r in s.rows if r.objectid == B1))
        res = bpy.ops.cgml3.db_browse_load(mode='LOD2')
        check(res == {'FINISHED'} and db_root(B1) is not None, "B1 loaded")
        case_eligibility(uc, bc)
        case_dry_run_and_refusals(uc, bc, s)
        case_terminate(uc, bc, s)
        case_delete(uc, bc, s)
        case_two_versions(uc, bc, s, buildings_dir, compose_dir)
        case_secrets(bc)
    finally:
        if "--keep-schema" not in a:
            drop_schema()
            left = psql(f"SELECT count(*) FROM pg_namespace WHERE nspname = '{SCHEMA}'")
            print(f"\n[T10] scratch schema dropped: {left == '0'}")
    print(f"[T10] {time.perf_counter() - t0:.0f} s")
    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] T10 write back checks satisfied")


try:
    main()
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
