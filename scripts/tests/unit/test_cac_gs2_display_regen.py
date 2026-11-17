"""GS.2: post-commit display regeneration orchestration. Pure Python, no bpy.

Scope: ``shared/authoring_core/blender/display_regen.py::plan_regeneration``
only (docs/agent/authoring-core/gs-design-note.md S4 item 2) - the bpy-free
decision of which surfaces get a rebuilt ``DisplayMesh``. The bpy-bound half
(``regenerate_committed_display``, writing to auxiliary display objects) is
covered by the headless test instead (needs a live Blender process).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender.display_builder import DisplayBuildError  # noqa: E402
from shared.authoring_core.blender.display_regen import plan_regeneration  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WALL_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
WIN_RING = ((1.0, 1.0, 0.0), (1.0, 2.0, 0.0), (2.0, 2.0, 0.0), (2.0, 1.0, 0.0))
WIN_EXT = WIN_RING  # the Window's own polygon; same rectangle it cuts out of the wall


def wall_record_set():
    polygon = PolygonRecord(
        polygon_id="P_WALL",
        feature_id="WALL1",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=WALL_EXT),
        interiors=(RingRecord(ring_id="P_WALL_1_", role="interior", coords=WIN_RING),),
        geometry_state="committed",
    )
    wall = FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface", polygon_ids=("P_WALL",),
    )
    return RecordSet(features=(wall,), polygons=(polygon,))


def window_record_set():
    polygon = PolygonRecord(
        polygon_id="P_WIN",
        feature_id="WIN_A",
        exterior=RingRecord(ring_id="P_WIN_0_", role="exterior", coords=WIN_EXT),
        geometry_state="committed",
    )
    window = FeatureRecord(
        gml_id="WIN_A", kind="Window", semantic_type="Window", host_id="WALL1",
        parent_id="WALL1", opening_link="LINK_A", polygon_ids=("P_WIN",),
    )
    return RecordSet(features=(window,), polygons=(polygon,))


def test_plan_regeneration_builds_wall_then_window():
    planned = plan_regeneration(wall_record_set(), "WALL1", window_record_set(), "WIN_A")

    assert [surface_id for surface_id, _mesh in planned] == ["WALL1", "WIN_A"]
    wall_mesh = planned[0][1]
    window_mesh = planned[1][1]
    assert wall_mesh.vertices[:4] == WALL_EXT
    assert window_mesh.vertices[:4] == WIN_EXT


def test_plan_regeneration_skips_a_surface_with_no_record_set():
    wall_only = plan_regeneration(wall_record_set(), "WALL1", None, "WIN_A")
    assert [surface_id for surface_id, _mesh in wall_only] == ["WALL1"]

    window_only = plan_regeneration(None, "WALL1", window_record_set(), "WIN_A")
    assert [surface_id for surface_id, _mesh in window_only] == ["WIN_A"]

    neither = plan_regeneration(None, "WALL1", None, "WIN_A")
    assert neither == ()


def test_plan_regeneration_skips_a_window_with_more_than_one_polygon():
    """A depth-authored Window (box: one PolygonRecord per face, confirmed
    against a real fixture by the headless test) does not fit
    ``build_surface_display``'s single-polygon contract; it is skipped, not
    raised - the wall's own display still regenerates."""
    box_window = RecordSet(
        features=(
            FeatureRecord(gml_id="WIN_A", kind="Window", polygon_ids=("P_WIN_FRONT", "P_WIN_BACK")),
        ),
        polygons=(
            PolygonRecord(
                polygon_id="P_WIN_FRONT", feature_id="WIN_A",
                exterior=RingRecord(role="exterior", coords=WIN_EXT), geometry_state="committed",
            ),
            PolygonRecord(
                polygon_id="P_WIN_BACK", feature_id="WIN_A",
                exterior=RingRecord(role="exterior", coords=WIN_EXT), geometry_state="committed",
            ),
        ),
    )

    planned = plan_regeneration(wall_record_set(), "WALL1", box_window, "WIN_A")
    assert [surface_id for surface_id, _mesh in planned] == ["WALL1"]


def test_plan_regeneration_propagates_display_build_error():
    """A record set that cannot be built (GS.1's own error cases) surfaces
    as a ``DisplayBuildError``, not silently dropped - GS.2 never swallows a
    genuine build failure (see ``regenerate_committed_display`` docstring)."""
    broken_wall = RecordSet(
        features=(FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",)),),
        polygons=(),  # feature references a polygon that isn't in the set
    )
    with pytest.raises(DisplayBuildError):
        plan_regeneration(broken_wall, "WALL1", None, "WIN_A")
