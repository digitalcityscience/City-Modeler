# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ops/installation_authoring.py
"""Add Balcony / Add Chimney (B08, click placement B15): outer BuildingInstallations.

From the sidebar both work like Window / Door: the mouse shows a preview on
the wall (balcony) or roof (chimney) under it, a left click places it, Esc
or right click cancels, F9 adjusts it afterwards. Clicking a door or window
fits the balcony to it (width and floor at the opening's bottom).

B101: the railing is flat faces (one surface of its own), not 5 cm boxes: SIG3D LoD3 draws balcony
parts up to 0.2 m thick planar, and Hamburg's LoD3 installations have 3-10 polygons.

Balcony types follow the B03 decision (slab + railing, LoD3). Bonsai has no
balcony tool (a slab plus an IfcRailing path, railing types FRAMELESS_PANEL /
WALL_MOUNTED_HANDRAIL); the railing here is a frameless panel like Bonsai's:
- Projecting: floor slab out of the wall, panel railing on three sides
- French: panel railing just in front of a door or window, no floor
- Corner (B28): an L around the building corner at one end of the wall
Chimneys (B28): Square, Round (a 16-sided prism), Wide (two flues).

B70: in Edit Facade Balcony / Chimney are tools like Window / Door: select the
face, a drag on it places the tool's type (``cgml.balcony_draw`` /
``cgml.chimney_draw``; a click elsewhere selects); the type gallery sits in the
tool header. B73: a click places nothing (like Window / Door, B71); the header has
the size again (balcony width / depth / railing, chimney size).
B73: a new installation goes in the "BuildingInstallation" collection next to the
building's collection, where the importer puts imported ones (still parented to it).
B71: the type's blue preview follows the mouse on the selected face; a balcony /
chimney keeps its type and size (``cgml_install_params``, never exported), so
Copy / Paste (``paste_args``) rebuilds it on another wall / roof.

Built like an imported installation: a mesh object parented to its
Building with cgml3_feature=BuildingInstallation, cgml_semantic_type
Balcony/Chimney, cgml_source_role=outer and (B40) one material per boundary
surface class (Roof / Wall / OuterCeiling), so the writers give it its own surfaces. No cgml_subtype_origin, so the exporter writes the add-on's
installation code (5010 balcony, 5030 chimney; shared/semantic_types.py).

B34: nothing is placed at a computed spot. Without a click (``host`` + ``point``
+ ``normal``) both refuse with a message; scripts pass those explicitly.
"""

import uuid

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy.types import Operator
from mathutils import Vector

from ..Openings_Cutter.Openings_Cutter import resolve_opening_cutter_snap_mouse, snap_point_to_increment
from ..shared import modal_hint
from ..shared.lod_helpers import lod_promotion_root, top_level_lods
from ..shared.object_helpers import breadth_first_descendants

BALCONY_TYPES = (
    ('PROJECTING', "Projecting", "Floor slab out of the wall, panel railing on three sides"),
    ('FRENCH', "French", "Panel railing just in front of a door or window, no floor"),
    ('CORNER', "Corner", "Wraps around the building corner at one end of the wall (B28)"),
)
CHIMNEY_TYPES = (  # B28; B80: the usual German kinds (ids kept for Paste / scripts)
    ('SQUARE', "Brick", "Brick chimney (gemauert): one rectangular block"),
    ('COVER', "Cover plate", "Brick block with an overhanging cover plate (Kragplatte)"),
    ('HOOD', "Rain hood", "Brick block with a hood on four posts (Regenhaube)"),
    ('WIDE', "Double flue", "One block with two flue pots on top (zweizügig)"),
    ('ROUND', "Steel pipe", "Round stainless steel flue (Edelstahlschornstein)"),
)
_CAP_OUT, _POST, _POT = 0.06, 0.25, 0.25  # B80: plate overhang, hood gap (posts), flue pots (m)
CORNER_SIDES = (
    ('END', "Right end", "The corner at the right end of the wall, seen from outside"),
    ('START', "Left end", "The corner at the left end of the wall, seen from outside"),
)
_OPENINGS = {"Door", "DoorSurface", "Window", "WindowSurface"}
_SLAB, _FRENCH_GAP = 0.2, 0.12
PART_PROP = "cgml3_part"  # B101: on a surface material, which part of the installation it is ("railing")
UP = Vector((0.0, 0.0, 1.0))


def building_root(obj):
    """The Building (or BuildingPart) ``obj`` belongs to, or None (not a building, browse outline)."""
    if obj is None or obj.get("cgml3_browse_path"):
        return None
    root = lod_promotion_root(obj)
    return root if root is not None and root.get("cgml3_feature") in ("Building", "BuildingPart") else None


def _poll_building(cls, context):
    obj = context.active_object
    # B28: also from Edit Facade (Edit Mode), on the selected face
    ok = context.mode in ('OBJECT', 'EDIT_MESH') and building_root(obj) is not None
    if not ok and getattr(cls, "TOOL", ""):
        from ..ui.tools import tool_active
        ok = context.mode == 'OBJECT' and tool_active(context, cls.TOOL)  # B19: the click picks the building
    if not ok:
        cls.poll_message_set("Select a CityGML building first")
    return ok


def _corners(centre, ax, ay, az, size):
    sx, sy, sz = size
    return [centre + ax * (dx * sx) + ay * (dy * sy) + az * (dz * sz)
            for dz in (-0.5, 0.5) for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))]


def _rings(item):
    """(bottom ring, top ring) of a box (centre, x_axis, y_axis, z_axis, (sx, sy, sz)) or of a
    B91 prism given as its two rings (the corner balcony's L slab, a chimney cut by the roof),
    B102: optionally with a third item, the set of ends left open ({"top"}, {"bottom"})."""
    if len(item) in (2, 3):
        return list(item[0]), list(item[1])
    c = _corners(*item)
    return c[:4], c[4:]


def _strip(path, height, inside):
    """B101: a railing as flat faces, one quad per leg of ``path`` (its foot points), ``height`` high,
    facing away from ``inside``. SIG3D LoD3: balcony parts up to 0.2 m thick are planar."""
    return ("strip", [Vector(p) for p in path], height, Vector(inside))


def _strip_quads(item):
    """[(foot a, foot b, top b, top a)] of a ``_strip``, each facing away from its ``inside``."""
    _tag, path, height, inside = item
    out = []
    for a, b in zip(path, path[1:]):
        quad = [a, b, b + UP * height, a + UP * height]
        if (b - a).cross(UP).dot((a + b) / 2 - inside) < 0:  # (b - a) x up is the quad's normal
            quad.reverse()
        out.append(quad)
    return out


def _newell(ring):
    return sum((ring[i].cross(ring[(i + 1) % len(ring)]) for i in range(len(ring))), Vector())


def _flat_faces(item):
    """Polygons of a tagged item: a ``_strip``'s quads, or a B102 ``("flat", ring)``: one horizontal
    face facing up (a chimney's cover / hood plate; SIG3D LoD3: up to 0.2 m thick is planar)."""
    if item[0] == "strip":
        return _strip_quads(item)
    ring = [Vector(p) for p in item[1]]
    return [ring if _newell(ring).z > 0 else ring[::-1]]


def _boxes_mesh(name, boxes):
    """One mesh of closed solids (boxes or B91 ring prisms, see ``_rings``), B101 railing strips and
    B102 flat plates in world space."""
    verts, faces = [], []
    for item in boxes:
        if isinstance(item[0], str):
            for poly in _flat_faces(item):
                faces.append(list(range(len(verts), len(verts) + len(poly))))
                verts += poly
            continue
        bot, top = _rings(item)
        base, k = len(verts), len(bot)
        verts += bot + top
        b, t = list(range(base, base + k)), list(range(base + k, base + 2 * k))
        solid = [b[::-1], t] + [[b[i], b[(i + 1) % k], t[(i + 1) % k], t[i]] for i in range(k)]
        if _newell(top).dot(top[0] - bot[0]) < 0:  # B82: a clockwise ring (balcony h, n, up) would turn it inside out
            solid = [f[::-1] for f in solid]
        open_ = item[2] if len(item) == 3 else ()  # B102: an end covered by a plate or standing on a block
        faces += [f for f, end in zip(solid, ("bottom", "top", *["side"] * k)) if end not in open_]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def box_edges(boxes) -> list:
    """Line pairs of every solid's edges (click preview)."""
    pts = []
    for item in boxes:
        if isinstance(item[0], str):
            for poly in _flat_faces(item):
                for a, b in zip(poly, poly[1:] + poly[:1]):
                    pts += [a[:], b[:]]
            continue
        bot, top = _rings(item)
        k = len(bot)
        for i in range(k):
            pts += [bot[i][:], bot[(i + 1) % k][:], top[i][:], top[(i + 1) % k][:], bot[i][:], top[i][:]]
    return pts


def balcony_boxes(base, normal, kind, width, depth, railing, mirror=False):
    """Boxes of a balcony whose floor centre touches the wall at ``base``; ``normal`` points out.
    CORNER: ``base`` is the building corner on the wall (floor level); the balcony runs ``width``
    back along this wall and ``width`` along the neighbouring one (``mirror``: the other end)."""
    n = Vector((normal[0], normal[1], 0.0)).normalized()
    h = Vector((-n.y, n.x, 0.0)) * (-1.0 if mirror else 1.0)
    base = Vector(base)
    def at(u, v):
        return base + h * u + n * v

    if kind == 'CORNER':
        # B91: one L-shaped slab (one floor face), not two boxes; in (h, n): along this wall, then
        # along the neighbour (faces +h); (0, 0) is the building corner
        top = [at(-width, 0), at(-width, depth), at(depth, depth), at(depth, -width), at(0, -width), at(0, 0)]
        boxes = [([p - UP * _SLAB for p in top], top)]
        rail = top[:5]  # B101: the outer edge of the L, open at the walls
    elif kind == 'FRENCH':
        boxes, rail = [], [at(-width / 2, _FRENCH_GAP), at(width / 2, _FRENCH_GAP)]
    else:
        boxes = [(base + n * (depth / 2) - UP * (_SLAB / 2), h, n, UP, (width, depth, _SLAB))]
        rail = [at(-width / 2, 0), at(-width / 2, depth), at(width / 2, depth), at(width / 2, 0)]
    if railing > 0:
        boxes.append(_strip(rail, railing, base))
    return boxes


def balcony_size(wall_len, width=None):
    """B67: (width, depth) of a clicked balcony from the wall it goes on: 60 % of the wall,
    at most 4 m, never wider than the wall; depth half the width, 0.6-1.5 m. ``width`` given
    (a door / window fit): only the depth follows it."""
    if width is None:
        width = max(0.5, min(0.6 * wall_len, 4.0, wall_len))
    return width, max(0.6, min(1.5, 0.5 * width))


def wall_extent(obj, loc, n):
    """B67: (lo, hi) along the wall (h = (-n.y, n.x)) of ``obj``'s faces in the clicked wall plane
    through ``loc`` with horizontal normal ``n``, or None (no such face, e.g. a sloped wall)."""
    # shortcut: coplanar faces of one merged mesh count as one wall even if not connected; split by connectivity if that shows up
    h = Vector((-n.y, n.x, 0.0))
    mw = obj.matrix_world
    nm = mw.to_3x3().inverted_safe().transposed()
    verts = obj.data.vertices
    along = []
    for poly in obj.data.polygons:
        if abs((nm @ poly.normal).normalized().dot(n)) < 0.98 or abs((mw @ poly.center - loc).dot(n)) > 0.05:
            continue
        along += [(mw @ verts[i].co).dot(h) for i in poly.vertices]
    return (min(along), max(along)) if along else None


def chimney_boxes(root, centre, normal, size, above_ridge, kind='SQUARE', depth=None):
    """Solids of a ``size`` (x) by ``depth`` (y, default ``size``) chimney standing on the roof plane at
    ``centre`` (B91: its base follows the slope, nothing below the roof; the roof stays whole) up to
    ``above_ridge`` over the highest roof point (the top of its plate / pots).
    B80 kinds: SQUARE a block, COVER + an overhanging plate, HOOD + a plate on four posts, WIDE + two
    flue pots, ROUND a pipe (a 16-sided prism).
    B102 (SIG3D LoD3, parts up to 0.2 m are planar): the plates are one flat face (COVER's is the
    block's top), the 6 cm hood posts are not drawn, the pots stand open-bottomed on the block."""
    import math
    depth = depth or size
    if kind == 'ROUND':
        size = depth = min(size, depth)
        ring = [(math.cos(a) * size / 2, math.sin(a) * size / 2) for a in (math.tau * i / 16 for i in range(16))]
    else:
        ring = [(-size / 2, -depth / 2), (size / 2, -depth / 2), (size / 2, depth / 2), (-size / 2, depth / 2)]
    centre, normal = Vector(centre), Vector(normal)
    # shortcut: one roof plane (the clicked face's); a chimney across a ridge floats on the far side
    slope = (-normal.x / normal.z, -normal.y / normal.z) if abs(normal.z) > 1e-6 else (0.0, 0.0)
    base = [Vector((centre.x + x, centre.y + y, centre.z + x * slope[0] + y * slope[1])) for x, y in ring]
    roofs = [o for o in breadth_first_descendants(root) if o.type == 'MESH' and o.get("cgml3_feature") == "RoofSurface"]
    meshes = roofs or [o for o in [root, *breadth_first_descendants(root)] if o.type == 'MESH']
    top = max((o.matrix_world @ v.co).z for o in meshes for v in o.data.vertices) + above_ridge
    body_top = max(max(p.z for p in base) + 0.1, top - {"HOOD": _POST, "WIDE": _POT}.get(kind, 0.0))

    def rect(x, y, sx, sy, z):
        return [Vector((centre.x + x + dx * sx / 2, centre.y + y + dy * sy / 2, z))
                for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]

    body = (base, [Vector((p.x, p.y, body_top)) for p in base])
    plate = ("flat", rect(0, 0, size + 2 * _CAP_OUT, depth + 2 * _CAP_OUT, top))
    if kind == 'COVER':
        return [(*body, {"top"}), plate]
    if kind == 'HOOD':
        return [body, plate]  # the plate floats _POST over the block; its posts are under 0.2 m
    if kind == 'WIDE':  # the two pots along the longer side
        along_x = size >= depth
        pot = 0.5 * (min(size / 2, depth) if along_x else min(size, depth / 2))
        off = (size / 4, 0.0) if along_x else (0.0, depth / 4)
        return [body] + [(rect(s * off[0], s * off[1], pot, pot, body_top), rect(s * off[0], s * off[1], pot, pot, top),
                          {"bottom"}) for s in (-1, 1)]
    return [body]


def face_class(obj, index=-1):
    """CityGML class of ``obj``'s face ``index``: the object's feature, or on a merged
    building the face material's SurfaceTyp."""
    kind = str(obj.get("cgml3_feature") or "")
    if kind in ("Building", "BuildingPart") and obj.type == 'MESH' and obj.data.materials:
        polys = obj.data.polygons
        mi = polys[index].material_index if 0 <= index < len(polys) else -1
        mat = obj.data.materials[mi] if 0 <= mi < len(obj.data.materials) else None
        kind = str(mat.get("SurfaceTyp") or "") if mat is not None else ""
    return kind


def target_reason(want, kind, normal):
    """B33: '' when a face of class ``kind`` (``normal`` facing the viewer) takes a balcony
    (``want`` 'WALL') or a chimney ('ROOF'), else why not. One rule for the row and the T tools."""
    if want == 'ROOF':
        return "" if kind == "RoofSurface" or normal[2] >= 0.2 else "Select a roof face"
    if kind in ("WallSurface", *_OPENINGS) and abs(normal[2]) <= 0.7:
        return ""
    return "Select a wall, door or window face"


def selected_face_placement(context, want):
    """B28: ``(cand, reason)`` from the one face selected in Edit Mode. ``want`` 'WALL' (a wall, door or
    window face) or 'ROOF'. ``cand`` = host / point / normal (+ face extent) for balcony_add / chimney_add."""
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return None, "Edit Facade, then select a face"
    root = building_root(obj)
    if root is None:
        return None, "Not part of a CityGML building"
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    faces = [f for f in bm.faces if f.select]
    if len(faces) != 1:
        return None, "Select one face"
    face = faces[0]
    kind = str(obj.get("cgml3_feature") or "")
    if kind in ("Building", "BuildingPart") and obj.data.materials:  # a merged building: the face's material
        mat = obj.data.materials[face.material_index] if face.material_index < len(obj.data.materials) else None
        kind = str(mat.get("SurfaceTyp") or "") if mat is not None else ""
    mw = obj.matrix_world
    pts = [mw @ v.co for v in face.verts]
    normal = (mw.to_3x3().inverted_safe().transposed() @ face.normal).normalized()
    centre = sum(pts, Vector()) / len(pts)
    reason = target_reason(want, kind, normal)
    if reason:
        return None, reason
    if want == 'ROOF':
        return {"host": lod_promotion_root(root).name, "point": centre[:], "normal": normal[:]}, ""
    n = Vector((normal.x, normal.y, 0.0)).normalized()
    h = Vector((-n.y, n.x, 0.0))
    along = [p.dot(h) for p in pts]
    return {"host": lod_promotion_root(root).name, "normal": n[:], "opening": kind in _OPENINGS,
            "lo": min(along), "hi": max(along), "zmin": min(p.z for p in pts), "zmax": max(p.z for p in pts),
            "plane": centre[:]}, ""


def face_surface_class(normal, centre=None, floor_z=None, wall=None, tol=1e-3) -> str:
    """B40/B45: the CityGML boundary surface of an installation face (construction.xsd /
    core.xsd definitions): up = RoofSurface, except a balcony's slab top at ``floor_z``
    (OuterFloorSurface, "the floor of a loggia"); down = OuterCeilingSurface, except a face at
    ``floor_z`` (a railing standing on the slab) or, without a floor, the base inside the roof
    (ClosureSurface, a virtual surface closing the volume); a side face on the host wall plane
    ``wall`` = (point, outward normal) is a ClosureSurface too; the rest are WallSurfaces."""
    if normal.z > 0.5:
        return "OuterFloorSurface" if floor_z is not None and abs(centre.z - floor_z) < tol else "RoofSurface"
    if normal.z < -0.5:
        if floor_z is None or abs(centre.z - floor_z) < tol:
            return "ClosureSurface"
        return "OuterCeilingSurface"
    if wall is not None and normal.dot(wall[1]) < -0.9 and abs((centre - wall[0]).dot(wall[1])) < tol:
        return "ClosureSurface"
    return "WallSurface"


def installation_collection(context, root):
    """B73: the "BuildingInstallation" collection next to ``root``'s collection, as the importer makes it
    (``_INLINE_SUBFEATURE_COLLECTION_NAMES``; flagged so the writer exports its members inline only)."""
    home = root.users_collection[0] if root.users_collection else context.scene.collection
    owner = next((c for c in (context.scene.collection, *bpy.data.collections) if home.name in c.children), home)
    coll = next((c for c in owner.children if c.name.split(".")[0] == "BuildingInstallation"), None)
    if coll is None:
        coll = bpy.data.collections.new("BuildingInstallation")
        owner.children.link(coll)
        coll["cgml3_auto_disabled_subfeatures"] = True
    return coll


def _make_installation(context, root, kind, mesh, floor_z=None, wall=None):
    """The installation's faces become its own boundary surfaces (B40, classes B45), stored
    the way the reader stores an imported one: a material per face with its polygon id, the
    faces of one class sharing a surface and MultiSurface id."""
    from ..shared.authoring_core.domain.ids import mint_gml_id, ring_id

    lod = max([2, *top_level_lods(root)])
    surfaces = {}
    for poly in mesh.polygons:  # the mesh is in world space (the object keeps an identity transform)
        cls = face_surface_class(poly.normal, poly.center, floor_z, wall)
        # B101: a balcony's railing (upright, above the slab) is a surface of its own, so it can take
        # another material than the slab's edges
        railing = (kind == "Balcony" and abs(poly.normal.z) <= 0.5
                   and (floor_z is None or poly.center.z > floor_z + 1e-3))
        surface_id, ms_id = surfaces.setdefault((cls, railing), (mint_gml_id(), mint_gml_id()))
        mat = bpy.data.materials.new(f"{kind}_{'Railing' if railing else cls}_{uuid.uuid4().hex[:8]}")
        for key in ("SurfaceTyp", "surface_type", "Typ"):
            mat[key] = cls
        if railing:
            mat[PART_PROP] = "railing"
        mat["lod"] = lod
        poly_id = mint_gml_id()
        mat["gml_polygon_id"] = poly_id
        mat["gml_ring_id"] = ring_id(poly_id, 0)
        mat["con_surface_id"] = surface_id
        mat["gml_multisurface_id"] = ms_id
        mat["is_multisurface_member"] = True
        mesh.materials.append(mat)
        poly.material_index = len(mesh.materials) - 1
    obj = bpy.data.objects.new(kind, mesh)
    installation_collection(context, root).objects.link(obj)
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted()
    obj["cgml3_feature"] = "BuildingInstallation"
    obj["gml_id"] = f"UUID_{uuid.uuid4()}"
    obj["cgml_semantic_type"] = kind
    obj["cgml_source_role"] = "outer"
    obj["cgml_parent_id"] = str(root.get("gml_id") or "")
    view_layer = context.view_layer  # B59: a script/timer context has no selected_objects
    for o in view_layer.objects:
        if o.select_get(view_layer=view_layer):
            o.select_set(False, view_layer=view_layer)
    obj.select_set(True, view_layer=view_layer)
    view_layer.objects.active = obj
    return obj


def make_balcony(context, root, point, normal, kind='PROJECTING', width=3.0, depth=1.4, railing=1.0,
                 corner_side='END', offset=0.0):
    """B59: the balcony ``balcony_add`` and the agent API build (world ``point`` on the wall at
    floor height, horizontal outward ``normal``)."""
    n = Vector(normal)
    h = Vector((-n.y, n.x, 0.0))
    boxes = balcony_boxes(Vector(point) + h * offset, n, kind, width, depth, railing, mirror=corner_side == 'START')
    floor_z = None if kind == 'FRENCH' else point[2]  # a French balcony has no slab
    obj = _make_installation(context, root, "Balcony", _boxes_mesh("Balcony", boxes),
                             floor_z=floor_z, wall=(Vector(point), n))
    obj["cgml_install_params"] = {"kind": kind, "width": width, "depth": depth, "railing": railing}  # B71: Paste
    return obj


def make_chimney(context, root, point, normal, kind='SQUARE', size=0.6, above_ridge=0.8, depth=None):
    """B59: the chimney ``chimney_add`` and the agent API build (world ``point`` on the roof)."""
    depth = depth or size
    mesh = _boxes_mesh("Chimney", chimney_boxes(root, point, normal, size, above_ridge, kind, depth))
    # B80: only the base closes (a plate's underside is a ceiling); B91: the base lies in the roof plane
    # through ``point``, so its centre is at point's height, and on a steep roof the plane rule catches it
    obj = _make_installation(context, root, "Chimney", mesh, floor_z=point[2],
                             wall=(Vector(point), Vector(normal).normalized()))
    obj["cgml_install_params"] = {"kind": kind, "size": size, "depth": depth, "above_ridge": above_ridge}  # B71
    return obj


def edit_selection(context):
    """(active object name, selected face indices) in Edit Mode, to give back after a placement."""
    obj = context.view_layer.objects.active
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        return None
    import bmesh
    return obj.name, [f.index for f in bmesh.from_edit_mesh(obj.data).faces if f.select]


def back_to_edit_facade(context, obj, keep=None):
    """B33: placed from Edit Facade -> back in Edit Facade on the same building. Like Window / Door,
    the wall face you drew on stays selected (``keep`` = ``edit_selection`` from before), so the next
    element needs no re-selecting; without it the new element is active with its faces selected."""
    wall = bpy.data.objects.get(keep[0]) if keep else None
    if wall is None or wall.type != 'MESH':
        wall, keep = obj, None
    root = building_root(obj) or obj
    for o in [root, *breadth_first_descendants(root)]:
        if o.type == 'MESH' and o.visible_get():
            o.select_set(True)
    context.view_layer.objects.active = wall
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    import bmesh
    bm = bmesh.from_edit_mesh(wall.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        if keep is None or f.index in keep[1]:
            f.select_set(True)
    if keep and keep[1] and keep[1][0] < len(bm.faces):
        bm.faces.active = bm.faces[keep[1][0]]
    bmesh.update_edit_mesh(wall.data)


def _clicked_host(op):
    """B34: the building the click (or a script's explicit ``host`` + ``point`` + ``normal``)
    names, or None after an ERROR report: no click, no placement."""
    props = op.properties
    if not op.host or not (props.is_property_set("point") and props.is_property_set("normal")):
        what = "a wall, door or window" if op.WANT == 'WALL' else "a roof"
        op.report({'ERROR'}, f"Click {what} where it goes (scripts pass host, point and normal)")
        return None
    root = bpy.data.objects.get(op.host)
    if root is None:
        op.report({'ERROR'}, f"Building '{op.host}' not found")
    return root


# --------------------------------------------------------------------------- click placement
def ray_under_mouse(context, event, holder=None):
    """View ray under the mouse. B35: with Blender's snap on (vertex / edge / edge midpoint) the
    mouse snaps first, through Window / Door's code; ``holder`` keeps the snap cache for a modal."""
    from bpy_extras import view3d_utils
    coord = (event.mouse_region_x, event.mouse_region_y)
    if holder is not None:
        coord, holder._snap_cache, _snapped = resolve_opening_cutter_snap_mouse(
            context, coord, getattr(holder, "_snap_cache", None))
    origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
    direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
    return origin, direction


class _ClickPlace:
    """Mouse preview + left click; subclasses give IDNAME, HINT, WANT, MISS, place_from_hit() and boxes()."""

    _preview = None
    why = ""

    @modal_hint.clears_status
    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "Only available in the 3D viewport")
            return {'CANCELLED'}
        if context.region is None or context.region.type != 'WINDOW':
            from .facade_authoring import invoke_in_viewport_region
            return invoke_in_viewport_region(context, self.IDNAME, kind=self.kind)  # B29: keep the type
        self.host = ""
        self._candidate = None
        if self.from_tool:
            # B19 toolbar click: place at this click at once; a click on nothing selects.
            cand = self.candidate_at(context, *ray_under_mouse(context, event, self))
            if cand is None:
                if self.why:  # B33: a building face that takes none: say why, place nothing
                    self.report({'WARNING'}, self.why)
                    return {'CANCELLED'}
                return {'PASS_THROUGH'}
            for key, value in cand.items():
                setattr(self, key, value)
            return self.execute(context)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_preview, (), 'WINDOW', 'POST_VIEW')
        modal_hint.status(context, modal_hint.hint(self.HINT, modal_hint.snap_note(context)))  # B24: status bar
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        self._snap_cache = None
        modal_hint.status(context)
        if context.area is not None:
            context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)

    def candidate_at(self, context, origin, direction):
        """Placement props (dict) for the first building surface on the ray, or None;
        then ``self.why`` says why a building surface there takes nothing ('' = no building)."""
        self.why = ""
        dg = context.evaluated_depsgraph_get()
        hit, loc, normal, index, obj, _m = context.scene.ray_cast(dg, origin, direction)
        if not hit:
            return None
        obj = obj.original
        root = building_root(obj)
        if root is None:
            return None
        normal = normal.normalized()
        facing = -normal if normal.dot(direction) > 0 else normal
        loc = snap_point_to_increment(context, loc, facing)  # B35: Blender's Increment snap
        kind = face_class(obj, index)
        cand = None
        if not target_reason(self.WANT, kind, facing):
            cand = self.place_from_hit(obj, root, loc, normal, direction)
        if cand is None:
            self.why = f"That is {'a ' + kind if kind else 'a surface without a CityGML class'}; {self.MISS}"
        return cand

    @modal_hint.clears_status
    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            self._candidate = self.candidate_at(context, *ray_under_mouse(context, event, self))
            context.area.tag_redraw()
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            cand = self.candidate_at(context, *ray_under_mouse(context, event, self))
            if cand is None:
                self.report({'WARNING'}, self.why or self.MISS)
                return {'RUNNING_MODAL'}
            self._end(context)
            for key, value in cand.items():
                setattr(self, key, value)
            return self.execute(context)
        return {'PASS_THROUGH'}  # orbit, pan, zoom keep working

    def _draw_preview(self):
        import gpu
        from gpu_extras.batch import batch_for_shader
        cand = self._candidate
        if not cand:
            return
        try:
            pts = box_edges(self.boxes(cand))
        except Exception:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        vp = gpu.state.viewport_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        shader.bind()
        shader.uniform_float("viewportSize", (vp[2], vp[3]))
        shader.uniform_float("lineWidth", 2.5)
        shader.uniform_float("color", (0.2, 0.55, 1.0, 1.0))
        batch_for_shader(shader, 'LINES', {"pos": pts}).draw(shader)
        gpu.state.blend_set('NONE')


class CGML_OT_balcony_add(_ClickPlace, Operator):
    """Add a balcony (CityGML BuildingInstallation): click a wall (floor at the click) or a door / window (fitted to it); F9 to adjust"""
    bl_idname = "cgml.balcony_add"
    bl_label = "Balcony"  # B33: = the tool name (F9 / undo)
    bl_options = {'REGISTER', 'UNDO'}
    IDNAME = "cgml.balcony_add"
    TOOL = "cgml.balcony_tool"
    WANT = 'WALL'
    HINT = modal_hint.hint(("LMB", "Place on a wall (floor at the click; door / window: fitted)"),
                           ("Esc/RMB", "Cancel"))
    MISS = "click a wall, door or window of a CityGML building"

    kind: EnumProperty(name="Type", items=BALCONY_TYPES, default='PROJECTING')
    width: FloatProperty(name="Width", default=3.0, min=0.5, unit='LENGTH')
    depth: FloatProperty(name="Depth", default=1.4, min=0.3, unit='LENGTH')
    railing: FloatProperty(name="Railing height", default=1.0, min=0.0, unit='LENGTH')
    corner_side: EnumProperty(name="Corner", items=CORNER_SIDES, default='END')
    offset: FloatProperty(name="Slide along wall", default=0.0, unit='LENGTH')
    host: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    point: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    normal: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    from_tool: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    poll = classmethod(_poll_building)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "kind")
        col.prop(self, "width")
        if self.kind != 'FRENCH':
            col.prop(self, "depth")
        if self.kind == 'CORNER':
            col.prop(self, "corner_side")
        col.prop(self, "railing")
        if self.kind != 'CORNER':
            col.prop(self, "offset")

    def place_from_hit(self, obj, root, loc, normal, direction):
        n = Vector((normal.x, normal.y, 0.0))
        if n.length < 0.7:  # not a vertical surface
            return None
        n.normalize()
        if n.dot(direction) > 0:
            n = -n  # face the viewer: the balcony goes on the side you click from
        h = Vector((-n.y, n.x, 0.0))
        cand = {"host": root.name, "point": loc[:], "normal": n[:]}
        if self.kind == 'CORNER' and obj.get("cgml3_feature") in _OPENINGS:
            return None  # a corner balcony goes on a wall, not on a door / window (was the B28 face rule)
        if obj.type != 'MESH':
            return cand
        here = loc.dot(h)
        if obj.get("cgml3_feature") in _OPENINGS:
            pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
            along = [p.dot(h) for p in pts]
            centre = loc + h * ((min(along) + max(along)) / 2 - here)
            centre.z = min(p.z for p in pts)
            wall = obj.parent  # B82: the opening box sticks out of its wall; sit on the wall plane, not on the box
            if wall is not None and wall.type == 'MESH' and wall.data.polygons:
                centre += n * (wall.matrix_world @ wall.data.polygons[0].center - centre).dot(n)
            width, depth = balcony_size(0, max(along) - min(along) + (0.2 if self.kind == 'FRENCH' else 0.8))
            cand.update(point=centre[:], width=width, depth=depth)
            return cand
        # B67: a type picked from the gallery / tool gets its size from the clicked wall (F9 changes it)
        ext = wall_extent(obj, loc, n)
        if ext is None:  # B28 fallback: the whole object
            along = [(obj.matrix_world @ v.co).dot(h) for v in obj.data.vertices]
            ext = (min(along), max(along))
        lo, hi = ext
        width, depth = balcony_size(hi - lo)
        cand.update(width=width, depth=depth)
        if self.kind == 'CORNER':  # B28: the wall end nearer the click
            end = abs(hi - here) <= abs(here - lo)
            cand.update(point=(loc + h * ((hi if end else lo) - here))[:], corner_side='END' if end else 'START')
            return cand
        centre = min(max(here, lo + width / 2), hi - width / 2)  # B67: the balcony stays on the wall
        cand.update(point=(loc + h * (centre - here))[:])
        return cand

    def boxes(self, cand=None):
        cand = cand or {}
        base = Vector(cand.get("point", self.point))
        n = Vector(cand.get("normal", self.normal))
        h = Vector((-n.y, n.x, 0.0))
        side = cand.get("corner_side", self.corner_side)
        return balcony_boxes(base + h * self.offset, n, self.kind, cand.get("width", self.width),
                             cand.get("depth", self.depth), self.railing, mirror=side == 'START')

    def execute(self, context):
        root = _clicked_host(self)
        if root is None:
            return {'CANCELLED'}
        from_edit = context.mode == 'EDIT_MESH'
        keep = edit_selection(context) if from_edit else None
        if from_edit:  # B29: clicked in Edit Facade; the new object needs Object Mode
            bpy.ops.object.mode_set(mode='OBJECT')
        on = root.name
        obj = make_balcony(context, root, self.point, self.normal, self.kind, self.width, self.depth,
                           self.railing, self.corner_side, self.offset)
        if from_edit:
            back_to_edit_facade(context, obj, keep)
        self.report({'INFO'}, f"Balcony '{obj.name}' on '{on}'")
        return {'FINISHED'}


class CGML_OT_chimney_add(_ClickPlace, Operator):
    """Add a chimney (CityGML BuildingInstallation): click a roof where it goes; it rises above the ridge; F9 to adjust"""
    bl_idname = "cgml.chimney_add"
    bl_label = "Chimney"
    bl_options = {'REGISTER', 'UNDO'}
    IDNAME = "cgml.chimney_add"
    TOOL = "cgml.chimney_tool"
    WANT = 'ROOF'
    HINT = modal_hint.hint(("LMB", "Place on a roof"), ("Esc/RMB", "Cancel"))
    MISS = "click a roof of a CityGML building"

    kind: EnumProperty(name="Type", items=CHIMNEY_TYPES, default='SQUARE')
    size: FloatProperty(name="Width", default=0.6, min=0.2, unit='LENGTH')
    depth: FloatProperty(name="Depth", default=0.6, min=0.2, unit='LENGTH')  # B80: a rectangle
    above_ridge: FloatProperty(name="Above ridge", default=0.8, min=0.0, unit='LENGTH')
    host: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    point: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    normal: FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    from_tool: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    poll = classmethod(_poll_building)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "kind")
        col.prop(self, "size")
        col.prop(self, "depth")
        col.prop(self, "above_ridge")

    def place_from_hit(self, obj, root, loc, normal, direction):
        if normal.dot(direction) > 0:
            normal = -normal
        if normal.z < 0.2:  # walls and undersides are not roofs
            return None
        return {"host": root.name, "point": loc[:], "normal": normal[:]}

    def boxes(self, cand=None):
        cand = cand or {}
        root = bpy.data.objects.get(cand.get("host", self.host))
        return chimney_boxes(root, cand.get("point", self.point), cand.get("normal", self.normal),
                             self.size, self.above_ridge, self.kind, self.depth)

    def execute(self, context):
        root = _clicked_host(self)
        if root is None:
            return {'CANCELLED'}
        from_edit = context.mode == 'EDIT_MESH'
        keep = edit_selection(context) if from_edit else None
        if from_edit:  # B29: the new object needs Object Mode
            bpy.ops.object.mode_set(mode='OBJECT')
        on = root.name
        obj = make_chimney(context, root, self.point, self.normal, self.kind, self.size, self.above_ridge,
                           self.depth)
        if from_edit:
            back_to_edit_facade(context, obj, keep)
        self.report({'INFO'}, f"Chimney '{obj.name}' on '{on}'")
        return {'FINISHED'}


def drag_to_balcony(cand, a, b, kind, width=None, depth=None, railing=None, final=True):
    """B28: ``balcony_add`` arguments for a rectangle dragged from ``a`` to ``b`` on the wall face ``cand``
    (``selected_face_placement``): width = the drag's width, floor = its bottom, railing = its height.
    B73 (Edit Facade tool header): ``width`` is used while the drag is narrower than 0.5 m, ``depth`` always;
    B81: ``railing`` only while the drag is lower than 0.3 m (else the preview's top did not follow the mouse);
    a door / window face fits the opening (its width, floor at its bottom).
    B98: ``final=False`` (the live preview) never uses ``width``: the box follows the mouse from the first
    pixel; it jumped from the header's 3 m down to the drag's width as the drag passed 0.5 m."""
    n = Vector(cand["normal"])
    h = Vector((-n.y, n.x, 0.0))
    plane = Vector(cand["plane"])
    a, b = Vector(a), Vector(b)
    if cand.get("opening"):
        lo, hi = cand["lo"], cand["hi"]
        drawn = hi - lo + (0.2 if kind == 'FRENCH' else 0.8)
        a.z = b.z = cand["zmin"]
    else:
        lo, hi = sorted((max(cand["lo"], min(a.dot(h), b.dot(h))), min(cand["hi"], max(a.dot(h), b.dot(h)))))
        drawn = hi - lo
    floor, rise = min(a.z, b.z), abs(a.z - b.z)
    w = width if final and width is not None and drawn < 0.5 else max(0.5, drawn)
    if not cand.get("opening") and cand["hi"] - cand["lo"] >= w:  # the balcony stays on the face
        mid = min(max((lo + hi) / 2, cand["lo"] + w / 2), cand["hi"] - w / 2)
        lo, hi = mid - w / 2, mid + w / 2
    w, d = balcony_size(0, w)  # B67: depth follows the dragged width
    args = {"host": cand["host"], "normal": n[:], "kind": kind, "width": w,
            "depth": depth if depth is not None else d,
            "railing": rise if rise >= 0.3 else (railing if railing is not None else 1.0)}
    if kind == 'CORNER':  # the wall end nearer the drag
        end = abs(cand["hi"] - (lo + hi) / 2) <= abs((lo + hi) / 2 - cand["lo"])
        along, args["corner_side"] = (cand["hi"], 'END') if end else (cand["lo"], 'START')
    else:
        along = (lo + hi) / 2
    point = plane + h * (along - plane.dot(h))
    point.z = floor
    args["point"] = point[:]
    return args


def drag_to_chimney(cand, a, b, kind, default=None, default_depth=None, final=True):
    """B70: ``chimney_add`` arguments for a rectangle dragged from ``a`` to ``b`` on the roof face ``cand``.
    B80: the chimney is the dragged rectangle (width x, depth y, each at least 0.2 m), centred in it.
    B98: ``final=False`` (the live preview) ignores the header's size, as ``drag_to_balcony``."""
    a, b = Vector(a), Vector(b)
    size, depth = abs(a.x - b.x), abs(a.y - b.y)
    if final and max(size, depth) < 0.2 and default is not None:  # B73: a short drag: the header's size
        size, depth = default, default_depth or default
    return {"host": cand["host"], "point": ((a + b) / 2)[:], "normal": cand["normal"], "kind": kind,
            "size": max(0.2, size), "depth": max(0.2, depth)}


class _Pasted:
    """B71: an add operator's ``candidate_at`` run with a copied installation's values (no operator)."""

    def __init__(self, cls, values):
        self.__dict__.update(values, _cls=cls, why="")

    def __getattr__(self, name):
        attr = getattr(self._cls, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr


def paste_args(context, source, origin, direction):
    """B71: (add operator, its arguments, '') to paste ``source`` (a balcony / chimney made here) where the
    ray meets a wall / roof: the copied type and size at the clicked spot; (op, None, why) when it takes none."""
    balcony = source.get("cgml_semantic_type") == "Balcony"
    cls = CGML_OT_balcony_add if balcony else CGML_OT_chimney_add
    op = bpy.ops.cgml.balcony_add if balcony else bpy.ops.cgml.chimney_add
    params = source["cgml_install_params"].to_dict()
    if not balcony:
        params.setdefault("depth", params["size"])  # B80: a chimney from before B80 is square
    cand = _Pasted(cls, params).candidate_at(context, origin, direction)
    if cand is None:
        return op, None, f"Paste the {source.get('cgml_semantic_type', '').lower()}: {cls.MISS}"
    keep = ("host", "point", "normal", "corner_side")  # where; the size is the copied one
    return op, {**params, **{k: v for k, v in cand.items() if k in keep}}, ""


class _FaceDraw:
    """B28 Drag on Wall; B70 the Edit Facade Balcony / Chimney tools. On the selected face a drag places
    the tool's type (B73: a click places nothing; a door / window face: fitted to it).
    From the tool a press off the selected face falls through to selection, like Window / Door."""
    bl_options = {'INTERNAL'}  # the balcony_add / chimney_add it ends in is the undo step (F9)
    from_tool: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    _handle = None
    _text = None
    _lift = None    # B80: after the drag, the height as New Building does (FootprintDraw's height phase)
    RAISE = False   # B80: the chimney gets that height phase

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    @modal_hint.clears_status
    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D' or context.region_data is None:
            self.report({'ERROR'}, "Only available in the 3D viewport")
            return {'CANCELLED'}
        if self.from_tool:
            from .facade_authoring import _tool_click_on_selected_face
            if not _tool_click_on_selected_face(context, event):
                return {'PASS_THROUGH'}  # a click elsewhere selects a face
        cand, reason = selected_face_placement(context, self.WANT)
        if cand is None or (cand.get("opening") and not self.from_tool):
            self.report({'ERROR'}, reason or "Drag on a wall face (a door or window: click it)")
            return {'CANCELLED'}
        self._cand, self._a, self._b, self._press = cand, None, None, None
        if self.from_tool:  # the tool's press is the first corner
            self._a = self._on_wall(context, event)
            self._press = Vector((event.mouse_region_x, event.mouse_region_y))
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_VIEW')
        self._text = bpy.types.SpaceView3D.draw_handler_add(self._draw_text, (), 'WINDOW', 'POST_PIXEL')
        modal_hint.status(context, modal_hint.hint(self.HINT, ("Esc/RMB", "Cancel"), modal_hint.snap_note(context)))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _on_wall(self, context, event):
        """The mouse on the selected face's plane (snapped), or None."""
        from mathutils.geometry import intersect_line_plane
        origin, direction = ray_under_mouse(context, event, self)
        plane = Vector(self._cand.get("plane", self._cand.get("point")))
        hit = intersect_line_plane(origin, origin + direction, plane, Vector(self._cand["normal"]))
        return None if hit is None else snap_point_to_increment(context, hit, self._cand["normal"])

    def _end(self, context):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        if self._text is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._text, 'WINDOW')
            self._text = None
        self._lift = None
        modal_hint.status(context)
        context.area.tag_redraw()

    def cancel(self, context):
        self._end(context)

    def _shown_args(self):
        """What the preview shows now: the lifted chimney, or the rectangle being dragged (None: nothing yet)."""
        if self._lift is not None:
            return self._args
        return self.drag_args(self._b, final=False) if self._a is not None and self._b is not None else None

    def label(self, args):
        return ""

    def _draw_text(self):
        """POST_PIXEL: the live size while dragging, the height while lifting (as New Building)."""
        args = self._shown_args()
        text = self.label(args) if args else ""
        context = bpy.context
        if not text or context.region_data is None:
            return
        import blf
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        pts = box_edges(self.boxes_for(args))
        p = location_3d_to_region_2d(context.region, context.region_data, max(pts, key=lambda q: q[2]))
        if p is not None:
            blf.size(0, 15)
            blf.color(0, 1.0, 1.0, 1.0, 1.0)
            blf.position(0, p.x + 6, p.y + 6, 0)
            blf.draw(0, text)

    def _draw(self):
        args = self._shown_args()
        if args is None:
            return
        pts = box_edges(self.boxes_for(args))
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        vp = gpu.state.viewport_get()
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        shader.bind()
        shader.uniform_float("viewportSize", (vp[2], vp[3]))
        shader.uniform_float("lineWidth", 2.5)
        shader.uniform_float("color", (0.2, 0.55, 1.0, 1.0))
        batch_for_shader(shader, 'LINES', {"pos": pts}).draw(shader)
        gpu.state.blend_set('NONE')

    def _is_click(self, event):
        from .facade_authoring import DRAG_CLICK_THRESHOLD_PX
        moved = (Vector((event.mouse_region_x, event.mouse_region_y)) - self._press).length
        return moved < DRAG_CLICK_THRESHOLD_PX

    @modal_hint.clears_status
    def modal(self, context, event):
        if self._lift is not None:
            return self._lift_modal(context, event)
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            if self._a is not None:
                self._b = self._on_wall(context, event)
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._a = self._on_wall(context, event)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._a is not None:
            b = self._on_wall(context, event) or self._b
            if self._press is not None and self._is_click(event):
                # B73: a click places nothing (selecting the face kept dropping one); a drag places
                self._end(context)
                self.report({'INFO'}, f"Drag on the face to place the {self.ADD.split('_')[0]}")
                return {'CANCELLED'}
            if b is None:
                self._end(context)
                return {'CANCELLED'}
            if self.RAISE:
                return self._start_lift(context, b)
            self._end(context)
            getattr(bpy.ops.cgml, self.ADD)('EXEC_DEFAULT', **self.drag_args(b))  # back in Edit Facade (B33)
            return {'FINISHED'}
        return {'PASS_THROUGH'}  # orbit, pan, zoom

    def _start_lift(self, context, b):
        """B80: the drag gave the rectangle; now the height, New Building's height phase as is."""
        from ..shared.footprint_draw import FootprintDraw
        self._args = self.drag_args(b)
        self._lift = FootprintDraw()
        self._lift.height = self._args["above_ridge"]
        self._lift._to_height()
        self._lift_status(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _lift_status(self, context):
        s = self._lift
        modal_hint.status(context, modal_hint.hint(f"Height {s.typed or f'{s.height:.1f}'} m above the ridge",
                                                   ("Move/Type", "Height"), ("LMB/Enter", "Done"),
                                                   ("Esc/RMB", "Cancel")))

    def _lift_modal(self, context, event):
        from .building_authoring import _KEYS, _NAVIGATION
        state = self._lift
        if event.type in _NAVIGATION or (event.type == 'MOUSEMOVE' and event.alt):
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type == 'MOUSEMOVE':
            m_per_px = context.region_data.view_distance * 2.0 / max(context.region.height, 1)
            state.move(None, (event.mouse_region_x, event.mouse_region_y), m_per_px=m_per_px)
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            state.press(None, None)
        elif event.value == 'PRESS' and event.type in _KEYS:
            state.key(_KEYS[event.type])
        else:
            return {'RUNNING_MODAL'}
        self._args["above_ridge"] = state.height
        context.area.tag_redraw()
        if state.phase == "DONE":
            args = self._args
            self._end(context)
            getattr(bpy.ops.cgml, self.ADD)('EXEC_DEFAULT', **args)  # back in Edit Facade (B33)
            return {'FINISHED'}
        self._lift_status(context)
        return {'RUNNING_MODAL'}


class CGML_OT_balcony_draw(_FaceDraw, Operator):
    """Drag on the selected wall face to place a balcony: where, floor (bottom), width (a short drag: the header's)"""
    bl_idname = "cgml.balcony_draw"
    bl_label = "Drag on Wall"  # B33
    WANT, ADD = 'WALL', "balcony_add"
    HINT = ("Drag", "Balcony width, floor (bottom), railing (height)")

    kind: EnumProperty(name="Type", items=BALCONY_TYPES, default='PROJECTING')
    # B73: the size is the user's again (B67 had dropped it from the header)
    width: FloatProperty(name="Width", description="Along the wall; a wider drag sets it", default=3.0, min=0.5,
                         unit='LENGTH')
    depth: FloatProperty(name="Depth", description="How far the floor comes out of the wall", default=1.4,
                         min=0.3, unit='LENGTH')
    railing: FloatProperty(name="Railing height",
                           description="Height of the railing above the floor (a flat drag; a taller drag sets it)",
                           default=1.0, min=0.0, unit='LENGTH')

    def type_props(self):
        return {"kind": self.kind, "width": self.width, "depth": self.depth, "railing": self.railing}

    def drag_args(self, b, final=True):
        return drag_to_balcony(self._cand, self._a, b, self.kind, self.width, self.depth, self.railing, final)

    def boxes_for(self, args):
        return balcony_boxes(args["point"], args["normal"], self.kind, args["width"], args["depth"],
                             args["railing"], mirror=args.get("corner_side") == 'START')

    def label(self, args):  # B81: the live size while dragging, as the chimney
        return f"{args['width']:.2f} m wide, {args['depth']:.2f} m deep, railing {args['railing']:.2f} m"


class CGML_OT_chimney_draw(_FaceDraw, Operator):
    """Drag a rectangle on the selected roof face (the chimney's width and depth), then move the mouse or type its height"""
    bl_idname = "cgml.chimney_draw"
    bl_label = "Drag on Roof"
    WANT, ADD = 'ROOF', "chimney_add"
    HINT = ("Drag", "Chimney width and depth (then its height)")
    RAISE = True  # B80

    kind: EnumProperty(name="Type", items=CHIMNEY_TYPES, default='SQUARE')
    size: FloatProperty(name="Width", default=0.6, min=0.2, unit='LENGTH')
    depth: FloatProperty(name="Depth", default=0.6, min=0.2, unit='LENGTH')
    above_ridge: FloatProperty(name="Above ridge", description="Start height of the lift: the top above the ridge",
                               default=0.8, min=0.0, unit='LENGTH')

    def type_props(self):
        return {"kind": self.kind, "size": self.size, "depth": self.depth, "above_ridge": self.above_ridge}

    def drag_args(self, b, final=True):
        return {**drag_to_chimney(self._cand, self._a, b, self.kind, self.size, self.depth, final),
                "above_ridge": self.above_ridge}

    def boxes_for(self, args):
        return chimney_boxes(bpy.data.objects.get(args["host"]), args["point"], args["normal"], args["size"],
                             args["above_ridge"], self.kind, args["depth"])

    def label(self, args):
        if self._lift is not None:
            return f"{args['above_ridge']:.1f} m above the ridge"
        return f"{args['size']:.2f} x {args['depth']:.2f} m"


operator_classes = (CGML_OT_balcony_add, CGML_OT_chimney_add, CGML_OT_balcony_draw, CGML_OT_chimney_draw)
