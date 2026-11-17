"""Ancestor-aware record checksum (CAC-P4.1)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import checksum_view  # noqa: E402
from shared.authoring_core.domain import checksum as c  # noqa: E402

IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _translation(dx=0.0, dy=0.0, dz=0.0):
    return (
        1.0, 0.0, 0.0, dx,
        0.0, 1.0, 0.0, dy,
        0.0, 0.0, 1.0, dz,
        0.0, 0.0, 0.0, 1.0,
    )


def _rows(flat):
    return tuple(flat[i:i + 4] for i in range(0, 16, 4))


WORLD = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
MOVED = ((0.5, 0.0, 0.0), (1.5, 0.0, 0.0), (1.5, 1.0, 0.0), (0.5, 1.0, 0.0))


def snapshot(gml_id="WALL1", matrix=IDENTITY, polygons=None, rings=None, relations=()):
    return c.RecordSnapshot(
        gml_id=gml_id,
        transform=c.TransformSnapshot(matrix=matrix),
        geometry=c.GeometrySnapshot(polygons=polygons or {}, rings=rings or {}),
        relations=relations,
    )


# ---------------------------------------------------------------------------
# domain/checksum.py - pure snapshot/checksum primitives
# ---------------------------------------------------------------------------

def test_identical_snapshots_produce_identical_checksum():
    a = snapshot(polygons={"P1": WORLD}, relations=(("host_id", "WALL1"),))
    b = snapshot(polygons={"P1": WORLD}, relations=(("host_id", "WALL1"),))
    assert c.compute_checksum(a) == c.compute_checksum(b)
    assert c.checksums_match(a, b)


def test_local_transform_change_changes_checksum():
    a = snapshot(matrix=IDENTITY)
    b = snapshot(matrix=_translation(dx=2.0))
    assert c.compute_checksum(a) != c.compute_checksum(b)
    assert not c.checksums_match(a, b)


def test_geometry_coordinate_change_changes_checksum():
    a = snapshot(polygons={"P1": WORLD})
    b = snapshot(polygons={"P1": MOVED})
    assert c.compute_checksum(a) != c.compute_checksum(b)


def test_relation_fact_change_changes_checksum():
    a = snapshot(relations=(("host_id", "WALL1"),))
    b = snapshot(relations=(("host_id", "WALL2"),))
    assert c.compute_checksum(a) != c.compute_checksum(b)


def test_relation_order_does_not_affect_checksum():
    a = snapshot(relations=(("host_id", "WALL1"), ("parent_id", "BLD1")))
    b = snapshot(relations=(("parent_id", "BLD1"), ("host_id", "WALL1")))
    assert c.compute_checksum(a) == c.compute_checksum(b)


def test_polygon_dict_insertion_order_does_not_affect_checksum():
    a = snapshot(polygons={"P1": WORLD, "P2": MOVED})
    b = snapshot(polygons={"P2": MOVED, "P1": WORLD})
    assert c.compute_checksum(a) == c.compute_checksum(b)


def test_gml_id_is_part_of_the_checksum():
    a = snapshot(gml_id="WALL1")
    b = snapshot(gml_id="WALL2")
    assert c.compute_checksum(a) != c.compute_checksum(b)


def test_checksum_is_deterministic_across_repeated_computation():
    a = snapshot(polygons={"P1": WORLD}, relations=(("host_id", "WALL1"),))
    first = c.compute_checksum(a)
    for _ in range(5):
        assert c.compute_checksum(a) == first


def test_coordinate_tolerance_is_explicit():
    # COORD_DECIMALS = 4 (0.1 mm world frame): a sub-tolerance nudge must not
    # register as drift, a nudge at/above the tolerance must.
    below_tolerance = tuple((x + 0.00001, y, z) for x, y, z in WORLD)
    at_tolerance = tuple((x + 0.0001, y, z) for x, y, z in WORLD)
    a = snapshot(polygons={"P1": WORLD})
    b = snapshot(polygons={"P1": below_tolerance})
    d = snapshot(polygons={"P1": at_tolerance})
    assert c.compute_checksum(a) == c.compute_checksum(b)
    assert c.compute_checksum(a) != c.compute_checksum(d)


def test_matrix_tolerance_is_explicit():
    # MATRIX_DECIMALS = 6: float noise below that must not register as drift.
    noisy = tuple(v + 1e-9 for v in IDENTITY)
    moved = tuple(v + 1e-5 if i == 3 else v for i, v in enumerate(IDENTITY))
    a = snapshot(matrix=IDENTITY)
    b = snapshot(matrix=noisy)
    d = snapshot(matrix=moved)
    assert c.compute_checksum(a) == c.compute_checksum(b)
    assert c.compute_checksum(a) != c.compute_checksum(d)


def test_malformed_matrix_length_is_rejected():
    try:
        c.TransformSnapshot(matrix=(1.0, 0.0, 0.0))
    except c.ChecksumError:
        pass
    else:
        raise AssertionError("expected ChecksumError for a short matrix")


# ---------------------------------------------------------------------------
# blender/checksum_view.py - fake-object adapter, no real bpy/mathutils
# ---------------------------------------------------------------------------

class FakeObject:
    def __init__(self, name, matrix_basis=IDENTITY, matrix_world=None, parent=None):
        self.name = name
        self.matrix_basis = _rows(matrix_basis)
        self.matrix_world = _rows(matrix_world if matrix_world is not None else matrix_basis)
        self.parent = parent


def test_local_snapshot_ignores_parent_world_snapshot_does_not():
    building = FakeObject("Building", matrix_basis=_translation(dx=5.0))
    wall = FakeObject(
        "Wall",
        matrix_basis=IDENTITY,
        matrix_world=_translation(dx=5.0),  # Blender bakes Building's move in
        parent=building,
    )

    local = checksum_view.local_transform_snapshot(wall)
    world = checksum_view.world_transform_snapshot(wall)

    assert local.matrix == IDENTITY
    assert world.matrix == _translation(dx=5.0)
    assert c.compute_checksum(snapshot(matrix=local.matrix)) != c.compute_checksum(snapshot(matrix=world.matrix))


def test_ancestor_only_move_changes_the_wall_s_world_checksum():
    building_at_origin = FakeObject("Building", matrix_basis=IDENTITY)
    wall_before = FakeObject(
        "Wall", matrix_basis=IDENTITY, matrix_world=IDENTITY, parent=building_at_origin,
    )

    building_moved = FakeObject("Building", matrix_basis=_translation(dx=3.0))
    wall_after = FakeObject(
        # The wall's own local transform is unchanged - only its ancestor moved.
        "Wall", matrix_basis=IDENTITY, matrix_world=_translation(dx=3.0), parent=building_moved,
    )

    before = checksum_view.record_snapshot(wall_before, "WALL1")
    after = checksum_view.record_snapshot(wall_after, "WALL1")

    assert wall_before.matrix_basis == wall_after.matrix_basis  # own local transform: unchanged
    assert c.compute_checksum(before) != c.compute_checksum(after)  # world checksum: changed


def test_ancestor_chain_is_root_first_and_breaks_on_a_cycle():
    building = FakeObject("Building")
    wall = FakeObject("Wall", parent=building)
    assert checksum_view.ancestor_chain(wall) == ("Building", "Wall")

    # A malformed cyclic parent chain must not hang the walk (still stops
    # once a node is revisited, order unchanged since the cycle is only
    # discovered after both nodes are already recorded).
    building.parent = wall
    assert checksum_view.ancestor_chain(wall) == ("Building", "Wall")


def test_record_snapshot_includes_geometry_and_relations():
    wall = FakeObject("Wall")
    geometry = c.GeometrySnapshot(polygons={"P_WALL": WORLD})
    a = checksum_view.record_snapshot(wall, "WALL1", geometry=geometry, relations=(("owner_id", "BLD1"),))
    b = checksum_view.record_snapshot(wall, "WALL1", geometry=geometry, relations=(("owner_id", "BLD1"),))
    assert c.compute_checksum(a) == c.compute_checksum(b)

    moved_geometry = c.GeometrySnapshot(polygons={"P_WALL": MOVED})
    d = checksum_view.record_snapshot(wall, "WALL1", geometry=moved_geometry, relations=(("owner_id", "BLD1"),))
    assert c.compute_checksum(a) != c.compute_checksum(d)
