"""Semantic / topological fingerprint of IR roots (CJ-03, plan section 7). No bpy.

Not bytes and not vertex order: feature counts, surface classes, ring counts, the 1 mm coordinate
set, parent / host relations and the attribute-card view of each feature. Ids are the file's own
(``extra["cgml3_cityjson_id"]`` when the reader mapped one). Polygon ids are not compared: they are
minted differently per encoding. A polygon is counted once even when a Solid repeats it inline.
"""
from collections import Counter

from shared.builder_payload import importer_properties

# bookkeeping, not data: which LoDs were Solids in the source, the ids the reader minted
# plus xAL detail only the GML writer adds (street / locality type)
_SKIP_PROPS = ("cgml_source_solid_lods", "cgml3_cityjson_surface_ids", "cgml3_cityjson_lods",
               "bldg:address:streetType",
               "bldg:address:localityType")


# 3.0 con:WindowSurface/DoorSurface and the older Window/Door spelling are the same opening
_SAME_TYPE = {"Window": "WindowSurface", "Door": "DoorSurface"}


def _mm(p):
    return tuple(round(c * 1000) for c in p[:3])


def _poly_key(poly):
    return frozenset(_mm(p) for r in (poly.exterior, *poly.interiors) for p in r.coords)


def fingerprint(roots, attributes=True, original_ids=True) -> dict:
    """``original_ids=False`` compares the mapped gml:ids (the GML side never sees the originals)."""
    def _id(node):
        return str(original_ids and node.extra.get("cgml3_cityjson_id") or node.gml_id)

    skip = _SKIP_PROPS + (() if original_ids else ("cgml3_cityjson_id",))
    feats, surfs, polys_by_class, rel, props = Counter(), Counter(), {}, set(), {}
    coords = set()

    def geoms(node, cls):
        for g in node.geometries.values():
            for p in g.polygons:
                k = _poly_key(p)
                if not polys_by_class.get(k, ("",))[0]:   # a Solid's copy must not hide the class
                    polys_by_class[k] = (cls, 1 + len(p.interiors))
                coords.update(_mm(c) for r in (p.exterior, *p.interiors) for c in r.coords)

    def visit(node, parent):
        if hasattr(node, "surface_type"):   # duck-typed: headless loads shared twice
            surfs[node.surface_type] += 1
            rel.add((_id(node), _id(parent), "boundary"))
            geoms(node, node.surface_type)
            for o in node.openings:
                visit(o, node)
            return
        ftype = _SAME_TYPE.get(node.feature_type, node.feature_type)
        feats[ftype] += 1
        kind = "opening" if ftype in ("WindowSurface", "DoorSurface") else "child"
        if parent is not None:
            rel.add((_id(node), _id(parent), kind))
        geoms(node, ftype if kind == "opening" else "")
        if attributes and kind == "child":
            p = importer_properties(node, schema="3.0")
            props[_id(node)] = sorted((k, str(v)) for k, v in p.items() if k not in skip)
        for b in node.boundaries:
            visit(b, node)
        for s in node.spaces:
            visit(s, node)

    for r in roots:
        visit(r, None)
    rings = Counter()
    for cls, n in polys_by_class.values():
        rings[f"{cls}:polygons"] += 1
        rings[f"{cls}:rings"] += n
    return {"features": dict(feats), "surfaces": dict(surfs), "rings": dict(rings),
            "coords": coords, "relations": rel, "props": props}


def _within_1mm(only_a, only_b) -> bool:
    """A value on a rounding boundary may land 1 mm apart after a text round trip."""
    return len(only_a) == len(only_b) and all(
        any(max(abs(x - y) for x, y in zip(p, q)) <= 1 for q in only_b) for p in only_a)


def diff(a: dict, b: dict) -> dict:
    """Keys whose values differ, with a short description (empty = same)."""
    out = {}
    for k in a:
        if a[k] == b[k]:
            continue
        if k == "coords" and _within_1mm(a[k] - b[k], b[k] - a[k]):
            continue
        if isinstance(a[k], set):
            out[k] = {"only_a": sorted(a[k] - b[k])[:8], "only_b": sorted(b[k] - a[k])[:8],
                      "n_a": len(a[k]), "n_b": len(b[k])}
        elif isinstance(a[k], dict):
            out[k] = {x: (a[k].get(x), b[k].get(x)) for x in set(a[k]) | set(b[k]) if a[k].get(x) != b[k].get(x)}
        else:
            out[k] = (a[k], b[k])
    return out
