"""B87: Cone / Dome / Tower roofs (AdV 3600 / 3700 / 4000) are closed solids with planar faces
on any convex footprint; the gallery offers every AdV roof type that has a shape."""
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from shared import roof_shapes  # noqa: E402  (pure Python, no bpy)

RECT = [(0, 0, 2.0), (12, 0, 2.0), (12, 8, 2.0), (0, 8, 2.0)]
ROUND = [(5 * math.cos(2 * math.pi * k / 16), 5 * math.sin(2 * math.pi * k / 16), 2.0) for k in range(16)]
SKEWED = [(0, 0, 2.0), (10, 0, 2.0), (12, 7, 2.0), (-1, 6, 2.0)]  # convex, no right angle
L_SHAPE = [(0, 0, 2.0), (10, 0, 2.0), (10, 4, 2.0), (4, 4, 2.0), (4, 9, 2.0), (0, 9, 2.0)]


def _closed(faces):
    """Every edge is used once in each direction: a closed, consistently oriented shell."""
    edges = [(f[i], f[(i + 1) % len(f)]) for f in faces for i in range(len(f))]
    return len(set(edges)) == len(edges) and all((b, a) in set(edges) for a, b in edges)


def _off_plane(verts, face):
    p = [verts[i] for i in face]
    n = [0.0, 0.0, 0.0]
    for a, b in zip(p, p[1:] + p[:1]):  # Newell normal
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    length = math.sqrt(sum(c * c for c in n))
    return max(abs(sum((q[i] - p[0][i]) * n[i] for i in range(3))) / length for q in p)


def test_radial_roofs_are_closed_planar_solids_on_convex_footprints():
    for ring in (RECT, ROUND, SKEWED):
        for code in roof_shapes.RADIAL:
            solid = roof_shapes.roof_solid(ring, 2.0, 8.0, code, 45.0)
            assert solid is not None, code
            verts, faces, labels = solid
            assert len(faces) == len(labels) and labels[0] == "GroundSurface"
            assert labels.count("WallSurface") == len(ring)
            assert _closed(faces), code
            assert max(_off_plane(verts, f) for f in faces) < 1e-9, code
            roofs = len(ring) * (roof_shapes.DOME_RINGS if code == "3700" else 1)
            assert labels.count("RoofSurface") == roofs, code


def test_rise_follows_the_pitch_and_the_dome_is_a_hemisphere_at_45():
    for code in roof_shapes.RADIAL:
        verts, _f, _l = roof_shapes.roof_solid(ROUND, 2.0, 8.0, code, 45.0)
        inradius = 5 * math.cos(math.pi / 16)
        assert abs(max(v[2] for v in verts) - (8.0 + inradius)) < 1e-9, code


def test_reflex_footprint_is_refused():
    assert not roof_shapes.is_convex(L_SHAPE)
    for code in roof_shapes.RADIAL:
        assert roof_shapes.roof_solid(L_SHAPE, 2.0, 8.0, code) is None


def test_gallery_has_every_adv_roof_type_with_a_shape():
    adv = json.loads((REPO / "shared" / "codelists" / "adv.json").read_text(encoding="utf-8"))
    codes = set(adv["attributes"]["bldg:roofType"]["codes"])
    assert codes - set(roof_shapes.ROOF_TYPES) == {"5000", "9999"}  # Mischform, Sonstiges: no shape
    assert set(roof_shapes.ROOF_TYPES) <= codes
