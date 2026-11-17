# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Sync Ring <-> Opening operators (ticket 09 of docs/agent/semantic-hierarchy).

User-triggered, bidirectional fixes for the desync
``shared/ring_opening_sync.py`` detects and the exporter halts on. Neither
operator ever runs automatically - both are explicit, undoable (bl_options
REGISTER/UNDO) actions the user picks from the CityGML panel or the
Object context menu, exactly like ``Openings_Cutter``'s existing tools.

Target objects come from the current selection: a selected ring-paired
opening directly (a Window, or a floating Door - facade-authoring ticket
06), or a selected WallSurface/Building/BuildingPart (its ring-paired
descendants are pulled in). A ground-touching Door is silently excluded
from the targeted set rather than reported as an error - it has no wall
ring to sync by design (see ``shared/ring_opening_sync.py`` module
docstring), so selecting a wall that only has ground-touching Door
children correctly does nothing.
"""

from __future__ import annotations

import bpy
from bpy.types import Operator, Panel

from ..shared.authoring_core.blender import flag, storage
from ..shared.authoring_core.blender.edit_session import EditSessionError, sync_existing_feature
from ..shared.ring_opening_sync import (
    RING_OPENING_SYNC_TOLERANCE_M,
    building_ancestor,
    find_ring_opening_pairs,
    sync_opening_to_ring,
    sync_ring_to_opening,
)


def _target_pairs(context):
    """Every RingOpeningPair reachable from the current selection: a pair
    is targeted if its Window, its wall, or their owning Building/
    BuildingPart is directly selected - so selecting just the Window,
    just the wall, or the whole Building all work."""
    selected = list(getattr(context, "selected_objects", []) or [])
    if not selected:
        return []
    selected_ids = {id(o) for o in selected}

    buildings = {}
    for obj in selected:
        building = building_ancestor(obj)
        if building is not None and id(building) not in buildings:
            buildings[id(building)] = building

    result = []
    for building in buildings.values():
        for pair in find_ring_opening_pairs(building):
            if (
                id(pair.opening) in selected_ids
                or id(pair.wall) in selected_ids
                or id(building) in selected_ids
            ):
                result.append(pair)
    return result


def _run_sync(context, operator, sync_fn, verb: str, noun: str):
    """Shared body for both sync operators: resolve targeted pairs, apply
    ``sync_fn`` to every desynced one, report a count. ``verb``/``noun``
    only vary the user-facing report text (e.g. "Moved"/"Window(s) back
    onto their wall opening" vs. "Recut"/"wall opening(s) to match their
    Window")."""
    pairs = _target_pairs(context)
    if not pairs:
        operator.report({'WARNING'}, "No Window/wall-ring pairs found in selection")
        return {'CANCELLED'}

    to_sync = [pair for pair in pairs if not pair.in_sync]
    record_pairs = [pair for pair in to_sync if flag.is_enabled() and storage.has_record(pair.opening)]
    legacy_pairs = [pair for pair in to_sync if pair not in record_pairs]
    # A single Window + host wall is the P4.6 atomic unit.  Do not process a
    # legacy pair before refusing an unsupported record-backed combination.
    if len(record_pairs) > 1 or (record_pairs and legacy_pairs):
        operator.report({'ERROR'},
                        f"{operator.bl_label}: refused - select one record-backed Window pair at a time; "
                        "multi-feature atomic sync is not implemented")
        return {'CANCELLED'}
    if record_pairs:
        pair = record_pairs[0]
        gml_id = str(pair.opening.get("gml_id") or "").strip()
        direction = "opening_to_ring" if sync_fn is sync_opening_to_ring else "ring_to_opening"
        try:
            sync_existing_feature(gml_id, direction)
        except EditSessionError as exc:
            operator.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.view_layer.update()
        operator.report({'INFO'}, f"{verb} 1 {noun} (record-backed, committed)")
        return {'FINISHED'}

    changed = 0
    for pair in pairs:
        if pair.in_sync:
            continue
        sync_fn(pair)
        changed += 1

    context.view_layer.update()
    if changed == 0:
        operator.report({'INFO'}, "Already in sync - nothing to do")
    else:
        operator.report({'INFO'}, f"{verb} {changed} {noun}")
    return {'FINISHED'}


class CGML3_OT_SyncRingToOpening(Operator):
    """Keep the wall's opening hole where it is; move the selected/targeted
    Window(s) back onto it."""

    bl_idname = "cgml3.sync_ring_to_opening"
    bl_label = "Sync Ring -> Opening"
    bl_description = (
        "Move the Window back onto its wall's opening (the wall is never "
        "touched)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context, "selected_objects", None))

    def execute(self, context):
        return _run_sync(
            context, self, sync_ring_to_opening,
            "Moved", "Window(s) back onto their wall opening",
        )


class CGML3_OT_SyncOpeningToRing(Operator):
    """Keep the selected/targeted Window(s) where they are; recut their
    wall's opening hole to match."""

    bl_idname = "cgml3.sync_opening_to_ring"
    bl_label = "Sync Opening -> Ring"
    bl_description = (
        "Recut the wall's opening hole to match the Window (the Window is "
        "never touched)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(getattr(context, "selected_objects", None))

    def execute(self, context):
        return _run_sync(
            context, self, sync_opening_to_ring,
            "Recut", "wall opening(s) to match their Window",
        )


class CGML3_PT_RingOpeningSync(Panel):
    bl_label = "Ring / Opening Sync"
    bl_idname = "CGML3_PT_ring_opening_sync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Tool"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 31
    bl_parent_id = "CGML3_PT_Tools"

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="When it matters", icon="INFO")
        box.label(text="Moving a Window without syncing halts export -")
        box.label(text="the wall's hole and the Window no longer match.")

        box = layout.box()
        box.label(text=f"Tolerance: {RING_OPENING_SYNC_TOLERANCE_M * 100:.0f} cm", icon="DRIVER_DISTANCE")

        col = layout.column(align=True)
        col.label(text="Select a Window (or its wall/Building):")
        col.operator(CGML3_OT_SyncRingToOpening.bl_idname, icon="TRACKING_BACKWARDS")
        col.operator(CGML3_OT_SyncOpeningToRing.bl_idname, icon="TRACKING_FORWARDS")

        box = layout.box()
        box.label(text="Leaving Edit Mode on a Window or its wall")
        box.label(text="auto-syncs the side that didn't change.")
        col = box.column(align=True)
        col.label(text="Wider, explicit checks:")
        # Referenced by idname string (ops/edit_exit_validation.py) rather
        # than a Python import - keeps the two ops modules decoupled; the
        # operator just needs to be registered by the time this draws.
        col.operator("cgml.validate_building_openings", icon="MOD_BUILD")
        col.operator("cgml.validate_scene_openings", icon="SCENE_DATA")


classes = (
    CGML3_OT_SyncRingToOpening,
    CGML3_OT_SyncOpeningToRing,
    CGML3_PT_RingOpeningSync,
)
