"""CAC-P6.1: RoofSurface semantic core - pure/domain proof, no bpy.

P6.0 (docs/agent/authoring-core/phase6-roof-design-note.md) found the
record schema, ``legacy_reader.py`` and ``promote_building.py`` already
boundary-surface-generic, with zero Wall-specific branching. This file is
the P6.1 regression proof of that finding for RoofSurface specifically: no
new "roof record adapter" function is introduced (there is nothing to add
- ``feature_record_from_legacy``/``read_legacy_scene`` already build a
correct ``FeatureRecord(kind="RoofSurface")`` today, exercised for a
material-only Roof by ``test_cac_legacy_reader.py::
test_merged_building_material_only_surfaces_and_openings`` already). What
was missing and is proven here for the first time:

- an *exploded* RoofSurface (its own object/gml_id, not material-only),
  with a hole (interior ring), converts with every identity/LoD field
  preserved (items 1-6 below);
- ``commit_rules.decide()`` (P1.7, unmodified) accepts/refuses the same
  planar/non-planar cases for a Roof feature (item 7-8), including
  composing two independently-owned polygons of one Roof feature - a case
  P6.0 §4 flagged as untested composition, not new logic (item 9 below,
  "multi-polygon-per-feature composition");
- ``build_promotion_candidate`` (P5.9, unmodified) promotes a legacy
  RoofSurface exactly like a WallSurface: ids/LoD/owner preserved, geometry
  carried through unchanged once committed to world-frame coordinates by
  the caller (mirroring what ``blender/promote_building.py`` does for real
  objects) - this is the domain-level half of the P6.1 promotion-reuse
  proof; the real-Blender half is
  ``scripts/headless/test_cac_p6_1_roof_promotion.py``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.domain import commit_rules as cr  # noqa: E402
from shared.authoring_core.domain import records as r  # noqa: E402
from shared.authoring_core.domain.legacy_reader import (  # noqa: E402
    MaterialView as M, ObjectView as O, read_legacy_scene,
)
from shared.authoring_core.domain.promote_building import build_promotion_candidate  # noqa: E402

# Same flat-roof-with-hole geometry as test_cac_commit_rules.py's roof_before,
# so P1.7's planarity math is reused unmodified, not re-derived.
SQUARE = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 3.0), (0.0, 4.0, 3.0))
HOLE = ((1.0, 1.0, 3.0), (2.0, 1.0, 3.0), (2.0, 2.0, 3.0), (1.0, 2.0, 3.0))
NON_PLANAR = ((0.0, 0.0, 3.0), (4.0, 0.0, 3.0), (4.0, 4.0, 4.0), (0.0, 4.0, 3.0))
QUAD_A = ((0.0, 0.0, 3.0), (2.0, 0.0, 3.0), (2.0, 4.0, 3.0), (0.0, 4.0, 3.0))
QUAD_B = ((2.0, 5.0, 3.0), (4.0, 5.0, 3.0), (4.0, 9.0, 3.0), (2.0, 9.0, 3.0))


def _exploded_roof_scene():
    """A single-feature RoofSurface, its own object, with a hole - the shape
    P6.0 §5 scopes in (single polygon, exterior + interior rings)."""
    building = O("Bldg", props={"cgml3_feature": "Building", "gml_id": "B1"})
    roof_mat = M("Roof", {"SurfaceTyp": "RoofSurface", "con_surface_id": "ROOF1",
                           "gml_polygon_id": "P_ROOF", "gml_ring_id": "P_ROOF_0_", "lod": 3})
    hole_mat = M("RoofHole", {"Interior": True, "ExteriorPolyId": "P_ROOF",
                              "SurfaceTyp": "RoofSurface", "con_surface_id": "ROOF1",
                              "gml_polygon_id": "P_ROOF_1", "gml_ring_id": "P_ROOF_1_", "lod": 3})
    roof = O("Roof", parent="Bldg", props={"cgml3_feature": "RoofSurface", "gml_id": "ROOF1"},
             materials=(roof_mat, hole_mat))
    return [building, roof]


# 1-6: valid legacy Roof facts -> FeatureRecord, identity/LoD preserved.
def test_exploded_roof_converts_with_identity_and_lod_preserved():
    rs = read_legacy_scene(_exploded_roof_scene()).records
    roof = rs.feature("ROOF1")
    assert roof.kind == "RoofSurface"
    assert roof.gml_id == "ROOF1"                       # gml_id preserved
    assert roof.parent_id == "B1" and roof.owner_id == "B1"
    assert roof.lod == 3  # LoD preserved (inferred from its own polygon materials, per legacy_reader)
    assert roof.polygon_ids == ("P_ROOF",)               # polygon id preserved
    poly = rs.polygon("P_ROOF")
    assert poly.exterior.ring_id == "P_ROOF_0_"          # ring id preserved
    assert len(poly.interiors) == 1 and poly.interiors[0].ring_id == "P_ROOF_1_"
    assert rs.validate() == []


# 9. repeated conversion deterministic.
def test_conversion_is_deterministic():
    scene = _exploded_roof_scene()
    first = read_legacy_scene(scene).records.to_dict()
    second = read_legacy_scene(scene).records.to_dict()
    assert first == second


# 7. P1.7 valid topology accepted for a Roof feature (single polygon w/ hole,
# reusing commit_rules unmodified - cites test_cac_commit_rules.py's own
# 9-case coverage rather than re-deriving it).
def test_p17_accepts_valid_roof_topology_with_hole():
    before = [r.PolygonRecord(
        polygon_id="P_ROOF", feature_id="ROOF1",
        exterior=r.RingRecord("P_ROOF_0_", "exterior", SQUARE),
        interiors=(r.RingRecord("P_ROOF_1_", "interior", HOLE),),
        geometry_state="committed",
    )]
    after = [cr.FaceGroup(poly_key="P_ROOF", exterior=SQUARE, interiors=(HOLE,))]
    plan = cr.decide(before, after)
    assert not plan.refused and len(plan.kept) == 1
    assert r.validate_polygon(plan.kept[0]) == []


# 8. P1.7 invalid (non-planar) topology refused.
def test_p17_refuses_non_planar_roof_topology():
    before = [r.PolygonRecord(
        polygon_id="P_ROOF", feature_id="ROOF1",
        exterior=r.RingRecord("P_ROOF_0_", "exterior", SQUARE),
        geometry_state="committed",
    )]
    after = [cr.FaceGroup(poly_key="P_ROOF", exterior=NON_PLANAR)]
    plan = cr.decide(before, after)
    assert not plan.kept and len(plan.refused) == 1


# 10 (P6.0 §4's flagged gap): decide() composes correctly when a Roof
# feature already owns two independent polygons - not new logic, decide()
# is written per-feature against its whole polygon list, this proves the
# composition rather than assuming it.
def test_p17_composes_across_two_polygons_of_one_roof_feature():
    before = [
        r.PolygonRecord(polygon_id="P_A", feature_id="ROOF1",
                         exterior=r.RingRecord("P_A_0_", "exterior", QUAD_A),
                         geometry_state="committed"),
        r.PolygonRecord(polygon_id="P_B", feature_id="ROOF1",
                         exterior=r.RingRecord("P_B_0_", "exterior", QUAD_B),
                         geometry_state="committed"),
    ]
    after = [
        cr.FaceGroup(poly_key="P_A", exterior=QUAD_A),
        cr.FaceGroup(poly_key="P_B", exterior=QUAD_B),
    ]
    plan = cr.decide(before, after)
    assert {p.polygon_id for p in plan.kept} == {"P_A", "P_B"}
    assert not plan.refused and not plan.removed and not plan.minted


# Domain-level half of the promotion-reuse proof (P6.0 §15): a RoofSurface
# in "legacy_committed" (geometry already committed to world-frame
# coordinates by the caller, exactly as blender/promote_building.py's
# _commit_legacy_geometry does for a live object) promotes into the
# candidate graph unchanged in shape from how WallSurface already does -
# no roof-specific branch exists in build_promotion_candidate to test
# against, which is itself the proof there is none.
def _committed_roof_polygon():
    return r.PolygonRecord(
        polygon_id="P_ROOF", feature_id="ROOF1", lod=3,
        exterior=r.RingRecord("P_ROOF_0_", "exterior", SQUARE),
        geometry_state="committed",
    )


def test_promotion_candidate_includes_roof_with_ids_lod_geometry_preserved():
    building = r.FeatureRecord(gml_id="B1", kind="Building", provenance="imported")
    roof = r.FeatureRecord(gml_id="ROOF1", kind="RoofSurface", parent_id="B1", owner_id="B1",
                            lod=3, lod_origin="source", polygon_ids=("P_ROOF",), provenance="imported")
    legacy_committed = r.RecordSet(features=(building, roof), polygons=(_committed_roof_polygon(),))
    persisted = r.RecordSet()

    plan = build_promotion_candidate("B1", persisted, legacy_committed, findings=())

    assert not plan.is_noop
    promoted_roof = plan.candidate.feature("ROOF1")
    assert promoted_roof.kind == "RoofSurface" and promoted_roof.provenance == "promoted"
    assert promoted_roof.gml_id == "ROOF1"                       # gml_id preserved
    assert promoted_roof.parent_id == "B1" and promoted_roof.owner_id == "B1"
    assert promoted_roof.lod == 3                                # LoD preserved
    poly = plan.candidate.polygon("P_ROOF")
    assert poly.exterior.coords == SQUARE                        # world-frame geometry preserved, no transform
    assert poly.geometry_state == "committed"
    assert plan.candidate.validate() == []


def test_promotion_of_already_promoted_roof_is_noop():
    building = r.FeatureRecord(gml_id="B1", kind="Building", provenance="promoted")
    roof = r.FeatureRecord(gml_id="ROOF1", kind="RoofSurface", parent_id="B1", owner_id="B1",
                            lod=3, polygon_ids=("P_ROOF",), provenance="promoted")
    persisted = r.RecordSet(features=(building, roof), polygons=(_committed_roof_polygon(),))

    plan = build_promotion_candidate("B1", persisted, persisted, findings=())

    assert plan.is_noop and plan.candidate.to_dict() == persisted.to_dict()
