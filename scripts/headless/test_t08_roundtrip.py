"""T08 follow-up: import -> export -> validate -> 3DCityDB -> import again.

Small files only. For every case the scene (Load all, or a Browse first
selection) is exported, the export is checked against the CityGML XSD
(offline schemas in Schema/), imported into a scratch 3DCityDB v5 schema
with citydb-tool, exported again by citydb-tool, and both files are imported
into Blender again. Each top-level feature and the whole scene are compared
with the first import: polygon count and the set of world coordinates (1 mm).

With --test-files <dir> every *.gml under <dir> (the repo's test_files:
Bridge, Tunnel, Railway, CityFurniture, Generics, Relief, WaterBody, ...)
is run through the same chain in its own and in the other CityGML version.
citydb-tool 1.3.0 stores coordinates unchanged as the DB CRS (EPSG:25832),
so files in another CRS come back with the same numbers (noted, not failed).

Usage (isolated profile, see tickets/README.md):
  blender -b --python-exit-code 1 --python scripts/headless/test_t08_roundtrip.py -- \
      <buildings_dir> <hh_tile.gml> <citydb_compose_dir> [--no-db] \
      [--test-files <dir>] [--only-test-files]

The output goes to <citydb_compose_dir>/data/roundtrip_t08 (the citydb-tool
container sees ./data as /data). The scratch schema t08_roundtrip is created
for each case and dropped at the end. The DB password is never passed on a
command line: docker compose reads it from its own defaults or the
environment (CITYDB_PASSWORD).
"""
import os
import random
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []
NOTES = []
SKIPPED = []  # test files the add-on refuses as out of scope
CONTROLLED = set()  # versions whose XSD validator was shown to reject a broken file
CASE = ""
ADDON = None
SCHEMA = "t08_roundtrip"
CONTAINER = "citydb-v5-dev"
DB = None  # compose dir, None with --no-db
OUT = None


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(cond, label, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"[{CASE}] {label}" + (f" - {detail}" if detail else ""))
    return cond


def fresh_scene():
    global ADDON
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, ADDON = ensure_addon_enabled()
    assert ok, ADDON
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False


def mod(name):
    return sys.modules[f"{ADDON}.{name}"]


# --------------------------------------------------------------------------- scene signature
def roots():
    """Top-level features: objects with a gml_id whose parent has none."""
    return [o for o in bpy.data.objects
            if o.get("gml_id") and (o.parent is None or not o.parent.get("gml_id"))
            and not o.get("cgml3_browse_path")]


def signature(root):
    world = bpy.context.scene.world
    off = (float(world.get("cgml_X-Origin", 0.0)), float(world.get("cgml_Y-Origin", 0.0)), float(world.get("cgml_Z-Origin", 0.0)))
    pts = set()
    polygons = 0
    for o in (root, *root.children_recursive):
        if o.type != 'MESH':
            continue
        mw = o.matrix_world
        polygons += len(o.data.polygons)
        for v in o.data.vertices:
            w = mw @ v.co
            pts.add((round(w.x + off[0], 3), round(w.y + off[1], 3), round(w.z + off[2], 3)))
    return {"polygons": polygons, "points": pts}


def scene_signatures(only=None):
    out = {}
    for r in roots():
        gid = str(r["gml_id"])
        if only is None or gid in only:
            out[gid] = signature(r)
    return out


def scene_total():
    """Polygons and world points of every mesh in the scene (helper objects excluded)."""
    world = bpy.context.scene.world
    off = (float(world.get("cgml_X-Origin", 0.0)), float(world.get("cgml_Y-Origin", 0.0)), float(world.get("cgml_Z-Origin", 0.0)))
    pts = set()
    polygons = 0
    for o in bpy.data.objects:
        if o.type != 'MESH' or o.get("cgml3_browse_path"):
            continue
        mw = o.matrix_world
        polygons += len(o.data.polygons)
        for v in o.data.vertices:
            w = mw @ v.co
            pts.add((round(w.x + off[0], 3), round(w.y + off[1], 3), round(w.z + off[2], 3)))
    return {"polygons": polygons, "points": pts}


def compare_total(label, a, b):
    check(a["polygons"] == b["polygons"], f"{label}: polygons in the scene", f"{a['polygons']} -> {b['polygons']}")
    missing = a["points"] - b["points"]
    extra = b["points"] - a["points"]
    detail = f"-{len(missing)}/+{len(extra)} of {len(a['points'])}"
    if missing and extra:
        # nearest-neighbour distance tells a shift from a missing part
        worst = max(min(max(abs(p[i] - q[i]) for i in range(3)) for q in extra) for p in list(missing)[:50])
        detail += f", worst shift of a sample {worst * 1000:.1f} mm"
    check(not missing and not extra, f"{label}: coordinates in the scene (1 mm)", detail)


def compare(label, ref, got):
    check(sorted(got) == sorted(ref), f"{label}: same features", f"{len(got)} of {len(ref)}")
    poly_diff = []
    point_diff = []
    for gid, a in ref.items():
        b = got.get(gid)
        if b is None:
            continue
        if a["polygons"] != b["polygons"]:
            poly_diff.append(f"{gid} {a['polygons']}->{b['polygons']}")
        missing = len(a["points"] - b["points"])
        extra = len(b["points"] - a["points"])
        if missing or extra:
            point_diff.append(f"{gid} -{missing}/+{extra} of {len(a['points'])}")
    check(not poly_diff, f"{label}: polygon count per building", "; ".join(poly_diff[:4]))
    check(not point_diff, f"{label}: coordinates per building (1 mm)", "; ".join(point_diff[:4]))


# --------------------------------------------------------------------------- export / validate
def export(path, version):
    res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
    return check(res == {'FINISHED'} and os.path.isfile(path), f"export CityGML {version}",
                 f"{os.path.getsize(path):,} bytes" if os.path.isfile(path) else str(res))


def _validate(path, version):
    if version == "3.0":
        ok, msg = mod("ops.validate").validate_citygml3_xsd(path)
    else:
        ok, msg = mod("ops.validate_citygml2").validate_citygml2_xsd(path, fallback_online=False)
    msg = str(msg or "")
    # a skipped schema check ("XSD-Check übersprungen") is not a pass
    return ok and "bersprungen" not in msg, msg


def xsd(path, version):
    ok, msg = _validate(path, version)
    return check(ok, f"XSD valid (CityGML {version})", msg.strip().replace("\n", " | ")[:300])


def xsd_negative_control(path, version):
    """The validator must reject a broken copy, otherwise a pass means nothing."""
    import re
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    bad = path[:-4] + "_broken.gml"
    # a second, unknown child in the first cityObjectMember (any module)
    broken, n = re.subn(r"(<(?:\w+:)?cityObjectMember>)", r'\1<x:notAFeature xmlns:x="urn:t08:broken"/>', text, 1)
    assert n == 1, "no cityObjectMember to break"
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write(broken)
    ok, msg = _validate(bad, version)
    os.remove(bad)
    check(not ok, f"XSD validator rejects a broken CityGML {version} file", msg.strip().splitlines()[-1][:200])


def member_ids(path):
    """gml:id of the feature in each top-level cityObjectMember/featureMember."""
    import xml.etree.ElementTree as ET
    ids = []
    for _ev, el in ET.iterparse(path):
        if el.tag.rsplit("}", 1)[-1] in ("cityObjectMember", "featureMember"):
            child = next(iter(el), None)
            gid = None
            if child is not None:
                gid = next((v for k, v in child.attrib.items() if k.rsplit("}", 1)[-1] == "id"), None)
            ids.append(gid or "")
    return ids


def member_types(path):
    """Element name of the feature in each top-level cityObjectMember/featureMember."""
    import xml.etree.ElementTree as ET
    types = []
    for _ev, el in ET.iterparse(path):
        if el.tag.rsplit("}", 1)[-1] in ("cityObjectMember", "featureMember"):
            child = next(iter(el), None)
            types.append(child.tag.rsplit("}", 1)[-1] if child is not None else "")
    return types


def count_members(path):
    return len(member_ids(path))


def reimport(path):
    fresh_scene()
    res = bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
    check(res == {'FINISHED'}, f"re-import {os.path.basename(path)}", str(res))


# --------------------------------------------------------------------------- 3DCityDB
def _run(cmd, env=None, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def psql(sql):
    return _run(["docker", "exec", CONTAINER, "psql", "-qtA", "-U", "citydb", "-d", "citydb", "-c", sql])


def reset_schema():
    _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
          "-v", f"schema_name={SCHEMA}", "-f", "drop-schema.sql"])
    rc, out = _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
                    "-v", f"schema_name={SCHEMA}", "-f", "create-schema.sql"])
    if rc != 0:
        raise RuntimeError(f"create-schema failed: {out[-500:]}")


def drop_schema():
    _run(["docker", "exec", "-w", "/3dcitydb", CONTAINER, "psql", "-q", "-U", "citydb", "-d", "citydb",
          "-v", f"schema_name={SCHEMA}", "-f", "drop-schema.sql"])


def citydb_tool(*cli):
    env = dict(os.environ, CITYDB_SCHEMA=SCHEMA, MSYS_NO_PATHCONV="1")
    return _run(["docker", "compose", "run", "--rm", "-e", "CITYDB_SCHEMA", "citydb-tool", *cli], env=env, cwd=DB)


def log_problems(log):
    lines = [ln for ln in log.splitlines() if "[ERROR]" in ln or "[WARN]" in ln]
    return lines


def source_srs(path):
    """(srsName, EPSG code or None) of the first srsName in the file."""
    import re
    with open(path, "rb") as fh:
        head = fh.read(256 * 1024).decode("utf-8", "replace")
    m = re.search(r'srsName="([^"]+)"', head)
    if not m:
        return "", None
    code = re.search(r"EPSG:+(\d+)", m.group(1))
    return m.group(1), (int(code.group(1)) if code else None)


def db_roundtrip(case, path, version, expected, srs=("", None)):
    """Import ``path`` with citydb-tool, export it again; returns the DB export path or None."""
    reset_schema()
    rel = os.path.relpath(path, os.path.join(DB, "data")).replace(os.sep, "/")
    rc, log = citydb_tool("import", "citygml", "--fail-fast", rel)
    problems = log_problems(log)
    ok = check(rc == 0, "citydb-tool import", f"exit {rc}" + (f"; {problems[0][:300]}" if problems else ""))
    for ln in problems[:5]:
        print(f"    citydb-tool: {ln.strip()[:300]}")
    if problems:
        NOTES.append(f"{case}: citydb-tool import logged {len(problems)} warning/error line(s): {problems[0].strip()[:200]}")
    if not ok:
        print(log[-2000:])
        return None
    # the exported members by gml:id (objectid); 3DCityDB v5 may store some of
    # them as non-top-level features (TINRelief without a ReliefFeature)
    ids = [i for i in member_ids(path) if i]
    id_list = ",".join("'" + i.replace("'", "''") + "'" for i in ids) or "''"
    rc, out = psql(f"SELECT oc.classname, oc.is_toplevel, count(DISTINCT f.objectid) FROM {SCHEMA}.feature f "
                   f"JOIN {SCHEMA}.objectclass oc ON oc.id = f.objectclass_id "
                   f"WHERE f.objectid IN ({id_list}) GROUP BY 1, 2 ORDER BY 1")
    rows = [line.split("|") for line in out.split() if line.count("|") == 2]
    n = sum(int(r[2]) for r in rows)
    check(n == expected, "exported features stored in the DB", f"{rows} (expected {expected})")
    if problems:
        print(log[-3000:])
    nested = [r[0] for r in rows if r[1] == "0"]
    if nested:
        NOTES.append(f"{case}: 3DCityDB stores {', '.join(nested)} as non-top-level; citydb-tool export "
                     f"does not return it, so the DB re-import is skipped")
        return None
    rc, out = psql(f"SELECT count(*) FROM {SCHEMA}.geometry_data WHERE geometry IS NOT NULL")
    print(f"    geometry_data rows: {out.strip()}")

    db_out = path[:-4] + "_db.gml"
    rel_out = os.path.relpath(db_out, os.path.join(DB, "data")).replace(os.sep, "/")
    # citydb-tool 1.3.0 does not reproject on import: the numbers are stored
    # unchanged as the DB CRS (EPSG:25832) whatever the srsName says, so they
    # come back unchanged without --crs. A file in another CRS is therefore
    # mislabelled in the DB; see NOTES.
    srs = source_srs(path)
    if srs[1] and srs[1] != 25832:
        NOTES.append(f"{case}: srsName {srs[0]} is stored as EPSG:25832 by citydb-tool (no reprojection)")
    rc, log = citydb_tool("export", "citygml", "-v", version, "-o", rel_out)
    check(rc == 0 and os.path.isfile(db_out), "citydb-tool export", f"exit {rc}")
    if rc != 0 or not os.path.isfile(db_out):
        print(log[-2000:])
        return None
    xsd(db_out, version)
    return db_out


# --------------------------------------------------------------------------- cases
def run_case(case, ref, version, expected=None, total=None, srs=("", None)):
    """``ref`` = signatures of the scene being exported (current scene);
    ``expected`` = top-level members the export must hold (default: len(ref));
    ``total`` = scene_total() of the first import, compared as well."""
    global CASE
    CASE = f"{case} v{version}"
    expected = len(ref) if expected is None else expected
    print(f"\n[T08-RT] --- {case}: export CityGML {version} ---")
    path = os.path.join(OUT, f"{case}_v{version.replace('.', '')}.gml")
    if not export(path, version):
        return
    members = count_members(path)
    check(members == expected, "export holds exactly the scene's top-level features",
          f"{members} members, expected {expected}")
    if xsd(path, version) and version not in CONTROLLED:
        CONTROLLED.add(version)
        xsd_negative_control(path, version)

    print(f"[T08-RT] {case}: re-import own export")
    reimport(path)
    compare(f"{case} v{version} own export", ref, scene_signatures())
    if total is not None:
        compare_total(f"{case} v{version} own export", total, scene_total())

    if DB:
        print(f"[T08-RT] {case}: 3DCityDB import / export")
        db_out = db_roundtrip(case, path, version, expected, srs)
        if db_out:
            reimport(db_out)
            compare(f"{case} v{version} via 3DCityDB", ref, scene_signatures())
            if total is not None:
                compare_total(f"{case} v{version} via 3DCityDB", total, scene_total())


def test_file_case(path):
    """One repo test file: import all, export in its own and the other version."""
    name = os.path.basename(path)
    own = "2.0" if "citygml2" in name.lower() or "CityGML2" in name else "3.0"
    other = "3.0" if own == "2.0" else "2.0"
    srs = source_srs(path)
    expected = count_members(path)
    case = os.path.splitext(name)[0].replace("Railway_Scene_LoD3_", "RS_")
    print(f"\n[T08-RT] ===== {name} (CityGML {own}, {srs[0] or 'no srsName'}, {expected} members) =====")
    global CASE
    CASE = case
    fresh_scene()
    try:
        res = bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
    except RuntimeError as exc:
        # the add-on refuses a file it builds nothing from with its scope notice; that is a skip
        # only if the file has no Building/Transportation/CityFurniture member, otherwise a failure
        bp = mod("shared.builder_payload")
        refused = bp.scope_notice(["x"]).split("(")[0] in str(exc)
        in_scope = set(bp.BUILDING_TYPES + bp.TRANSPORT_TOP_TYPES + bp.FURNITURE_TYPES)
        types = sorted(set(member_types(path)))
        if refused and not in_scope.intersection(types):
            print(f"  [SKIP] {name}: out of scope ({', '.join(types)})")
            SKIPPED.append(f"{os.path.relpath(path)} ({', '.join(types)})")
            return
        res = str(exc).strip().splitlines()[0][:300]
    if not check(res == {'FINISHED'}, f"import {name}", str(res)):
        return
    ref = scene_signatures()
    total = scene_total()
    print(f"    {len(ref)} top-level objects with gml_id, {total['polygons']} polygons, {len(total['points'])} points")
    for version in (own, other):
        fresh_scene()
        bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
        run_case(case, ref, version, expected=expected, total=total, srs=srs)


def full_import_case(buildings_dir, name, versions):
    fresh_scene()
    src = os.path.join(buildings_dir, name)
    res = bpy.ops.cgml3.import_gml_file(filepath=src, load_mode='ALL')
    check(res == {'FINISHED'}, f"import {name}", str(res))
    ref = scene_signatures()
    print(f"    {name}: {len(ref)} buildings, {sum(s['polygons'] for s in ref.values())} polygons")
    case = name.replace("buildings_", "").replace("_citygml", "").replace(".gml", "")
    for version in versions:
        # every case starts again from the original import
        fresh_scene()
        bpy.ops.cgml3.import_gml_file(filepath=src, load_mode='ALL')
        run_case(f"all_{case}", ref, version)


def browse_case(path, case, pick, versions, seed):
    def load():
        fresh_scene()
        cache = os.path.join(OUT, "index_cache")
        os.environ["CGML3_INDEX_CACHE_DIR"] = cache
        res = bpy.ops.cgml3.browse_gml_file(filepath=path, use_cache=True)
        s = sys.modules[f"{ADDON}.ops.browse_gml"].get_session(path)
        ids = sorted(e.gml_id for e in random.Random(seed).sample(s.entries, pick))
        bpy.ops.cgml3.browse_select(action='SET', ids=",".join(ids))
        res = bpy.ops.cgml3.browse_load(mode='HIGHEST')
        check(res == {'FINISHED'}, f"browse {os.path.basename(path)}: load {pick} of {len(s.entries)}", str(res))
        return ids

    ids = load()
    ref = scene_signatures()
    check(sorted(ref) == ids, "scene holds only the loaded buildings (proxy is not a feature)",
          f"{len(ref)} roots")
    for version in versions:
        load()
        run_case(f"browse_{case}", ref, version)


def main():
    global DB, OUT
    a = args()
    buildings_dir, hh_tile, compose_dir = a[0], a[1], a[2]
    DB = None if "--no-db" in a else compose_dir
    OUT = os.path.join(compose_dir, "data", "roundtrip_t08")
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)
    test_files = a[a.index("--test-files") + 1] if "--test-files" in a else None
    t0 = time.perf_counter()
    try:
        if "--only-test-files" not in a:
            full_import_case(buildings_dir, "buildings_lod2_citygml_3_0.gml", ("3.0", "2.0"))
            full_import_case(buildings_dir, "buildings_lod3_citygml_3_0.gml", ("3.0",))
            full_import_case(buildings_dir, "buildings_lod2_citygml_2_0.gml", ("2.0", "3.0"))
            full_import_case(buildings_dir, "buildings_lod3_citygml_2_0.gml", ("2.0",))
            browse_case(os.path.join(buildings_dir, "buildings_lod3_citygml_3_0.gml"), "lod3_3_0", 2, ("3.0",), 1)
            browse_case(hh_tile, "hh_1_0", 6, ("2.0", "3.0"), 564)
        if test_files:
            for root, _dirs, files in sorted(os.walk(test_files)):
                if os.path.basename(root) in ("Building", "CitySmith"):
                    continue  # buildings are covered above
                for f in sorted(files):
                    if f.lower().endswith(".gml"):
                        test_file_case(os.path.join(root, f))
    finally:
        if DB:
            drop_schema()
            rc, out = psql(f"SELECT count(*) FROM pg_namespace WHERE nspname = '{SCHEMA}'")
            print(f"\n[T08-RT] scratch schema dropped: {out.strip() == '0'}")

    print(f"\n[T08-RT] {time.perf_counter() - t0:.0f} s")
    for n in NOTES:
        print(f"  note: {n}")
    for s in SKIPPED:
        print(f"  SKIP (out of scope): {s}")
    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\n[ALL PASS] T08 round trip checks satisfied")


main()
