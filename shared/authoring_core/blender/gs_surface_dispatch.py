# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Blender adapter for the P3b/GS per-surface IR export path (PLAN.md
GS.3.2, generalized to N Windows by P3b.2, flag disposition decided by
P3b.6 - gs-design-note.md, docs/agent/authoring-core/phase3b-design-note.md
§4/§5, STATUS.md P3b.6).

Flag disposition (P3b.6): supported, opt-in for the frozen P3b WallSurface
+ N record-backed Window slice; default OFF; never overrides Building-
level D-6 (``export_eligibility.py::can_export_building_from_ir`` always
decided first by the writer - see ``io/writer/exporter.py``/``io_v2/
writer/exporter.py``'s ``_cac_ir_groups`` vs. ``_cac_gs_surface_groups``
dispatch precedence). A surface this module does not report eligible
(unsupported kind, depth-authored Window, non-committed geometry, a legacy
mirror the P3.3 consistency gate refuses) stays on the ordinary legacy
export path - never a silent fallback, never a crash.

Builds, from real ``bpy.data.objects``, the set of WallSurfaces a v2/v3
writer may export through the record/IR path (``domain/gs_surface_
eligibility.py::can_export_surface_from_ir``) while the rest of their
(non-IR-eligible) Building stays legacy - plus the actual ``ExportSurface``
each one folds to (``domain/gs_surface_dispatch.py::gs_surface_export_
group``). Read-only: nothing here writes to Blender or to any object's
stored record, exactly like ``blender/building_root.py``.

Degenerate-geometry safety net (GS.3.2 task item 5): a wall can be
structurally eligible (``can_export_surface_from_ir``'s cheap checks: ring
count, opening_link pairing, committed state) yet still fail to build a
real display mesh (zero-area loop, zero-length edge - ``display_builder.
build_surface_display``'s own, stricter geometry checks). Both functions
here re-run that builder as a dry run and silently exclude such a wall
from the GS override rather than let it raise - the caller's only
remaining option is then the legacy surface path for that one wall, never
a crashed Building export.
"""
from __future__ import annotations

from ..domain.gs_surface_dispatch import gs_surface_export_group
from ..domain.gs_surface_eligibility import can_export_surface_from_ir
from ..domain.ir_adapter import IRAdapterError
from ..domain.ir_to_groups import IRToGroupsError
from . import display_builder
from .consistency_view import merged_record_set


def gs_eligible_wall_surfaces(objects):
    """``(RecordSet, {wall_gml_id: GsSurfaceEligibility})`` for every
    GS.3.2-eligible WallSurface among ``objects``'s merged records.

    A WallSurface counts as eligible here only when both hold:

    1. ``can_export_surface_from_ir`` reports it structurally eligible.
    2. ``display_builder.build_surface_display`` can actually build its
       display mesh without raising ``DisplayBuildError`` - the
       degenerate-geometry safety net described in the module docstring.

    ``objects`` should already be scoped to one Building (e.g.
    ``building_root.building_scope(building_obj)``) - a wider, scene-level
    object list would report every eligible WallSurface in the scene, not
    just this Building's, the same scope contract ``building_root.py``'s
    own functions already document.
    """
    record_set = merged_record_set(objects)
    eligible = {}
    for feature in record_set.features:
        if feature.kind != "WallSurface":
            continue
        result = can_export_surface_from_ir(feature.gml_id, record_set)
        if not result.eligible:
            continue
        try:
            display_builder.build_surface_display(record_set, feature.gml_id)
        except display_builder.DisplayBuildError:
            continue
        eligible[feature.gml_id] = result
    return record_set, eligible


def gs_surface_groups(objects):
    """``(RecordSet, {wall_gml_id: ExportSurface})`` for every GS.3.2-
    eligible WallSurface among ``objects``'s merged records (see
    ``gs_eligible_wall_surfaces``).

    A wall whose eligibility and display-build both passed but whose
    read-only IR/export-group fold itself still raises
    (``IRAdapterError``/``IRToGroupsError`` - not expected, defensive only)
    is excluded here too, for the same "never crash the whole Building
    export" reason as the display-build check above.
    """
    record_set, eligible = gs_eligible_wall_surfaces(objects)
    groups = {}
    for wall_gml_id in eligible:
        try:
            groups[wall_gml_id] = gs_surface_export_group(wall_gml_id, record_set)
        except (IRAdapterError, IRToGroupsError):
            continue
    return record_set, groups
