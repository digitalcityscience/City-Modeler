"""CAC-P6.3: RoofSurface derived display regeneration. Pure Python, no bpy.

Scope: ``shared/authoring_core/blender/display_builder.py::build_roof_display``,
``shared/authoring_core/domain/display_checksum.py::compute_roof_display_checksum``
and ``shared/authoring_core/blender/display_regen.py::plan_roof_regeneration`` -
the bpy-free halves of P6.3's Roof display builder/checksum/regeneration
planning (docs/agent/authoring-core/phase6-roof-design-note.md). The bpy-bound
half (writing/verifying/rolling back the Roof's own display object) is
covered by the headless real-Blender test instead.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender.display_builder import (  # noqa: E402
    DisplayBuildError, build_roof_display, build_surface_display,
)
from shared.authoring_core.blender.display_regen import (  # noqa: E402
    plan_roof_regeneration, roof_display_freshness,
)
from shared.authoring_core.domain.display_checksum import (  # noqa: E402
    DisplayChecksumError, compute_roof_display_checksum,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

ROOF_ID = "ROOF1"

# Two disjoint, non-overlapping triangular-ish quads in the z=2 plane, each
# its own committed polygon under the same RoofSurface feature - the P6.3 S8
# "multiple PolygonRecords under one RoofSurface" shape, modeled after a
# split-surfaces-exploded real Roof (roof_edit_session._geometry_scope).
POLY_A_EXT = ((0.0, 0.0, 2.0), (4.0, 0.0, 2.0), (4.0, 3.0, 2.0), (0.0, 3.0, 2.0))
POLY_A_HOLE = ((1.0, 1.0, 2.0), (2.0, 1.0, 2.0), (2.0, 2.0, 2.0), (1.0, 2.0, 2.0))
POLY_B_EXT = ((10.0, 0.0, 2.0), (14.0, 0.0, 2.0), (14.0, 3.0, 2.0), (10.0, 3.0, 2.0))


def _ring(ring_id, coords, role="interior", opening_link=""):
    return RingRecord(ring_id=ring_id, role=role, coords=coords, opening_link=opening_link)


def _polygon(polygon_id, exterior, interiors=(), geometry_state="committed"):
    return PolygonRecord(
        polygon_id=polygon_id,
        feature_id=ROOF_ID,
        exterior=_ring(f"{polygon_id}_0_", exterior, role="exterior"),
        interiors=interiors,
        geometry_state=geometry_state,
    )


def _roof_record_set(polygons):
    feature = FeatureRecord(
        gml_id=ROOF_ID, kind="RoofSurface",
        polygon_ids=tuple(p.polygon_id for p in polygons),
    )
    return RecordSet(features=(feature,), polygons=tuple(polygons))


# ---------------------------------------------------------------------------
# build_roof_display
# ---------------------------------------------------------------------------

def test_single_polygon_roof_matches_build_surface_display():
    """build_roof_display's single-polygon case must agree with the
    single-polygon kernel build_surface_display already uses (same
    triangulator, same result) - the degenerate N=1 case of P6.3's merge."""
    polygon = _polygon("P_A", POLY_A_EXT, interiors=(_ring("R_H", POLY_A_HOLE),))
    record_set = _roof_record_set([polygon])

    roof_mesh = build_roof_display(record_set, ROOF_ID)
    wall_mesh = build_surface_display(record_set, ROOF_ID)

    assert roof_mesh.vertices == wall_mesh.vertices
    assert roof_mesh.faces == wall_mesh.faces


def test_multi_polygon_roof_merges_all_committed_polygons():
    poly_a = _polygon("P_A", POLY_A_EXT, interiors=(_ring("R_H", POLY_A_HOLE),))
    poly_b = _polygon("P_B", POLY_B_EXT)
    record_set = _roof_record_set([poly_a, poly_b])

    mesh = build_roof_display(record_set, ROOF_ID)

    mesh_a = build_surface_display(_roof_record_set([poly_a]), ROOF_ID)
    mesh_b = build_surface_display(_roof_record_set([poly_b]), ROOF_ID)

    assert len(mesh.vertices) == len(mesh_a.vertices) + len(mesh_b.vertices)
    assert len(mesh.faces) == len(mesh_a.faces) + len(mesh_b.faces)
    # Every vertex index used by a face stays within bounds (no cross-polygon
    # index leakage from the merge's offsetting).
    assert all(0 <= i < len(mesh.vertices) for face in mesh.faces for i in face)
    # Polygon A's own exterior vertices are present verbatim (deterministic
    # polygon_id-ordered merge: "P_A" sorts before "P_B").
    assert mesh.vertices[: len(mesh_a.vertices)] == mesh_a.vertices


def test_multi_polygon_merge_is_order_independent():
    poly_a = _polygon("P_A", POLY_A_EXT)
    poly_b = _polygon("P_B", POLY_B_EXT)
    forward = build_roof_display(_roof_record_set([poly_a, poly_b]), ROOF_ID)
    backward = build_roof_display(_roof_record_set([poly_b, poly_a]), ROOF_ID)
    assert forward.vertices == backward.vertices
    assert forward.faces == backward.faces


def test_roof_with_no_polygons_raises():
    feature = FeatureRecord(gml_id=ROOF_ID, kind="RoofSurface", polygon_ids=())
    record_set = RecordSet(features=(feature,), polygons=())
    with pytest.raises(DisplayBuildError):
        build_roof_display(record_set, ROOF_ID)


def test_roof_with_unknown_surface_id_raises():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT)])
    with pytest.raises(DisplayBuildError):
        build_roof_display(record_set, "NOT_A_ROOF")


def test_roof_with_a_non_committed_polygon_raises():
    poly_a = _polygon("P_A", POLY_A_EXT, geometry_state="committed")
    poly_b = _polygon("P_B", POLY_B_EXT, geometry_state="candidate")
    record_set = _roof_record_set([poly_a, poly_b])
    with pytest.raises(DisplayBuildError):
        build_roof_display(record_set, ROOF_ID)


def test_roof_display_contains_no_semantic_authority():
    """A DisplayMesh is plain vertex/face topology only - no gml_id, no
    material/semantic id of any kind travels through it (P6.3 §4/§7 test
    5)."""
    mesh = build_roof_display(_roof_record_set([_polygon("P_A", POLY_A_EXT)]), ROOF_ID)
    assert set(vars(mesh).keys()) == {"vertices", "faces"}


# ---------------------------------------------------------------------------
# compute_roof_display_checksum
# ---------------------------------------------------------------------------

def test_roof_checksum_deterministic_across_repeated_computation():
    record_set = _roof_record_set([
        _polygon("P_A", POLY_A_EXT, interiors=(_ring("R_H", POLY_A_HOLE),)),
        _polygon("P_B", POLY_B_EXT),
    ])
    first = compute_roof_display_checksum(record_set, ROOF_ID)
    second = compute_roof_display_checksum(record_set, ROOF_ID)
    assert first == second


def test_roof_checksum_independent_of_polygon_tuple_order():
    poly_a = _polygon("P_A", POLY_A_EXT)
    poly_b = _polygon("P_B", POLY_B_EXT)
    forward = compute_roof_display_checksum(_roof_record_set([poly_a, poly_b]), ROOF_ID)
    backward = compute_roof_display_checksum(_roof_record_set([poly_b, poly_a]), ROOF_ID)
    assert forward == backward


def test_roof_checksum_changes_when_a_second_polygons_geometry_changes():
    poly_a = _polygon("P_A", POLY_A_EXT)
    poly_b = _polygon("P_B", POLY_B_EXT)
    before = compute_roof_display_checksum(_roof_record_set([poly_a, poly_b]), ROOF_ID)

    moved_b = _polygon("P_B", tuple((x + 1.0, y, z) for x, y, z in POLY_B_EXT))
    after = compute_roof_display_checksum(_roof_record_set([poly_a, moved_b]), ROOF_ID)
    assert before != after


def test_roof_checksum_changes_when_a_hole_is_added():
    bare = compute_roof_display_checksum(_roof_record_set([_polygon("P_A", POLY_A_EXT)]), ROOF_ID)
    holed = compute_roof_display_checksum(
        _roof_record_set([_polygon("P_A", POLY_A_EXT, interiors=(_ring("R_H", POLY_A_HOLE),))]), ROOF_ID
    )
    assert bare != holed


def test_roof_checksum_raises_for_missing_feature():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT)])
    with pytest.raises(DisplayChecksumError):
        compute_roof_display_checksum(record_set, "NOT_A_ROOF")


def test_roof_checksum_raises_for_no_polygons():
    feature = FeatureRecord(gml_id=ROOF_ID, kind="RoofSurface", polygon_ids=())
    record_set = RecordSet(features=(feature,), polygons=())
    with pytest.raises(DisplayChecksumError):
        compute_roof_display_checksum(record_set, ROOF_ID)


def test_roof_checksum_raises_for_a_non_committed_polygon():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT, geometry_state="candidate")])
    with pytest.raises(DisplayChecksumError):
        compute_roof_display_checksum(record_set, ROOF_ID)


# ---------------------------------------------------------------------------
# plan_roof_regeneration
# ---------------------------------------------------------------------------

def test_plan_roof_regeneration_builds_from_record_set():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT)])
    mesh = plan_roof_regeneration(record_set, ROOF_ID)
    assert mesh is not None
    assert mesh.vertices[:4] == POLY_A_EXT


def test_plan_roof_regeneration_skips_a_missing_record_set():
    assert plan_roof_regeneration(None, ROOF_ID) is None


def test_plan_roof_regeneration_propagates_display_build_error():
    feature = FeatureRecord(gml_id=ROOF_ID, kind="RoofSurface", polygon_ids=())
    record_set = RecordSet(features=(feature,), polygons=())
    with pytest.raises(DisplayBuildError):
        plan_roof_regeneration(record_set, ROOF_ID)


# ---------------------------------------------------------------------------
# roof_display_freshness
# ---------------------------------------------------------------------------

class _FakeDisplayObject:
    """Minimal stand-in for a bpy display object's custom-property dict,
    just enough for ``roof_display_freshness`` (which only ever calls
    ``.get``) - keeps this test bpy-free."""

    def __init__(self, checksum=None):
        self._props = {} if checksum is None else {"_cgml_display_source_checksum": checksum}

    def get(self, key, default=None):
        return self._props.get(key, default)


def test_roof_display_freshness_is_missing_with_no_display_object():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT)])
    assert roof_display_freshness(None, record_set, ROOF_ID) == "missing"


def test_roof_display_freshness_is_fresh_when_checksum_matches():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT), _polygon("P_B", POLY_B_EXT)])
    expected = compute_roof_display_checksum(record_set, ROOF_ID)
    display_obj = _FakeDisplayObject(checksum=expected)
    assert roof_display_freshness(display_obj, record_set, ROOF_ID) == "fresh"


def test_roof_display_freshness_is_stale_when_checksum_does_not_match():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT), _polygon("P_B", POLY_B_EXT)])
    display_obj = _FakeDisplayObject(checksum="not-the-real-checksum")
    assert roof_display_freshness(display_obj, record_set, ROOF_ID) == "stale"


def test_roof_display_freshness_is_stale_when_no_checksum_property_stored():
    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT)])
    display_obj = _FakeDisplayObject(checksum=None)
    assert roof_display_freshness(display_obj, record_set, ROOF_ID) == "stale"


def test_roof_display_freshness_is_invalid_when_records_cannot_build():
    feature = FeatureRecord(gml_id=ROOF_ID, kind="RoofSurface", polygon_ids=())
    record_set = RecordSet(features=(feature,), polygons=())
    display_obj = _FakeDisplayObject(checksum="whatever")
    assert roof_display_freshness(display_obj, record_set, ROOF_ID) == "invalid"


def test_roof_display_freshness_disagrees_with_single_polygon_wall_checksum():
    """The exact P6.3 real-Blender regression this function fixes: a single-
    polygon RoofSurface's ``mark_display_fresh``-written checksum (always
    computed via ``compute_roof_display_checksum``) must classify as FRESH
    against ``roof_display_freshness`` even though ``compute_surface_
    display_checksum``'s differently-serialized checksum for the same
    single polygon does NOT equal it (the two functions are not
    interchangeable, by design)."""
    from shared.authoring_core.domain.display_checksum import compute_surface_display_checksum

    record_set = _roof_record_set([_polygon("P_A", POLY_A_EXT, interiors=(_ring("R_H", POLY_A_HOLE),))])
    roof_checksum = compute_roof_display_checksum(record_set, ROOF_ID)
    wall_checksum = compute_surface_display_checksum(record_set, ROOF_ID)
    assert roof_checksum != wall_checksum

    display_obj = _FakeDisplayObject(checksum=roof_checksum)
    assert roof_display_freshness(display_obj, record_set, ROOF_ID) == "fresh"
