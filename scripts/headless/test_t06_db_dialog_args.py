"""T06: the DB import/export dialogs build the same citydb-tool commands after the relabeling.

``CityDBTool._run`` is replaced by a fake on the main thread: it records the
argument list and environment keys, runs nothing and reports success. The
import dialog then stops at the missing GML, the export dialog after the
(fake) citydb import; nothing reaches the database. A connection is still
resolved (the operators verify the target with psycopg), so the local v5 must
be up.

Cases cover the defaults, every dialog option set to a non-default value and
the scene filters (bbox, LoD, feature types). Temp paths are normalized.

Usage (isolated profile, password only in the environment):
  CGML3_TEST_DB_PASSWORD=... blender -b --python-exit-code 1 --python scripts/headless/test_t06_db_dialog_args.py -- \
      <buildings_dir> --write <baseline.json>
  ... -- <buildings_dir> --compare <baseline.json>

``--write`` stores the commands (run it before a dialog change), ``--compare``
checks them against that file and that changing one setting changes the
command (negative control). Both modes also check the password is never an
argument (it goes to the child process environment only).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
ADDON = None
SECRET = os.environ.get("CGML3_TEST_DB_PASSWORD") or os.environ.get("CITYDB_PASSWORD", "")
HOST, PORT, USER = "127.0.0.1", 55433, "citydb"

IMPORT_CASES = {
    "import_defaults": ({}, {}),
    "import_all_options": ({
        "crs": "EPSG:4326", "crs_name": "urn:ogc:def:crs:EPSG::4326", "transform": "swap-xy",
        "type_names": "bldg:Building, brid:Bridge", "lod": "1,2", "lod_mode": "minimum",
        "lod_search_depth": "2", "cql2_filter": "bldg:storeysAboveGround > 1",
        "filter_crs": "EPSG:25832", "sql_filter": "select id from citydb.feature",
        "sort_by": "core:id+desc", "limit": 7, "start_index": 3,
        "validity": "at", "validity_at": "2024-01-01", "validity_between": "",
        "validity_reference": "transaction", "lenient_validity": True,
        "no_appearances": True, "appearance_themes": "rgbTexture,summer",
        "no_pretty_print": True,
    }, {}),
    "import_validity_between": ({
        "validity": "between", "validity_between": "2020-01-01,2025-01-01", "validity_reference": "validity",
        "lod_mode": "or",
    }, {}),
    "import_scene_filters": ({}, {
        "use_bbox_filter": True, "bbox_min_x": 564700.0, "bbox_min_y": 5934100.0,
        "bbox_max_x": 565000.0, "bbox_max_y": 5934500.0,
        "use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_3": False, "lod_4": False,
        "use_feature_type_filter": True, "import_bridges": False,
    }),
    "import_scene_bbox_and_cql2": ({"cql2_filter": "core:id > 0", "crs": "EPSG:25832"}, {
        "use_bbox_filter": True, "bbox_min_x": 1.0, "bbox_min_y": 2.0, "bbox_max_x": 3.0, "bbox_max_y": 4.0,
    }),
}

EXPORT_CASES = {
    "export_defaults": ({}, {}),
    "export_all_options": ({
        "citygml_version": "2.0", "pre_delete_existing_features": False,
        "import_mode": "skip", "import_threads": 3, "import_compute_extent": True,
        "import_transform": "swap-xy", "import_type_names": "bldg:Building",
        "import_feature_ids": "ID_1,ID_2", "import_bbox": "1,2,3,4,25832", "import_bbox_mode": "contains",
        "import_limit": 5, "import_start_index": 1, "import_no_appearances": True,
        "import_appearance_themes": "rgbTexture", "import_xal_source": True,
        "use_lod4_as_lod3": True, "map_lod0_roof_edge": True, "map_lod1_surface": True,
    }, {}),
    "export_pre_delete_terminate": ({"pre_delete_mode": "terminate", "import_mode": "skip"}, {}),
    "export_scene_filters": ({"pre_delete_existing_features": False}, {
        "use_bbox_filter": True, "bbox_min_x": 564700.0, "bbox_min_y": 5934100.0,
        "bbox_max_x": 565000.0, "bbox_max_y": 5934500.0,
        "use_feature_type_filter": True, "import_vegetation": False,
    }),
}

# negative control: one setting changed must change the recorded command
NEGATIVE = {
    "import_all_options": ("import_from_db", {"lod": "3"}),
    "export_all_options": ("export_to_db", {"import_bbox_mode": "on_tile"}),
}


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label + (f" - {detail}" if detail else ""))
    return cond


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


_TMP = re.compile(r"[^\s=]*cgml3_[A-Za-z0-9_]+[\\/]")
# the add-on folder depends on the Blender profile; the baseline and the compare run may use different ones
_ADDON_DIR = re.compile(r"[^\s=]*[\\/]city_modeler[\\/]")


def _normalize(arg):
    return _ADDON_DIR.sub("<ADDON>/", _TMP.sub("<TMP>/", str(arg))).replace("\\", "/")


def fresh_scene(buildings_dir):
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False
    res = bpy.ops.cgml3.import_gml_file(filepath=os.path.join(buildings_dir, "buildings_lod2_citygml_3_0.gml"))
    assert res == {'FINISHED'}, res


def set_target():
    prefs = bpy.context.preferences.addons[ADDON].preferences
    p = prefs.db_profiles.get("T06 local")
    if p is None:
        p = prefs.db_profiles.add()
        p.name = "T06 local"
    p.host, p.port, p.user = HOST, PORT, USER
    prefs.db_active_profile = p.name
    mod("citydb.credentials").store(p.server().key, SECRET)
    sc = bpy.context.scene.cgml3
    sc.db_profile = p.name
    sc.db_host, sc.db_port, sc.db_user, sc.db_sslmode = HOST, str(PORT), USER, "prefer"
    sc.db_name, sc.db_schema = "citydb", "citydb"
    sc.db_target_label, sc.db_target_srid = "T06 dialog", 25832


def record(op_name, op_kwargs, scene_kwargs):
    """citydb-tool calls of one operator run: [[args...], ...] plus the env keys."""
    tool_cls = mod("ops.citydb_cli").CityDBTool
    calls = []

    def fake_run(self, run_args, cwd=None, timeout=None, env=None, on_output=None, cancelled=None):
        calls.append({"args": [_normalize(a) for a in run_args], "env": sorted((env or {}).keys()),
                      "raw": " ".join(map(str, run_args))})
        return True, ""

    sc = bpy.context.scene.cgml3
    saved = {k: getattr(sc, k) for k in scene_kwargs}
    original = tool_cls._run
    tool_cls._run = fake_run
    try:
        for k, v in scene_kwargs.items():
            setattr(sc, k, v)
        getattr(bpy.ops.cgml3, op_name)('EXEC_DEFAULT', **op_kwargs)
    except RuntimeError as exc:  # CANCELLED with an error report
        print(f"    ({op_name}: {str(exc).strip().splitlines()[-1][:120]})")
    finally:
        tool_cls._run = original
        for k, v in saved.items():
            setattr(sc, k, v)
    leaked = bool(SECRET) and any(SECRET in c["raw"] for c in calls)
    return [{"args": c["args"], "env": c["env"]} for c in calls], leaked


def check_labels():
    """No dialog label is a raw citydb-tool flag; the flag sits at the end of the tooltip."""
    for op_name in ("import_from_db", "export_to_db"):
        rna = getattr(bpy.ops.cgml3, op_name).get_rna_type()
        raw = [p.identifier for p in rna.properties if p.name.startswith("-")]
        raw += [f"{p.identifier}.{i.identifier}" for p in rna.properties if p.type == 'ENUM'
                for i in p.enum_items if i.name.startswith("(")]
        check(not raw, f"{op_name}: no raw flag labels", ", ".join(raw))
        flag_mid = [p.identifier for p in rna.properties
                    if "citydb-tool --" in p.description and not p.description.rstrip().endswith(")")]
        check(not flag_mid, f"{op_name}: flags only at the end of tooltips", ", ".join(flag_mid))


def check_area_fill():
    """'Scene extent' writes the CRS box of the imported buildings into the scene bbox filter."""
    sc = bpy.context.scene.cgml3
    saved = (sc.use_bbox_filter, sc.bbox_min_x, sc.bbox_min_y, sc.bbox_max_x, sc.bbox_max_y)
    try:
        sc.use_bbox_filter = False
        res = bpy.ops.cgml3.db_area_fill(source='SCENE')
        box = (sc.bbox_min_x, sc.bbox_min_y, sc.bbox_max_x, sc.bbox_max_y)
        # the 3 buildings span 564830.83/5934297.45 - 564870.59/5934340.83 (EPSG:25832, DB envelopes)
        want = (564830.83, 5934297.45, 564870.59, 5934340.83)
        ok = res == {'FINISHED'} and sc.use_bbox_filter and all(abs(b - w) < 1.0 for b, w in zip(box, want))
        check(ok, "area from scene extent is in CRS coordinates", f"{res} {box}")
    finally:
        sc.use_bbox_filter, sc.bbox_min_x, sc.bbox_min_y, sc.bbox_max_x, sc.bbox_max_y = saved


def temp_dirs():
    """``cgml3_*`` folders in the temp dir (the DB operators must delete the ones they make)."""
    import glob
    import tempfile
    return {p for p in glob.glob(os.path.join(tempfile.gettempdir(), "cgml3_*"))
            if not os.path.basename(p).startswith("cgml3_citydb_")}   # the Browse cache, removed at exit


def collect():
    out = {}
    for name, (kw, scene_kw) in IMPORT_CASES.items():
        out[name], leaked = record("import_from_db", kw, scene_kw)
        check(not leaked, f"{name}: password not on the command line")
    for name, (kw, scene_kw) in EXPORT_CASES.items():
        out[name], leaked = record("export_to_db", kw, scene_kw)
        check(not leaked, f"{name}: password not on the command line")
    return out


def main():
    a = args()
    if not a or not SECRET or not ({"--write", "--compare"} & set(a)):
        print("Usage: -- <buildings_dir> --write|--compare <json>  (CGML3_TEST_DB_PASSWORD set)")
        sys.exit(2)
    buildings_dir = a[0]
    mode = "--write" if "--write" in a else "--compare"
    path = a[a.index(mode) + 1]

    fresh_scene(buildings_dir)
    set_target()
    before = temp_dirs()
    got = collect()
    left = temp_dirs() - before
    check(not left, "the DB operators leave no temp folder behind", ", ".join(sorted(left)))
    for name, calls in got.items():
        check(len(calls) >= 1, f"{name}: citydb-tool was called", str(len(calls)))

    if mode == "--write":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(got, fh, indent=1)
        print(f"[T06] wrote {len(got)} cases to {path}")
    else:
        check_labels()
        check_area_fill()
        with open(path, encoding="utf-8") as fh:
            want = json.load(fh)
        check(set(got) == set(want), "same case set", f"{sorted(set(got) ^ set(want))}")
        for name in sorted(want):
            same = got.get(name) == want[name]
            check(same, f"{name}: command unchanged",
                  "" if same else f"\n      before: {want[name]}\n      after:  {got.get(name)}")
        for name, (op_name, change) in NEGATIVE.items():
            cases = IMPORT_CASES if op_name == "import_from_db" else EXPORT_CASES
            kw, scene_kw = cases[name]
            changed, _ = record(op_name, {**kw, **change}, scene_kw)
            check(changed != want[name], f"negative control {name} {change}: command changes")

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] T06 dialog command checks satisfied")


main()
