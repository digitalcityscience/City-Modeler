# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Per-surface IR export group builder for the P3b/GS surface-dispatch
path (PLAN.md GS.3.2, generalized to N Windows by P3b.2/P3b.6,
gs-design-note.md, docs/agent/authoring-core/phase3b-design-note.md).

Flag disposition (P3b.6, phase3b-design-note.md §4/§5 + STATUS.md P3b.6):
this is a **supported, opt-in** export path for the frozen P3b WallSurface
+ N record-backed Window slice, not merely prototype scaffolding - but it
stays default OFF (``experimental_gs_surface_dispatch``) and Building-level
D-6 (``export_eligibility.py::can_export_building_from_ir``) remains the
primary, always-first export-dispatch rule. See ``blender/flag.py::gs_
surface_dispatch_enabled`` for the flag contract.

``gs_surface_export_group`` is the read-only "surface eligible -> plain
export group" step a writer-adapter dispatcher calls for exactly one
``gs_surface_eligibility.can_export_surface_from_ir``-eligible WallSurface.
It performs no eligibility check of its own - a caller must have already
confirmed eligibility - and reuses the exact same per-surface builders
``domain/ir_adapter.py::building_ir_from_root`` and
``domain/ir_to_groups.py::groups_from_building_ir`` already use internally
for each of a Building's WallSurfaces (``ir_adapter.surface_node_from_
record`` / ``ir_to_groups.surface_group_from_node``), so this module
introduces no second semantic representation (GS.3.2 task item 6) - it is
a thin fold over the existing Phase-5 IR/export-group structures, exactly
as scoped.

Pure Python only: no bpy/bmesh/mathutils/gpu (rule R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``).
"""
from __future__ import annotations

from .ir_adapter import surface_node_from_record
from .ir_to_groups import ExportSurface, surface_group_from_node


def gs_surface_export_group(surface_gml_id, record_set) -> ExportSurface:
    """``ExportSurface`` for one GS.3.2-eligible WallSurface.

    Caller contract: ``surface_gml_id`` must already have been confirmed
    eligible by ``gs_surface_eligibility.can_export_surface_from_ir``
    against this same ``record_set``. This function re-reads the wall's
    feature record (never re-checks eligibility) and folds it, and every
    one of its N eligible hosted Windows (P3b.2 generalization - no longer
    capped at one), into the same ``ExportSurface`` shape ``groups_from_
    building_ir`` would have produced for this wall had the whole Building
    been IR-eligible - so a v2/v3 writer's existing IR-surface writer
    helper (``_write_ir_building_surfaces``) can consume it unchanged.

    Raises ``ir_adapter.IRAdapterError`` or ``ir_to_groups.IRToGroupsError``
    for the same structural problems those two builders already raise for
    - a genuine eligibility/builder mismatch, never expected for a wall
    ``can_export_surface_from_ir`` already accepted, but never silently
    swallowed here either; a caller wiring this into export dispatch
    (GS.3.2's writer seam) is the one responsible for treating that as
    "abandon the GS surface override, use the legacy surface path"
    (gs-design-note.md S5's degenerate-geometry safety net), not this
    function.
    """
    wall = record_set.feature(surface_gml_id)
    node = surface_node_from_record(record_set, wall)
    return surface_group_from_node(node)
