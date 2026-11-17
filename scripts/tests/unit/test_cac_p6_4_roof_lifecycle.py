# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record-level RoofSurface delete/duplicate-init lifecycle - pure parts only
(CAC-P6.4).

The Blender-integrated delete/duplicate flow (object removal, display
regeneration, material remap, registry, undo/redo, save/reload) needs a real
``bpy`` object and is exercised by
``scripts/headless/test_cac_p6_4_roof_lifecycle.py``. This file covers only
``shared/authoring_core/domain/roof_lifecycle.py`` - the pure planning layer
with no Blender dependency at all.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import roof_lifecycle as rl  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

EXT = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0))
HOLE = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))


def _simple_roof():
    feature = FeatureRecord(
        gml_id="ROOF1", kind="RoofSurface", parent_id="BLDG1", owner_id="BLDG1",
        lod=3, lod_origin="authored", provenance="promoted", generator="promote_building",
        params={"note": "x"}, polygon_ids=("POLY1",),
    )
    polygon = PolygonRecord(
        polygon_id="POLY1", feature_id="ROOF1", lod=3,
        exterior=RingRecord(ring_id="POLY1_0_", role="exterior", coords=EXT),
        geometry_state="committed",
    )
    return RecordSet(features=(feature,), polygons=(polygon,))


def _hole_bearing_roof():
    feature = FeatureRecord(
        gml_id="ROOF2", kind="RoofSurface", parent_id="BLDG1", owner_id="BLDG1",
        lod=3, lod_origin="authored", provenance="promoted", polygon_ids=("POLY2",),
    )
    polygon = PolygonRecord(
        polygon_id="POLY2", feature_id="ROOF2", lod=3,
        exterior=RingRecord(ring_id="POLY2_0_", role="exterior", coords=EXT),
        interiors=(RingRecord(ring_id="POLY2_1_", role="interior", coords=HOLE),),
        geometry_state="committed",
    )
    return RecordSet(features=(feature,), polygons=(polygon,))


# --------------------------------------------------------------------------
# plan_roof_delete
# --------------------------------------------------------------------------

def test_plan_roof_delete_returns_feature():
    record_set = _simple_roof()
    feature = rl.plan_roof_delete("ROOF1", record_set)
    assert feature.gml_id == "ROOF1"
    assert feature.kind == "RoofSurface"


def test_plan_roof_delete_requires_gml_id():
    try:
        rl.plan_roof_delete("", _simple_roof())
    except rl.RoofLifecycleError as exc:
        assert "gml_id" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


def test_plan_roof_delete_refuses_absent_record():
    try:
        rl.plan_roof_delete("ROOF1", None)
    except rl.RoofLifecycleError as exc:
        assert "no authoring record" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


def test_plan_roof_delete_refuses_unnamed_record():
    try:
        rl.plan_roof_delete("NOT-IN-RECORD", _simple_roof())
    except rl.RoofLifecycleError as exc:
        assert "already deleted" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


def test_plan_roof_delete_refuses_wrong_kind():
    record_set = RecordSet(features=(FeatureRecord(gml_id="WIN1", kind="Window"),))
    try:
        rl.plan_roof_delete("WIN1", record_set)
    except rl.RoofLifecycleError as exc:
        assert "RoofSurface only" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


# --------------------------------------------------------------------------
# plan_roof_duplicate
# --------------------------------------------------------------------------

def test_plan_roof_duplicate_mints_fresh_feature_id():
    new_record_set, new_gml_id, polygon_id_map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    assert new_gml_id != "ROOF1"
    assert new_record_set.feature(new_gml_id) is not None
    assert new_record_set.feature("ROOF1") is None


def test_plan_roof_duplicate_mints_fresh_polygon_and_ring_ids():
    new_record_set, new_gml_id, polygon_id_map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    new_polygon = new_record_set.polygon(polygon_id_map["POLY1"])
    assert polygon_id_map["POLY1"] != "POLY1"
    assert new_polygon.exterior.ring_id != "POLY1_0_"
    assert new_polygon.feature_id == new_gml_id


def test_plan_roof_duplicate_preserves_geometry_verbatim():
    new_record_set, _new_gml_id, polygon_id_map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    new_polygon = new_record_set.polygon(polygon_id_map["POLY1"])
    assert new_polygon.exterior.coords == EXT
    assert new_polygon.geometry_state == "committed"


def test_plan_roof_duplicate_preserves_lod_and_owner():
    source = _simple_roof().feature("ROOF1")
    new_record_set, new_gml_id, _map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    new_feature = new_record_set.feature(new_gml_id)
    assert new_feature.lod == source.lod
    assert new_feature.lod_origin == source.lod_origin
    assert new_feature.parent_id == source.parent_id
    assert new_feature.owner_id == source.owner_id
    assert new_feature.provenance == source.provenance
    assert new_feature.params == source.params


def test_plan_roof_duplicate_restamps_generator():
    _new_record_set, new_gml_id, _map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    assert _new_record_set.feature(new_gml_id).generator == "native_duplicate"


def test_plan_roof_duplicate_hole_bearing_preserves_topology_with_new_ids():
    source_record_set = _hole_bearing_roof()
    new_record_set, new_gml_id, polygon_id_map = rl.plan_roof_duplicate("ROOF2", source_record_set)
    new_polygon = new_record_set.polygon(polygon_id_map["POLY2"])
    source_polygon = source_record_set.polygon("POLY2")

    assert len(new_polygon.interiors) == len(source_polygon.interiors) == 1
    assert new_polygon.exterior.coords == source_polygon.exterior.coords
    assert new_polygon.interiors[0].coords == source_polygon.interiors[0].coords
    assert new_polygon.interiors[0].ring_id != source_polygon.interiors[0].ring_id
    assert new_polygon.geometry_state == "committed"


def test_plan_roof_duplicate_result_validates_clean():
    new_record_set, _new_gml_id, _map = rl.plan_roof_duplicate("ROOF2", _hole_bearing_roof())
    assert new_record_set.validate() == []


def test_plan_roof_duplicate_ids_are_deterministic_with_injected_factory():
    import uuid

    counter = {"n": 0}

    def factory():
        counter["n"] += 1
        return uuid.UUID(int=counter["n"])

    new_record_set, new_gml_id, polygon_id_map = rl.plan_roof_duplicate(
        "ROOF1", _simple_roof(), uuid_factory=factory,
    )
    assert new_gml_id == f"ID_{uuid.UUID(int=1).hex}"
    assert polygon_id_map["POLY1"] == f"ID_{uuid.UUID(int=2).hex}"


def test_plan_roof_duplicate_refuses_absent_record():
    try:
        rl.plan_roof_duplicate("ROOF1", None)
    except rl.RoofLifecycleError as exc:
        assert "no authoring record" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


def test_plan_roof_duplicate_refuses_wrong_kind():
    record_set = RecordSet(features=(FeatureRecord(gml_id="WIN1", kind="Window"),))
    try:
        rl.plan_roof_duplicate("WIN1", record_set)
    except rl.RoofLifecycleError as exc:
        assert "RoofSurface only" in str(exc)
    else:
        raise AssertionError("expected RoofLifecycleError")


def test_plan_roof_duplicate_never_reuses_source_polygon_id():
    """D-4: source semantic ids != duplicate semantic ids, at every level."""
    new_record_set, new_gml_id, polygon_id_map = rl.plan_roof_duplicate("ROOF1", _simple_roof())
    source_ids = {"ROOF1", "POLY1", "POLY1_0_"}
    new_ids = {new_gml_id, polygon_id_map["POLY1"], new_record_set.polygon(polygon_id_map["POLY1"]).exterior.ring_id}
    assert source_ids.isdisjoint(new_ids)
