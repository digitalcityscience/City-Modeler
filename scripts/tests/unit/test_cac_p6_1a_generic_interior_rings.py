# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P6.1a: generic interior-ring promotion completeness. No Blender.

``blender/promote_building.py::_commit_polygon`` only committed an interior
ring when it carried a Window's ``opening_link`` (``commit_host_polygon``,
CAC-P4.10). An ordinary interior ring - a hole with no opening behind it (a
RoofSurface skylight cutout is the real fixture that surfaced this, but the
rule is generic, not Roof-specific) - was left ``ids_only`` forever: the
polygon's own exterior was never even attempted, since the old code only
ever committed geometry *together with* a matched opening.

``_commit_polygon`` and its new helper ``_commit_generic_interior_rings``
take a plain ``consistency.GeometryFact`` and plain records - no bpy is
needed to exercise them directly, unlike the rest of ``blender/
promote_building.py`` (see that module's real-Blender counterpart,
``scripts/headless/test_cac_p6_1_roof_promotion.py``, and its own docstring
for why it could not be run in this environment).
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ``blender/promote_building.py`` imports ``blender/consistency_view.py`` ->
# ``shared/ring_opening_sync.py``, which imports real ``bpy``/``mathutils``
# at module scope. ``_commit_polygon`` itself never touches either module -
# it takes plain records and a plain ``consistency.GeometryFact`` - so a
# bare stub (same convention as ``test_ring_opening_sync.py``) is enough to
# import it here without a real Blender.
if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.ModuleType("bpy")
if "mathutils" not in sys.modules:
    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = object
    mathutils.Matrix = object
    sys.modules["mathutils"] = mathutils

from shared.authoring_core.blender.promote_building import _commit_polygon  # noqa: E402
from shared.authoring_core.domain import consistency  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

EXT = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0))
HOLE_A = ((1.0, 1.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0), (1.0, 2.0, 0.0))
HOLE_B = ((5.0, 5.0, 0.0), (6.0, 5.0, 0.0), (6.0, 6.0, 0.0), (5.0, 6.0, 0.0))


def _ring(role="interior", coords=(), ring_id="", opening_link=""):
    return RingRecord(ring_id=ring_id, role=role, coords=coords, opening_link=opening_link)


def _polygon(interiors=(), polygon_id="P_ROOF", exterior_ring_id=""):
    return PolygonRecord(
        polygon_id=polygon_id, feature_id="ROOF1",
        exterior=_ring(role="exterior", ring_id=exterior_ring_id),
        interiors=tuple(interiors),
    )


def _geometry(polygons=None, rings=None, generic_rings=None):
    return consistency.GeometryFact(
        polygons=polygons or {}, rings=rings or {}, generic_rings=generic_rings or {},
    )


def test_exterior_only_polygon_commits_unchanged_from_before():
    polygon = _polygon()
    geometry = _geometry(polygons={"P_ROOF": EXT})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "committed"
    assert out.exterior.coords == EXT


def test_one_generic_interior_ring_commits():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "committed"
    assert out.exterior.coords == EXT
    assert out.interiors[0].coords == HOLE_A


def test_multiple_generic_interior_rings_commit_in_order():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_"), _ring(ring_id="P_ROOF_2_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A, HOLE_B)})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "committed"
    assert out.interiors[0].coords == HOLE_A
    assert out.interiors[1].coords == HOLE_B


def test_generic_interior_ring_commits_without_opening_link():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_", opening_link="")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "committed"
    assert out.interiors[0].opening_link == ""


def test_ring_ids_are_preserved_when_already_present():
    polygon = _polygon(
        interiors=[_ring(ring_id="MY_HOLE_ID")],
        exterior_ring_id="MY_EXTERIOR_ID",
    )
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out.exterior.ring_id == "MY_EXTERIOR_ID"
    assert out.interiors[0].ring_id == "MY_HOLE_ID"


def test_ring_ids_are_minted_deterministically_when_absent():
    polygon = _polygon(interiors=[_ring(ring_id=""), _ring(ring_id="")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A, HOLE_B)})
    out = _commit_polygon(polygon, geometry)
    assert out.exterior.ring_id == "P_ROOF_0_"
    assert out.interiors[0].ring_id == "P_ROOF_1_"
    assert out.interiors[1].ring_id == "P_ROOF_2_"


def test_world_coordinates_are_preserved_verbatim():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out.interiors[0].coords == HOLE_A
    assert out.exterior.coords == EXT


def test_window_linked_interior_ring_behavior_is_unchanged():
    """A pure Window/opening polygon still goes through ``commit_host_polygon``
    exactly as before P6.1a - untouched by the new generic path."""
    polygon = _polygon(interiors=[_ring(ring_id="P_WALL_1_", opening_link="LINK_A")], polygon_id="P_WALL")
    geometry = _geometry(polygons={"P_WALL": EXT}, rings={"LINK_A": HOLE_A})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "committed"
    assert out.interiors[0].coords == HOLE_A
    assert out.interiors[0].opening_link == "LINK_A"


def test_mixed_generic_and_opening_linked_rings_stays_ids_only():
    """The existing record contract (unchanged - ``commit_host_polygon``,
    CAC-P4.10) has no way to fully resolve a polygon that mixes a Window
    ring with a generic hole in the same call: it requires every interior
    ring to resolve before it will mark the polygon ``committed``, and a
    generic ring's empty ``opening_link`` never resolves through it. P6.1a
    does not invent a new mechanism for this - the polygon's
    ``geometry_state`` stays ``ids_only`` exactly as it did before, an
    explicit refusal, and ``RecordSet.validate()`` (the atomicity backstop
    ``build_promotion_candidate`` runs before any write) refuses the whole
    candidate rather than accept coordinates alongside ``ids_only``."""
    polygon = _polygon(
        interiors=[_ring(ring_id="P_WALL_1_", opening_link="LINK_A"), _ring(ring_id="P_WALL_2_")],
        polygon_id="P_WALL",
    )
    geometry = _geometry(polygons={"P_WALL": EXT}, rings={"LINK_A": HOLE_A},
                          generic_rings={"P_WALL": (HOLE_B,)})
    out = _commit_polygon(polygon, geometry)
    assert out.geometry_state == "ids_only"
    feature = FeatureRecord(gml_id="WALL1", kind="WallSurface", polygon_ids=("P_WALL",))
    assert RecordSet(features=(feature,), polygons=(out,)).validate() != []


def test_injected_missing_ring_rolls_back_the_whole_polygon():
    """Fewer live generic rings than the record expects (a face the mesh
    lost, or an import gap) must leave the polygon exactly as given -
    exterior included - never a partial commit."""
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_"), _ring(ring_id="P_ROOF_2_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out is polygon
    assert out.geometry_state == "ids_only"
    assert out.exterior.coords == ()


def test_injected_degenerate_ring_rolls_back_the_whole_polygon():
    """A live ring with fewer than 3 distinct points (degenerate face) must
    refuse the whole polygon, not just that ring."""
    degenerate = ((1.0, 1.0, 0.0), (1.0, 1.0, 0.0), (1.0, 1.0, 0.0))
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (degenerate,)})
    out = _commit_polygon(polygon, geometry)
    assert out is polygon
    assert out.exterior.coords == ()


def test_missing_exterior_rolls_back_generic_interior_commit():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_")])
    geometry = _geometry(polygons={}, generic_rings={"P_ROOF": (HOLE_A,)})
    out = _commit_polygon(polygon, geometry)
    assert out is polygon


def test_repeat_promotion_is_idempotent():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_"), _ring(ring_id="P_ROOF_2_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A, HOLE_B)})
    once = _commit_polygon(polygon, geometry)
    twice = _commit_polygon(once, geometry)
    assert twice.geometry_state == "committed"
    assert twice.exterior.coords == once.exterior.coords
    assert [r.coords for r in twice.interiors] == [r.coords for r in once.interiors]
    assert [r.ring_id for r in twice.interiors] == [r.ring_id for r in once.interiors]


def test_committed_generic_polygon_passes_record_validation():
    polygon = _polygon(interiors=[_ring(ring_id="P_ROOF_1_"), _ring(ring_id="P_ROOF_2_")])
    geometry = _geometry(polygons={"P_ROOF": EXT}, generic_rings={"P_ROOF": (HOLE_A, HOLE_B)})
    out = _commit_polygon(polygon, geometry)
    feature = FeatureRecord(gml_id="ROOF1", kind="RoofSurface", polygon_ids=("P_ROOF",))
    assert RecordSet(features=(feature,), polygons=(out,)).validate() == []
