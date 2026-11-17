import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("roof_shapes_under_test", ROOT / "shared" / "roof_shapes.py")
roofs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roofs)

# 12 m x 8 m, rotated 20 degrees, counter-clockwise, ground at z=2.
ANGLE = math.radians(20)
C, S = math.cos(ANGLE), math.sin(ANGLE)
RING = [(x * C - y * S + 100, x * S + y * C + 50, 2.0) for x, y in ((0, 0), (12, 0), (12, 8), (0, 8))]
L, W, BASE, EAVE = 12.0, 8.0, 2.0, 8.0


def volume(verts, faces):
    """Signed volume (divergence theorem); positive only for a closed, outward-facing solid."""
    vol = 0.0
    for f in faces:
        a = verts[f[0]]
        for i in range(1, len(f) - 1):
            b, c = verts[f[i]], verts[f[i + 1]]
            vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return vol / 6.0


def assert_closed(verts, faces):
    directed = {}
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            assert (a, b) not in directed, "edge used twice in the same direction"
            directed[(a, b)] = True
    for a, b in directed:
        assert (b, a) in directed, "open edge"


def expected_volume(roof_type, pitch, along_long=True):
    t = math.tan(math.radians(pitch))
    block = L * W * (EAVE - BASE)
    span, run = (W, L) if along_long else (L, W)
    if roof_type == "1000":
        return block
    if roof_type == "2100":
        return block + run * span * (span * t) / 2
    if roof_type == "3100":
        return block + run * span * (span / 2 * t) / 2
    if roof_type == "3200":
        h = W / 2 * t
        return block + (W * h / 2) * (L - W) + (W * W * h / 3)
    if roof_type == "3500":
        return block + L * W * (min(L, W) / 2 * t) / 3


def profile_area(roof_type, pitch):
    pts, _ = roofs._profile(roof_type, W, EAVE, math.tan(math.radians(pitch)))
    ring = [(0.0, BASE)] + [(v, z) for v, z in pts] + [(W, BASE)]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2


@pytest.mark.parametrize("roof_type", ["2200", "3400", "3800", "3900"])
def test_extruded_v2_roofs_are_closed_with_the_profile_volume(roof_type):
    verts, faces, labels = roofs.roof_solid(RING, BASE, EAVE, roof_type, pitch_deg=30)
    assert_closed(verts, faces)
    assert math.isclose(volume(verts, faces), L * profile_area(roof_type, 30), rel_tol=1e-9)
    assert volume(verts, faces) > L * W * (EAVE - BASE)
    assert labels.count("GroundSurface") == 1 and labels.count("RoofSurface") >= 2


def test_half_hip_lies_between_gable_and_hip():
    vol = {r: volume(*roofs.roof_solid(RING, BASE, EAVE, r, 30)[:2]) for r in ("3100", "3200", "3300")}
    verts, faces, labels = roofs.roof_solid(RING, BASE, EAVE, "3300", 30)
    assert_closed(verts, faces)
    assert vol["3200"] < vol["3300"] < vol["3100"]
    assert labels.count("RoofSurface") == 4 and labels.count("WallSurface") == 4


@pytest.mark.parametrize("roof_type", ["1000", "2100", "3100", "3200", "3500"])
def test_every_roof_is_a_closed_solid_with_the_right_volume(roof_type):
    verts, faces, labels = roofs.roof_solid(RING, BASE, EAVE, roof_type, pitch_deg=30)
    assert_closed(verts, faces)
    assert math.isclose(volume(verts, faces), expected_volume(roof_type, 30), rel_tol=1e-9)
    assert labels[0] == "GroundSurface" and labels.count("GroundSurface") == 1
    assert labels.count("WallSurface") == 4
    assert labels.count("RoofSurface") == {"1000": 1, "2100": 1, "3100": 2, "3200": 4, "3500": 4}[roof_type]


def test_gable_ridge_on_the_short_side_and_flipped_mono_pitch():
    verts, faces, _ = roofs.roof_solid(RING, BASE, EAVE, "3100", 30, ridge_along_long=False)
    assert math.isclose(volume(verts, faces), expected_volume("3100", 30, along_long=False), rel_tol=1e-9)
    a = roofs.roof_solid(RING, BASE, EAVE, "2100", 15)
    b = roofs.roof_solid(RING, BASE, EAVE, "2100", 15, flip=True)
    assert math.isclose(volume(a[0], a[1]), volume(b[0], b[1]))
    assert a[0] != b[0]


def test_roof_faces_point_up_and_walls_sideways():
    verts, faces, labels = roofs.roof_solid(RING, BASE, EAVE, "3200", 40)
    for f, label in zip(faces, labels):
        p0, p1, p2 = (verts[i] for i in f[:3])
        n = ((p1[1] - p0[1]) * (p2[2] - p0[2]) - (p1[2] - p0[2]) * (p2[1] - p0[1]),
             (p1[2] - p0[2]) * (p2[0] - p0[0]) - (p1[0] - p0[0]) * (p2[2] - p0[2]),
             (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0]))
        nz = n[2] / math.sqrt(sum(c * c for c in n))
        assert {"RoofSurface": nz > 0.1, "WallSurface": abs(nz) < 1e-9, "GroundSurface": nz < -0.99}[label]


def test_square_hip_becomes_a_pyramid_and_non_rectangles_are_refused():
    square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    _, _, labels = roofs.roof_solid(square, 0, 3, "3200", 30)
    assert labels.count("RoofSurface") == 4
    trapezoid = [(0, 0, 0), (10, 0, 0), (8, 6, 0), (2, 6, 0)]
    assert roofs.roof_solid(trapezoid, 0, 3, "3100", 30) is None
    assert roofs.roof_solid(square, 0, 3, "9999", 30) is None
