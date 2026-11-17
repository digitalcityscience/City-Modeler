# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Thin-adapter operators for LoD3 facade authoring (ADR 0004 / facade-authoring
tickets 02-03/06). The Edit-Mode face context-menu, the inline "split into
surfaces" action (D6), and "Draw Window"/"Draw Door" (D9, both topologies)
land here; Balcony is a later ticket. Every geometry/semantics decision lives in
``shared/facade_surface_resolver.py`` and ``shared/facade_placement.py`` -
this module only reads the selected face + mouse and calls those. The one
other thing it reads from is ``Openings_Cutter/Openings_Cutter.py``'s
face-selection-free snap and mouse-to-plane machinery (per the ticket:
"Reuse Openings_Cutter's face-selection + snap + placement machinery") -
that's UI plumbing (turning a mouse position into a snapped world point),
not a geometry/semantics decision, so it stays outside the "calls those two
shared modules" rule above.
"""

from __future__ import annotations

import bmesh
import bpy
import gpu
import time
import traceback
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Menu, Operator
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..Openings_Cutter.Openings_Cutter import (
    build_opening_cutter_snap_cache,
    draw_opening_cutter_snap_marker,
    project_opening_cutter_mouse_to_plane,
    resolve_opening_cutter_snap_mouse,
)
from ..shared import modal_hint
from ..shared.align_objects import align_source_to_target
from ..shared.authoring_core.blender import flag, storage
from ..shared.authoring_core.blender.edit_session import EditSessionError, align_existing_window
from ..shared.authoring_core.blender.guards import refuse_if_record_backed
from ..shared.authoring_core.blender.registry import resolve
from ..shared.authoring_core.blender.usecases import (
    CreateFeatureError,
    DeleteFeatureError,
    create_feature,
    create_preflight,
    delete_feature,
)
from ..shared.alignment_geometry import snap_to_neighbours
from ..shared.facade_alignment_overlay import (
    candidate_rect,
    compute_alignment_overlay,
    draw_alignment_overlay,
    gather_same_wall_neighbours,
)
from ..shared.facade_placement import (
    DEFAULT_WINDOW_DEPTH,
    FacadeElementDefinition,
    PlacementRequest,
    definition_from_source_object,
    place_semantic_element,
    surface_rect_from_drag,
    surface_uv_from_world_point,
    surface_world_point_from_uv,
)
from ..shared.facade_surface_resolver import (
    resolve_surface_context,
    resolve_surface_context_for_face,
    split_into_semantic_surfaces,
    surface_needs_split,
)
from ..shared import opening_types
from ..shared.lod_helpers import lod_promotion_root
from ..shared.ring_opening_sync import is_ring_paired_opening
from ..shared.semantic_tree import is_boundary_surface

# "Default-sized" element dropped by a single click with no drag (design doc
# §8.3's own example proposal dimensions - a typical residential window).
DEFAULT_WINDOW_WIDTH = 1.2
DEFAULT_WINDOW_HEIGHT = 1.4
# A standard single-leaf door (facade-authoring ticket 06). Set explicitly in
# invoke() below - not just left as CGML_OT_facade_draw's class-level
# FloatProperty default - because Blender's operator "remember last values"
# would otherwise leak a Window's last-drawn size into the next Door draw (or
# vice versa), the two sharing one bl_idname.
DEFAULT_DOOR_WIDTH = 0.9
DEFAULT_DOOR_HEIGHT = 2.1
# Screen-pixel movement below which a press+release is a "click" (default
# size), not a "drag" (exact rectangle) - matches Openings_Cutter's own
# drag-vs-click convention.
DRAG_CLICK_THRESHOLD_PX = 4.0
_STAMP_TRACE = {"token": 0, "last": None, "until": 0.0}


def _start_semantic_stamp_trace(source_name: str) -> None:
    """Emit a short, change-only console trace for the source -> target flow.

    This is deliberately diagnostic-only: it never selects an object, changes
    mode, or creates geometry.  It expires after three minutes and helps identify
    whether a user reached target Edit Mode and actually invoked Stamp.
    """
    _STAMP_TRACE["token"] += 1
    token = _STAMP_TRACE["token"]
    _STAMP_TRACE["last"] = None
    _STAMP_TRACE["until"] = time.monotonic() + 180.0
    print(f"[SEMANTIC_STAMP_TRACE] SOURCE_CAPTURED source={source_name!r}; waiting for target Edit Mode")

    def tick():
        if token != _STAMP_TRACE["token"] or time.monotonic() >= _STAMP_TRACE["until"]:
            if token == _STAMP_TRACE["token"]:
                print("[SEMANTIC_STAMP_TRACE] STOPPED: no Semantic Stamp invocation observed")
            return None
        obj = bpy.context.active_object
        mode = getattr(obj, "mode", "NONE") if obj is not None else "NONE"
        feature = str(obj.get("cgml3_feature") or "") if obj is not None else ""
        selected_faces = -1
        if obj is not None and obj.type == 'MESH' and mode == 'EDIT':
            try:
                selected_faces = sum(1 for face in bmesh.from_edit_mesh(obj.data).faces if face.select)
            except RuntimeError:
                selected_faces = -1
        state = (getattr(obj, "name", None), getattr(obj, "type", None), mode, feature, selected_faces)
        if state != _STAMP_TRACE["last"]:
            _STAMP_TRACE["last"] = state
            print(
                "[SEMANTIC_STAMP_TRACE] "
                f"active={state[0]!r} type={state[1]} mode={state[2]} "
                f"feature={state[3]!r} selected_faces={state[4]}"
            )
        return 0.25

    bpy.app.timers.register(tick, first_interval=0.1)


def invoke_in_viewport_region(context, idname: str, **props):
    """Run a click-in-the-viewport operator from a sidebar button (B02).

    Its mouse projection uses ``context.region``, which is the sidebar there,
    so start it again with the 3D view's WINDOW region; the modal handler then
    binds to that region. This outer call does nothing itself (no undo step).
    ``idname`` is the dotted form ("cgml.facade_draw"); an instance's
    ``bl_idname`` reads back as "CGML_OT_facade_draw"."""
    region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
    if region is not None:
        module, name = idname.split(".")
        with context.temp_override(region=region):
            try:
                getattr(getattr(bpy.ops, module), name)('INVOKE_DEFAULT', **props)
            except RuntimeError:
                pass  # its own report() already told the user why
    return {'CANCELLED'}


def _selected_face(context):
    """The active mesh object + its single selected face index in Edit Mode,
    or ``(None, -1)`` if that precondition isn't met."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return None, -1
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    selected = [f for f in bm.faces if f.select]
    if len(selected) != 1:
        return None, -1
    return obj, selected[0].index


def ray_hits_selected_face(obj, face_index, origin, direction):
    """True when the world ray ``origin + t*direction`` (t > 0) meets face
    ``face_index`` of ``obj`` (Edit-Mode mesh or object data). B18: a toolbar
    click draws only on the selected face; anywhere else it selects."""
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
    else:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
    try:
        bm.faces.ensure_lookup_table()
        if not 0 <= face_index < len(bm.faces):
            return False
        face = bm.faces[face_index]
        inv = obj.matrix_world.inverted()
        o = inv @ Vector(origin)
        d = inv.to_3x3() @ Vector(direction)
        n = face.normal
        denom = d.dot(n)
        if abs(denom) < 1e-9:
            return False
        t = (face.verts[0].co - o).dot(n) / denom
        return t > 0 and bmesh.geometry.intersect_face_point(face, o + d * t)
    finally:
        if obj.mode != 'EDIT':
            bm.free()


def _tool_click_on_selected_face(context, event):
    obj, face_index = _selected_face(context)
    if obj is None:
        return False
    coord = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
    return ray_hits_selected_face(obj, face_index, origin, direction)


def _refuse_placement(operator, op_name, target, face_index=-1, *, record_backed_window=False):
    """Draw/Stamp placement guard. True when refused, already reported.

    ``record_backed_window`` (flag on, Window): the placement runs through
    ``create_feature``, which authors the records itself, so the CAC-P3.0
    Building-wide refusal of any record-backed object no longer applies
    (CAC-P4.10). The same ``create_preflight`` ``create_feature`` runs decides
    instead, before any mutation: a consistent record-backed host wall may
    take another record-backed Window (a record-backed sibling elsewhere in
    the Building never blocks), a host with unmanaged opening rings is
    refused (Case B), a malformed record fails closed. A merged Building
    whose face still needs the implicit Split is not a migrated path: it
    keeps the P3.0 refusal below.

    Otherwise (Door - legacy-only - and the implicit Split) the CAC-P3.0
    guard: the target surface (or merged Building) plus the hierarchy root
    ``promote_opening_host_lod`` rewrites. The Stamp source is not in scope
    (CAC-P3.2a): ``create_feature`` only reads it (own dimensions), never
    copies or mutates its record - AB-14 was the copy, not the read. Flag off,
    ``refuse_if_record_backed`` is always ``None``.
    """
    message = None
    if record_backed_window and target is not None and not surface_needs_split(target, face_index):
        try:
            create_preflight("Window", target, face_index)
        except CreateFeatureError as exc:
            message = f"{op_name}: refused - {exc}"
        except Exception as exc:  # fail closed, like refuse_if_record_backed
            traceback.print_exc()
            message = f"{op_name}: refused - placement preflight failed ({exc}); see the console"
    else:
        scope = [target]
        if target is not None:
            scope.append(lod_promotion_root(target))
        message = refuse_if_record_backed(scope, op_name)
    if message:
        operator.report({'ERROR'}, message)
    return bool(message)


def _wall_alignment_overlay(surface, object_name, position_uv, width, height, subtype):
    """Resolve ``object_name``'s wall object and compute ticket 01/02's
    alignment overlay against it, or ``None`` if the wall no longer exists.
    Shared by Draw's and Stamp's ``draw_callback``s (ticket 03) so both wire
    ``compute_alignment_overlay`` the same way instead of each resolving the
    wall object separately."""
    wall = resolve(surface.semantic_id, object_name)
    if wall is None:
        return None
    return compute_alignment_overlay(surface, wall, position_uv, width, height, subtype=subtype)


def paste_snap(surface, object_name, world_pt, width, height, subtype):
    """B91: Shift while pasting: ``world_pt`` moved so the copy lines up with the windows / doors
    already on the wall (same row, same column; ``snap_to_neighbours``)."""
    wall = resolve(surface.semantic_id, object_name)
    if wall is None:
        return world_pt
    cand = candidate_rect(surface, surface_uv_from_world_point(surface, world_pt), width, height, subtype)
    du, dv = snap_to_neighbours(cand, gather_same_wall_neighbours(surface, wall))
    return world_pt + Vector(surface.horizontal_axis_world) * du + Vector(surface.vertical_axis_world) * dv


def _is_window_object(obj):
    return (
        obj is not None
        and obj.type == 'MESH'
        and str(obj.get("cgml3_feature") or "").strip().lower()
        in {"window", "windowsurface"}
    )


def is_pasteable_installation(obj):
    """B71: a balcony / chimney made by the add-on (it keeps its type and size), so Paste can rebuild it."""
    return (obj is not None and obj.get("cgml3_feature") == "BuildingInstallation"
            and obj.get("cgml_install_params") is not None)


def _is_stamp_source(obj):
    """B50: a Window, or a Door with the ring + fill topology (a ground-notch Door reshapes the
    wall boundary instead of cutting a ring; Stamp copies ring-paired fillings only)."""
    if _is_window_object(obj) or is_pasteable_installation(obj):  # B71: a balcony / chimney made here too
        return True
    return (
        obj is not None and obj.type == 'MESH'
        and str(obj.get("cgml3_feature") or "").strip().lower() in {"door", "doorsurface"}
        and str(obj.get("cgml_authoring_topology") or "") != "ground_notch"
    )


def _is_alignable_opening(obj):
    """Accept a ring-paired opening (Window, or a floating Door) as an Align
    Objects source/target - the same scope `shared/align_objects.py` itself
    enforces, checked here too so the context menu never offers the
    operator for a pair it would immediately refuse."""
    return obj is not None and obj.type == 'MESH' and is_ring_paired_opening(obj)


class CGML_OT_split_surfaces(Operator):
    """Split a merged (un-exploded) Building/BuildingPart mesh into per-surface
    WallSurface/RoofSurface/... objects (ADR 0004 D6), so the selected face
    becomes resolvable to a facade SurfaceContext."""

    bl_idname = "cgml.split_surfaces"
    bl_label = "Split into Surfaces"
    bl_description = (
        "Split this merged building mesh into per-surface WallSurface/"
        "RoofSurface/... objects so facade elements can be drawn on it"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
        if not ok:  # B57 (A2): say why instead of "context is incorrect"
            cls.poll_message_set("Enter Edit Mode on a building and select one face to split")
        return ok

    def execute(self, context):
        obj, face_index = _selected_face(context)
        if obj is None:
            self.report({'WARNING'}, "Select exactly one face to split its building")
            return {'CANCELLED'}
        message = refuse_if_record_backed([obj], "Split into Surfaces")
        if message:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        if is_boundary_surface(obj):
            self.report({'INFO'}, f"'{obj.name}' is already an exploded surface object")
            return {'CANCELLED'}
        if not surface_needs_split(obj, face_index):
            self.report({'WARNING'}, "Selected face has no recognizable surface type to split")
            return {'CANCELLED'}

        exploded = split_into_semantic_surfaces(obj)
        if not exploded:
            self.report({'WARNING'}, f"'{obj.name}' had nothing recognizable to split")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Split '{obj.name}' into per-surface objects")
        return {'FINISHED'}


class _FacadeDraw:
    """Draw a facade element on the selected Edit-Mode face (ADR 0004 D9).

    A thin adapter, nothing more: it resolves the selected face to a
    ``SurfaceContext`` (reusing ticket 02's resolver, including its
    transparent one-click merged-building split), reads a click/drag from
    Openings_Cutter's existing snap + mouse-to-plane machinery to build a
    ``FacadeElementDefinition``/``PlacementRequest``, and calls
    ``place_semantic_element`` - the sole geometry/semantics sink. No
    geometry or semantics logic lives here.

    ``execute()`` alone is fully self-sufficient (object_name/face_index are
    registered properties, not plain Python attributes) so it works
    identically for: the interactive click/drag finishing the modal below,
    Blender's native F9 "Adjust Last Operation" redo panel re-running it with
    tweaked width/height, and a direct headless call.
    """

    bl_description = "Draw an LoD3 facade element on the selected face"
    bl_options = {'REGISTER', 'UNDO'}

    # The element kind - fixed by the menu item that invoked this operator
    # ("the type comes from the menu item", not a mid-flow dialog). "Window"
    # and a floating "Door" are implemented (ticket 01/03/06); Balcony reuses
    # this same operator in a later ticket.
    semantic_type: StringProperty(default="Window", options={'HIDDEN'})
    # B18: started by the toolbar tool's click. That click is the gesture's
    # first press; a click off the selected face passes on to selection.
    from_tool: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    # Resolved target, persisted as registered properties (not plain instance
    # attributes) so they survive Blender re-running execute() for the F9
    # redo panel - re-running invoke() is not part of that flow. The target's
    # gml_id is kept beside its name: the name is only a fallback (P2.3).
    object_name: StringProperty(default="", options={'HIDDEN'})
    object_gml_id: StringProperty(default="", options={'HIDDEN'})
    face_index: IntProperty(default=-1, options={'HIDDEN'})

    # CAC-P4.3: the record-backed Window *this operator instance* has
    # already created, if any - set at the end of a successful execute()
    # and, like object_name/object_gml_id above, a registered property so
    # it survives Blender's F9 redo-panel mechanism (which reverts the
    # scene to before this operator's own last execute() and re-runs it
    # with edited width/height/etc.) even though that revert destroys the
    # fill object and record it names. Empty means "this call is a first
    # draw"; non-empty means "this call is an F9 adjustment of that
    # feature" - execute() uses this, and only this, to decide whether to
    # mint a fresh gml_id or preserve this one.
    feature_gml_id: StringProperty(default="", options={'HIDDEN'})

    # Redo-adjustable placement (F9 panel): the final, already-resolved
    # numbers - the operator itself does all click/drag arithmetic once, up
    # front, so execute() never re-touches the mouse.
    position_uv: FloatVectorProperty(name="Position (U, V)", size=2, default=(0.5, 0.5))
    width: FloatProperty(name="Width", default=DEFAULT_WINDOW_WIDTH, min=0.1)
    height: FloatProperty(name="Height", default=DEFAULT_WINDOW_HEIGHT, min=0.1)
    depth: FloatProperty(name="Depth", default=DEFAULT_WINDOW_DEPTH, min=0.0)
    frame_thickness: FloatProperty(name="Frame Thickness", default=0.0, min=0.0)
    wall_offset: FloatProperty(name="Wall Offset", default=0.0)

    # Door-only (ADR 0004 D3): geometry decides ground-notch vs floating
    # ring+fill by default; this forces either topology - so a false-positive
    # "touches ground" classification on a genuinely floating door (or vice
    # versa) can be overridden.
    door_topology: EnumProperty(
        name="Door Topology",
        items=(
            ('AUTO', "Auto (geometry)", "Decide from the drawn rectangle's position (ADR 0004 D3)"),
            ('FLOATING', "Force Floating (Ring)", "Always use the interior-ring + fill topology, like a Window"),
            ('GROUND', "Force Ground Notch", "Always use the exterior-boundary notch topology"),
        ),
        default='AUTO',
    )
    # B81: Door only. The sidebar / tool header's "Entrance door" type sets it on invoke; F9 can change it.
    entrance: BoolProperty(
        name="Entrance Door", default=False, options={'SKIP_SAVE'},
        description="Stands on the ground (its bottom goes down to the wall's lower edge) and carries the "
                    "building's address (CityGML 2.0 bldg:Door, 3.0 con:DoorSurface address)",
    )
    # Display-only: what execute() actually resolved AUTO to, so the F9 redo
    # panel shows the chosen topology (ticket 06) rather than just the intent.
    resolved_topology: StringProperty(default="", options={'HIDDEN', 'SKIP_SAVE'})

    _handle = None
    _surface = None
    start_mouse = None
    current_mouse = None
    _start_world = None
    _current_world = None
    snap_cache = None
    current_snap_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
        if not ok:  # B57 (A2): say why instead of "context is incorrect"
            cls.poll_message_set("Enter Edit Mode on a WallSurface and select one face")
        return ok

    def _record_backed_window(self):
        """Flag on + Window: this placement runs through ``create_feature``
        (CAC-P3.2) and is guarded by its preflight (CAC-P4.10). Door stays
        legacy-only."""
        return flag.is_enabled() and self.semantic_type.strip().lower() == "window"

    def _resolve_selected_surface(self, context):
        """Resolve the current Edit-Mode selection to a validated WallSurface
        ``SurfaceContext``, or ``None`` (having already ``self.report``'d
        why) if it can't be. Shared by ``invoke()`` and ``execute()``'s
        no-``object_name`` fallback so both entry points validate the same
        way, once."""
        obj, face_index = _selected_face(context)
        if obj is None:
            self.report({'WARNING'}, "Select exactly one face to draw a facade element")
            return None
        # Before resolution: on a merged Building it runs an implicit Split.
        if _refuse_placement(self, f"Draw {self.semantic_type}", obj, face_index,
                             record_backed_window=self._record_backed_window()):
            return None
        try:
            surface = resolve_surface_context_for_face(obj, face_index)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return None
        if surface.semantic_type.lower() != "wallsurface":
            self.report(
                {'WARNING'},
                f"Draw {self.semantic_type} needs a WallSurface face, "
                f"got {surface.semantic_type or 'an unrecognized surface'}",
            )
            return None
        return surface

    # -- interactive entry: resolve the face, then wait for a click/drag ----
    @modal_hint.clears_status
    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Only available in the 3D viewport.")
            return {'CANCELLED'}
        if context.region is None or context.region.type != 'WINDOW':
            door = self.semantic_type.strip().lower() == "door"
            return invoke_in_viewport_region(context, "cgml.door_draw" if door else "cgml.facade_draw",
                                             semantic_type=self.semantic_type)
        if self.from_tool and not _tool_click_on_selected_face(context, event):
            return {'PASS_THROUGH'}

        surface = self._resolve_selected_surface(context)
        if surface is None:
            return {'CANCELLED'}

        # Reset to this type's own default size on every fresh invocation -
        # otherwise Blender's "remember last operator values" would leak a
        # Window's last-drawn width/height into a Door draw (or vice versa),
        # since both share this operator's bl_idname. A click-drag below
        # still overwrites these with the exact dragged rectangle.
        if self.semantic_type.strip().lower() == "door":
            self.width = DEFAULT_DOOR_WIDTH
            self.height = DEFAULT_DOOR_HEIGHT
        else:
            self.width = DEFAULT_WINDOW_WIDTH
            self.height = DEFAULT_WINDOW_HEIGHT
        # B07: the type picked in the sidebar (2 Edit) sets the single-click size.
        kind = "Door" if self.semantic_type.strip().lower() == "door" else "Window"
        picked_key = getattr(context.window_manager, f"cgml_{kind.lower()}_type", "")
        picked = opening_types.size(picked_key, kind)
        if picked:
            self.width, self.height = picked
        self.entrance = kind == "Door" and picked_key == opening_types.ENTRANCE

        # A fresh interactive invocation is always a new draw, never an F9
        # adjustment of a previous one (Blender gives every new invoke() a
        # new operator instance; only its own later F9 redo re-runs
        # execute() on this same instance) - CAC-P4.3.
        self.feature_gml_id = ""

        self.object_name = surface.object_name
        self.object_gml_id = surface.semantic_id
        self.face_index = surface.face_indices[0]
        self._surface = surface
        self.start_mouse = None
        self._start_world = None
        self._current_world = None
        self.snap_cache = build_opening_cutter_snap_cache(context)
        self.current_mouse, self.snap_cache, self.current_snap_active = resolve_opening_cutter_snap_mouse(
            context, (event.mouse_region_x, event.mouse_region_y), snap_cache=self.snap_cache,
        )

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        modal_hint.status(context, modal_hint.hint(("Drag", "Draw"), ("Esc/RMB", "Cancel"),
                                                   modal_hint.snap_note(context)))
        if self.from_tool:
            return self._press(context, (event.mouse_region_x, event.mouse_region_y))
        return {'RUNNING_MODAL'}

    def _press(self, context, mouse_xy):
        """First corner of the click/drag (the modal's press, or the tool's click)."""
        world_pt, resolved_mouse, self.snap_cache, self.current_snap_active = project_opening_cutter_mouse_to_plane(
            context, mouse_xy, self._surface.origin_world, self._surface.normal_world, snap_cache=self.snap_cache,
        )
        if world_pt is None:
            self.report({'WARNING'}, "Could not project the click onto the surface plane.")
            self._finish(context)
            return {'CANCELLED'}
        self.start_mouse = resolved_mouse
        self._start_world = world_pt
        self._current_world = world_pt
        self.current_mouse = resolved_mouse
        return {'RUNNING_MODAL'}

    @modal_hint.clears_status
    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            mouse_xy = (event.mouse_region_x, event.mouse_region_y)
            if self.start_mouse is not None:
                # Dragging: also project onto the surface plane every move
                # (not just at release) so the alignment overlay's candidate
                # rect - and its live width/height readout - track the drag
                # continuously instead of only appearing at the end.
                self._current_world, self.current_mouse, self.snap_cache, self.current_snap_active = (
                    project_opening_cutter_mouse_to_plane(
                        context, mouse_xy, self._surface.origin_world, self._surface.normal_world,
                        snap_cache=self.snap_cache,
                    )
                )
            else:
                self.current_mouse, self.snap_cache, self.current_snap_active = resolve_opening_cutter_snap_mouse(
                    context, mouse_xy, snap_cache=self.snap_cache,
                )
            return {'RUNNING_MODAL'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish(context)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            return self._press(context, (event.mouse_region_x, event.mouse_region_y))

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            if self.start_mouse is None:
                return {'RUNNING_MODAL'}

            mouse_xy = (event.mouse_region_x, event.mouse_region_y)
            world_pt, resolved_mouse, self.snap_cache, self.current_snap_active = project_opening_cutter_mouse_to_plane(
                context, mouse_xy, self._surface.origin_world, self._surface.normal_world, snap_cache=self.snap_cache,
            )
            self._finish(context)
            if world_pt is None:
                self.report({'WARNING'}, "Could not project the click onto the surface plane.")
                return {'CANCELLED'}

            dx = resolved_mouse[0] - self.start_mouse[0]
            dy = resolved_mouse[1] - self.start_mouse[1]
            if (dx * dx + dy * dy) < (DRAG_CLICK_THRESHOLD_PX * DRAG_CLICK_THRESHOLD_PX):
                # B71: a click places nothing (it dropped a default window the user never drew); drag draws
                self.report({'INFO'}, f"Drag on the face to draw the {self.semantic_type.lower()}")
                return {'CANCELLED'}
            else:
                # Click-drag: exact rectangle between the two corners.
                position_uv, width, height = surface_rect_from_drag(self._surface, self._start_world, world_pt)
                self.position_uv = position_uv
                self.width = width
                self.height = height

            # A direct Python method call, not a fresh bpy.ops.cgml.facade_draw()
            # dispatch - the whole gesture (invoke's face resolve through this
            # placement) stays inside the ONE top-level operator invocation
            # Blender pushes a single undo step for. This is precisely what the
            # old flow got wrong: SURFACEBOX_OT_DrawBox finished as its own
            # top-level operator, then separately re-dispatched
            # bpy.ops.surfacebox.type_dialog("INVOKE_DEFAULT") - a second,
            # independent top-level call, hence a second undo step.
            return self.execute(context)

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._finish(context)

    def _finish(self, context):
        modal_hint.status(context)
        self.snap_cache = None
        self.current_snap_active = False
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None

    @staticmethod
    def draw_callback(self, context):
        if self.start_mouse is not None and self.current_mouse is not None:
            x1, y1 = self.start_mouse
            x2, y2 = self.current_mouse
            verts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            indices = ((0, 1), (1, 2), (2, 3), (3, 0))

            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            batch = batch_for_shader(shader, "LINES", {"pos": verts}, indices=indices)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(2.0)
            shader.bind()
            shader.uniform_float("color", (0.1, 0.7, 1.0, 1.0))
            batch.draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")

        self._draw_alignment_overlay(context)

        if self.current_snap_active:
            draw_opening_cutter_snap_marker(self.current_mouse)

    def _draw_alignment_overlay(self, context):
        """Alignment guides + neighbour-distance + live size, while
        dragging (ticket 02) - reuses the shared render helper so Stamp
        (ticket 03) draws identically instead of re-deriving this."""
        if self._start_world is None or self._current_world is None:
            return
        position_uv, width, height = surface_rect_from_drag(self._surface, self._start_world, self._current_world)
        overlay = _wall_alignment_overlay(self._surface, self.object_name, position_uv, width, height, self.semantic_type)
        if overlay is None:
            return
        draw_alignment_overlay(context, overlay, self.current_mouse, width_m=width, height_m=height)

    # -- headless-sufficient placement: redo panel + direct/testing calls ---
    def execute(self, context):
        if self.object_name or self.object_gml_id:
            wall = resolve(self.object_gml_id, self.object_name)
            if wall is None:
                self.report({'ERROR'}, f"'{self.object_name or self.object_gml_id}' no longer exists")
                return {'CANCELLED'}
            if _refuse_placement(self, f"Draw {self.semantic_type}", wall, self.face_index,
                                 record_backed_window=self._record_backed_window()):
                return {'CANCELLED'}
            try:
                surface = resolve_surface_context(wall, self.face_index)
            except ValueError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
        else:
            surface = self._resolve_selected_surface(context)
            if surface is None:
                return {'CANCELLED'}
            self.face_index = surface.face_indices[0]
        self.object_name = surface.object_name
        self.object_gml_id = surface.semantic_id

        is_door = self.semantic_type.strip().lower() == "door"
        door_touches_ground_override = None
        if is_door and self.door_topology != 'AUTO':
            door_touches_ground_override = self.door_topology == 'GROUND'

        definition = FacadeElementDefinition(
            semantic_type=self.semantic_type,
            width=self.width,
            height=self.height,
            depth=self.depth,
            frame_thickness=self.frame_thickness,
            wall_offset=self.wall_offset,
            generator_name="facade_draw",
            door_touches_ground=door_touches_ground_override,
            entrance=is_door and self.entrance,
        )
        placement = PlacementRequest(position_uv=tuple(self.position_uv), source="manual_draw")

        # CAC-P3.2: flag on routes Window creation through the record-backed
        # create_feature lifecycle (fresh FeatureRecord + host ring record,
        # same undo step). Door and flag-off stay on the legacy-only sink -
        # PLAN.md P3.2 scope.
        use_create_feature = flag.is_enabled() and self.semantic_type.strip().lower() == "window"

        # CAC-P4.3: feature_gml_id is empty on this operator instance's
        # first execute() (a genuine new draw - mint a fresh gml_id, the
        # existing_gml_id=None default) and non-empty on every later F9
        # redo-panel re-run of the *same* instance (an adjustment of the
        # Window that first execute() created - preserve its gml_id instead
        # of minting a new one). Blender's F9 mechanism reverts the scene to
        # before that first execute() and re-runs it, so the object being
        # placed here is always physically new either way; only the id
        # survives, via this operator property, not via the object.
        existing_gml_id = self.feature_gml_id.strip() if use_create_feature else ""
        try:
            if use_create_feature:
                fill = create_feature(
                    "Window", surface, definition, placement,
                    existing_gml_id=existing_gml_id or None,
                )
                self.feature_gml_id = str(fill.get("gml_id") or "")
            else:
                fill = place_semantic_element(definition, surface, placement)
        except (ValueError, RuntimeError, NotImplementedError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if is_door:
            self.resolved_topology = (
                "Ground notch" if fill.get("cgml_authoring_topology") == "ground_notch"
                else "Floating (ring + fill)"
            )
        else:
            self.resolved_topology = ""

        self.report({'INFO'}, f"Placed {self.semantic_type} '{fill.name}' on '{surface.object_name}'")
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "position_uv")
        layout.prop(self, "width")
        layout.prop(self, "height")
        layout.prop(self, "depth")
        layout.prop(self, "frame_thickness")
        layout.prop(self, "wall_offset")
        if self.semantic_type.strip().lower() == "door":
            layout.prop(self, "entrance")
            layout.prop(self, "door_topology")
            if self.resolved_topology:
                layout.label(text=f"Topology: {self.resolved_topology}")


class CGML_OT_facade_draw(_FacadeDraw, Operator):
    bl_idname = "cgml.facade_draw"
    bl_label = "Window"  # B33: F9 / undo say what the user picked


class CGML_OT_door_draw(_FacadeDraw, Operator):
    bl_idname = "cgml.door_draw"
    bl_label = "Door"
    semantic_type: StringProperty(default="Door", options={'HIDDEN'})


class CGML_OT_set_semantic_stamp_source(Operator):
    """Remember the selected CityGML Window or Door as the next Stamp template (B50); B71: Copy, also a balcony / chimney."""

    bl_idname = "cgml.set_semantic_stamp_source"
    bl_label = "Copy"
    bl_description = "Copy the selected window, door, balcony or chimney; Paste puts copies on a selected face"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ok = _is_stamp_source(context.active_object) and context.active_object.mode == 'OBJECT'
        if not ok:
            cls.poll_message_set("In Object Mode, make a window, door, balcony or chimney the active object")
        return ok

    def execute(self, context):
        source = context.active_object
        context.scene["cgml_semantic_stamp_source"] = source.name
        context.scene["cgml_semantic_stamp_source_gml_id"] = str(source.get("gml_id") or "")
        # The source is now durable scene state.  Leaving it as the active
        # object makes the very next Tab enter Edit Mode on the source Window,
        # which is the opposite of the intended source -> target workflow.
        # Clear only the viewport selection; no source geometry is copied or
        # otherwise changed here.
        source.select_set(False)
        context.view_layer.objects.active = None
        _start_semantic_stamp_trace(source.name)
        self.report({'INFO'}, f"Copied '{source.name}'; Edit Facade, select a face, Paste")
        return {'FINISHED'}


def _stamp_place_semantic_element(source, surface, world_pt):
    """Place a Semantic Stamp copy of ``source`` on ``surface`` at ``world_pt``.

    CAC-P3.2a: flag on routes through ``create_feature`` (mirrors Draw's
    P3.2 routing), so the stamped Window gets a fresh record of its own -
    own gml_id, target wall as host, ``generator="semantic_stamp"`` - and
    the target wall gets its RingRecord, exactly as Draw does. The source's
    record is never copied (AB-14; also guarded in
    ``facade_placement._STAMP_IDENTITY_OVERRIDE_KEYS``). Flag off stays on
    the legacy-only sink.
    """
    if flag.is_enabled():
        definition = definition_from_source_object(source)
        placement = PlacementRequest(
            position_uv=surface_uv_from_world_point(surface, world_pt), source="semantic_stamp",
        )
        return create_feature(definition.semantic_type, surface, definition, placement, generator="semantic_stamp")
    return place_semantic_element(source_object=source, surface_context=surface, position_world=world_pt)


class SEMANTIC_OT_stamp(Operator):
    """Click a target WallSurface face repeatedly to drop independent copies
    of the remembered Window ("Semantic stamp" - CONTEXT.md's Facade
    Authoring glossary: "Repeatedly placing a chosen element ... by clicking
    target surfaces, each click producing a new independent placement").

    A ghost preview of the source Window's own footprint (its own measured
    width/height - ``definition_from_source_object``) follows the mouse,
    projected onto the target face's plane with the same Openings_Cutter
    snap/mouse-to-plane machinery Draw Window uses. Each left-click places one
    independent copy; Esc/right-click ends the session, which is one undo step
    (B33) and keeps every copy. ``execute()`` alone (no ``invoke()``/modal) stays self-sufficient
    for the F9 redo panel and headless/direct calls, placing exactly one copy
    at ``position_uv`` (default: the target face centre).
    """

    bl_idname = "cgml.semantic_stamp"
    bl_label = "Paste"  # B71: was Stamp; the copied window / door / balcony / chimney
    bl_description = "Click the selected face to paste copies of what you copied"
    bl_options = {'REGISTER', 'UNDO'}

    # Persisted for F9 redo and headless calls.  The source is normally set
    # from Object Mode using CGML_OT_set_semantic_stamp_source before entering
    # Edit Mode; a direct operator caller may set it explicitly instead.
    source_object_name: StringProperty(default="", options={'HIDDEN'})
    source_gml_id: StringProperty(default="", options={'HIDDEN'})
    # B19: started by the Stamp tool's click on the selected face; that click
    # stamps at once, further clicks keep stamping until Esc.
    from_tool: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    # Resolved target, persisted as registered properties (not plain instance
    # attributes) so they survive Blender re-running execute() for the F9
    # redo panel - re-running invoke() is not part of that flow. Mirrors
    # CGML_OT_facade_draw's contract, including the gml_id beside the name.
    object_name: StringProperty(default="", options={'HIDDEN'})
    object_gml_id: StringProperty(default="", options={'HIDDEN'})
    face_index: IntProperty(default=-1, options={'HIDDEN'})
    position_uv: FloatVectorProperty(name="Position (U, V)", size=2, default=(0.5, 0.5))

    _handle = None
    _surface = None
    _source_name = ""
    _source_gml_id = ""
    _ghost_width = 0.0
    _ghost_height = 0.0
    _ghost_subtype = ""
    _ghost_screen_corners = None
    _overlay = None
    _placed_count = 0
    _shift = False  # B91: Shift held = line the copy up with the openings on the wall
    _mouse_xy = (0, 0)
    snap_cache = None
    current_mouse = None
    current_snap_active = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        ok = obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'
        if not ok:  # B57 (A2): say why instead of "context is incorrect"
            cls.poll_message_set("Enter Edit Mode on a WallSurface and select one face to stamp on")
        return ok

    def _source(self, context):
        if self.source_object_name or self.source_gml_id:
            gml_id, name = self.source_gml_id, self.source_object_name
        else:
            gml_id = str(context.scene.get("cgml_semantic_stamp_source_gml_id") or "")
            name = str(context.scene.get("cgml_semantic_stamp_source") or "")
        source = resolve(gml_id, name) if (name or gml_id) else None
        if _is_stamp_source(source):
            return source

        # This also supports a source Window kept selected alongside the
        # target while entering Edit Mode, without requiring an extra action.
        for candidate in context.selected_objects:
            if candidate is not context.active_object and _is_stamp_source(candidate):
                return candidate
        return None

    def _resolve_target_surface(self, context):
        """The single selected Edit-Mode face, resolved and validated as a
        WallSurface ``SurfaceContext`` (already-``self.report``'d on failure) -
        Stamp's target contract matches Draw Window's single-face contract;
        repeated clicks (not multi-face selection) are how Stamp places more
        than one copy."""
        obj, face_index = _selected_face(context)
        if obj is None:
            self.report({'WARNING'}, "Select exactly one target WallSurface face for Semantic Stamp")
            return None
        try:
            surface = resolve_surface_context_for_face(obj, face_index)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return None
        if surface.semantic_type.lower() != "wallsurface":
            self.report(
                {'ERROR'},
                f"Semantic Stamp Window needs a WallSurface face, got "
                f"{surface.semantic_type or 'an unrecognized surface'}",
            )
            return None
        return surface

    # -- interactive entry: click repeatedly to place copies -----------------
    @modal_hint.clears_status
    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Only available in the 3D viewport.")
            return {'CANCELLED'}
        if context.region is None or context.region.type != 'WINDOW':
            return invoke_in_viewport_region(context, "cgml.semantic_stamp")
        if self.from_tool and not _tool_click_on_selected_face(context, event):
            return {'PASS_THROUGH'}

        source = self._source(context)
        if source is None:
            self.report(
                {'ERROR'},
                "Select a CityGML window, door, balcony or chimney in Object Mode and Copy it first",
            )
            return {'CANCELLED'}
        if is_pasteable_installation(source):
            return self._paste_installation(context, event, source)

        # Before target resolution: on a merged Building it runs an implicit Split.
        if _refuse_placement(self, "Semantic Stamp", *_selected_face(context),
                             record_backed_window=flag.is_enabled()):
            return {'CANCELLED'}
        surface = self._resolve_target_surface(context)
        if surface is None:
            return {'CANCELLED'}

        try:
            definition = definition_from_source_object(source)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        self._source_name = source.name
        self._source_gml_id = str(source.get("gml_id") or "")
        self._surface = surface
        self._ghost_width = definition.width
        self._ghost_height = definition.height
        self._ghost_subtype = definition.semantic_type
        self._ghost_screen_corners = None
        self._overlay = None
        self._placed_count = 0
        self._shift = event.shift
        self.object_name = surface.object_name
        self.object_gml_id = surface.semantic_id
        self.face_index = surface.face_indices[0]

        self.snap_cache = build_opening_cutter_snap_cache(context)
        mouse_xy = (event.mouse_region_x, event.mouse_region_y)
        self.current_mouse, self.snap_cache, self.current_snap_active = resolve_opening_cutter_snap_mouse(
            context, mouse_xy, snap_cache=self.snap_cache,
        )
        self._update_ghost(context, mouse_xy)

        args = (self, context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        from ..ui.tools import _preview
        _preview["pts"] = None  # B74: the ghost takes over from the hover preview (the modal eats MOUSEMOVE)
        context.area.tag_redraw()
        print(f"[SEMANTIC_STAMP_TRACE] MODAL_ENTER source={source.name!r} target={surface.object_name!r}")
        modal_hint.status(context, modal_hint.hint(("LMB", "Stamp a copy"), ("Shift", "Line up with the wall's openings"),
                                                   ("Esc/RMB", "Done (copies stay)"), modal_hint.snap_note(context)))
        if self.from_tool:
            self._place_at_mouse(context, mouse_xy)
        return {'RUNNING_MODAL'}

    def _paste_installation(self, context, event, source):
        """B71: one copy of the copied balcony / chimney (its type and size) where the selected face is
        clicked; the Paste tool stays on (select a face again, click again for another)."""
        from .installation_authoring import paste_args, ray_under_mouse
        if not self.from_tool:
            self.report({'WARNING'}, "Paste a balcony / chimney with the Paste tool: click on the selected face")
            return {'CANCELLED'}
        op, args, why = paste_args(context, source, *ray_under_mouse(context, event))
        if args is None:
            self.report({'WARNING'}, why)
            return {'CANCELLED'}
        res = op('EXEC_DEFAULT', **args)
        if 'FINISHED' in res:
            bpy.ops.ed.undo_push(message=self.bl_label)  # CANCELLED below: F9 would re-run a window stamp
        return {'CANCELLED'}

    def _update_ghost(self, context, mouse_xy):
        """Recompute the click-follow ghost rectangle (the source Window's
        own measured footprint, centred on the mouse's current projection
        onto the target plane) as 2D region points for ``draw_callback``, and
        - reusing Draw's same overlay wiring (ticket 02/03) - the alignment
        overlay for that same candidate footprint against the target wall's
        current opening set (including anything stamped earlier this
        session, since ``gather_same_wall_neighbours`` reads the wall's
        children live)."""
        world_pt = self._project(context, mouse_xy)
        if world_pt is None:
            self._ghost_screen_corners = None
            self._overlay = None
            return

        position_uv = surface_uv_from_world_point(self._surface, world_pt)
        self._overlay = _wall_alignment_overlay(
            self._surface, self.object_name, position_uv, self._ghost_width, self._ghost_height, self._ghost_subtype,
        )

        h_axis = Vector(self._surface.horizontal_axis_world).normalized()
        v_axis = Vector(self._surface.vertical_axis_world).normalized()
        half_w = self._ghost_width * 0.5
        half_h = self._ghost_height * 0.5
        corners_world = [
            world_pt + h_axis * -half_w + v_axis * -half_h,
            world_pt + h_axis * half_w + v_axis * -half_h,
            world_pt + h_axis * half_w + v_axis * half_h,
            world_pt + h_axis * -half_w + v_axis * half_h,
        ]
        region, rv3d = context.region, context.space_data.region_3d
        screen_corners = [view3d_utils.location_3d_to_region_2d(region, rv3d, p) for p in corners_world]
        self._ghost_screen_corners = screen_corners if all(c is not None for c in screen_corners) else None

    def _project(self, context, mouse_xy):
        """The mouse on the target plane (Openings_Cutter snap), lined up with the wall's openings
        while Shift is held (B91); None off the plane."""
        self._mouse_xy = mouse_xy
        world_pt, self.current_mouse, self.snap_cache, self.current_snap_active = project_opening_cutter_mouse_to_plane(
            context, mouse_xy, self._surface.origin_world, self._surface.normal_world, snap_cache=self.snap_cache,
        )
        if world_pt is not None and self._shift:
            world_pt = paste_snap(self._surface, self.object_name, world_pt,
                                  self._ghost_width, self._ghost_height, self._ghost_subtype)
        return world_pt

    def _place_at_mouse(self, context, mouse_xy):
        world_pt = self._project(context, mouse_xy)
        if world_pt is None:
            self.report({'WARNING'}, "Could not project the click onto the surface plane.")
            return

        source = resolve(self._source_gml_id, self._source_name)
        if source is None:
            self.report({'ERROR'}, f"Semantic Stamp source '{self._source_name}' no longer exists")
            return
        target = resolve(self._surface.semantic_id, self._surface.object_name)
        if _refuse_placement(self, "Semantic Stamp", target, self._surface.face_indices[0],
                             record_backed_window=flag.is_enabled()):
            return

        try:
            # The placement service reads the source Window's own geometry
            # directly (its own facade frame, not a resolved host wall - see
            # shared/facade_placement._source_window_axes), so no Blender
            # parent/metadata resolution or reparenting is needed here.
            created = _stamp_place_semantic_element(source, self._surface, world_pt)
        except (ValueError, RuntimeError, NotImplementedError) as e:
            print(f"[SEMANTIC_STAMP_TRACE] click placement error={e}")
            self.report({'ERROR'}, str(e))
            return

        self._placed_count += 1
        print(f"[SEMANTIC_STAMP_TRACE] PLACED #{self._placed_count} {created.name!r} on {self._surface.object_name!r}")
        self.report({'INFO'}, f"Stamped '{created.name}' ({self._placed_count} this session)")

        # Refresh immediately (not just on the next MOUSEMOVE) so a second
        # click at the same spot - "tık tık" placement - already sees this
        # click's own placement as a neighbour.
        self._update_ghost(context, mouse_xy)

    @modal_hint.clears_status
    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            self._shift = event.shift
            self._update_ghost(context, (event.mouse_region_x, event.mouse_region_y))
            return {'RUNNING_MODAL'}

        if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'} and event.value in {'PRESS', 'RELEASE'}:
            self._shift = event.value == 'PRESS'  # B91: the ghost jumps at once, no mouse move needed
            self._update_ghost(context, self._mouse_xy)
            return {'RUNNING_MODAL'}

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            self._finish(context)
            print(f"[SEMANTIC_STAMP_TRACE] MODAL_END placed={self._placed_count}")
            return self._end_session()

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._shift = event.shift
            self._place_at_mouse(context, (event.mouse_region_x, event.mouse_region_y))
            return {'RUNNING_MODAL'}

        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._finish(context)

    def _end_session(self):
        """B33: the whole session is one undo step; Esc keeps every copy. CANCELLED on purpose:
        a FINISHED would offer F9, whose redo re-runs execute() = one copy, dropping the others."""
        if self._placed_count:
            bpy.ops.ed.undo_push(message=f"{self.bl_label} ({self._placed_count})")
        return {'CANCELLED'}

    def _finish(self, context):
        modal_hint.status(context)
        self.snap_cache = None
        self.current_snap_active = False
        self._ghost_screen_corners = None
        self._overlay = None
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None

    @staticmethod
    def draw_callback(self, context):
        if self._ghost_screen_corners:
            (x1, y1), (x2, y2), (x3, y3), (x4, y4) = self._ghost_screen_corners
            verts = [(x1, y1), (x2, y2), (x3, y3), (x4, y4)]
            indices = ((0, 1), (1, 2), (2, 3), (3, 0))

            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            batch = batch_for_shader(shader, "LINES", {"pos": verts}, indices=indices)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(2.0)
            shader.bind()
            shader.uniform_float("color", (0.1, 0.7, 1.0, 0.9))
            batch.draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")

        # Same guides + neighbour-distance label as Draw (ticket 02); no live
        # size readout - width_m/height_m omitted - since the stamped
        # element's size is fixed by its captured source, not being dragged.
        if self._overlay is not None:
            draw_alignment_overlay(context, self._overlay, self.current_mouse)

        if self.current_snap_active:
            draw_opening_cutter_snap_marker(self.current_mouse)

    # -- headless-sufficient placement: redo panel + direct/testing calls ----
    def execute(self, context):
        source = self._source(context)
        if source is None:
            self.report(
                {'ERROR'},
                "Select a CityGML window, door, balcony or chimney in Object Mode and Copy it first",
            )
            return {'CANCELLED'}
        if is_pasteable_installation(source):
            self.report({'ERROR'}, "Paste a balcony / chimney with the Paste tool: click on the selected face")
            return {'CANCELLED'}

        if self.object_name or self.object_gml_id:
            wall = resolve(self.object_gml_id, self.object_name)
            if wall is None:
                self.report({'ERROR'}, f"'{self.object_name or self.object_gml_id}' no longer exists")
                return {'CANCELLED'}
            if _refuse_placement(self, "Semantic Stamp", wall, self.face_index,
                                 record_backed_window=flag.is_enabled()):
                return {'CANCELLED'}
            try:
                surface = resolve_surface_context(wall, self.face_index)
            except ValueError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
        else:
            if _refuse_placement(self, "Semantic Stamp", *_selected_face(context),
                                 record_backed_window=flag.is_enabled()):
                return {'CANCELLED'}
            surface = self._resolve_target_surface(context)
            if surface is None:
                return {'CANCELLED'}
            self.face_index = surface.face_indices[0]
        self.object_name = surface.object_name
        self.object_gml_id = surface.semantic_id

        world_pt = surface_world_point_from_uv(surface, tuple(self.position_uv))
        try:
            created = _stamp_place_semantic_element(source, surface, world_pt)
        except (ValueError, RuntimeError, NotImplementedError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        _STAMP_TRACE["until"] = 0.0
        self.report({'INFO'}, f"Stamped '{created.name}' onto '{surface.object_name}'")
        return {'FINISHED'}


class CGML_OT_align_objects(Operator):
    """Align Objects (ADR 0006 / copy-paste-object-behaviour ticket 04): move
    the earlier-selected opening onto the active opening's edge, along
    whichever axis their relative position implies - Blender's own
    align/snap-to-active convention, no axis picked manually. A thin
    adapter, nothing more: every geometry/decision rule lives in
    ``shared/align_objects.py``.
    """

    bl_idname = "cgml.align_objects"
    bl_label = "Align Objects"
    bl_description = (
        "Move the selected opening onto the active opening's top/bottom or "
        "left/right edge, inferred from their relative position"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT':
            return False
        target = context.active_object
        selected = context.selected_objects
        if target is None or len(selected) != 2 or target not in selected:
            return False
        source = next((o for o in selected if o is not target), None)
        return _is_alignable_opening(source) and _is_alignable_opening(target)

    def execute(self, context):
        target = context.active_object
        source = next(o for o in context.selected_objects if o is not target)
        source_record = flag.is_enabled() and storage.has_record(source)
        target_record = flag.is_enabled() and storage.has_record(target)
        if source_record or target_record:
            # Align changes only Blender's non-active source.  A record-backed
            # target is therefore safe as a reference; a record-backed source
            # is committed atomically with its own host wall.  A legacy source
            # against a record target has no coherent record ownership, so it
            # remains an explicit refusal rather than a partial migration.
            if not source_record:
                self.report({'ERROR'}, "Align Objects: refused - a record-backed target requires a record-backed Window source")
                return {'CANCELLED'}
            if target_record:
                target_records = storage.read_record(target)
                target_feature = target_records.feature(str(target.get("gml_id") or "")) if target_records else None
                if target_feature is None or target_feature.kind != "Window":
                    self.report({'ERROR'}, "Align Objects: refused - record-backed targets are supported for Window only")
                    return {'CANCELLED'}
            try:
                result = align_existing_window(str(source.get("gml_id") or ""), target)
            except EditSessionError as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
            self.report({'INFO'}, result.message)
            return {'FINISHED'}
        try:
            result = align_source_to_target(source, target)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        message = f"Aligned '{source.name}' to '{target.name}' ({result.axis} match, {result.edge} edge)"
        if not result.synced:
            message += " - no ring pairing found to re-cut"
        self.report({'INFO'}, message)
        return {'FINISHED'}


class CGML_OT_delete_feature(Operator):
    """Delete a record-backed Window's complete semantic feature (CAC-P3.4):
    its own record, the host WallSurface's ring record and legacy ring
    face, and the Window object itself, in one undo step
    (``shared/authoring_core/blender/usecases.py::delete_feature``).

    This is the supported delete path for a record-backed Window while
    the experimental authoring core is on. Native Blender X/Delete on such
    a Window is *not* routed through here (AB-02's native-X bypass is out
    of scope for CAC-P3.4 - PLAN.md P3.4 NATIVE X DELETE); it still leaves
    the host's ring behind, which ``domain/consistency.py``'s validator
    keeps catching.
    """

    bl_idname = "cgml.delete_feature"
    bl_label = "Delete Window"
    bl_description = "Delete this Window's complete feature, including its host wall's ring"
    bl_options = {'REGISTER', 'UNDO'}

    object_gml_id: StringProperty(name="gml_id", default="", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        if not flag.is_enabled():
            return False
        obj = context.active_object
        return _is_window_object(obj) and storage.has_record(obj)

    def execute(self, context):
        obj = context.active_object
        gml_id = self.object_gml_id.strip() or str(obj.get("gml_id") or "").strip()
        try:
            delete_feature(gml_id)
        except (DeleteFeatureError, ValueError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Deleted Window '{gml_id}'")
        return {'FINISHED'}


class CGML_MT_facade_context(Menu):
    """CityGML Facade quick-actions submenu (Edit-Mode face context menu,
    ADR 0004 D9). Draw Balcony is added by a later ticket."""

    bl_label = "Facade"
    bl_idname = "CGML_MT_facade_context"

    def draw(self, context):
        layout = self.layout
        layout.operator(
            CGML_OT_facade_draw.bl_idname, text="Draw Window", icon='MOD_BUILD',
        ).semantic_type = 'Window'
        layout.operator(
            CGML_OT_door_draw.bl_idname, text="Draw Door", icon='MOD_BUILD',
        ).semantic_type = 'Door'
        # B71: Paste = the Paste tool (a click on the selected face pastes what you copied)
        layout.operator("cgml.tool_pick", text="Paste", icon='PASTEDOWN').key = "stamp"
        layout.separator()
        layout.operator(CGML_OT_split_surfaces.bl_idname, icon='MOD_EXPLODE')


def draw_facade_context_menu(self, context):
    """Append 'CityGML Facade' to the Edit-Mode mesh face context menu."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return
    layout = self.layout
    layout.separator()
    layout.menu(CGML_MT_facade_context.bl_idname, icon='HOME')


def draw_semantic_stamp_source_context_menu(self, context):
    """Object-mode source capture for the first step of the Stamp flow."""
    if _is_stamp_source(context.active_object):
        self.layout.separator()
        self.layout.operator(CGML_OT_set_semantic_stamp_source.bl_idname, icon='COPYDOWN')


def draw_align_objects_context_menu(self, context):
    """Object-mode 'Align Objects' action: shown only once a source opening
    and an active target opening are both selected, per ``CGML_OT_align_objects.poll``."""
    if not CGML_OT_align_objects.poll(context):
        return
    self.layout.separator()
    self.layout.operator(CGML_OT_align_objects.bl_idname, icon='SNAP_ON')


def draw_delete_feature_context_menu(self, context):
    """Object-mode delete action for a record-backed Window (CAC-P3.4) -
    the explicit, narrow authoring-core delete path, never a hook on
    native X/Delete (PLAN.md P3.4 NATIVE X DELETE)."""
    obj = context.active_object
    if not (flag.is_enabled() and _is_window_object(obj) and storage.has_record(obj)):
        return
    self.layout.separator()
    op = self.layout.operator(CGML_OT_delete_feature.bl_idname, icon='TRASH')
    op.object_gml_id = str(obj.get("gml_id") or "")


classes = (
    CGML_OT_split_surfaces,
    CGML_OT_facade_draw,
    CGML_OT_door_draw,
    CGML_OT_set_semantic_stamp_source,
    SEMANTIC_OT_stamp,
    CGML_OT_align_objects,
    CGML_OT_delete_feature,
    CGML_MT_facade_context,
)
