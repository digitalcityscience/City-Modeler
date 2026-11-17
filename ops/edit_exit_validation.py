# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Edit-exit opening/ring integrity handler + explicit validate commands
(facade-authoring ticket 04, design doc section 11.1).

Wires ``shared/edit_exit_validation.py``'s pure snapshot/diff logic into a
real, automatic Blender mode-change handler: Edit-Mode entry snapshots every
relevant object entering the session (a Window opening fill, or a wall
carrying at least one interior-ring face); exit diffs each one against its
entry snapshot and acts by which side changed - **never** a blind
one-direction Sync (see ``_resolve_pair`` below).

**No native "on mode change" event exists in Blender's API** (verified -
this is a new, additive layer). ``bpy.app.handlers.depsgraph_update_post``
is used instead: it fires reliably on every ``bpy.ops.object.mode_set()``
call (confirmed empirically, including in ``--background`` mode), so a
per-object "was this in Edit Mode last time we looked" comparison there
detects entry/exit transitions without polling. Blender supports
multi-object Edit Mode (select wall + its Window, Tab together) with every
selected mesh object's ``.mode`` flipping to ``'EDIT'`` simultaneously and
back together - so one Tab session's entered/exited sets are handled as one
batch, which is exactly what makes "both sides changed in the same session"
(the ambiguous case) detectable at all (ticket 04 bullet 3 / R8).

Pairing is strictly local (ticket 04 bullet 5, R8): a changed opening looks
up its own parent wall (``fill.parent``, set by both the placement kernel
and the importer's explode step); a changed wall looks up only its own
Window children (``shared/ring_opening_sync.pairs_for_wall_children``) -
never a building-wide or scene-wide scan. The two **explicit** wider checks
(``cgml.validate_building_openings`` / ``cgml.validate_scene_openings``) are
separate, user-triggered operators that intentionally do scan broadly.
"""

from __future__ import annotations

import traceback
from typing import NamedTuple

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..shared.authoring_core.blender.guards import refuse_if_record_backed
from ..shared.authoring_core.blender.registry import resolve
from ..shared.edit_exit_validation import (
    ACTION_AMBIGUOUS,
    ACTION_SYNC_OPENING_TO_RING,
    ACTION_SYNC_RING_TO_OPENING,
    changed_ring_keys,
    opening_state_changed,
    resolve_action,
    ring_key,
    snapshot_relevant_state,
)
from ..shared.ring_opening_sync import (
    building_ancestor,
    check_ring_opening_sync,
    pairs_for_wall_children,
    ring_face_link,
    sync_opening_to_ring,
    sync_ring_to_opening,
)


class AmbiguousConflict(NamedTuple):
    """One "both sides changed in the same Edit-Mode session" halt (ticket 04
    bullet 3) - recorded rather than guessed. Exposed as a small module-level
    list, ``PENDING_AMBIGUOUS``, so headless tests and a future N-panel can
    both observe it without needing the interactive dialog."""

    wall_name: str
    opening_name: str
    # Field names match CGML_OT_resolve_ambiguous_sync's properties.
    wall_gml_id: str = ""
    opening_gml_id: str = ""


PENDING_AMBIGUOUS: list[AmbiguousConflict] = []

# name -> (kind, snapshot) for every relevant object currently inside an
# Edit-Mode session, captured on entry.
_entry_snapshots: dict = {}
# name -> gml_id captured with that snapshot; the name is only a fallback
# when the object is looked up again on exit (P2.3).
_entry_ids: dict = {}
# names of every mesh object this handler last observed with .mode == 'EDIT'.
_tracked_edit_objects: set = set()
# Guard messages for pairs the handler left alone because a side (or a
# dependant) is record-backed (CAC-P3.0, PLAN §2.6 C4): each is printed once
# per session and kept here for headless tests and a future panel.
SKIPPED_RECORD_BACKED: list[str] = []


def _pair_key(pair):
    return (pair.wall.name, pair.ring_face_index)


def _ring_key_for_pair(pair) -> str:
    """The same snapshot key ``shared.edit_exit_validation.snapshot_relevant_
    state`` would compute for ``pair``'s ring face - derived from the ring
    face's *own* material link (``ring_face_link``), never the paired
    opening's, so a pairing made by the vertex-identity fallback (a linked
    opening paired to an unlinked ring, or vice versa) still looks up the
    key its wall-side snapshot actually used."""
    link = ring_face_link(pair.wall, pair.ring_face_index)
    return ring_key(link, pair.ring_face_index)


def _find_pair(wall, opening):
    for pair in pairs_for_wall_children(wall):
        if pair.opening is opening:
            return pair
    return None


def _flag_ambiguous(pair) -> None:
    conflict = AmbiguousConflict(
        wall_name=pair.wall.name,
        opening_name=pair.opening.name,
        wall_gml_id=str(pair.wall.get("gml_id") or ""),
        opening_gml_id=str(pair.opening.get("gml_id") or ""),
    )
    PENDING_AMBIGUOUS.append(conflict)
    print(
        f"[CityGML] Edit-exit: '{pair.opening.name}' and its wall ring on "
        f"'{pair.wall.name}' both changed in this edit - halting, not "
        "guessing. Run 'Sync Ring -> Opening' or 'Sync Opening -> Ring' "
        "(CityGML > Ring / Opening Sync panel) to pick the authoritative side."
    )
    if bpy.app.background:
        return
    try:
        bpy.ops.cgml.resolve_ambiguous_sync('INVOKE_DEFAULT', **conflict._asdict())
    except Exception:
        traceback.print_exc()


def _push_undo_step(message: str) -> None:
    try:
        bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


def _resolve_pair(pair, *, opening_changed: bool, ring_changed: bool) -> None:
    """Executes ``shared.edit_exit_validation.resolve_action``'s pure
    decision for ``pair``: the boolean-pair -> action mapping lives there
    (unit-tested without bpy); this only carries out the bpy side effect for
    whichever action comes back. Callers only invoke this when at least one
    side changed, so ``ACTION_NONE`` is never reached here.

    A pair touching a record-backed object is skipped and reported, never
    synced or raised as ambiguous (PLAN §2.6 C4: handlers do not mutate
    record-backed meshes)."""
    message = refuse_if_record_backed([pair.wall, pair.opening], "Edit-exit auto-sync")
    if message:
        if message not in SKIPPED_RECORD_BACKED:
            SKIPPED_RECORD_BACKED.append(message)
            print(f"[CityGML] {message}")
        return
    action = resolve_action(opening_changed=opening_changed, ring_changed=ring_changed)
    if action == ACTION_AMBIGUOUS:
        _flag_ambiguous(pair)
        return
    if action == ACTION_SYNC_OPENING_TO_RING:
        sync_opening_to_ring(pair)
        _push_undo_step("CityGML: auto-synced opening -> ring")
    elif action == ACTION_SYNC_RING_TO_OPENING:
        sync_ring_to_opening(pair)
        _push_undo_step("CityGML: auto-synced ring -> opening")
    bpy.context.view_layer.update()


def _handle_exit(exited_names) -> None:
    changed_openings = []
    changed_wall_rings = {}

    for name in exited_names:
        before = _entry_snapshots.pop(name, None)
        gml_id = _entry_ids.pop(name, "")
        if before is None:
            continue  # was out of scope on entry - nothing was ever snapshotted
        obj = resolve(gml_id, name)
        if obj is None:
            continue  # deleted mid-edit; no counterpart left to sync against
        if obj.mode == 'EDIT':
            # Renamed mid-edit, not exited: keep its entry snapshot under the
            # new name so the real exit still diffs against the entry state.
            _entry_snapshots[obj.name] = before
            _entry_ids[obj.name] = gml_id
            continue

        after = snapshot_relevant_state(obj)
        if after is None:
            continue  # became out of scope mid-edit (e.g. its last ring face was deleted)
        before_kind, before_state = before
        after_kind, after_state = after
        if before_kind != after_kind:
            continue  # scope changed shape mid-edit; nothing sane to diff

        if before_kind == "opening":
            if opening_state_changed(before_state, after_state):
                changed_openings.append(obj)
        else:
            keys = changed_ring_keys(before_state, after_state)
            if keys:
                changed_wall_rings[obj] = keys

    if not changed_openings and not changed_wall_rings:
        return

    handled = set()

    for opening in changed_openings:
        wall = opening.parent
        if wall is None or getattr(wall, "type", None) != "MESH":
            continue  # no wall to sync against
        pair = _find_pair(wall, opening)
        if pair is None:
            continue
        key = _pair_key(pair)
        if key in handled:
            continue
        handled.add(key)
        ring_also_changed = _ring_key_for_pair(pair) in changed_wall_rings.get(wall, ())
        _resolve_pair(pair, opening_changed=True, ring_changed=ring_also_changed)

    for wall, ring_keys in changed_wall_rings.items():
        for pair in pairs_for_wall_children(wall):
            if _ring_key_for_pair(pair) not in ring_keys:
                continue
            key = _pair_key(pair)
            if key in handled:
                continue
            handled.add(key)
            opening_also_changed = pair.opening in changed_openings
            _resolve_pair(pair, opening_changed=opening_also_changed, ring_changed=True)


def _process_mode_transitions() -> None:
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is None:
        return

    editing = {
        obj.name: obj for obj in view_layer.objects
        if getattr(obj, "type", None) == "MESH" and obj.mode == 'EDIT'
    }
    currently_editing = set(editing)

    entered = currently_editing - _tracked_edit_objects
    exited = _tracked_edit_objects - currently_editing

    for name in entered:
        obj = editing[name]  # seen in this same pass: no lookup needed
        snap = snapshot_relevant_state(obj)
        if snap is not None:
            _entry_snapshots[name] = snap
            _entry_ids[name] = str(obj.get("gml_id") or "")
        # out-of-scope objects are simply never recorded - no snapshot taken,
        # nothing to diff on exit, no full-model scan (ticket 04 bullet 5).

    if exited:
        _handle_exit(exited)

    _tracked_edit_objects.clear()
    _tracked_edit_objects.update(currently_editing)


def _on_depsgraph_update(scene, depsgraph):
    try:
        _process_mode_transitions()
    except Exception:
        # A bug here must never break the user's edit session or undo stack.
        traceback.print_exc()


def reset_state() -> None:
    """Clear all tracked entry snapshots / mode bookkeeping / pending
    ambiguous conflicts - used on register/unregister and by headless tests
    that need a clean slate between scenarios."""
    _entry_snapshots.clear()
    _entry_ids.clear()
    _tracked_edit_objects.clear()
    PENDING_AMBIGUOUS.clear()
    SKIPPED_RECORD_BACKED.clear()


# --------------------------------------------------------------------------
# Ambiguous-conflict resolution dialog (interactive "halt and ask").
# --------------------------------------------------------------------------
class CGML_OT_resolve_ambiguous_sync(Operator):
    """Both a Window opening and its wall's ring changed in the same
    Edit-Mode session - ask which side is authoritative rather than
    guessing (ticket 04 bullet 3)."""

    bl_idname = "cgml.resolve_ambiguous_sync"
    bl_label = "Opening/Ring Both Changed"
    bl_description = (
        "Both the opening and its wall's ring changed in this edit - pick "
        "which side is authoritative"
    )
    bl_options = {'REGISTER', 'UNDO'}

    wall_name: StringProperty(options={'HIDDEN'})
    opening_name: StringProperty(options={'HIDDEN'})
    wall_gml_id: StringProperty(options={'HIDDEN'})
    opening_gml_id: StringProperty(options={'HIDDEN'})
    authoritative: EnumProperty(
        name="Keep",
        items=[
            ('OPENING', "The Window", "Keep the Window where it is; recut the wall's ring to match it"),
            ('RING', "The Wall's Ring", "Keep the wall's ring where it is; move the Window back onto it"),
        ],
        default='OPENING',
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"'{self.opening_name}' and its wall ring both changed.", icon='ERROR')
        layout.label(text="Which side is correct?")
        layout.prop(self, "authoritative", expand=True)

    def execute(self, context):
        wall = resolve(self.wall_gml_id, self.wall_name)
        opening = resolve(self.opening_gml_id, self.opening_name)
        if wall is None or opening is None:
            self.report({'WARNING'}, "Object(s) no longer exist")
            return {'CANCELLED'}
        pair = _find_pair(wall, opening)
        if pair is None:
            self.report({'WARNING'}, "Opening/ring pair no longer found")
            return {'CANCELLED'}
        message = refuse_if_record_backed([pair.wall, pair.opening], self.bl_label)
        if message:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        if self.authoritative == 'OPENING':
            sync_opening_to_ring(pair)
        else:
            sync_ring_to_opening(pair)
        context.view_layer.update()

        global PENDING_AMBIGUOUS
        PENDING_AMBIGUOUS[:] = [
            c for c in PENDING_AMBIGUOUS
            if not (c.wall_name == self.wall_name and c.opening_name == self.opening_name)
        ]
        self.report({'INFO'}, f"Synced '{opening.name}' with its wall ring")
        return {'FINISHED'}


# --------------------------------------------------------------------------
# Explicit wider checks (ticket 04 bullet 6) - deliberately NOT local; these
# are the two user-triggered commands the design doc calls out as covering
# what the automatic local handler intentionally never scans for.
# --------------------------------------------------------------------------
def _building_and_parts(root):
    """``root`` plus every Building/BuildingPart object anywhere in its
    child subtree - each one is a separate ``check_ring_opening_sync`` unit
    (that function itself never descends into a nested Building/BuildingPart,
    matching what the exporter's own per-object guard checks)."""
    found = [root]
    stack = list(root.children)
    while stack:
        obj = stack.pop()
        if str(obj.get("cgml3_feature") or "").strip() in ("Building", "BuildingPart"):
            found.append(obj)
        stack.extend(obj.children)
    return found


class CGML_OT_validate_building_openings(Operator):
    """Explicit, whole-building opening/ring sync check (ticket 04 bullet 6)
    - covers what the automatic local handler deliberately never scans for."""

    bl_idname = "cgml.validate_building_openings"
    bl_label = "Validate Openings in Active Building"
    bl_description = "Check every opening/ring pair in the active object's Building for desync"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        anchor = building_ancestor(context.active_object)
        if anchor is None:
            self.report({'WARNING'}, "Active object has no Building/BuildingPart ancestor")
            return {'CANCELLED'}

        targets = _building_and_parts(anchor)
        desynced = []
        for obj in targets:
            desynced.extend(check_ring_opening_sync(obj))

        if not desynced:
            self.report(
                {'INFO'},
                f"'{anchor.name}': all opening/ring pairs in sync "
                f"({len(targets)} building object(s) checked)",
            )
        else:
            self.report(
                {'WARNING'},
                f"'{anchor.name}': {len(desynced)} opening/ring pair(s) desynced across "
                f"{len(targets)} building object(s) - use Sync Ring/Opening",
            )
        return {'FINISHED'}


class CGML_OT_validate_scene_openings(Operator):
    """Explicit, whole-scene opening/ring sync check (ticket 04 bullet 6) -
    the "validate before export" command."""

    bl_idname = "cgml.validate_scene_openings"
    bl_label = "Validate All Openings in Scene"
    bl_description = "Check every opening/ring pair in every Building/BuildingPart in the scene for desync"
    bl_options = {'REGISTER'}

    def execute(self, context):
        targets = [
            obj for obj in context.scene.objects
            if str(obj.get("cgml3_feature") or "").strip() in ("Building", "BuildingPart")
        ]
        if not targets:
            self.report({'WARNING'}, "No Building/BuildingPart objects in the scene")
            return {'CANCELLED'}

        desynced_total = 0
        desynced_buildings = 0
        for obj in targets:
            desynced = check_ring_opening_sync(obj)
            if desynced:
                desynced_total += len(desynced)
                desynced_buildings += 1

        if desynced_total == 0:
            self.report(
                {'INFO'},
                f"Scene clean: {len(targets)} building object(s) checked, all opening/ring pairs in sync",
            )
        else:
            self.report(
                {'WARNING'},
                f"{desynced_total} opening/ring pair(s) desynced across {desynced_buildings} "
                f"building object(s) ({len(targets)} checked) - use Sync Ring/Opening before export",
            )
        return {'FINISHED'}


# --------------------------------------------------------------------------
# Register / unregister
# --------------------------------------------------------------------------
def register():
    reset_state()
    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    reset_state()


classes = (
    CGML_OT_resolve_ambiguous_sync,
    CGML_OT_validate_building_openings,
    CGML_OT_validate_scene_openings,
)
