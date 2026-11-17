"""CAC-P5.1: record-shaped semantic IR node types (PolygonNode, extended
RingNode/OpeningNode/SurfaceNode). Pure Python, no bpy, no Blender object.

Scope: node construction, round-trip serialization, and deterministic
ordering only - not a records->IR traversal (that is P5.2's
``domain/ir_adapter.py`` work) and not export-time geometry (CRS/origin
transform, canonicalization - both explicitly P5.2+ per the module
docstring in ``shared/semantic_tree.py``).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.semantic_tree import (  # noqa: E402
    OpeningNode, PolygonNode, RingNode, SurfaceNode,
    opening_sort_key, polygon_sort_key, ring_sort_key,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
RING_B = ((2.0, 0.5, 0.0), (3.0, 0.5, 0.0), (3.0, 1.5, 0.0), (2.0, 1.5, 0.0))


def _wall_with_two_windows():
    """The representative case from the P5.1 task brief:

    WallSurface
      exterior
      Ring A  (paired with Window A)
      Ring B  (paired with Window B)
    Window A -> host WallSurface
    Window B -> host WallSurface
    """

    exterior = RingNode(ring_id="P_WALL_0_", coords=WORLD_EXT)
    ring_a = RingNode(
        ring_id="P_WALL_1_", polygon_id="P_WALL", coords=RING_A, opening_link="LINK_A",
    )
    ring_b = RingNode(
        ring_id="P_WALL_2_", polygon_id="P_WALL", coords=RING_B, opening_link="LINK_B",
    )
    wall_polygon = PolygonNode(
        polygon_id="P_WALL", exterior=exterior, interiors=(ring_a, ring_b),
    )
    window_a = OpeningNode(
        gml_id="WIN_A", semantic_type="Window", host_id="WALL1", parent_id="WALL1",
        opening_link="LINK_A", lod=3, lod_origin="authored",
    )
    window_b = OpeningNode(
        gml_id="WIN_B", semantic_type="Window", host_id="WALL1", parent_id="WALL1",
        opening_link="LINK_B", lod=3, lod_origin="authored",
    )
    wall_surface = SurfaceNode(
        gml_id="WALL1", semantic_type="WallSurface", parent_id="BLD1", lod=3,
        polygons=(wall_polygon,), openings=(window_a, window_b),
    )
    return wall_surface, wall_polygon, window_a, window_b


# 1. polygon node with exterior ring -----------------------------------------


def test_polygon_node_with_exterior_ring():
    exterior = RingNode(ring_id="P_0_", coords=WORLD_EXT)
    polygon = PolygonNode(polygon_id="P", exterior=exterior)
    assert polygon.exterior.ring_id == "P_0_"
    assert polygon.exterior.coords == WORLD_EXT
    assert polygon.interiors == ()


# 2. polygon node with multiple interior rings -------------------------------


def test_polygon_node_with_multiple_interior_rings():
    _, wall_polygon, _, _ = _wall_with_two_windows()
    assert len(wall_polygon.interiors) == 2
    assert {r.ring_id for r in wall_polygon.interiors} == {"P_WALL_1_", "P_WALL_2_"}
    assert {r.opening_link for r in wall_polygon.interiors} == {"LINK_A", "LINK_B"}


# 3. committed coordinates preserved exactly ---------------------------------


def test_committed_coordinates_preserved_exactly():
    ring = RingNode(ring_id="P_1_", coords=RING_A)
    assert ring.coords == RING_A
    assert all(isinstance(c, tuple) for c in ring.coords)
    # No transform, no reordering, no rounding: the exact tuple survives.
    assert ring.coords[0] == (0.5, 0.5, 0.0)


# 4. LoD preserved ------------------------------------------------------------


def test_lod_preserved_on_surface_and_opening():
    wall_surface, _, window_a, _ = _wall_with_two_windows()
    assert wall_surface.lod == 3
    assert window_a.lod == 3
    assert window_a.lod_origin == "authored"


# 5. feature gml_id preserved -------------------------------------------------


def test_feature_gml_id_preserved():
    _, _, window_a, window_b = _wall_with_two_windows()
    assert window_a.gml_id == "WIN_A"
    assert window_b.gml_id == "WIN_B"


# 6. host/parent relationship represented explicitly -------------------------


def test_host_and_parent_relationship_represented_explicitly():
    wall_surface, _, window_a, window_b = _wall_with_two_windows()
    # Explicit fields (PLAN.md P5.0 decision 2: never geometry matching) ...
    assert window_a.host_id == "WALL1"
    assert window_a.parent_id == "WALL1"
    assert window_b.host_id == "WALL1"
    # ... which must also agree with the structural nesting (tree position),
    # not merely exist alongside it.
    assert window_a in wall_surface.openings
    assert window_b in wall_surface.openings
    assert wall_surface.gml_id == window_a.host_id == window_b.host_id


def test_opening_ring_pairing_is_by_opening_link_not_geometry():
    wall_surface, wall_polygon, window_a, window_b = _wall_with_two_windows()
    # The interior ring paired with window_a is found by matching
    # opening_link values - never by comparing ring coordinates/shape to the
    # opening's own geometry (there is none stored on OpeningNode.rings here
    # by design - PLAN.md P5.0: "explicit host relation, never geometry
    # matching").
    ring_for_a = next(r for r in wall_polygon.interiors if r.opening_link == window_a.opening_link)
    ring_for_b = next(r for r in wall_polygon.interiors if r.opening_link == window_b.opening_link)
    assert ring_for_a.ring_id == "P_WALL_1_"
    assert ring_for_b.ring_id == "P_WALL_2_"
    assert ring_for_a.coords == RING_A
    assert ring_for_b.coords == RING_B


# 7. deterministic polygon ordering ------------------------------------------


def test_deterministic_polygon_ordering():
    p_a = PolygonNode(polygon_id="P_B", exterior=RingNode(ring_id="P_B_0_"))
    p_b = PolygonNode(polygon_id="P_A", exterior=RingNode(ring_id="P_A_0_"))
    p_c = PolygonNode(polygon_id="P_C", exterior=RingNode(ring_id="P_C_0_"))
    scrambled = (p_a, p_c, p_b)
    canonical = tuple(sorted(scrambled, key=polygon_sort_key))
    assert [p.polygon_id for p in canonical] == ["P_A", "P_B", "P_C"]
    # A second, independently-scrambled ordering of the same logical set
    # produces the identical canonical tuple.
    other_scramble = (p_c, p_b, p_a)
    assert tuple(sorted(other_scramble, key=polygon_sort_key)) == canonical


# 8. deterministic ring ordering ----------------------------------------------


def test_deterministic_ring_ordering():
    _, wall_polygon, _, _ = _wall_with_two_windows()
    scrambled = tuple(reversed(wall_polygon.interiors))
    canonical = tuple(sorted(scrambled, key=ring_sort_key))
    assert [r.ring_id for r in canonical] == ["P_WALL_1_", "P_WALL_2_"]
    assert tuple(sorted(wall_polygon.interiors, key=ring_sort_key)) == canonical


# 9. deterministic child/opening ordering -------------------------------------


def test_deterministic_opening_ordering():
    _, _, window_a, window_b = _wall_with_two_windows()
    scrambled = (window_b, window_a)
    canonical = tuple(sorted(scrambled, key=opening_sort_key))
    assert [o.gml_id for o in canonical] == ["WIN_A", "WIN_B"]
    other_scramble = (window_a, window_b)
    assert tuple(sorted(other_scramble, key=opening_sort_key)) == canonical


# 10. equality / canonical representation is stable ---------------------------


def test_equality_and_round_trip_serialization_is_stable():
    wall_surface, _, _, _ = _wall_with_two_windows()
    again, _, _, _ = _wall_with_two_windows()
    # Two independently-built trees for the same logical data compare equal
    # (frozen dataclass equality, tuple fields - no Python object identity
    # involved).
    assert wall_surface == again
    assert wall_surface is not again

    as_dict = wall_surface.to_dict()
    round_tripped = SurfaceNode.from_dict(as_dict)
    assert round_tripped == wall_surface
    assert round_tripped.to_dict() == as_dict

    # Sorting children before construction is what makes two builders that
    # discover openings in a different order still produce equal trees.
    reordered = SurfaceNode(
        gml_id=wall_surface.gml_id,
        semantic_type=wall_surface.semantic_type,
        parent_id=wall_surface.parent_id,
        lod=wall_surface.lod,
        polygons=wall_surface.polygons,
        openings=tuple(sorted(reversed(wall_surface.openings), key=opening_sort_key)),
    )
    assert reordered == wall_surface
