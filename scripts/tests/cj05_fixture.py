"""CJ-05: synthetic CityJSON buildings for the Browse-first tests (pure Python; no big file in the repo).

``n`` box buildings on a 30 m grid, EPSG:25832, vertices in integer millimetres:

- every building: LoD "1.2" Solid (no semantics) and LoD "2.2" Solid (Ground / Roof / 4 Wall surfaces);
  building 3, 13, 23, ... has only LoD "1.2";
- every 7th: a ``BuildingPart`` child (LoD "2.2" MultiSurface with semantics);
- every 11th: a ``BuildingInstallation`` child (LoD "2.2" MultiSurface, no semantics);
- building 5: a ``BuildingRoom`` child (out of scope);
- building 1 is called "B.001" (not a valid gml:id: the reader maps it);
- two root ``WaterBody`` objects (out of scope).

The same buildings as one CityJSON document (:func:`document`) or as CityJSONSeq lines (:func:`seq_lines`).
"""
import copy
import json

SCALE = [0.001, 0.001, 0.001]
TRANSLATE = [564000.0, 5934000.0, 0.0]
CRS = "https://www.opengis.net/def/crs/EPSG/0/25832"
GRID = 20           # buildings per row
STEP = 30000        # mm
BASE_Z = 10000
PART_DX = 4000
UNUSED_VERTICES = 5  # at the start of the document's vertex list: nothing refers to them


def oid(i):
    return "B.001" if i == 1 else f"B{i:03d}"


def has_lod22(i):
    return i % 10 != 3


def has_part(i):
    return i % 7 == 0


def has_installation(i):
    return i % 11 == 0


def size(i):
    return 10000, 8000, 6000 + 1000 * (i % 5)


def origin_mm(i):
    return (i % GRID) * STEP, (i // GRID) * STEP, BASE_Z


def bbox(i):
    """World bbox of building ``i`` with its part and installation (the index entry's bbox)."""
    x, y, z = origin_mm(i)
    dx, dy, dz = size(i)
    x1 = x + dx + (PART_DX if has_part(i) else 0)
    z1 = z + dz + (1000 if has_installation(i) else 0)
    lo = [c * s + t for c, s, t in zip((x, y, z), SCALE, TRANSLATE)]
    hi = [c * s + t for c, s, t in zip((x1, y + dy, z1), SCALE, TRANSLATE)]
    return tuple(lo + hi)


def _box(vertices, x, y, z, dx, dy, dz):
    """Append the 8 corners; return the 6 faces (ground, roof, 4 walls), outward, as polygons."""
    k = len(vertices)
    vertices += [[x, y, z], [x + dx, y, z], [x + dx, y + dy, z], [x, y + dy, z],
                 [x, y, z + dz], [x + dx, y, z + dz], [x + dx, y + dy, z + dz], [x, y + dy, z + dz]]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return [[[k + v for v in f]] for f in faces]


def _semantics(solid):
    values = [0, 1, 2, 3, 4, 5]
    return {"surfaces": [{"type": "GroundSurface"}, {"type": "RoofSurface"}] + [{"type": "WallSurface"}] * 4,
            "values": [values] if solid else values}


def building(i):
    """``(id, CityObjects of the building, its own vertex list)``: indices are local, as in a feature line."""
    vertices, root_id = [], oid(i)
    x, y, z = origin_mm(i)
    dx, dy, dz = size(i)
    faces = _box(vertices, x, y, z, dx, dy, dz)
    geometry = [{"type": "Solid", "lod": "1.2", "boundaries": [faces]}]
    if has_lod22(i):
        geometry.append({"type": "Solid", "lod": "2.2", "boundaries": [faces], "semantics": _semantics(True)})
    root = {"type": "Building", "attributes": {"measuredHeight": dz / 1000.0, "function": "31001_1000"},
            "geometry": geometry}
    objects = {root_id: root}
    children = []
    if has_part(i):
        children.append(f"{root_id}-P")
        objects[children[-1]] = {"type": "BuildingPart", "parents": [root_id], "geometry": [{
            "type": "MultiSurface", "lod": "2.2", "boundaries": _box(vertices, x + dx, y, z, PART_DX, dy, 3000),
            "semantics": _semantics(False)}]}
    if has_installation(i):
        children.append(f"{root_id}-I")
        objects[children[-1]] = {"type": "BuildingInstallation", "parents": [root_id], "geometry": [{
            "type": "MultiSurface", "lod": "2.2",
            "boundaries": _box(vertices, x + 1000, y + 1000, z + dz, 1000, 1000, 1000)}]}
    if i == 5:
        children.append(f"{root_id}-R")
        objects[children[-1]] = {"type": "BuildingRoom", "parents": [root_id], "geometry": [{
            "type": "Solid", "lod": "2.2", "boundaries": [_box(vertices, x + 500, y + 500, z, 2000, 2000, 2500)]}]}
    if children:
        root["children"] = children
    return root_id, objects, vertices


def _water(k):
    vertices = [[-50000, k * 5000, 9000], [-40000, k * 5000, 9000], [-40000, k * 5000 + 4000, 9000],
                [-50000, k * 5000 + 4000, 9000]]
    return f"W{k}", {f"W{k}": {"type": "WaterBody", "geometry": [
        {"type": "MultiSurface", "lod": "1", "boundaries": [[[0, 1, 2, 3]]]}]}}, vertices


def features(n):
    return [building(i) for i in range(n)] + [_water(1), _water(2)]


def extent(n):
    boxes = [bbox(i) for i in range(n)]
    return [min(b[k] for b in boxes) for k in range(3)] + [max(b[k + 3] for b in boxes) for k in range(3)]


def header(n, with_extent=True):
    meta = {"referenceSystem": CRS}
    if with_extent:
        meta["geographicalExtent"] = extent(n)
    return {"type": "CityJSON", "version": "2.0", "transform": {"scale": SCALE, "translate": TRANSLATE},
            "metadata": meta, "CityObjects": {}, "vertices": []}


def _shift(b, k):
    return [_shift(x, k) for x in b] if isinstance(b, list) else b + k


def document(n, with_extent=True):
    doc = header(n, with_extent)
    doc["vertices"] = [[0, 0, 0] for _ in range(UNUSED_VERTICES)]
    for _root, objects, vertices in features(n):
        k = len(doc["vertices"])
        for object_id, o in objects.items():
            doc["CityObjects"][object_id] = dict(
                o, geometry=[dict(g, boundaries=_shift(g["boundaries"], k)) for g in o["geometry"]])
        doc["vertices"] += vertices
    return doc


def seq_lines(n, with_extent=True):
    lines = [json.dumps(header(n, with_extent), separators=(",", ":"))]
    lines += [json.dumps({"type": "CityJSONFeature", "id": root, "CityObjects": objects, "vertices": vertices},
                         separators=(",", ":")) for root, objects, vertices in features(n)]
    return [line.encode("utf-8") for line in lines]


def write_document(path, n, with_extent=True, indent=1):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document(n, with_extent), f, indent=indent)
    return str(path)


def write_seq(path, n, with_extent=True, newline=b"\n", extra=None):
    """``extra``: ``{line number: raw bytes}`` put in after that line (blank or broken lines)."""
    extra = extra or {}
    with open(path, "wb") as f:
        for k, line in enumerate(seq_lines(n, with_extent)):
            f.write(line + newline)
            if k in extra:
                f.write(extra[k] + newline)
    return str(path)


# -- comparison helpers (CJ-06 tests) ------------------------------------------------------------

def subtree(objs, rid):
    out, stack = [], [rid]
    while stack:
        oid = stack.pop(0)
        if oid in objs:
            out.append(oid)
            stack += objs[oid].get("children") or []
    return out


def _flat(b, out):
    for x in b:
        if isinstance(x, list):
            _flat(x, out)
        else:
            out.append(x)


def deref(doc, o):
    """The object with vertex indices replaced by world coordinates (mm)."""
    s, t, vs = doc["transform"]["scale"], doc["transform"]["translate"], doc["vertices"]

    def f(b):
        return [f(x) if isinstance(x, list) else tuple(round((vs[x][k] * s[k] + t[k]) * 1000) for k in range(3))
                for x in b]
    o = copy.deepcopy(o)
    for g in o.get("geometry") or ():
        g["boundaries"] = f(g["boundaries"])
    for a in o.get("address") or ():
        if isinstance(a.get("location"), dict):
            a["location"]["boundaries"] = f(a["location"]["boundaries"])
    return o


def same_objects(a, b, ids):
    return [i for i in ids if deref(a, a["CityObjects"][i]) != deref(b, b["CityObjects"][i])]


def world_points(doc, o, lod):
    s, t, vs = doc["transform"]["scale"], doc["transform"]["translate"], doc["vertices"]
    out = set()
    for g in o["geometry"]:
        if g["lod"] == lod:
            idx = []
            _flat(g["boundaries"], idx)
            out |= {tuple(round((vs[i][k] * s[k] + t[k]) * 1000) for k in range(3)) for i in idx}
    return out
