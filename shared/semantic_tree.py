# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Schema-neutral semantic IR for a Building's boundary surfaces, openings,
installations and rings (ticket 05 of docs/agent/semantic-hierarchy).

This module is plain data plus a builder. It imports no ``bpy`` and no XML
library, and the tree it produces never carries a ``bldg:``/``con:`` element
name - only the neutral facts (canonical semantic type, schema-neutral
source role, schema version, verbatim source-element string for provenance).
Per-schema element names are a *rendering* concern for the writers in later
tickets (06/07/10/11), not a fact this IR stores.

Traversal and ring bookkeeping are written once here so two ~6,000-line
exporters don't grow their own, divergent copies - see
``docs/agent/semantic-hierarchy/README.md`` decision 8.

Ring ownership (plan decision 5, investigation section 1.3): an interior ring
is intrinsic to the polygon that owns it, not a function of that polygon's
children. ``SurfaceNode`` and ``OpeningNode`` therefore each carry their own
``rings`` tuple, populated straight from the object's stored ring records.
The builder never infers or copies a ring from a child object into its
parent, and never invents one - the RoofSurface/chimney-penetration case is a
ring on a surface with zero child openings.

CAC-P5.1 (docs/agent/authoring-core/PLAN.md §6 Phase 5) extends this module
with record-shaped geometry (``PolygonNode``, committed ``coords`` on
``RingNode``, ``lod``/``lod_origin`` on ``SurfaceNode``/``OpeningNode``) so a
record-backed Building can be expressed as this same IR instead of only the
schema-neutral semantic facts the original ticket-05 tree carried. Three
responsibility boundaries fixed by that plan and enforced here by *not*
doing them in this module:

- **Canonicalization (D-3) stays a no-op here.** ``PolygonRecord.polygon_id``
  and ``RingRecord.ring_id`` are already the canonical, exporter-assigned
  ids by the time a record exists (``shared/authoring_core/blender/
  usecases.py::create_feature`` commits them that way); this module and the
  P5.2 ``ir_to_groups`` stage both carry them through unchanged. Nothing in
  this repository renumbers ring/polygon ids on the record-backed export
  path - that is intentional, not an oversight, and no future change here
  should reintroduce renumbering without updating this note.
- **Coordinates are carried unchanged.** ``RingNode.coords`` is a direct,
  untransformed copy of ``RingRecord.coords`` - already Blender-world-frame,
  already "committed" (ADR 0009 D-2). CRS/origin transformation is an
  export-time concern for a later stage (``ir_to_groups``, P5.2+), never
  this module.
- **Ordering is the builder's job, not the dataclass's.** ``PolygonNode``,
  ``RingNode`` and ``OpeningNode`` tuples carry whatever order they are
  constructed with - a frozen dataclass cannot enforce order on its own.
  Any code that builds one of these tuples from an unordered source (a
  ``RecordSet``, a ``dict``, Blender's own child iteration) must sort by the
  matching ``*_sort_key`` function below first, so two independently-built
  trees for the same logical data compare equal regardless of iteration
  order. See "Ordering" below.

Blender object contract the builder walks (duck-typed - see
``shared/lod_helpers.py`` for the same pattern applied to LoD bookkeeping):

- ``obj["gml_id"]`` - the feature's ``gml:id``.
- ``obj["cgml3_feature"]`` - existing, unchanged property (111 references
  across the repo) carrying the verbatim source feature type, e.g.
  ``"WallSurface"`` or ``"bldg:WallSurface"``. The IR strips any namespace
  prefix before storing it, since the IR itself must stay schema-neutral.
- ``obj["cgml_semantic_type"]`` / ``obj["cgml_source_role"]`` /
  ``obj["cgml_source_element"]`` / ``obj["cgml_schema_version"]`` /
  ``obj["cgml_subtype_origin"]`` - the ticket-04 property model, present on
  openings and installations.
- ``obj["cgml_hierarchy_model"]`` - on the Building, ``"semantic_objects"``
  or ``"legacy_mesh"`` (decision 9).
- ``obj["cgml_rings"]`` - optional, a JSON string or a plain list of
  ``{"ring_id": ..., "polygon_id": ...}`` records stored on the surface or
  opening that owns them. Never computed from children.
- ``obj["cgml_appearance_refs"]`` - optional, a JSON string or plain list of
  appearance ``gml:id`` strings referencing the object's geometry.
- ``obj["structure_part"]`` - optional. When present, ``"opening"`` and
  ``"installation"`` are trusted outright as an explicit classification -
  these are pre-existing values already set by the importers (e.g.
  ``io_v2/reader/importer.py`` lines 809/927). There is no explicit
  ``structure_part`` value for a boundary surface; those are always
  recognized by ``cgml3_feature`` (below).
- ``obj.children`` - child objects (parent edges the tree structure follows).
- ``obj.get(key, default)`` - custom-property lookup; any mapping-like or
  ``bpy_struct``-like object satisfies this.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping, Tuple

try:
    from .object_helpers import safe_get as _safe_get
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.object_helpers import safe_get as _safe_get


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _strip_schema_prefix(value: Any) -> str:
    """Drop a ``bldg:``/``con:``/... namespace prefix, if present.

    The IR is schema-neutral (module docstring); a verbatim, prefixed source
    element name belongs in ``source_element`` for provenance, never here.
    """

    text = _text(value)
    if ":" in text:
        return text.split(":", 1)[1]
    return text


def _read_json_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return list(parsed) if isinstance(parsed, (list, tuple)) else []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _iter_children(obj: Any) -> Iterable[Any]:
    try:
        return list(getattr(obj, "children", []) or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# IR node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RingNode:
    """A ring (exterior or interior) belonging to one polygon.

    ``coords`` (CAC-P5.1) is a direct, untransformed copy of a committed
    ``RingRecord.coords`` - world-frame, no CRS/origin transform, no
    renumbering (see the module docstring). Empty for a ring built from the
    schema-neutral ticket-05 path, which never carried geometry.
    ``opening_link`` (CAC-P5.1) mirrors ``RingRecord.opening_link``: set only
    on an interior ring that pairs with a hosted opening, matching that
    opening's ``OpeningNode.opening_link`` - the explicit host relation
    (never a geometry/ring-shape comparison, PLAN.md P5.0 decision).
    """

    ring_id: str
    polygon_id: str = ""
    role: str = "exterior"
    coords: Tuple[Tuple[float, float, float], ...] = ()
    opening_link: str = ""

    def to_dict(self) -> dict:
        return {
            "ring_id": self.ring_id,
            "polygon_id": self.polygon_id,
            "role": self.role,
            "coords": [list(c) for c in self.coords],
            "opening_link": self.opening_link,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RingNode":
        return cls(
            ring_id=_text(data.get("ring_id")),
            polygon_id=_text(data.get("polygon_id")),
            role=_text(data.get("role")) or "exterior",
            coords=tuple(tuple(float(v) for v in c) for c in data.get("coords", []) or []),
            opening_link=_text(data.get("opening_link")),
        )


def ring_sort_key(node: "RingNode") -> str:
    """Canonical, deterministic ordering for a tuple of ``RingNode``.

    By ``ring_id`` - already the canonical id (D-3) by the time a
    record-backed ring exists, so this is a plain string sort, not a
    semantic decision of its own.
    """

    return node.ring_id


@dataclass(frozen=True)
class PolygonNode:
    """One committed polygon: one exterior ring plus zero or more interior
    rings (CAC-P5.1).

    Mirrors ``shared/authoring_core/domain/records.py::PolygonRecord``
    field-for-field where a field is meaningful to the IR
    (``geometry_state``/``extra`` stay a record-storage concern, not
    carried into the IR). ``exterior`` is mandatory - a polygon with no
    exterior ring is not a valid polygon, so unlike every other field here
    it has no default.
    """

    polygon_id: str
    exterior: RingNode
    interiors: Tuple[RingNode, ...] = ()
    multisurface_id: str = ""
    compositesurface_id: str = ""
    is_multisurface_member: bool = False

    def to_dict(self) -> dict:
        return {
            "polygon_id": self.polygon_id,
            "exterior": self.exterior.to_dict(),
            "interiors": [r.to_dict() for r in self.interiors],
            "multisurface_id": self.multisurface_id,
            "compositesurface_id": self.compositesurface_id,
            "is_multisurface_member": self.is_multisurface_member,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolygonNode":
        return cls(
            polygon_id=_text(data.get("polygon_id")),
            exterior=RingNode.from_dict(data.get("exterior") or {}),
            interiors=tuple(RingNode.from_dict(r) for r in data.get("interiors", []) or []),
            multisurface_id=_text(data.get("multisurface_id")),
            compositesurface_id=_text(data.get("compositesurface_id")),
            is_multisurface_member=bool(data.get("is_multisurface_member", False)),
        )


def polygon_sort_key(node: "PolygonNode") -> str:
    """Canonical, deterministic ordering for a tuple of ``PolygonNode``.

    By ``polygon_id`` - already canonical (D-3) for a committed record.
    """

    return node.polygon_id


@dataclass(frozen=True)
class OpeningNode:
    """A Window or Door surface, owned by exactly one boundary surface.

    Openings are themselves surfaces in CityGML (``_AbstractFillingSurface``)
    and so may own their own rings - the 15 rings found inside Window
    surfaces in the project fixture (investigation section 1.3, open
    question 2). ``rings`` is that pre-P5.1 schema-neutral field, kept
    unchanged for compatibility with existing callers (P3.5's shadow-IR
    check leaves it empty by design - CAC-P3.2 never modelled a Window's own
    frame/glazing ring).

    CAC-P5.1 additions, mirroring ``FeatureRecord`` field-for-field:
    ``lod``/``lod_origin`` (the opening's own LoD), ``host_id``/``parent_id``
    (the *explicit* semantic host relation - PLAN.md P5.0 decision 2: an
    opening's host is always this field, never a ring-geometry or material
    match; redundant with, but independently checkable against, this node's
    position inside a ``SurfaceNode.openings`` tuple), ``opening_link``
    (mirrors ``FeatureRecord.opening_link``, pairs with the host's interior
    ``RingNode.opening_link``), and ``polygons`` (the opening's *own*
    committed fill geometry - CityGML 3's ``con:fillingSurface`` channel,
    PLAN.md P5.0 deep-dive channel 2 - separate from ``rings`` above).
    """

    gml_id: str
    semantic_type: str
    source_role: str = ""
    source_element: str = ""
    schema_version: str = ""
    subtype_origin: str = ""
    appearance_refs: Tuple[str, ...] = ()
    rings: Tuple[RingNode, ...] = ()
    lod: int | None = None
    lod_origin: str = ""
    host_id: str = ""
    parent_id: str = ""
    opening_link: str = ""
    polygons: Tuple[PolygonNode, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "semantic_type": self.semantic_type,
            "source_role": self.source_role,
            "source_element": self.source_element,
            "schema_version": self.schema_version,
            "subtype_origin": self.subtype_origin,
            "appearance_refs": list(self.appearance_refs),
            "rings": [ring.to_dict() for ring in self.rings],
            "lod": self.lod,
            "lod_origin": self.lod_origin,
            "host_id": self.host_id,
            "parent_id": self.parent_id,
            "opening_link": self.opening_link,
            "polygons": [p.to_dict() for p in self.polygons],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpeningNode":
        lod = data.get("lod")
        return cls(
            gml_id=_text(data.get("gml_id")),
            semantic_type=_text(data.get("semantic_type")),
            source_role=_text(data.get("source_role")),
            source_element=_text(data.get("source_element")),
            schema_version=_text(data.get("schema_version")),
            subtype_origin=_text(data.get("subtype_origin")),
            appearance_refs=tuple(_text(v) for v in data.get("appearance_refs", []) or []),
            rings=tuple(RingNode.from_dict(r) for r in data.get("rings", []) or []),
            lod=int(lod) if lod is not None else None,
            lod_origin=_text(data.get("lod_origin")),
            host_id=_text(data.get("host_id")),
            parent_id=_text(data.get("parent_id")),
            opening_link=_text(data.get("opening_link")),
            polygons=tuple(PolygonNode.from_dict(p) for p in data.get("polygons", []) or []),
        )


def opening_sort_key(node: "OpeningNode") -> str:
    """Canonical, deterministic ordering for a tuple of ``OpeningNode``.

    By ``gml_id`` - the feature's own stable identity (never geometry or
    Blender iteration order, PLAN.md P5.0 decision 2).
    """

    return node.gml_id


@dataclass(frozen=True)
class SurfaceNode:
    """A boundary surface (WallSurface, RoofSurface, GroundSurface, ...).

    Holds its own rings - including a ring with no corresponding opening,
    such as a chimney penetration in a RoofSurface - plus any openings it
    hosts. Rings are never derived from ``openings``; both are populated
    independently from stored data. ``openings`` *is* this surface's own
    representation of "which opening does this surface host" (PLAN.md P5.0
    decision 2) - a builder places an ``OpeningNode`` here because its
    ``FeatureRecord.host_id`` names this surface, never because a ring
    shape happened to match.

    CAC-P5.1 additions, mirroring ``FeatureRecord``: ``lod``/``lod_origin``,
    ``parent_id`` (this surface's own owning Building/BuildingPart), and
    ``polygons`` (the surface's own committed geometry - PLAN.md P5.0
    channel 1, the ``con:opening``/boundary-element geometry, as opposed to
    an opening's own ``polygons`` which is channel 2, the filling geometry).
    """

    gml_id: str
    semantic_type: str
    schema_version: str = ""
    appearance_refs: Tuple[str, ...] = ()
    rings: Tuple[RingNode, ...] = ()
    openings: Tuple[OpeningNode, ...] = ()
    lod: int | None = None
    lod_origin: str = ""
    parent_id: str = ""
    polygons: Tuple[PolygonNode, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "semantic_type": self.semantic_type,
            "schema_version": self.schema_version,
            "appearance_refs": list(self.appearance_refs),
            "rings": [ring.to_dict() for ring in self.rings],
            "openings": [opening.to_dict() for opening in self.openings],
            "lod": self.lod,
            "lod_origin": self.lod_origin,
            "parent_id": self.parent_id,
            "polygons": [p.to_dict() for p in self.polygons],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SurfaceNode":
        lod = data.get("lod")
        return cls(
            gml_id=_text(data.get("gml_id")),
            semantic_type=_text(data.get("semantic_type")),
            schema_version=_text(data.get("schema_version")),
            appearance_refs=tuple(_text(v) for v in data.get("appearance_refs", []) or []),
            rings=tuple(RingNode.from_dict(r) for r in data.get("rings", []) or []),
            openings=tuple(OpeningNode.from_dict(o) for o in data.get("openings", []) or []),
            lod=int(lod) if lod is not None else None,
            lod_origin=_text(data.get("lod_origin")),
            parent_id=_text(data.get("parent_id")),
            polygons=tuple(PolygonNode.from_dict(p) for p in data.get("polygons", []) or []),
        )


def surface_sort_key(node: "SurfaceNode") -> str:
    """Canonical, deterministic ordering for a tuple of ``SurfaceNode``.

    By ``gml_id`` - the feature's own stable identity (never geometry or
    Blender iteration order, PLAN.md P5.0 decision 2). Used by CAC-P5.2's
    ``domain/ir_adapter.py::building_ir_from_records`` to order a
    Building's boundary surfaces independently of ``RecordSet`` iteration
    order.
    """

    return node.gml_id


@dataclass(frozen=True)
class InstallationNode:
    """A BuildingInstallation / IntBuildingInstallation, already resolved to
    a canonical subtype (``Balcony``, ``Chimney`` or generic
    ``BuildingInstallation``) by ``shared/semantic_types.py``.
    """

    gml_id: str
    semantic_type: str
    source_role: str = ""
    source_element: str = ""
    schema_version: str = ""
    subtype_origin: str = ""
    appearance_refs: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "semantic_type": self.semantic_type,
            "source_role": self.source_role,
            "source_element": self.source_element,
            "schema_version": self.schema_version,
            "subtype_origin": self.subtype_origin,
            "appearance_refs": list(self.appearance_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InstallationNode":
        return cls(
            gml_id=_text(data.get("gml_id")),
            semantic_type=_text(data.get("semantic_type")),
            source_role=_text(data.get("source_role")),
            source_element=_text(data.get("source_element")),
            schema_version=_text(data.get("schema_version")),
            subtype_origin=_text(data.get("subtype_origin")),
            appearance_refs=tuple(_text(v) for v in data.get("appearance_refs", []) or []),
        )


@dataclass(frozen=True)
class BuildingNode:
    """Root of the tree: one Building (or BuildingPart) and everything it owns."""

    gml_id: str
    hierarchy_model: str = ""
    boundary_surfaces: Tuple[SurfaceNode, ...] = ()
    installations: Tuple[InstallationNode, ...] = ()

    def to_dict(self) -> dict:
        return {
            "gml_id": self.gml_id,
            "hierarchy_model": self.hierarchy_model,
            "boundary_surfaces": [s.to_dict() for s in self.boundary_surfaces],
            "installations": [i.to_dict() for i in self.installations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuildingNode":
        return cls(
            gml_id=_text(data.get("gml_id")),
            hierarchy_model=_text(data.get("hierarchy_model")),
            boundary_surfaces=tuple(
                SurfaceNode.from_dict(s) for s in data.get("boundary_surfaces", []) or []
            ),
            installations=tuple(
                InstallationNode.from_dict(i) for i in data.get("installations", []) or []
            ),
        )


# ---------------------------------------------------------------------------
# Builder: walks a Blender object hierarchy, produces the IR
# ---------------------------------------------------------------------------

_RINGS_PROPERTY = "cgml_rings"
_APPEARANCE_REFS_PROPERTY = "cgml_appearance_refs"

BOUNDARY_SURFACE_TYPES = {
    "wallsurface",
    "roofsurface",
    "groundsurface",
    "closuresurface",
    "outerceilingsurface",
    "outerfloorsurface",
    "clearanceshapesurface",
}

_OPENING_SEMANTIC_TYPES = {"window", "door"}
OPENING_FEATURE_TYPES = {"window", "windowsurface", "door", "doorsurface"}
_INSTALLATION_SEMANTIC_TYPES = {"balcony", "chimney", "buildinginstallation"}
_INSTALLATION_FEATURE_TYPES = {"buildinginstallation", "intbuildinginstallation"}
# CityGML 2.0-only member of the set above: 3.0 has no IntBuildingInstallation
# (it becomes a BuildingInstallation inside the building, see
# shared/citygml_ir.py CITYGML2_CONCEPTS). Kept so 2.0 scenes still classify.
CITYGML2_ONLY_FEATURE_TYPES = {"intbuildinginstallation"}


def _read_rings(obj: Any) -> Tuple[RingNode, ...]:
    raw = _safe_get(obj, _RINGS_PROPERTY)
    return tuple(RingNode.from_dict(item) for item in _read_json_list(raw))


def _read_appearance_refs(obj: Any) -> Tuple[str, ...]:
    raw = _safe_get(obj, _APPEARANCE_REFS_PROPERTY)
    return tuple(_text(item) for item in _read_json_list(raw))


def is_boundary_surface(obj: Any) -> bool:
    feature = _strip_schema_prefix(_safe_get(obj, "cgml3_feature")).lower()
    return feature in BOUNDARY_SURFACE_TYPES


def is_opening(obj: Any) -> bool:
    if _text(_safe_get(obj, "structure_part")) == "opening":
        return True
    semantic = _text(_safe_get(obj, "cgml_semantic_type")).lower()
    if semantic in _OPENING_SEMANTIC_TYPES:
        return True
    feature = _strip_schema_prefix(_safe_get(obj, "cgml3_feature")).lower()
    return feature in OPENING_FEATURE_TYPES


def _is_installation(obj: Any) -> bool:
    # Deliberately narrower than "cgml_semantic_type is set" - that property
    # is also set on openings (Window/Door, see build_opening), so a lenient
    # any-value check would misclassify an opening-shaped object that ends up
    # as a direct child of the Building (outside its parent surface) as an
    # installation instead of leaving it unrecognized.
    if _text(_safe_get(obj, "structure_part")) == "installation":
        return True
    semantic = _text(_safe_get(obj, "cgml_semantic_type")).lower()
    if semantic in _INSTALLATION_SEMANTIC_TYPES:
        return True
    feature = _strip_schema_prefix(_safe_get(obj, "cgml3_feature")).lower()
    return feature in _INSTALLATION_FEATURE_TYPES


def build_opening(obj: Any) -> OpeningNode:
    """Build an :class:`OpeningNode` from a single Window/Door object."""

    semantic_type = _text(_safe_get(obj, "cgml_semantic_type")) or _strip_schema_prefix(
        _safe_get(obj, "cgml3_feature")
    )
    return OpeningNode(
        gml_id=_text(_safe_get(obj, "gml_id")),
        semantic_type=semantic_type,
        source_role=_text(_safe_get(obj, "cgml_source_role")),
        source_element=_text(_safe_get(obj, "cgml_source_element")),
        schema_version=_text(_safe_get(obj, "cgml_schema_version")),
        subtype_origin=_text(_safe_get(obj, "cgml_subtype_origin")),
        appearance_refs=_read_appearance_refs(obj),
        rings=_read_rings(obj),
    )


def build_surface(obj: Any) -> SurfaceNode:
    """Build a :class:`SurfaceNode` from a single boundary-surface object."""

    openings = tuple(
        build_opening(child) for child in _iter_children(obj) if is_opening(child)
    )
    return SurfaceNode(
        gml_id=_text(_safe_get(obj, "gml_id")),
        semantic_type=_strip_schema_prefix(_safe_get(obj, "cgml3_feature")),
        schema_version=_text(_safe_get(obj, "cgml_schema_version")),
        appearance_refs=_read_appearance_refs(obj),
        rings=_read_rings(obj),
        openings=openings,
    )


def build_installation(obj: Any) -> InstallationNode:
    """Build an :class:`InstallationNode` from a single installation object."""

    semantic_type = _text(_safe_get(obj, "cgml_semantic_type")) or _strip_schema_prefix(
        _safe_get(obj, "cgml3_feature")
    )
    return InstallationNode(
        gml_id=_text(_safe_get(obj, "gml_id")),
        semantic_type=semantic_type,
        source_role=_text(_safe_get(obj, "cgml_source_role")),
        source_element=_text(_safe_get(obj, "cgml_source_element")),
        schema_version=_text(_safe_get(obj, "cgml_schema_version")),
        subtype_origin=_text(_safe_get(obj, "cgml_subtype_origin")),
        appearance_refs=_read_appearance_refs(obj),
    )


def build_semantic_tree(building_obj: Any) -> BuildingNode:
    """Walk ``building_obj``'s children and produce the schema-neutral IR.

    ``building_obj`` and its descendants only need to satisfy the duck-typed
    contract documented in the module docstring - a real ``bpy.types.Object``
    qualifies, and so does a plain stub used in unit tests.
    """

    boundary_surfaces = []
    installations = []

    for child in _iter_children(building_obj):
        if is_boundary_surface(child):
            boundary_surfaces.append(build_surface(child))
        elif _is_installation(child):
            installations.append(build_installation(child))

    return BuildingNode(
        gml_id=_text(_safe_get(building_obj, "gml_id")),
        hierarchy_model=_text(_safe_get(building_obj, "cgml_hierarchy_model")),
        boundary_surfaces=tuple(boundary_surfaces),
        installations=tuple(installations),
    )
