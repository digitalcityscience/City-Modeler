# Extracted verbatim from io_v2/reader/importer.py (T11d d3-C): the helpers the shared builder uses.
from __future__ import annotations


import bpy
import hashlib
import struct
from .namespaces import NS
from ..v3.xml_utils import localname
from functools import partial
# identical to the 2.0 copy; helpers that read NS stay here (the gml namespace differs)
from ..v3.importer import (_collection_base_name, _compute_geometry_hash, _parse_gml_id_filter,
                           _crs_matches, _normalize_epsg, _extract_vertical_epsg)
from ..v3 import importer as _v3

# same code as 3.0, read with the 2.0 namespaces (GML 3.1.1)
_determine_file_origin_and_crs = partial(_v3._determine_file_origin_and_crs, ns=NS)
_get_envelope_srs_and_corners = partial(_v3._get_envelope_srs_and_corners, ns=NS)


def _infer_feature_crs(feat_el) -> str:
    """
    Infer per-feature CRS from geometry elements carrying @srsName.
    Returns normalized string like 'EPSG:3878' or 'Unknown CRS'.
    """
    try:
        geom_tags = (
            "Solid",
            "MultiSurface",
            "CompositeSurface",
            "Polygon",
            "MultiPolygon",
            "Point",
            "MultiPoint",
            "Curve",
            "LineString",
            "MultiCurve",
            "Surface",
            "Envelope",
        )
        for el in feat_el.iter():
            tag = getattr(el, "tag", None)
            if not isinstance(tag, str):
                continue
            ln = localname(tag)
            if ln not in geom_tags:
                continue
            srs_name = el.get("srsName") or ""
            cand = _normalize_epsg(srs_name) if srs_name else "Unknown CRS"
            if cand != "Unknown CRS":
                return cand
    except Exception:
        pass
    return "Unknown CRS"


def ensure_collection(context, name):
    coll = bpy.data.collections.get(name)
    if not coll:
        coll = bpy.data.collections.new(name)
        # Handle both dict context and Blender context
        if isinstance(context, dict):
            bpy.context.scene.collection.children.link(coll)
        else:
            context.scene.collection.children.link(coll)
    return coll


_INLINE_SUBFEATURE_COLLECTION_NAMES = {
    "BuildingPart", "BuildingParts",
    "BridgePart", "BridgeParts",
    "TunnelPart", "TunnelParts",
    "BuildingInstallation", "BuildingInstallations",
    "IntBuildingInstallation", "IntBuildingInstallations",
    "BuildingFurniture", "BuildingFurnitures",
    "BridgeInstallation", "BridgeInstallations",
    "IntBridgeInstallation", "IntBridgeInstallations",
    "BridgeConstructionElement", "BridgeConstructionElements",
    "IntBridgeConstructionElement", "IntBridgeConstructionElements",
    "BridgeFurniture", "BridgeFurnitures",
    "TunnelInstallation", "TunnelInstallations",
    "IntTunnelInstallation", "IntTunnelInstallations",
    "TunnelFurniture", "TunnelFurnitures",
}




def _disable_inline_subfeature_collections(root_collection):
    """Mark imported inline subfeature collections to avoid double export."""
    if root_collection is None:
        return
    try:
        children = list(root_collection.children)
    except Exception:
        children = []

    for child in children:
        child_name = _collection_base_name(getattr(child, "name", ""))
        if child_name not in _INLINE_SUBFEATURE_COLLECTION_NAMES:
            continue

        try:
            child["cgml3_auto_disabled_subfeatures"] = True
        except Exception:
            pass




def _finalize_appearance_diagnostics(totals, warnings, subtype_summary, *, label="CityGML2"):
    """Build and return the aggregated appearance-diagnostics report dict and
    print the completion-report summary (same shape as the CityGML 3 one)."""
    report = {
        "source_textures_used": totals.source_textures_used,
        "source_materials_used": totals.source_materials_used,
        "semantic_fallback_used": totals.semantic_fallback_used,
        "missing_invalid_appearance": totals.missing_invalid_appearance,
        "unknown_installation_subtypes": totals.unknown_installation_subtypes,
        "warnings": warnings.entries(),
        "unknown_subtype_values": subtype_summary.entries(),
        "unknown_subtype_overflow": subtype_summary.overflow_count(),
    }
    print(
        f"[{label}][Appearance] source textures used: "
        f"{totals.source_textures_used} | source materials used: "
        f"{totals.source_materials_used} | semantic fallback: "
        f"{totals.semantic_fallback_used} | missing/invalid: "
        f"{totals.missing_invalid_appearance} | unknown installation subtypes: "
        f"{totals.unknown_installation_subtypes}"
    )
    if warnings.distinct_count:
        print(f"[{label}][Appearance] {warnings.distinct_count} distinct warning(s):")
        for value, count in warnings.entries():
            print(f"  - {value} (x{count})")
    if subtype_summary.distinct_count:
        print(f"[{label}][Appearance] unknown installation subtype value(s):")
        for value, count in subtype_summary.entries():
            print(f"  - {value!r} (x{count})")
        overflow = subtype_summary.overflow_count()
        if overflow:
            print(f"  ... and {overflow} more distinct value(s)")
    return report
