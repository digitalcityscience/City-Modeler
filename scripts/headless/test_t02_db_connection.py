"""T02 end to end in Blender: server profile, discovery, city-model target,
DB import through citydb-tool (env credentials) and no password in .blend files.

Usage (password only through the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python scripts/headless/test_t02_db_connection.py -- \
      <out_dir> [--host 127.0.0.1] [--port 55433] [--user citydb]

Needs the local 3DCityDB v5 fixtures (see citydb-v5/README.md), the add-on as
an extension (bundled psycopg/keyring wheels) and Java for citydb-tool.
Exits 1 when any check fails.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

import bpy

from addon_test_utils import ensure_addon_enabled

FAILURES = []
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD", "")


def temp_dirs():
    """``cgml3_*`` folders in the temp dir (the DB operators must delete the ones they make)."""
    import glob
    import tempfile
    return {p for p in glob.glob(os.path.join(tempfile.gettempdir(), "cgml3_*"))
            if not os.path.basename(p).startswith("cgml3_citydb_")}   # the Browse cache, removed at exit


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("out_dir")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=55433)
    p.add_argument("--user", default="citydb")
    return p.parse_args(argv)


def addon_module(name, addon):
    import importlib
    return importlib.import_module(f"{addon}.{name}")


def run_op(op, *args, **kwargs):
    try:
        return op(*args, **kwargs), ""
    except RuntimeError as exc:
        return {"CANCELLED"}, str(exc)


def main():
    a = parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    if not SECRET:
        print("Set CGML3_TEST_DB_PASSWORD")
        sys.exit(2)

    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    client = addon_module("citydb.client", addon)
    credentials = addon_module("citydb.credentials", addon)

    print("\n[T02] --- driver inside Blender ---")
    check(client.available(), "psycopg importable from the bundled wheels")

    print("\n[T02] --- server profile + password + discovery ---")
    prefs = bpy.context.preferences.addons[addon].preferences
    prefs.db_profiles.clear()
    p = prefs.db_profiles.add()
    p.name = "Local dev"
    p.host, p.port, p.user = a.host, a.port, a.user
    prefs.db_active_profile = p.name
    wm = bpy.context.window_manager

    res, msg = run_op(bpy.ops.cgml3.db_discover)
    check(res == {"CANCELLED"} and "Password required" in msg, "Discovery without password asks for it", msg.strip())

    credentials.store(p.server().key, SECRET)
    res, msg = run_op(bpy.ops.cgml3.db_discover)
    check(res == {"FINISHED"}, "Discovery finished", wm.cgml3_db_message)
    items = {f"{i.database}/{i.schema}": i for i in wm.cgml3_db_models}
    check({"citydb/citydb", "citydb/hamburg_lod2", "citydb_demo/citydb"} <= set(items), "City models listed", ", ".join(items))
    hh = items.get("citydb/hamburg_lod2")
    check(hh is not None and hh.label == "Hamburg HafenCity (LoD2 only)", "Schema comment used as label",
          hh.label if hh else "")
    check(hh is not None and hh.counts_text == "3 Building" and hh.srid == 25832, "Facts per city model",
          f"{hh.counts_text} EPSG:{hh.srid} {hh.extent_text} {hh.lonlat_text}" if hh else "")
    check("postgres (no 3DCityDB)" in wm.cgml3_db_other, "Databases without 3DCityDB reported", wm.cgml3_db_other)
    sc = bpy.context.scene.cgml3
    check(sc.db_profile == "Local dev" and sc.db_name == "citydb" and sc.db_schema == "citydb",
          "First city model became the scene target", f"{sc.db_name}/{sc.db_schema}")

    print("\n[T02] --- pick another city model, rename it ---")
    wm.cgml3_db_models_index = list(items).index("citydb/hamburg_lod2")
    check(sc.db_name == "citydb" and sc.db_schema == "hamburg_lod2" and sc.db_target_srid == 25832,
          "Selecting a row sets the target", f"{sc.db_name}/{sc.db_schema}")
    hh.label = "HafenCity test"
    check(p.alias("citydb", "hamburg_lod2") == "HafenCity test" and sc.db_target_label == "HafenCity test",
          "Label stored per server profile and shown for the target")
    run_op(bpy.ops.cgml3.db_discover)
    check(wm.cgml3_db_models[wm.cgml3_db_models_index].label == "HafenCity test", "Label survives rediscovery")

    print("\n[T02] --- DB import of the target (citydb-tool with env credentials) ---")
    before = {o.as_pointer() for o in bpy.data.objects}
    dirs_before = temp_dirs()
    res, msg = run_op(bpy.ops.cgml3.import_from_db, "EXEC_DEFAULT")
    left = temp_dirs() - dirs_before
    check(not left, "DB import leaves no temp folder (no exported GML) behind", ", ".join(sorted(left)))
    new = [o for o in bpy.data.objects if o.as_pointer() not in before]
    roots = [o for o in new if o.parent is None and o.get("cgml3_feature") == "Building"]
    check(res == {"FINISHED"}, "DB import finished", msg.strip())
    check(len(roots) == 3, "3 buildings imported from citydb/hamburg_lod2", str(len(roots)))
    w = bpy.context.scene.world
    origin = (float(w.get("cgml_X-Origin", 0)), float(w.get("cgml_Y-Origin", 0)))
    xs = [(o.matrix_world @ v.co).x for o in new if o.type == 'MESH' for v in o.data.vertices]
    ys = [(o.matrix_world @ v.co).y for o in new if o.type == 'MESH' for v in o.data.vertices]
    centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if xs else (1e9, 1e9)
    # The envelope citydb-tool writes is the data's own, so the rounded centre lies within 50 m.
    check(origin[0] % 100 == 0 and origin[1] % 100 == 0 and abs(centre[0]) <= 100 and abs(centre[1]) <= 100,
          "T01 origin rule applies to DB imports", f"origin {origin}, local data centre {centre}")
    texts = "\n".join(t.as_string() for t in bpy.data.texts)
    check(SECRET not in texts, "No password in Blender text logs")

    print("\n[T02] --- verification stops bad targets before citydb-tool runs ---")
    res, msg = run_op(bpy.ops.cgml3.import_from_db, "EXEC_DEFAULT", db_schema="no_such_schema")
    check(res == {"CANCELLED"} and "is not a 3DCityDB schema" in msg, "Unknown schema rejected", msg.strip())
    res, msg = run_op(bpy.ops.cgml3.import_from_db, "EXEC_DEFAULT", db_name="postgres")
    check(res == {"CANCELLED"} and "not a 3DCityDB schema" in msg, "Non-3DCityDB database rejected", msg.strip())
    credentials.store(p.server().key, "wrong-password")
    res, msg = run_op(bpy.ops.cgml3.import_from_db, "EXEC_DEFAULT")
    check(res == {"CANCELLED"} and "Wrong user name or password" in msg and "wrong-password" not in msg,
          "Wrong password reported without echoing it", msg.strip())
    credentials.store(p.server().key, SECRET)

    print("\n[T02] --- .blend files never contain the password ---")
    legacy_ok = False
    sys_props = getattr(bpy.context.scene, "bl_system_properties_get", None)
    if sys_props is not None:
        try:
            group = sys_props().get("cgml3")
            group["db_pass"] = "LEGACY-SECRET-123"
            legacy_ok = "db_pass" in group.keys()
        except Exception as exc:
            print(f"     (could not plant a legacy value: {exc})")
    dbc = addon_module("ops.citydb_connection", addon)
    removed = dbc.purge_legacy_passwords()
    if legacy_ok:
        check(removed >= 1, "Legacy db_pass removed from the scene", str(removed))
    path = os.path.join(a.out_dir, "t02_target.blend")
    # Uncompressed, so a byte search really sees every stored string.
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=False)
    data = open(path, "rb").read()
    check(SECRET.encode() not in data, "Saved .blend does not contain the password")
    check(b"LEGACY-SECRET-123" not in data, "Saved .blend does not contain the legacy password")
    check(b"hamburg_lod2" in data, "Saved .blend remembers the target schema")

    bpy.ops.wm.open_mainfile(filepath=path)
    sc = bpy.context.scene.cgml3
    check(sc.db_schema == "hamburg_lod2" and sc.db_profile == "Local dev", "Target restored from the .blend",
          f"{sc.db_profile}: {sc.db_name}/{sc.db_schema}")

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed: {FAILURES}")
        sys.exit(1)
    print("\n[ALL PASS] T02 DB connection checks satisfied")


try:
    main()
except SystemExit:
    raise
except BaseException:
    import traceback
    traceback.print_exc()
    sys.exit(1)
