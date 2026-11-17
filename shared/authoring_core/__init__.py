# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
CityGML authoring core (CAC): typed CityGML records as the authoring source
of truth, introduced step by step next to the legacy object/material state.

``domain`` is pure Python (never imports bpy/bmesh/mathutils/gpu, rule R1);
``blender`` holds the bpy-bound adapters. See
docs/agent/authoring-core/PLAN.md. This package imports nothing on purpose.
"""
