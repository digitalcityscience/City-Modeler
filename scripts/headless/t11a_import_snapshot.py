"""T11a baseline: import a few GML files and write a scene summary as JSON.

Run once before and once after a change that must not alter the importer's
output, then compare the two files (``--compare a.json b.json``). Per file
the summary holds the object count per type, every object's custom property
keys, the stored authoring records (``_cgml_record``) and mesh totals.

Usage (isolated profile, see tickets/README.md):
  blender -b --python-exit-code 1 --python scripts/headless/t11a_import_snapshot.py -- \
      <out.json> <file.gml> [<file.gml> ...]
  python scripts/headless/t11a_import_snapshot.py --compare <before.json> <after.json>
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def compare(before_path, after_path):
    with open(before_path, encoding="utf-8") as f:
        before = json.load(f)
    with open(after_path, encoding="utf-8") as f:
        after = json.load(f)
    problems, detail_problems = [], []
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name), after.get(name)
        if a != b:
            keys = sorted(k for k in set(a or {}) | set(b or {})
                          if k != "detail" and (a or {}).get(k) != (b or {}).get(k))
            if keys:
                problems.append(f"{name}: differs in {keys}")
        da, db = (a or {}).get("detail"), (b or {}).get("detail")
        if da is not None and db is not None and da != db:
            detail_problems.extend(f"{name}: {line}" for line in detail_diff(da, db))
    for p in problems:
        print("  [FAIL]", p)
    print(f"{len(before)} files compared, {len(problems)} differ")
    if "--detail" in sys.argv or detail_problems:
        limit = None if "--all" in sys.argv else 60
        for p in detail_problems[:limit]:
            print("  [DETAIL]", p)
        print(f"detail: {len(detail_problems)} object-level differences")
    return not problems


def detail_diff(da, db):
    """Object-level differences: parent, collections, mesh sizes, property values, materials."""
    out = []
    for obj in sorted(set(da) | set(db)):
        a, b = da.get(obj), db.get(obj)
        if a is None or b is None:
            out.append(f"{obj}: only in {'before' if b is None else 'after'}")
            continue
        for k in sorted(set(a) | set(b)):
            if a.get(k) == b.get(k):
                continue
            if k in ("props", "mesh_props"):
                pa, pb = a.get(k) or {}, b.get(k) or {}
                for pk in sorted(set(pa) | set(pb)):
                    if pa.get(pk) != pb.get(pk):
                        out.append(f"{obj}: {k}[{pk}] {str(pa.get(pk))[:60]!r} -> {str(pb.get(pk))[:60]!r}")
            else:
                out.append(f"{obj}: {k} {str(a.get(k))[:80]!r} -> {str(b.get(k))[:80]!r}")
    return out


def _plain(value):
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "to_list"):
        value = value.to_list()
    return json.loads(json.dumps(value, default=str))


def object_detail(obj):
    """Everything an import is expected to reproduce, per object (values included)."""
    d = {
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else "",
        "collections": sorted(c.name for c in obj.users_collection),
        "props": {k: _plain(obj[k]) for k in obj.keys() if k != "_cgml_record"},
    }
    if obj.type == 'MESH':
        me = obj.data
        d["verts"], d["polys"], d["loops"] = len(me.vertices), len(me.polygons), len(me.loops)
        d["mesh_props"] = {k: _plain(me[k]) for k in me.keys()}
        d["uv_layers"] = sorted(u.name for u in me.uv_layers)
        d["materials"] = [
            {"name": m.name if m else "", "props": {k: _plain(m[k]) for k in m.keys()} if m else {}}
            for m in me.materials]
        d["poly_material"] = [p.material_index for p in me.polygons]
        d["bbox"] = [round(c, 3) for c in (obj.matrix_world @ me.vertices[0].co)] if me.vertices else []
    return d


def snapshot(path):
    import bpy
    from addon_test_utils import ensure_addon_enabled

    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False
    try:
        res = bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
    except RuntimeError as exc:
        # a file with nothing the add-on imports (Tunnel, WaterBody, ...) is refused: empty scene
        assert "does not import" in str(exc), exc
        res = {'FINISHED'}
    assert res == {'FINISHED'}, res

    counts, props, records = {}, {}, {}
    verts = polys = 0
    for obj in bpy.data.objects:
        counts[obj.type] = counts.get(obj.type, 0) + 1
        props[obj.name] = sorted(obj.keys())
        if "_cgml_record" in obj.keys():
            records[obj.name] = str(obj["_cgml_record"])
        if obj.type == 'MESH':
            verts += len(obj.data.vertices)
            polys += len(obj.data.polygons)
    return normalize_generated_ids({
        "objects": counts,
        "collections": len(bpy.data.collections),
        "materials": len(bpy.data.materials),
        "mesh_vertices": verts,
        "mesh_polygons": polys,
        "custom_property_keys": props,
        "records": records,
        "detail": {obj.name: object_detail(obj) for obj in bpy.data.objects},
    }, list(bpy.data.objects))


GENERATED_ID = re.compile(r"feat_\d+")
# the semantic split gives opening prisms ids made of a fresh uuid on every import
RANDOM_ID = re.compile(r"ID_[0-9a-f]{32}")


def _signature(obj):
    me = obj.data if obj.type == 'MESH' else None
    first = tuple(round(c, 3) for c in obj.matrix_world @ me.vertices[0].co) if me and me.vertices else ()
    return (str(obj.get("cgml3_feature", "")), len(me.polygons) if me else 0,
            len(me.vertices) if me else 0, first, len(obj.children))


def normalize_generated_ids(summary, objects):
    """Importers name id-less features ``feat_<hash of the XML>``, which changes from run to run.
    Number them by what the object holds (type, mesh size, first vertex) so two runs can be compared."""
    tokens = {}
    for obj in objects:
        # the object may carry the token only in gml_id (it was renamed after its gml:name)
        for token in GENERATED_ID.findall(obj.name) + GENERATED_ID.findall(str(obj.get("gml_id", ""))):
            tokens.setdefault(token, _signature(obj))
    order = {t: f"feat_{i}" for i, t in enumerate(sorted(tokens, key=lambda t: (tokens[t], t)))}
    text = GENERATED_ID.sub(lambda m: order.get(m.group(0), m.group(0)), json.dumps(summary, sort_keys=True))
    return json.loads(RANDOM_ID.sub("ID_random", text))


def main():
    a = args()
    if a and a[0] == "--compare":
        sys.exit(0 if compare(a[1], a[2]) else 1)
    out, files = a[0], a[1:]
    result = {}
    for path in files:
        result[os.path.basename(path)] = snapshot(path)
        s = result[os.path.basename(path)]
        print(f"  {os.path.basename(path)}: {s['objects']}, {s['mesh_polygons']} polygons, "
              f"{len(s['records'])} records")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, sort_keys=True)
    print("written", out)


if __name__ == "__main__":
    main()
