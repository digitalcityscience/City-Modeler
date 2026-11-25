"""IR (``SpaceNode``) -> CityJSON 2.0 / CityJSONSeq (CJ-03). No bpy.

Plan ``CITYJSON_INTEGRATION_PLAN_2026-11-17.md`` sections 3.2 and 4.1. The IR comes from the K1 pivot
(scene -> CityGML 3.0 writer -> ``citygml_reader``, ``ops/export_cityjson.scene_to_ir``) or from
``cityjson_reader``; this module is the mirror of ``cityjson_reader`` and inverts its tables
(``THEMATIC``, ``_DATES``, ``_ADDRESS``, ``OPENINGS``), so one table maps both ways.

Scope: Building, BuildingPart, BuildingInstallation, their boundary surfaces and Window/Door; Road,
Railway, Square and CityFurniture (CJ-07). Every other feature type (and interior content) is counted
in the report's ``not_written``, never written.

CJ-07, the CityJSON core profile for Transportation (K3): the core has Road, Railway and
TransportSquare (the IR's Square) and the semantic surfaces TrafficArea / AuxiliaryTrafficArea, nothing
else. A road is written as one city object with one MultiSurface per LoD: its traffic areas (wherever
they hang below it) as semantic surfaces with their id and attributes, every other polygon below it
unclassified. What has no core counterpart (Section, Intersection, TrafficSpace, AuxiliaryTrafficSpace,
...) is counted in the report's ``reduced`` with its id in ``reduced_ids``: its polygons are in the
road's geometry, the object, its id and its attributes are not in the file. A Track has no core type:
``not_written``.

CityFurniture: an ``ImplicitGeometry`` is written as ``GeometryInstance`` with its template in
``geometry-templates`` (equal templates once); the matrix translation moves into the reference point.

Geometry, per city object and integer LoD:
- one geometry with the classified polygons of its surfaces and openings plus the object's own
  unclassified ones (semantic value null); ``Solid`` when the object has ``lodNSolid``, else
  ``MultiSurface`` (``CompositeSurface`` when that is all it has);
- a second ``MultiSurface`` when the object has both ``lodNSolid`` and ``lodNMultiSurface``
  (two geometries in one LoD are kept, not merged or dropped);
- a polygon (by ``polygon_id``) is written once per geometry: a Solid repeating its surfaces inline
  does not double them;
- inner rings are kept; each semantic surface carries ``"id"`` (the original CityJSON id when the
  reader mapped one, ``extra["cgml3_cityjson_id"]``); Window/Door get ``parent`` and their host
  ``children``;
- ``lod`` is the stored text (``GeometryNode.extra["cityjson_lod"]``, K6), else the integer.
Vertices: world coordinates quantised to ``10**-precision`` m, de-duplicated; ``translate`` is the
quantised minimum.

``save`` checks the document (``validate_document``) before anything reaches the target: the file is
written next to it under a temporary name and only then renamed over it.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

try:
    from .builder_payload import address_properties, construction_properties
    from .citygml_ir import CityObjectAttrs, IRError, parse_lod_key
    from .cityjson_reader import (_ADDRESS, _DATES, FURNITURE, INSTANCE, NAME, OPENINGS, SURFACE_TYPES, THEMATIC,
                                  TRAFFIC_AREAS, TRANSPORT, CityJSONError, _check_geometry, check_document,
                                  check_instance)
except ImportError:  # pure-Python unit tests import ``shared`` as a top-level package
    from shared.builder_payload import address_properties, construction_properties
    from shared.citygml_ir import CityObjectAttrs, IRError, parse_lod_key
    from shared.cityjson_reader import (_ADDRESS, _DATES, FURNITURE, INSTANCE, NAME, OPENINGS, SURFACE_TYPES,
                                        THEMATIC, TRAFFIC_AREAS, TRANSPORT, CityJSONError, _check_geometry,
                                        check_document, check_instance)

VERSION = "2.0"
ROOT_TYPES = ("Building", "BuildingPart")
CHILD_TYPES = ("BuildingPart", "BuildingInstallation")
# CJ-07, core profile: IR feature type -> CityJSON type. Track has no city object type in the CityJSON
# 2.0 core (from the specification's type list; no schema file here to check it against): not written.
TRANSPORT_OUT = {ir: name for name, ir in TRANSPORT.items()}
TOP_TYPES = ROOT_TYPES + tuple(TRANSPORT_OUT) + (FURNITURE,)   # written as top-level city objects
_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
# the reader's tables, inverted
ATTRIBUTES_OUT = {ir: name for name, ir in THEMATIC.items()}
DATES_OUT = {field: name for name, field in _DATES.items()}
ADDRESS_OUT = {field: key for key, field in reversed(list(_ADDRESS.items()))}   # PostalCode, not PostCode
OPENINGS_OUT = {**{ir: name for name, ir in OPENINGS.items()}, "Window": "Window", "Door": "Door"}
# bldg:address:* keys of builder_payload.address_properties -> Address fields
_XAL_KEYS = {"street": "street", "streetNumber": "house_number", "postCode": "postal_code",
             "locality": "locality", "country": "country"}
_SURFACE_GEOMETRY = ("MultiSurface", "CompositeSurface", "Solid")
_RESERVED = ("type", "id", "parent", "children")
_EPSG = re.compile(r"EPSG(?:/0/|:+|/)(\d+)")
OGC_CRS = "https://www.opengis.net/def/crs/EPSG/0/{}"


def crs_url(crs: str) -> str:
    """OGC URL of ``crs`` ("EPSG:25832", a URN or URL); a compound (horizontal + vertical) code pair
    becomes an OGC ``crs-compound`` URL. '' when no EPSG code is found."""
    codes = _EPSG.findall(crs or "")
    if len(codes) == 1:
        return OGC_CRS.format(codes[0])
    if len(codes) == 2:
        return ("https://www.opengis.net/def/crs-compound?1=" + OGC_CRS.format(codes[0])
                + "&2=" + OGC_CRS.format(codes[1]))
    return ""


def original_id(node) -> str:
    """The CityJSON id of ``node``: the original when the reader had to map it (K2), else the gml:id."""
    return str(node.extra.get("cgml3_cityjson_id") or node.gml_id)


def annotate(roots, ids: Dict[str, str], lods: Dict[str, Dict[str, str]]) -> None:
    """Put what only the scene knows back on pivot IR (plan 3.2.2 Open 2), by gml:id: the original
    CityJSON ids (``ids``: gml_id -> id) and the LoD texts (``lods``: gml_id of a city object ->
    {"2": "2.2"}) of the object, its surfaces and openings. Changes the nodes' ``extra`` in place."""
    def visit(node, lod_map):
        if node.gml_id in ids:
            node.extra["cgml3_cityjson_id"] = ids[node.gml_id]
        if getattr(node, "feature_type", "") and node.feature_type not in OPENINGS_OUT:
            lod_map = lods.get(node.gml_id) or {}   # a city object: its own texts, surfaces inherit them
        for key, g in node.geometries.items():
            if len(key) > 3 and key[3] in lod_map and not g.extra.get("cityjson_lod"):
                g.extra["cityjson_lod"] = lod_map[key[3]]
        for child in (*getattr(node, "boundaries", ()), *getattr(node, "openings", ()),
                      *getattr(node, "spaces", ())):
            visit(child, lod_map)
    for r in roots:
        visit(r, None)


def _generic_value(a):
    if a.type == "set":
        return {m.name: _generic_value(m) for m in a.members}
    if a.type == "int" and isinstance(a.value, int):
        return a.value
    if a.type in ("double", "measure") and isinstance(a.value, (int, float)):
        return float(a.value)
    return "" if a.value is None else str(a.value)


def attributes(node, thematic: bool = True) -> Dict[str, Any]:
    """CityJSON ``attributes`` of a city object (or the extra keys of a semantic surface)."""
    out: Dict[str, Any] = {}
    if thematic:
        for key, entries in (node.extra.get("thematic_attributes") or {}).items():
            values = [e.get("value") for e in entries if e.get("value") not in (None, "")]
            if values:   # a name outside the table goes out under its local name (read back as generic)
                out[ATTRIBUTES_OUT.get(key) or key.rpartition(":")[2]] = values[0] if len(values) == 1 else values
        con = construction_properties(node)   # CityGML 3.0 con:height / con:dateOf... (pivot IR)
        if isinstance(con.get("con:height:value"), (int, float)):
            out.setdefault("measuredHeight", con["con:height:value"])
        for local in ("dateOfConstruction", "dateOfDemolition"):
            if con.get(f"con:{local}"):
                out.setdefault(local, con[f"con:{local}"])
    for field, name in DATES_OUT.items():
        if getattr(node.attrs, field):
            out[name] = getattr(node.attrs, field)
    if node.attrs.names:
        out.setdefault(NAME, node.attrs.names[0] if len(node.attrs.names) == 1 else list(node.attrs.names))
    for a in node.attrs.generic_attributes:
        out.setdefault(a.name, _generic_value(a))
    out.update(node.extra.get("cityjson_raw_attributes") or {})
    return out


class _Writer:
    def __init__(self, precision: int):
        self.precision = precision
        self.scale = 10.0 ** -precision
        self.keys: Dict[tuple, int] = {}    # quantised world vertex -> index
        self.objects: Dict[str, dict] = {}
        self.warnings: List[str] = []
        self.not_written: Counter = Counter()
        self.reduced: Counter = Counter()           # CJ-07: flattened into their road, by type
        self.reduced_ids: Dict[str, List[str]] = {}
        self.templates: List[dict] = []             # geometry-templates
        self.template_keys: Dict[tuple, int] = {}
        self.template_vertices: Dict[tuple, int] = {}

    def key(self, p) -> tuple:
        return tuple(int(round(c / self.scale)) for c in p[:3])

    def index(self, key: tuple) -> int:
        return self.keys.setdefault(key, len(self.keys))

    def ring(self, coords) -> list:
        out = []
        for p in coords:
            k = self.key(p)
            if not out or out[-1] != k:   # points closer than the precision collapse
                out.append(k)
        if len(out) > 1 and out[0] == out[-1]:
            out.pop()
        return out

    def polygon(self, poly, where: str) -> Optional[list]:
        ext = self.ring(poly.exterior.coords)
        if len(ext) < 3:
            self.warnings.append(f"{where}: polygon {poly.polygon_id} degenerate at the precision, not written")
            return None
        rings = [ext]
        for r in poly.interiors:
            inner = self.ring(r.coords)
            if len(inner) >= 3:
                rings.append(inner)
            else:
                self.warnings.append(f"{where}: an inner ring of {poly.polygon_id} is degenerate, not written")
        return [[self.index(k) for k in r] for r in rings]

    # -- semantics -------------------------------------------------------------------------

    def semantic_object(self, node) -> dict:
        ftype = getattr(node, "feature_type", "")    # a traffic area is a space (2.0) or a boundary (3.0)
        stype = OPENINGS_OUT.get(ftype) or getattr(node, "surface_type", "") or ftype
        obj = {"type": stype, "id": original_id(node)}
        for k, v in attributes(node, thematic=stype in TRAFFIC_AREAS).items():
            if k not in _RESERVED:
                obj[k] = v
        return obj

    def geometry(self, gtype: str, lod: str, items, where: str) -> Optional[dict]:
        """``items``: ``[(polygon, (surface or opening node, host node or None) or None)]``."""
        sems, index, values, boundaries, seen = [], {}, [], [], set()

        def sem(entry) -> int:
            node, host = entry
            if node.gml_id not in index:
                obj = self.semantic_object(node)
                hi = index.get(host.gml_id) if host is not None else None
                if hi is not None:
                    obj["parent"] = hi
                    sems[hi].setdefault("children", []).append(len(sems))
                elif host is not None:
                    self.warnings.append(f"{where}: {obj['type']} {obj['id']!r}: host has no polygon "
                                         f"in LoD {lod}, written without parent")
                index[node.gml_id] = len(sems)
                sems.append(obj)
            return index[node.gml_id]

        for poly, entry in items:
            if poly.polygon_id and poly.polygon_id in seen:
                continue
            rings = self.polygon(poly, where)
            if rings is None:
                continue
            seen.add(poly.polygon_id)
            boundaries.append(rings)
            values.append(sem(entry) if entry else None)
        if not boundaries:
            return None
        solid = gtype == "Solid"
        g = {"type": gtype, "lod": lod, "boundaries": [boundaries] if solid else boundaries}
        if sems:
            g["semantics"] = {"surfaces": sems, "values": [values] if solid else values}
        return g

    def _slot(self, by_lod: dict, key: str, owner, where: str):
        try:
            n, kind = parse_lod_key(key)
        except IRError:
            n, kind = -1, key
        if kind not in _SURFACE_GEOMETRY or not 0 <= n <= 3:
            self.not_written[f"{key} geometry"] += 1
            self.warnings.append(f"{where}: {key} of {owner.gml_id} not written (CityJSON writer: "
                                 "surface geometry in LoD 0-3 only)")
            return None, None
        return by_lod.setdefault(n, {"own": {}, "items": [], "plain": [], "lod": []}), kind

    def instance(self, key: str, g, where: str) -> Optional[dict]:
        """``GeometryInstance`` of an IR ImplicitGeometry; its template goes to ``geometry-templates``."""
        try:
            n, _kind = parse_lod_key(key)
        except IRError:
            n = -1
        if not 0 <= n <= 3 or not g.polygons:
            self.not_written[f"{key} geometry"] += 1
            self.warnings.append(f"{where}: {key} not written (no template polygons, or not LoD 0-3)")
            return None
        m = [float(x) for x in g.extra.get("transformation_matrix") or ()]
        if len(m) != 16:
            if m:
                self.warnings.append(f"{where}: {key}: transformation matrix of {len(m)} values, identity written")
            m = list(_IDENTITY)
        p = tuple(g.points[0]) + (0.0,) * 3 if g.points else (0.0, 0.0, 0.0)
        # placement = M * v + reference point: the translation of M goes into the point (CityJSON places
        # the template's origin there), so a matrix-only placement does not put a vertex at 0, 0, 0
        ref = (p[0] + m[3], p[1] + m[7], p[2] + m[11])
        m[3] = m[7] = m[11] = 0.0
        if g.extra.get("relative_gml_type") not in (None, "", "MultiSurface", "CompositeSurface"):
            self.warnings.append(f"{where}: {key}: {g.extra['relative_gml_type']} template written as MultiSurface")
        lod = str(g.extra.get("cityjson_lod") or n)
        rings = []
        for poly in g.polygons:
            prings = []
            for r in (poly.exterior, *poly.interiors):
                pts = [tuple(round(float(c), 6) for c in pt[:3]) for pt in r.coords]
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts.pop()
                prings.append(tuple(pts))
            rings.append(tuple(prings))
        tkey = (lod, tuple(rings))
        if tkey not in self.template_keys:
            self.template_keys[tkey] = len(self.templates)
            self.templates.append({"type": "MultiSurface", "lod": lod, "boundaries": [
                [[self.template_vertices.setdefault(pt, len(self.template_vertices)) for pt in ring]
                 for ring in poly] for poly in rings]})
        return {"type": INSTANCE, "template": self.template_keys[tkey],
                "boundaries": [self.index(self.key(ref))], "transformationMatrix": m}

    @staticmethod
    def _lod(s: dict, n: int) -> str:
        return next((t for t in s["lod"] if t.split(".")[0] == str(n)), str(n))

    def flat_geometries(self, node, where: str) -> list:
        """CJ-07 core profile: one MultiSurface per LoD for a road with everything below it (module text)."""
        by_lod: Dict[int, dict] = {}

        def visit(n, top: bool):
            kind = getattr(n, "feature_type", "") or getattr(n, "surface_type", "")
            area = kind in TRAFFIC_AREAS
            if not top and not area:
                self.reduced[kind] += 1
                self.reduced_ids.setdefault(kind, []).append(n.gml_id)
            for key, g in n.geometries.items():
                s, _kind = self._slot(by_lod, key, n, where)
                if s is not None:
                    s["items" if area else "plain"] += [(p, (n, None) if area else None) for p in g.polygons]
                    if g.extra.get("cityjson_lod"):
                        s["lod"].append(str(g.extra["cityjson_lod"]))
            for child in (*getattr(n, "boundaries", ()), *getattr(n, "spaces", ()),
                          *getattr(n, "constructive_elements", ())):
                visit(child, False)

        visit(node, True)
        out = []
        for n in sorted(by_lod):
            s = by_lod[n]
            lod = self._lod(s, n)
            # the classified polygons first: a road repeating its areas' polygons keeps their class
            g = self.geometry("MultiSurface", lod, s["items"] + s["plain"], f"{where} LoD {lod}")
            if g is not None:
                out.append(g)
        return out

    def geometries(self, node, where: str) -> list:
        by_lod: Dict[int, dict] = {}
        instances = []

        def slot(key, owner):
            return self._slot(by_lod, key, owner, where)

        def lod_text(s, g):
            text = str(g.extra.get("cityjson_lod") or "")
            if text:
                s["lod"].append(text)

        for key, g in node.geometries.items():
            if g.gml_type == "ImplicitGeometry":    # CJ-07: a CityFurniture placed by a template
                inst = self.instance(key, g, where)
                if inst is not None:
                    instances.append(inst)
                continue
            s, kind = slot(key, node)
            if s is not None:
                s["own"][kind] = g
                lod_text(s, g)
        openings = [(o, None) for o in node.spaces if o.feature_type in OPENINGS_OUT]
        for b in node.boundaries:
            if b.surface_type not in SURFACE_TYPES:
                self.not_written[b.surface_type] += 1
                continue
            for key, g in b.geometries.items():
                s, _kind = slot(key, b)
                if s is not None:
                    s["items"] += [(p, (b, None)) for p in g.polygons]
                    lod_text(s, g)
            openings += [(o, b) for o in b.openings]
        for o, host in openings:
            for key, g in o.geometries.items():
                s, _kind = slot(key, o)
                if s is not None:
                    s["items"] += [(p, (o, host)) for p in g.polygons]
                    lod_text(s, g)

        out = []
        for n in sorted(by_lod):
            s = by_lod[n]
            lod = self._lod(s, n)
            own = s["own"]
            gtype = "Solid" if "Solid" in own else (
                "CompositeSurface" if "CompositeSurface" in own and "MultiSurface" not in own else "MultiSurface")
            # the object's own polygons; with a Solid its MultiSurface is the second geometry below
            kinds = ("Solid",) if gtype == "Solid" else ("MultiSurface", "CompositeSurface")
            inline = [(p, None) for kind in kinds if kind in own for p in own[kind].polygons]
            g = self.geometry(gtype, lod, s["items"] + inline, f"{where} LoD {lod}")
            if g is not None:
                out.append(g)
            if gtype == "Solid" and own.get("MultiSurface") is not None and own["MultiSurface"].polygons:
                # a second geometry in the same LoD: its polygons keep the class they have in the first
                by_pid = {p.polygon_id: e for p, e in s["items"] if p.polygon_id}
                second = self.geometry("MultiSurface", lod, [(p, by_pid.get(p.polygon_id))
                                                             for p in own["MultiSurface"].polygons],
                                       f"{where} LoD {lod} (second geometry)")
                if second is not None:
                    out.append(second)
        return out + instances

    # -- city objects ----------------------------------------------------------------------

    def address(self, a) -> dict:
        fields = {f: getattr(a, f) for f in ADDRESS_OUT if getattr(a, f)}
        if not fields and a.xal:   # a CityGML 3.0 xAL address: the reader keeps only the fragment
            props = address_properties(CityObjectAttrs(addresses=(replace(a, multi_point=()),)), "3.0")
            fields = {f: str(props[f"bldg:address:{k}"]) for k, f in _XAL_KEYS.items()
                      if props.get(f"bldg:address:{k}") not in (None, "")}
        out = {ADDRESS_OUT[f]: v for f, v in fields.items()}
        out.update(a.extra.get("cityjson") or {})
        if a.multi_point:
            out["location"] = {"type": "MultiPoint", "lod": "1",
                               "boundaries": [self.index(self.key(p)) for p in a.multi_point]}
        return out

    def addresses(self, node) -> list:
        """The object's addresses, then those of its doors (a B81 entrance door's address belongs to
        the building in CityJSON, plan 4.1), each once."""
        out = []
        doors = [o for b in node.boundaries for o in b.openings] + [o for o in node.spaces
                                                                   if o.feature_type in OPENINGS_OUT]
        for a in (*node.attrs.addresses, *(a for d in doors for a in d.attrs.addresses)):
            d = self.address(a)
            if d and d not in out:
                out.append(d)
        return out

    def city_object(self, node, parent: str = "") -> str:
        cid = original_id(node)
        if cid in self.objects:
            raise CityJSONError(f"two city objects with the id {cid!r}")
        where = f"{node.feature_type} {cid!r}"
        obj: Dict[str, Any] = {"type": TRANSPORT_OUT.get(node.feature_type, node.feature_type)}
        self.objects[cid] = obj
        attrs = attributes(node)
        if attrs:
            obj["attributes"] = attrs
        if node.feature_type in ROOT_TYPES:
            adr = self.addresses(node)
            if adr:
                obj["address"] = adr
        if node.attrs.external_references or node.attrs.relations:
            self.warnings.append(f"{where}: external references / relations not written")
        if node.feature_type in TRANSPORT_OUT:   # everything below a road is flattened into it
            obj["geometry"] = self.flat_geometries(node, where)
            return cid
        obj["geometry"] = self.geometries(node, where)
        if parent:
            obj["parents"] = [parent]
        children = []
        for s in node.spaces:
            if s.feature_type in OPENINGS_OUT:
                continue   # written as semantic surfaces of this object
            if s.feature_type in CHILD_TYPES and not s.interior:
                children.append(self.city_object(s, cid))
            else:
                self.not_written[("interior " if s.interior else "") + s.feature_type] += 1
        for c in node.constructive_elements:
            self.not_written[c.feature_type] += 1
        if children:
            obj["children"] = children
        return cid

    def document(self, crs: str) -> dict:
        keys = list(self.keys)
        if keys:
            low = [min(k[i] for k in keys) for i in range(3)]
            high = [max(k[i] for k in keys) for i in range(3)]
        else:
            low = high = [0, 0, 0]
        p = self.precision
        meta: Dict[str, Any] = {}
        url = crs_url(crs)
        if url:
            meta["referenceSystem"] = url
        elif keys:
            self.warnings.append(f"no EPSG code in the scene CRS {crs!r}: metadata.referenceSystem not written")
        if keys:
            meta["geographicalExtent"] = [round(c * self.scale, p) for c in (*low, *high)]
        doc = {"type": "CityJSON", "version": VERSION,
               "transform": {"scale": [self.scale] * 3, "translate": [round(c * self.scale, p) for c in low]},
               "metadata": meta, "CityObjects": self.objects,
               "vertices": [[k[0] - low[0], k[1] - low[1], k[2] - low[2]] for k in keys]}
        if self.templates:
            doc["geometry-templates"] = {"templates": self.templates,
                                         "vertices-templates": [list(v) for v in self.template_vertices]}
        return doc


def write_document(roots, crs: str = "", precision: int = 3) -> Tuple[dict, dict]:
    """``(document, report)``. ``report``: ``written`` (top-level ids), ``objects`` (count),
    ``not_written`` ({kind: count}), ``reduced`` ({type: count} flattened into their road, CJ-07) with
    ``reduced_ids`` ({type: [gml ids]}), ``warnings``. Raises ``CityJSONError`` on duplicate ids."""
    w = _Writer(int(precision))
    written = []
    for r in roots:
        if r.feature_type in TOP_TYPES:
            written.append(w.city_object(r))
        else:
            w.not_written[r.feature_type] += 1
    doc = w.document(crs)
    return doc, {"written": written, "objects": len(w.objects), "not_written": dict(w.not_written),
                 "reduced": dict(w.reduced), "reduced_ids": w.reduced_ids, "warnings": w.warnings}


def loss_notes(report: dict) -> List[str]:
    """What the export summary tells the user about ``report``: one text per kind of loss, with counts."""
    notes = []
    if report.get("reduced"):
        notes.append("CityJSON core profile, flattened into their Road/Railway/TransportSquare (surfaces kept, "
                     "the object, its id and attributes not written): "
                     + ", ".join(f"{n} {k}" for k, n in sorted(report["reduced"].items())))
    if report.get("not_written"):
        notes.append("not written (no CityJSON core type, or outside Building/Transportation/CityFurniture): "
                     + ", ".join(f"{n} {k}" for k, n in sorted(report["not_written"].items())))
    return notes


# -- CityJSONSeq ---------------------------------------------------------------------------------

def _remap(b, mapping: dict) -> list:
    return [_remap(x, mapping) if isinstance(x, list) else mapping.setdefault(x, len(mapping)) for x in b]


def features(doc: dict) -> Tuple[dict, List[dict]]:
    """``(header, [CityJSONFeature])`` of ``doc``: one feature per top-level object with its children
    and its own vertices; the header keeps transform and metadata."""
    header = {k: v for k, v in doc.items() if k not in ("CityObjects", "vertices")}
    header.update(CityObjects={}, vertices=[])
    objects, out = doc["CityObjects"], []
    for rid, root in objects.items():
        if root.get("parents"):
            continue
        ids, stack = [], [rid]
        while stack:
            oid = stack.pop(0)
            ids.append(oid)
            stack += objects[oid].get("children") or []
        mapping: Dict[int, int] = {}
        objs = {}
        for oid in ids:
            o = json.loads(json.dumps(objects[oid]))   # deep copy
            for g in o.get("geometry") or ():
                g["boundaries"] = _remap(g["boundaries"], mapping)
            for a in o.get("address") or ():
                if isinstance(a.get("location"), dict):
                    a["location"]["boundaries"] = _remap(a["location"]["boundaries"], mapping)
            objs[oid] = o
        verts = [None] * len(mapping)
        for old, new in mapping.items():
            verts[new] = doc["vertices"][old]
        out.append({"type": "CityJSONFeature", "id": rid, "CityObjects": objs, "vertices": verts})
    return header, out


# -- self-check ----------------------------------------------------------------------------------

def validate_document(doc: dict) -> List[str]:
    """Structural problems of a CityJSON document ([] = none): the reader's checks (version, transform,
    integer vertices, vertex index range, semantic value depth / length / range), plus non-empty
    geometries, semantic parent/children symmetry and parents/children symmetry between objects."""
    problems: List[str] = []
    try:
        check_document(doc)
    except CityJSONError as e:
        return [str(e)]
    if doc.get("version") != VERSION:
        problems.append(f"version {doc.get('version')!r}, the writer writes {VERSION}")
    objects, nv = doc["CityObjects"], len(doc["vertices"])
    gt = doc.get("geometry-templates") or {}
    templates, nt = gt.get("templates") or [], len(gt.get("vertices-templates") or ())
    for ti, t in enumerate(templates):
        if not isinstance(t, dict) or t.get("type") not in _SURFACE_GEOMETRY or not t.get("boundaries") \
                or not isinstance(t.get("lod"), str) or not t["lod"]:
            problems.append(f"template {ti}: not a non-empty surface geometry with a lod text")
            continue
        try:
            _check_geometry(t, nt, f"template {ti}")
        except CityJSONError as e:
            problems.append(str(e))
    for oid, o in objects.items():
        where = f"{o.get('type')} {oid!r}"
        if not isinstance(o.get("type"), str):
            problems.append(f"{where}: no type")
        for g in o.get("geometry") or ():
            if g.get("type") == INSTANCE:
                try:
                    check_instance(g, templates, nv, nt, where)
                except CityJSONError as e:
                    problems.append(str(e))
                continue
            if g.get("type") not in _SURFACE_GEOMETRY:
                problems.append(f"{where}: geometry type {g.get('type')!r}")
                continue
            if not isinstance(g.get("lod"), str) or not g["lod"]:
                problems.append(f"{where}: geometry without lod text")
            if not g.get("boundaries") or (g["type"] == "Solid" and not all(g["boundaries"])):
                problems.append(f"{where}: empty {g['type']} (lod {g.get('lod')!r})")
                continue
            try:
                _check_geometry(g, nv, where)
            except CityJSONError as e:
                problems.append(str(e))
                continue
            sems = (g.get("semantics") or {}).get("surfaces") or []
            for i, s in enumerate(sems):
                p = s.get("parent")
                if p is not None and i not in (sems[p].get("children") or ()):
                    problems.append(f"{where}: semantic surface {i} names parent {p}, which does not list it")
                for c in s.get("children") or ():
                    if not (isinstance(c, int) and 0 <= c < len(sems)) or sems[c].get("parent") != i:
                        problems.append(f"{where}: semantic surface {i} lists child {c!r} without parent {i}")
        for c in o.get("children") or ():
            if c not in objects or oid not in (objects[c].get("parents") or ()):
                problems.append(f"{where}: child {c!r} missing or does not name it as parent")
        for p in o.get("parents") or ():
            if p not in objects or oid not in (objects[p].get("children") or ()):
                problems.append(f"{where}: parent {p!r} missing or does not list it as child")
        for a in o.get("address") or ():
            for i in ((a.get("location") or {}).get("boundaries") or ()) if isinstance(a, dict) else ():
                if not (isinstance(i, int) and 0 <= i < nv):
                    problems.append(f"{where}: address location index {i!r} out of range")
    return problems


def validate_feature(header: dict, feature: dict) -> List[str]:
    if feature.get("type") != "CityJSONFeature" or feature.get("id") not in (feature.get("CityObjects") or {}):
        return [f"line {feature.get('id')!r}: not a CityJSONFeature with its id among its CityObjects"]
    doc = dict(header, CityObjects=feature["CityObjects"], vertices=feature.get("vertices"))
    return [f"feature {feature['id']!r}: {p}" for p in validate_document(doc)]


def save(path: str, doc: dict, seq: bool = False) -> None:
    """Self-check ``doc``, write it (``seq``: CityJSONSeq, one CityJSONFeature per top-level object)
    to a temporary file beside ``path`` and rename it over ``path``. On any problem ``CityJSONError``
    is raised and ``path`` is left as it was."""
    problems = validate_document(doc)
    if seq:
        header, feats = features(doc)
        for f in feats:
            problems += validate_feature(header, f)
    if problems:
        raise CityJSONError("CityJSON self-check failed, nothing written: " + "; ".join(problems[:5])
                            + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))
    tmp = f"{path}.cj03-{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            if seq:
                for line in (header, *feats):
                    f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
            else:
                json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
