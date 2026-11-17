"""Record-level Window delete lifecycle (CAC-P3.4). No Blender."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.delete_lifecycle import (  # noqa: E402
    DeleteFeatureError, plan_window_delete,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))


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


def window_polygon(gml_id="WIN1", polygon_id="P_WIN"):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=gml_id, geometry_state="committed",
        exterior=RingRecord(ring_id=f"{polygon_id}_0_", role="exterior", coords=WORLD),
    )


def wall_polygon(opening_links=("LINK1",), polygon_id="P_WALL", feature_id="WALL1"):
    rings = tuple(
        RingRecord(ring_id=f"{polygon_id}_{i}_", role="interior", coords=WORLD, opening_link=link)
        for i, link in enumerate(opening_links, start=1)
    )
    return PolygonRecord(
        polygon_id=polygon_id, feature_id=feature_id, geometry_state="committed",
        exterior=RingRecord(ring_id=f"{polygon_id}_0_", role="exterior", coords=WORLD),
        interiors=rings,
    )


def window_records(feature=None, polygon=None):
    return RecordSet(features=(feature or window(),), polygons=(polygon or window_polygon(),))


def wall_records(feature=None, polygon=None):
    return RecordSet(features=(feature or wall(),), polygons=(polygon or wall_polygon(),))


def test_happy_path_removes_only_the_matching_ring():
    window_feature, wall_feature, new_wall_records, opening_link = plan_window_delete(
        "WIN1", window_records(), "WALL1", wall_records(),
    )
    assert window_feature.gml_id == "WIN1"
    assert wall_feature.gml_id == "WALL1"
    assert opening_link == "LINK1"
    new_polygon = new_wall_records.polygon("P_WALL")
    assert new_polygon.interiors == ()
    assert new_wall_records.validate() == []


def test_second_window_on_same_wall_is_left_intact():
    _, _, new_wall_records, _ = plan_window_delete(
        "WIN1", window_records(), "WALL1",
        wall_records(polygon=wall_polygon(opening_links=("LINK1", "LINK2"))),
    )
    remaining = new_wall_records.polygon("P_WALL").interiors
    assert [r.opening_link for r in remaining] == ["LINK2"]


def test_unknown_gml_id_refuses_cleanly():
    with pytest.raises(DeleteFeatureError, match="no authoring record"):
        plan_window_delete("WIN1", None, "WALL1", wall_records())


def test_object_exists_but_record_names_someone_else():
    with pytest.raises(DeleteFeatureError, match="does not name"):
        plan_window_delete("WIN1", window_records(feature=window(gml_id="OTHER")), "WALL1", wall_records())


def test_non_window_kind_is_refused():
    with pytest.raises(DeleteFeatureError, match="Window only"):
        plan_window_delete("WIN1", window_records(feature=window(kind="Door")), "WALL1", wall_records())


def test_missing_opening_link_is_refused():
    with pytest.raises(DeleteFeatureError, match="opening_link"):
        plan_window_delete("WIN1", window_records(feature=window(opening_link="")), "WALL1", wall_records())


def test_missing_host_is_refused():
    with pytest.raises(DeleteFeatureError, match="no host recorded"):
        plan_window_delete("WIN1", window_records(feature=window(host_id="")), "", None)


def test_missing_host_object_is_refused():
    with pytest.raises(DeleteFeatureError, match="no authoring record"):
        plan_window_delete("WIN1", window_records(), "WALL1", None)


def test_wall_record_names_someone_else():
    with pytest.raises(DeleteFeatureError, match="does not name"):
        plan_window_delete(
            "WIN1", window_records(), "WALL1", wall_records(feature=wall(gml_id="OTHER")),
        )


def test_wall_missing_expected_ring_is_refused():
    with pytest.raises(DeleteFeatureError, match="no RingRecord"):
        plan_window_delete(
            "WIN1", window_records(), "WALL1",
            wall_records(polygon=wall_polygon(opening_links=("LINK2",))),
        )


def test_broken_relation_never_mutates_the_input_record_sets():
    original = wall_records()
    with pytest.raises(DeleteFeatureError):
        plan_window_delete("WIN1", window_records(feature=window(kind="Door")), "WALL1", original)
    assert original.polygon("P_WALL").interiors[0].opening_link == "LINK1"
