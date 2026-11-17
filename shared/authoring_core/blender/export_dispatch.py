# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Per-Building IR export dispatch (CAC-P5.6, PLAN.md P5.6).

The single entry point a v2/v3 writer adapter calls, once per Building
object, to decide whether that Building can use the record-backed IR
export path (P5.1-P5.5) and, if so, to get its fully merged export data
in one call:

```
building_obj (bpy)
    -> building_export_plan (this module)
        -> BuildingExportPlan(eligibility, groups)
            eligibility  domain/export_eligibility.py::ExportEligibility
            groups       domain/ir_to_groups.py::ExportBuildingGroups | None
                         (IR groups already merged with legacy Door
                         passthrough, PLAN.md P5.5)
```

This module only orchestrates existing P5.1-P5.5 building blocks
(``blender/building_root.py``, ``domain/ir_adapter.py``,
``domain/ir_to_groups.py``, ``blender/door_passthrough.py``) - it
introduces no new domain logic and performs no scene mutation of its own
(read-only, same contract as ``building_root.py``).

## Door passthrough membership (P5.6 task item 8, resolved)

``ExportEligibility.door_passthrough_features`` (already scoped to this
Building - ``can_export_building_from_ir``'s own scope contract) is the
single source of Building-scoped Door membership. ``extract_door_
passthrough`` is called with ``allowed_ids=door_passthrough_features``,
so a scene-wide Door scan can never fold in a Door belonging to a
different Building. There is deliberately no second, independent
Building-membership algorithm.

## Failure behaviour

If ``eligibility.eligible`` is ``False``, ``groups`` is ``None`` and the
caller must use the legacy export path unchanged (P5.6 item 12) - this
module never falls back internally. If ``eligibility.eligible`` is
``True``, ``building_ir_from_root``/``groups_from_building_ir``/
``merge_door_passthrough`` are expected to succeed (everything
``can_export_building_from_ir`` already checked); if one of them still
raises, that is a genuine eligibility/builder mismatch bug and is left to
propagate rather than silently degraded to a legacy fallback (P5.1-P5.5's
own "fail explicitly, never a partial result" convention - PLAN.md P5.0).

Pure orchestration aside from the one ``building_scope``/context read this
module shares with ``building_root.py``; the geometry/identity logic
itself stays in the no-bpy domain modules this calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..domain.export_eligibility import ExportEligibility
from ..domain.ir_adapter import building_ir_from_root
from ..domain.ir_to_groups import (
    ExportBuildingGroups,
    groups_from_building_ir,
    merge_door_passthrough,
)
from . import building_root
from .consistency_view import merged_record_set
from .door_passthrough import extract_door_passthrough


@dataclass(frozen=True)
class BuildingExportPlan:
    """Result of ``building_export_plan`` for one Building object."""

    eligibility: ExportEligibility
    groups: Optional[ExportBuildingGroups] = None


def building_export_plan(building_obj, objects=None, context=None) -> BuildingExportPlan:
    """``BuildingExportPlan`` for ``building_obj`` (CAC-P5.6).

    ``objects`` overrides the computed scope (``building_root.building_
    scope``), for tests that inject a fixed object list - the same
    convention ``building_root.py``'s own functions already use.
    ``context`` is forwarded to ``extract_door_passthrough`` for its one
    ``evaluated_depsgraph_get()`` need; ``None`` defaults to
    ``bpy.context`` there, as before P5.6.

    When ``eligibility.eligible`` is ``False``, ``groups`` is ``None`` -
    the caller must use the legacy export path for this Building. When
    ``True``, ``groups`` is the Building's IR-derived export groups with
    any legacy Door passthrough already merged in (empty passthrough is a
    no-op, ``merge_door_passthrough``'s own contract).
    """
    scope = objects if objects is not None else building_root.building_scope(building_obj)

    eligibility = building_root.export_eligibility(building_obj, objects=scope)
    if not eligibility.eligible:
        return BuildingExportPlan(eligibility=eligibility, groups=None)

    persisted = merged_record_set(scope)
    root, _source = building_root.building_ir_root(building_obj, objects=scope)
    ir = building_ir_from_root(persisted, root)
    groups = groups_from_building_ir(ir)

    if eligibility.door_passthrough_features:
        door_openings = extract_door_passthrough(
            objects=scope,
            context=context,
            allowed_ids=set(eligibility.door_passthrough_features),
        )
        groups = merge_door_passthrough(groups, door_openings)

    return BuildingExportPlan(eligibility=eligibility, groups=groups)
