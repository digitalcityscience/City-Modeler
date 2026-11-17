# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""B55: polygon planarity check and repair (CityGML / CityDoctor ``GE_P_NON_PLANAR``).

A polygon is planar when every vertex lies within ``tolerance`` of its best-fit
plane (Newell normal through the centroid) - CityDoctor's "distance" rule,
default 1 cm. Repair projects the vertices onto that plane; vertices shared by
neighbouring faces of the same mesh are re-projected for a few passes.
"""
from __future__ import annotations

import bmesh
from mathutils import Vector

DEFAULT_TOLERANCE_M = 0.01  # CityDoctor R_GE_P_NON_PLANAR distanceTolerance default
_REPAIR_PASSES = 20


def best_fit_plane(points):
    """``(centroid, unit normal)`` - Newell's method; normal is zero for a degenerate loop."""
    c = sum(points, Vector()) / len(points)
    n = Vector()
    for a, b in zip(points, points[1:] + points[:1]):
        n += Vector(((a.y - b.y) * (a.z + b.z), (a.z - b.z) * (a.x + b.x), (a.x - b.x) * (a.y + b.y)))
    return c, (n.normalized() if n.length > 0.0 else n)


def deviation(points) -> float:
    """Largest distance of a vertex from the best-fit plane (0 for triangles / degenerate loops)."""
    if len(points) < 4:
        return 0.0
    c, n = best_fit_plane(points)
    return max(abs((p - c).dot(n)) for p in points) if n.length else 0.0


def non_planar_faces(obj, tolerance=DEFAULT_TOLERANCE_M):
    """``[(face_index, deviation_m)]`` of ``obj``'s faces off-plane by more than ``tolerance`` (world space)."""
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    out = []
    for f in obj.data.polygons:
        d = deviation([verts[i] for i in f.vertices])
        if d > tolerance:
            out.append((f.index, d))
    return out


def flatten(objects, tolerance=DEFAULT_TOLERANCE_M) -> int:
    """Project the vertices of every non-planar face in ``objects`` onto its best-fit plane.
    A vertex sitting at the same place in another of ``objects`` (the neighbouring surface's
    corner) moves with it, so shared edges - and the closed shell - stay intact.
    Returns how many faces are still off-plane by more than ``tolerance`` afterwards."""
    bms, clusters = {}, {}
    for o in objects:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bms[o] = bm
        for v in bm.verts:
            key = tuple(round(c, 5) for c in o.matrix_world @ v.co)
            clusters.setdefault(key, []).append((o, v))
    cluster_of = {(o, v): members for members in clusters.values() for o, v in members}
    inv = {o: o.matrix_world.inverted() for o in objects}
    for _ in range(_REPAIR_PASSES):
        moved = False
        for o, bm in bms.items():
            mw = o.matrix_world
            for f in bm.faces:
                pts = [mw @ v.co for v in f.verts]
                if deviation(pts) <= tolerance * 1e-3:
                    continue
                c, n = best_fit_plane(pts)
                if not n.length:
                    continue
                for v, p in zip(f.verts, pts):
                    q = p - n * (p - c).dot(n)
                    for o2, v2 in cluster_of[(o, v)]:
                        v2.co = inv[o2] @ q
                moved = True
        if not moved:
            break
    for o, bm in bms.items():
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
    return sum(len(non_planar_faces(o, tolerance)) for o in objects)
