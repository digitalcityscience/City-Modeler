"""Shared helpers for the authoring-core (CAC) headless tests.

Kept deliberately small (PLAN.md amendment A1): only what the P0 tests
need. ``bpy`` is imported lazily inside the Blender-bound helpers so the
pure helpers (XML normalisation, id collection, golden comparison, result
reporting) can be unit-tested without Blender.

A headless test uses it like this::

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cac_harness as h

    R = h.Results(__file__)
    h.load_addon()
    h.import_fixture(h.FIXTURES / "Building" / "...gml", "3.0", use_semantic_objects=True)
    R.check("railway_v3_ids", *h.compare_golden("import_ids_railway_v3", data))
    R.finish()

Each check id becomes the test id ``<script stem>::<check>`` that the
runner (run_cac_suite.py) and known_arch_bugs.json refer to.
"""
import difflib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HEADLESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HEADLESS_DIR.parents[1]
FIXTURES = REPO_ROOT / "test_files"
GOLDEN_DIR = REPO_ROOT / "scripts" / "tests" / "golden" / "cac"
# The default preserves the legacy probe convention.  The isolated extension
# verification profile sets this to Blender's extension module name.
ADDON = os.environ.get("CAC_ADDON_MODULE", "city_modeler")
GOLDEN_UPDATE_ENV = "CAC_ALLOW_GOLDEN_UPDATE"

# The old P0 probe uses a legacy add-on junction and needs this compatibility
# shim.  An extension module must instead use Blender's declared-wheel site;
# prepending this path there would silently contaminate the verification.
_PYDEPS = HEADLESS_DIR / "pydeps"
if ADDON == "city_modeler" and _PYDEPS.is_dir() and str(_PYDEPS) not in sys.path:
    sys.path.insert(0, str(_PYDEPS))

_TEST_PACKAGE = "city_modeler"


class _ExtensionAlias:
    """Resolve the tests' ``city_modeler.*`` imports to ``ADDON``.

    CAC predates Blender's extension namespace and imports the package by its
    distribution id, often before ``load_addon()``.  Every aliased name must be
    the *same module object* as its ``bl_ext.*`` counterpart: importing the
    package a second time under another name would give tests private copies
    of module state (registry, flags) that the enabled add-on never sees.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname != _TEST_PACKAGE and not fullname.startswith(_TEST_PACKAGE + "."):
            return None
        import importlib.util
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        import importlib
        module = importlib.import_module(ADDON + spec.name[len(_TEST_PACKAGE):])
        self._real_specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module):
        # Already executed under its real bl_ext name.  The import machinery
        # has just overwritten ``__spec__`` with the alias spec; restore it, or
        # the add-on's own relative imports resolve against the wrong parent.
        module.__spec__ = self._real_specs.pop(module.__spec__.name)

    _real_specs = {}


if ADDON != _TEST_PACKAGE and not any(isinstance(f, _ExtensionAlias) for f in sys.meta_path):
    sys.meta_path.insert(0, _ExtensionAlias())


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def script_args():
    """Arguments after ``--`` on the Blender command line."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def out_dir():
    """First positional script argument; created if missing."""
    positional = [a for a in script_args() if not a.startswith("--")]
    path = Path(positional[0]) if positional else Path.cwd() / "cac-out"
    path.mkdir(parents=True, exist_ok=True)
    return path


def update_goldens_requested():
    """True when ``--update-goldens`` was passed; refuses without the env switch (R8)."""
    if "--update-goldens" not in script_args():
        return False
    if os.environ.get(GOLDEN_UPDATE_ENV) != "1":
        raise SystemExit(
            f"--update-goldens refused: set {GOLDEN_UPDATE_ENV}=1 to rewrite goldens"
        )
    return True


# --------------------------------------------------------------------------
# Blender-bound helpers
# --------------------------------------------------------------------------

def load_addon():
    """Empty factory scene with the add-on enabled."""
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)


def addon_environment():
    """Resolved runtime facts for an actual CAC child Blender process."""
    import importlib
    import bpy
    import pyproj

    addon = importlib.import_module(ADDON)
    probe = ".shared.authoring_core.blender.registry"
    stamp = Path(addon.__file__).resolve().parent / "cac_source_stamp.json"
    return {
        "addon_module": ADDON,
        "addon_source": str(Path(addon.__file__).resolve()),
        # utf-8-sig: setup_cac_verification.ps1 writes this file with
        # PowerShell's `-Encoding utf8`, which emits a UTF-8 BOM on Windows
        # PowerShell; utf-8-sig strips it transparently and still reads a
        # BOM-less file identically, so this is not scoped to CAC-P3b.1.
        "addon_source_stamp": json.loads(stamp.read_text(encoding="utf-8-sig")) if stamp.is_file() else None,
        "test_alias_is_same_module": (
            importlib.import_module(_TEST_PACKAGE + probe) is importlib.import_module(ADDON + probe)
            and importlib.import_module(_TEST_PACKAGE) is addon
        ),
        # Aliasing must not rewrite the real modules' import identity.
        "alias_preserves_spec": (
            addon.__spec__.name == ADDON
            and importlib.import_module(ADDON + probe).__spec__.name == ADDON + probe
        ),
        "blender_version": bpy.app.version_string,
        "python_version": sys.version,
        "pyproj_version": pyproj.__version__,
        "pyproj_crs_probe": pyproj.CRS.from_epsg(25832).name,
        "pyproj_source": str(Path(pyproj.__file__).resolve()),
        "extension_sites": [p for p in sys.path if "extensions" in p.lower()],
    }


def import_fixture(path, version, use_semantic_objects):
    """Import ``path`` into a fresh scene; ``version`` is checked against the file."""
    import bpy

    detected = detect_citygml_version(Path(path).read_text(encoding="utf-8", errors="replace"))
    if detected != version:
        raise AssertionError(f"{path}: expected CityGML {version}, file is {detected}")
    load_addon()
    bpy.context.scene.cgml3.use_semantic_objects = bool(use_semantic_objects)
    res = bpy.ops.cgml3.import_gml_file(filepath=str(path))
    if res != {"FINISHED"}:
        raise AssertionError(f"import failed for {path}: {res}")


def export_scene(path, version):
    """Export the current scene to ``path`` as CityGML ``version``; returns the text."""
    import bpy

    res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    if res != {"FINISHED"}:
        raise AssertionError(f"export failed for {path}: {res}")
    return Path(path).read_text(encoding="utf-8")


def _plain(value):
    """ID-property value -> JSON-safe plain value."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_list"):
        return value.to_list()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def snapshot_objects(object_keys=(), material_keys=()):
    """Deterministic snapshot of scene objects and their materials.

    Only the listed custom-property keys are recorded (missing keys are
    omitted, so "absent" and "present" stay distinguishable).
    """
    import bpy

    def props(idblock, keys):
        return {k: _plain(idblock[k]) for k in keys if k in idblock}

    objects = []
    for obj in sorted(bpy.data.objects, key=lambda o: o.name):
        entry = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else None,
            "props": props(obj, object_keys),
        }
        if obj.type == "MESH":
            entry["materials"] = [
                {"name": m.name, "props": props(m, material_keys)} if m else None
                for m in obj.data.materials
            ]
        objects.append(entry)
    return {"objects": objects}


def is_clean_face(obj, face):
    """A convex face of a wall without openings: no cut-out, notch or opening
    ring, so a rectangle centred on it lands on solid wall. Placement checks the
    real host boundary (AB-17); the Railway v3 wall found first by name has its
    arched door cut-out at the centre, which the facade tests used to hit."""
    if obj.children:
        return False
    vs = [obj.data.vertices[i].co for i in face.vertices]
    turns = [(vs[i] - vs[i - 1]).cross(vs[(i + 1) % len(vs)] - vs[i]).dot(face.normal) for i in range(len(vs))]
    return all(t >= -1e-9 for t in turns) or all(t <= 1e-9 for t in turns)


def first_wall_with_exterior_face():
    """First WallSurface object (name order) and one of its exterior faces."""
    import bpy

    for obj in bpy.data.objects:  # name order: deterministic
        if str(obj.get("cgml3_feature") or "") != "WallSurface" or obj.type != "MESH":
            continue
        for face in obj.data.polygons:
            mats = obj.data.materials
            mat = mats[face.material_index] if 0 <= face.material_index < len(mats) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id"):
                return obj, face.index
    raise AssertionError("fixture has no WallSurface with an exterior face")


def surface_context(wall, face_index):
    """SurfaceContext with a min-corner frame on the host face (as in test_facade01)."""
    from city_modeler.shared.facade_placement import SurfaceContext

    mesh = wall.data
    poly = mesh.polygons[face_index]
    mw = wall.matrix_world
    wps = [mw @ mesh.vertices[vi].co.copy() for vi in poly.vertices]
    n = (mw.to_3x3() @ poly.normal).normalized()
    v0 = wps[0]
    axis_u = (wps[1] - v0).normalized()
    axis_v = n.cross(axis_u).normalized()
    us = [(wp - v0).dot(axis_u) for wp in wps]
    vs = [(wp - v0).dot(axis_v) for wp in wps]
    building = wall
    while building is not None and str(building.get("cgml3_feature") or "") not in ("Building", "BuildingPart"):
        building = building.parent
    return SurfaceContext(
        object_name=wall.name,
        semantic_id=str(wall.get("gml_id") or ""),
        semantic_type="WallSurface",
        building_id=str(building.get("gml_id")) if building else "",
        face_indices=[face_index],
        origin_world=v0 + axis_u * min(us) + axis_v * min(vs),
        normal_world=n,
        horizontal_axis_world=axis_u,
        vertical_axis_world=axis_v,
        width=max(us) - min(us),
        height=max(vs) - min(vs),
    )


def solid_area_position_uv(wall, face_index, ctx, half_w, half_h, step=0.05, avoid=()):
    """A ``(u, v)`` fraction on ``ctx`` where a ``2*half_w x 2*half_h``
    (metres) rectangle lands fully inside the host face's real, possibly
    concave, solid area - clear of any cut-out - and clear of every
    already-placed rectangle in ``avoid`` (each ``(u_metres, v_metres,
    half_w, half_h)``, e.g. built from a previous call's result via
    ``(u_frac * ctx.width, v_frac * ctx.height, half_w, half_h)``).

    Several fixed uv positions used by earlier CAC tests on the Railway v3
    fixture's ``WallSurface_UUID_0d935dab.../face 0`` (the arched ground
    cut-out) turned out to sit outside the actual solid polygon once AB-17's
    fix started validating placement against the real host boundary
    (see ``test_cac_p3_draw_placement.py``). This factors out that fixture's
    grid search so any CAC test needing "a Window that will actually place
    on this face" (or a second, non-overlapping one) gets one without
    hand-picking magic numbers.
    """
    from mathutils import Vector

    mesh = wall.data
    mw = wall.matrix_world
    poly = mesh.polygons[face_index]
    origin = Vector(ctx.origin_world)
    h_axis = Vector(ctx.horizontal_axis_world).normalized()
    v_axis = Vector(ctx.vertical_axis_world).normalized()
    ext_poly = []
    for vi in poly.vertices:
        wp = mw @ mesh.vertices[vi].co
        rel = wp - origin
        ext_poly.append((rel.dot(h_axis), rel.dot(v_axis)))

    def point_in_polygon(pt):
        x, y = pt
        inside = False
        n = len(ext_poly)
        for i in range(n):
            x1, y1 = ext_poly[i]
            x2, y2 = ext_poly[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                x_at_y = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if x < x_at_y:
                    inside = not inside
        return inside

    def overlaps_avoided(u, v):
        # A margin, not a bare touching-edge test: two rectangles that only
        # share a boundary still trip _classify_rect_vs_polygon's edge-
        # intersection test in shared/facade_placement.py (it uses a small
        # but non-zero tolerance), so leave a real gap.
        margin = max(step, 0.02)
        for ou, ov, ohw, ohh in avoid:
            if not (u + half_w + margin <= ou - ohw or ou + ohw + margin <= u - half_w
                    or v + half_h + margin <= ov - ohh or ov + ohh + margin <= v - half_h):
                return True
        return False

    def rect_inside(u, v):
        corners = [(u + half_w, v + half_h), (u - half_w, v + half_h),
                   (u - half_w, v - half_h), (u + half_w, v - half_h)]
        return all(point_in_polygon(c) for c in corners) and not overlaps_avoided(u, v)

    us = [p[0] for p in ext_poly]
    vs = [p[1] for p in ext_poly]
    min_u, max_u, min_v, max_v = min(us), max(us), min(vs), max(vs)
    u = min_u + half_w
    while u <= max_u - half_w + 1e-9:
        v = min_v + half_h
        while v <= max_v - half_h + 1e-9:
            if rect_inside(u, v):
                return (u / ctx.width, v / ctx.height)
            v += step
        u += step
    raise AssertionError("no fully-solid location found in the host face bbox")


def thickness(obj, normal_world):
    """Extent of ``obj``'s vertices along ``normal_world`` (0 = flat)."""
    from mathutils import Vector

    n = Vector(normal_world).normalized()
    signed = [(obj.matrix_world @ v.co).dot(n) for v in obj.data.vertices]
    return max(signed) - min(signed) if signed else 0.0


def face_normal_world(obj, face_index=0):
    return (obj.matrix_world.to_3x3() @ obj.data.polygons[face_index].normal).normalized()


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

_NS_VERSION = {
    "http://www.opengis.net/citygml/2.0": "2.0",
    "http://www.opengis.net/citygml/3.0": "3.0",
}


def detect_citygml_version(text):
    for ns, version in _NS_VERSION.items():
        if ns in text:
            return version
    return None


# Minted ids: <Prefix>_<uuid4 hex> or <Prefix>_<uuid4 with dashes>, optionally
# followed by a numeric suffix such as "_0_" (io/writer/exporter.py), and the
# opening ids UUID_Window_/UUID_Door_<12 hex> (Openings_Cutter.py).
_FRESH_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"([A-Za-z]+)_(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"|(UUID_(?:Window|Door))_[0-9a-f]{12}"
    r")(?![0-9a-f])"
)
_CREATION_DATE = re.compile(r"(<(?:[\w-]+:)?creationDate>)[^<]*(</(?:[\w-]+:)?creationDate>)")
# Pre-existing exporter bug (AB candidate, see STATUS.md): CityGML 3 export can
# write the repr of an ID property group, including its memory address, as a
# generic attribute value. Only the address is masked; the repr stays visible.
_BPY_ADDRESS = re.compile(r"address=0x[0-9A-Fa-f]+")


def normalise_xml(text, preserve_ids=()):
    """Replace export-time noise so two exports of the same scene compare equal.

    Freshly minted uuid ids are renumbered ``<Prefix>_FRESH<n>`` in order of
    first appearance, so references (``#id``, ``xlink:href``) stay consistent.
    Ids in ``preserve_ids`` (for example the source file's ids) are never
    touched, because they are exactly what the tests must check.
    """
    preserve = set(preserve_ids)
    mapping = {}

    def repl(match):
        token = match.group(0)
        if token in preserve:
            return token
        if token not in mapping:
            mapping[token] = f"{match.group(1) or match.group(2)}_FRESH{len(mapping)}"
        return mapping[token]

    text = _FRESH_ID.sub(repl, text)
    text = _BPY_ADDRESS.sub("address=0xX", text)
    return _CREATION_DATE.sub(r"\1X\2", text)


def normalise_json(data, preserve_ids=()):
    """``normalise_xml`` applied to a JSON-able value (e.g. a scene snapshot).

    Import also mints uuid ids (for example the polygon ids of synthetic
    opening prism faces), so snapshots need the same renumbering.
    """
    return json.loads(normalise_xml(_dump(data), preserve_ids))


def local_name(tag):
    tag = str(tag)
    return tag.rsplit("}", 1)[1] if tag.startswith("{") else tag


# GML 3.1 (CityGML 2) and GML 3.2 (CityGML 3)
GML_IDS = ("{http://www.opengis.net/gml}id", "{http://www.opengis.net/gml/3.2}id")


def collect_ids(xml_text):
    """``{local element name: sorted gml:ids}`` for every element carrying a gml:id."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    found = {}
    for elem in root.iter():
        gid = next((elem.get(a) for a in GML_IDS if elem.get(a) is not None), None)
        if gid is not None:
            found.setdefault(local_name(elem.tag), []).append(gid)
    return {name: sorted(ids) for name, ids in sorted(found.items())}


BOUNDARY_SURFACES = frozenset({
    "WallSurface", "RoofSurface", "GroundSurface", "ClosureSurface",
    "OuterCeilingSurface", "OuterFloorSurface", "InteriorWallSurface",
    "CeilingSurface", "FloorSurface",
})
OPENINGS = frozenset({"Window", "Door", "WindowSurface", "DoorSurface"})
# Elements whose ids are geometry or appearance plumbing, not features.
NON_FEATURE_ELEMENTS = frozenset({
    "Polygon", "LinearRing", "MultiSurface", "CompositeSurface", "Solid", "Shell",
    "TriangulatedSurface", "Triangle", "OrientableSurface", "Surface",
    "Appearance", "X3DMaterial", "ParameterizedTexture", "GeoreferencedTexture",
    "TexCoordList", "TextureCoordinates",
})


def export_fingerprint(xml_text, preserve_ids=()):
    """Structural fingerprint of an exported CityGML document (P0.4).

    Computed on the normalised text, so minted ids appear as ``*_FRESH<n>``.
    """
    root = ET.fromstring(normalise_xml(xml_text, preserve_ids).encode("utf-8"))
    parent = {child: elem for elem in root.iter() for child in elem}

    def gid(elem):
        return next((elem.get(a) for a in GML_IDS if elem.get(a) is not None), None)

    counts, features, polygons, interiors, nesting = {}, [], [], {}, []
    for elem in root.iter():
        name = local_name(elem.tag)
        counts[name] = counts.get(name, 0) + 1
        ident = gid(elem)
        if name == "Polygon":
            polygons.append(ident)
            n = sum(1 for child in elem if local_name(child.tag) == "interior")
            if n:
                interiors[ident] = n
        elif ident is not None and name not in NON_FEATURE_ELEMENTS:
            features.append(f"{name}:{ident}")
        if name in OPENINGS:
            up = parent.get(elem)
            while up is not None and local_name(up.tag) not in BOUNDARY_SURFACES:
                up = parent.get(up)
            nesting.append([gid(up) if up is not None else None, ident])
    return {
        "element_counts": dict(sorted(counts.items())),
        "feature_ids": sorted(features),
        "polygon_ids": sorted(p or "" for p in polygons),
        "interior_rings": dict(sorted(interiors.items())),
        "opening_nesting": sorted(nesting, key=lambda p: (p[0] or "", p[1] or "")),
        "coordinates": coordinate_digests(root),
    }


COORD_DECIMALS = 4  # 0.1 mm


def ring_coordinates(ring):
    """Coordinates of one gml:LinearRing (``posList`` or a sequence of ``pos``)."""
    values = []
    for elem in ring.iter():
        name = local_name(elem.tag)
        if name in ("posList", "pos", "coordinates") and elem.text:
            values.extend(float(v) for v in elem.text.replace(",", " ").split())
    dim = int(next((e.get("srsDimension") for e in ring.iter() if e.get("srsDimension")), 3))
    return [tuple(values[i:i + dim]) for i in range(0, len(values) - dim + 1, dim)]


def polygon_digest(polygon):
    """Short hash of a polygon's rings: exterior first, then interiors in document order."""
    import hashlib

    parts = []
    for child in polygon:
        role = local_name(child.tag)
        if role not in ("exterior", "interior"):
            continue
        pts = [" ".join(f"{round(c, COORD_DECIMALS) + 0.0:.{COORD_DECIMALS}f}" for c in p)
               for ring in child for p in ring_coordinates(ring)]
        parts.append(role + ":" + ";".join(pts))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def coordinate_digests(root):
    """``{polygon id: digest}`` for every gml:Polygon, so a moved vertex shows as a diff.

    Polygons without an id are keyed ``<no-id>#<n>``; a repeated id gets ``#<n>``.
    """
    digests = {}
    for n, poly in enumerate(e for e in root.iter() if local_name(e.tag) == "Polygon"):
        key = next((poly.get(a) for a in GML_IDS if poly.get(a) is not None), None) or f"<no-id>#{n}"
        while key in digests:
            key = f"{key}#{n}"
        digests[key] = polygon_digest(poly)
    return dict(sorted(digests.items()))


def to_export_coords(points):
    """Blender (record) coordinates as the exporter writes them: plus the import origin that
    World "X/Y/Z-Origin" holds (same CRS, no reprojection)."""
    import bpy

    w = bpy.data.worlds.get("World")
    o = [float(w.get(k, 0.0)) if w else 0.0 for k in ("cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin")]
    return [tuple(c + o[i] for i, c in enumerate(p)) for p in points]


def same_ring(expected, got, tol=1e-3):
    """Every point of each ring has a point of the other within ``tol`` (closing points may differ)."""
    def covered(xs, ys):
        return all(any(max(abs(a - b) for a, b in zip(p, q)) <= tol for q in ys) for p in xs)
    return bool(expected) and covered(expected, got) and covered(got, expected)


def export_structure(path):
    """Raw-XML view of an export: ``openings`` as ``(gml:id, host surface gml:id)``
    and, per surface polygon (openings' own polygons excluded), its host and
    rounded exterior/interior rings.  Unlike ``export_fingerprint`` it keeps
    real ids and coordinates, so ring ownership can be compared."""
    root = ET.parse(path).getroot()
    parent = {child: elem for elem in root.iter() for child in elem}

    def gid(elem):
        return next((elem.get(a) for a in GML_IDS if elem.get(a) is not None), None)

    def enclosing(elem, names):
        up = parent.get(elem)
        while up is not None and local_name(up.tag) not in names:
            up = parent.get(up)
        return up

    def ring(elem):
        return tuple(tuple(round(c, 4) + 0.0 for c in p) for r in elem for p in ring_coordinates(r))

    openings = []
    for elem in root.iter():
        if local_name(elem.tag) in OPENINGS and gid(elem) is not None:
            host = enclosing(elem, BOUNDARY_SURFACES)
            openings.append((gid(elem), gid(host) if host is not None else None))
    polygons = {}
    # The openings' own geometry is compared separately; numbering id-less
    # polygons after that filter keeps keys stable when an opening is added.
    surface_polygons = [e for e in root.iter()
                        if local_name(e.tag) == "Polygon" and enclosing(e, OPENINGS) is None]
    for n, poly in enumerate(surface_polygons):
        host = enclosing(poly, BOUNDARY_SURFACES)
        key = gid(poly) or f"<no-id>#{n}"
        polygons[key] = {
            "host": gid(host) if host is not None else None,
            "exterior": next((ring(c) for c in poly if local_name(c.tag) == "exterior"), ()),
            "interiors": [ring(c) for c in poly if local_name(c.tag) == "interior"],
        }
    return {"openings": openings, "polygons": polygons}

def schema_cache_catalog():
    """Catalog of the local mirror of the http schemas (cac_schema_cache.py; built on first use
    in $CAC_SCHEMA_CACHE or <tmp>/cac-schema-cache): put it first in XML_CATALOG_FILES."""
    import tempfile

    cache = Path(os.environ.get("CAC_SCHEMA_CACHE") or Path(tempfile.gettempdir()) / "cac-schema-cache")
    cache_catalog = cache / "catalog.xml"
    if not cache_catalog.is_file():
        sys.path.insert(0, str(HEADLESS_DIR))
        import cac_schema_cache
        cac_schema_cache.mirror(cache)
    return cache_catalog


def xmllint(path, version):
    """Real XSD validation of ``path`` (CityGML ``version``): ``{"exit": code, "errors": n}``.

    Uses the committed wrapper + catalog in xmllint_v2/xmllint_v3 and a local
    mirror of the http schemas (cac_schema_cache.py; built on first use in
    $CAC_SCHEMA_CACHE or <tmp>/cac-schema-cache), because libxml2 >= 2.14
    cannot fetch http imports itself.
    """
    import shutil
    import subprocess

    exe = shutil.which("xmllint")
    if not exe:
        raise AssertionError("xmllint not found on PATH")
    cache_catalog = schema_cache_catalog()
    major = version.split(".")[0]
    harness = HEADLESS_DIR / f"xmllint_v{major}"
    env = dict(os.environ, XML_CATALOG_FILES=f"{cache_catalog} {harness / 'catalog.xml'}")
    proc = subprocess.run(
        [exe, "--noout", "--schema", str(harness / f"wrapper_v{major}.xsd"), str(path)],
        env=env, capture_output=True, text=True,
    )
    errors = [line for line in proc.stderr.splitlines() if "Schemas validity error" in line]
    if proc.returncode not in (0, 3):  # 3 = document invalid; anything else = harness problem
        raise AssertionError(f"xmllint could not validate {path} (exit {proc.returncode}):\n"
                             + "\n".join(proc.stderr.splitlines()[-5:]))
    return {"exit": proc.returncode, "errors": len(errors)}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(data), encoding="utf-8", newline="\n")


def _dump(data):
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def compare_json(expected, actual, context_lines=3, max_lines=80):
    """Return ``(equal, readable unified diff)`` of two JSON-able values."""
    a, b = _dump(expected), _dump(actual)
    if a == b:
        return True, ""
    diff = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(), "golden", "actual", n=context_lines, lineterm=""
    ))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"... ({len(diff) - max_lines} more diff lines)"]
    return False, "\n".join(diff)


def compare_golden(name, actual, update=None):
    """Compare ``actual`` with ``golden/cac/<name>.json``.

    With ``--update-goldens`` (and CAC_ALLOW_GOLDEN_UPDATE=1) the golden is
    rewritten instead and the check passes. A missing golden is a failure.
    """
    path = GOLDEN_DIR / f"{name}.json"
    if update is None:
        update = update_goldens_requested()
    if update:
        write_json(path, actual)
        return True, f"golden written: {path.name}"
    if not path.is_file():
        return False, f"golden missing: {path} (run with --update-goldens)"
    return compare_json(json.loads(path.read_text(encoding="utf-8")), actual)


# --------------------------------------------------------------------------
# Result reporting (read by run_cac_suite.py)
# --------------------------------------------------------------------------

class Results:
    """Collects named checks and writes ``<out_dir>/<stem>.result.json``."""

    def __init__(self, script_file, directory=None):
        self.stem = Path(script_file).stem
        self.directory = Path(directory) if directory else None
        self.checks = {}

    def test_id(self, check):
        return f"{self.stem}::{check}"

    def check(self, check, ok, detail=""):
        test_id = self.test_id(check)
        if test_id in self.checks:
            raise ValueError(f"duplicate check id {test_id}")
        self.checks[test_id] = {"ok": bool(ok), "detail": str(detail)}
        print(f"[{self.stem}] {'OK  ' if ok else 'FAIL'} {check}" + (f"\n{detail}" if detail and not ok else ""))
        return bool(ok)

    def finish(self):
        """Write the result file and exit 0 only if every check passed."""
        directory = self.directory or out_dir()
        write_json(directory / f"{self.stem}.result.json", {"script": self.stem, "checks": self.checks})
        failed = [t for t, c in self.checks.items() if not c["ok"]]
        print(f"[{self.stem}] {len(self.checks) - len(failed)}/{len(self.checks)} checks passed")
        sys.stdout.flush()
        raise SystemExit(1 if failed or not self.checks else 0)
