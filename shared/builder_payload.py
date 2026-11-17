# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
IR -> scene payload for the shared Blender builder (T11d, part 1).

``common/unified_builder.py`` builds the scene from what this module returns;
nothing here touches ``bpy`` or an XML library, so the worker side of a later
``FeaturePayload`` / ``build_feature`` split (T04) can run it off the main
thread, and unit tests can run it without Blender.

Per city object the payload holds

- the arrays today's importers hand to ``apply_materials_uvs`` (``faces``,
  ``verts``, ``surf_labels`` and the per-polygon dicts), built from the IR
  polygons with the same welding, ring and label rules as
  ``io_v2/reader/geometry.py::build_geometry``;
- the custom properties the importer writes, with values mapped from the
  IR's lists (``IMPORTER_VALUE_RULES``).

Where the IR is more exact than the importer (the importer looks values up
with ``.//``, so a Building without its own ``bldg:function`` inherits one
from a nested part), the IR value wins; ``DIFFERENCES`` lists these.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from .citygml_ir import SpaceNode
    from .semantic_types import resolve_semantic_type
    from .xal_parser import parse_xal_address
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.citygml_ir import SpaceNode
    from shared.semantic_types import resolve_semantic_type
    from shared.xal_parser import parse_xal_address


VERT_WELD_ND = 5   # build_geometry: 1e-5 CRS units

# Feature types the builder turns into objects. Everything else makes
# ``unsupported_reasons`` non-empty (the add-on refuses the file or skips the content).
BUILDING_TYPES = ("Building", "BuildingPart")
INSTALLATION_TYPES = ("BuildingInstallation", "IntBuildingInstallation")
BRIDGE_TYPES = ("Bridge", "BridgePart")
# Transportation: the four top-level features and the parts they own (2.0 TrafficArea; 3.0 Section,
# Intersection, TrafficSpace, TrafficArea). Marking, Hole and ClearanceSpace are not built.
TRANSPORT_TOP_TYPES = ("Road", "Railway", "Track", "Square")
TRANSPORT_TYPES = TRANSPORT_TOP_TYPES + ("Section", "Intersection", "TrafficSpace", "AuxiliaryTrafficSpace",
                                         "TrafficArea", "AuxiliaryTrafficArea")
FURNITURE_TYPES = ("CityFurniture",)
SUPPORTED_SPACES = (BUILDING_TYPES + INSTALLATION_TYPES + BRIDGE_TYPES + TRANSPORT_TYPES
                    + FURNITURE_TYPES)
OPENING_TYPES = ("Window", "Door", "WindowSurface", "DoorSurface")
# Features whose polygons lack a surface get Roof/Wall/Ground from geometry.
INFER_SURFACE_FOR = ("Building", "BuildingPart", "Bridge", "BridgePart", "Tunnel", "TunnelPart")

# What the IR holds more exactly than the importer (see module docstring).
DIFFERENCES = (
    "values are looked up on the feature itself; the importer's './/' lookup can take a nested part's value",
    "a polygon without gml:id is named <feature>_poly_<n>, the importer uses a hash of its XML",
    "the srsName used for surf_labels is the document default, not the one on the geometry element",
)


@dataclass
class FeatureGeometry:
    """The tuple ``build_geometry(..., include_surface_attrs=True)`` returns."""

    faces: List[List[int]] = field(default_factory=list)
    verts: List[Tuple[float, float, float]] = field(default_factory=list)
    surf_labels: List[tuple] = field(default_factory=list)
    surf_attrs_by_poly: Dict[str, dict] = field(default_factory=dict)
    surface_id_by_poly: Dict[str, str] = field(default_factory=dict)
    multisurface_id_by_poly: Dict[str, str] = field(default_factory=dict)
    compositesurface_id_by_poly: Dict[str, str] = field(default_factory=dict)
    # CityGML 3 importer only
    filling_parent_surface_id_by_poly: Dict[str, str] = field(default_factory=dict)


@dataclass
class ImplicitPlacement:
    """A feature whose whole geometry is one ``ImplicitGeometry``: the template stays in its own
    coordinates (``FeatureGeometry``), the object carries ``matrix`` (transformationMatrix and
    referencePoint, minus the scene origin) as its world matrix."""

    template_id: str
    fingerprint: str      # same polygons -> same fingerprint -> one shared mesh
    matrix: List[List[float]]
    matrix_text: str      # row-major, translation cleared (the referencePoint carries it on export)
    lod: Optional[int] = None


@dataclass
class FeaturePayload:
    gml_id: str
    feature_type: str
    parent_id: str = ""
    generated_id: bool = False
    display_name: str = ""
    geometry: FeatureGeometry = field(default_factory=FeatureGeometry)
    properties: Dict[str, Any] = field(default_factory=dict)
    # BuildingInstallation / IntBuildingInstallation only
    installation_type: Optional[str] = None
    installation_origin: str = ""
    installation_unknown_subtype: bool = False
    installation_unknown_value: str = ""
    source_role: str = ""
    source_element: str = ""
    # CityGML 3 importer: the same attributes once more as dict + JSON
    generic_attributes: Dict[str, Any] = field(default_factory=dict)
    implicit: Optional[ImplicitPlacement] = None
    node: Any = None


@dataclass
class DocumentPlan:
    features: List[FeaturePayload] = field(default_factory=list)
    # city objects the document holds before the import filters
    total: int = 0
    # (parent gml:id, child gml:id, kind) for ids the file itself defines
    edges: List[Tuple[str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# import filters (gml:id, feature type, bbox: which objects; LoD: which geometry)
# ---------------------------------------------------------------------------

# UI flag -> feature types, per importer (io/reader/importer.py, io_v2/reader/importer.py).
# The transportation flag also lists the parts a Road owns: the old importers' filter drops them
# (and, for 2.0, never builds the Road itself once it has TrafficAreas), the builder keeps the whole road.
# The tables are therefore NOT copies of the old importers' (which also list Storey/BuildingRoom for 3.0
# buildings and have no Section/TrafficArea); do not "sync" them. test_t11d_cleanup pins the flag names.
_TYPE_FLAGS = {
    "3.0": (
        ("import_buildings", ("Building", "BuildingPart", "BuildingInstallation")),
        ("import_bridges", ("Bridge", "BridgePart")),
        ("import_tunnels", ("Tunnel", "TunnelPart")),
        ("import_vegetation", ("SolitaryVegetationObject", "PlantCover")),
        ("import_water", ("WaterBody", "WaterSurface")),
        ("import_transportation", ("Road", "Railway", "Track", "Square", "Intersection", "Section",
                                   "TrafficSpace", "AuxiliaryTrafficSpace", "TrafficArea",
                                   "AuxiliaryTrafficArea")),
        ("import_cityfurniture", ("CityFurniture",)),
        ("import_landuse", ("LandUse",)),
        ("import_relief", ("ReliefFeature", "TINRelief", "MassPointRelief", "BreaklineRelief", "RasterRelief")),
        ("import_generics", ("GenericOccupiedSpace", "GenericLogicalSpace", "GenericUnoccupiedSpace",
                             "GenericThematicSurface")),
    ),
    "2.0": (
        ("import_buildings", ("Building", "BuildingPart", "BuildingInstallation", "IntBuildingInstallation")),
        ("import_bridges", ("Bridge", "BridgePart")),
        ("import_tunnels", ("Tunnel", "TunnelPart")),
        ("import_vegetation", ("SolitaryVegetationObject", "PlantCover")),
        ("import_water", ("WaterBody", "WaterSurface")),
        ("import_transportation", ("Road", "Railway", "Track", "Square", "TrafficArea",
                                   "AuxiliaryTrafficArea")),
        ("import_cityfurniture", ("CityFurniture",)),
        ("import_landuse", ("LandUse",)),
        ("import_relief", ("TINRelief", "MassPointRelief", "BreaklineRelief", "RasterRelief")),
        ("import_generics", ("GenericCityObject",)),
    ),
}


def feature_type_set(options, schema: str) -> Optional[set]:
    """The importer's feature type filter from the scene options (None: off).
    An empty set filters nothing, as in the importer."""
    if not getattr(options, "use_feature_type_filter", False):
        return None
    out: set = set()
    for flag, types in _TYPE_FLAGS[schema]:
        if getattr(options, flag, False):
            out.update(types)
    return out


def lod_set(options) -> Optional[set]:
    """The importer's LoD filter (None: off). An empty set excludes every geometry."""
    if not getattr(options, "use_lod_filter", False):
        return None
    return {n for n in range(5) if getattr(options, f"lod_{n}", False)}


@dataclass
class ImportFilters:
    """What the importers' ``iter_features`` / ``build_geometry`` filters do.

    ``gml_ids`` / ``feature_types`` empty or None: no filter. A feature without
    its own gml:id, or without a readable envelope, passes the id / bbox filter.
    ``lods``: the LoD filter; the polygons of unselected LoDs are dropped."""

    gml_ids: Optional[set] = None
    feature_types: Optional[set] = None
    lods: Optional[set] = None
    bbox: Optional[tuple] = None

    @property
    def active(self) -> bool:
        return bool(self.gml_ids or self.feature_types or self.bbox) or self.lods is not None

    def passes(self, node) -> bool:
        """Object-level filters (id, type, bbox); the LoD filter acts on geometry."""
        if self.gml_ids and not node.extra.get("generated_id") and node.gml_id not in self.gml_ids:
            return False
        if self.bbox and not self._in_bbox(node):
            return False
        return not (self.feature_types and node.feature_type not in self.feature_types)

    def _in_bbox(self, node) -> bool:
        envelope = node.extra.get("envelope")
        if not envelope:
            return True
        xmin, ymin, xmax, ymax = self.bbox
        try:
            lower, upper = envelope["lower"], envelope["upper"]
            return lower[0] <= xmax and upper[0] >= xmin and lower[1] <= ymax and upper[1] >= ymin
        except (IndexError, KeyError, TypeError):
            return True


# ---------------------------------------------------------------------------
# what the builder can do
# ---------------------------------------------------------------------------

def _children(node):
    return list(getattr(node, "spaces", ())) + list(getattr(node, "constructive_elements", ()))


def skipped_features(roots, schema: str = "2.0", filters: Optional[ImportFilters] = None) -> Tuple[set, List[str]]:
    """What the builder does not build: ``(skip, reasons)``. ``skip`` holds ``id()`` of every feature
    (with everything below it) that is left out; ``plan_document`` honours it. ``reasons`` names why, in
    the order met (empty: everything is built). With ``filters``, objects they leave out need no support
    (their children are still checked)."""

    def kept(node):
        return filters is None or filters.passes(node)

    skip: set = set()
    reasons: List[str] = []

    def add(msg):
        if msg not in reasons:
            reasons.append(msg)

    def visit(node, top=True, parent_type=""):
        def flag(msg):
            add(msg)
            skip.add(id(node))

        if not isinstance(node, SpaceNode):
            flag(f"{type(node).__name__} as a city object")
            return
        ftype = node.feature_type
        if top and not kept(node):
            # a filtered-out city object needs no support; what it owns is checked on its own
            # (below a kept feature the old importer folds filtered children into it)
            for child in _children(node):
                visit(child, False, ftype)
            return
        if ftype not in SUPPORTED_SPACES:
            flag(f"feature type {ftype}")
            return
        if node.extra.get("feature_hrefs"):
            flag("feature-level xlink")
        if node.extra.get("unmapped_geometries"):
            flag("geometry the IR does not map")
        raws = [raw for target in (node, *node.boundaries) for raw in target.extra.get("unknown_elements", ())]
        if any(_has_geometry(raw) for raw in raws):
            flag("geometry inside an element the IR does not map")
        if any("PointCloud" in raw for raw in raws):
            add("PointCloud")       # the feature itself is built, without the points
        for kind, geom in _all_geometries(node):
            if geom.gml_type == "ImplicitGeometry":
                if not any(geom is own for own in node.geometries.values()) or not _implicit_only(node, polygons=False):
                    flag("ImplicitGeometry next to other geometry")
                elif not geom.polygons or geom.curves:
                    flag("ImplicitGeometry without polygons")
            if geom.href or geom.extra.get("unresolved_hrefs"):
                # what resolved is built, as the old importer did (our own 2.0 export writes hrefs whose
                # polygons it also writes inline); the user is told, the feature is not skipped
                add("unresolved geometry xlink")
        if ftype in BRIDGE_TYPES and node.constructive_elements:
            flag("bridge construction elements")
        for b in node.boundaries:
            for opening in b.openings:
                if opening.feature_type not in OPENING_TYPES:
                    flag(f"opening type {opening.feature_type}")
                if opening.spaces or opening.boundaries:
                    flag("nested opening content")
        for child in _children(node):
            if ftype in BRIDGE_TYPES and child.feature_type not in BRIDGE_TYPES and kept(child):
                add(f"bridge child {child.feature_type}")
                skip.add(id(child))
                continue
            visit(child, False, ftype)
        if any(s.feature_type in OPENING_TYPES for s in node.spaces):
            flag("opening directly below a space")
        if not _has_polygons(node) and _has_curves_or_points(node):
            flag("feature with curve or point geometry only")

    for root in roots:
        visit(root)
    return skip, reasons


def unsupported_reasons(roots, schema: str = "2.0", filters: Optional[ImportFilters] = None) -> List[str]:
    """Why parts of the IR are not built (empty list: all of it is). See ``skipped_features``."""
    return skipped_features(roots, schema, filters)[1]


def scope_notice(reasons: List[str]) -> str:
    """What to tell the user when ``reasons`` (``skipped_features``) keep the builder from importing part
    of a file: the old importers are gone, so content outside the add-on's scope (interior spaces and the
    other CityGML modules) is skipped and belongs in 3DCityDB v5, not here."""
    if not reasons:
        return ""
    return ("Skipped content the add-on does not import (" + "; ".join(reasons) + "). The add-on "
            "imports Building, Transportation (Road, Railway, Track, Square) and CityFurniture; interior "
            "spaces and the other CityGML modules belong in 3DCityDB v5: load the file into 3DCityDB v5 "
            "and open it from the database browser.")


def _implicit_only(node, polygons: bool = True) -> bool:
    """True if the feature's geometry is nothing but ImplicitGeometry templates (polygons only;
    ``polygons=False`` leaves the polygon check to the caller)."""
    own = list(node.geometries.values())
    implicit = [g for g in own if g.gml_type == "ImplicitGeometry"]
    if not implicit or (polygons and not all(g.polygons and not g.curves for g in implicit)):
        return False
    if any(g.gml_type != "ImplicitGeometry" and (g.polygons or g.curves or g.points) for g in own):
        return False
    return not any(g.polygons or g.curves or g.points
                   for b in node.boundaries for _key, g in _surface_geometries(b))


def _surface_geometries(surface):
    yield from surface.geometries.items()
    for opening in surface.openings:
        yield from opening.geometries.items()


def _has_geometry(raw: str) -> bool:
    return "Polygon" in raw or "posList" in raw or "Triangle" in raw


def _all_geometries(node):
    """(key, geometry) of the feature itself, its surfaces and their openings."""
    yield from node.geometries.items()
    for b in node.boundaries:
        yield from b.geometries.items()
        for opening in b.openings:
            yield from opening.geometries.items()


def _own_geometries(node):
    for _key, geometry in _all_geometries(node):
        yield geometry


def _has_polygons(node) -> bool:
    return any(g.polygons for g in _own_geometries(node))


def _has_curves_or_points(node) -> bool:
    return any(g.curves or g.points for g in _own_geometries(node))


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def flat_generic_attributes(attrs) -> Dict[str, Any]:
    """``{name: value}`` as ``attribute_utils._parse_generic_attributes_common_impl``
    returns it: double -> float, int -> int, everything else text."""

    out: Dict[str, Any] = {}
    for a in attrs.generic_attributes:
        if not a.name or a.type == "set":
            continue
        if a.type == "double" and isinstance(a.value, (int, float)):
            out[a.name] = float(a.value)
        elif a.type == "int" and isinstance(a.value, int):
            out[a.name] = a.value
        else:
            out[a.name] = "" if a.value is None else str(a.value).strip()
    return out


def _lod_of(key: str) -> Optional[int]:
    return int(key[3]) if len(key) > 3 and key.startswith("lod") and key[3].isdigit() else None


@dataclass
class _Entry:
    polygon: Any
    lod: Optional[int]
    label: str = ""
    surface_id: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)
    has_context: bool = False
    container: str = ""        # gml_type of the geometry the polygon belongs to


def _own_id(node) -> str:
    """The gml:id the file gives ``node``; '' for an id the reader made up."""
    return "" if node.extra.get("generated_id") else node.gml_id


def object_id(node) -> str:
    """Object name / ``gml_id`` property: the file's id, or ``feat_<number>``
    (the importer's name for a feature without one, made stable)."""
    if not node.extra.get("generated_id"):
        return node.gml_id
    return f"feat_{int(stable_hash(node.gml_id), 16)}"


# build_geometry collects MultiSurface polygons first, then CompositeSurface, Solid, MultiSolid
_GEOMETRY_PHASE = {"MultiSurface": 0, "CompositeSurface": 1, "Solid": 2, "MultiSolid": 3}


def _geometry_entries(geometries, ctx, sub=None):
    for key, geom in geometries.items():
        for poly in geom.polygons:
            label, surface_id, attrs = ctx if ctx else ("", "", {})
            attrs = dict(attrs)
            if sub:
                attrs.update(sub)
            yield _Entry(poly, _lod_of(key), label, surface_id, attrs, bool(ctx), geom.gml_type)


def _entries(node, schema: str = "2.0") -> List[_Entry]:
    """Polygons of ``node`` itself: its surfaces first (they carry the
    surface context), then its own geometry; sub-features are separate.
    """

    return _own_entries(node, schema)


def _own_entries(node, schema: str, sub: Optional[dict] = None) -> List[_Entry]:
    entries: List[_Entry] = []
    own_ctx = None
    if node.feature_type in INSTALLATION_TYPES and schema == "2.0":
        own_id = node.gml_id
        if node.extra.get("generated_id"):
            own_id = f"{object_id(node)}_{node.feature_type}_{stable_hash(node.gml_id)}"
        own_ctx = (node.feature_type, own_id, flat_generic_attributes(node.attrs))
    for b in node.boundaries:
        b_id = _own_id(b)
        if not b_id and b.openings:
            # B64 (AB-17): an id-less wall with windows/doors needs an id the openings can name as
            # their host, else they hang off the Building and 3.0 writes them as its boundary.
            # Stable like the 2.0 installation ids above; id-less walls without openings stay id-less.
            b_id = f"{object_id(node)}_{b.surface_type}_{stable_hash(b.gml_id)}"
        ctx = (b.surface_type, b_id, flat_generic_attributes(b.attrs))
        entries.extend(_geometry_entries(b.geometries, ctx, sub))
        for opening in b.openings:
            attrs = flat_generic_attributes(opening.attrs)
            if b_id:
                attrs.setdefault("filling_parent_surface_id", b_id)
                attrs.setdefault("opening_surface_id", b_id)
            attrs.setdefault("BoundarySurfaceType", b.surface_type)
            attrs.setdefault("opening_surface_type", b.surface_type)
            entries.extend(_geometry_entries(opening.geometries,
                                             (opening.feature_type, _own_id(opening), attrs), sub))
    own = list(_geometry_entries(node.geometries, own_ctx, sub))
    own.sort(key=lambda e: _GEOMETRY_PHASE.get(e.container, len(_GEOMETRY_PHASE)))
    entries.extend(own)
    return entries


def _ring_points(coords, ref):
    rx, ry, rz = ref
    out = [(x - rx, y - ry, z - rz) for (x, y, z) in coords]
    if len(out) >= 4 and all(abs(out[0][k] - out[-1][k]) < 1e-9 for k in range(3)):
        out = out[:-1]
    clean = []
    for p in out:
        if not clean or any(abs(p[k] - clean[-1][k]) > 1e-12 for k in range(3)):
            clean.append(p)
    return clean


def _infer_surface_type(pts, zmin, zmax, horiz_thr=0.9) -> str:
    if not pts or len(pts) < 3:
        return "WallSurface"
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = pts[0], pts[1], pts[2]
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nlen < 1e-12:
        return "WallSurface"
    if abs(nz / nlen) >= horiz_thr:
        cz = sum(p[2] for p in pts) / float(len(pts))
        if abs(zmax - zmin) < 1e-9:
            return "RoofSurface"
        return "RoofSurface" if abs(zmax - cz) <= abs(cz - zmin) else "GroundSurface"
    return "WallSurface"


BOUNDARY_SURFACE_TYPES_30 = (
    "RoofSurface", "WallSurface", "GroundSurface", "CeilingSurface", "OuterCeilingSurface",
    "InteriorWallSurface", "FloorSurface", "OuterFloorSurface", "ClosureSurface",
)
# io/reader/geometry.py: parts, installations, ... keep their own lodNMultiSurface
# even when they have boundary surfaces, unless a surface repeats it.
DIRECT_SUBFEATURE_TYPES_30 = (
    "BuildingPart", "BridgePart", "TunnelPart", "BuildingInstallation", "IntBuildingInstallation",
    "BridgeInstallation", "IntBridgeInstallation", "TunnelInstallation", "IntTunnelInstallation",
    "BuildingFurniture", "BridgeFurniture", "TunnelFurniture", "BuildingRoom", "BridgeRoom",
)


def _signature(ring, ref):
    rx, ry, rz = ref
    return tuple(sorted((round(x - rx, 6), round(y - ry, 6), round(z - rz, 6)) for x, y, z in ring.coords))


def _select_entries(node, entries: List[_Entry], ref_origin, schema: str) -> List[_Entry]:
    """Duplicate filter: once a real boundary surface exists, polygons without one
    (copies from lodNSolid / lodNMultiSurface) are dropped."""
    if schema == "3.0":
        if not any(e.has_context and e.label in BOUNDARY_SURFACE_TYPES_30 for e in entries):
            return entries
        if node.feature_type not in DIRECT_SUBFEATURE_TYPES_30:
            return [e for e in entries if e.has_context]
        known = {_signature(e.polygon.exterior, ref_origin) for e in entries if e.has_context}
        return [e for e in entries
                if e.has_context or _signature(e.polygon.exterior, ref_origin) not in known]
    if any(e.has_context and e.label not in INSTALLATION_TYPES for e in entries):
        return [e for e in entries if e.has_context]
    return entries


def apply_lod_filter(entries: List[_Entry], lods: Optional[set]) -> List[_Entry]:
    """The LoD filter on polygon entries: polygons of an unselected LoD are dropped."""
    if lods is None:
        return entries
    return [e for e in entries if e.lod is None or e.lod in lods]


def feature_geometry(node, ref_origin, default_srs: str = "", schema: str = "2.0",
                     lods: Optional[set] = None,
                     shell_ids: Optional[set] = None) -> FeatureGeometry:
    """Faces, vertices and per-face labels of ``node`` itself, as the importer of
    ``schema`` builds them: 2.0 welds vertices at 1e-5 and drops closing points,
    infers Roof/Wall/Ground for polygons without a surface; 3.0 keeps every point."""

    v3 = schema == "3.0"
    seen = set()
    entries: List[_Entry] = []
    for entry in apply_lod_filter(_entries(node, schema), lods):
        pid = entry.polygon.polygon_id
        if pid:
            if pid in seen:
                continue
            seen.add(pid)
        entries.append(entry)
    entries = _select_entries(node, entries, ref_origin, schema)

    out = FeatureGeometry()
    zmin, zmax = float("inf"), float("-inf")
    if not v3:
        for e in entries:
            for ring in (e.polygon.exterior, *e.polygon.interiors):
                for p in _ring_points(ring.coords, ref_origin):
                    zmin, zmax = min(zmin, p[2]), max(zmax, p[2])
        if zmin == float("inf"):
            zmin = zmax = 0.0

    vmap: Dict[tuple, int] = {}

    def add_vertex(co):
        if v3:
            out.verts.append(co)
            return len(out.verts) - 1
        key = tuple(round(float(c), VERT_WELD_ND) for c in co)
        idx = vmap.get(key)
        if idx is None:
            idx = len(out.verts)
            out.verts.append(co)
            vmap[key] = idx
        return idx

    infer = node.feature_type in INFER_SURFACE_FOR
    rx, ry, rz = ref_origin
    for number, e in enumerate(entries):
        poly = e.polygon
        poly_id = poly.polygon_id if v3 else (poly.polygon_id or f"{object_id(node)}_poly_{number}")
        if e.has_context and poly_id:
            if e.surface_id:
                out.surface_id_by_poly[poly_id] = e.surface_id
            if e.attrs:
                out.surf_attrs_by_poly[poly_id] = dict(e.attrs)
            if v3 and e.attrs.get("filling_parent_surface_id"):
                out.filling_parent_surface_id_by_poly[poly_id] = str(e.attrs["filling_parent_surface_id"])
        if poly_id and not e.has_context and e.attrs:
            out.surf_attrs_by_poly[poly_id] = dict(e.attrs)
        if poly_id:
            if poly.multisurface_id:
                out.multisurface_id_by_poly[poly_id] = poly.multisurface_id
            elif e.label in INSTALLATION_TYPES and e.surface_id and not v3:
                out.multisurface_id_by_poly[poly_id] = f"{e.surface_id}_ms"
            if poly.compositesurface_id:
                out.compositesurface_id_by_poly[poly_id] = poly.compositesurface_id
        for ring in (poly.exterior, *poly.interiors):
            if v3:
                clean = [(x - rx, y - ry, z - rz) for (x, y, z) in ring.coords]
                if len(clean) < 3:
                    continue
                idxs = [add_vertex(c) for c in clean]
            else:
                clean = _ring_points(ring.coords, ref_origin)
                if len(clean) < 3:
                    continue
                raw = [add_vertex(c) for c in clean]
                idxs = []
                for i in raw:
                    if not idxs or i != idxs[-1]:
                        idxs.append(i)
                if len(idxs) >= 3 and idxs[0] == idxs[-1]:
                    idxs = idxs[:-1]
                if len(idxs) < 3:
                    continue
            label = e.label
            if not label:
                inferred = not v3 and (infer or bool(poly.compositesurface_id))
                label = _infer_surface_type(clean, zmin, zmax) if inferred else node.feature_type
            out.faces.append(idxs)
            if v3:
                in_shell = bool(poly_id) and shell_ids is not None and poly_id in shell_ids
                out.surf_labels.append((label, poly_id, ring.ring_id, default_srs or "", tuple(idxs),
                                        e.lod, in_shell, ring.role == "interior"))
            else:
                out.surf_labels.append((label, poly_id, ring.ring_id, default_srs or "",
                                        tuple(idxs), e.lod))
    return out


# ---------------------------------------------------------------------------
# custom properties
# ---------------------------------------------------------------------------

def _first(thematic: dict, key: str) -> Optional[dict]:
    entries = thematic.get(key) or []
    return entries[0] if entries else None


def _text(entry) -> str:
    return "" if entry is None else str(entry.get("value", "")).strip()


# B22: coded attributes whose codeSpace the importer keeps (written back by both writers).
CODED_KEYS = tuple(f"{p}:{local}" for p in ("bldg", "tran", "frn") for local in ("class", "function", "usage")) + (
    "bldg:roofType", "tran:surfaceMaterial")


def _keep_code_spaces(props: dict, thematic: dict) -> None:
    from .attribute_utils import CODE_SPACE_PREFIX
    for key in CODED_KEYS:
        entry = _first(thematic, key)
        if entry is not None and str(entry.get("code_space") or "").strip():
            props[CODE_SPACE_PREFIX + key] = str(entry["code_space"]).strip()


# Properties the importer writes only for some feature types: (prefix, types).
_CLASS_FUNCTION_USAGE = (
    ("bldg", ("Building", "BuildingPart", "BuildingInstallation", "IntBuildingInstallation",
              "BuildingFurniture", "BuildingRoom")),
    ("brid", ("Bridge", "BridgePart", "BridgeRoom", "BridgeFurniture",
              "BridgeInstallation", "IntBridgeInstallation")),
    ("tun", ("Tunnel", "TunnelPart", "TunnelInstallation", "IntTunnelInstallation", "TunnelFurniture")),
)


def importer_properties(node, thematic: Optional[dict] = None, schema: str = "2.0") -> Dict[str, Any]:
    """The custom properties ``import_citygml2_into_blender`` writes for a
    mesh feature, minus ``CRS`` and the installation semantic block."""

    thematic = node.extra.get("thematic_attributes", {}) if thematic is None else thematic
    props: Dict[str, Any] = {}
    ftype = node.feature_type

    for key, value in (("core:creationDate", node.attrs.creation_date),
                       ("core:terminationDate", node.attrs.termination_date),
                       ("core:validFrom", _text(_first(thematic, "core:validFrom"))),
                       ("core:validTo", _text(_first(thematic, "core:validTo")))):
        if value and value.strip():
            props[key] = value.strip()

    _keep_code_spaces(props, thematic)
    for local in ("class", "function", "surfaceMaterial", "trafficDirection", "granularity"):
        text = _text(_first(thematic, f"tran:{local}"))
        if text:
            props[f"tran:{local}"] = text
    if ftype in TRANSPORT_TYPES:
        text = _text(_first(thematic, "tran:usage"))
        if text:
            props["tran:usage"] = text

    if ftype in FURNITURE_TYPES:
        for local in ("class", "function", "usage"):
            text = _text(_first(thematic, f"frn:{local}"))
            if text:
                props[f"frn:{local}"] = text

    text = _text(_first(thematic, "luse:class"))
    if text:
        props["luse:class"] = text
    for local in ("function", "usage"):
        values = [str(e["value"]).strip() for e in thematic.get(f"luse:{local}", ())
                  if str(e.get("value", "")).strip()]
        if len(values) == 1:
            props[f"luse:{local}"] = values[0]
        elif len(values) > 1:
            props[f"luse:{local}"] = values

    entry = _first(thematic, "bldg:sortKey")
    if entry is not None and str(entry["value"]).strip():
        props["bldg:sortKey"] = entry["value"] if isinstance(entry["value"], int) else str(entry["value"]).strip()

    for local in ("class", "function", "usage"):
        text = _text(_first(thematic, f"bldg:{local}"))
        if text:
            props[f"bldg:{local}"] = text
    entry = _first(thematic, "bldg:roofType")
    if entry is not None:
        props["bldg:roofType"] = str(entry["value"]).strip()
    entry = _first(thematic, "bldg:measuredHeight")
    if entry is not None:
        try:
            props["bldg:measuredHeight"] = float(entry["value"])
            if str(entry.get("uom") or "").strip():
                props["bldg:measuredHeight:uom"] = str(entry["uom"]).strip()
        except (TypeError, ValueError):
            pass
    for local in ("yearOfConstruction", "yearOfDemolition", "storeysAboveGround", "storeysBelowGround"):
        entry = _first(thematic, f"bldg:{local}")
        if entry is not None and isinstance(entry["value"], int):
            props[f"bldg:{local}"] = entry["value"]
    for local in ("storeyHeightsAboveGround", "storeyHeightsBelowGround"):
        entry = _first(thematic, f"bldg:{local}")
        if entry is not None and isinstance(entry["value"], list) and entry["value"]:
            props[f"bldg:{local}"] = list(entry["value"])
            if entry.get("uom"):
                props[f"bldg:{local}:uom"] = entry["uom"]

    if ftype in BRIDGE_TYPES:
        for local in ("class", "function", "usage"):
            text = _text(_first(thematic, f"brid:{local}"))
            if text:
                props[f"brid:{local}"] = text
        entry = _first(thematic, "brid:isMovable")
        if entry is not None and isinstance(entry["value"], bool):
            props["brid:isMovable"] = entry["value"]

    props.update(address_properties(node.attrs, schema))
    if schema == "3.0":
        props.update(construction_properties(node))
    props.update(flat_generic_attributes(node.attrs))
    # B48: the LoDs whose geometry was a Solid in the source; the writers keep that solid on
    # round-trip even where our edge-based closure check is stricter than ISO 19107 (T-junctions)
    solid_lods = sorted({k[3] for k, g in node.geometries.items()
                         if len(k) == 9 and k.startswith("lod") and k.endswith("Solid")
                         and g.gml_type in ("Solid", "CompositeSolid")})
    if solid_lods:
        props["cgml_source_solid_lods"] = ",".join(solid_lods)
    return props


XAL_NAMESPACE = {"2.0": "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0", "3.0": "urn:oasis:names:tc:ciq:xal:3"}

# Attribute lookups the importer writes next to the plain ones.
_LEGACY_ADDRESS_KEYS = ("country", "locality", "street", "streetNumber", "postCode")


def address_properties(attrs, schema: str) -> Dict[str, Any]:
    """``bldg:address:*`` of the first address, via the xAL parser the importer uses
    on the verbatim ``xalAddress`` fragment the IR keeps."""
    props: Dict[str, Any] = {}
    if not attrs.addresses:
        return props
    address = attrs.addresses[0]
    if address.xal:
        try:
            el = ET.fromstring(address.xal)
        except ET.ParseError:
            el = None
        # <xAL:Address> in the schema's own xAL namespace, else the 2.0 <xAL:AddressDetails>
        uri = XAL_NAMESPACE[schema]
        if el is not None and el.tag == f"{{{uri}}}Address":
            data = parse_xal_address(el, {"xAL": uri}, version=schema)
            for key, value in data.items():
                if value:
                    props[f"bldg:address:{key}"] = value
            for key in _LEGACY_ADDRESS_KEYS:
                if key in data:
                    props[f"bldg:address:{key}"] = data[key]
        elif schema == "2.0":
            # a real 2.0 file carries <xAL:AddressDetails>; the IR has read its fields
            for key, value in (("country", address.country), ("locality", address.locality),
                               ("street", address.street), ("streetNumber", address.house_number),
                               ("postCode", address.postal_code)):
                if value:
                    props[f"bldg:address:{key}"] = value
    if address.multi_point:
        x, y, *rest = address.multi_point[0]
        props["bldg:address:point:x"], props["bldg:address:point:y"] = float(x), float(y)
        props["bldg:address:point:z"] = float(rest[0]) if rest else 0.0
    return props


def _unknown_by_local(node, local: str):
    for raw in node.extra.get("unknown_elements", ()):
        try:
            el = ET.fromstring(raw)
        except ET.ParseError:
            continue
        if el.tag.rpartition("}")[2] == local and "/construction/" in el.tag:
            yield el


def construction_properties(node) -> Dict[str, Any]:
    """CityGML 3 ``con:`` elements the reader leaves in ``unknown_elements``:
    the construction dates and the qualified ``con:height``."""
    props: Dict[str, Any] = {}
    for local in ("dateOfConstruction", "dateOfDemolition"):
        for el in _unknown_by_local(node, local):
            text = (el.text or "").strip()
            if text:
                props[f"con:{local}"] = text
            break
    for wrapper in _unknown_by_local(node, "height"):
        height = next((c for c in wrapper if c.tag.rpartition("}")[2] == "Height"), None)
        if height is None:
            continue
        props["con:height"] = ""
        children = {c.tag.rpartition("}")[2]: c for c in height}
        for local in ("highReference", "lowReference", "status"):
            if local in children:
                props[f"con:height:{local}"] = (children[local].text or "").strip()
        value = children.get("value")
        if value is not None:
            text = (value.text or "").strip()
            props["con:height:value"] = float(text) if text else ""
            if value.get("uom") is not None:
                props["con:height:uom"] = value.get("uom")
        break
    return props


def _installation_verdict(node, thematic):
    cls = _text(_first(thematic, "bldg:class"))
    func = _text(_first(thematic, "bldg:function"))
    usage = _text(_first(thematic, "bldg:usage"))
    name = node.attrs.names[0] if node.attrs.names else ""
    verdict = resolve_semantic_type(source_feature_type=node.feature_type, class_value=cls,
                                    function_value=func, usage_value=usage, name=name)
    unknown = verdict.origin == "generic" and bool((cls or func or usage).strip())
    return verdict, unknown, (cls or func or usage).strip()


def implicit_geometry(node, lods: Optional[set] = None):
    """``(key, geometry)`` of the ImplicitGeometry that shows the feature: the highest selected LoD.
    None: the feature has no implicit geometry of its own (or none in a selected LoD)."""
    best = None
    for key, geom in node.geometries.items():
        lod = _lod_of(key)
        if geom.gml_type != "ImplicitGeometry" or (lods is not None and lod is not None and lod not in lods):
            continue
        if best is None or (lod or 0) > (_lod_of(best[0]) or 0):
            best = (key, geom)
    return best


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def implicit_placement(geom, ref_origin, lod: Optional[int] = None) -> ImplicitPlacement:
    """Template identity and world matrix of an ImplicitGeometry (CityGML: M = transformationMatrix,
    translation += referencePoint; the scene origin is subtracted from the result)."""
    values = list(geom.extra.get("transformation_matrix") or ())
    if len(values) == 16:
        m = [values[0:4], values[4:8], values[8:12], values[12:16]]
    else:
        m = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    if geom.points:
        point = tuple(geom.points[0]) + (0.0,) * 3
        for i in range(3):
            m[i][3] += float(point[i])
    rx, ry, rz = ref_origin
    shift = [[1.0, 0.0, 0.0, -rx], [0.0, 1.0, 0.0, -ry], [0.0, 0.0, 1.0, -rz], [0.0, 0.0, 0.0, 1.0]]
    matrix = _matmul(shift, m)
    cleared = [row[:3] + [0.0] for row in matrix[:3]] + [matrix[3]]
    text = " ".join(str(float(v)) for row in cleared for v in row)
    rings = [[tuple(round(float(c), 6) for c in p) for p in ring.coords]
             for poly in geom.polygons for ring in (poly.exterior, *poly.interiors)]
    fingerprint = hashlib.sha1(json.dumps(rings).encode("utf-8")).hexdigest()
    template_id = (str(geom.extra.get("resolved_href") or "").lstrip("#")
                   or str(geom.extra.get("relative_gml_id") or "") or f"geomfp:{fingerprint}")
    return ImplicitPlacement(template_id, fingerprint, matrix, text, lod)


def feature_payload(node, ref_origin, default_srs: str = "", schema: str = "2.0",
                    lods: Optional[set] = None,
                    shell_ids: Optional[set] = None) -> FeaturePayload:
    thematic = node.extra.get("thematic_attributes", {})
    own_name = node.attrs.names[0].strip() if node.attrs.names else ""
    implicit = implicit_geometry(node, lods) if _implicit_only(node) else None
    if implicit is not None:
        # the template keeps its own coordinates; the object's matrix places it
        template = dataclasses.replace(node, geometries={implicit[0]: implicit[1]})
        geometry = feature_geometry(template, (0.0, 0.0, 0.0), default_srs, schema, None, shell_ids)
    elif _implicit_only(node):
        geometry = FeatureGeometry()     # only LoDs the filter leaves out
    else:
        geometry = feature_geometry(node, ref_origin, default_srs, schema, lods, shell_ids)
    payload = FeaturePayload(
        gml_id=object_id(node), feature_type=node.feature_type, parent_id=node.parent_id,
        generated_id=bool(node.extra.get("generated_id")),
        display_name=own_name,
        geometry=geometry,
        properties=importer_properties(node, thematic, schema),
        implicit=implicit_placement(implicit[1], ref_origin, _lod_of(implicit[0])) if implicit is not None else None,
        node=node)
    if schema == "3.0":
        payload.generic_attributes = flat_generic_attributes(node.attrs)
    if node.feature_type in INSTALLATION_TYPES:
        verdict, unknown, value = _installation_verdict(node, thematic)
        payload.installation_type = verdict.canonical_type
        payload.installation_origin = verdict.origin
        payload.installation_unknown_subtype = unknown
        payload.installation_unknown_value = value if unknown else ""
        interior = node.source_role.startswith("interior")
        payload.source_role = "interior" if interior else "outer"
        payload.source_element = f"bldg:{node.source_role}" if node.source_role else ""
        if schema == "3.0":
            payload.source_role, payload.source_element = "outer", "bldg:buildingInstallation"
    return payload


def edge_kind(parent, child, schema: str) -> str:
    """How the importer parents ``child`` below ``parent`` ('' for no parenting).
    CityGML 2.0: every child with an id. CityGML 3.0 (its pre-scan): parts below a
    Building, installations below a Building / BuildingPart."""
    ptype, ctype = parent.feature_type, child.feature_type
    if ctype in TRANSPORT_TYPES:
        return "part" if ptype in TRANSPORT_TYPES else ""
    if schema != "3.0":
        return "part" if ctype in BUILDING_TYPES + BRIDGE_TYPES else "installation"
    if ctype == "BuildingPart":
        return "part" if ptype == "Building" else ""
    if ctype == "BridgePart":
        return "part" if ptype in BRIDGE_TYPES else ""
    if ctype in INSTALLATION_TYPES:
        return "installation" if ptype in BUILDING_TYPES else ""
    return ""


def plan_document(roots, ref_origin, default_srs: str = "", schema: str = "2.0",
                  filters: Optional[ImportFilters] = None,
                  shell_ids: Optional[set] = None, skip: Optional[set] = None) -> DocumentPlan:
    """Payloads of every supported city object that passes ``filters``, in document order, and the
    parent edges between kept features that have ids in the file. ``skip``: ``id()`` of features left
    out, with their children (``skipped_features``)."""

    plan = DocumentPlan()
    lods = filters.lods if filters is not None else None
    built = []

    def visit(node, parent):
        if skip and id(node) in skip:
            return      # not built (``skipped_features``), nor anything below it
        plan.total += 1
        keep = filters is None or filters.passes(node)
        if keep:
            built.append(feature_payload(node, ref_origin, default_srs, schema, lods, shell_ids=shell_ids))
        if keep and parent is not None and (filters is None or filters.passes(parent)) \
                and not node.extra.get("generated_id") and not parent.extra.get("generated_id"):
            kind = edge_kind(parent, node, schema)
            if kind:
                plan.edges.append((parent.gml_id, node.gml_id, kind))
        for child in _children(node):
            visit(child, node)

    for root in roots:
        visit(root, None)
    plan.features = built
    return plan


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
