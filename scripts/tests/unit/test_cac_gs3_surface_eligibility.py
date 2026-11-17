"""GS.3.1: per-surface IR-exportability predicate. Pure Python, no bpy.
Generalized to N hosted Windows by P3b.2
(docs/agent/authoring-core/phase3b-design-note.md §7).

Scope: ``shared/authoring_core/domain/gs_surface_eligibility.py::
can_export_surface_from_ir`` only (docs/agent/authoring-core/gs-design-note.md
§4 item 3). No dispatch wiring, no flag plumbing, no export integration -
see that module's docstring.
"""
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import gs_surface_eligibility as gse  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WALL_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
WIN_RING = ((1.0, 1.0, 0.0), (1.0, 2.0, 0.0), (2.0, 2.0, 0.0), (2.0, 1.0, 0.0))
WIN_RING2 = ((2.5, 1.0, 0.0), (2.5, 2.0, 0.0), (3.5, 2.0, 0.0), (3.5, 1.0, 0.0))
WIN_EXT = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))


def _wall_polygon(interiors=(), committed=True, exterior=WALL_EXT):
    return PolygonRecord(
        polygon_id="P_WALL", feature_id="WALL1",
        exterior=RingRecord(ring_id="P_WALL_0_", role="exterior", coords=exterior),
        interiors=interiors,
        geometry_state="committed" if committed else "ids_only",
    )


def _window_polygon(committed=True, poly_id="P_WIN"):
    return PolygonRecord(
        polygon_id=poly_id, feature_id="WIN1",
        exterior=RingRecord(ring_id=f"{poly_id}_0_", role="exterior", coords=WIN_EXT),
        geometry_state="committed" if committed else "ids_only",
    )


def _record_set_with_window(
    opening_link="LINK1", window_opening_link="LINK1",
    window_parent="WALL1", window_host="WALL1",
    window_generator="facade.window.rect", window_polygon_ids=("P_WIN",),
    extra_window_polygons=(), window_committed=True,
):
    wall_ring = RingRecord(ring_id="P_WALL_1_", role="interior", coords=WIN_RING, opening_link=opening_link)
    wall_polygon = _wall_polygon(interiors=(wall_ring,))
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    window_polygons = [_window_polygon(committed=window_committed, poly_id=window_polygon_ids[0])]
    window_polygons.extend(extra_window_polygons)
    window = FeatureRecord(
        gml_id="WIN1", kind="Window", parent_id=window_parent, host_id=window_host,
        opening_link=window_opening_link, generator=window_generator,
        polygon_ids=tuple(p.polygon_id for p in window_polygons),
    )
    return RecordSet(features=(wall, window), polygons=(wall_polygon, *window_polygons))


def _record_set_windowless():
    wall_polygon = _wall_polygon()
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    return RecordSet(features=(wall,), polygons=(wall_polygon,))


def test_eligible_with_one_hosted_window():
    result = gse.can_export_surface_from_ir("WALL1", _record_set_with_window())
    assert result.eligible
    assert result.reason == gse.GS_SURFACE_ELIGIBLE
    assert result.window_gml_ids == ("WIN1",)


def test_eligible_windowless_wall():
    result = gse.can_export_surface_from_ir("WALL1", _record_set_windowless())
    assert result.eligible
    assert result.window_gml_ids == ()


def test_no_surface_record():
    result = gse.can_export_surface_from_ir("MISSING", RecordSet())
    assert not result.eligible
    assert result.reason == gse.NO_SURFACE_RECORD


def test_not_a_wall_surface():
    record_set = RecordSet(features=(FeatureRecord(gml_id="ROOF1", kind="RoofSurface"),))
    result = gse.can_export_surface_from_ir("ROOF1", record_set)
    assert not result.eligible
    assert result.reason == gse.NOT_A_WALL_SURFACE


def test_wall_polygon_count_zero():
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=())
    result = gse.can_export_surface_from_ir("WALL1", RecordSet(features=(wall,)))
    assert not result.eligible
    assert result.reason == gse.WALL_POLYGON_COUNT


def test_wall_not_committed():
    wall_polygon = _wall_polygon(committed=False)
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    result = gse.can_export_surface_from_ir("WALL1", RecordSet(features=(wall,), polygons=(wall_polygon,)))
    assert not result.eligible
    assert result.reason == gse.WALL_NOT_COMMITTED


def test_wall_degenerate_exterior():
    wall_polygon = _wall_polygon(exterior=((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    result = gse.can_export_surface_from_ir("WALL1", RecordSet(features=(wall,), polygons=(wall_polygon,)))
    assert not result.eligible
    assert result.reason == gse.WALL_DEGENERATE_EXTERIOR


def _add_second_window(record_set, second_ring_opening_link="LINK2"):
    """Add a second hosted Window ("WIN2") to a one-Window record set,
    plus a matching second wall interior ring (unless the caller wants a
    ring/opening_link mismatch, hence the parameter)."""
    second_ring = RingRecord(ring_id="P_WALL_2_", role="interior", coords=WIN_RING2, opening_link=second_ring_opening_link)
    wall_polygon = record_set.polygon("P_WALL")
    wall_polygon = replace(wall_polygon, interiors=wall_polygon.interiors + (second_ring,))
    record_set = record_set.with_polygon(wall_polygon)
    second_window = replace(record_set.feature("WIN1"), gml_id="WIN2", opening_link="LINK2")
    second_polygon = replace(record_set.polygon("P_WIN"), polygon_id="P_WIN2", feature_id="WIN2")
    return record_set.with_feature(second_window).with_polygon(second_polygon)


def test_eligible_with_two_hosted_windows():
    record_set = _add_second_window(_record_set_with_window())
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert result.eligible
    assert result.reason == gse.GS_SURFACE_ELIGIBLE
    assert result.window_gml_ids == ("WIN1", "WIN2")


def test_multiple_hosted_windows_without_matching_ring_count_is_ineligible():
    record_set = _record_set_with_window()
    second_window = replace(record_set.feature("WIN1"), gml_id="WIN2", opening_link="LINK2")
    second_polygon = replace(record_set.polygon("P_WIN"), polygon_id="P_WIN2", feature_id="WIN2")
    record_set = record_set.with_feature(second_window).with_polygon(second_polygon)
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WALL_RING_WINDOW_MISMATCH


def test_duplicate_ring_opening_link_is_ineligible():
    record_set = _add_second_window(_record_set_with_window(), second_ring_opening_link="LINK1")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.DUPLICATE_RING_OPENING_LINK


def test_second_window_opening_link_mismatch_is_ineligible():
    record_set = _add_second_window(_record_set_with_window(), second_ring_opening_link="OTHER_LINK")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.OPENING_LINK_MISMATCH


def test_window_parent_mismatch():
    record_set = _record_set_with_window(window_parent="OTHER_WALL")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WINDOW_PARENT_MISMATCH


def test_window_depth_authored_multi_polygon_rejected():
    extra = _window_polygon(poly_id="P_WIN_SIDE")
    record_set = _record_set_with_window(
        window_polygon_ids=("P_WIN",), extra_window_polygons=(extra,),
    )
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WINDOW_POLYGON_COUNT


def test_window_not_committed():
    record_set = _record_set_with_window(window_committed=False)
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WINDOW_NOT_COMMITTED


def test_window_not_rectangular_generator():
    record_set = _record_set_with_window(window_generator="facade.window.arch")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WINDOW_NOT_RECTANGULAR


def test_wall_ring_window_mismatch_window_without_ring():
    wall_polygon = _wall_polygon()  # no interior ring
    wall = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    window_polygon = _window_polygon()
    window = FeatureRecord(
        gml_id="WIN1", kind="Window", parent_id="WALL1", host_id="WALL1",
        opening_link="LINK1", generator="facade.window.rect", polygon_ids=("P_WIN",),
    )
    record_set = RecordSet(features=(wall, window), polygons=(wall_polygon, window_polygon))
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WALL_RING_WINDOW_MISMATCH


def test_wall_ring_window_mismatch_ring_without_window():
    result = gse.can_export_surface_from_ir("WALL1", _record_set_windowless())
    assert result.eligible  # sanity: baseline is eligible
    wall_ring = RingRecord(ring_id="P_WALL_1_", role="interior", coords=WIN_RING, opening_link="ORPHAN")
    record_set = _record_set_windowless()
    wall_polygon = replace(record_set.polygon("P_WALL"), interiors=(wall_ring,))
    record_set = record_set.with_polygon(wall_polygon)
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.WALL_RING_WINDOW_MISMATCH


def test_opening_link_mismatch():
    record_set = _record_set_with_window(opening_link="LINK1", window_opening_link="LINK2")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.OPENING_LINK_MISMATCH


def test_opening_link_missing_on_window():
    record_set = _record_set_with_window(window_opening_link="")
    result = gse.can_export_surface_from_ir("WALL1", record_set)
    assert not result.eligible
    assert result.reason == gse.OPENING_LINK_MISMATCH
