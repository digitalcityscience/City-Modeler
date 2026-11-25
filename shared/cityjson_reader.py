"""CityJSON 2.0 / 1.1 -> ``SpaceNode`` IR (CJ-01). No bpy.

Plan: ``CITYJSON_INTEGRATION_PLAN_2026-11-17.md`` section 4.1. The document is
checked for shape first (vertex indices, semantic value depth, ``transform``),
then mapped onto the IR the CityGML reader produces for a CityGML 3.0 file, so
everything downstream sees an ordinary 3.0 document (``schema_version="3.0"``).

Scope: Building, BuildingPart, BuildingInstallation, their semantic surfaces and
Window/Door openings; Road, Railway, TransportSquare and CityFurniture (CJ-07, core
profile). Interior content (rooms, storeys, units, furniture) and the other modules
are not read; their names go to ``skipped`` (for ``builder_payload.scope_notice``).

Mapping notes:
- ``world = v * scale + translate``; rings are closed like the GML reader's.
- Inner rings are kept (``PolygonNode.interiors``).
- A semantic surface object becomes one ``ThematicSurfaceNode`` holding all its
  polygons; objects with the same ``id`` in several geometries are merged.
- Window/Door become ``WindowSurface``/``DoorSurface`` spaces in the host
  surface's ``openings`` (``host_id``), as the reader gives for 3.0 GML.
- A polygon whose semantic value is null stays in the object's own geometry,
  without a class. In a Solid the classified polygons are referenced by
  ``extra["member_hrefs"]`` (like an xlinked GML Solid), the null ones inline.
- The LoD text ("2.2") is kept in ``GeometryNode.extra["cityjson_lod"]``; the IR
  key uses its integer part.
- CJ-07: a ``TrafficArea`` / ``AuxiliaryTrafficArea`` semantic surface becomes a child
  space of its Road / Railway / Square (the IR a CityGML 2.0 road gives), a
  ``TransportSquare`` is the IR's ``Square``; a ``GeometryInstance`` of a CityFurniture
  becomes the IR's ``ImplicitGeometry`` (template polygons in template coordinates,
  reference point, matrix), as ``citygml_reader`` gives it.
- An id that is a valid gml:id (xs:ID = NCName, ``ids.is_ncname``: "B.1", "B.001",
  "NL.IMBAG.Pand.0503100000000010") is kept as it is. Only an invalid one (starts with a
  digit, has a blank or ':') is mapped deterministically (K2); its original is kept in
  ``extra["cgml3_cityjson_id"]``.
"""
import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Dict, List, Optional

try:
    from .authoring_core.domain.ids import is_ncname, ring_id
    from .citygml_ir import (Address, CityObjectAttrs, GenericAttribute, GeometryNode,
                             SpaceNode, ThematicSurfaceNode, lod_key)
    from .semantic_tree import PolygonNode, RingNode
except ImportError:  # pure-Python unit tests import ``shared`` as a top-level package
    from shared.authoring_core.domain.ids import is_ncname, ring_id
    from shared.citygml_ir import (Address, CityObjectAttrs, GenericAttribute, GeometryNode,
                                   SpaceNode, ThematicSurfaceNode, lod_key)
    from shared.semantic_tree import PolygonNode, RingNode

SUPPORTED_VERSIONS = ("2.0", "1.1")
BUILDING_TYPES = ("Building", "BuildingPart", "BuildingInstallation")
_ROLES = {"BuildingPart": "buildingPart", "BuildingInstallation": "buildingInstallation"}
SURFACE_TYPES = ("RoofSurface", "WallSurface", "GroundSurface", "ClosureSurface",
                 "OuterCeilingSurface", "OuterFloorSurface")
INTERIOR_SURFACE_TYPES = ("InteriorWallSurface", "CeilingSurface", "FloorSurface")
OPENINGS = {"Window": "WindowSurface", "Door": "DoorSurface"}
_SURFACE_DEPTH = {"MultiSurface": 1, "CompositeSurface": 1, "Solid": 2}
# CJ-07 (core profile): CityJSON type -> IR feature type; semantic surface -> source role of the child space
TRANSPORT = {"Road": "Road", "Railway": "Railway", "TransportSquare": "Square"}
TRAFFIC_AREAS = {"TrafficArea": "trafficArea", "AuxiliaryTrafficArea": "auxiliaryTrafficArea"}
FURNITURE = "CityFurniture"
ROOT_TYPES = BUILDING_TYPES[:2] + tuple(TRANSPORT) + (FURNITURE,)   # read as top-level objects
READ_TYPES = BUILDING_TYPES + ROOT_TYPES[2:]
INSTANCE = "GeometryInstance"
# CityJSON attribute -> IR thematic key (the keys builder_payload.importer_properties reads)
THEMATIC = {name: f"bldg:{name}" for name in (
    "class", "function", "usage", "roofType", "measuredHeight", "storeysAboveGround",
    "storeysBelowGround", "storeyHeightsAboveGround", "storeyHeightsBelowGround",
    "yearOfConstruction", "yearOfDemolition")}
THEMATIC["relationToConstruction"] = "con:relationToConstruction"
THEMATIC.update(dateOfConstruction="con:dateOfConstruction", dateOfDemolition="con:dateOfDemolition")  # CJ-03
# the same for the other two modules (the keys builder_payload.importer_properties reads)
THEMATIC_TRAN = {name: f"tran:{name}" for name in ("class", "function", "usage", "surfaceMaterial")}
THEMATIC_FRN = {name: f"frn:{name}" for name in ("class", "function", "usage")}
_DATES = {"creationDate": "creation_date", "terminationDate": "termination_date"}
NAME = "name"   # <-> CityObjectAttrs.names (gml:name)
_ADDRESS = {"ThoroughfareName": "street", "ThoroughfareNumber": "house_number",
            "PostalCode": "postal_code", "PostCode": "postal_code", "Locality": "locality",
            "AdministrativeArea": "administrative_area", "Country": "country"}


class CityJSONError(ValueError):
    """The document cannot be read (wrong version, broken structure)."""


def gml_id_for(cityjson_id: str) -> str:
    """``cityjson_id`` itself when it is a valid gml:id (NCName), else a deterministic gml:id (K2)."""
    return cityjson_id if is_ncname(cityjson_id) else mapped_id(cityjson_id)


def mapped_id(cityjson_id: str) -> str:
    """The gml:id an invalid id is mapped to. Scenes opened before 2026-11-20 carry it for dotted ids too."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", cityjson_id)
    if not re.match(r"^[A-Za-z_]", s):
        s = "ID_" + s
    # the hash keeps "a.b" and "a:b" apart after both became "a_b"
    return f"{s}_{hashlib.sha1(cityjson_id.encode('utf-8')).hexdigest()[:8]}"


def load(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_document(doc: Any) -> None:
    """Raise ``CityJSONError`` unless ``doc`` is a CityJSON 2.0/1.1 object we can index safely."""
    if not isinstance(doc, dict) or doc.get("type") != "CityJSON":
        raise CityJSONError("not a CityJSON document (type must be 'CityJSON')")
    version = str(doc.get("version", ""))
    if version == "1.0":
        raise CityJSONError("CityJSON 1.0 is not supported: upgrade the file with cjio "
                            "('cjio in.city.json upgrade save out.city.json')")
    if version not in SUPPORTED_VERSIONS:
        raise CityJSONError(f"CityJSON version {version!r} is not supported (2.0, 1.1)")
    transform = doc.get("transform")
    if not isinstance(transform, dict) or not all(
            isinstance(transform.get(k), list) and len(transform[k]) == 3
            and all(isinstance(x, (int, float)) for x in transform[k]) for k in ("scale", "translate")):
        if transform is None and isinstance(doc.get("vertices"), list) and any(
                isinstance(c, float) for v in doc["vertices"][:100] if isinstance(v, list) for c in v):
            raise CityJSONError(f"vertices are real numbers without 'transform': CityJSON 1.0 layout, "
                                f"not valid CityJSON {version}; re-export the file as CityJSON {version}")
        raise CityJSONError("missing or invalid 'transform' (scale and translate, 3 numbers each)")
    vertices = doc.get("vertices")
    if not isinstance(vertices, list) or not all(
            isinstance(v, list) and len(v) == 3 and all(isinstance(c, int) for c in v) for v in vertices):
        raise CityJSONError("'vertices' must be a list of [x, y, z] integers")
    if not isinstance(doc.get("CityObjects"), dict):
        raise CityJSONError("'CityObjects' must be an object")
    check_templates(doc)


def _check_geometry(g: dict, nv: int, where: str) -> None:
    gtype = g.get("type")
    depth = _SURFACE_DEPTH.get(gtype)
    if depth is None:
        return   # not read; the caller warns
    where = f"{where} {gtype} lod {g.get('lod')!r}"

    def surfaces(b, d):   # boundaries down to polygons
        if not isinstance(b, list):
            raise CityJSONError(f"{where}: boundaries have the wrong depth")
        return [b] if d == 0 else [p for x in b for p in surfaces(x, d - 1)]

    for poly in surfaces(g.get("boundaries"), depth):
        if not poly or not all(isinstance(r, list) for r in poly):
            raise CityJSONError(f"{where}: boundaries have the wrong depth")
        for ring in poly:
            for i in ring:
                if not isinstance(i, int) or isinstance(i, bool):
                    raise CityJSONError(f"{where}: boundaries have the wrong depth")
                if not 0 <= i < nv:
                    raise CityJSONError(f"{where}: vertex index {i} out of range (0..{nv - 1})")
    sem = g.get("semantics")
    if sem is None:
        return
    n_surfaces = len(sem.get("surfaces") or ())

    def values(v, b, d):   # values mirror boundaries down to polygons; null may stand for a shell
        if v is None:
            return
        if d == 0:
            if not isinstance(v, int) or isinstance(v, bool):
                raise CityJSONError(f"{where}: semantic values have the wrong depth")
            if not 0 <= v < n_surfaces:
                raise CityJSONError(f"{where}: semantic value {v} out of range (0..{n_surfaces - 1})")
            return
        if not isinstance(v, list) or len(v) != len(b):
            raise CityJSONError(f"{where}: semantic values have the wrong depth or length")
        for x, y in zip(v, b):
            values(x, y, d - 1)

    values(sem.get("values"), g["boundaries"], depth)
    for s in sem.get("surfaces") or ():
        p = s.get("parent")
        if p is not None and not (isinstance(p, int) and 0 <= p < n_surfaces):
            raise CityJSONError(f"{where}: semantic parent {p!r} out of range")


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_instance(g: dict, templates: list, nv: int, nt: int, where: str) -> dict:
    """The template of GeometryInstance ``g`` (shape-checked against ``nt`` template vertices); raises
    ``CityJSONError`` unless ``g`` names one, has one reference vertex below ``nv`` and a 4x4 matrix."""
    ti, ref, matrix = g.get("template"), g.get("boundaries"), g.get("transformationMatrix")
    if not (isinstance(ti, int) and not isinstance(ti, bool) and 0 <= ti < len(templates)
            and isinstance(templates[ti], dict)):
        raise CityJSONError(f"{where}: GeometryInstance template {ti!r} not in geometry-templates")
    if not (isinstance(ref, list) and len(ref) == 1 and isinstance(ref[0], int)
            and not isinstance(ref[0], bool) and 0 <= ref[0] < nv):
        raise CityJSONError(f"{where}: GeometryInstance needs one reference vertex in 'boundaries'")
    if not (isinstance(matrix, list) and len(matrix) == 16 and all(_number(x) for x in matrix)):
        raise CityJSONError(f"{where}: GeometryInstance needs a transformationMatrix of 16 numbers")
    _check_geometry(templates[ti], nt, f"{where} template {ti}")
    return templates[ti]


def check_templates(doc: dict) -> None:
    """Raise ``CityJSONError`` when ``geometry-templates`` is there but not indexable."""
    templates = doc.get("geometry-templates")
    if not templates:       # absent or empty: nothing can refer to it
        return
    if not (isinstance(templates, dict) and isinstance(templates.get("templates"), list)
            and isinstance(templates.get("vertices-templates"), list)
            and all(isinstance(v, list) and len(v) == 3 and all(_number(c) for c in v)
                    for v in templates["vertices-templates"])):
        raise CityJSONError("'geometry-templates' needs 'templates' and 'vertices-templates' ([x, y, z] numbers)")


def _generic(name: str, value: Any, raw: dict) -> Optional[GenericAttribute]:
    if isinstance(value, bool):
        return GenericAttribute(name, "string", "true" if value else "false")
    if isinstance(value, int):
        return GenericAttribute(name, "int", value)
    if isinstance(value, float):
        return GenericAttribute(name, "double", value)
    if isinstance(value, str):
        return GenericAttribute(name, "string", value)
    raw[name] = value   # null / list / object: no CityGML generic type, kept verbatim
    return None


def thematic_table(ctype: str) -> dict:
    """CityJSON attribute name -> IR thematic key, for a city object or semantic surface type."""
    if ctype in TRANSPORT or ctype in TRAFFIC_AREAS:
        return THEMATIC_TRAN
    return THEMATIC_FRN if ctype == FURNITURE else THEMATIC if ctype in BUILDING_TYPES else {}


def _attributes(source: dict, extra: dict, table: dict) -> CityObjectAttrs:
    generic, raw, dates, thematic = [], {}, {}, {}
    names = ()
    for name, value in (source.get("attributes") or {}).items():
        if name in _DATES and isinstance(value, str):
            dates[_DATES[name]] = value
        elif name == NAME and (isinstance(value, str) or isinstance(value, list) and value
                               and all(isinstance(v, str) for v in value)):
            names = (value,) if isinstance(value, str) else tuple(value)   # gml:name (CJ-03 writes it)
        elif name in table and value is not None:
            thematic[table[name]] = [{"value": v} for v in (value if isinstance(value, list) else [value])]
        else:
            attr = _generic(name, value, raw)
            if attr is not None:
                generic.append(attr)
    if thematic:
        extra["thematic_attributes"] = thematic
    if raw:
        extra["cityjson_raw_attributes"] = raw
    return CityObjectAttrs(names=names, generic_attributes=tuple(generic), **dates)


class _Reader:
    def __init__(self, doc: dict, warnings: Optional[list], skipped: Optional[list], lod: Optional[str]):
        self.objects = doc["CityObjects"]
        t = doc["transform"]
        self.scale, self.translate = t["scale"], t["translate"]
        self.vertices = doc["vertices"]
        templates = doc.get("geometry-templates")
        templates = templates if isinstance(templates, dict) else {}
        self.templates = templates.get("templates") or []
        self.template_vertices = templates.get("vertices-templates") or []
        self.warnings = warnings if warnings is not None else []
        self.skipped = skipped if skipped is not None else []
        self.lod = lod

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def world(self, i: int):
        v, s, t = self.vertices[i], self.scale, self.translate
        return (v[0] * s[0] + t[0], v[1] * s[1] + t[1], v[2] * s[2] + t[2])

    def polygon(self, rings: list, pid: str, where: str) -> Optional[PolygonNode]:
        out = []
        for ri, ring in enumerate(rings):
            if len(ring) < 3:
                self.warn(f"{where}: ring {ri} of polygon {pid} has {len(ring)} vertices, polygon skipped")
                return None
            coords = [self.world(i) for i in ring]
            out.append(RingNode(ring_id(pid, ri), pid, "exterior" if ri == 0 else "interior",
                                tuple(coords + coords[:1])))
        return PolygonNode(pid, out[0], tuple(out[1:]))

    def address(self, a: dict) -> Address:
        fields = {f: str(a[k]) for k, f in _ADDRESS.items() if a.get(k) not in (None, "")}
        rest = {k: v for k, v in a.items() if k not in _ADDRESS and k != "location"}
        loc = a.get("location") or {}
        points = ()
        if loc.get("type") == "MultiPoint":
            points = tuple(self.world(i) for i in loc.get("boundaries", ())
                           if isinstance(i, int) and 0 <= i < len(self.vertices))
        return Address(multi_point=points, extra={"cityjson": rest} if rest else {}, **fields)

    # -- one CityObject ------------------------------------------------------------------------

    def city_object(self, oid: str, parent_id: str, seen: set) -> SpaceNode:
        o = self.objects[oid]
        ctype = o["type"]
        gml_id = gml_id_for(oid)
        extra: Dict[str, Any] = {}
        if gml_id != oid:
            extra["cgml3_cityjson_id"] = oid
        attrs = _attributes(o, extra, thematic_table(ctype))
        if ctype in ("Building", "BuildingPart") and isinstance(o.get("address"), list):
            attrs = replace(attrs, addresses=tuple(self.address(a) for a in o["address"]
                                                   if isinstance(a, dict)))
        where = f"{ctype} {oid!r}"

        own: Dict[str, GeometryNode] = {}
        surfaces: Dict[str, dict] = {}    # gml_id -> {type, geometries, openings, attrs, extra}
        loose_openings: List[SpaceNode] = []
        used_lods = set()   # one geometry per integer LoD: surfaces carry lodNMultiSurface either way
        classes = (TRAFFIC_AREAS if ctype in TRANSPORT else
                   (*SURFACE_TYPES, *OPENINGS) if ctype in BUILDING_TYPES else ())
        for gi, g in enumerate(o.get("geometry") or ()):
            gtype, lod_text = g.get("type"), str(g.get("lod", ""))
            if gtype == INSTANCE and ctype == FURNITURE:
                self.instance(g, where, own)
                continue
            if self.lod is not None and lod_text != self.lod:
                continue
            if gtype not in _SURFACE_DEPTH:
                self.warn(f"{where}: {gtype} geometry (lod {lod_text}) not read")
                self.skipped.append(f"{gtype} geometry of {ctype} {oid}")
                continue
            try:
                n = int(lod_text.split(".")[0])
                if not 0 <= n <= 3:
                    raise ValueError
            except ValueError:
                self.warn(f"{where}: lod {lod_text!r} not read")
                continue
            key = lod_key(n, "Solid" if gtype == "Solid" else "MultiSurface")
            if n in used_lods:
                self.warn(f"{where}: second LoD {n} geometry (lod {lod_text}) skipped; "
                          "read with lod= to choose one")
                continue
            used_lods.add(n)
            self.geometry(g, gi, n, key, lod_text, gml_id, where, own, surfaces, loose_openings, classes)

        boundaries = []
        areas = []          # CJ-07: traffic areas, child spaces of the road
        for sid, s in surfaces.items():
            if s["type"] in OPENINGS:
                continue
            if s["type"] in TRAFFIC_AREAS:
                areas.append(SpaceNode(
                    gml_id=sid, feature_type=s["type"], source_role=TRAFFIC_AREAS[s["type"]],
                    schema_version="3.0", source_element=s["type"], parent_id=gml_id,
                    geometries=s["geometries"], attrs=s["attrs"], extra=s["extra"]))
                continue
            boundaries.append(ThematicSurfaceNode(
                gml_id=sid, surface_type=s["type"], geometries=s["geometries"],
                openings=tuple(s["openings"]), schema_version="3.0", source_element=s["type"],
                parent_id=gml_id, attrs=s["attrs"], extra=s["extra"]))

        children = []
        for cid in o.get("children") or ():
            child = self.objects.get(cid)
            if child is None:
                self.warn(f"{where}: child {cid!r} not in CityObjects")
            elif cid in seen:
                self.warn(f"{where}: child {cid!r} already read (cycle or shared child), skipped")
            elif child.get("type") not in BUILDING_TYPES[1:] or ctype not in BUILDING_TYPES:
                self.skipped.append(f"{child.get('type')} {cid}")
            else:
                seen.add(cid)
                children.append(self.city_object(cid, gml_id, seen))

        ftype = TRANSPORT.get(ctype, ctype)
        return SpaceNode(gml_id=gml_id, feature_type=ftype, source_role=_ROLES.get(ctype, ""),
                         schema_version="3.0", source_element=ftype, parent_id=parent_id,
                         geometries=own, boundaries=tuple(boundaries),
                         spaces=tuple(loose_openings + areas + children), attrs=attrs, extra=extra)

    def instance(self, g: dict, where: str, own: dict) -> None:
        """A ``GeometryInstance`` as the IR's ImplicitGeometry in ``own`` (see ``citygml_reader.implicit``)."""
        t = check_instance(g, self.templates, len(self.vertices), len(self.template_vertices), where)
        ti, ttype, lod_text = g["template"], t.get("type"), str(t.get("lod", ""))
        if self.lod is not None and lod_text != self.lod:
            return
        try:
            n = int(lod_text.split(".")[0])
            key = lod_key(n, "ImplicitRepresentation")
        except ValueError:      # IRError is one
            self.warn(f"{where}: template {ti} lod {lod_text!r} not read")
            return
        if ttype not in _SURFACE_DEPTH:
            self.warn(f"{where}: {ttype} template (lod {lod_text}) not read")
            self.skipped.append(f"{ttype} template of {where}")
            return
        if key in own:
            self.warn(f"{where}: second LoD {n} GeometryInstance skipped")
            return
        shells = t["boundaries"] if ttype == "Solid" else [t["boundaries"]]
        if len(shells) > 1:
            self.warn(f"{where}: template {ti}: {len(shells) - 1} inner shell(s) not read")
            self.skipped.append(f"inner shell of {where}")
        polys = []
        for pi, rings in enumerate(shells[0] if shells else ()):
            pid = f"cityjson_template_{ti}_p{pi}"
            if any(len(ring) < 3 for ring in rings):
                self.warn(f"{where}: template {ti}: polygon {pi} has a ring under 3 vertices, skipped")
                continue
            out = []
            for ri, ring in enumerate(rings):
                coords = [tuple(float(c) for c in self.template_vertices[i]) for i in ring]
                out.append(RingNode(ring_id(pid, ri), pid, "exterior" if ri == 0 else "interior",
                                    tuple(coords + coords[:1])))
            polys.append(PolygonNode(pid, out[0], tuple(out[1:])))
        own[key] = GeometryNode(
            gml_type="ImplicitGeometry", polygons=tuple(polys), points=(self.world(g["boundaries"][0]),),
            extra={"relative_gml_type": "MultiSurface", "relative_gml_id": "",
                   "transformation_matrix": [float(x) for x in g["transformationMatrix"]],
                   "cityjson_lod": lod_text})

    def geometry(self, g, gi, n, key, lod_text, gml_id, where, own, surfaces, loose_openings, classes):
        _check_geometry(g, len(self.vertices), where)
        if g["type"] == "Solid":
            shells = g["boundaries"]
            if len(shells) > 1:
                self.warn(f"{where}: Solid lod {lod_text}: {len(shells) - 1} inner shell(s) not read")
                self.skipped.append(f"inner shell of {where}")
            polys = shells[0] if shells else []
            vals = (g.get("semantics") or {}).get("values")
            vals = vals[0] if vals else None
        else:
            polys = g["boundaries"]
            vals = (g.get("semantics") or {}).get("values")
        vals = vals if vals is not None else [None] * len(polys)
        sems = (g.get("semantics") or {}).get("surfaces") or []
        lodx = {"cityjson_lod": lod_text}

        parent_of = {}   # children lists as a fallback for a missing "parent"
        for si, s in enumerate(sems):
            for c in s.get("children") or ():
                if isinstance(c, int):
                    parent_of.setdefault(c, si)
        sids = []
        for si, s in enumerate(sems):
            sid = s.get("id")
            sids.append(gml_id_for(str(sid)) if isinstance(sid, str) and sid else f"{gml_id}_g{gi}_s{si}")

        by_surface: Dict[int, list] = {}
        unclassified, member_hrefs = [], []
        for pi, (rings, v) in enumerate(zip(polys, vals)):
            pid = f"{gml_id}_g{gi}_p{pi}"
            poly = self.polygon(rings, pid, where)
            if poly is None:
                continue
            stype = sems[v].get("type") if v is not None else None
            if stype in INTERIOR_SURFACE_TYPES:
                self.skipped.append(f"{stype} of {where}")
                continue
            if v is None or stype not in classes:
                if v is not None:
                    self.warn(f"{where}: semantic type {stype!r} unknown, polygon kept unclassified")
                unclassified.append(poly)
                continue
            by_surface.setdefault(v, []).append(poly)
            member_hrefs.append(f"#{pid}")

        for si, plist in by_surface.items():
            s = sems[si]
            stype, sid = s["type"], sids[si]
            # one gml:MultiSurface per surface and LoD with an id, as the GML reader gives it (CJ-02):
            # without one the 3.0 writer mints an id per polygon and splits the surface
            ms_id = f"{sid}_lod{n}"
            plist = [replace(p, multisurface_id=ms_id, is_multisurface_member=True) for p in plist]
            if stype in OPENINGS:
                host = s.get("parent", parent_of.get(si))
                host_ok = host is not None and sems[host].get("type") in SURFACE_TYPES
                host_id = sids[host] if host_ok else ""
                extra: Dict[str, Any] = {}
                if sid != s.get("id", sid):
                    extra["cgml3_cityjson_id"] = s["id"]
                node = SpaceNode(
                    gml_id=sid, feature_type=OPENINGS[stype], source_role="fillingSurface",
                    schema_version="3.0", source_element=OPENINGS[stype], parent_id=host_id or gml_id,
                    host_id=host_id, attrs=_attributes(s, extra, {}), extra=extra,
                    geometries={lod_key(n, "MultiSurface"): GeometryNode(
                        gml_type="MultiSurface", gml_id=ms_id, polygons=tuple(plist), extra=dict(lodx))})
                if host_ok:
                    self._surface(surfaces, sid=host_id, s=sems[host])["openings"].append(node)
                else:
                    self.warn(f"{where}: {stype} {sid!r} has no host surface, kept on the object")
                    loose_openings.append(node)
                continue
            self._surface(surfaces, sid, s)["geometries"][lod_key(n, "MultiSurface")] = GeometryNode(
                gml_type="MultiSurface", gml_id=ms_id, polygons=tuple(plist), extra=dict(lodx))

        if g["type"] == "Solid":
            ex = dict(lodx, member_hrefs=member_hrefs) if member_hrefs else dict(lodx)
            own[key] = GeometryNode(gml_type="Solid", polygons=tuple(unclassified), extra=ex)
        elif unclassified:
            own[key] = GeometryNode(gml_type=g["type"], polygons=tuple(unclassified), extra=dict(lodx))

    @staticmethod
    def _surface(surfaces: dict, sid: str, s: dict) -> dict:
        if sid not in surfaces:
            extra: Dict[str, Any] = {}
            if isinstance(s.get("id"), str) and s["id"] != sid:
                extra["cgml3_cityjson_id"] = s["id"]
            rest = {k: v for k, v in s.items() if k not in ("type", "id", "parent", "children")}
            surfaces[sid] = {"type": s["type"], "geometries": {}, "openings": [], "extra": extra,
                             "attrs": _attributes({"attributes": rest}, extra, thematic_table(s["type"]))}
        return surfaces[sid]


def read_document(doc: dict, warnings: Optional[list] = None, skipped: Optional[list] = None,
                  lod: Optional[str] = None) -> List[SpaceNode]:
    """IR roots (Buildings) of a CityJSON document; raises ``CityJSONError`` when it is broken.

    ``skipped`` receives what was not read (out of scope); ``lod`` keeps only the
    geometries whose ``lod`` text equals it (e.g. "2.2")."""
    check_document(doc)
    r = _Reader(doc, warnings, skipped, lod)
    out, seen, other = [], set(), {}
    for oid, o in doc["CityObjects"].items():
        if not isinstance(o, dict) or not isinstance(o.get("type"), str):
            raise CityJSONError(f"CityObject {oid!r} has no type")
        if any(p in doc["CityObjects"] for p in o.get("parents") or ()):
            continue   # read through its parent's "children"
        if o.get("parents"):
            r.warn(f"{o['type']} {oid!r}: parents not in CityObjects, read as a root")
        if o["type"] in ROOT_TYPES:
            seen.add(oid)
            out.append(r.city_object(oid, "", seen))
        else:
            other[o["type"]] = other.get(o["type"], 0) + 1
    r.skipped.extend(f"{n} {t}" for t, n in sorted(other.items()))
    return out
