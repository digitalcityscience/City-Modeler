# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/citygml_reader.py
"""CityGML 1.0 / 2.0 / 3.0 XML -> ``SpaceNode`` IR (T11c). No bpy.

One walker for all versions; the version only changes property names:

=====================  ==============================  ============================
                       CityGML 1.0 / 2.0               CityGML 3.0
=====================  ==============================  ============================
thematic surface       ``bldg:boundedBy``              ``core:boundary``
Window / Door          ``bldg:opening``                ``con:filling``
child spaces           ``consistsOfBuildingPart``,     ``buildingPart``,
                       ``outer/interiorBuilding-``     ``buildingInstallation``,
                       ``Installation``, ...           ``buildingRoom``, ...
=====================  ==============================  ============================

1.0 namespaces are read as 2.0 (``citygml_upgrade.namespace_1_to_2``); a tree
read from 1.0/2.0 is then upgraded to 3.0 concepts (``upgrade_citygml2``).
Coordinates come from ``shared/gml_geometry.py`` as read (no CRS/origin).

xlinks are resolved against the document's ``gml:id`` table: a geometry
property that only points elsewhere gets the target geometry (and
``extra["resolved_href"]``); member references inside a geometry
(``extra["member_hrefs"]``, e.g. a Solid made of boundary polygons) are only
checked. An unresolvable reference is a warning, never an error.

Thematic attributes (``bldg:class``, ``function``, ``roofType``,
``storeysAboveGround``, ``measuredHeight``, ... any child of a CityGML module
that is a plain text element) are structured in ``extra["thematic_attributes"]``:
``{"bldg:function": [{"value": "1000", "code_space": "..."}], ...}``. The key is
the element's local name behind a fixed module prefix (``_PREFIXES``), the same
in 1.0/2.0/3.0; values are a list in document order (the element may repeat),
typed per element (``_INT``, ``_FLOAT``, ``_BOOL``, ``_FLOAT_LIST``; text
otherwise). ``uom`` and ``code_space`` are kept when present. The IR codec
stays at its version: ``extra`` is part of every node's dict form.

Appearances: ``attrs.appearance_refs`` lists the ``gml:id`` of every
``app:Appearance`` that targets the feature (its own id or one of its own
polygons/geometries; document level and nested) plus the ones nested in it.
Content (textures, materials) is not read here; a nested ``app:appearance`` is
also kept verbatim in ``unknown_elements``.

Feature-level xlinks (``opening xlink:href``, ``consistsOfBuildingPart
xlink:href``) never copy the target: the target feature is read once, where it
is defined; the referencing node lists ``extra["feature_hrefs"] =
[{"role", "href", "target_id", "target_type"}]``. A target that is missing or is
not a feature is a warning and has no ``target_id``.

Anything else the IR has no field for (the ``gml:boundedBy`` envelope, ADE
elements, ...) is kept verbatim as XML text in ``extra["unknown_elements"]``.
Features without ``gml:id`` get ``<parent id>_<element>_<n>`` and
``extra["generated_id"] = True``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import Dict, List, Optional

try:
    from .citygml_ir import (Address, CityObjectAttrs, ConstructiveElementNode,
                             ExternalReference, GenericAttribute, GeometryNode,
                             SpaceNode, ThematicSurfaceNode)
    from .citygml_upgrade import namespace_1_to_2, upgrade_citygml2
    from .gml_geometry import GEOMETRY_TYPES, GML_NAMESPACES, parse_geometry
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.citygml_ir import (Address, CityObjectAttrs, ConstructiveElementNode,
                                   ExternalReference, GenericAttribute, GeometryNode,
                                   SpaceNode, ThematicSurfaceNode)
    from shared.citygml_upgrade import namespace_1_to_2, upgrade_citygml2
    from shared.gml_geometry import GEOMETRY_TYPES, GML_NAMESPACES, parse_geometry

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_GML_IDS = tuple(f"{{{ns}}}id" for ns in GML_NAMESPACES)
_CITYGML = "http://www.opengis.net/citygml"
_LOD_PROPERTY = re.compile(r"^lod[0-4][A-Z]\w*$")
_VERSION = re.compile(r"/(\d\.\d)$")

_BOUNDARY_ROLES = {"boundedBy", "boundary"}
_OPENING_ROLES = {"opening", "filling", "fillingSurface"}
_SPACE_ROLES = {
    # 1.0 / 2.0
    "consistsOfBuildingPart", "outerBuildingInstallation", "interiorBuildingInstallation",
    "interiorRoom", "interiorFurniture", "roomInstallation",
    "consistsOfBridgePart", "outerBridgeInstallation", "interiorBridgeInstallation",
    "interiorBridgeRoom", "outerBridgeConstruction", "bridgeRoomInstallation",
    "consistsOfTunnelPart", "outerTunnelInstallation", "interiorTunnelInstallation",
    "interiorHollowSpace", "hollowSpaceInstallation",
    # 3.0
    "buildingPart", "buildingInstallation", "buildingRoom", "buildingFurniture",
    "buildingConstructiveElement", "buildingSubdivision",
    "bridgePart", "bridgeInstallation", "bridgeRoom", "bridgeFurniture",
    "bridgeConstructiveElement",
    "tunnelPart", "tunnelInstallation", "hollowSpace", "tunnelFurniture",
    "tunnelConstructiveElement",
    # transportation (2.0 Road/Railway/Track/Square own TrafficAreas; 3.0 adds sections and spaces)
    "trafficArea", "auxiliaryTrafficArea", "section", "intersection",
    "trafficSpace", "auxiliaryTrafficSpace",
}
_MEMBER_ROLES = {"cityObjectMember", "featureMember"}
# Namespace module (URI segment before the version) -> conventional prefix.
_PREFIXES = {"citygml": "core", "building": "bldg", "bridge": "brid", "tunnel": "tun",
             "landuse": "luse", "transportation": "tran", "vegetation": "veg",
             "cityfurniture": "frn", "waterbody": "wtr", "relief": "dem",
             "generics": "gen", "cityobjectgroup": "grp"}
_INT = {"storeysAboveGround", "storeysBelowGround", "yearOfConstruction", "yearOfDemolition"}
_FLOAT = {"measuredHeight"}
_BOOL = {"isMovable"}
_FLOAT_LIST = {"storeyHeightsAboveGround", "storeyHeightsBelowGround"}
_ATTRIBUTE_TYPES_2 = {"stringAttribute": "string", "intAttribute": "int",
                      "doubleAttribute": "double", "dateAttribute": "date",
                      "uriAttribute": "uri", "measureAttribute": "measure",
                      "genericAttributeSet": "set"}
_ATTRIBUTE_TYPES_3 = {"StringAttribute": "string", "IntAttribute": "int",
                      "DoubleAttribute": "double", "DateAttribute": "date",
                      "UriAttribute": "uri", "MeasureAttribute": "measure",
                      "CodeAttribute": "code", "GenericAttributeSet": "set"}
_XAL_FIELDS = {"ThoroughfareName": "street", "ThoroughfareNumber": "house_number",
               "PostalCodeNumber": "postal_code", "LocalityName": "locality",
               "AdministrativeAreaName": "administrative_area", "CountryName": "country"}


def _split(el):
    """(namespace as read, 2.0 namespace for 1.0, local name)."""
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, namespace_1_to_2(ns), local
    return "", "", tag


def _elements(el):
    return [c for c in el if isinstance(c.tag, str)]


def _gml_id(el) -> str:
    for key in _GML_IDS:
        value = el.get(key)
        if value:
            return value
    return ""


def _is_feature(el) -> bool:
    ns, _, local = _split(el)
    return ns.startswith(_CITYGML) and local[:1].isupper()


def _module(ns: str) -> str:
    parts = ns.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def _version(ns: str) -> str:
    match = _VERSION.search(ns)
    return match.group(1) if match else ""


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _child(el, *locals_):
    return next((c for c in _elements(el) if _split(c)[2] in locals_), None)


def _xml(el) -> str:
    text = ET.tostring(el, encoding="unicode")
    return text[: len(text) - len(el.tail)] if el.tail and text.endswith(el.tail) else text


def document_ids(root) -> Dict[str, object]:
    """``{gml:id: element}`` for every element of the document."""
    ids = {}
    for el in root.iter():
        if isinstance(el.tag, str):
            gml_id = _gml_id(el)
            if gml_id:
                ids.setdefault(gml_id, el)
    return ids


def appearance_targets(root) -> Dict[str, List[str]]:
    """``{target gml:id: [gml:id of every app:Appearance targeting it]}`` over the
    whole document (``app:target`` text or ``uri``; external-file targets are
    ignored). Appearances without ``gml:id`` cannot be referenced and are skipped."""
    out: Dict[str, List[str]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        _, ns2, local = _split(el)
        app_id = _gml_id(el)
        if local != "Appearance" or _module(ns2) != "appearance" or not app_id:
            continue
        for t in el.iter():
            if not isinstance(t.tag, str) or _split(t)[2] != "target":
                continue
            file_part, _, fragment = (t.get("uri") or _text(t)).partition("#")
            if fragment and not file_part and app_id not in out.setdefault(fragment, []):
                out[fragment].append(app_id)
    return out


def _first_envelope(el) -> Optional[dict]:
    """The first ``gml:Envelope`` below ``el`` (document order, any depth) as
    ``{"lower": [...], "upper": [...]}``: what the importers' bbox filter looks at.
    None when there is none or a corner is missing or not numeric."""
    for sub in el.iter():
        if not isinstance(sub.tag, str):
            continue
        ns, _, local = sub.tag[1:].partition("}") if sub.tag.startswith("{") else ("", "", sub.tag)
        if local != "Envelope" or ns not in GML_NAMESPACES:
            continue
        corners = {}
        for corner in sub.iter():
            if isinstance(corner.tag, str) and corner.tag.startswith("{")                     and corner.tag.partition("}")[0][1:] == ns:
                name = corner.tag.rpartition("}")[2]
                if name in ("lowerCorner", "upperCorner") and name not in corners:
                    corners[name] = corner
        if len(corners) < 2:
            return None
        try:
            return {"lower": [float(x) for x in (corners["lowerCorner"].text or "").split()],
                    "upper": [float(x) for x in (corners["upperCorner"].text or "").split()]}
        except ValueError:
            return None
    return None


class _Reader:
    def __init__(self, ids: Dict[str, object], warnings: Optional[list],
                 appearances: Optional[Dict[str, List[str]]] = None, envelopes: bool = False):
        self.ids = ids
        self.envelopes = envelopes
        self.warnings = warnings if warnings is not None else []
        self.appearances = appearances or {}

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # -- geometry ------------------------------------------------------------

    def _target_geometry(self, href: str):
        target = self.ids.get(href.lstrip("#")) if href.startswith("#") or "#" not in href else None
        if target is None:
            return None
        ns, _, local = _split(target)
        return target if ns in GML_NAMESPACES and local in GEOMETRY_TYPES else None

    def resolve(self, geometry: GeometryNode, where: str) -> GeometryNode:
        if geometry.href:
            target = self._target_geometry(geometry.href)
            if target is None:
                self.warn(f"{where}: unresolved xlink {geometry.href!r}")
                return geometry
            resolved = parse_geometry(target, self.warnings)
            return replace(resolved, extra={**resolved.extra, "resolved_href": geometry.href})
        missing = [h for h in geometry.extra.get("member_hrefs", ())
                   if self._target_geometry(h) is None]
        for href in missing:
            self.warn(f"{where}: unresolved xlink {href!r}")
        if missing:
            return replace(geometry, extra={**geometry.extra, "unresolved_hrefs": missing})
        return geometry

    def implicit(self, el) -> GeometryNode:
        """``core:ImplicitGeometry``: the relative geometry's polygons/curves,
        the reference point in ``points``, the rest in ``extra``."""
        rel = _child(el, "relativeGMLGeometry", "relativeGeometry")
        geometry = parse_geometry(rel, self.warnings) if rel is not None else GeometryNode()
        geometry = self.resolve(geometry, f"ImplicitGeometry {_gml_id(el)!r}")
        ref = _child(el, "referencePoint")
        points = parse_geometry(ref, self.warnings).points if ref is not None else ()
        extra = dict(geometry.extra)
        extra.update(relative_gml_type=geometry.gml_type, relative_gml_id=geometry.gml_id)
        matrix = _child(el, "transformationMatrix")
        if matrix is not None:
            try:
                extra["transformation_matrix"] = [float(v) for v in _text(matrix).split()]
            except ValueError:
                self.warn(f"ImplicitGeometry {_gml_id(el)!r}: bad transformationMatrix")
        for local, key in (("mimeType", "mime_type"), ("libraryObject", "library_object")):
            if _child(el, local) is not None:
                extra[key] = _text(_child(el, local))
        return GeometryNode(gml_id=_gml_id(el), gml_type="ImplicitGeometry",
                            polygons=geometry.polygons, curves=geometry.curves,
                            points=points, href=geometry.href, extra=extra)

    def geometry(self, prop, where: str) -> GeometryNode:
        inner = _child(prop, "ImplicitGeometry")
        if inner is not None:
            return self.implicit(inner)
        return self.resolve(parse_geometry(prop, self.warnings), f"{where} {_split(prop)[2]}")

    # -- city object properties ---------------------------------------------

    def generic_attribute(self, el, where: str) -> Optional[GenericAttribute]:
        _, _, local = _split(el)
        if local in _ATTRIBUTE_TYPES_2:          # 2.0: <gen:stringAttribute name="..">
            kind, name = _ATTRIBUTE_TYPES_2[local], el.get("name", "")
            value_el = _child(el, "value")
            members = [m for m in _elements(el) if _split(m)[2] in _ATTRIBUTE_TYPES_2]
        elif local in _ATTRIBUTE_TYPES_3:        # 3.0: <gen:StringAttribute><gen:name>
            kind, name = _ATTRIBUTE_TYPES_3[local], _text(_child(el, "name"))
            value_el = _child(el, "value")
            members = [m for c in _elements(el) if _split(c)[2] == "value"
                       for m in _elements(c) if _split(m)[2] in _ATTRIBUTE_TYPES_3]
        else:
            return None
        code_space = el.get("codeSpace", "") or _text(_child(el, "codeSpace"))
        if kind == "set":
            return GenericAttribute(name=name, type="set", code_space=code_space,
                                    members=tuple(a for a in (self.generic_attribute(m, where)
                                                              for m in members) if a))
        text, uom = _text(value_el), (value_el.get("uom", "") if value_el is not None else "")
        value = text
        try:
            if kind == "int":
                value = int(text)
            elif kind in ("double", "measure"):
                value = float(text)
        except ValueError:
            self.warn(f"{where}: generic attribute {name!r} {kind} value {text!r} kept as string")
            kind = "string"
        return GenericAttribute(name=name, type=kind, value=value, uom=uom, code_space=code_space)

    def external_reference(self, el) -> Optional[ExternalReference]:
        ref = _child(el, "ExternalReference")            # 3.0 wraps it in a feature
        ref = el if ref is None else ref
        system = _text(_child(ref, "informationSystem"))
        target = _child(ref, "targetResource")
        if target is not None:
            return ExternalReference(target_resource=_text(target), information_system=system,
                                     relation_type=_text(_child(ref, "relationType")))
        obj = _child(ref, "externalObject")               # 2.0: externalObject/(name|uri)
        inner = _elements(obj)[0] if obj is not None and _elements(obj) else None
        if inner is None:
            return None
        return ExternalReference(target_resource=_text(inner), information_system=system,
                                 extra={"external_object": _split(inner)[2]})

    def address(self, el) -> Optional[Address]:
        addr = _child(el, "Address")
        if addr is None:
            return None
        values = {}
        for node in addr.iter():
            key = _XAL_FIELDS.get(_split(node)[2]) if isinstance(node.tag, str) else None
            if key and key not in values:
                values[key] = _text(node)
        xal = _child(addr, "xalAddress")
        points = ()
        mp = _child(addr, "multiPoint")
        if mp is not None:
            points = parse_geometry(mp, self.warnings).points
        return Address(gml_id=_gml_id(addr), multi_point=points,
                       xal=_xml(_elements(xal)[0]) if xal is not None and _elements(xal) else "",
                       **values)

    def _typed(self, local: str, text: str, where: str):
        try:
            if text and local in _INT:
                return int(text)
            if text and local in _FLOAT:
                return float(text)
            if text and local in _BOOL:
                if text.lower() not in ("true", "false", "1", "0"):
                    raise ValueError(text)
                return text.lower() in ("true", "1")
            if text and local in _FLOAT_LIST:
                return [float(v) for v in text.split()]
            if local == "sortKey" and text.isdigit():
                return int(text)
        except ValueError:
            self.warn(f"{where}: {local} value {text!r} kept as string")
        return text

    def thematic(self, el, out: dict, where: str) -> bool:
        """Collect a plain text element of a CityGML module; False if it is not one."""
        _, ns2, local = _split(el)
        prefix = _PREFIXES.get(_module(ns2))
        if not prefix:
            return False
        entry = {"value": self._typed(local, _text(el), where)}
        if el.get("uom"):
            entry["uom"] = el.get("uom")
        if el.get("codeSpace"):
            entry["code_space"] = el.get("codeSpace")
        out.setdefault(f"{prefix}:{local}", []).append(entry)
        return True

    def feature_href(self, role: str, href: str, where: str) -> dict:
        entry = {"role": role, "href": href}
        target = self.ids.get(href.lstrip("#"))
        if target is None:
            self.warn(f"{where}: unresolved xlink {href!r}")
        elif not _is_feature(target):
            self.warn(f"{where}: xlink {href!r} does not point to a feature")
        else:
            entry.update(target_id=href.lstrip("#"), target_type=_split(target)[2])
        return entry

    # -- features ------------------------------------------------------------

    def feature(self, el, parent_id: str, role: str, index: int, host_id: str = ""):
        ns, ns2, local = _split(el)
        gml_id = _gml_id(el)
        extra = {}
        if not gml_id:
            gml_id = f"{parent_id or 'cgml'}_{local}_{index}"
            extra["generated_id"] = True
        if self.envelopes:
            envelope = _first_envelope(el)
            if envelope:
                extra["envelope"] = envelope
        names, description, dates = [], "", {}
        generic, refs, addresses, unknown = [], [], [], []
        geometries, boundaries, openings, spaces, constructive = {}, [], [], [], []
        feature_hrefs, thematic, nested_appearances = [], {}, []
        where = f"{local} {gml_id!r}"

        for i, child in enumerate(_elements(el)):
            cns, cns2, clocal = _split(child)
            kids = _elements(child)
            target = kids[0] if len(kids) == 1 and _is_feature(kids[0]) else None
            if cns in GML_NAMESPACES and clocal == "name":
                names.append(_text(child))
            elif cns in GML_NAMESPACES and clocal == "description":
                description = _text(child)
            elif _LOD_PROPERTY.match(clocal) and cns.startswith(_CITYGML):
                geometries[clocal] = self.geometry(child, where)
            elif clocal in ("creationDate", "terminationDate") and _module(cns2) == "citygml":
                dates[clocal] = _text(child)
            elif _module(cns2) == "generics" and clocal in _ATTRIBUTE_TYPES_2:
                attr = self.generic_attribute(child, where)
                generic.append(attr)
            elif _module(cns2) in ("generics", "citygml") and clocal == "genericAttribute" and kids:
                attr = self.generic_attribute(kids[0], where)
                if attr is not None:
                    generic.append(attr)
                else:
                    unknown.append(_xml(child))
            elif clocal == "externalReference":
                ref = self.external_reference(child)
                refs.append(ref) if ref else unknown.append(_xml(child))
            elif clocal == "address" and target is not None:
                addr = self.address(child)
                addresses.append(addr) if addr else unknown.append(_xml(child))
            elif target is not None and clocal in _BOUNDARY_ROLES:
                boundaries.append(self.surface(target, gml_id, clocal, i))
            elif target is not None and clocal in _OPENING_ROLES:
                openings.append(self.feature(target, gml_id, clocal, i, host_id=gml_id))
            elif target is not None and clocal in _SPACE_ROLES:
                node = self.feature(target, gml_id, clocal, i)
                (constructive if isinstance(node, ConstructiveElementNode) else spaces).append(node)
            elif not kids and child.get(_XLINK_HREF) and (
                    clocal in _SPACE_ROLES | _BOUNDARY_ROLES | _OPENING_ROLES):
                feature_hrefs.append(self.feature_href(clocal, child.get(_XLINK_HREF), where))
            elif not kids and not child.get(_XLINK_HREF) and self.thematic(child, thematic, where):
                pass
            else:
                if clocal == "appearance" and _module(cns2) == "appearance":
                    nested_appearances += [_gml_id(a) for a in kids
                                           if _split(a)[2] == "Appearance" and _gml_id(a)]
                unknown.append(_xml(child))

        if unknown:
            extra["unknown_elements"] = unknown
        if feature_hrefs:
            extra["feature_hrefs"] = feature_hrefs
        if thematic:
            extra["thematic_attributes"] = thematic
        own = {gml_id}
        for g in geometries.values():
            if "resolved_href" not in g.extra:
                own.update(filter(None, [g.gml_id, *(p.polygon_id for p in g.polygons)]))
        appearance_refs = sorted({a for i in own for a in self.appearances.get(i, ())}
                                 | set(nested_appearances))
        attrs = CityObjectAttrs(appearance_refs=tuple(appearance_refs),names=tuple(names), description=description,
                                creation_date=dates.get("creationDate", ""),
                                termination_date=dates.get("terminationDate", ""),
                                generic_attributes=tuple(generic),
                                external_references=tuple(refs), addresses=tuple(addresses))
        common = dict(gml_id=gml_id, geometries=geometries, boundaries=tuple(boundaries),
                      schema_version=_version(ns), source_element=local, parent_id=parent_id,
                      attrs=attrs, extra=extra)
        if local.endswith(("ConstructiveElement", "ConstructionElement")):
            return ConstructiveElementNode(feature_type=local, openings=tuple(openings), **common)
        if openings:   # a space never owns openings directly; keep them as children
            spaces = list(openings) + spaces
        return SpaceNode(feature_type=local, source_role=role, spaces=tuple(spaces),
                         constructive_elements=tuple(constructive), host_id=host_id,
                         interior=role.startswith("interior"), **common)

    def surface(self, el, parent_id: str, role: str, index: int) -> ThematicSurfaceNode:
        node = self.feature(el, parent_id, role, index)
        openings = tuple(s for s in node.spaces if s.source_role in _OPENING_ROLES)
        rest = tuple(s for s in node.spaces if s.source_role not in _OPENING_ROLES)
        extra = dict(node.extra)
        if rest or node.boundaries or node.constructive_elements:
            extra["unexpected_children"] = [c.to_dict() for c in rest + node.boundaries
                                            + node.constructive_elements]
            self.warn(f"{node.feature_type} {node.gml_id!r}: children other than openings "
                      "kept in extra['unexpected_children']")
        return ThematicSurfaceNode(gml_id=node.gml_id, surface_type=node.feature_type,
                                   geometries=node.geometries, openings=openings,
                                   schema_version=node.schema_version,
                                   source_element=node.source_element, parent_id=parent_id,
                                   attrs=node.attrs, extra=extra)


def read_feature(el, warnings: Optional[list] = None, report: Optional[list] = None,
                 ids: Optional[dict] = None, upgrade: bool = True,
                 appearances: Optional[dict] = None, envelopes: bool = False):
    """IR of one city object element. A 1.0/2.0 feature is upgraded to 3.0
    concepts unless ``upgrade`` is False. ``ids`` / ``appearances`` default to
    what is below ``el``; pass ``document_ids(root)`` / ``appearance_targets(root)``
    to resolve xlinks and appearance targets across the file."""
    reader = _Reader(ids if ids is not None else document_ids(el), warnings,
                     appearances if appearances is not None else appearance_targets(el),
                     envelopes)
    node = reader.feature(el, "", "", 0)
    if upgrade and node.schema_version in ("1.0", "2.0"):
        node = upgrade_citygml2(node, report)
    return node


read_citygml2 = read_feature


def read_document(root, warnings: Optional[list] = None, report: Optional[list] = None,
                  upgrade: bool = True, envelopes: bool = False) -> List[object]:
    """IR roots of every ``cityObjectMember`` / ``featureMember`` of a
    CityModel (or of ``root`` itself when it is a city object)."""
    ids, appearances = document_ids(root), appearance_targets(root)
    if _split(root)[2] != "CityModel":
        return [read_feature(root, warnings, report, ids, upgrade, appearances, envelopes)]
    out = []
    for member in _elements(root):
        if _split(member)[2] in _MEMBER_ROLES:
            for el in _elements(member):
                if _is_feature(el):
                    out.append(read_feature(el, warnings, report, ids, upgrade, appearances, envelopes))
            if not _elements(member) and member.get(_XLINK_HREF):
                if warnings is not None:
                    warnings.append(f"cityObjectMember by reference {member.get(_XLINK_HREF)!r} skipped")
    return out
