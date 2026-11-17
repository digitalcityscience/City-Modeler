"""Build authoring records from legacy object/material state (P1.3).

Read-only and pure: works on ``ObjectView``/``MaterialView`` snapshots that
``blender/legacy_views.py`` makes from real objects (or tests make by hand).
Nothing here writes to Blender.

Legacy conventions read (as locked by golden/cac/import_ids_*.json):

- an object with ``gml_id`` is one feature; ``cgml3_feature`` is its kind,
  ``cgml_semantic_type`` its subtype;
- parent: ``cgml_parent_id``, else ``filling_parent_surface_id`` (openings),
  else the nearest Blender ancestor with a ``gml_id``;
- each non-interior material is one polygon (``gml_polygon_id``,
  ``gml_ring_id``) of the feature named by ``con_surface_id`` (or of the
  object's own feature when that key is missing, e.g. installations);
- a material with ``Interior`` is an interior ring of the polygon named by
  ``ExteriorPolyId``;
- in merged (un-exploded) buildings, surfaces and openings exist only as
  ``con_surface_id`` on materials. Their host wall is not recorded there
  (only geometry could tell), so merged openings get an empty parent/host
  and a note.

Views must list only materials that faces actually use: a Building keeps
all its material slots after the semantic explode.
"""
from dataclasses import dataclass, field, replace

from ...lod_helpers import read_lod_value
from ...semantic_tree import BOUNDARY_SURFACE_TYPES, OPENING_FEATURE_TYPES
from .records import FeatureRecord, PolygonRecord, RecordSet, RingRecord

_OWNER_KINDS = ("Building", "BuildingPart")
# Neutral kinds for the two CityGML 3 opening spellings.
_NEUTRAL_KIND = {"windowsurface": "Window", "doorsurface": "Door"}


@dataclass(frozen=True)
class MaterialView:
    name: str
    props: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.props.get(key, default)


@dataclass(frozen=True)
class ObjectView:
    name: str
    type: str = "MESH"
    props: dict = field(default_factory=dict)
    parent: str | None = None          # parent object name
    materials: tuple = ()              # tuple[MaterialView], used by faces, slot order

    def get(self, key, default=None):
        return self.props.get(key, default)


@dataclass(frozen=True)
class LegacyScene:
    records: RecordSet
    notes: tuple = ()                  # things legacy state cannot express; not errors


def _text(value):
    return "" if value is None else str(value).strip()


def _strip_prefix(value):
    text = _text(value)
    return text.split(":", 1)[1] if ":" in text else text


def neutral_kind(value):
    """``con:WindowSurface`` -> ``Window``; other kinds keep their local name."""
    kind = _strip_prefix(value)
    return _NEUTRAL_KIND.get(kind.lower(), kind)


def _is_boundary(kind):
    return kind.lower() in BOUNDARY_SURFACE_TYPES


def _is_opening(kind):
    return kind.lower() in OPENING_FEATURE_TYPES


def _is_interior(mat):
    return bool(mat.get("Interior"))


class _Scene:
    def __init__(self, objects):
        self.objects = list(objects)
        self.by_name = {o.name: o for o in self.objects}

    def ancestors(self, obj):
        seen = set()
        name = obj.parent
        while name and name in self.by_name and name not in seen:
            seen.add(name)
            obj = self.by_name[name]
            yield obj
            name = obj.parent

    def owner_id(self, obj):
        for anc in self.ancestors(obj):
            if neutral_kind(anc.get("cgml3_feature")) in _OWNER_KINDS and _text(anc.get("gml_id")):
                return _text(anc.get("gml_id"))
        return ""

    def parent_id(self, obj):
        explicit = _text(obj.get("cgml_parent_id")) or _text(obj.get("filling_parent_surface_id"))
        if explicit:
            return explicit
        return next((_text(a.get("gml_id")) for a in self.ancestors(obj) if _text(a.get("gml_id"))), "")


def _lod_of_materials(materials):
    counts = {}
    for mat in materials:
        lod = read_lod_value(mat.get("lod"))
        if lod is not None:
            counts[lod] = counts.get(lod, 0) + 1
    # same tie-break as lod_helpers._infer_lod_from_materials
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0] if counts else None


def feature_record_from_legacy(obj, scene_objects=()):
    """FeatureRecord for one object with a ``gml_id`` (None otherwise); no polygons."""
    gml_id = _text(obj.get("gml_id"))
    if not gml_id:
        return None
    scene = scene_objects if isinstance(scene_objects, _Scene) else _Scene([obj, *scene_objects])
    raw_feature = obj.get("cgml3_feature") or obj.get("cgml_semantic_type")
    kind = neutral_kind(raw_feature)
    lod = read_lod_value(obj.get("lod"))
    if lod is None:
        lod = read_lod_value(obj.get("cgml3_lod"))
    lod_origin = "source"
    if lod is None:
        own = [m for m in obj.materials if not _is_interior(m)
               and _text(m.get("con_surface_id")) in ("", gml_id)]
        lod = _lod_of_materials(own)
        lod_origin = "inferred" if lod is not None else "source"
    return FeatureRecord(
        gml_id=gml_id,
        kind=kind,
        semantic_type=_text(obj.get("cgml_semantic_type")) or kind,
        source_role=_text(obj.get("cgml_source_role")),
        source_element=_text(obj.get("cgml_source_element")) or _text(raw_feature),
        schema_version=_text(obj.get("cgml_schema_version")),
        parent_id=scene.parent_id(obj),
        owner_id=scene.owner_id(obj),
        host_id=(_text(obj.get("filling_parent_surface_id")) if _is_opening(kind)
                 else _text(obj.get("cgml_host_surface_id"))),
        opening_link=_text(obj.get("cgml_opening_link")),
        lod=lod,
        lod_origin=lod_origin,
        provenance="imported",
    )


def read_legacy_scene(objects):
    """All features and polygons expressed by legacy state of ``objects``."""
    scene = _Scene(objects)
    notes = []
    features = {}                      # gml_id -> FeatureRecord (insertion order)
    for obj in scene.objects:
        record = feature_record_from_legacy(obj, scene)
        if record is None:
            continue
        if record.gml_id in features:
            notes.append(f"object {obj.name!r}: gml_id {record.gml_id!r} also on another object")
            continue
        features[record.gml_id] = record

    # Polygons (exterior materials) and material-only features.
    polygons = {}                      # polygon_id -> dict of PolygonRecord kwargs
    material_features = {}
    for obj in scene.objects:
        obj_id = _text(obj.get("gml_id"))
        seen_here = set()
        for mat in obj.materials:
            if _is_interior(mat):
                continue
            poly_id = _text(mat.get("gml_polygon_id"))
            if not poly_id or poly_id in seen_here:
                continue
            seen_here.add(poly_id)
            feature_id = _text(mat.get("con_surface_id")) or obj_id
            if not feature_id:
                notes.append(f"object {obj.name!r}: polygon {poly_id!r} has no owning feature id")
                continue
            if poly_id in polygons:
                notes.append(f"polygon {poly_id!r}: on more than one object ({obj.name!r})")
                continue
            if feature_id not in features and feature_id not in material_features:
                kind = neutral_kind(mat.get("SurfaceTyp"))
                owner = obj_id if neutral_kind(obj.get("cgml3_feature")) in _OWNER_KINDS \
                    else scene.owner_id(obj)
                boundary = _is_boundary(kind)
                if _is_opening(kind):
                    notes.append(f"opening {feature_id!r}: exists only as materials of {obj.name!r}; "
                                 "host surface not recorded in legacy state")
                material_features[feature_id] = FeatureRecord(
                    gml_id=feature_id, kind=kind, semantic_type=kind,
                    source_element=_text(mat.get("SurfaceTyp")),
                    parent_id=obj_id if boundary else "",
                    owner_id=owner,
                    lod=read_lod_value(mat.get("lod")),
                    provenance="imported",
                )
            polygons[poly_id] = dict(
                polygon_id=poly_id,
                feature_id=feature_id,
                lod=read_lod_value(mat.get("lod")),
                exterior=RingRecord(ring_id=_text(mat.get("gml_ring_id")), role="exterior"),
                interiors=[],
                multisurface_id=_text(mat.get("gml_multisurface_id")),
                is_multisurface_member=bool(_text(mat.get("gml_multisurface_id"))),
            )

    # Interior rings, attached by ExteriorPolyId; each ring material once.
    for obj in scene.objects:
        seen_rings = set()
        for mat in obj.materials:
            if not _is_interior(mat):
                continue
            ring_id = _text(mat.get("gml_ring_id")) or _text(mat.get("gml_polygon_id"))
            if ring_id in seen_rings:
                continue
            seen_rings.add(ring_id)
            host = _text(mat.get("ExteriorPolyId"))
            if host not in polygons:
                notes.append(f"object {obj.name!r}: interior ring {ring_id!r} names missing polygon {host!r}")
                continue
            polygons[host]["interiors"].append(RingRecord(
                ring_id=ring_id, role="interior", opening_link=_text(mat.get("cgml_opening_link"))))

    all_features = {**features, **material_features}
    polygon_records = []
    polygon_ids_by_feature = {}
    for kwargs in polygons.values():
        kwargs["interiors"] = tuple(kwargs["interiors"])
        polygon_records.append(PolygonRecord(**kwargs))
        polygon_ids_by_feature.setdefault(kwargs["feature_id"], []).append(kwargs["polygon_id"])
        if kwargs["feature_id"] not in all_features:
            notes.append(f"polygon {kwargs['polygon_id']!r}: feature {kwargs['feature_id']!r} unknown")

    feature_records = []
    for gml_id, record in all_features.items():
        poly_ids = tuple(polygon_ids_by_feature.get(gml_id, ()))
        feature_records.append(replace(record, polygon_ids=poly_ids))
    return LegacyScene(RecordSet(tuple(feature_records), tuple(polygon_records)), tuple(notes))
