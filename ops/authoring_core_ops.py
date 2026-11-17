# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Explicit edit/commit lifecycle operators for record-backed features (CAC-P4.2).

Thin wrappers around ``shared/authoring_core/blender/edit_session.py``. Each
operator is one Blender undo step (R5), driven from Object Mode, and never
runs from a handler (R4) - only from an explicit user/test call.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from ..shared.authoring_core.blender import flag, storage, transform_drift
from ..shared.authoring_core.domain.legacy_reader import neutral_kind
from ..shared.authoring_core.blender.edit_session import (
    EditSessionError,
    begin,
    cancel,
    commit,
    commit_transform_drift,
)
from ..shared.authoring_core.blender import roof_edit_session
from ..shared.authoring_core.blender.roof_edit_session import RoofEditSessionError
from ..shared.authoring_core.blender.guards import plan_guarded_delete
from ..shared.authoring_core.blender.promote_building import (
    PromotionError,
    promote_building,
)
from ..shared.authoring_core.blender.usecases import (
    DeleteFeatureError,
    DuplicateFeatureError,
    delete_feature,
    initialize_duplicate,
)
from ..shared.authoring_core.blender.roof_usecases import (
    RoofLifecycleError,
    delete_roof,
    initialize_roof_duplicate,
)
from ..shared.authoring_core.domain.roof_lifecycle import plan_roof_delete
from ..shared.facade_placement import close_opening

_OPENING_FEATURES = {"window", "windowsurface", "door", "doorsurface"}


def _is_window_object(obj):
    return (
        obj is not None
        and obj.type == 'MESH'
        and str(obj.get("cgml3_feature") or "").strip().lower()
        in {"window", "windowsurface"}
    )


def _is_roof_object(obj):
    return (
        obj is not None
        and obj.type == 'MESH'
        and str(obj.get("cgml3_feature") or "").strip().lower() == "roofsurface"
    )


def _target_gml_id(self, context):
    obj = context.active_object
    return self.object_gml_id.strip() or str(obj.get("gml_id") or "").strip() if obj else ""


class CGML_OT_feature_edit_begin(Operator):
    """Open an explicit edit session for a record-backed Window (CAC-P4.2):
    snapshots its committed state (CAC-P4.1 checksum) so a later
    ``cgml.feature_edit_commit``/``cgml.feature_edit_cancel`` can detect
    drift and restore the pre-edit display, respectively."""

    bl_idname = "cgml.feature_edit_begin"
    bl_label = "Begin Feature Edit"
    bl_description = "Open an explicit edit session for this record-backed Window"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return (
            (_is_window_object(obj) or _is_roof_object(obj))
            and storage.has_record(obj) and obj.mode == 'OBJECT'
        )

    def execute(self, context):
        gml_id = _target_gml_id(self, context)
        obj = context.active_object
        session_module = roof_edit_session if _is_roof_object(obj) else None
        try:
            if session_module is not None:
                session_module.begin(gml_id)
            else:
                begin(gml_id)
        except (EditSessionError, RoofEditSessionError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Edit session open for '{gml_id}'")
        return {'FINISHED'}


class CGML_OT_feature_edit_commit(Operator):
    """Commit an open edit session (CAC-P4.2): checksum -> validate ->
    dual-write the record and the legacy mesh/ring mirror, or a clean no-op
    if nothing changed, or a clean refusal if the edit is invalid."""

    bl_idname = "cgml.feature_edit_commit"
    bl_label = "Commit Feature Edit"
    bl_description = "Commit the open edit session for this record-backed Window"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return (
            (_is_window_object(obj) or _is_roof_object(obj))
            and storage.has_record(obj) and obj.mode == 'OBJECT'
        )

    def execute(self, context):
        gml_id = _target_gml_id(self, context)
        obj = context.active_object
        session_module = roof_edit_session if _is_roof_object(obj) else None
        try:
            result = session_module.commit(gml_id) if session_module is not None else commit(gml_id)
        except (EditSessionError, RoofEditSessionError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        level = 'INFO' if result.status == "committed" else 'INFO'
        self.report({level}, result.message)
        return {'FINISHED'}


class CGML_OT_feature_edit_cancel(Operator):
    """Cancel an open edit session (CAC-P4.2): restore the Window's and its
    host wall's pre-edit mesh/transform; the record was never touched."""

    bl_idname = "cgml.feature_edit_cancel"
    bl_label = "Cancel Feature Edit"
    bl_description = "Discard the open edit session and restore this Window's pre-edit state"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return (
            (_is_window_object(obj) or _is_roof_object(obj))
            and storage.has_record(obj) and obj.mode == 'OBJECT'
        )

    def execute(self, context):
        gml_id = _target_gml_id(self, context)
        obj = context.active_object
        session_module = roof_edit_session if _is_roof_object(obj) else None
        try:
            if session_module is not None:
                session_module.cancel(gml_id)
            else:
                cancel(gml_id)
        except (EditSessionError, RoofEditSessionError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Edit session for '{gml_id}' cancelled")
        return {'FINISHED'}


class CGML_OT_guarded_delete(Operator):
    """Guarded replacement for native Object-Mode ``X``/``Delete`` (CAC-P4.4,
    D-7a). Bound ahead of Blender's own ``object.delete`` keymap items (see
    ``register_keymaps`` below) - Blender has no pre-delete handler, so the
    keymap is the only point an add-on can intervene *before* a native
    delete runs, which matters because the authoritative Window record
    lives on the object about to be destroyed (a post-hoc detector, P3.3's
    ``dangling_ring``, only ever reports damage that already happened).

    Partitions the current selection *before* deleting anything
    (``guards.plan_guarded_delete``): record-backed Windows with a
    pre-validated delete plan route through ``usecases.delete_feature``;
    any record-backed object with no available delete use case (or whose
    plan itself fails) blocks the *entire* operation with a report naming
    it, deleting nothing (all-or-nothing - never a partial delete that
    removes some selected objects and leaves others); objects with no
    record fall through to native ``object.delete`` unchanged. Flag off:
    pure pass-through to ``object.delete`` (R6/C6 parity) - this wrapper
    does not exist functionally with the experimental authoring core off.

    One top-level operator call, so Blender's own undo model gives the
    whole guarded delete (routed Windows and any native deletes together)
    exactly one undo step - no separate transaction layer needed, same as
    ``cgml.delete_feature`` (P3.4) and ``cgml.facade_draw`` (P3.2).
    """

    bl_idname = "cgml.guarded_delete"
    bl_label = "Delete"
    bl_description = "Delete the selected objects; record-backed Windows go through their delete use case"
    bl_options = {'REGISTER', 'UNDO'}

    use_global: BoolProperty(name="Delete Globally", default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def _close_openings(self, objects):
        """B55: a deleted Window/Door leaves no hole/notch behind in its wall
        (skipped when the wall itself is deleted too)."""
        selected = set(objects)
        done = []
        for obj in objects:
            if obj.type == 'MESH' and str(obj.get("cgml3_feature") or "").strip().lower() in _OPENING_FEATURES \
                    and obj.parent not in selected:
                msg = close_opening(obj)
                if msg:
                    done.append(msg)
        if done:
            self.report({'INFO'}, "; ".join(done[:3]) + (f" (+{len(done) - 3} more)" if len(done) > 3 else ""))

    def execute(self, context):
        # B77: a deleted CityGML object takes its surfaces / openings / installations along; left
        # behind they stayed visible and were exported as loose OtherConstruction features
        kids = {c.name for o in context.selected_objects if o.get("cgml3_feature") for c in o.children_recursive}
        done = self._delete(context)
        if 'FINISHED' in done:
            for name in kids:
                ob = bpy.data.objects.get(name)
                if ob is not None:
                    bpy.data.objects.remove(ob)
        return done

    def _delete(self, context):
        objects = list(context.selected_objects)
        if not objects:
            return {'CANCELLED'}
        if not flag.is_enabled():
            self._close_openings(objects)
            return bpy.ops.object.delete(use_global=self.use_global)

        # RoofSurface has its own, simpler delete use case (CAC-P6.4: no
        # host/ring relation - see roof_usecases.delete_roof) - partitioned
        # out before plan_guarded_delete, which only knows Window's
        # host-wall-ring delete use case and would otherwise block the
        # whole selection on a record-backed Roof ("kind has no delete use
        # case yet"). Routed and blocking Roofs take part in the very same
        # all-or-nothing decision below as Window's own routable/blocking.
        roof_objects = [o for o in objects if _is_roof_object(o) and storage.has_record(o)]
        rest = [o for o in objects if o not in roof_objects]

        routable, blocking, legacy = plan_guarded_delete(rest)

        roof_routable = []
        for obj in roof_objects:
            gml_id = str(obj.get("gml_id") or "").strip()
            try:
                plan_roof_delete(gml_id, storage.read_record(obj))
            except RoofLifecycleError as e:
                blocking.append((obj, str(e)))
                continue
            roof_routable.append((obj, gml_id))

        if blocking:
            names = "; ".join(f"'{obj.name}' ({reason})" for obj, reason in blocking[:5])
            more = f" (+{len(blocking) - 5} more)" if len(blocking) > 5 else ""
            self.report(
                {'ERROR'},
                f"Delete refused - no delete use case available: {names}{more}",
            )
            return {'CANCELLED'}

        for obj, gml_id in routable:
            try:
                delete_feature(gml_id)
            except DeleteFeatureError as e:
                # Pre-validated above by plan_guarded_delete; this is a
                # fail-safe, not an expected path (e.g. a race with another
                # mutation between planning and deleting).
                self.report({'ERROR'}, f"Delete refused - '{obj.name}': {e}")
                return {'CANCELLED'}

        for obj, gml_id in roof_routable:
            try:
                delete_roof(gml_id)
            except RoofLifecycleError as e:
                self.report({'ERROR'}, f"Delete refused - '{obj.name}': {e}")
                return {'CANCELLED'}

        if legacy:
            self._close_openings(legacy)
            for o in context.view_layer.objects:
                o.select_set(False)
            for o in legacy:
                o.select_set(True)
            context.view_layer.objects.active = legacy[0]
            bpy.ops.object.delete(use_global=self.use_global)

        return {'FINISHED'}


class CGML_OT_initialize_duplicate(Operator):
    """Resolve a native Shift+D collision as a new Window (CAC-P4.7, D-4).

    A native duplicate remains deliberately non-exportable until this explicit
    operation succeeds: pre-flight reports its copied ``gml_id`` collision.
    The operation replaces that temporary object with a fresh record-backed
    Window and ring created through the normal authoring use case.
    """

    bl_idname = "cgml.initialize_duplicate"
    bl_label = "Initialize Duplicate as New Window"
    bl_description = "Give a native duplicated Window a new identity and independent wall opening"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            flag.is_enabled()
            and context.mode == 'OBJECT'
            and _is_window_object(obj)
            and storage.has_record(obj)
        )

    def execute(self, context):
        duplicate = context.active_object
        try:
            replacement = initialize_duplicate(duplicate)
        except DuplicateFeatureError as exc:
            self.report({'ERROR'}, f"Duplicate initialization refused: {exc}")
            return {'CANCELLED'}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        replacement.select_set(True)
        context.view_layer.objects.active = replacement
        self.report({'INFO'}, f"Initialized duplicate as new Window '{replacement.get('gml_id')}'")
        return {'FINISHED'}


class CGML_OT_delete_roof(Operator):
    """Delete a record-backed RoofSurface and its complete semantic feature
    (CAC-P6.4): removes the object, its own record (ADR 0009: one object,
    one record) and its derived display - no host wall/ring to update,
    unlike ``cgml.delete_feature``'s Window."""

    bl_idname = "cgml.delete_roof"
    bl_label = "Delete RoofSurface"
    bl_description = "Delete this record-backed RoofSurface and its display"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return _is_roof_object(obj) and storage.has_record(obj) and context.mode == 'OBJECT'

    def execute(self, context):
        gml_id = _target_gml_id(self, context)
        try:
            delete_roof(gml_id)
        except RoofLifecycleError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Deleted RoofSurface '{gml_id}'")
        return {'FINISHED'}


class CGML_OT_initialize_roof_duplicate(Operator):
    """Resolve a native Shift+D collision as a new RoofSurface (CAC-P6.4, D-4).

    A native duplicate carries the source's own ``gml_id``/record/materials
    - deliberately invalid: two objects answering to the same semantic and
    polygon identity. This operation mints a fresh feature/polygon/ring
    identity directly onto the duplicate's own already-committed geometry
    (no placement kernel - unlike Window's ``cgml.initialize_duplicate``,
    RoofSurface has none) and gives it its own, independent derived
    display."""

    bl_idname = "cgml.initialize_roof_duplicate"
    bl_label = "Initialize Duplicate as New RoofSurface"
    bl_description = "Give a native duplicated RoofSurface a new, independent identity"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            flag.is_enabled()
            and context.mode == 'OBJECT'
            and _is_roof_object(obj)
            and storage.has_record(obj)
        )

    def execute(self, context):
        duplicate = context.active_object
        try:
            replacement = initialize_roof_duplicate(duplicate)
        except RoofLifecycleError as exc:
            self.report({'ERROR'}, f"Duplicate initialization refused: {exc}")
            return {'CANCELLED'}
        for obj in context.view_layer.objects:
            obj.select_set(False)
        replacement.select_set(True)
        context.view_layer.objects.active = replacement
        self.report({'INFO'}, f"Initialized duplicate as new RoofSurface '{replacement.get('gml_id')}'")
        return {'FINISHED'}


class CGML_OT_commit_transform_drift(Operator):
    """Explicit resolution for native G/R/S transform drift (CAC-P4.5, D-7b).

    A depsgraph handler (``shared/authoring_core/blender/transform_drift.py``)
    only ever marks a record-backed object's transform dirty (R4 - it never
    writes a record); this operator is the *only* code allowed to clear that
    drift, by checksumming the object's current ``matrix_world``-derived
    geometry against its last-committed record, validating, and - if valid -
    dual-write committing (``edit_session.commit_transform_drift``, reusing
    P4.2's commit primitives rather than a second implementation). Export
    never calls this itself (``consistency_view.export_preflight`` hard-
    refuses instead and points here).

    ``target`` selects what ``object_gml_id`` alone does not already pin
    down: the active object, every selected record-backed Window, or every
    Window the drift cache currently believes is dirty scene-wide.
    """

    bl_idname = "cgml.commit_transform_drift"
    bl_label = "Commit Transform Drift"
    bl_description = "Commit this record-backed Window's current transform into its record"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})
    target: EnumProperty(
        name="Target",
        items=[
            ('ACTIVE', "Active Object", "Commit the active object's transform drift"),
            ('SELECTED', "Selected Objects", "Commit transform drift for every selected record-backed Window"),
            ('ALL_DIRTY', "All Dirty", "Commit transform drift for every dirty record-backed Window in the scene"),
        ],
        default='ACTIVE',
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return flag.is_enabled()

    def _gml_ids(self, context):
        if self.object_gml_id.strip():
            return [self.object_gml_id.strip()]
        if self.target == 'SELECTED':
            return [
                gml_id for obj in context.selected_objects
                if (gml_id := str(obj.get("gml_id") or "").strip())
            ]
        if self.target == 'ALL_DIRTY':
            return sorted(transform_drift.dirty_gml_ids())
        obj = context.active_object
        return [str(obj.get("gml_id") or "").strip()] if obj is not None else []

    def execute(self, context):
        gml_ids = [g for g in self._gml_ids(context) if g]
        if not gml_ids:
            self.report({'WARNING'}, "No record-backed target resolved")
            return {'CANCELLED'}

        committed, errors = [], []
        for gml_id in gml_ids:
            resolved = roof_edit_session.registry.resolve(gml_id)
            is_roof = _is_roof_object(resolved)
            try:
                result = (
                    roof_edit_session.commit_transform_drift(gml_id)
                    if is_roof else commit_transform_drift(gml_id)
                )
            except (EditSessionError, RoofEditSessionError) as e:
                errors.append(str(e))
                continue
            if result.status == "committed":
                committed.append(gml_id)

        if errors:
            self.report({'ERROR'}, "; ".join(errors))
            if not committed:
                return {'CANCELLED'}
        if committed:
            self.report({'INFO'}, f"Committed transform drift for {len(committed)} object(s)")
        elif not errors:
            self.report({'INFO'}, "No transform drift to commit")
        return {'FINISHED'}


class CGML_OT_promote_building(Operator):
    """Promote a legacy/partially record-backed Building to a complete,
    persisted record graph (CAC-P5.9).

    Building-scoped and atomic: reads current legacy + persisted state,
    builds the complete candidate record graph, validates it, and only then
    writes - or refuses with the scene completely untouched. Door stays
    legacy-only (never promoted, never a blocker); any other unsupported
    non-Door feature under the Building refuses the whole promotion before
    anything is written. Already-record-backed features are preserved
    exactly as they are, never recreated; an already-fully-promoted
    Building is a deterministic no-op.
    """

    bl_idname = "cgml.promote_building"
    bl_label = "Promote Building"
    bl_description = (
        "Promote this Building's legacy/partially record-backed semantic state "
        "to a complete, persisted authoring record graph"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return (
            obj is not None
            and neutral_kind(obj.get("cgml3_feature")) in ("Building", "BuildingPart")
            and str(obj.get("gml_id") or "").strip() != ""
        )

    def execute(self, context):
        obj = context.active_object
        try:
            result = promote_building(obj)
        except PromotionError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if result.is_noop:
            self.report({'INFO'}, f"'{result.building_gml_id}' is already fully promoted; no changes made")
        else:
            self.report(
                {'INFO'},
                f"Promoted {len(result.promoted_feature_ids)} feature(s) for '{result.building_gml_id}'",
            )
        return {'FINISHED'}


_addon_keymaps = []


def register_keymaps():
    """Insert ``X``/``DEL`` Object-Mode keymap items bound to
    ``cgml.guarded_delete`` (CAC-P4.4, D-7a) - the standard Blender add-on
    idiom for extending/overriding a native shortcut (``wm.keyconfigs.addon``,
    merged ahead of the default keyconfig's own bindings for the same key).

    A no-op if no window manager/keyconfig is available - Blender running
    ``--background`` has no real window manager, so this degrades to
    nothing rather than raising (matched by ``unregister_keymaps``, which
    is then also a no-op since nothing was appended)."""
    wm = getattr(bpy.context, "window_manager", None)
    kc = getattr(wm, "keyconfigs", None)
    kc = getattr(kc, "addon", None) if kc is not None else None
    if kc is None:
        return
    km = kc.keymaps.new(name='Object Mode', space_type='EMPTY')
    for key in ('X', 'DEL'):
        kmi = km.keymap_items.new(CGML_OT_guarded_delete.bl_idname, key, 'PRESS')
        _addon_keymaps.append((km, kmi))
    # B93: Tab on a CityGML building = Edit Facade (all its surfaces, face select). Same keymap
    # as Blender's own Tab (object.mode_set) so ours is tried first; its poll fails on anything
    # else (and outside Object Mode), so Tab falls through to the normal mode toggle.
    km = kc.keymaps.new(name='Object Non-modal', space_type='EMPTY')
    _addon_keymaps.append((km, km.keymap_items.new("cgml.facade_edit_start", 'TAB', 'PRESS')))


def unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()

