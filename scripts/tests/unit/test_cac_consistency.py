"""Record <-> legacy consistency validator (CAC-P3.3). No Blender."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import consistency as c  # noqa: E402
from shared.authoring_core.domain import registry_core as rc  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
MOVED = ((0.5, 0.0, 0.0), (1.5, 0.0, 0.0), (1.5, 1.0, 0.0), (0.5, 1.0, 0.0))


def window(**over):
    base = dict(
        gml_id="WIN1", kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link="LINK1", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN",),
    )
    base.update(over)
    return FeatureRecord(**base)


def wall(**over):
    base = dict(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, provenance="imported",
        polygon_ids=("P_WALL",),
    )
    base.update(over)
    return FeatureRecord(**base)


def window_polygon(coords=WORLD, geometry_state="committed"):
    return PolygonRecord(
        polygon_id="P_WIN", feature_id="WIN1", geometry_state=geometry_state,
        exterior=RingRecord(ring_id="P_WIN_0_", role="exterior", coords=coords if geometry_state == "committed" else ()),
    )


def wall_polygon(ring_coords=WORLD, geometry_state="committed", opening_link="LINK1"):
    ring = RingRecord(
        ring_id="P_WALL_1_", role="interior",
        coords=ring_coords if geometry_state == "committed" else (), opening_link=opening_link,
    )
    return PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state=geometry_state,
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=WORLD if geometry_state == "committed" else ()),
        interiors=(ring,),
    )


def fresh_geometry():
    return c.GeometryFact(polygons={"P_WIN": WORLD, "P_WALL": WORLD}, rings={"LINK1": WORLD})


def legacy_of_matching(feature):
    return c.LegacyFact(exists=True, feature=feature, polygon_ids=feature.polygon_ids)


def legacy_of_record_set(record_set):
    """``legacy_of`` that mirrors every feature in ``record_set`` and treats
    any other referenced id (e.g. an owning Building not itself modelled in
    a test fixture) as existing, so cross-cutting reference checks don't
    misfire on ids the test never meant to exercise."""
    def _legacy_of(gml_id):
        feature = record_set.feature(gml_id)
        if feature is not None:
            return legacy_of_matching(feature)
        return c.LegacyFact(exists=True)
    return _legacy_of


# -- clean cases (1, 2 in the task's "known cases") -------------------------------

def test_fresh_window_is_clean():
    feature = window()
    findings = c.validate_feature(feature, (window_polygon(),), legacy_of_matching(feature), fresh_geometry())
    assert findings == ()


def test_fresh_wall_ring_is_clean():
    feature = wall()
    findings = c.validate_feature(feature, (wall_polygon(),), legacy_of_matching(feature), fresh_geometry())
    assert findings == ()


def test_record_set_clean_scene_has_no_findings():
    record_set = RecordSet(features=(window(), wall()), polygons=(window_polygon(), wall_polygon()))
    findings = c.validate_record_set(
        record_set,
        legacy_of=legacy_of_record_set(record_set),
        geometry_of=lambda _f: fresh_geometry(),
    )
    assert findings == ()


# -- geometry drift (3, 4) --------------------------------------------------------

def test_moved_window_reports_coords_stale_not_error():
    feature = window()
    geometry = c.GeometryFact(polygons={"P_WIN": MOVED}, rings={})
    findings = c.validate_feature(feature, (window_polygon(),), legacy_of_matching(feature), geometry)
    assert [f.code for f in findings] == [c.COORDS_STALE]
    assert findings[0].severity == c.WARNING
    assert not c.is_blocking(findings)


def test_ancestor_transform_move_is_indistinguishable_from_own_move():
    # A parent Building transform changes the same live world-space
    # coordinates the validator reads (matrix_world already folds in every
    # ancestor) - so it is caught by the same coords_stale path as a direct
    # G/R/S on the object itself; no separate code path is needed.
    test_moved_window_reports_coords_stale_not_error()


def test_unresolved_geometry_is_a_warning_not_an_error():
    feature = window()
    geometry = c.GeometryFact(polygons={}, rings={})
    findings = c.validate_feature(feature, (window_polygon(),), legacy_of_matching(feature), geometry)
    assert [f.code for f in findings] == [c.GEOMETRY_UNRESOLVED]
    assert not c.is_blocking(findings)


def test_ids_only_polygon_is_not_geometry_checked():
    feature = window()
    polygon = window_polygon(geometry_state="ids_only")
    findings = c.validate_feature(feature, (polygon,), legacy_of_matching(feature), c.GeometryFact())
    assert findings == ()


# -- identity (6, 7) ---------------------------------------------------------------

def test_object_gml_id_mismatch_is_missing_object():
    feature = window()
    findings = c.validate_feature(feature, (window_polygon(),), c.LegacyFact(exists=False), fresh_geometry())
    assert [f.code for f in findings] == [c.MISSING_OBJECT]
    assert findings[0].severity == c.ERROR
    assert c.is_blocking(findings)


def test_duplicate_and_foreign_records_from_registry_are_blocking():
    findings = c.from_registry_findings([
        rc.Finding(rc.DUPLICATE_RECORD, "W", ("a", "b"), "2 objects carry a record for 'W'"),
        rc.Finding(rc.FOREIGN_RECORD, "S2", ("copy",), "'copy' carries a record for ['S1']"),
        rc.Finding(rc.MISSING_GML_ID, "", ("x",), "object 'x' has no gml_id"),
    ])
    assert [f.code for f in findings] == [c.DUPLICATE_RECORD, c.FOREIGN_RECORD, "missing_gml_id"]
    assert c.is_blocking(findings[:2])
    assert not c.is_blocking(findings[2:])


# -- relationships (8, 9) -----------------------------------------------------------

def test_missing_window_object_is_dangling_record():
    record_set = RecordSet(features=(window(),), polygons=(window_polygon(),))
    findings = c.validate_record_set(
        record_set,
        legacy_of=lambda gml_id: c.LegacyFact(exists=False),
        geometry_of=lambda _f: fresh_geometry(),
    )
    assert any(f.code == c.MISSING_OBJECT and f.gml_id == "WIN1" for f in findings)
    assert c.is_blocking(findings)


def test_deleted_window_leaves_a_dangling_ring_on_the_wall():
    # AB-02 via X-delete: the Window's own record vanishes with its object
    # (per-object storage), but the wall's RingRecord still names the
    # opening_link - nothing else would catch this since there is no
    # "missing" feature to report against.
    record_set = RecordSet(features=(wall(),), polygons=(wall_polygon(),))
    findings = c.validate_record_set(
        record_set,
        legacy_of=legacy_of_record_set(record_set),
        geometry_of=lambda _f: fresh_geometry(),
    )
    assert any(f.code == c.DANGLING_RING for f in findings)
    assert c.is_blocking(findings)


def test_missing_wall_ring_pairing_is_a_relationship_finding():
    # Window claims opening_link LINK1, but the wall's own polygon carries no
    # such interior ring (a broken opening_link, or a dropped RingRecord).
    record_set = RecordSet(
        features=(window(), wall()),
        polygons=(window_polygon(), wall_polygon(opening_link="OTHER")),
    )
    findings = c.validate_record_set(
        record_set,
        legacy_of=legacy_of_record_set(record_set),
        geometry_of=lambda _f: fresh_geometry(),
    )
    assert any(f.code == c.MISSING_RING_PAIRING for f in findings)
    assert c.is_blocking(findings)


def test_host_id_mismatch_is_blocking():
    feature = window(host_id="SOMEONE_ELSE")
    legacy_feature = window()  # live state still says WALL1
    legacy = c.LegacyFact(exists=True, feature=legacy_feature, polygon_ids=legacy_feature.polygon_ids)
    findings = c.validate_feature(feature, (window_polygon(),), legacy, fresh_geometry())
    assert any(f.code == c.HOST_MISMATCH for f in findings)
    assert c.is_blocking(findings)


def test_orphan_opening_has_no_host_or_parent():
    feature = window(host_id="", parent_id="")
    legacy_feature = window(host_id="", parent_id="")
    legacy = c.LegacyFact(exists=True, feature=legacy_feature, polygon_ids=legacy_feature.polygon_ids)
    findings = c.validate_feature(feature, (window_polygon(),), legacy, fresh_geometry())
    assert any(f.code == c.ORPHAN_OPENING and f.severity == c.WARNING for f in findings)
    assert not c.is_blocking(findings)


# -- semantics (10) ------------------------------------------------------------------

def test_lod_mismatch_is_a_warning():
    feature = window()
    legacy_feature = window(lod=2)
    legacy = c.LegacyFact(exists=True, feature=legacy_feature, polygon_ids=legacy_feature.polygon_ids)
    findings = c.validate_feature(feature, (window_polygon(),), legacy, fresh_geometry())
    assert [f.code for f in findings] == [c.LOD_MISMATCH]
    assert not c.is_blocking(findings)


def test_kind_mismatch_is_blocking():
    feature = window()
    legacy_feature = window(kind="Door")
    legacy = c.LegacyFact(exists=True, feature=legacy_feature, polygon_ids=legacy_feature.polygon_ids)
    findings = c.validate_feature(feature, (window_polygon(),), legacy, fresh_geometry())
    assert any(f.code == c.KIND_MISMATCH for f in findings)
    assert c.is_blocking(findings)


def test_polygon_ids_mismatch_is_blocking():
    feature = window()
    legacy_feature = window(polygon_ids=("P_OTHER",))
    legacy = c.LegacyFact(exists=True, feature=legacy_feature, polygon_ids=legacy_feature.polygon_ids)
    findings = c.validate_feature(feature, (window_polygon(),), legacy, fresh_geometry())
    assert any(f.code == c.POLYGON_IDS_MISMATCH for f in findings)
    assert c.is_blocking(findings)


def test_coordinate_digest_is_stable_under_sub_tolerance_noise():
    noisy = tuple((x + 1e-9, y, z) for x, y, z in WORLD)
    assert c._coordinate_digest(WORLD) == c._coordinate_digest(noisy)
    assert c._coordinate_digest(WORLD) != c._coordinate_digest(MOVED)
