"""CAC-P1.4: benchmark record persistence options (input for ADR 0009).

Not a test (the suite runner only picks test_cac_*.py). One option per
Blender process:

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/bench_cac_persistence.py -- <out_dir> <option> [copies]

option: none | a_collection | b_json | c_array_json
  none          baseline: same scene, no records
  a_collection  typed PropertyGroups; coordinates as a CollectionProperty of float3
  b_json        one JSON string ID property per object (feature + polygons + coords)
  c_array_json  coordinates in one float-array ID property per object, the rest JSON

Scene: Railway LoD3 CityGML 3 imported with use_semantic_objects, all
objects duplicated to `copies` buildings (default 50). Every object with a
gml_id stores its FeatureRecord and its PolygonRecords in the committed
state (world coordinates of the faces using each polygon/ring material).

Writes <out_dir>/bench_<option>.json.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

FIXTURE = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


# --------------------------------------------------------------------------
# scene + records
# --------------------------------------------------------------------------

def build_scene(copies):
    import bpy

    h.import_fixture(FIXTURE, "3.0", use_semantic_objects=True)
    originals = list(bpy.data.objects)
    collection = bpy.context.scene.collection
    for n in range(1, copies):
        clone = {}
        for obj in originals:
            new = obj.copy()           # shares mesh data, like Alt+D
            new.location.x += 40.0 * n
            collection.objects.link(new)
            clone[obj] = new
        for obj, new in clone.items():
            if obj.parent in clone:
                new.parent = clone[obj.parent]
    bpy.context.view_layer.update()


def records_per_object():
    """{object: (feature dict, [polygon dicts])} with committed coordinates."""
    import bpy
    from dataclasses import replace
    from city_modeler.shared.authoring_core.blender.legacy_views import object_view
    from city_modeler.shared.authoring_core.domain import records as r
    from city_modeler.shared.authoring_core.domain.legacy_reader import read_legacy_scene

    out = {}
    for obj in bpy.data.objects:
        if not obj.get("gml_id") or obj.type != "MESH":
            continue
        scene = read_legacy_scene([object_view(o) for o in (obj, obj.parent) if o is not None])
        feature = scene.records.feature(str(obj["gml_id"]))
        mw = obj.matrix_world
        mesh = obj.data
        face_by_key = {}
        for poly in mesh.polygons:
            mat = mesh.materials[poly.material_index] if poly.material_index < len(mesh.materials) else None
            if mat is None:
                continue
            key = str(mat.get("gml_ring_id") or mat.get("gml_polygon_id") or "")
            face_by_key.setdefault(key, poly)

        def coords(ring_id):
            face = face_by_key.get(ring_id)
            if face is None:
                return ()
            return tuple(tuple(round(c, 6) for c in (mw @ mesh.vertices[v].co)) for v in face.vertices)

        polygons = []
        for p in scene.records.polygons_of(feature.gml_id):
            ext = replace(p.exterior, coords=coords(p.exterior.ring_id))
            ints = tuple(replace(i, coords=coords(i.ring_id)) for i in p.interiors)
            polygons.append(r.polygon_to_dict(replace(p, exterior=ext, interiors=ints,
                                                      geometry_state="committed")))
        out[obj] = (r.feature_to_dict(feature), polygons)
    return out


# --------------------------------------------------------------------------
# option a: typed PropertyGroups
# --------------------------------------------------------------------------

def register_a():
    import bpy
    from bpy.props import (BoolProperty, CollectionProperty, FloatVectorProperty, IntProperty,
                           PointerProperty, StringProperty)

    class BenchCoordPG(bpy.types.PropertyGroup):
        co: FloatVectorProperty(size=3)

    class BenchRingPG(bpy.types.PropertyGroup):
        ring_id: StringProperty()
        role: StringProperty()
        opening_link: StringProperty()
        coords: CollectionProperty(type=BenchCoordPG)

    class BenchPolygonPG(bpy.types.PropertyGroup):
        polygon_id: StringProperty()
        feature_id: StringProperty()
        lod: IntProperty(default=-1)
        exterior: PointerProperty(type=BenchRingPG)
        interiors: CollectionProperty(type=BenchRingPG)
        multisurface_id: StringProperty()
        is_multisurface_member: BoolProperty()
        geometry_state: StringProperty()

    class BenchFeaturePG(bpy.types.PropertyGroup):
        gml_id: StringProperty()
        kind: StringProperty()
        semantic_type: StringProperty()
        parent_id: StringProperty()
        owner_id: StringProperty()
        host_id: StringProperty()
        opening_link: StringProperty()
        lod: IntProperty(default=-1)
        lod_origin: StringProperty()
        provenance: StringProperty()
        params_json: StringProperty()
        polygons: CollectionProperty(type=BenchPolygonPG)

    for cls in (BenchCoordPG, BenchRingPG, BenchPolygonPG, BenchFeaturePG):
        bpy.utils.register_class(cls)
    bpy.types.Object.cac_bench = PointerProperty(type=BenchFeaturePG)


def _ring_to_pg(pg, ring):
    pg.ring_id, pg.role, pg.opening_link = ring["ring_id"], ring["role"], ring["opening_link"]
    for c in ring["coords"]:
        pg.coords.add().co = c


def _ring_from_pg(pg):
    return {"ring_id": pg.ring_id, "role": pg.role, "opening_link": pg.opening_link,
            "coords": [tuple(c.co) for c in pg.coords]}


def write_a(data):
    for obj, (feature, polygons) in data.items():
        pg = obj.cac_bench
        for key in ("gml_id", "kind", "semantic_type", "parent_id", "owner_id", "host_id",
                    "opening_link", "lod_origin", "provenance"):
            setattr(pg, key, feature[key])
        pg.lod = -1 if feature["lod"] is None else feature["lod"]
        pg.params_json = json.dumps(feature["params"])
        pg.polygons.clear()
        for p in polygons:
            item = pg.polygons.add()
            item.polygon_id, item.feature_id = p["polygon_id"], p["feature_id"]
            item.lod = -1 if p["lod"] is None else p["lod"]
            item.multisurface_id, item.is_multisurface_member = p["multisurface_id"], p["is_multisurface_member"]
            item.geometry_state = p["geometry_state"]
            _ring_to_pg(item.exterior, p["exterior"])
            for ring in p["interiors"]:
                _ring_to_pg(item.interiors.add(), ring)


def read_a(objects):
    out = 0
    for obj in objects:
        pg = obj.cac_bench
        if not pg.gml_id:
            continue
        polys = [{"polygon_id": p.polygon_id, "exterior": _ring_from_pg(p.exterior),
                  "interiors": [_ring_from_pg(i) for i in p.interiors]} for p in pg.polygons]
        out += len(polys)
    return out


# --------------------------------------------------------------------------
# option b: JSON string; option c: float array + JSON
# --------------------------------------------------------------------------

def write_b(data):
    for obj, (feature, polygons) in data.items():
        obj["cac_bench_json"] = json.dumps({"feature": feature, "polygons": polygons}, separators=(",", ":"))


def read_b(objects):
    from city_modeler.shared.authoring_core.domain import records as r

    out = 0
    for obj in objects:
        raw = obj.get("cac_bench_json")
        if raw:
            d = json.loads(raw)
            r.feature_from_dict(d["feature"])
            out += len([r.polygon_from_dict(p) for p in d["polygons"]])
    return out


def write_c(data):
    for obj, (feature, polygons) in data.items():
        flat, stripped = [], []
        for p in polygons:
            q = json.loads(json.dumps(p))
            for ring in [q["exterior"], *q["interiors"]]:
                ring["coords_at"] = [len(flat) // 3, len(ring["coords"])]
                flat.extend(c for pt in ring["coords"] for c in pt)
                ring["coords"] = []
            stripped.append(q)
        obj["cac_bench_json"] = json.dumps({"feature": feature, "polygons": stripped}, separators=(",", ":"))
        obj["cac_bench_coords"] = flat if flat else [0.0]


def read_c(objects):
    from city_modeler.shared.authoring_core.domain import records as r

    out = 0
    for obj in objects:
        raw = obj.get("cac_bench_json")
        if not raw:
            continue
        d = json.loads(raw)
        flat = obj["cac_bench_coords"].to_list()
        for p in d["polygons"]:
            for ring in [p["exterior"], *p["interiors"]]:
                start, n = ring.pop("coords_at")
                ring["coords"] = [flat[3 * i:3 * i + 3] for i in range(start, start + n)]
            r.polygon_from_dict(p)
            out += 1
    return out


OPTIONS = {
    "none": (None, None),
    "a_collection": (write_a, read_a),
    "b_json": (write_b, read_b),
    "c_array_json": (write_c, read_c),
}


def timed(fn, *args, **kwargs):
    t = time.perf_counter()
    result = fn(*args, **kwargs)
    return time.perf_counter() - t, result


def main():
    import bpy

    args = [a for a in h.script_args() if not a.startswith("--")]
    out, option = h.out_dir(), args[1]
    copies = int(args[2]) if len(args) > 2 else 50
    write, read = OPTIONS[option]
    if option == "a_collection":
        register_a()

    build_scene(copies)
    t_build, data = timed(records_per_object)
    n_polys = sum(len(p) for _, p in data.values())
    n_coords = sum(len(ring["coords"]) for _, ps in data.values() for p in ps
                   for ring in [p["exterior"], *p["interiors"]])
    result = {"option": option, "copies": copies, "objects": len(bpy.data.objects),
              "record_objects": len(data), "polygons": n_polys, "coordinates": n_coords,
              "prepare_s": round(t_build, 3)}

    bpy.ops.ed.undo_push(message="before write")
    if write:
        result["write_s"] = round(timed(write, data)[0], 3)
        result["read_s"], read_polys = timed(read, list(bpy.data.objects))
        result["read_s"] = round(result["read_s"], 3)
        result["read_polygons"] = read_polys

    # undo step cost after changing one object's record (or one prop for baseline)
    one = next(iter(data))
    samples = []
    for _ in range(5):
        if write:
            write({one: data[one]})
        else:
            one["cac_bench_touch"] = time.time()
        samples.append(timed(bpy.ops.ed.undo_push, message="edit one record")[0])
    result["undo_push_ms_median"] = round(sorted(samples)[2] * 1000, 2)

    path = out / f"bench_{option}.blend"
    result["save_s"] = round(timed(bpy.ops.wm.save_as_mainfile, filepath=str(path), compress=False)[0], 3)
    result["blend_mb"] = round(path.stat().st_size / 1e6, 2)
    result["open_s"] = round(timed(bpy.ops.wm.open_mainfile, filepath=str(path))[0], 3)
    if read and option != "a_collection":   # ID properties survive without registration
        result["read_after_reload_polygons"] = read(list(bpy.data.objects))
    elif option == "a_collection":
        result["read_after_reload_polygons"] = read(list(bpy.data.objects))

    h.write_json(out / f"bench_{option}.json", result)
    print("BENCH", json.dumps(result))
    sys.stdout.flush()
    os._exit(0)


main()
