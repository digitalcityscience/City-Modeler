"""B51: a flat roof (AdV 1000) goes on any footprint; pitched roofs still need right angles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shared import roof_shapes  # noqa: E402  (pure Python, no bpy)

SKEWED = [(0, 0, 10.0), (10, 0, 10.0), (12, 7, 10.0), (-1, 6, 10.0)]  # no right angle anywhere


def test_flat_roof_on_a_skewed_footprint_is_a_closed_prism():
    verts, faces, labels = roof_shapes.roof_solid(SKEWED, 10.0, 19.0, "1000")
    assert labels.count("GroundSurface") == 1 and labels.count("RoofSurface") == 1
    assert labels.count("WallSurface") == 4 and len(faces) == 6
    zs = {round(v[2], 6) for v in verts}
    assert zs == {10.0, 19.0}  # ground at base, flat roof at the eave
    # every edge used by exactly two faces: closed
    from collections import Counter
    edges = Counter()
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[frozenset((a, b))] += 1
    assert all(n == 2 for n in edges.values())


def test_pitched_roof_still_needs_right_angles():
    assert roof_shapes.roof_solid(SKEWED, 10.0, 19.0, "3100") is None
