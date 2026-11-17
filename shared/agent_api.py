"""Agent API (agent plan A3/A4): read the scene as JSON and author without the mouse.

No ``bpy.context`` selection, active object or mode is read or changed: an agent (or a
script) names everything by ``gml_id`` and gets plain JSON-serialisable dicts back.
See ``docs/agent-api.md``.

Coordinates: Blender world metres. CRS coordinate = world + ``offset`` (``inspect()["offset"]``).
A surface face's frame is ``origin`` (min-u/min-v corner), ``u_axis`` (horizontal), ``v_axis``
(up the face), ``width``/``height`` (m); a placement's ``(u, v)`` is the fractional centre in it.
"""
from __future__ import annotations

import bpy
from mathutils import Vector

from .export_scope import top_level_features
from .facade_surface_resolver import compute_facade_frame
from .lod_helpers import object_lod
from .object_helpers import face_material
from .planarity import DEFAULT_TOLERANCE_M, non_planar_faces
from .geometry_checks import NON_PLANAR_TOL_M as NON_PLANAR_TOL
from .ring_opening_sync import wall_interior_ring_faces
from .semantic_tree import build_semantic_tree, is_boundary_surface, is_opening

SCHEMA = "cgml-inspect/1"
BUILDING_TYPES = ("Building", "BuildingPart")
TRANSPORTATION_TYPES = ("Road", "Railway", "Track", "Square")
FURNITURE_TYPES = ("CityFurniture",)
HOST_TYPES = ("WallSurface",)
# an opening's prism sits on its wall face; farther than this it belongs to another face
_OPENING_PLANE_TOL_M = 0.15


def _r(x, nd=4):
    return round(float(x), nd)


def _vec(v, nd=4):
    return [_r(c, nd) for c in v]


def _attributes(obj) -> dict:
    """The object's CityGML attributes: custom properties named ``prefix:name`` with scalar values."""
    out = {}
    for key in obj.keys():
        if ":" not in key or key.startswith("cgml"):
            continue
        val = obj[key]
        if isinstance(val, (str, int, float, bool)) and val != "":
            out[key] = val
    return out


def _objects_by_id(scene) -> dict:
    out = {}
    for o in scene.objects:
        gid = str(o.get("gml_id") or "").strip()
        if gid and gid not in out:
            out[gid] = o
    return out


def find_object(gml_id: str, scene=None):
    """The scene object carrying ``gml_id`` (first one), or ``None``."""
    scene = scene or bpy.context.scene
    for o in scene.objects:
        if str(o.get("gml_id") or "").strip() == gml_id:
            return o
    return None


def _newell_area(points) -> float:
    n = Vector()
    for a, b in zip(points, points[1:] + points[:1]):
        n += a.cross(b)
    return n.length / 2.0


def _opening_rect(opening, frame):
    """``(u, v, width, height, plane_distance)`` of ``opening``'s world vertices in ``frame``."""
    mw = opening.matrix_world
    o, h, v, n = (Vector(frame.origin), Vector(frame.horizontal_axis),
                  Vector(frame.vertical_axis), Vector(frame.normal))
    pts = [mw @ p.co - o for p in opening.data.vertices]
    if not pts:
        return None
    us, vs = [p.dot(h) for p in pts], [p.dot(v) for p in pts]
    dist = min(abs(p.dot(n)) for p in pts)
    w, ht = max(us) - min(us), max(vs) - min(vs)
    cu, cv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
    return (cu / frame.width if frame.width > 1e-9 else 0.5,
            cv / frame.height if frame.height > 1e-9 else 0.5, w, ht, dist)


def _opening_kind(obj) -> str:
    """``Window`` / ``Door`` (imported 2.0 objects say ``WindowSurface`` / ``DoorSurface``)."""
    t = str(obj.get("cgml_semantic_type") or obj.get("cgml3_feature") or "").split(":")[-1]
    return t[:-7] if t in ("WindowSurface", "DoorSurface") else t


def _faces(obj, semantic_type, openings, merged: bool):
    """Per-face records of a surface object (or of a merged building's mesh)."""
    mw = obj.matrix_world
    verts = [mw @ v.co for v in obj.data.vertices]
    ring_faces = {fi for fi, _pts, _link in wall_interior_ring_faces(obj)} if not merged else set()
    faces = []
    for poly in obj.data.polygons:
        pts = [verts[i] for i in poly.vertices]
        ftype = semantic_type
        if merged:
            mat = face_material(obj.data, poly)
            ftype = str((mat.get("SurfaceTyp") or mat.get("surface_type") or "") if mat else "")
        rec = {"index": poly.index, "type": ftype, "area": _r(_newell_area(pts), 3)}
        if poly.index in ring_faces:
            rec.update(role="interior_ring", can_host=False, reason="hole of an opening (the opening owns it)")
            faces.append(rec)
            continue
        rec["role"] = "polygon"
        try:
            frame = compute_facade_frame([tuple(p) for p in pts])
        except ValueError as e:
            rec.update(can_host=False, reason=f"no facade frame: {e}")
            faces.append(rec)
            continue
        rec["normal"] = _vec(frame.normal)
        rec["frame"] = {"origin": _vec(frame.origin, 6), "u_axis": _vec(frame.horizontal_axis, 6),
                        "v_axis": _vec(frame.vertical_axis, 6), "width": _r(frame.width, 6),
                        "height": _r(frame.height, 6)}
        if ftype not in HOST_TYPES:
            rec.update(can_host=False, reason=f"{ftype or 'untyped face'}: only WallSurface faces take openings")
        else:
            rec.update(can_host=True,
                       reason="merged building: split into surfaces on the first placement" if merged else "")
        rec["openings"] = []
        rec["_frame"] = frame
        faces.append(rec)
    # each opening goes to the polygon face it sits on (centre inside the frame, nearest plane)
    for op in openings:
        best = None
        for rec in faces:
            frame = rec.get("_frame")
            if frame is None or op.type != 'MESH':
                continue
            rect = _opening_rect(op, frame)
            if rect is None:
                continue
            u, v, w, h, dist = rect
            if -1e-3 <= u <= 1 + 1e-3 and -1e-3 <= v <= 1 + 1e-3 and dist < _OPENING_PLANE_TOL_M:
                if best is None or dist < best[0]:
                    best = (dist, rec, (u, v, w, h))
        if best is not None:
            _d, rec, (u, v, w, h) = best
            rec["openings"].append({
                "gml_id": str(op.get("gml_id") or ""), "object": op.name,
                "type": _opening_kind(op),
                "u": _r(u), "v": _r(v), "width": _r(w, 3), "height": _r(h, 3),
            })
    for rec in faces:
        rec.pop("_frame", None)
    return faces


def _planarity_errors(objs, tolerance):
    errs = []
    for o in objs:
        if o.type != 'MESH':
            continue
        for fi, d in non_planar_faces(o, tolerance):
            errs.append({"code": "GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE", "object": o.name,
                         "gml_id": str(o.get("gml_id") or ""), "face": fi, "value_m": _r(d)})
    return errs


def _bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' and o.data.vertices for c in o.bound_box]
    if not pts:
        return None
    return {"min": _vec([min(p[i] for p in pts) for i in range(3)], 3),
            "max": _vec([max(p[i] for p in pts) for i in range(3)], 3)}


def inspect_building(root, tolerance=DEFAULT_TOLERANCE_M) -> dict:
    """One Building/BuildingPart as a JSON-ready dict (surfaces, faces, openings, installations, errors)."""
    tree = build_semantic_tree(root)
    children = {str(c.get("gml_id") or ""): c for c in root.children}
    surfaces = []
    for node in tree.boundary_surfaces:
        obj = children.get(node.gml_id)
        if obj is None or obj.type != 'MESH':
            continue
        openings = [c for c in obj.children if is_opening(c)]
        surfaces.append({"gml_id": node.gml_id, "type": node.semantic_type, "object": obj.name,
                         "faces": _faces(obj, node.semantic_type, openings, merged=False)})
    merged = not surfaces and root.type == 'MESH' and len(root.data.polygons) > 0
    if merged:  # imported "merged": one mesh, the surface type sits on each face's material
        openings = [o for o in root.children_recursive if is_opening(o)]
        surfaces.append({"gml_id": str(root.get("gml_id") or ""), "type": "merged", "object": root.name,
                         "faces": _faces(root, "", openings, merged=True)})
    installations = []
    for node in tree.installations:
        obj = children.get(node.gml_id)
        installations.append({"gml_id": node.gml_id, "type": node.semantic_type,
                              "object": obj.name if obj else "",
                              "function": str(obj.get("bldg:function") or "") if obj else ""})
    members = [root, *root.children_recursive]
    parts = [c for c in root.children if str(c.get("cgml3_feature") or "") == "BuildingPart"]
    return {
        "gml_id": str(root.get("gml_id") or ""), "name": root.name,
        "type": str(root.get("cgml3_feature") or ""), "lod": object_lod(root),
        "hierarchy": "merged" if merged else "surfaces",
        "attributes": _attributes(root),
        "bbox": _bbox(members),
        "surfaces": surfaces,
        "installations": installations,
        "parts": [inspect_building(p, tolerance) for p in parts],
        "errors": _planarity_errors([o for o in members if o.get("cgml3_feature")], tolerance),
    }


def _inspect_other(obj) -> dict:
    members = [obj, *obj.children_recursive]
    counts: dict[str, int] = {}
    for o in obj.children_recursive:
        t = str(o.get("cgml3_feature") or "")
        if t:
            counts[t] = counts.get(t, 0) + 1
    return {"gml_id": str(obj.get("gml_id") or ""), "name": obj.name, "type": str(obj.get("cgml3_feature") or ""),
            "lod": object_lod(obj), "attributes": _attributes(obj), "bbox": _bbox(members), "parts": counts}


def inspect(building_gml_id: str | None = None, *, tolerance: float = DEFAULT_TOLERANCE_M, scene=None) -> dict:
    """The scene (or one building) as JSON-ready dict: buildings with surfaces, faces (frame,
    ``can_host`` + reason, openings with ``u/v/width/height``), installations and errors;
    Transportation and CityFurniture features; CRS and offset."""
    from . import georef as gr

    scene = scene or bpy.context.scene
    w = gr.world()
    crs = str(w.get(gr.CRS) or "") if w is not None else ""
    offset = list(getattr(scene, "cgml3_offset", (0.0, 0.0, 0.0)))
    tops = top_level_features(scene)
    by_id = _objects_by_id(scene)
    out = {"schema": SCHEMA, "crs": crs, "offset": _vec(offset, 3), "units": "m",
           "buildings": [], "transportation": [], "city_furniture": [], "other": []}
    if building_gml_id:
        obj = by_id.get(building_gml_id)
        if obj is None or str(obj.get("cgml3_feature") or "") not in BUILDING_TYPES:
            out["error"] = {"code": "NOT_FOUND", "message": f"no Building with gml_id {building_gml_id!r}"}
            return out
        out["buildings"].append(inspect_building(obj, tolerance))
        return out
    for gid, ftype in tops.items():
        obj = by_id.get(gid)
        if obj is None:
            continue
        if ftype in BUILDING_TYPES:
            out["buildings"].append(inspect_building(obj, tolerance))
        elif ftype in TRANSPORTATION_TYPES:
            out["transportation"].append(_inspect_other(obj))
        elif ftype in FURNITURE_TYPES:
            out["city_furniture"].append(_inspect_other(obj))
        else:
            out["other"].append({"gml_id": gid, "type": ftype, "name": obj.name})
    return out


# =========================================================================== commands (A4, B59)
# Every command takes gml_ids and world coordinates, never reads the selection / active object /
# mode, and returns the same dict. A command that changed the scene is one undo step.
ERRORS = {
    "NOT_FOUND": "no object with that gml_id in the scene",
    "BAD_ARGUMENT": "an argument is missing, of the wrong kind or out of range",
    "NOT_A_WALL": "openings go on WallSurface faces only",
    "BAD_FACE": "face index out of range, an opening's hole, or a face without a facade frame",
    "PLACEMENT_REFUSED": "the element does not fit there (outside the face, overlaps an opening, ...)",
    "NOT_A_BUILDING": "the gml_id is not a Building / BuildingPart",
    "ROOF_REFUSED": "the roof does not fit this building (footprint, windows or installations)",
    "NOT_DELETABLE": "this kind of object is not deleted by the agent API",
    "RECORD_BACKED": "the object is managed by the experimental authoring core (flag); use the UI",
    "SCOPE_ERROR": "the export scope selects nothing (empty selection, unknown ids)",
    "EXPORT_FAILED": "the export did not write a file",
    "INTERNAL": "unexpected error in the add-on (see message and the console)",
}

OPENING_KINDS = ("Window", "Door")
DOOR_TOPOLOGIES = ("AUTO", "FLOATING", "GROUND")


class CommandError(Exception):
    def __init__(self, code, message):
        assert code in ERRORS, code
        super().__init__(message)
        self.code = code


def result(ok=True, error_code="", message="", created=(), changed=(), warnings=(), **extra) -> dict:
    out = {"ok": ok, "error_code": error_code, "message": message, "created": list(created),
           "changed": list(changed), "warnings": list(warnings)}
    out.update(extra)
    return out


def _undo_push(message):
    try:
        bpy.ops.ed.undo_push(message=f"CityGML agent: {message}")
    except Exception:  # background mode / no window: no undo stack to push to
        pass


def _command(fn):
    """Error codes instead of exceptions; one undo step when the command changed something."""
    import functools
    import traceback

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            res = fn(*args, **kwargs)
        except CommandError as e:
            return result(False, e.code, str(e))
        except Exception as e:  # never raise into the agent
            traceback.print_exc()
            return result(False, "INTERNAL", f"{type(e).__name__}: {e}")
        if res["ok"] and (res["created"] or res["changed"] or res.get("deleted")):
            _undo_push(fn.__name__)
        return res
    return run


def _gid(obj) -> str:
    return str(obj.get("gml_id") or "").strip()


def _need(gml_id):
    obj = find_object(str(gml_id or "").strip())
    if obj is None:
        raise CommandError("NOT_FOUND", f"no object with gml_id {gml_id!r}")
    return obj


def _guard_records(objs, what):
    from .authoring_core.blender.guards import refuse_if_record_backed
    msg = refuse_if_record_backed([o for o in objs if o is not None], what)
    if msg:
        raise CommandError("RECORD_BACKED", msg)


def _building(gml_id):
    from ..ops.installation_authoring import building_root
    obj = _need(gml_id)
    if building_root(obj) is not obj:
        raise CommandError("NOT_A_BUILDING", f"{gml_id!r} is not a Building / BuildingPart")
    return obj


def _wall_surface(wall_id, face):
    """``(SurfaceContext, changed ids)`` for face ``face`` of the WallSurface (or merged building)."""
    from .facade_surface_resolver import (resolve_surface_context, resolve_surface_context_for_face,
                                          surface_needs_split)
    from .lod_helpers import lod_promotion_root
    obj = _need(wall_id)
    if not isinstance(face, int) or obj.type != 'MESH' or not 0 <= face < len(obj.data.polygons):
        raise CommandError("BAD_FACE", f"face {face!r} is not a face of {wall_id!r}")
    merged = not is_boundary_surface(obj)
    if merged and not surface_needs_split(obj, face):
        raise CommandError("NOT_A_WALL", f"{wall_id!r} face {face} is not a CityGML boundary surface")
    if merged:
        mat = face_material(obj.data, obj.data.polygons[face])
        stype = str(mat.get("SurfaceTyp") or mat.get("surface_type") or "") if mat else ""
    else:
        stype = str(obj.get("cgml3_feature") or "")
    if stype not in HOST_TYPES:
        raise CommandError("NOT_A_WALL", f"{wall_id!r} face {face} is a {stype or 'untyped face'}")
    if not merged and face in {fi for fi, _p, _l in wall_interior_ring_faces(obj)}:
        raise CommandError("BAD_FACE", f"{wall_id!r} face {face} is the hole of an opening")
    _guard_records([obj, lod_promotion_root(obj)], "agent placement")
    try:
        surface = resolve_surface_context_for_face(obj, face) if merged else resolve_surface_context(obj, face)
    except ValueError as e:
        raise CommandError("BAD_FACE", str(e))
    return surface, ([wall_id] if merged else [])


def _number(name, val, low=None, high=None, positive=False):
    if isinstance(val, bool) or not isinstance(val, (int, float)) or (positive and val <= 0) \
            or (low is not None and val < low) or (high is not None and val > high):
        rng = "a positive number" if positive else f"a number in [{low}, {high}]"
        raise CommandError("BAD_ARGUMENT", f"{name} must be {rng} (got {val!r})")
    return float(val)


def _placement_kw(source, model_name, model_version, confidence, extras):
    return {"source": source or "agent", "model_name": model_name, "model_version": model_version,
            "confidence": confidence, "extras": dict(extras or {})}


def _kind(kind):
    kind = str(kind or "").strip().capitalize()
    if kind not in OPENING_KINDS:
        raise CommandError("BAD_ARGUMENT", f"kind must be one of {OPENING_KINDS} (got {kind!r})")
    return kind


@_command
def add_opening(wall_id, face, u, v, width=1.2, height=1.4, kind="Window", topology="AUTO",
                depth=None, source="agent", model_name=None, model_version=None, confidence=None,
                extras=None) -> dict:
    """Put a Window / Door with its centre at ``(u, v)`` (fractions of the face frame) on face
    ``face`` of the WallSurface ``wall_id`` (a merged building is split into surfaces first).
    Door ``topology``: AUTO (bottom within 1 cm of the face bottom = ground notch), FLOATING, GROUND."""
    from .facade_placement import (DEFAULT_WINDOW_DEPTH, FacadeElementDefinition, PlacementRequest,
                                   place_semantic_element)
    kind = _kind(kind)
    topology = str(topology or "AUTO").upper()
    if topology not in DOOR_TOPOLOGIES:
        raise CommandError("BAD_ARGUMENT", f"topology must be one of {DOOR_TOPOLOGIES}")
    width, height = _number("width", width, positive=True), _number("height", height, positive=True)
    u, v = _number("u", u, 0.0, 1.0), _number("v", v, 0.0, 1.0)
    depth = DEFAULT_WINDOW_DEPTH if depth is None else _number("depth", depth, 0.0, 2.0)
    surface, changed = _wall_surface(wall_id, face)
    definition = FacadeElementDefinition(
        semantic_type=kind, width=width, height=height, depth=depth, generator_name="agent_api",
        door_touches_ground=None if kind == "Window" or topology == "AUTO" else topology == "GROUND")
    placement = PlacementRequest(position_uv=(u, v),
                                 **_placement_kw(source, model_name, model_version, confidence, extras))
    try:
        fill = place_semantic_element(definition, surface, placement)
    except (ValueError, RuntimeError, NotImplementedError) as e:
        raise CommandError("PLACEMENT_REFUSED", str(e))
    return result(True, message=f"{kind} {_gid(fill)} on {surface.semantic_id} face {surface.face_indices[0]}",
                  created=[_gid(fill)], changed=changed + [surface.semantic_id], object=fill.name,
                  surface={"gml_id": surface.semantic_id, "face": surface.face_indices[0]},
                  topology=str(fill.get("cgml_authoring_topology") or "ring"))


@_command
def add_openings_grid(wall_id, face, columns, rows, width=1.2, height=1.4, step_x=2.5, step_z=3.0,
                      origin_uv=(0.2, 0.3), kind="Window", source="agent", model_name=None,
                      model_version=None, confidence=None, extras=None) -> dict:
    """``columns`` x ``rows`` openings; the first centre at ``origin_uv`` of the face frame, then
    ``step_x`` metres along the wall and ``step_z`` metres up. Those that do not fit are skipped."""
    from ..ops.opening_authoring import array_openings
    from .facade_placement import surface_world_point_from_uv
    kind = _kind(kind)
    columns, rows = int(_number("columns", columns, 1, 50)), int(_number("rows", rows, 1, 50))
    width, height = _number("width", width, positive=True), _number("height", height, positive=True)
    step_x, step_z = _number("step_x", step_x, positive=True), _number("step_z", step_z, positive=True)
    try:
        origin_uv = (_number("origin_uv", origin_uv[0], 0.0, 1.0), _number("origin_uv", origin_uv[1], 0.0, 1.0))
    except (TypeError, IndexError):
        raise CommandError("BAD_ARGUMENT", "origin_uv is two fractions in [0, 1]")
    surface, changed = _wall_surface(wall_id, face)
    wall = bpy.data.objects[surface.object_name]
    centre = surface_world_point_from_uv(surface, origin_uv)
    created, failed = array_openings(
        wall, centre, Vector(surface.horizontal_axis_world), Vector(surface.vertical_axis_world), kind,
        width, height, columns, rows, step_x, step_z,
        placement_kw=_placement_kw(source, model_name, model_version, confidence, extras))
    skipped = [{"column": c, "row": r, "reason": why} for c, r, why in failed]
    if not created:
        return result(False, "PLACEMENT_REFUSED", f"none of the {columns * rows} {kind.lower()}s fit",
                      skipped=skipped)
    return result(True, message=f"{len(created)} {kind.lower()}(s) placed, {len(skipped)} skipped",
                  created=[_gid(o) for o in created], changed=changed + [surface.semantic_id],
                  warnings=[f"{len(skipped)} did not fit"] if skipped else [], skipped=skipped)


def _vector3(name, value):
    try:
        vec = Vector([float(c) for c in value])
    except (TypeError, ValueError):
        vec = None
    if vec is None or len(vec) != 3:
        raise CommandError("BAD_ARGUMENT", f"{name} is three numbers (world x, y, z)")
    return vec


def _choice(name, value, items):
    ids = [i[0] for i in items]
    if value not in ids:
        raise CommandError("BAD_ARGUMENT", f"{name} must be one of {ids} (got {value!r})")
    return value


@_command
def add_balcony(building_id, point, normal, kind="PROJECTING", width=3.0, depth=1.4, railing=1.0,
                corner_side="END", offset=0.0) -> dict:
    """A balcony on the wall at world ``point`` (floor level; for CORNER the building corner),
    ``normal`` = the wall's outward horizontal normal (``inspect`` face ``normal``)."""
    from ..ops.installation_authoring import BALCONY_TYPES, CORNER_SIDES, make_balcony
    root = _building(building_id)
    _choice("kind", kind, BALCONY_TYPES)
    _choice("corner_side", corner_side, CORNER_SIDES)
    width, depth = _number("width", width, positive=True), _number("depth", depth, positive=True)
    railing, offset = _number("railing", railing, 0.0, 5.0), _number("offset", offset, -100.0, 100.0)
    p, n = _vector3("point", point), _vector3("normal", normal)
    flat = Vector((n.x, n.y, 0.0))
    if n.length == 0 or flat.length < 0.7 * n.length:
        raise CommandError("BAD_ARGUMENT", "normal must be horizontal (a wall's outward normal)")
    _guard_records([root], "agent balcony")
    obj = make_balcony(bpy.context, root, p, flat.normalized(), kind, width, depth, railing, corner_side, offset)
    return result(True, message=f"Balcony {_gid(obj)} on {building_id}", created=[_gid(obj)],
                  changed=[building_id], object=obj.name)


@_command
def add_chimney(building_id, point, normal=(0.0, 0.0, 1.0), kind="SQUARE", size=0.6, above_ridge=0.8) -> dict:
    """A chimney through the roof at world ``point``; ``normal`` = the roof face normal."""
    from ..ops.installation_authoring import CHIMNEY_TYPES, make_chimney
    root = _building(building_id)
    _choice("kind", kind, CHIMNEY_TYPES)
    size, above_ridge = _number("size", size, positive=True), _number("above_ridge", above_ridge, 0.0, 20.0)
    p, n = _vector3("point", point), _vector3("normal", normal)
    if n.length == 0 or n.normalized().z < 0.2:
        raise CommandError("BAD_ARGUMENT", "normal must point up (a roof face's normal)")
    _guard_records([root], "agent chimney")
    obj = make_chimney(bpy.context, root, p, n.normalized(), kind, size, above_ridge)
    return result(True, message=f"Chimney {_gid(obj)} on {building_id}", created=[_gid(obj)],
                  changed=[building_id], object=obj.name)


@_command
def add_roof(building_id, roof_type="3100", pitch=35.0, ridge_along_long=True, flip=False) -> dict:
    """A parametric LoD2 roof (AdV roofType code) on an authored building without openings or
    installations. The surfaces are rebuilt: their gml_ids change."""
    from ..ops.roof_authoring import ROOF_ITEMS, put_roof, roof_target_for
    root = _building(building_id)
    roof_type = _choice("roof_type", str(roof_type), ROOF_ITEMS)
    pitch = _number("pitch", pitch, 0.0, 75.0)
    target, reason = roof_target_for(root)
    if target is None:
        raise CommandError("ROOF_REFUSED", reason)
    _guard_records([target, *target.children_recursive], "agent roof")
    try:
        parts = put_roof(bpy.context, target, roof_type, pitch, bool(ridge_along_long), bool(flip))
    except ValueError as e:
        raise CommandError("ROOF_REFUSED", str(e))
    return result(True, message=f"roof {roof_type} on {len(parts)} part(s)",
                  changed=[_gid(target)] + [_gid(p) for p in parts if p is not target])


@_command
def delete_feature(gml_id) -> dict:
    """Delete a Window / Door (its wall is closed again, B55), an installation, a CityFurniture
    or a whole Building. Boundary surfaces are refused (the shell would open)."""
    from .facade_placement import close_opening
    from .lod_helpers import lod_promotion_root
    obj = _need(gml_id)
    feat = str(obj.get("cgml3_feature") or "")
    _guard_records([obj, lod_promotion_root(obj)], "agent delete")
    changed, msg = [], ""
    if is_opening(obj):
        wall = obj.parent
        msg = close_opening(obj)
        changed = [_gid(wall)] if wall is not None and msg else []
    elif feat not in BUILDING_TYPES + ("BuildingInstallation",) + FURNITURE_TYPES:
        raise CommandError("NOT_DELETABLE", f"{feat or obj.type} objects are not deleted by the agent API "
                                            "(a boundary surface would open the building's shell)")
    tree = [obj, *obj.children_recursive]
    deleted = [_gid(o) for o in tree if _gid(o)]
    for o in reversed(tree):
        bpy.data.objects.remove(o)
    return result(True, message=f"deleted {gml_id}" + (f"; {msg}" if msg else ""), changed=changed,
                  deleted=deleted)


def _planarity_scope(building_id):
    if building_id:
        root = _need(building_id)
        objs = [root, *root.children_recursive]
    else:
        objs = list(bpy.context.scene.objects)
    return sorted((o for o in objs if o.type == 'MESH' and o.get("cgml3_feature") and o.mode != 'EDIT'),
                  key=lambda o: o.name)


@_command
def check_planarity(building_id=None, tolerance=DEFAULT_TOLERANCE_M) -> dict:
    """Polygons off their best-fit plane by more than ``tolerance`` (CityDoctor GE_P_NON_PLANAR)."""
    tolerance = _number("tolerance", tolerance, positive=True)
    faces = _planarity_errors(_planarity_scope(building_id), tolerance)
    return result(True, message=f"{len(faces)} non-planar polygon(s)", faces=faces, count=len(faces))


@_command
def fix_planarity(building_id=None, tolerance=DEFAULT_TOLERANCE_M) -> dict:
    """Flatten the non-planar polygons (B55); returns the count before and after."""
    from .planarity import flatten
    tolerance = _number("tolerance", tolerance, positive=True)
    objs = _planarity_scope(building_id)
    bad = [o for o in objs if non_planar_faces(o, tolerance)]
    before = sum(len(non_planar_faces(o, tolerance)) for o in bad)
    after = flatten(objs, tolerance) if bad else 0
    return result(True, message=f"non-planar polygons {before} -> {after}", changed=[_gid(o) for o in bad],
                  warnings=[f"{after} polygon(s) still off-plane"] if after else [], before=before, after=after)


@_command
def export(path, version="3.0", scope="SCENE", gml_ids=None, xsd=True) -> dict:
    """Write a CityGML file. ``scope``: SCENE (default), SELECTION or IDS (``gml_ids``). The result
    has the written / skipped top-level ids and the XSD result; geometry is not checked."""
    import os
    from . import export_scope
    if str(version) not in ("2.0", "3.0"):
        raise CommandError("BAD_ARGUMENT", "version is '2.0' or '3.0'")
    ctx = bpy.context
    ids = export_scope.parse_ids(gml_ids or [])
    try:
        export_scope.validate_scope(ctx.view_layer, ctx.scene, scope, ids)
    except ValueError as e:
        raise CommandError("SCOPE_ERROR", str(e))
    path = os.path.abspath(str(path))
    if not path.lower().endswith(".gml"):
        raise CommandError("BAD_ARGUMENT", "path must end with .gml")
    if os.path.exists(path):
        os.remove(path)
    props = ctx.scene.cgml3
    old = props.xsd_validate
    props.xsd_validate = bool(xsd)
    try:
        r = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=str(version), scope=scope,
                                          scope_gml_ids=",".join(ids))
    except RuntimeError as e:
        raise CommandError("EXPORT_FAILED", str(e))
    finally:
        props.xsd_validate = old
    rec = export_scope.last_export(ctx.scene)
    if r != {'FINISHED'} or not os.path.isfile(path) or rec is None or rec.get("path") != path:
        raise CommandError("EXPORT_FAILED", f"export returned {sorted(r)}; see the console")
    warnings = []
    if rec["skipped"]:
        warnings.append(f"{rec['skipped_count']} top-level feature(s) not written")
    if rec.get("xsd") == "invalid":
        warnings.append("XSD validation failed (see the citygml_xsd_check text block)")
    return result(True, message=export_scope.summary(rec) + f"; XSD {rec.get('xsd')}; geometry not checked",
                  warnings=warnings, export=rec)


# =========================================================================== validation (A5, B60)
LAST_VALIDATION_KEY = "cgml_last_validation"


def _ids(building_ids):
    if building_ids is None:
        return []
    if isinstance(building_ids, str):
        return [building_ids]
    return [str(i) for i in building_ids]


def citydoctor_validator():
    """The CityDoctor validator command (Preferences, else CITYDOCTOR_VALIDATE_SH), or None."""
    from ..ops.browse_gml import pref
    from .citydoctor import validator_path
    return validator_path(pref("citydoctor_validator", ""))


def merge_validation(checks: dict, cd: dict | None) -> dict:
    """Per building ``{"checks", "details", "closed", "citydoctor"}`` from both sources."""
    out = {}
    for gid in sorted(set(checks) | set((cd or {}).get("buildings", {}))):
        c = checks.get(gid, {})
        out[gid] = {"checks": c.get("codes", {}), "details": c.get("details", {}), "closed": c.get("closed"),
                    "citydoctor": (cd or {}).get("buildings", {}).get(gid) if cd else None}
    return out


@_command
def validate(building_ids=None, version="3.0", citydoctor=False, tolerance=NON_PLANAR_TOL) -> dict:
    """Export the buildings (all when ``building_ids`` is empty) to a temporary file and check what
    was written: closed shell (open edges, T-junctions), ring self-intersection, non-planar polygons,
    fillings without a hole; with ``citydoctor`` also run CityDoctor when it is set up. Changes
    nothing in the scene. ``buildings``: ``{gml_id: {"checks": {code: n}, "details", "closed",
    "citydoctor": {code: n} | None}}``."""
    import json
    import os
    import tempfile
    from . import citydoctor as cdr
    from . import geometry_checks
    ids = _ids(building_ids)
    for gid in ids:
        _building(gid)
    tolerance = _number("tolerance", tolerance, positive=True)
    tmp = tempfile.mkdtemp(prefix="cgml_validate_")
    path = os.path.join(tmp, "validate.gml")
    res = export.__wrapped__(path, version, scope="IDS" if ids else "SCENE", gml_ids=ids, xsd=True)
    checks = geometry_checks.check_file(path, tolerance)
    warnings, cd = [], None
    if citydoctor:
        validator = citydoctor_validator()
        if validator is None:
            warnings.append("CityDoctor is not set up (Preferences > CityDoctor validator); only the "
                            "add-on's own checks ran")
        else:
            try:
                cd = cdr.parse_report(cdr.run(validator, path))
            except Exception as e:  # noqa: BLE001 - Java / report problems are a warning, the checks stand
                warnings.append(f"CityDoctor failed: {e}")
    buildings = merge_validation(checks, cd)
    out = {"path": path, "xsd": res["export"].get("xsd"), "buildings": buildings,
           "summary": geometry_checks.summary(checks), "citydoctor": cd["global"] if cd else None}
    bpy.context.scene[LAST_VALIDATION_KEY] = json.dumps(out)
    bad = sum(1 for b in buildings.values() if b["checks"] or b["citydoctor"])
    return result(True, message=f"{len(buildings)} building(s) checked, {bad} with findings; XSD {out['xsd']}",
                  warnings=warnings + res["warnings"], **out)


# =========================================================================== repair (A6, B61)
REPAIR_CODES = ("GE_P_NON_PLANAR", "SE_BS_UNFRAGMENTED", "GE_S_NOT_CLOSED", "GE_S_SELF_INTERSECTION")


def _shells(root):
    """``[(Building or BuildingPart, its boundary surface objects)]`` under ``root``."""
    from .ring_opening_sync import building_ancestor
    owners = {}
    for o in [root, *root.children_recursive]:
        if o.type == 'MESH' and is_boundary_surface(o):
            owners.setdefault(building_ancestor(o), []).append(o)
    return [(b, objs) for b, objs in owners.items() if b is not None]


def _installations(root):
    return [o for o in root.children_recursive
            if o.type == 'MESH' and str(o.get("cgml3_feature") or "") == "BuildingInstallation"]


def _count(code, shells, tolerance, angle_deg, installations=()):
    from . import geometry_repair as gr
    if code == "GE_P_NON_PLANAR":
        return sum(len(non_planar_faces(o, tolerance)) for _b, objs in shells for o in objs)
    if code == "SE_BS_UNFRAGMENTED":  # building surfaces are objects, installation surfaces materials (B40)
        return sum(len(gr.unfragmented(objs, angle_deg)) for _b, objs in shells) +             sum(len(gr.unfragmented_material_surfaces(i, angle_deg)) for i in installations)
    if code == "GE_S_NOT_CLOSED":
        return sum(len(gr.t_junctions(objs)) for _b, objs in shells)
    return sum(len(gr.self_intersections(objs)) for _b, objs in shells)


@_command
def repair(building_id, code, tolerance=DEFAULT_TOLERANCE_M, angle_deg=1.0) -> dict:
    """Repair one CityDoctor error class on a building, only when asked. ``code``:
    GE_P_NON_PLANAR (flatten polygons), SE_BS_UNFRAGMENTED (split a surface whose polygons point
    in different directions into one surface per direction), GE_S_NOT_CLOSED (weld T-junctions),
    GE_S_SELF_INTERSECTION (diagnosis only: which polygons, nothing changes).
    ``before`` / ``after``: count of the finding on the Blender meshes."""
    from . import geometry_repair as gr
    from .planarity import flatten
    code = str(code or "").upper()
    if code.startswith("GE_P_NON_PLANAR"):
        code = "GE_P_NON_PLANAR"
    if code not in REPAIR_CODES:
        raise CommandError("BAD_ARGUMENT", f"code must be one of {REPAIR_CODES} (got {code!r})")
    root = _building(building_id)
    tolerance = _number("tolerance", tolerance, positive=True)
    angle_deg = _number("angle_deg", angle_deg, 0.01, 45.0)
    shells = _shells(root)
    objs = [o for _b, os_ in shells for o in os_]
    _guard_records([root, *objs], "agent repair")
    inst = _installations(root)
    before = _count(code, shells, tolerance, angle_deg, inst)
    created, changed, extra = [], [], {}
    if code == "GE_S_SELF_INTERSECTION":
        pairs = [p for _b, os_ in shells for p in gr.self_intersections(os_)]
        return result(True, message=f"{len(pairs)} pair(s) of polygons touch or cross; not repaired "
                                    "automatically: move or rebuild one of each pair", pairs=pairs,
                      before=len(pairs), after=len(pairs))
    if before:
        if code == "GE_P_NON_PLANAR":
            bad = [o for o in objs if non_planar_faces(o, tolerance)]
            flatten(objs, tolerance)
            changed = [_gid(o) for o in bad]
        elif code == "SE_BS_UNFRAGMENTED":
            for orig, new in gr.fragment_surfaces(objs, angle_deg):
                changed.append(_gid(orig))
                created += [_gid(n) for n in new]
            for i in inst:
                new_ids = gr.fragment_material_surfaces(i, angle_deg)
                if new_ids:
                    changed.append(_gid(i))
                    created += new_ids
        else:
            extra["inserted"] = sum(gr.weld_t_junctions(os_) for _b, os_ in shells)
            changed = [_gid(o) for o in objs]
        shells = _shells(root)
    after = _count(code, shells, tolerance, angle_deg, inst)
    warnings = [f"{after} still found"] if after else []
    return result(True, message=f"{code}: {before} -> {after}", created=created, changed=changed,
                  warnings=warnings, before=before, after=after, **extra)


# =========================================================================== facade layout (A8, B62)
LAYOUT_SCHEMA = "cgml-facade-layout/1"
SAME_PLACE_M = 0.05   # an existing opening this close (centre and size) is the same one: skipped


def validate_layout(layout) -> list:
    """Problems of a ``cgml-facade-layout/1`` document (empty list = valid)."""
    from ..ops.installation_authoring import BALCONY_TYPES
    p = []
    if not isinstance(layout, dict):
        return ["the layout must be a JSON object"]
    if layout.get("schema") != LAYOUT_SCHEMA:
        p.append(f"schema must be {LAYOUT_SCHEMA!r}")
    if layout.get("units", "m") != "m":
        p.append("units must be 'm'")
    for key in ("building_gml_id", "wall_gml_id"):
        if not isinstance(layout.get(key), str) or not layout.get(key):
            p.append(f"{key} is required")
    if not isinstance(layout.get("face_index"), int) or isinstance(layout.get("face_index"), bool):
        p.append("face_index must be an integer")

    def num(where, val, low=None, high=None, positive=False):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or (positive and val <= 0) \
                or (low is not None and val < low) or (high is not None and val > high):
            p.append(f"{where} out of range ({val!r})")

    openings = layout.get("openings", [])
    if not isinstance(openings, list):
        p.append("openings must be a list")
        openings = []
    for i, o in enumerate(openings):
        w = f"openings[{i}]"
        if not isinstance(o, dict):
            p.append(f"{w} must be an object")
            continue
        if o.get("kind") not in OPENING_KINDS:
            p.append(f"{w}.kind must be one of {OPENING_KINDS}")
        for k in ("u", "v"):
            num(f"{w}.{k}", o.get(k), 0.0, 1.0)
        for k in ("width", "height"):
            num(f"{w}.{k}", o.get(k), positive=True)
        if o.get("topology", "AUTO") not in DOOR_TOPOLOGIES:
            p.append(f"{w}.topology must be one of {DOOR_TOPOLOGIES}")
    grid = layout.get("grid")
    if grid is not None:
        if not isinstance(grid, dict):
            p.append("grid must be an object")
        else:
            if grid.get("kind", "Window") not in OPENING_KINDS:
                p.append(f"grid.kind must be one of {OPENING_KINDS}")
            for k in ("columns", "rows"):
                if not isinstance(grid.get(k), int) or not 1 <= grid.get(k) <= 50:
                    p.append(f"grid.{k} must be an integer 1..50")
            for k in ("width", "height", "step_x", "step_z"):
                num(f"grid.{k}", grid.get(k), positive=True)
            ouv = grid.get("origin_uv")
            if not isinstance(ouv, (list, tuple)) or len(ouv) != 2:
                p.append("grid.origin_uv must be [u, v]")
            else:
                for k, val in zip("uv", ouv):
                    num(f"grid.origin_uv.{k}", val, 0.0, 1.0)
    for i, inst in enumerate(layout.get("installations", []) or []):
        w = f"installations[{i}]"
        if not isinstance(inst, dict) or inst.get("kind") != "Balcony":
            p.append(f"{w}.kind must be 'Balcony'")
            continue
        if inst.get("type", "PROJECTING") not in [t[0] for t in BALCONY_TYPES]:
            p.append(f"{w}.type must be one of {[t[0] for t in BALCONY_TYPES]}")
        at = inst.get("at_opening")
        if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at < len(openings):
            p.append(f"{w}.at_opening must index openings (0-based)")
    src = layout.get("source", {})
    if src is not None and not isinstance(src, dict):
        p.append("source must be an object")
    return p


def _layout_items(layout, surface):
    """``[(label, kind, world centre, width, height, topology)]`` of openings and the grid."""
    from .facade_placement import surface_world_point_from_uv
    items = []
    for i, o in enumerate(layout.get("openings", [])):
        items.append((f"openings[{i}]", o["kind"], surface_world_point_from_uv(surface, (o["u"], o["v"])),
                      float(o["width"]), float(o["height"]), o.get("topology", "AUTO")))
    g = layout.get("grid")
    if g:
        h, v = Vector(surface.horizontal_axis_world), Vector(surface.vertical_axis_world)
        start = surface_world_point_from_uv(surface, tuple(g["origin_uv"]))
        for r in range(g["rows"]):
            for c in range(g["columns"]):
                items.append((f"grid[{c},{r}]", g.get("kind", "Window"), start + h * (c * g["step_x"]) + v * (r * g["step_z"]),
                              float(g["width"]), float(g["height"]), "AUTO"))
    return items


def _existing_same(wall, surface, kind, centre, width, height):
    """An opening of ``wall`` of the same kind at the same place and size, or None."""
    from .facade_placement import surface_uv_from_world_point
    from .facade_surface_resolver import compute_facade_frame
    frame = compute_facade_frame([tuple(wall.matrix_world @ wall.data.vertices[i].co)
                                  for i in wall.data.polygons[surface.face_indices[0]].vertices])
    cu, cv = surface_uv_from_world_point(surface, centre)
    for op in wall.children:
        if not is_opening(op) or _opening_kind(op) != kind or op.type != 'MESH':
            continue
        rect = _opening_rect(op, frame)
        if rect is None:
            continue
        u, v, w, h, _d = rect
        if abs(u - cu) * frame.width <= SAME_PLACE_M and abs(w - width) <= SAME_PLACE_M \
                and abs(h - height) <= SAME_PLACE_M and (
                    abs(v - cv) * frame.height <= SAME_PLACE_M
                    or kind == "Door" and abs(v * frame.height - h / 2) <= SAME_PLACE_M):  # notch: cut at the bottom
            return op
    return None


@_command
def apply_facade_layout(layout) -> dict:
    """Apply a ``cgml-facade-layout/1`` document (``docs/agent-api.md``) to one wall face in one undo
    step. Openings already there (same kind, centre and size within 5 cm) and those that do not fit
    are listed in ``skipped``; applying the same layout twice adds nothing."""
    from ..ops.installation_authoring import make_balcony
    from ..ops.opening_authoring import _surface_at
    if isinstance(layout, str):
        import json
        try:
            layout = json.loads(layout)
        except ValueError as e:
            raise CommandError("BAD_ARGUMENT", f"layout is not JSON: {e}")
    problems = validate_layout(layout)
    if problems:
        raise CommandError("BAD_ARGUMENT", "; ".join(problems[:10]))
    root = _building(layout["building_gml_id"])
    surface, changed = _wall_surface(layout["wall_gml_id"], layout["face_index"])
    wall = bpy.data.objects[surface.object_name]
    if building_ancestor_of(wall) is not root:
        raise CommandError("BAD_ARGUMENT", f"wall {layout['wall_gml_id']!r} is not part of {layout['building_gml_id']!r}")
    src = dict(layout.get("source") or {})
    prov = _placement_kw(src.pop("kind", "deep_learning"), src.pop("model_name", None), src.pop("model_version", None),
                         src.pop("confidence", None), {**src, "layout_schema": LAYOUT_SCHEMA})
    created, skipped, placed_at = [], [], {}
    for label, kind, centre, width, height, topology in _layout_items(layout, surface):
        surf = _surface_at(wall, centre)
        if surf is None:
            skipped.append({"item": label, "reason": "outside the wall face"})
            continue
        same = _existing_same(wall, surf, kind, centre, width, height)
        if same is not None:
            skipped.append({"item": label, "reason": f"already there ({_gid(same)})"})
            placed_at[label] = same
            continue
        from .facade_placement import (FacadeElementDefinition, PlacementRequest, place_semantic_element,
                                       surface_uv_from_world_point)
        u, v = surface_uv_from_world_point(surf, centre)
        definition = FacadeElementDefinition(
            semantic_type=kind, width=width, height=height, generator_name="facade_layout",
            door_touches_ground=None if kind == "Window" or topology == "AUTO" else topology == "GROUND")
        try:
            fill = place_semantic_element(definition, surf, PlacementRequest(position_uv=(u, v), **prov))
        except (ValueError, RuntimeError, NotImplementedError) as e:
            skipped.append({"item": label, "reason": str(e)})
            continue
        created.append(_gid(fill))
        placed_at[label] = fill
    for i, inst in enumerate(layout.get("installations", []) or []):
        label = f"installations[{i}]"
        target = placed_at.get(f"openings[{inst['at_opening']}]")
        if target is None:
            skipped.append({"item": label, "reason": f"openings[{inst['at_opening']}] was not placed"})
            continue
        pts = [target.matrix_world @ v.co for v in target.data.vertices]
        n = Vector(surface.normal_world)
        n = Vector((n.x, n.y, 0.0)).normalized()
        h = Vector((-n.y, n.x, 0.0))
        along = [q.dot(h) for q in pts]
        base = Vector(surface.origin_world)
        point = base + h * ((min(along) + max(along)) / 2 - base.dot(h))
        point.z = min(q.z for q in pts)
        point -= n * (point - base).dot(n)  # on the wall plane, under the opening's middle
        kind = inst.get("type", "PROJECTING")
        if any(str(c.get("cgml_semantic_type") or "") == "Balcony" and _near(c, point) for c in root.children):
            skipped.append({"item": label, "reason": "a balcony is already there"})
            continue
        width = (max(along) - min(along)) + (0.2 if kind == "FRENCH" else 0.8)
        obj = make_balcony(bpy.context, root, point, n, kind, width)
        created.append(_gid(obj))
    if created:
        changed = changed + [surface.semantic_id]
    return result(True, message=f"{len(created)} placed, {len(skipped)} skipped", created=created,
                  changed=changed, warnings=[f"{len(skipped)} item(s) skipped"] if skipped else [],
                  placed=len(created), skipped=skipped)


def building_ancestor_of(obj):
    from ..ops.installation_authoring import building_root
    return building_root(obj)


def _near(obj, point, tol=0.3):
    if obj.type != 'MESH' or not obj.data.vertices:
        return False
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = Vector([min(q[i] for q in pts) - tol for i in range(3)])
    hi = Vector([max(q[i] for q in pts) + tol for i in range(3)])
    return all(lo[i] <= point[i] <= hi[i] for i in range(3))


# =========================================================================== CityFurniture (B63)
_IMPLICIT_KEYS = ("cgml3_transformationMatrix", "cgml3_implicit_lod", "cgml3_implicit_template_id",
                  "cgml3_implicit_reference_point", "cgml3_implicit_mime_type", "cgml3_implicit_library_object")


@_command
def place_city_furniture(source_gml_id, point, rotation_deg=0.0, function=None) -> dict:
    """Put a copy of a ready-made CityFurniture of the scene (imported or prepared by the user) at
    world ``point``: the copy's base (bottom centre of its bounding box) lands there, turned by
    ``rotation_deg`` around the vertical. The copy gets new gml_ids for itself, its polygons, rings
    and multi-surface, keeps the attributes (``function`` overrides frn:function). An implicit
    (template) source becomes explicit geometry."""
    import math
    from mathutils import Matrix
    from .authoring_core.domain.ids import mint_gml_id, ring_id
    src = _need(source_gml_id)
    if str(src.get("cgml3_feature") or "") not in FURNITURE_TYPES or src.type != 'MESH':
        raise CommandError("BAD_ARGUMENT", f"{source_gml_id!r} is not a CityFurniture mesh")
    p = _vector3("point", point)
    rot = _number("rotation_deg", rotation_deg, -360.0, 360.0)
    if src.children:
        raise CommandError("BAD_ARGUMENT", f"{source_gml_id!r} has child objects; only single-mesh "
                                           "CityFurniture can be copied")
    corners = [src.matrix_world @ Vector(c) for c in src.bound_box]
    anchor = Vector(((min(c.x for c in corners) + max(c.x for c in corners)) / 2,
                     (min(c.y for c in corners) + max(c.y for c in corners)) / 2, min(c.z for c in corners)))
    mesh = src.data.copy()
    gid = f"UUID_{__import__('uuid').uuid4()}"
    ms_id = mint_gml_id()
    for i, m in enumerate(mesh.materials):
        if m is None:
            continue
        c = m.copy()
        old_poly = str(c.get("gml_polygon_id") or "")
        poly = mint_gml_id()
        c["gml_polygon_id"] = poly
        c["gml_ring_id"] = ring_id(poly, 0)
        if "gml_multisurface_id" in c:
            c["gml_multisurface_id"] = ms_id
        if old_poly and str(c.get("app_target_or_uri") or "") == old_poly:
            c["app_target_or_uri"] = poly  # appearance follows the polygon
        mesh.materials[i] = c
    obj = bpy.data.objects.new(f"CityFurniture_{gid}", mesh)
    for k in src.keys():
        if k not in ("gml_id",) + _IMPLICIT_KEYS:
            obj[k] = src[k]
    obj["gml_id"] = gid
    if function is not None:
        obj["frn:function"] = str(function)
    obj.matrix_world = Matrix.Translation(p) @ Matrix.Rotation(math.radians(rot), 4, 'Z') \
        @ Matrix.Translation(-anchor) @ src.matrix_world
    for coll in (src.users_collection or [bpy.context.scene.collection]):
        coll.objects.link(obj)
    warnings = ["the source was an implicit (template) geometry; the copy is explicit"] \
        if any(k in src for k in _IMPLICIT_KEYS) else []
    return result(True, message=f"CityFurniture {gid} at {tuple(round(c, 2) for c in p)}", created=[gid],
                  warnings=warnings, object=obj.name)
