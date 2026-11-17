"""B61 (agent plan A6): repairs that run only when asked, and a self-intersection diagnosis.

- ``fragment_surfaces``: CityDoctor ``SE_BS_UNFRAGMENTED`` ("BoundarySurface unfragmented") says
  the polygons of one boundary surface point in directions more than ~1 degree apart (its
  ``angleDerivation`` is in radians). The surface is split into one surface per orientation
  group; every group keeps the surface type, the polygons and their ids, only the extra groups
  get a new surface gml_id. (Merging the polygons instead would bend them into one non-planar
  polygon.) Holes stay with the polygon they belong to.
- ``weld_t_junctions``: a vertex of one surface lying inside an edge of its neighbour leaves both
  edges used once (``GE_S_NOT_CLOSED``). The vertex is inserted into that edge.
- ``self_intersections``: polygons of the shell that touch or cross without sharing a corner
  (CityDoctor ``GE_S_SELF_INTERSECTION``). Diagnosis only: which two polygons; no repair.
"""
from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

DEFAULT_ANGLE_DEG = 1.0
_KEY_DIGITS = 4


def _mat(obj, poly):
    mats = obj.data.materials
    return mats[poly.material_index] if 0 <= poly.material_index < len(mats) else None


def _is_ring_face(obj, poly) -> bool:
    m = _mat(obj, poly)
    return bool(m and m.get("Interior"))


def _world_normal(obj, poly) -> Vector:
    n = obj.matrix_world.to_3x3().inverted().transposed() @ poly.normal
    return n.normalized() if n.length else n


def orientation_groups(obj, angle_deg=DEFAULT_ANGLE_DEG, faces=None):
    """Faces of ``obj`` (or only ``faces``) grouped so that the outer polygons of one group are
    pairwise within ``angle_deg``; each hole face goes with the polygon it belongs to
    (``ExteriorPolyId``)."""
    limit = math.radians(angle_deg)
    pool = obj.data.polygons if faces is None else [obj.data.polygons[i] for i in faces]
    polys = [p for p in pool if not _is_ring_face(obj, p)]
    polys.sort(key=lambda p: -p.area)
    groups = []  # [(normals, faces)]
    for p in polys:
        n = _world_normal(obj, p)
        for normals, faces in groups:
            if all(n.angle(m, 0.0) <= limit for m in normals):
                normals.append(n)
                faces.append(p.index)
                break
        else:
            groups.append(([n], [p.index]))
    group_of_polygon_id = {}
    for gi, (_n, faces) in enumerate(groups):
        for fi in faces:
            m = _mat(obj, obj.data.polygons[fi])
            if m is not None and m.get("gml_polygon_id"):
                group_of_polygon_id[str(m["gml_polygon_id"])] = gi
    out = [list(faces) for _n, faces in groups]
    for p in pool:
        if _is_ring_face(obj, p):
            m = _mat(obj, p)
            gi = group_of_polygon_id.get(str(m.get("ExteriorPolyId") or ""), 0) if out else None
            if gi is None:
                out.append([p.index])
            else:
                out[gi].append(p.index)
    return out


def unfragmented(objs, angle_deg=DEFAULT_ANGLE_DEG):
    """The surface objects among ``objs`` whose polygons point in more than one direction."""
    return [o for o in objs if o.type == 'MESH' and len(orientation_groups(o, angle_deg)) > 1]


def _material_surfaces(obj):
    """``{con_surface_id: [face index]}``: the surfaces an installation keeps on its materials (B40)."""
    out = {}
    for p in obj.data.polygons:
        m = _mat(obj, p)
        sid = str(m.get("con_surface_id") or "") if m is not None else ""
        if sid:
            out.setdefault(sid, []).append(p.index)
    return out


def unfragmented_material_surfaces(obj, angle_deg=DEFAULT_ANGLE_DEG):
    """``[(surface id, orientation groups)]`` of an installation's bent surfaces."""
    out = []
    for sid, faces in _material_surfaces(obj).items():
        groups = orientation_groups(obj, angle_deg, faces)
        if len(groups) > 1:
            out.append((sid, groups))
    return out


def fragment_material_surfaces(obj, angle_deg=DEFAULT_ANGLE_DEG):
    """Give every extra orientation group of a bent installation surface its own surface id
    (copied materials, same surface type). Returns the new surface ids."""
    from .authoring_core.domain.ids import mint_gml_id
    new_ids = []
    mesh = obj.data
    for _sid, groups in unfragmented_material_surfaces(obj, angle_deg):
        for faces in groups[1:]:
            gid, ms_id = mint_gml_id(), mint_gml_id()
            copies = {}
            for fi in faces:
                poly = mesh.polygons[fi]
                old = poly.material_index
                if old not in copies:
                    c = mesh.materials[old].copy()
                    c["con_surface_id"] = gid
                    if "gml_multisurface_id" in c:
                        c["gml_multisurface_id"] = ms_id
                    mesh.materials.append(c)
                    copies[old] = len(mesh.materials) - 1
                poly.material_index = copies[old]
            new_ids.append(gid)
    return new_ids


def fragment_surfaces(objs, angle_deg=DEFAULT_ANGLE_DEG):
    """Split each unfragmented surface object of ``objs`` (see module docstring).
    Returns ``[(original, [new objects])]``."""
    from ..common.builder_support.semantic_explode import _extract_face_subset
    from .authoring_core.domain.ids import mint_gml_id
    done = []
    for obj in unfragmented(objs, angle_deg):
        groups = orientation_groups(obj, angle_deg)
        keep, rest = groups[0], groups[1:]
        children = [c for c in obj.children]
        new_objs = []
        for faces in rest:
            gid = mint_gml_id()
            new = _extract_face_subset(obj, faces, f"{obj.get('cgml3_feature', 'Surface')}_{gid}")
            for k in obj.keys():  # surface attributes travel along, the identity is new
                if k not in ("gml_id", "cgml_rings"):
                    new[k] = obj[k]
            new["gml_id"] = gid
            ms_id = mint_gml_id()
            slots = []
            for m in new.data.materials:
                c = m.copy()
                c["con_surface_id"] = gid
                if "gml_multisurface_id" in c:
                    c["gml_multisurface_id"] = ms_id
                slots.append(c)
            for i, c in enumerate(slots):
                new.data.materials[i] = c
            for coll in obj.users_collection:
                coll.objects.link(new)
            new.parent = obj.parent
            new.matrix_world = obj.matrix_world.copy()
            new_objs.append((new, set(faces)))
        _delete_faces(obj, [f for faces in rest for f in faces])
        for child in children:  # openings go with the surface they sit on
            target = _host_of(child, [obj] + [n for n, _f in new_objs])
            if target is not None and target is not obj:
                mw = child.matrix_world.copy()
                child.parent = target
                child.matrix_world = mw
                for key in ("filling_parent_surface_id", "opening_surface_id", "cgml_parent_id"):
                    if key in child:
                        child[key] = target["gml_id"]
        done.append((obj, [n for n, _f in new_objs]))
    return done


def _delete_faces(obj, face_indices):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in face_indices], context='FACES')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _host_of(child, candidates):
    if child.type != 'MESH' or not child.data.vertices:
        return None
    c = sum((child.matrix_world @ v.co for v in child.data.vertices), Vector()) / len(child.data.vertices)
    best = None
    for o in candidates:
        for p in o.data.polygons:
            centre = o.matrix_world @ p.center
            n = _world_normal(o, p)
            d = abs((c - centre).dot(n))
            if best is None or d < best[0]:
                best = (d, o)
    return best[1] if best else None


# ------------------------------------------------------------------ T-junctions
def _key(v):
    return tuple(round(c, _KEY_DIGITS) for c in v)


def _edge_uses(objs):
    """``{frozenset(key a, key b): [(obj, vert index a, vert index b)]}`` over the face loops."""
    uses = {}
    for o in objs:
        mw = o.matrix_world
        keys = [_key(mw @ v.co) for v in o.data.vertices]
        for p in o.data.polygons:
            vs = list(p.vertices)
            for a, b in zip(vs, vs[1:] + vs[:1]):
                if keys[a] != keys[b]:
                    uses.setdefault(frozenset((keys[a], keys[b])), []).append((o, a, b))
    return uses


def t_junctions(objs, tol=1e-3):
    """``[(obj, vert a, vert b, [world points inside a-b])]``: open edges with a corner of another
    open edge lying inside them."""
    uses = _edge_uses(objs)
    open_edges = [(e, u[0]) for e, u in uses.items() if len(u) == 1]
    corners = {k for e, _u in open_edges for k in e}
    out = []
    for e, (o, a, b) in open_edges:
        pa, pb = (Vector(k) for k in sorted(e))
        ab = pb - pa
        ll = ab.length_squared
        if ll < 1e-12:
            continue
        inside = []
        for k in corners:
            if k in e:
                continue
            p = Vector(k)
            t = (p - pa).dot(ab) / ll
            if 1e-6 < t < 1 - 1e-6 and (pa + ab * t - p).length <= tol:
                inside.append(p)
        if inside:
            out.append((o, a, b, inside))
    return out


def weld_t_junctions(objs, tol=1e-3, passes=5) -> int:
    """Insert each T-junction corner into the edge it lies on. Returns how many were inserted."""
    total = 0
    for _ in range(passes):
        found = t_junctions(objs, tol)
        if not found:
            break
        by_obj = {}
        for o, a, b, pts in found:
            by_obj.setdefault(o, []).append((a, b, pts))
        for o, items in by_obj.items():
            inv = o.matrix_world.inverted()
            bm = bmesh.new()
            bm.from_mesh(o.data)
            bm.verts.ensure_lookup_table()
            for a, b, pts in items:
                va, vb = bm.verts[a], bm.verts[b]
                edge = bm.edges.get((va, vb))
                if edge is None:
                    continue
                start = va.co.copy()
                # split from the far end so earlier splits keep the remaining edge a-b
                for p in sorted((inv @ q for q in pts), key=lambda q: -(q - start).length):
                    if edge is None or not edge.is_valid:
                        break
                    e_new, v_new = bmesh.utils.edge_split(edge, va, 0.5)
                    v_new.co = p
                    total += 1
                    edge = bm.edges.get((va, v_new))
            bm.to_mesh(o.data)
            bm.free()
            o.data.update()
    return total


# ------------------------------------------------------------------ self-intersection (diagnosis)
SHRINK_M = 0.002  # neighbours sharing an edge stop touching; a real crossing stays


def self_intersections(objs, shrink=SHRINK_M):
    """Pairs of shell polygons that touch or cross each other beyond a shared edge or corner:
    ``[{"a": {object, surface, face, polygon_id}, "b": {...}}]``. Every polygon is shrunk by
    ``shrink`` towards its centre first, so neighbours meeting along an edge (or at a T-junction)
    are not reported, while two polygons that cross, fold through or overlap each other are."""
    verts, polys, owner = [], [], []
    for o in objs:
        if o.type != 'MESH':
            continue
        mw = o.matrix_world
        world = [mw @ v.co for v in o.data.vertices]
        for p in o.data.polygons:
            if _is_ring_face(o, p):
                continue  # a hole is the absence of surface, not a polygon of the shell
            pts = [world[i] for i in p.vertices]
            if len(pts) < 3:
                continue
            c = sum(pts, Vector()) / len(pts)
            start = len(verts)
            for q in pts:
                d = c - q
                verts.append(q + d * min(1.0, shrink / d.length) if d.length > 1e-12 else q)
            polys.append(list(range(start, start + len(pts))))
            owner.append((o, p.index))
    if not polys:
        return []
    tree = BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0)
    out, seen = [], set()
    for i, j in tree.overlap(tree):
        if i == j or (min(i, j), max(i, j)) in seen:
            continue
        seen.add((min(i, j), max(i, j)))
        pair = []
        for k in (i, j):
            o, fi = owner[k]
            m = _mat(o, o.data.polygons[fi])
            pair.append({"object": o.name, "surface": str(o.get("gml_id") or ""), "face": fi,
                         "polygon_id": str(m.get("gml_polygon_id") or "") if m else ""})
        out.append({"a": pair[0], "b": pair[1]})
    return out
