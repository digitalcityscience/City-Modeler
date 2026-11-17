"""B10: an authored building goes into 3DCityDB v5 and comes back.

Scratch schema ``b10_insert`` (created here, dropped at the end).
Scene: offset (565000, 5934000) in EPSG:25832; a gable-roofed house with a
window and a chimney, and an L-shaped house (two BuildingParts, hip roof).

Checks:
- Add to 3DCityDB (export_to_db, import mode "skip") inserts both
  Buildings with their gml:ids as objectid, the parts, the window and the
  chimney; coordinates land at the offset;
- pressing it again inserts nothing new ("skip");
- DB Browse finds the two boxes and loads the house back: LoD2 surfaces,
  roofType 3100, its window.

Usage (isolated profile, password only in the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python-exit-code 1 --python scripts/headless/test_b10_db_insert.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402

FAILURES = []
CONTAINER, SCHEMA = "citydb-v5-dev", "b10_insert"
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"
OFFSET = (565000.0, 5934000.0, 0.0)
ADDON = None


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    return _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])[1].strip()


def schema_sql(script):
    return _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                 "-v", f"schema_name={SCHEMA}", "-f", script])


def activate(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def set_target():
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("B10 local") or prefs.db_profiles.add()
    p.name, p.host, p.port, p.user = "B10 local", HOST, PORT, USER
    prefs.db_active_profile = p.name
    sys.modules[f"{ADDON}.citydb.credentials"]._session[p.server().key] = SECRET
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", SCHEMA
    sc.db_target_label, sc.db_target_srid = "B10 insert", 25832


def author_scene():
    bpy.context.scene.cgml3_offset = OFFSET
    bpy.ops.cgml.building_new(points=footprint(12, 8), height=6, epsg=25832)
    house = bpy.context.active_object
    bpy.ops.cgml.roof_add(roof_type="3100", pitch=35)
    front = next(o for o in house.children if o.get("cgml3_feature") == "WallSurface"
                 and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.9)
    activate(front)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.cgml.facade_draw('EXEC_DEFAULT', semantic_type='Window', object_name=front.name, face_index=0,
                             position_uv=(0.3, 0.4), width=1.2, height=1.4)
    bpy.ops.object.mode_set(mode='OBJECT')
    roof = next(o for o in house.children if o.get("cgml3_feature") == "RoofSurface")
    activate(roof)
    # B34: scripts pass the click (host, point on the roof, roof normal)
    poly = roof.data.polygons[0]
    centre = roof.matrix_world @ poly.center
    normal = (roof.matrix_world.to_3x3() @ poly.normal).normalized()
    bpy.ops.cgml.chimney_add(host=house.name, point=centre[:], normal=normal[:])

    mesh = bpy.data.meshes.new("L")
    mesh.from_pydata([(30, 0, 0), (40, 0, 0), (40, 5, 0), (35, 5, 0), (35, 12, 0), (30, 12, 0)], [],
                     [[0, 1, 2, 3, 4, 5]])
    src = bpy.data.objects.new("L", mesh)
    bpy.context.scene.collection.objects.link(src)
    bpy.context.view_layer.objects.active = src
    bpy.ops.cgml.building_from_footprint(height=5)
    lhouse = bpy.context.active_object
    bpy.data.objects.remove(src)
    activate(lhouse)
    bpy.ops.cgml.roof_add(roof_type="3200", pitch=30)
    return house, lhouse


def main():
    global ADDON
    if not SECRET:
        print("Set CGML3_TEST_DB_PASSWORD")
        sys.exit(2)
    schema_sql("drop-schema.sql")
    rc, out = schema_sql("create-schema.sql")
    if rc != 0:
        raise RuntimeError(out[-400:])
    try:
        bpy.ops.wm.read_homefile(use_empty=True)
        ok, ADDON = ensure_addon_enabled()
        assert ok, ADDON
        bpy.context.scene.world = bpy.data.worlds.new("World")
        bpy.context.scene.cgml3.xsd_validate = False
        set_target()
        house, lhouse = author_scene()
        ids = [house["gml_id"], lhouse["gml_id"]]

        for o in bpy.context.selected_objects:
            o.select_set(False)
        res = bpy.ops.cgml3.export_to_db('EXEC_DEFAULT', import_mode='skip', pre_delete_existing_features=False,
                                         citygml_version="3.0")
        check(res == {'FINISHED'}, "Add to 3DCityDB finishes", str(res))
        id_list = ",".join(f"'{i}'" for i in ids)
        n = psql(f"SELECT count(*) FROM {SCHEMA}.feature WHERE objectid IN ({id_list}) AND termination_date IS NULL")
        check(n == "2", "both Buildings inserted with their gml:id as objectid", n)
        kinds = psql(f"SELECT string_agg(o.classname || ':' || c, ',' ORDER BY o.classname) FROM (SELECT objectclass_id, "
                     f"count(*) c FROM {SCHEMA}.feature GROUP BY objectclass_id) t JOIN {SCHEMA}.objectclass o "
                     f"ON o.id = t.objectclass_id")
        for cls, minimum in (("BuildingPart", 2), ("WindowSurface", 1), ("BuildingInstallation", 1), ("RoofSurface", 2)):
            got = dict(kv.split(":") for kv in kinds.split(",") if ":" in kv).get(cls, "0")
            check(int(got) >= minimum, f"DB holds {cls} >= {minimum}", got)
        x = psql(f"SELECT round(ST_XMin(envelope)::numeric) FROM {SCHEMA}.feature WHERE objectid = '{ids[0]}'")
        check(x and abs(float(x) - (OFFSET[0] - 6)) < 2, "coordinates at the scene offset", x)

        rows_sql = f"SELECT count(*) || ':' || md5(string_agg(id::text, ',' ORDER BY id)) FROM {SCHEMA}.feature"
        before = psql(rows_sql)
        bpy.ops.cgml3.export_to_db('EXEC_DEFAULT', import_mode='skip', pre_delete_existing_features=False,
                                   citygml_version="3.0")
        after = psql(rows_sql)
        check(before == after, "pressing it again changes nothing (same rows, same ids)", f"{before} -> {after}")

        # Load the house back through DB Browse into a fresh scene.
        bpy.ops.wm.read_homefile(use_empty=True)
        ensure_addon_enabled()
        bpy.context.scene.world = bpy.data.worlds.new("World")
        set_target()
        res = bpy.ops.cgml3.db_browse_view(use_extent=True, extent=(OFFSET[0] - 50, OFFSET[1] - 50,
                                                                    OFFSET[0] + 100, OFFSET[1] + 100))
        bc = sys.modules[f"{ADDON}.ops.browse_citydb"]
        s = bc.active_session(bpy.context)
        check(res == {'FINISHED'} and s is not None and len(s.rows) == 2, "DB Browse shows 2 boxes",
              str(len(s.rows) if s else None))
        row = next(r for r in s.rows if r.objectid == ids[0])
        bpy.ops.cgml3.db_browse_select(action='SET', feature_ids=str(row.id))
        res = bpy.ops.cgml3.db_browse_load(mode='HIGHEST')
        root = next((o for o in bpy.data.objects if o.get("gml_id") == ids[0]), None)
        check(res == {'FINISHED'} and root is not None, "house loaded back", str(res))
        if root is not None:
            kinds = [str(o.get("cgml3_feature")) for o in root.children_recursive]
            check(kinds.count("RoofSurface") == 2 and kinds.count("WallSurface") >= 4, "LoD2 surfaces back",
                  str(sorted(set(kinds))))
            check(str(root.get("bldg:roofType")) == "3100", "roofType 3100 back", str(root.get("bldg:roofType")))
            check(any(k in ("WindowSurface", "Window") for k in kinds), "window back")
    finally:
        schema_sql("drop-schema.sql")
        left = psql(f"SELECT count(*) FROM pg_namespace WHERE nspname = '{SCHEMA}'")
        print(f"[B10] scratch schema dropped: {left == '0'}")
    if FAILURES:
        print(f"\n[FAIL] B10 NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print("\n[ALL PASS] B10 DB insert")


main()
