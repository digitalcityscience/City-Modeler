# Extracted verbatim from io/reader/importer.py (T11d d3-C): the helpers the shared builder uses.
from __future__ import annotations


import bpy
import hashlib
import struct
from .namespaces import NS
from .xml_utils import localname
from ....shared.materials_common import normalize_crs_to_epsg, extract_vertical_epsg
from ....shared.import_origin import parse_corner, file_origin as _file_origin, bounds_from_pos_lists
import json


def _safe_store_json_chunks(owner, base_key: str, data: dict, *, chunk_size: int = 16000) -> None:
    """
    Store JSON representation of `data` into IDProperties, splitting into chunks if needed.
    Blender IDProperties can reject very large strings; chunking keeps it visible/debuggable.
    """
    try:
        txt = json.dumps(data, ensure_ascii=False, sort_keys=True)
    except Exception:
        txt = str(data)

    # Clear old chunks if any
    try:
        for k in list(getattr(owner, "keys", lambda: [])()):
            if isinstance(k, str) and (k == base_key or k.startswith(base_key + "_")):
                try:
                    del owner[k]
                except Exception:
                    pass
    except Exception:
        pass

    if not isinstance(txt, str):
        txt = str(txt)

    if len(txt) <= chunk_size:
        owner[base_key] = txt
        return

    # Split into chunks
    for i in range(0, len(txt), chunk_size):
        owner[f"{base_key}_{i//chunk_size}"] = txt[i : i + chunk_size]


def _compute_geometry_hash(verts, faces):
    """Compute a hash for geometry deduplication based on vertices and faces.
    
    Uses binary packing for faster hashing compared to string formatting.
    """
    try:
        # Pack vertices as binary floats (3 floats per vertex)
        vert_bytes = b''.join(struct.pack('fff', *v) for v in verts)
        # Pack face indices as binary integers
        face_bytes = b''.join(struct.pack(f'{len(f)}I', *f) for f in faces)
        combined = vert_bytes + face_bytes
        return hashlib.sha256(combined).hexdigest()[:16]
    except (struct.error, TypeError, ValueError):
        return None


_normalize_epsg = normalize_crs_to_epsg


_extract_vertical_epsg = extract_vertical_epsg


def _crs_matches(a: str, b: str) -> bool:
    ea = _normalize_epsg(a or "")
    eb = _normalize_epsg(b or "")
    return ea != "Unknown CRS" and ea == eb


def _determine_file_origin_and_crs(root, ns=NS):
    # Origin = envelope centre (X/Y rounded, Z = min Z); scan only without envelope.
    srs, lc, uc = _get_envelope_srs_lower_upper(root, ns)
    origin = _file_origin(lc, uc, scan_bounds=lambda: _scan_bounds(root, ns))
    crs = _normalize_epsg(srs) if srs else "Unknown CRS"

    # Fallback: if no CRS was found on the CityModel/Envelope, try to infer it from
    # geometry elements that carry their own @srsName (e.g. gml:Solid, gml:MultiSurface, gml:Point).
    if crs == "Unknown CRS":
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
            )
            for el in root.iter():
                tag = getattr(el, "tag", None)
                if not isinstance(tag, str):
                    continue
                ln = localname(tag)
                if ln not in geom_tags:
                    continue
                srs_name = el.get("srsName") or ""
                cand = _normalize_epsg(srs_name) if srs_name else "Unknown CRS"
                if cand != "Unknown CRS":
                    crs = cand
                    break
        except Exception:
            pass

    return origin, crs


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
        context.scene.collection.children.link(coll)
    return coll


_INLINE_SUBFEATURE_COLLECTION_NAMES = {
    "BuildingInstallation", "BuildingInstallations",
    "IntBuildingInstallation", "IntBuildingInstallations",
    "BuildingFurniture", "BuildingFurnitures",
    "BridgeInstallation", "BridgeInstallations",
    "IntBridgeInstallation", "IntBridgeInstallations",
    "BridgeConstructionElement", "BridgeConstructionElements",
    "BridgeFurniture", "BridgeFurnitures",
    "TunnelInstallation", "TunnelInstallations",
    "IntTunnelInstallation", "IntTunnelInstallations",
    "TunnelFurniture", "TunnelFurnitures",
}


def _collection_base_name(name: str) -> str:
    text = str(name or "").strip()
    if "." in text:
        base, suffix = text.rsplit(".", 1)
        if suffix.isdigit():
            return base
    return text


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


def _get_envelope_srs_and_corners(root, ns=NS):
    env = root.find(".//gml:Envelope", ns)
    if env is None: return "", None
    srs = env.get("srsName", "") or ""
    lc = (env.findtext("./gml:lowerCorner", default="", namespaces=ns) or "").strip()
    def _parse_corner(txt):
        vals = [float(x) for x in " ".join(txt.split()).split()]
        if len(vals) >= 3: return (vals[0], vals[1], vals[2])
        if len(vals) == 2: return (vals[0], vals[1], 0.0)
        return None
    return srs, _parse_corner(lc)


def _get_envelope_srs_lower_upper(root, ns=NS):
    env = root.find(".//gml:Envelope", ns)
    if env is None:
        return "", None, None
    srs = env.get("srsName", "") or ""
    lc = parse_corner(env.findtext("./gml:lowerCorner", default="", namespaces=ns))
    uc = parse_corner(env.findtext("./gml:upperCorner", default="", namespaces=ns))
    return srs, lc, uc


def _scan_bounds(root, ns=NS):
    def _pos_lists():
        for el in root.findall(".//gml:posList", ns) + root.findall(".//gml:pos", ns):
            dim_attr = el.get("srsDimension")
            yield (el.text or ""), (int(dim_attr) if dim_attr and dim_attr.isdigit() else None)
    return bounds_from_pos_lists(_pos_lists())


def _parse_gml_id_filter(filter_str: str) -> set[str]:
    """Parse GML ID filter string in format 'ID1,ID2,ID3-ID4,...'"""
    if not filter_str:
        return set()
    
    ids = set()
    for part in filter_str.split(','):
        if '-' in part:  # Range of IDs
            start, end = part.split('-', 1)
            # For now, just add start and end IDs since we don't have a way to generate IDs in between
            ids.add(start.strip())
            ids.add(end.strip())
        else:  # Single ID
            ids.add(part.strip())
    return ids


def _finalize_appearance_diagnostics(totals, warnings, subtype_summary, *, label="CityGML3"):
    """Build and return the aggregated appearance-diagnostics report dict and
    print the completion-report summary."""
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
