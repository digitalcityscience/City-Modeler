# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Blender adapter for the CAC-P5.3 transient Building root and
IR-exportability predicate.

Builds the plain, already-scoped facts ``domain/building_root.py`` and
``domain/export_eligibility.py`` need from real ``bpy.data.objects`` -
this Building's whole descendant scope, its persisted ``RecordSet``
(``consistency_view.merged_record_set``, unchanged from P3.3/P4.10), a
fresh legacy-derived ``RecordSet`` (``legacy_views.read_scene``, the same
oracle every other legacy-fact adapter in this package already uses) and
the P3.3 consistency findings over that scope. Read-only: nothing here
writes to Blender or to any object's stored record.
"""
from __future__ import annotations

from ...object_helpers import breadth_first_descendants
from ..domain.building_root import resolve_building_root
from ..domain.export_eligibility import can_export_building_from_ir
from . import legacy_views
from .consistency_view import merged_record_set, run_validation


def building_scope(building_obj):
    """``building_obj`` plus every descendant, in one list.

    The whole-Building generalisation of
    ``consistency_view.host_validation_scope``'s per-wall walk (PLAN.md
    P5.0 revision 2's dispatch-predicate pseudocode uses the same
    ``breadth_first_descendants`` traversal) - every object whose records
    or legacy state this Building's IR-exportability decision, or its
    transient root, could possibly depend on.
    """
    return [building_obj, *breadth_first_descendants(building_obj)]


def _building_gml_id(building_obj):
    return str(building_obj.get("gml_id") or "").strip()


def building_ir_root(building_obj, objects=None):
    """``(FeatureRecord, source)`` Building root for ``building_obj``
    (CAC-P5.3) - persisted if something (CAC-P5.9's ``promote_building``)
    has already written one, else a transient, non-persisted root read
    fresh from legacy state. ``objects`` overrides the computed scope, for
    tests that inject a fixed object list.
    """
    scope = objects if objects is not None else building_scope(building_obj)
    persisted = merged_record_set(scope)
    legacy = legacy_views.read_scene(scope).records
    return resolve_building_root(persisted, legacy, _building_gml_id(building_obj))


def export_eligibility(building_obj, objects=None):
    """``ExportEligibility`` for ``building_obj`` (CAC-P5.3)."""
    scope = objects if objects is not None else building_scope(building_obj)
    persisted = merged_record_set(scope)
    legacy = legacy_views.read_scene(scope).records
    findings = run_validation(scope)
    return can_export_building_from_ir(_building_gml_id(building_obj), persisted, legacy, findings)
