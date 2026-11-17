# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
CityGML 3 shaped IR: spaces, thematic surfaces, constructive elements and the
city-object properties every feature can carry (ticket T11a).

``shared/semantic_tree.py`` describes one Building as boundary surfaces,
openings and installations, with one integer LoD per surface. This module is
the general form the shared reader (T11c/T11d) will produce for CityGML 1.0,
2.0 and 3.0 alike:

- ``SpaceNode`` - an ``AbstractSpace`` (Building, BuildingPart, Storey,
  BuildingRoom, BuildingUnit, BuildingInstallation, BuildingFurniture,
  Window/Door, the Bridge/Tunnel counterparts, ...). ``space_type`` is the
  3.0 occupied/unoccupied split.
- ``ThematicSurfaceNode`` - an ``AbstractThematicSurface`` (WallSurface,
  RoofSurface, ...). Its openings are ``SpaceNode`` objects, because in 3.0 a
  Window/Door is a filling *element* (a space), not a surface.
- ``ConstructiveElementNode`` - BuildingConstructiveElement and the
  Bridge/Tunnel counterparts.
- ``CityObjectAttrs`` - names, description, lifespan, typed generic
  attributes, external references, CityObjectRelations, addresses and
  appearance references.

LoD belongs to the geometry property, not the feature: ``geometries`` maps a
property name such as ``"lod2Solid"`` or ``"lod0MultiSurface"`` to a
``GeometryNode``. CityGML 2.0 property names (``lod4*``, ``lod0FootPrint``,
``lod0RoofEdge``, ``lodNGeometry``) are accepted and flagged as legacy, and
2.0-only feature types (``IntBuildingInstallation``, ``Room``, ...) are kept
as read and flagged through ``CITYGML2_CONCEPTS``. Converting them to the 3.0
concept is the 2.0 reader's job (T11c), not this module's.

Serialization follows ``shared/authoring_core/domain/records.py``:

- ``to_dict``/``from_dict`` give plain JSON; keys a node does not know are
  kept in ``extra`` and written back unchanged;
- a document (``ir_to_dict``) carries ``ir_version``. Older documents are
  upgraded by ``MIGRATIONS``; version 1 is the ``BuildingNode.to_dict()``
  form of ``semantic_tree`` (no ``ir_version`` key). A newer, unreadable or
  missing version is rejected with ``IRError``: unlike a stored record, an
  IR document is never written back by an older add-on;
- validation is separate (``validate`` returns a list of problems), so a
  damaged tree can still be loaded and reported.

Nodes are frozen dataclasses; ``geometries`` and ``extra`` are dicts, so the
nodes compare by value but are not hashable. Plain data only: no ``bpy``, no
XML library.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, Mapping, NamedTuple, Tuple

try:
    from .semantic_tree import BuildingNode, PolygonNode, RingNode
    from .authoring_core.domain.ids import is_valid_gml_id
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.semantic_tree import BuildingNode, PolygonNode, RingNode
    from shared.authoring_core.domain.ids import is_valid_gml_id


IR_VERSION = 2


class IRError(ValueError):
    """An IR dict cannot be read at all (wrong type, unreadable version)."""


# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------

SPACE_TYPES = ("", "occupied", "unoccupied")

GENERIC_ATTRIBUTE_TYPES = ("string", "int", "double", "date", "uri", "measure", "code", "set")

# Geometry property kinds of CityGML 3.0 (AbstractSpace, AbstractThematicSurface,
# AbstractConstruction) after the ``lodN`` prefix.
GEOMETRY_KINDS = (
    "Point", "MultiCurve", "MultiSurface", "Solid",
    "TerrainIntersectionCurve", "ImplicitRepresentation",
)
# CityGML 2.0 property kinds that 3.0 no longer has.
CITYGML2_GEOMETRY_KINDS = ("FootPrint", "RoofEdge", "Geometry")

_LOD_KEY = re.compile(r"^lod([0-4])([A-Z][A-Za-z]*)$")


class ConceptUpgrade(NamedTuple):
    """How a CityGML 2.0-only feature type maps onto CityGML 3.0."""

    target: str     # 3.0 feature type
    interior: bool  # the 2.0 type encoded "inside the building"


# 2.0 feature types without a 3.0 class of the same name. The IR keeps the
# 2.0 name as read; T11c rewrites it to ``target`` (citydb-tool does the same).
CITYGML2_CONCEPTS = {
    "IntBuildingInstallation": ConceptUpgrade("BuildingInstallation", True),
    "IntBridgeInstallation": ConceptUpgrade("BridgeInstallation", True),
    "IntTunnelInstallation": ConceptUpgrade("TunnelInstallation", True),
    "Room": ConceptUpgrade("BuildingRoom", True),
    "BridgeConstructionElement": ConceptUpgrade("BridgeConstructiveElement", False),
}


def lod_key(lod: int, kind: str) -> str:
    """``lod_key(2, "Solid") == "lod2Solid"``; raises ``IRError`` if invalid."""

    key = f"lod{lod}{kind}"
    parse_lod_key(key)
    return key


def parse_lod_key(key: str) -> Tuple[int, str]:
    """``"lod2Solid" -> (2, "Solid")``; raises ``IRError`` for anything else."""

    match = _LOD_KEY.match(key) if isinstance(key, str) else None
    if not match or match.group(2) not in GEOMETRY_KINDS + CITYGML2_GEOMETRY_KINDS:
        raise IRError(f"invalid LoD geometry key {key!r}")
    return int(match.group(1)), match.group(2)


def is_legacy_lod_key(key: str) -> bool:
    """True for a CityGML 2.0-only property: any ``lod4*``, ``lod0FootPrint``, ..."""

    lod, kind = parse_lod_key(key)
    return lod == 4 or kind in CITYGML2_GEOMETRY_KINDS


def lod3_equivalent(key: str) -> str:
    """The 3.0 key a ``lod4*`` property becomes under citydb-tool's
    ``use_lod4_as_lod3`` (``"lod4Solid" -> "lod3Solid"``). Other keys are
    returned unchanged. Only a name mapping; T11c moves the geometry."""

    lod, kind = parse_lod_key(key)
    return f"lod3{kind}" if lod == 4 else key


def citygml2_upgrade(feature_type: str) -> ConceptUpgrade | None:
    return CITYGML2_CONCEPTS.get(feature_type)


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _coords(value: Any) -> tuple:
    return tuple(tuple(float(c) for c in point) for point in (value or ()))


class _Codec(NamedTuple):
    encode: Any
    decode: Any


_TEXT = _Codec(lambda v: v, _text)
_BOOL = _Codec(lambda v: v, bool)
_RAW = _Codec(copy.deepcopy, copy.deepcopy)
_TEXTS = _Codec(list, lambda v: tuple(_text(x) for x in (v or ())))
_COORDS = _Codec(lambda v: [list(p) for p in v], _coords)
_CURVES = _Codec(lambda v: [[list(p) for p in c] for c in v],
                 lambda v: tuple(_coords(c) for c in (v or ())))


def _node(cls) -> _Codec:
    return _Codec(lambda v: v.to_dict(), lambda v: cls.from_dict(v or {}))


def _nodes(cls) -> _Codec:
    return _Codec(lambda v: [n.to_dict() for n in v],
                  lambda v: tuple(cls.from_dict(n) for n in (v or ())))


def _dict_of(cls) -> _Codec:
    return _Codec(lambda v: {k: n.to_dict() for k, n in v.items()},
                  lambda v: {_text(k): cls.from_dict(n) for k, n in dict(v or {}).items()})


def _lazy(name: str, many: bool) -> _Codec:
    """Codec for a node class defined further down (recursive trees)."""

    def encode(v):
        return [n.to_dict() for n in v] if many else v.to_dict()

    def decode(v):
        cls = globals()[name]
        return tuple(cls.from_dict(n) for n in (v or ())) if many else cls.from_dict(v or {})

    return _Codec(encode, decode)


class _Node:
    """``to_dict``/``from_dict`` driven by ``_CODECS`` {field: codec}; unknown
    keys go to ``extra`` and come back out unchanged."""

    _CODECS: Mapping[str, _Codec] = {}

    def to_dict(self) -> dict:
        out = copy.deepcopy(self.extra)
        for f in fields(self):
            if f.name != "extra":
                out[f.name] = self._CODECS.get(f.name, _RAW).encode(getattr(self, f.name))
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]):
        if not isinstance(data, Mapping):
            raise IRError(f"{cls.__name__}: expected a dict, got {type(data).__name__}")
        known = {f.name for f in fields(cls)} - {"extra"}
        values = {k: cls._CODECS.get(k, _RAW).decode(v) for k, v in data.items() if k in known}
        # A missing required field (gml_id, ...) reads as empty; validate() reports it.
        for f in fields(cls):
            if f.name not in values and f.name != "extra" and f.default is MISSING \
                    and f.default_factory is MISSING:
                values[f.name] = cls._CODECS.get(f.name, _RAW).decode(None)
        extra = copy.deepcopy({k: v for k, v in data.items() if k not in known})
        return cls(**values, extra=extra)


# PolygonNode/RingNode come from semantic_tree unchanged (no ``extra`` there).
_POLYGONS = _Codec(lambda v: [p.to_dict() for p in v],
                   lambda v: tuple(PolygonNode.from_dict(p) for p in (v or ())))
_RINGS = _Codec(lambda v: [r.to_dict() for r in v],
                lambda v: tuple(RingNode.from_dict(r) for r in (v or ())))


# ---------------------------------------------------------------------------
# city-object properties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GenericAttribute(_Node):
    """One typed generic attribute. ``type`` is one of
    ``GENERIC_ATTRIBUTE_TYPES``; a ``set`` holds ``members`` and no value.
    ``uom`` belongs to ``measure``, ``code_space`` to ``code``."""

    name: str
    type: str = "string"
    value: Any = None
    uom: str = ""
    code_space: str = ""
    members: Tuple["GenericAttribute", ...] = ()
    extra: dict = field(default_factory=dict)

    _CODECS = {"name": _TEXT, "type": _TEXT, "uom": _TEXT, "code_space": _TEXT,
               "members": _lazy("GenericAttribute", True)}


def generic_attributes_from_flat(values: Mapping[str, Any]) -> Tuple[GenericAttribute, ...]:
    """Typed attributes from today's untyped ``{name: value}`` dict
    (``shared/attribute_utils.py``): int -> ``int``, float -> ``double``,
    everything else ``string``. The flat form has no date/uri/measure/set."""

    out = []
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append(GenericAttribute(_text(name), "string", _text(value)))
        elif isinstance(value, int):
            out.append(GenericAttribute(_text(name), "int", value))
        else:
            out.append(GenericAttribute(_text(name), "double", value))
    return tuple(out)


@dataclass(frozen=True)
class ExternalReference(_Node):
    """3.0 ``core:ExternalReference``; 2.0's ``externalObject`` name/uri
    becomes ``target_resource``."""

    target_resource: str
    information_system: str = ""
    relation_type: str = ""
    extra: dict = field(default_factory=dict)

    _CODECS = {"target_resource": _TEXT, "information_system": _TEXT, "relation_type": _TEXT}


@dataclass(frozen=True)
class CityObjectRelation(_Node):
    """3.0 ``core:CityObjectRelation``: a typed link to another city object."""

    related_to: str
    relation_type: str = ""
    attributes: Tuple[GenericAttribute, ...] = ()
    extra: dict = field(default_factory=dict)

    _CODECS = {"related_to": _TEXT, "relation_type": _TEXT,
               "attributes": _nodes(GenericAttribute)}


@dataclass(frozen=True)
class Address(_Node):
    """A postal address in plain fields; ``xal`` keeps the verbatim xAL
    fragment when the source had more structure than these fields."""

    gml_id: str = ""
    street: str = ""
    house_number: str = ""
    postal_code: str = ""
    locality: str = ""
    administrative_area: str = ""
    country: str = ""
    multi_point: Tuple[Tuple[float, float, float], ...] = ()
    xal: str = ""
    extra: dict = field(default_factory=dict)

    _CODECS = {"multi_point": _COORDS, **{k: _TEXT for k in (
        "gml_id", "street", "house_number", "postal_code", "locality",
        "administrative_area", "country", "xal")}}


@dataclass(frozen=True)
class CityObjectAttrs(_Node):
    """Properties every city object can carry."""

    names: Tuple[str, ...] = ()
    description: str = ""
    creation_date: str = ""
    termination_date: str = ""
    generic_attributes: Tuple[GenericAttribute, ...] = ()
    external_references: Tuple[ExternalReference, ...] = ()
    relations: Tuple[CityObjectRelation, ...] = ()
    addresses: Tuple[Address, ...] = ()
    appearance_refs: Tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    _CODECS = {
        "names": _TEXTS, "description": _TEXT, "creation_date": _TEXT,
        "termination_date": _TEXT, "appearance_refs": _TEXTS,
        "generic_attributes": _nodes(GenericAttribute),
        "external_references": _nodes(ExternalReference),
        "relations": _nodes(CityObjectRelation),
        "addresses": _nodes(Address),
    }


# ---------------------------------------------------------------------------
# geometry and features
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeometryNode(_Node):
    """The value of one LoD geometry property. ``gml_type`` is the GML
    element (``MultiSurface``, ``CompositeSurface``, ``Solid``, ...);
    ``href`` is an unresolved xlink instead of inline geometry. Coordinates
    are as read (no CRS/origin transform)."""

    gml_id: str = ""
    gml_type: str = ""
    polygons: Tuple[PolygonNode, ...] = ()
    curves: Tuple[Tuple[Tuple[float, float, float], ...], ...] = ()
    points: Tuple[Tuple[float, float, float], ...] = ()
    href: str = ""
    extra: dict = field(default_factory=dict)

    _CODECS = {"gml_id": _TEXT, "gml_type": _TEXT, "href": _TEXT,
               "polygons": _POLYGONS, "curves": _CURVES, "points": _COORDS}


_FEATURE_TEXT = ("gml_id", "schema_version", "source_element", "parent_id", "lod_origin")


@dataclass(frozen=True)
class ThematicSurfaceNode(_Node):
    """An ``AbstractThematicSurface`` bounding a space. ``ring_refs`` are the
    schema-neutral ring records of ``semantic_tree.SurfaceNode.rings``."""

    gml_id: str
    surface_type: str
    geometries: dict = field(default_factory=dict)   # {lod key: GeometryNode}
    openings: Tuple["SpaceNode", ...] = ()
    ring_refs: Tuple[RingNode, ...] = ()
    schema_version: str = ""
    source_element: str = ""
    parent_id: str = ""
    lod_origin: str = ""
    attrs: CityObjectAttrs = field(default_factory=CityObjectAttrs)
    extra: dict = field(default_factory=dict)

    _CODECS = {
        "surface_type": _TEXT, "geometries": _dict_of(GeometryNode),
        "openings": _lazy("SpaceNode", True), "ring_refs": _RINGS,
        "attrs": _node(CityObjectAttrs), **{k: _TEXT for k in _FEATURE_TEXT},
    }


@dataclass(frozen=True)
class ConstructiveElementNode(_Node):
    """BuildingConstructiveElement (and the Bridge/Tunnel counterparts);
    ``openings`` are its filling elements (Window/Door spaces)."""

    gml_id: str
    feature_type: str = "BuildingConstructiveElement"
    geometries: dict = field(default_factory=dict)
    boundaries: Tuple[ThematicSurfaceNode, ...] = ()
    openings: Tuple["SpaceNode", ...] = ()
    schema_version: str = ""
    source_element: str = ""
    parent_id: str = ""
    lod_origin: str = ""
    attrs: CityObjectAttrs = field(default_factory=CityObjectAttrs)
    extra: dict = field(default_factory=dict)

    _CODECS = {
        "feature_type": _TEXT, "geometries": _dict_of(GeometryNode),
        "boundaries": _nodes(ThematicSurfaceNode), "openings": _lazy("SpaceNode", True),
        "attrs": _node(CityObjectAttrs), **{k: _TEXT for k in _FEATURE_TEXT},
    }


@dataclass(frozen=True)
class SpaceNode(_Node):
    """An ``AbstractSpace`` and everything it owns.

    ``subtype`` is the resolved semantic subtype (``Chimney``, ``Balcony``;
    ``semantic_tree``'s ``semantic_type``). ``interior`` marks a space that
    CityGML 2.0 encoded inside the building (``IntBuildingInstallation``,
    ``interiorRoom``). ``host_id``/``opening_link``/``ring_refs`` are only
    used by Window/Door spaces, mirroring ``semantic_tree.OpeningNode``.
    """

    gml_id: str
    feature_type: str
    space_type: str = ""
    subtype: str = ""
    subtype_origin: str = ""
    source_role: str = ""
    schema_version: str = ""
    source_element: str = ""
    interior: bool = False
    hierarchy_model: str = ""
    geometries: dict = field(default_factory=dict)   # {lod key: GeometryNode}
    boundaries: Tuple[ThematicSurfaceNode, ...] = ()
    spaces: Tuple["SpaceNode", ...] = ()
    constructive_elements: Tuple[ConstructiveElementNode, ...] = ()
    parent_id: str = ""
    host_id: str = ""
    opening_link: str = ""
    ring_refs: Tuple[RingNode, ...] = ()
    lod_origin: str = ""
    attrs: CityObjectAttrs = field(default_factory=CityObjectAttrs)
    extra: dict = field(default_factory=dict)

    _CODECS = {
        **{k: _TEXT for k in _FEATURE_TEXT + (
            "feature_type", "space_type", "subtype", "subtype_origin", "source_role",
            "hierarchy_model", "host_id", "opening_link")},
        "interior": _BOOL, "geometries": _dict_of(GeometryNode),
        "boundaries": _nodes(ThematicSurfaceNode), "spaces": _lazy("SpaceNode", True),
        "constructive_elements": _nodes(ConstructiveElementNode),
        "ring_refs": _RINGS, "attrs": _node(CityObjectAttrs),
    }

    @property
    def is_citygml2_concept(self) -> bool:
        return self.feature_type in CITYGML2_CONCEPTS

    def upgrade_target(self) -> ConceptUpgrade | None:
        return citygml2_upgrade(self.feature_type)


def iter_features(node: Any):
    """Depth-first: ``node`` and every space, surface and constructive
    element below it."""

    yield node
    for name in ("boundaries", "openings", "spaces", "constructive_elements"):
        for child in getattr(node, name, ()):
            yield from iter_features(child)


# ---------------------------------------------------------------------------
# documents, versions, migrations
# ---------------------------------------------------------------------------

def ir_to_dict(root: SpaceNode) -> dict:
    return {"ir_version": IR_VERSION, "root": root.to_dict()}


def _document_version(data: Mapping[str, Any]) -> int:
    if "ir_version" not in data:
        # Version 1 has no envelope: it is a bare BuildingNode.to_dict().
        if "boundary_surfaces" in data or "installations" in data:
            return 1
        raise IRError("IR document without ir_version")
    version = data["ir_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise IRError(f"unreadable ir_version {version!r}")
    if version > IR_VERSION:
        raise IRError(f"ir_version {version} is newer than supported ({IR_VERSION})")
    return version


def migrate(data: Any) -> dict:
    """Upgrade an IR document to ``IR_VERSION``."""

    if not isinstance(data, Mapping):
        raise IRError(f"IR document: expected a dict, got {type(data).__name__}")
    version = _document_version(data)
    data = copy.deepcopy(dict(data))
    while version < IR_VERSION:
        if version not in MIGRATIONS:
            raise IRError(f"no migration from ir_version {version}")
        data = MIGRATIONS[version](data)
        version += 1
        data["ir_version"] = version
    return data


def ir_from_dict(data: Any) -> SpaceNode:
    data = migrate(data)
    if not isinstance(data.get("root"), Mapping):
        raise IRError("IR document without a root space")
    return SpaceNode.from_dict(data["root"])


# --- version 1: semantic_tree.BuildingNode.to_dict() -----------------------

_V1_POLYGONS = "v1_polygons"   # extra key: polygons whose surface had no LoD
_V1_LOD = "v1_lod"             # extra key: a LoD outside 0..4


def _v1_geometries(item: Mapping[str, Any], extra: dict) -> dict:
    lod, polygons = item.get("lod"), list(item.get("polygons") or [])
    if lod is None:
        if polygons:
            extra[_V1_POLYGONS] = polygons
        return {}
    if isinstance(lod, bool) or not isinstance(lod, int) or not 0 <= lod <= 4:
        extra[_V1_LOD] = lod
        if polygons:
            extra[_V1_POLYGONS] = polygons
        return {}
    return {f"lod{lod}MultiSurface": {"gml_type": "MultiSurface", "polygons": polygons}}


def _v1_opening(item: Mapping[str, Any]) -> dict:
    extra: dict = {}
    out = {
        "gml_id": item.get("gml_id"),
        "feature_type": item.get("semantic_type"),
        "space_type": "occupied",
        "source_role": item.get("source_role"),
        "source_element": item.get("source_element"),
        "schema_version": item.get("schema_version"),
        "subtype_origin": item.get("subtype_origin"),
        "attrs": {"appearance_refs": item.get("appearance_refs") or []},
        "ring_refs": item.get("rings") or [],
        "geometries": _v1_geometries(item, extra),
        "lod_origin": item.get("lod_origin"),
        "host_id": item.get("host_id"),
        "parent_id": item.get("parent_id"),
        "opening_link": item.get("opening_link"),
    }
    return {**extra, **out}


def _v1_surface(item: Mapping[str, Any]) -> dict:
    extra: dict = {}
    out = {
        "gml_id": item.get("gml_id"),
        "surface_type": item.get("semantic_type"),
        "schema_version": item.get("schema_version"),
        "attrs": {"appearance_refs": item.get("appearance_refs") or []},
        "ring_refs": item.get("rings") or [],
        "openings": [_v1_opening(o) for o in item.get("openings") or []],
        "geometries": _v1_geometries(item, extra),
        "lod_origin": item.get("lod_origin"),
        "parent_id": item.get("parent_id"),
    }
    return {**extra, **out}


def _v1_installation(item: Mapping[str, Any]) -> dict:
    source = _text(item.get("source_element"))
    local = source.split(":", 1)[-1]
    feature_type = local if local in CITYGML2_CONCEPTS else "BuildingInstallation"
    return {
        "gml_id": item.get("gml_id"),
        "feature_type": feature_type,
        "space_type": "occupied",
        "subtype": item.get("semantic_type"),
        "source_role": item.get("source_role"),
        "source_element": source,
        "schema_version": item.get("schema_version"),
        "subtype_origin": item.get("subtype_origin"),
        "interior": _text(item.get("source_role")) == "interior" or local in CITYGML2_CONCEPTS,
        "attrs": {"appearance_refs": item.get("appearance_refs") or []},
    }


def _migrate_v1(data: Mapping[str, Any]) -> dict:
    root = {
        "gml_id": data.get("gml_id"),
        "feature_type": "Building",   # v1 did not tell Building from BuildingPart
        "space_type": "occupied",
        "hierarchy_model": data.get("hierarchy_model"),
        "boundaries": [_v1_surface(s) for s in data.get("boundary_surfaces") or []],
        "spaces": [_v1_installation(i) for i in data.get("installations") or []],
    }
    known = {"gml_id", "hierarchy_model", "boundary_surfaces", "installations"}
    root.update({k: v for k, v in data.items() if k not in known})
    return {"root": root}


# {from_version: fn(dict) -> dict at from_version + 1}
MIGRATIONS = {1: _migrate_v1}


def space_from_building_node(building: BuildingNode) -> SpaceNode:
    """The ``semantic_tree`` Building as a ``SpaceNode`` (via migration 1)."""

    return ir_from_dict(building.to_dict())


def _v1_lod_polygons(node: Any) -> Tuple[Any, list]:
    if _V1_LOD in node.extra or _V1_POLYGONS in node.extra:
        return node.extra.get(_V1_LOD), copy.deepcopy(node.extra.get(_V1_POLYGONS, []))
    if not node.geometries:
        return None, []
    if len(node.geometries) != 1:
        raise IRError(f"{node.gml_id!r}: {len(node.geometries)} geometries cannot be written as v1")
    key, geometry = next(iter(node.geometries.items()))
    lod, kind = parse_lod_key(key)
    if kind != "MultiSurface":
        raise IRError(f"{node.gml_id!r}: {key} cannot be written as v1")
    return lod, [p.to_dict() for p in geometry.polygons]


def building_node_dict(space: SpaceNode) -> dict:
    """Inverse of migration 1: the ``BuildingNode.to_dict()`` form of a
    space that only uses what v1 can express. Raises ``IRError`` otherwise."""

    def opening(o: SpaceNode) -> dict:
        lod, polygons = _v1_lod_polygons(o)
        return {
            "gml_id": o.gml_id, "semantic_type": o.feature_type,
            "source_role": o.source_role, "source_element": o.source_element,
            "schema_version": o.schema_version, "subtype_origin": o.subtype_origin,
            "appearance_refs": list(o.attrs.appearance_refs),
            "rings": [r.to_dict() for r in o.ring_refs],
            "lod": lod, "lod_origin": o.lod_origin, "host_id": o.host_id,
            "parent_id": o.parent_id, "opening_link": o.opening_link, "polygons": polygons,
        }

    def surface(s: ThematicSurfaceNode) -> dict:
        lod, polygons = _v1_lod_polygons(s)
        return {
            "gml_id": s.gml_id, "semantic_type": s.surface_type,
            "schema_version": s.schema_version,
            "appearance_refs": list(s.attrs.appearance_refs),
            "rings": [r.to_dict() for r in s.ring_refs],
            "openings": [opening(o) for o in s.openings],
            "lod": lod, "lod_origin": s.lod_origin, "parent_id": s.parent_id,
            "polygons": polygons,
        }

    def installation(i: SpaceNode) -> dict:
        return {
            "gml_id": i.gml_id, "semantic_type": i.subtype,
            "source_role": i.source_role, "source_element": i.source_element,
            "schema_version": i.schema_version, "subtype_origin": i.subtype_origin,
            "appearance_refs": list(i.attrs.appearance_refs),
        }

    return {
        "gml_id": space.gml_id,
        "hierarchy_model": space.hierarchy_model,
        "boundary_surfaces": [surface(s) for s in space.boundaries],
        "installations": [installation(i) for i in space.spaces],
    }


# ---------------------------------------------------------------------------
# validation and reports
# ---------------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def validate_generic_attribute(attr: GenericAttribute, where: str) -> list:
    where = f"{where} attribute {attr.name!r}"
    problems = []
    if not attr.name:
        problems.append(f"{where}: empty name")
    if attr.type not in GENERIC_ATTRIBUTE_TYPES:
        return problems + [f"{where}: type {attr.type!r} not in {GENERIC_ATTRIBUTE_TYPES}"]
    if attr.type == "set":
        if attr.value is not None:
            problems.append(f"{where}: a set has members, not a value")
        for member in attr.members:
            problems += validate_generic_attribute(member, where)
        return problems
    if attr.members:
        problems.append(f"{where}: only a set has members")
    if attr.type == "int" and (isinstance(attr.value, bool) or not isinstance(attr.value, int)):
        problems.append(f"{where}: int value {attr.value!r}")
    elif attr.type in ("double", "measure") and not _is_number(attr.value):
        problems.append(f"{where}: {attr.type} value {attr.value!r}")
    elif attr.type in ("string", "date", "uri", "code") and not isinstance(attr.value, str):
        problems.append(f"{where}: {attr.type} value {attr.value!r} is not text")
    return problems


def _validate_feature(node: Any) -> list:
    where = f"{type(node).__name__} {node.gml_id!r}"
    problems = []
    if not is_valid_gml_id(node.gml_id):
        problems.append(f"{where}: invalid or empty gml_id")
    for key, geometry in node.geometries.items():
        try:
            parse_lod_key(key)
        except IRError as exc:
            problems.append(f"{where}: {exc}")
        if not isinstance(geometry, GeometryNode):
            problems.append(f"{where}: {key} is not a GeometryNode")
    if isinstance(node, SpaceNode):
        if not node.feature_type:
            problems.append(f"{where}: empty feature_type")
        if node.space_type not in SPACE_TYPES:
            problems.append(f"{where}: space_type {node.space_type!r} not in {SPACE_TYPES}")
    if isinstance(node, ThematicSurfaceNode) and not node.surface_type:
        problems.append(f"{where}: empty surface_type")
    for attr in node.attrs.generic_attributes:
        problems += validate_generic_attribute(attr, where)
    for ref in node.attrs.external_references:
        if not ref.target_resource:
            problems.append(f"{where}: external reference without target_resource")
    for rel in node.attrs.relations:
        if not rel.related_to:
            problems.append(f"{where}: relation without related_to")
        for attr in rel.attributes:
            problems += validate_generic_attribute(attr, f"{where} relation")
    return problems


def validate(root: SpaceNode) -> list:
    """Problems of ``root`` and everything below it; ``[]`` when valid.
    2.0 concepts and legacy LoD keys are not problems (see the reports)."""

    problems, seen = [], set()
    for node in iter_features(root):
        problems += _validate_feature(node)
        if node.gml_id in seen:
            problems.append(f"{type(node).__name__} {node.gml_id!r}: duplicate gml_id")
        seen.add(node.gml_id)
    return problems


def citygml2_concepts(root: SpaceNode) -> list:
    """``[(gml_id, feature_type, ConceptUpgrade)]`` for every 2.0-only type."""

    return [(n.gml_id, n.feature_type, CITYGML2_CONCEPTS[n.feature_type])
            for n in iter_features(root)
            if getattr(n, "feature_type", "") in CITYGML2_CONCEPTS]


def legacy_lod_keys(root: SpaceNode) -> list:
    """``[(gml_id, key)]`` for every 2.0-only geometry property (``lod4*``, ...)."""

    out = []
    for node in iter_features(root):
        for key in node.geometries:
            try:
                if is_legacy_lod_key(key):
                    out.append((node.gml_id, key))
            except IRError:
                pass   # reported by validate()
    return out
