# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/proxy_geometry.py
"""Flat arrays of the single preview mesh (T08, pure Python, no bpy).

One mesh holds every feature as flat n-gons at the bbox minimum Z (a 2D
map, B14): the real LoD0 exterior rings, else the GroundSurface rings
(footprint), else the bbox rectangle. Each face carries the index of its
feature and a load state. The arrays are ``array`` buffers that ``Mesh.foreach_set`` accepts.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import Collection, Dict, Iterable, Optional, Sequence, Tuple

from .gml_index import FeatureIndexEntry

STATE_INDEXED = 0
STATE_LOADING = 1
STATE_LOADED = 2
STATE_ERROR = 3
STATE_RISK = 4  # indexed, with a risk flag (drawn in another colour)

PREVIEW_BBOX = "Bounding box"
PREVIEW_LOD0 = "LoD0"
PREVIEW_FOOTPRINT = "Footprint (ground surface)"  # same text as the 3DCityDB preview


@dataclass
class ProxyArrays:
    co: array = field(default_factory=lambda: array("f"))
    loop_vertex: array = field(default_factory=lambda: array("i"))
    loop_start: array = field(default_factory=lambda: array("i"))
    loop_total: array = field(default_factory=lambda: array("i"))
    face_feature: array = field(default_factory=lambda: array("i"))
    face_state: array = field(default_factory=lambda: array("i"))
    feature_count: int = 0
    lod0_features: int = 0

    @property
    def vertex_count(self) -> int:
        return len(self.co) // 3

    @property
    def face_count(self) -> int:
        return len(self.loop_start)

    @property
    def loop_count(self) -> int:
        return len(self.loop_vertex)

    def extend(self, other: "ProxyArrays") -> None:
        """Append a batch built with this instance's counts as offsets."""
        self.co.extend(other.co)
        self.loop_vertex.extend(other.loop_vertex)
        self.loop_start.extend(other.loop_start)
        self.loop_total.extend(other.loop_total)
        self.face_feature.extend(other.face_feature)
        self.face_state.extend(other.face_state)
        self.feature_count += other.feature_count
        self.lod0_features += other.lod0_features


def preview_label(entries: Iterable[FeatureIndexEntry]) -> str:
    """'LoD0' only for real lod0 geometry, 'Footprint (ground surface)', else 'Bounding box'."""
    entries = [e for e in entries if e.bbox]
    real = sum(1 for e in entries if e.lod0)
    foot = sum(1 for e in entries if e.footprint and not e.lod0)
    parts = [label for label, n in ((PREVIEW_LOD0, real), (PREVIEW_FOOTPRINT, foot)) if n]
    if real + foot < len(entries) or not parts:
        parts.append(PREVIEW_BBOX)
    return " + ".join(parts)


def _ring_points(flat: Sequence[float]) -> list:
    pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def build_proxy_arrays(
    entries: Sequence[FeatureIndexEntry],
    origin: Sequence[float],
    *,
    skip: Collection[int] = (),
    states: Optional[Dict[int, int]] = None,
    use_lod0: bool = True,
    first_feature: int = 0,
    vertex_offset: int = 0,
    loop_offset: int = 0,
) -> ProxyArrays:
    """Arrays for ``entries`` in local coordinates (``world - origin``).

    ``skip`` holds feature indices not drawn. ``states`` overrides the state
    of single features. ``first_feature`` / ``vertex_offset`` / ``loop_offset``
    let a batch be appended to arrays that already hold earlier batches.
    """
    ox, oy, oz = float(origin[0]), float(origin[1]), float(origin[2]) if len(origin) > 2 else 0.0
    out = ProxyArrays()
    co, lv, ls, lt = out.co, out.loop_vertex, out.loop_start, out.loop_total
    ff, fs = out.face_feature, out.face_state
    skip = set(skip)
    states = states or {}
    nv = vertex_offset
    nl = loop_offset
    for fi, e in enumerate(entries, start=first_feature):
        if fi in skip or not e.bbox:
            continue
        state = states.get(fi, STATE_RISK if e.risky else STATE_INDEXED)
        x0, y0, z0, x1, y1, z1 = e.bbox
        x0 -= ox; x1 -= ox; y0 -= oy; y1 -= oy; z0 -= oz; z1 -= oz
        src = e.lod0 if (use_lod0 and e.lod0) else e.footprint
        rings = [r for r in (_ring_points(r) for r in src) if len(r) >= 3]
        if rings and src is e.lod0:
            out.lod0_features += 1
        if not rings:  # flat bbox rectangle
            rings = [[(x0 + ox, y0 + oy), (x1 + ox, y0 + oy), (x1 + ox, y1 + oy), (x0 + ox, y1 + oy)]]
        for ring in rings:
            start = nv
            for x, y in ring:
                co.extend((x - ox, y - oy, z0))
            nv += len(ring)
            ls.append(nl)
            lt.append(len(ring))
            lv.extend(range(start, start + len(ring)))
            nl += len(ring)
            ff.append(fi)
            fs.append(state)
        out.feature_count += 1
    return out


def outline_edges(arrays: ProxyArrays, features: Collection[int]) -> list:
    """Edges of every face of ``features`` as local point pairs (selection / risk outline)."""
    co, ls, lt, lv, ff = arrays.co, arrays.loop_start, arrays.loop_total, arrays.loop_vertex, arrays.face_feature
    pts = []
    for f in range(len(ls)):
        if ff[f] not in features:
            continue
        s, n = ls[f], lt[f]
        for k in range(n):
            for vi in (lv[s + k], lv[s + (k + 1) % n]):
                pts.append((co[3 * vi], co[3 * vi + 1], co[3 * vi + 2]))
    return pts


def box_edges(bbox: Sequence[float], origin: Sequence[float]) -> list:
    """12 edges of a bbox as 24 local points (selection outline)."""
    ox, oy, oz = origin[0], origin[1], origin[2] if len(origin) > 2 else 0.0
    x0, y0, z0, x1, y1, z1 = bbox
    c = [(x0 - ox, y0 - oy, z0 - oz), (x1 - ox, y0 - oy, z0 - oz), (x1 - ox, y1 - oy, z0 - oz),
         (x0 - ox, y1 - oy, z0 - oz), (x0 - ox, y0 - oy, z1 - oz), (x1 - ox, y0 - oy, z1 - oz),
         (x1 - ox, y1 - oy, z1 - oz), (x0 - ox, y1 - oy, z1 - oz)]
    pairs = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
             (0, 4), (1, 5), (2, 6), (3, 7))
    pts = []
    for a, b in pairs:
        pts.append(c[a])
        pts.append(c[b])
    return pts


def bounds_center(bbox: Sequence[float]) -> Tuple[float, float, float]:
    return ((bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0, (bbox[2] + bbox[5]) / 2.0)
