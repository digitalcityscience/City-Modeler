# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Explicit single-surface edit/commit lifecycle for RoofSurface - pure parts
only (CAC-P6.2).

The Blender-integrated begin/commit/cancel flow needs a real ``bpy`` object
(registry resolution, mesh vertex writes, ``consistency_view.geometry_fact``)
and is exercised by ``scripts/headless/test_cac_p6_2_roof_edit_session.py``,
same split ``test_cac_edit_session.py`` already established for the Window+
Wall pair transaction. This file covers only the parts of
``shared/authoring_core/blender/roof_edit_session.py`` that do not touch
Blender: dataclass defaults, the no-session error paths, the pure relation/
committed-geometry-snapshot helpers, and kind/refusal guards.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import roof_edit_session as rs  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

EXT = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0))
HOLE = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))


def test_active_session_none_when_absent():
    assert rs.active_session("NO-SUCH-GML-ID") is None


def test_roof_edit_session_dataclass_defaults():
    session = rs.RoofEditSession(gml_id="ROOF1", object_name="Roof", checksum="deadbeef")
    assert session.mesh_state == ((), ())


def test_commit_result_reused_from_edit_session():
    from shared.authoring_core.blender.edit_session import CommitResult

    assert rs.CommitResult is CommitResult


def test_commit_without_begin_raises():
    try:
        rs.commit("NO-SUCH-SESSION")
    except rs.RoofEditSessionError as exc:
        assert "no open edit session" in str(exc)
    else:
        raise AssertionError("expected RoofEditSessionError")


def test_cancel_without_begin_raises():
    try:
        rs.cancel("NO-SUCH-SESSION")
    except rs.RoofEditSessionError as exc:
        assert "no open edit session" in str(exc)
    else:
        raise AssertionError("expected RoofEditSessionError")


def test_begin_requires_gml_id():
    try:
        rs.begin("")
    except rs.RoofEditSessionError as exc:
        assert "gml_id" in str(exc)
    else:
        raise AssertionError("expected RoofEditSessionError")


def test_relations_excludes_window_only_fields():
    """P6.2: this module never reads/writes host_id/opening_link - the
    checksum relation tuple must not depend on them."""
    feature = FeatureRecord(
        gml_id="ROOF1", kind="RoofSurface", parent_id="BLDG1", owner_id="BLDG1",
        host_id="SHOULD-BE-IGNORED", opening_link="SHOULD-BE-IGNORED",
    )
    relations = dict(rs._relations(feature))
    assert relations == {"parent_id": "BLDG1", "owner_id": "BLDG1", "kind": "RoofSurface"}


def _polygon(polygon_id="P_ROOF", interiors=(), exterior_coords=EXT, state="committed"):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id="ROOF1",
        exterior=RingRecord(ring_id=f"{polygon_id}_0_", role="exterior", coords=exterior_coords),
        interiors=tuple(interiors), geometry_state=state,
    )


def test_committed_geometry_snapshot_reads_exterior_and_interior_from_storage():
    ring = RingRecord(ring_id="P_ROOF_1_", role="interior", coords=HOLE)
    polygon = _polygon(interiors=[ring])
    feature = FeatureRecord(gml_id="ROOF1", kind="RoofSurface", polygon_ids=("P_ROOF",))
    record_set = RecordSet(features=(feature,), polygons=(polygon,))

    snapshot = rs._committed_geometry_snapshot(feature, record_set)
    assert snapshot.polygons == {"P_ROOF": EXT}
    assert snapshot.rings == {"P_ROOF#0": HOLE}


def test_committed_geometry_snapshot_ignores_polygons_outside_feature():
    other = _polygon(polygon_id="P_OTHER")
    feature = FeatureRecord(gml_id="ROOF1", kind="RoofSurface", polygon_ids=("P_ROOF",))
    record_set = RecordSet(features=(feature,), polygons=(_polygon(), other))

    snapshot = rs._committed_geometry_snapshot(feature, record_set)
    assert set(snapshot.polygons) == {"P_ROOF"}


def test_resolve_roof_refuses_non_roof_kind():
    """A pure guard-level check: ``_resolve_roof`` never even reaches a
    Blender read for a feature whose stored kind isn't RoofSurface - proven
    here with a minimal stand-in ``registry.resolve``/``storage.read_record``
    so no bpy is required."""
    import types

    class _FakeObj:
        type = "MESH"

    feature = FeatureRecord(gml_id="WIN1", kind="Window")
    record_set = RecordSet(features=(feature,))

    real_resolve, real_read = rs.registry.resolve, rs.storage.read_record
    rs.registry.resolve = lambda gml_id, name="": _FakeObj()
    rs.storage.read_record = lambda obj: record_set
    try:
        try:
            rs._resolve_roof("WIN1")
        except rs.RoofEditSessionError as exc:
            assert "RoofSurface" in str(exc)
        else:
            raise AssertionError("expected RoofEditSessionError")
    finally:
        rs.registry.resolve = real_resolve
        rs.storage.read_record = real_read


def test_resolve_roof_refuses_when_not_record_backed():
    class _FakeObj:
        type = "MESH"

    real_resolve, real_read = rs.registry.resolve, rs.storage.read_record
    rs.registry.resolve = lambda gml_id, name="": _FakeObj()
    rs.storage.read_record = lambda obj: None
    try:
        try:
            rs._resolve_roof("ROOF1")
        except rs.RoofEditSessionError as exc:
            assert "no authoring record" in str(exc)
        else:
            raise AssertionError("expected RoofEditSessionError")
    finally:
        rs.registry.resolve = real_resolve
        rs.storage.read_record = real_read
