"""B39: shell_is_closed decides whether an authored building gets a lodNSolid."""
from shared.writer_geometry import shell_is_closed

V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def rings(faces, close=False):
    return [[V[i] for i in f] + ([V[f[0]]] if close else []) for f in faces]


def test_cube_is_closed():
    assert shell_is_closed(rings(CUBE))
    assert shell_is_closed(rings(CUBE, close=True))  # posList style, first point repeated


def test_missing_face_is_open():
    assert not shell_is_closed(rings(CUBE[1:]))


def test_hole_without_filling_is_open():
    hole = [(0.2, 0, 0.2), (0.2, 0, 0.8), (0.8, 0, 0.8), (0.8, 0, 0.2)]
    assert not shell_is_closed(rings(CUBE) + [hole])


def test_empty_is_not_closed():
    assert not shell_is_closed([])
