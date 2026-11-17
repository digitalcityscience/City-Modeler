"""Typed CityGML authoring records (schema v1, PLAN.md §2.3).

Records are frozen dataclasses; change them with ``dataclasses.replace``.
``to_dict``/``from_dict`` give a JSON-safe form for persistence:

- unknown keys (for example fields added by a newer add-on) are kept in
  ``extra`` and written back unchanged, so an older add-on never drops data;
- ``record_version`` older than ``RECORD_VERSION`` is upgraded by the
  functions in ``MIGRATIONS`` before the fields are read.

Validation is separate from construction (``validate_*`` return a list of
problems), so a damaged record can still be loaded and reported.
"""
import copy
import json
from dataclasses import dataclass, field, fields, replace

from .ids import is_valid_gml_id

RECORD_VERSION = 1

GEOMETRY_STATES = ("ids_only", "committed")
PROVENANCES = ("imported", "promoted", "authored")
LOD_ORIGINS = ("source", "inferred", "authored")
RING_ROLES = ("exterior", "interior")

# {from_version: fn(dict) -> dict at from_version + 1}. Empty for v1.
MIGRATIONS = {}


class RecordError(ValueError):
    """A record dict cannot be read at all (wrong type, unreadable version)."""


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RingRecord:
    ring_id: str = ""
    role: str = "exterior"
    coords: tuple = ()                 # ((x, y, z), ...); empty while ids_only
    opening_link: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PolygonRecord:
    polygon_id: str
    feature_id: str
    lod: int | None = None
    exterior: RingRecord = field(default_factory=RingRecord)
    interiors: tuple = ()              # tuple[RingRecord]
    multisurface_id: str = ""
    compositesurface_id: str = ""
    is_multisurface_member: bool = False
    appearance_refs: tuple = ()        # tuple[str]
    component_role: str = ""
    geometry_state: str = "ids_only"
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureRecord:
    gml_id: str
    kind: str                          # neutral feature kind, e.g. WallSurface, Window
    record_version: int = RECORD_VERSION
    semantic_type: str = ""            # resolved subtype (Chimney, Balcony, ...); "" = kind
    source_role: str = ""
    source_element: str = ""
    schema_version: str = ""
    parent_id: str = ""
    owner_id: str = ""
    host_id: str = ""
    opening_link: str = ""
    lod: int | None = None
    lod_origin: str = "source"
    generator: str = ""
    params: dict = field(default_factory=dict)
    provenance: str = "imported"
    polygon_ids: tuple = ()            # tuple[str], ordered
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# dict round trip
# --------------------------------------------------------------------------

def _known(cls):
    return {f.name for f in fields(cls)} - {"extra"}


def _split(cls, data):
    if not isinstance(data, dict):
        raise RecordError(f"{cls.__name__}: expected a dict, got {type(data).__name__}")
    known = _known(cls)
    values = {k: v for k, v in data.items() if k in known}
    extra = copy.deepcopy({k: v for k, v in data.items() if k not in known})
    return values, extra


def _join(record):
    out = copy.deepcopy(record.extra)
    for f in fields(record):
        if f.name != "extra":
            out[f.name] = getattr(record, f.name)
    return out


def _coords(value):
    return tuple(tuple(float(c) for c in point) for point in (value or ()))


def ring_to_dict(ring):
    out = _join(ring)
    out["coords"] = [list(p) for p in ring.coords]
    return out


def ring_from_dict(data):
    values, extra = _split(RingRecord, data)
    values["coords"] = _coords(values.get("coords"))
    return RingRecord(**values, extra=extra)


def polygon_to_dict(polygon):
    out = _join(polygon)
    out["exterior"] = ring_to_dict(polygon.exterior)
    out["interiors"] = [ring_to_dict(r) for r in polygon.interiors]
    out["appearance_refs"] = list(polygon.appearance_refs)
    return out


def polygon_from_dict(data):
    values, extra = _split(PolygonRecord, data)
    if "exterior" in values:
        values["exterior"] = ring_from_dict(values["exterior"])
    values["interiors"] = tuple(ring_from_dict(r) for r in values.get("interiors") or ())
    values["appearance_refs"] = tuple(values.get("appearance_refs") or ())
    return PolygonRecord(**values, extra=extra)


def feature_to_dict(feature):
    out = _join(feature)
    out["params"] = copy.deepcopy(feature.params)
    out["polygon_ids"] = list(feature.polygon_ids)
    return out


def migrate(data):
    """Upgrade a feature dict to ``RECORD_VERSION``; newer versions pass through."""
    version = data.get("record_version", RECORD_VERSION)
    if not isinstance(version, int) or version < 1:
        raise RecordError(f"unreadable record_version {version!r}")
    data = copy.deepcopy(data)
    while version < RECORD_VERSION:
        if version not in MIGRATIONS:
            raise RecordError(f"no migration from record_version {version}")
        data = MIGRATIONS[version](data)
        version += 1
        data["record_version"] = version
    return data


def feature_from_dict(data):
    if not isinstance(data, dict):
        raise RecordError(f"FeatureRecord: expected a dict, got {type(data).__name__}")
    values, extra = _split(FeatureRecord, migrate(data))
    values["params"] = copy.deepcopy(values.get("params") or {})
    values["polygon_ids"] = tuple(values.get("polygon_ids") or ())
    return FeatureRecord(**values, extra=extra)


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate_ring(ring, expected_role, geometry_state, where):
    problems = []
    if ring.role != expected_role:
        problems.append(f"{where}: role {ring.role!r}, expected {expected_role!r}")
    if ring.ring_id and not is_valid_gml_id(ring.ring_id):
        problems.append(f"{where}: invalid ring_id {ring.ring_id!r}")
    if geometry_state == "ids_only" and ring.coords:
        problems.append(f"{where}: coords present while geometry_state is 'ids_only'")
    if geometry_state == "committed" and len(set(ring.coords)) < 3:
        problems.append(f"{where}: committed ring needs at least 3 distinct points")
    if any(len(p) != 3 for p in ring.coords):
        problems.append(f"{where}: every coordinate must be (x, y, z)")
    return problems


def validate_polygon(polygon):
    where = f"polygon {polygon.polygon_id!r}"
    problems = []
    if not is_valid_gml_id(polygon.polygon_id):
        problems.append(f"{where}: invalid polygon_id")
    if not is_valid_gml_id(polygon.feature_id):
        problems.append(f"{where}: invalid feature_id {polygon.feature_id!r}")
    if polygon.geometry_state not in GEOMETRY_STATES:
        problems.append(f"{where}: geometry_state {polygon.geometry_state!r} not in {GEOMETRY_STATES}")
    problems += validate_ring(polygon.exterior, "exterior", polygon.geometry_state, f"{where} exterior")
    for i, ring in enumerate(polygon.interiors):
        problems += validate_ring(ring, "interior", polygon.geometry_state, f"{where} interior[{i}]")
    return problems


def validate_feature(feature):
    where = f"feature {feature.gml_id!r}"
    problems = []
    if not is_valid_gml_id(feature.gml_id):
        problems.append(f"{where}: invalid or empty gml_id")
    if not feature.kind:
        problems.append(f"{where}: empty kind")
    if feature.provenance not in PROVENANCES:
        problems.append(f"{where}: provenance {feature.provenance!r} not in {PROVENANCES}")
    if feature.lod_origin not in LOD_ORIGINS:
        problems.append(f"{where}: lod_origin {feature.lod_origin!r} not in {LOD_ORIGINS}")
    if feature.lod is not None and (not isinstance(feature.lod, int) or not 0 <= feature.lod <= 4):
        problems.append(f"{where}: lod {feature.lod!r} must be None or 0..4")
    if len(set(feature.polygon_ids)) != len(feature.polygon_ids):
        problems.append(f"{where}: repeated polygon ids")
    try:
        json.dumps(feature.params, allow_nan=False)
    except (TypeError, ValueError) as exc:
        problems.append(f"{where}: params are not plain JSON ({exc})")
    return problems


# --------------------------------------------------------------------------
# record set
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RecordSet:
    """Features and polygons of one scope (e.g. one Building), indexed by id."""

    features: tuple = ()   # tuple[FeatureRecord]
    polygons: tuple = ()   # tuple[PolygonRecord]

    def feature(self, gml_id):
        return next((f for f in self.features if f.gml_id == gml_id), None)

    def polygon(self, polygon_id):
        return next((p for p in self.polygons if p.polygon_id == polygon_id), None)

    def children(self, parent_id):
        return tuple(f for f in self.features if f.parent_id == parent_id)

    def polygons_of(self, gml_id):
        feature = self.feature(gml_id)
        if feature is None:
            return ()
        return tuple(p for p in (self.polygon(pid) for pid in feature.polygon_ids) if p is not None)

    def with_feature(self, feature):
        """Copy with ``feature`` added or replacing the one with the same gml_id."""
        kept = tuple(f for f in self.features if f.gml_id != feature.gml_id)
        return replace(self, features=kept + (feature,))

    def with_polygon(self, polygon):
        """Copy with ``polygon`` added or replacing the one with the same polygon_id."""
        kept = tuple(p for p in self.polygons if p.polygon_id != polygon.polygon_id)
        return replace(self, polygons=kept + (polygon,))

    def validate(self):
        problems = []
        seen = set()
        for f in self.features:
            problems += validate_feature(f)
            if f.gml_id in seen:
                problems.append(f"feature {f.gml_id!r}: duplicate gml_id")
            seen.add(f.gml_id)
        polygon_ids = set()
        for p in self.polygons:
            problems += validate_polygon(p)
            if p.polygon_id in polygon_ids:
                problems.append(f"polygon {p.polygon_id!r}: duplicate polygon_id")
            polygon_ids.add(p.polygon_id)
            owner = self.feature(p.feature_id)
            if owner is None:
                problems.append(f"polygon {p.polygon_id!r}: feature {p.feature_id!r} not in set")
            elif p.polygon_id not in owner.polygon_ids:
                problems.append(f"polygon {p.polygon_id!r}: not listed by feature {p.feature_id!r}")
        for f in self.features:
            for pid in f.polygon_ids:
                if pid not in polygon_ids:
                    problems.append(f"feature {f.gml_id!r}: polygon {pid!r} referenced but missing")
        return problems

    def to_dict(self):
        return {
            "record_version": RECORD_VERSION,
            "features": [feature_to_dict(f) for f in self.features],
            "polygons": [polygon_to_dict(p) for p in self.polygons],
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise RecordError(f"RecordSet: expected a dict, got {type(data).__name__}")
        return cls(
            features=tuple(feature_from_dict(f) for f in data.get("features") or ()),
            polygons=tuple(polygon_from_dict(p) for p in data.get("polygons") or ()),
        )
