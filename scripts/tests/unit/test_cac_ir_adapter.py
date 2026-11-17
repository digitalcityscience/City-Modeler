"""Record-derived shadow IR for a Window (CAC-P3.5). No Blender."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain.ir_adapter import (  # noqa: E402
    IRAdapterError, opening_node_from_records,
)
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)
from shared.semantic_tree import OpeningNode, RingNode  # noqa: E402

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


def window_polygon():
    return PolygonRecord(
        polygon_id="P_WIN", feature_id="WIN1", geometry_state="committed",
        exterior=RingRecord(ring_id="P_WIN_0_", role="exterior", coords=WORLD),
    )


def wall_polygon(opening_link="LINK1"):
    ring = RingRecord(ring_id="P_WALL_1_", role="interior", coords=WORLD, opening_link=opening_link)
    return PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=WORLD),
        interiors=(ring,) if opening_link else (),
    )


def merged(window_feature=None, wall_feature=None, wall_poly=None):
    return RecordSet(
        features=(window_feature or window(), wall_feature or wall()),
        polygons=(window_polygon(), wall_poly if wall_poly is not None else wall_polygon()),
    )


def test_happy_path_returns_opening_and_host_ring():
    opening, host_ring, host_gml_id = opening_node_from_records(merged(), "WIN1")
    assert opening == OpeningNode(
        gml_id="WIN1", semantic_type="Window", source_role="", source_element="", schema_version="",
    )
    assert host_ring == RingNode(ring_id="P_WALL_1_", polygon_id="P_WALL")
    assert host_gml_id == "WALL1"


def test_falls_back_to_kind_when_semantic_type_blank():
    opening, _, _ = opening_node_from_records(merged(window_feature=window(semantic_type="")), "WIN1")
    assert opening.semantic_type == "Window"


def test_missing_host_ring_is_none_not_raised():
    opening, host_ring, host_gml_id = opening_node_from_records(
        merged(wall_poly=wall_polygon(opening_link="")), "WIN1",
    )
    assert host_ring is None
    assert host_gml_id == "WALL1"
    assert opening.gml_id == "WIN1"


def test_missing_host_id_gives_no_host_and_no_ring():
    opening, host_ring, host_gml_id = opening_node_from_records(
        merged(window_feature=window(host_id="")), "WIN1",
    )
    assert host_gml_id == ""
    assert host_ring is None


def test_unknown_gml_id_raises():
    with pytest.raises(IRAdapterError, match="no feature"):
        opening_node_from_records(merged(), "NOT_THERE")
