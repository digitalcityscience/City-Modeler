import math

from shared.facade_surface_resolver import (
    FacadeFrame,
    _vcross,
    _vdot,
    _vlen,
    compute_facade_frame,
    surface_needs_split,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _assert_orthonormal_right_handed(frame: FacadeFrame, tol=1e-9):
    h, v, n = frame.horizontal_axis, frame.vertical_axis, frame.normal
    for axis in (h, v, n):
        assert abs(_vlen(axis) - 1.0) < tol
    assert abs(_vdot(h, v)) < tol
    assert abs(_vdot(h, n)) < tol
    assert abs(_vdot(v, n)) < tol
    cross_hv = _vcross(h, v)
    assert all(abs(cross_hv[i] - n[i]) < tol for i in range(3))


class FakeMaterial(dict):
    pass


class FakePolygon:
    def __init__(self, material_index=0):
        self.material_index = material_index


class FakeMeshData:
    def __init__(self, polygons=None, materials=None):
        self.polygons = polygons if polygons is not None else []
        self.materials = materials if materials is not None else []


class FakeObject(dict):
    def __init__(self, name="obj", *, obj_type="MESH", data=None, **props):
        super().__init__(**props)
        self.name = name
        self.type = obj_type
        self.data = data


# ---------------------------------------------------------------------------
# Frame math: vertical wall (exact, hand-verified values)
# ---------------------------------------------------------------------------
def test_vertical_wall_frame_matches_expected_axes_and_dimensions():
    # Rectangle in the XZ plane (y=0), width 2 along X, height 1 along Z,
    # wound so Newell's method yields outward normal (0, 1, 0).
    points = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 0.0, 0.0)]

    frame = compute_facade_frame(points)

    assert frame.normal == (0.0, 1.0, 0.0)
    assert frame.horizontal_axis == (-1.0, 0.0, 0.0)
    assert frame.vertical_axis == (0.0, -0.0, 1.0)
    assert frame.width == 2.0
    assert frame.height == 1.0
    assert frame.origin == (2.0, 0.0, 0.0)
    _assert_orthonormal_right_handed(frame)


def test_reversing_winding_flips_the_outward_normal():
    """Outward orientation follows the surface's own winding, not a fixed
    convention - reversing the stored vertex order must negate the normal."""
    forward = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 0.0, 0.0)]
    backward = list(reversed(forward))

    forward_frame = compute_facade_frame(forward)
    backward_frame = compute_facade_frame(backward)

    assert forward_frame.normal == (0.0, 1.0, 0.0)
    assert backward_frame.normal == (0.0, -1.0, 0.0)


def test_mirrored_scale_would_make_a_naively_transformed_normal_wrong():
    """The module's whole reason to recompute the normal from world-space
    winding (rather than trust a transformed ``mesh.polygons[i].normal``) is
    that the naive transform silently flips outward for a mirrored/negative-
    scale object. Prove the two genuinely disagree for one: a local quad
    winds to normal (0, 1, 0) (see the vertical-wall test above); mirror it
    on X (matrix_world scale = (-1, 1, 1), the same transform a mirrored
    Blender object applies) and recompute from the *transformed* vertex
    positions - the only thing ``compute_facade_frame`` ever sees."""
    local_points = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (2.0, 0.0, 0.0)]
    local_frame = compute_facade_frame(local_points)
    assert local_frame.normal == (0.0, 1.0, 0.0)

    def mirror_x(p):
        return (-p[0], p[1], p[2])

    world_points = [mirror_x(p) for p in local_points]
    world_frame = compute_facade_frame(world_points)

    # The naive approach a mirrored object would get wrong: transform the
    # *precomputed local* normal by the same 3x3 matrix (diag(-1, 1, 1)).
    naively_transformed_normal = mirror_x(local_frame.normal)

    assert world_frame.normal == (0.0, -1.0, 0.0)
    assert naively_transformed_normal == (0.0, 1.0, 0.0)
    # They disagree - the naive transform would have kept the normal
    # pointing the pre-mirror way, while the true (winding-consistent)
    # outward direction flipped with the mirror.
    assert _vdot(world_frame.normal, naively_transformed_normal) < 0.0


# ---------------------------------------------------------------------------
# Frame math: tilted wall (property-based - orthonormal, right-handed,
# vertical axis leans toward global up, dimensions preserved)
# ---------------------------------------------------------------------------
def test_tilted_wall_frame_is_orthonormal_and_follows_projected_global_up():
    # normal leans back from horizontal: mostly +Y with a +Z tilt component.
    raw_normal = (0.0, 0.6, 0.8)
    length = math.sqrt(sum(c * c for c in raw_normal))
    normal = tuple(c / length for c in raw_normal)

    u_axis = (1.0, 0.0, 0.0)  # already perpendicular to normal (no x component)
    w_axis = _vcross(normal, u_axis)
    w_len = _vlen(w_axis)
    w_axis = tuple(c / w_len for c in w_axis)

    def point(a, b):
        return tuple(u_axis[i] * a + w_axis[i] * b for i in range(3))

    points = [point(0, 0), point(2, 0), point(2, 1), point(0, 1)]

    frame = compute_facade_frame(points)

    _assert_orthonormal_right_handed(frame)
    assert frame.width == 2.0
    assert frame.height == 1.0
    # V is globalZ projected onto the plane, re-orthogonalized: it must lean
    # toward global up, not down.
    assert frame.vertical_axis[2] > 0.0


# ---------------------------------------------------------------------------
# Frame math: roof / globalZ-parallel fallback (exact, hand-verified values)
# ---------------------------------------------------------------------------
def test_tilted_roof_frame_is_orthonormal():
    # Roof tilted mostly upward with a shallow slope - not parallel to Z.
    raw_normal = (0.2, 0.1, 0.97)
    length = math.sqrt(sum(c * c for c in raw_normal))
    normal = tuple(c / length for c in raw_normal)

    u_axis = _vcross((0.0, 0.0, 1.0), normal)
    u_len = _vlen(u_axis)
    u_axis = tuple(c / u_len for c in u_axis)
    w_axis = _vcross(normal, u_axis)

    def point(a, b):
        return tuple(u_axis[i] * a + w_axis[i] * b for i in range(3))

    points = [point(0, 0), point(3, 0), point(3, 2), point(0, 2)]

    frame = compute_facade_frame(points)

    _assert_orthonormal_right_handed(frame)
    assert abs(frame.width - 3.0) < 1e-9
    assert abs(frame.height - 2.0) < 1e-9


def test_global_z_parallel_normal_falls_back_to_longest_edge():
    # Flat roof: normal exactly (0, 0, 1) - global Z projection is zero-length,
    # so H must come from the surface's own longest edge instead.
    points = [(0.0, 0.0, 5.0), (3.0, 0.0, 5.0), (3.0, 1.0, 5.0), (0.0, 1.0, 5.0)]

    frame = compute_facade_frame(points)

    assert frame.normal == (0.0, 0.0, 1.0)
    assert frame.horizontal_axis == (1.0, 0.0, 0.0)
    assert frame.vertical_axis == (0.0, 1.0, 0.0)
    assert frame.width == 3.0
    assert frame.height == 1.0
    assert frame.origin == (0.0, 0.0, 5.0)


# ---------------------------------------------------------------------------
# Degenerate / non-planar rejection
# ---------------------------------------------------------------------------
def test_collinear_points_are_rejected_as_degenerate():
    points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    try:
        compute_facade_frame(points)
        assert False, "expected ValueError for collinear points"
    except ValueError as e:
        assert "degenerate" in str(e)


def test_fewer_than_three_distinct_points_is_rejected():
    points = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
    try:
        compute_facade_frame(points)
        assert False, "expected ValueError for < 3 distinct vertices"
    except ValueError as e:
        assert "fewer than 3 distinct vertices" in str(e)


def test_non_planar_face_is_rejected_with_clear_message():
    # A unit-scale quad with one vertex lifted far out of plane.
    points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.5)]
    try:
        compute_facade_frame(points)
        assert False, "expected ValueError for non-planar face"
    except ValueError as e:
        assert "non-planar" in str(e)


# ---------------------------------------------------------------------------
# Merged-building detection (D6) - pure duck-typed predicate, no bpy needed.
# ---------------------------------------------------------------------------
def test_merged_building_face_with_boundary_surface_material_needs_split():
    mat = FakeMaterial(SurfaceTyp="WallSurface")
    mesh = FakeMeshData(polygons=[FakePolygon(material_index=0)], materials=[mat])
    obj = FakeObject("Building_1", cgml3_feature="Building", data=mesh)

    assert surface_needs_split(obj, 0) is True


def test_already_exploded_surface_object_does_not_need_split():
    mat = FakeMaterial(SurfaceTyp="WallSurface")
    mesh = FakeMeshData(polygons=[FakePolygon(material_index=0)], materials=[mat])
    obj = FakeObject("WallSurface_1", cgml3_feature="WallSurface", data=mesh)

    assert surface_needs_split(obj, 0) is False


def test_face_with_no_recognizable_surface_type_does_not_need_split():
    mat = FakeMaterial()  # no SurfaceTyp/surface_type at all
    mesh = FakeMeshData(polygons=[FakePolygon(material_index=0)], materials=[mat])
    obj = FakeObject("Building_1", cgml3_feature="Building", data=mesh)

    assert surface_needs_split(obj, 0) is False


def test_non_mesh_object_does_not_need_split():
    obj = FakeObject("Empty_1", obj_type="EMPTY", cgml3_feature="Building", data=None)

    assert surface_needs_split(obj, 0) is False


def test_out_of_range_face_index_does_not_need_split():
    mat = FakeMaterial(SurfaceTyp="WallSurface")
    mesh = FakeMeshData(polygons=[FakePolygon(material_index=0)], materials=[mat])
    obj = FakeObject("Building_1", cgml3_feature="Building", data=mesh)

    assert surface_needs_split(obj, 5) is False
