"""Display-source cache freshness / checksum strategy (CAC-P3b.4)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import display_regen  # noqa: E402
from shared.authoring_core.domain import display_checksum as dc  # noqa: E402
from shared.authoring_core.domain import records  # noqa: E402

WALL_ID = "WALL1"
POLYGON_ID = "P_WALL"

EXTERIOR = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
HOLE_A = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))
HOLE_B = ((2.5, 0.3, 0.0), (3.2, 0.3, 0.0), (3.2, 1.0, 0.0), (2.5, 1.0, 0.0))
HOLE_C = ((0.3, 0.3, 0.0), (0.8, 0.3, 0.0), (0.8, 0.8, 0.0), (0.3, 0.8, 0.0))


def _ring(ring_id, coords, opening_link="", role="interior"):
    return records.RingRecord(ring_id=ring_id, role=role, coords=coords, opening_link=opening_link)


def _wall_record_set(exterior=EXTERIOR, interiors=(), surface_id=WALL_ID, polygon_id=POLYGON_ID,
                      geometry_state="committed", extra_feature=None, extra_polygon=None):
    polygon = records.PolygonRecord(
        polygon_id=polygon_id,
        feature_id=surface_id,
        exterior=_ring("R_EXT", exterior, role="exterior"),
        interiors=interiors,
        geometry_state=geometry_state,
    )
    feature = records.FeatureRecord(gml_id=surface_id, kind="WallSurface", polygon_ids=(polygon_id,))
    features = (feature,) if extra_feature is None else (extra_feature, feature)
    polygons = (polygon,) if extra_polygon is None else (extra_polygon, polygon)
    return records.RecordSet(features=features, polygons=polygons)


# ---------------------------------------------------------------------------
# domain/display_checksum.py - canonical checksum input, determinism
# ---------------------------------------------------------------------------

def test_deterministic_checksum_for_simple_wall():
    a = _wall_record_set()
    b = _wall_record_set()
    assert dc.compute_surface_display_checksum(a, WALL_ID) == dc.compute_surface_display_checksum(b, WALL_ID)


def test_deterministic_checksum_for_wall_with_one_hole():
    interiors = (_ring("R_A", HOLE_A, opening_link="WINDOW_A"),)
    a = _wall_record_set(interiors=interiors)
    b = _wall_record_set(interiors=interiors)
    assert dc.compute_surface_display_checksum(a, WALL_ID) == dc.compute_surface_display_checksum(b, WALL_ID)


def test_deterministic_checksum_for_n_holes():
    interiors = (
        _ring("R_A", HOLE_A, opening_link="WINDOW_A"),
        _ring("R_B", HOLE_B, opening_link="WINDOW_B"),
        _ring("R_C", HOLE_C, opening_link="WINDOW_C"),
    )
    a = _wall_record_set(interiors=interiors)
    b = _wall_record_set(interiors=interiors)
    assert dc.compute_surface_display_checksum(a, WALL_ID) == dc.compute_surface_display_checksum(b, WALL_ID)


def test_reordered_interior_rings_and_features_do_not_change_checksum():
    interiors = (
        _ring("R_A", HOLE_A, opening_link="WINDOW_A"),
        _ring("R_B", HOLE_B, opening_link="WINDOW_B"),
        _ring("R_C", HOLE_C, opening_link="WINDOW_C"),
    )
    reordered = (interiors[2], interiors[0], interiors[1])

    other_feature = records.FeatureRecord(gml_id="OTHER", kind="WallSurface", polygon_ids=("P_OTHER",))
    other_polygon = records.PolygonRecord(
        polygon_id="P_OTHER", feature_id="OTHER",
        exterior=_ring("R_EXT2", EXTERIOR, role="exterior"), geometry_state="committed",
    )

    a = _wall_record_set(interiors=interiors, extra_feature=other_feature, extra_polygon=other_polygon)
    b = _wall_record_set(interiors=reordered)  # different construction order, no unrelated feature
    assert dc.compute_surface_display_checksum(a, WALL_ID) == dc.compute_surface_display_checksum(b, WALL_ID)


def test_changed_exterior_coordinates_changes_checksum():
    moved_exterior = tuple((x + 0.5, y, z) for x, y, z in EXTERIOR)
    a = _wall_record_set()
    b = _wall_record_set(exterior=moved_exterior)
    assert dc.compute_surface_display_checksum(a, WALL_ID) != dc.compute_surface_display_checksum(b, WALL_ID)


def test_changed_interior_ring_coordinates_changes_checksum():
    moved_hole = tuple((x + 0.1, y, z) for x, y, z in HOLE_A)
    a = _wall_record_set(interiors=(_ring("R_A", HOLE_A, opening_link="WINDOW_A"),))
    b = _wall_record_set(interiors=(_ring("R_A", moved_hole, opening_link="WINDOW_A"),))
    assert dc.compute_surface_display_checksum(a, WALL_ID) != dc.compute_surface_display_checksum(b, WALL_ID)


def test_changed_opening_link_changes_checksum():
    a = _wall_record_set(interiors=(_ring("R_A", HOLE_A, opening_link="WINDOW_A"),))
    b = _wall_record_set(interiors=(_ring("R_A", HOLE_A, opening_link="WINDOW_B"),))
    assert dc.compute_surface_display_checksum(a, WALL_ID) != dc.compute_surface_display_checksum(b, WALL_ID)


def test_sibling_hole_ordering_does_not_change_checksum_but_geometry_does():
    interiors = (
        _ring("R_A", HOLE_A, opening_link="WINDOW_A"),
        _ring("R_B", HOLE_B, opening_link="WINDOW_B"),
    )
    a = _wall_record_set(interiors=interiors)
    b = _wall_record_set(interiors=tuple(reversed(interiors)))
    assert dc.compute_surface_display_checksum(a, WALL_ID) == dc.compute_surface_display_checksum(b, WALL_ID)

    changed = (
        _ring("R_A", HOLE_A, opening_link="WINDOW_A"),
        _ring("R_B", tuple((x + 0.2, y, z) for x, y, z in HOLE_B), opening_link="WINDOW_B"),
    )
    d = _wall_record_set(interiors=changed)
    assert dc.compute_surface_display_checksum(a, WALL_ID) != dc.compute_surface_display_checksum(d, WALL_ID)


def test_checksum_is_deterministic_across_repeated_computation():
    a = _wall_record_set(interiors=(_ring("R_A", HOLE_A, opening_link="WINDOW_A"),))
    first = dc.compute_surface_display_checksum(a, WALL_ID)
    for _ in range(5):
        assert dc.compute_surface_display_checksum(a, WALL_ID) == first


def test_missing_feature_raises_display_checksum_error():
    record_set = records.RecordSet()
    try:
        dc.compute_surface_display_checksum(record_set, "NOPE")
    except dc.DisplayChecksumError:
        pass
    else:
        raise AssertionError("expected DisplayChecksumError for a missing feature")


def test_wrong_polygon_count_raises_display_checksum_error():
    feature = records.FeatureRecord(gml_id=WALL_ID, kind="WallSurface", polygon_ids=())
    record_set = records.RecordSet(features=(feature,), polygons=())
    try:
        dc.compute_surface_display_checksum(record_set, WALL_ID)
    except dc.DisplayChecksumError:
        pass
    else:
        raise AssertionError("expected DisplayChecksumError for zero polygons")


# ---------------------------------------------------------------------------
# domain/display_checksum.py - freshness classification
# ---------------------------------------------------------------------------

def test_missing_stored_checksum_is_stale_not_fresh():
    assert dc.classify_freshness(None, "abc123") == dc.STALE


def test_mismatching_checksum_is_stale():
    assert dc.classify_freshness("old", "new") == dc.STALE


def test_matching_checksum_is_fresh():
    assert dc.classify_freshness("same", "same") == dc.FRESH


def test_malformed_stored_checksum_is_invalid():
    assert dc.classify_freshness(123, "abc") == dc.INVALID
    assert dc.classify_freshness("", "abc") == dc.INVALID


# ---------------------------------------------------------------------------
# blender/display_regen.py - freshness API over a fake (duck-typed) object
# ---------------------------------------------------------------------------

class FakeDisplayObject:
    def __init__(self, props=None):
        self._props = dict(props or {})

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __setitem__(self, key, value):
        self._props[key] = value

    def __delitem__(self, key):
        del self._props[key]

    def keys(self):
        return self._props.keys()


def test_no_display_object_is_missing():
    record_set = _wall_record_set()
    assert display_regen.display_freshness(None, record_set, WALL_ID) == dc.MISSING


def test_display_with_no_checksum_property_is_stale():
    record_set = _wall_record_set()
    obj = FakeDisplayObject()
    assert display_regen.display_freshness(obj, record_set, WALL_ID) == dc.STALE


def test_display_with_matching_checksum_is_fresh_after_mark():
    record_set = _wall_record_set()
    obj = FakeDisplayObject()
    checksum = dc.compute_surface_display_checksum(record_set, WALL_ID)
    display_regen.mark_display_fresh(obj, checksum)
    assert display_regen.display_freshness(obj, record_set, WALL_ID) == dc.FRESH


def test_display_freshness_flips_to_stale_after_record_change():
    record_set = _wall_record_set()
    obj = FakeDisplayObject()
    display_regen.mark_display_fresh(obj, dc.compute_surface_display_checksum(record_set, WALL_ID))
    assert display_regen.display_freshness(obj, record_set, WALL_ID) == dc.FRESH

    moved_exterior = tuple((x + 1.0, y, z) for x, y, z in EXTERIOR)
    changed_record_set = _wall_record_set(exterior=moved_exterior)
    assert display_regen.display_freshness(obj, changed_record_set, WALL_ID) == dc.STALE


def test_unbuildable_records_are_invalid_not_fresh_or_stale():
    record_set = records.RecordSet()
    obj = FakeDisplayObject({display_regen.DISPLAY_SOURCE_CHECKSUM_KEY: "whatever"})
    assert display_regen.display_freshness(obj, record_set, "NOPE") == dc.INVALID
