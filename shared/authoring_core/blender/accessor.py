"""One accessor for UI semantic metadata: record first, legacy fallback (P1.6).

``read_feature_view`` is the only thing ``ui/citygml_attributes_panel.py``
reads object state through from here on. It prefers a stored record (ADR
0009) when the experimental flag is on and the object carries one;
otherwise it falls back to the exact legacy computation the panel always
used, so flag-off output stays byte-for-byte identical (PLAN.md R6).

Duck-typed on ``obj`` (``.get``, ``.type``, ``.material_slots``), like
``domain/legacy_reader.py``'s views -- so this is unit-testable without a
real Blender object.
"""
from dataclasses import replace

from ...lod_helpers import object_lod
from ..domain.view import RECORD_GML_ID_MISMATCH, FeatureView, ViewFinding
from . import flag, storage

_COMMON_ATTRS = (
    "class", "function", "usage", "gml_name", "gml_description",
    "creationDate", "terminationDate", "validFrom", "validTo",
)
_SPECIFIC_ATTRS = {
    "Building": ["yearOfConstruction", "yearOfDemolition", "roofType",
                 "measuredHeight", "storeysAboveGround", "storeysBelowGround"],
    "BuildingPart": ["yearOfConstruction", "yearOfDemolition", "roofType",
                     "measuredHeight", "storeysAboveGround", "storeysBelowGround"],
    "BuildingUnit": ["type", "ownerName", "numberOfRooms",
                     "numberOfBedRooms", "numberOfBathRooms"],
    "Storey": ["type", "storeysAboveGround", "storeysBelowGround"],
    "Bridge": ["isMovable", "yearOfConstruction", "yearOfDemolition"],
    "BridgePart": ["isMovable", "yearOfConstruction", "yearOfDemolition"],
    "BridgeRoom": [],
    "BridgeFurniture": [],
    "Tunnel": ["yearOfConstruction", "yearOfDemolition"],
    "TunnelPart": ["yearOfConstruction", "yearOfDemolition"],
    "HollowSpace": [],
    "TunnelFurniture": [],
    "Road": ["surfaceMaterial"],
    "Railway": ["surfaceMaterial"],
    "Section": [],
    "Intersection": [],
    "Waterway": [],
    "WaterBody": ["waterLevel"],
    "SolitaryVegetationObject": ["species", "trunkDiameter", "crownDiameter", "height"],
    "PlantCover": ["averageHeight"],
    "CityFurniture": [],
    "LandUse": [],
    "ReliefFeature": [],
    "TINRelief": [],
}


def _legacy_feature_type(obj):
    return obj.get("cgml3_feature") or obj.get("feature_type") or None


def _legacy_lod(obj):
    lod = object_lod(obj)
    if lod is not None:
        return lod
    name = obj.name.lower()
    for i in range(5):
        if f"lod{i}" in name:
            return i
    return None


def _legacy_attributes(obj, feature_type):
    attrs = {}
    for attr in _COMMON_ATTRS:
        val = obj.get(attr)
        if val is not None:
            attrs[attr] = val
    if feature_type and feature_type in _SPECIFIC_ATTRS:
        for attr in _SPECIFIC_ATTRS[feature_type]:
            val = obj.get(attr)
            if val is not None:
                attrs[attr] = val
    return attrs


def _legacy_boundary_surfaces(obj):
    if obj.type != "MESH":
        return ()
    boundaries = []
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if mat is None:
            continue
        surf_type = mat.get("surface_type") or mat.get("SurfaceTyp")
        if surf_type:
            boundaries.append({
                "name": mat.name,
                "type": surf_type,
                "gml_id": mat.get("gml_surface_id") or mat.get("gml_polygon_id"),
            })
    return tuple(boundaries)


def _legacy_view(obj):
    feature_type = _legacy_feature_type(obj)
    return FeatureView(
        origin="legacy",
        feature_type=feature_type,
        gml_id=obj.get("gml_id"),
        semantic_type=obj.get("cgml_semantic_type"),
        semantic_origin=obj.get("cgml_subtype_origin"),
        lod=_legacy_lod(obj),
        attributes=_legacy_attributes(obj, feature_type),
        boundary_surfaces=_legacy_boundary_surfaces(obj),
    )


def _record_view(feature):
    # Boundary surfaces of a record-backed feature live in sibling objects'
    # own record documents; resolving them needs the object registry (P2).
    # Until then a record-origin view leaves this empty rather than guess.
    return FeatureView(
        origin="record",
        feature_type=feature.kind,
        gml_id=feature.gml_id,
        semantic_type=feature.semantic_type or feature.kind,
        lod=feature.lod,
        attributes=dict(feature.params),
        boundary_surfaces=(),
    )


def read_feature_view(obj):
    """``FeatureView`` for ``obj``: a stored record first (flag on), else legacy (R6).

    The record is used only if one of its features has the object's own
    ``gml_id``. Otherwise the record belongs to another object (AB-14,
    Shift+D): the legacy view is returned with a ``RECORD_GML_ID_MISMATCH``
    finding instead of guessing a feature.
    """
    if obj is None:
        return None
    if flag.is_enabled():
        record_set = storage.read_record(obj)
        if record_set is not None and record_set.features:
            own_id = obj.get("gml_id")
            feature = record_set.feature(str(own_id)) if own_id else None
            if feature is not None:
                return _record_view(feature)
            record_ids = [f.gml_id for f in record_set.features]
            finding = ViewFinding(
                RECORD_GML_ID_MISMATCH,
                f"object gml_id {own_id!r} is not among its record's features {record_ids}",
            )
            return replace(_legacy_view(obj), findings=(finding,))
    return _legacy_view(obj)
