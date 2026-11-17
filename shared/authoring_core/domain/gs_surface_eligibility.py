# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Per-surface IR-exportability predicate for the GS prototype (CAC-GS.3,
PLAN.md §6 "GS - Geometry-separation prototype", gs-design-note.md §4
item 3, "the one piece of this prototype that is genuinely new
architecture rather than wiring"), generalized to N hosted Windows per
P3b.2 (docs/agent/authoring-core/phase3b-design-note.md §7).

``can_export_surface_from_ir`` answers, for exactly one WallSurface (plus
every Window it hosts), whether that single surface can be IR-exported
while the rest of its Building stays on the legacy mesh/material export
path. This is a deliberate, narrow relaxation of D-6 ("Mixed record/legacy
inside one Building is not allowed") that exists *only* for this prototype
and *only* at the granularity this function checks - it says nothing
about, and does not touch, whole-Building eligibility or promotion:

- ``export_eligibility.py::can_export_building_from_ir`` and its
  Building-granular contract are untouched by this module and by every
  caller of it (GS.3 does not modify that file).
- ``domain/promote_building.py::PromotionPlan`` stays atomic per Building;
  this predicate is an export-time read of a scope's *existing* records,
  never a promotion, and never mutates anything.
- Because this module answers only "is this one surface eligible", and
  never "is the whole Building eligible", the rest of the Building can
  freely stay legacy (ids_only records, or no records at all) without
  affecting the result - that is checked implicitly, by never looking past
  the one WallSurface and the Windows it hosts.

Scope contract: ``record_set`` must contain the WallSurface's own
``FeatureRecord``/``PolygonRecord`` and, if a Window is hosted on it, that
Window's ``FeatureRecord``/``PolygonRecord`` too (e.g. a caller-assembled
``RecordSet`` scoped to just those one or two features, or a wider one -
this function only ever reads the one surface and features that declare
``host_id == surface_gml_id``).

Structural checks only, deliberately cheap (no triangulation, no planarity
math): this mirrors ``blender/display_builder.py::build_surface_display``'s
documented input contract (exactly one committed ``PolygonRecord`` per
surface; a depth-authored Window - GS.2's real-fixture finding - has
multiple ``PolygonRecord``s and is correctly rejected here, the same way
``display_regen.py::plan_regeneration`` skips it) without depending on
``blender/display_builder.py`` itself: that module lives in the bpy-bound
adapter layer (``shared/authoring_core/blender/``), and this module must
stay importable without Blender (rule R1), so it re-checks the same cheap
structural facts rather than importing across that layer boundary. A
caller that wires this predicate into export dispatch (GS.3.2, not this
module) still needs to catch ``DisplayBuildError`` from the real builder
for the rarer geometry-degenerate cases (zero-area loop, zero-length
edge) this predicate does not attempt to detect.

Pure Python only: no bpy/bmesh/mathutils/gpu (rule R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``).
"""
from __future__ import annotations

from dataclasses import dataclass

NO_SURFACE_RECORD = "no_surface_record"
NOT_A_WALL_SURFACE = "not_a_wall_surface"
WALL_POLYGON_COUNT = "wall_polygon_count"
WALL_NOT_COMMITTED = "wall_not_committed"
WALL_DEGENERATE_EXTERIOR = "wall_degenerate_exterior"
WINDOW_PARENT_MISMATCH = "window_parent_mismatch"
WINDOW_POLYGON_COUNT = "window_polygon_count"
WINDOW_NOT_COMMITTED = "window_not_committed"
WINDOW_NOT_RECTANGULAR = "window_not_rectangular"
WALL_RING_WINDOW_MISMATCH = "wall_ring_window_mismatch"
DUPLICATE_RING_OPENING_LINK = "duplicate_ring_opening_link"
OPENING_LINK_MISMATCH = "opening_link_mismatch"
GS_SURFACE_ELIGIBLE = "gs_surface_eligible"

# The only generator the FeatureKind registry (domain/kinds.py) currently
# enables (Window, "facade.window.rect") - every authored Window is
# rectangular by construction. Checked defensively rather than assumed, so
# a future non-rectangular generator cannot silently slip through this
# prototype's "rectangular Window only" scope limit (gs-design-note.md §3).
_RECTANGULAR_WINDOW_GENERATOR = "facade.window.rect"


@dataclass(frozen=True)
class GsSurfaceEligibility:
    """Structured result of ``can_export_surface_from_ir`` - mirrors
    ``export_eligibility.ExportEligibility``'s shape at surface
    granularity, so a future dispatcher can report *why* one surface fell
    back to legacy the same way the Building-level predicate already
    does."""

    eligible: bool
    reason: str = ""
    message: str = ""
    window_gml_ids: tuple = ()    # () if the wall hosts no (eligible) Window


def can_export_surface_from_ir(surface_gml_id, record_set) -> GsSurfaceEligibility:
    """``GsSurfaceEligibility`` for one WallSurface ``surface_gml_id``
    (CAC-GS.3).

    Checks, in order (first failure wins, same "never a best-effort
    partial result" convention as ``can_export_building_from_ir``):

    1. The surface has a ``FeatureRecord`` of kind ``WallSurface``.
    2. It has exactly one ``PolygonRecord``, ``geometry_state ==
       "committed"``, with a non-degenerate exterior ring (>= 3 distinct
       points) - the wall's own record-backed geometry contract.
    3. Every feature hosted on this wall (``host_id == surface_gml_id``,
       ``kind == "Window"``) - any number of them (P3b.2, phase3b-design-
       note.md §7) - has ``parent_id`` equal to this wall, exactly one
       committed ``PolygonRecord`` (rejects a depth-authored Window - GS.2's
       real-fixture finding), and a ``generator`` matching the one
       rectangular generator the kind registry allows. Checked in the
       wall's own hosted-Window iteration order; the first Window that
       fails stops the whole predicate (first-failure-wins, same as the
       single-Window case this generalizes).
    4. The wall's own polygon has exactly as many interior rings as it has
       eligible hosted Windows, each interior ring's ``opening_link`` is
       distinct (no two rings share one), and each Window's ``opening_link``
       matches exactly one of those rings - the N-Window generalization of
       the ring-count/opening_link pairing check GS.3.1 already had for the
       single-Window case.

    A wall with zero hosted Windows and no interior ring is eligible too;
    nothing about display/export requires a Window to be present for the
    wall's own record-backed geometry to be exportable.
    """
    wall = record_set.feature(surface_gml_id)
    if wall is None:
        return GsSurfaceEligibility(False, NO_SURFACE_RECORD, f"no feature record for surface {surface_gml_id!r}")
    if wall.kind != "WallSurface":
        return GsSurfaceEligibility(False, NOT_A_WALL_SURFACE, f"surface {surface_gml_id!r} has kind {wall.kind!r}, expected 'WallSurface'")

    wall_polygons = record_set.polygons_of(surface_gml_id)
    if len(wall_polygons) != 1:
        return GsSurfaceEligibility(
            False, WALL_POLYGON_COUNT,
            f"surface {surface_gml_id!r}: expected exactly one polygon, found {len(wall_polygons)}",
        )
    wall_polygon = wall_polygons[0]
    if wall_polygon.geometry_state != "committed":
        return GsSurfaceEligibility(
            False, WALL_NOT_COMMITTED,
            f"surface {surface_gml_id!r}: polygon geometry_state is {wall_polygon.geometry_state!r}, expected 'committed'",
        )
    if len(set(wall_polygon.exterior.coords)) < 3:
        return GsSurfaceEligibility(False, WALL_DEGENERATE_EXTERIOR, f"surface {surface_gml_id!r}: exterior ring has fewer than 3 distinct points")

    hosted_windows = tuple(
        f for f in record_set.features
        if f.host_id == surface_gml_id and f.kind == "Window"
    )

    for window in hosted_windows:
        if window.parent_id != surface_gml_id:
            return GsSurfaceEligibility(
                False, WINDOW_PARENT_MISMATCH,
                f"window {window.gml_id!r}: parent_id {window.parent_id!r} != host wall {surface_gml_id!r}",
            )
        window_polygons = record_set.polygons_of(window.gml_id)
        if len(window_polygons) != 1:
            return GsSurfaceEligibility(
                False, WINDOW_POLYGON_COUNT,
                f"window {window.gml_id!r}: expected exactly one polygon (flat), found {len(window_polygons)} - "
                "a depth-authored Window is out of this prototype's scope",
            )
        window_polygon = window_polygons[0]
        if window_polygon.geometry_state != "committed":
            return GsSurfaceEligibility(
                False, WINDOW_NOT_COMMITTED,
                f"window {window.gml_id!r}: polygon geometry_state is {window_polygon.geometry_state!r}, expected 'committed'",
            )
        if window.generator and window.generator != _RECTANGULAR_WINDOW_GENERATOR:
            return GsSurfaceEligibility(
                False, WINDOW_NOT_RECTANGULAR,
                f"window {window.gml_id!r}: generator {window.generator!r} is not the rectangular generator this prototype supports",
            )

    if len(wall_polygon.interiors) != len(hosted_windows):
        return GsSurfaceEligibility(
            False, WALL_RING_WINDOW_MISMATCH,
            f"surface {surface_gml_id!r}: {len(wall_polygon.interiors)} interior ring(s) but "
            f"{len(hosted_windows)} eligible hosted Window(s)",
        )

    if hosted_windows:
        ring_by_link = {}
        for ring in wall_polygon.interiors:
            if ring.opening_link in ring_by_link:
                return GsSurfaceEligibility(
                    False, DUPLICATE_RING_OPENING_LINK,
                    f"surface {surface_gml_id!r}: more than one interior ring has opening_link "
                    f"{ring.opening_link!r}",
                )
            ring_by_link[ring.opening_link] = ring
        for window in hosted_windows:
            if not window.opening_link or window.opening_link not in ring_by_link:
                return GsSurfaceEligibility(
                    False, OPENING_LINK_MISMATCH,
                    f"surface {surface_gml_id!r}: window {window.gml_id!r} opening_link "
                    f"{window.opening_link!r} matches no interior ring",
                )

    window_gml_ids = tuple(window.gml_id for window in hosted_windows)
    return GsSurfaceEligibility(True, GS_SURFACE_ELIGIBLE, "", window_gml_ids=window_gml_ids)
