"""Opening a CityJSON file (CJ-02), the parts without bpy.

Plan ``CITYJSON_INTEGRATION_PLAN_2026-11-17.md`` section 5: same Open button, the content decides the
reader; up to the Browse-first threshold a single document opens directly, above it and for CityJSONSeq
Browse first (CJ-05, ``shared/browse/cityjson_index``). The LoD is chosen by its text ("2.2"); a building
without the chosen LoD is skipped and counted, never filled from another LoD (the GML rule, CJ-00 K6).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from .cityjson_reader import INSTANCE, READ_TYPES
    from .import_origin import file_origin
    from .load_limits import LOAD_ALL_MAX_FEATURES, LOAD_ALL_MAX_MB
except ImportError:  # pure-Python unit tests
    from shared.cityjson_reader import INSTANCE, READ_TYPES
    from shared.import_origin import file_origin
    from shared.load_limits import LOAD_ALL_MAX_FEATURES, LOAD_ALL_MAX_MB

SOURCE_FORMAT_KEY = "cgml_source_format"     # World key; the Save tab reads it in CJ-03
SOURCES_KEY = "cgml_cityjson_sources"        # World key (CJ-03): {path: {"partial": bool}} as JSON
# CJ-06, on a top-level building built from a CityJSON file: its source path, and the B84 fingerprint
# of it as built / last saved (a different one now = changed)
SOURCE_PROP = "cgml3_cityjson_source"
FINGERPRINT_PROP = "cgml3_cityjson_fingerprint"
SEQ_NO_LOAD_ALL = "CityJSONSeq opens with Browse first only (no Load all)"
TOO_LARGE = ("{size_mb:,.0f} MB: a single CityJSON document above {max_mb:,.0f} MB cannot be opened here. "
             "Load it into 3DCityDB v5: citydb import cityjson, then open the part you need from the "
             "database browser.")


def open_decision(summary, threshold_features: int = 30, max_mb: float = LOAD_ALL_MAX_MB) -> Tuple[str, str]:
    """``(how, message)``: 'cityjson' (open the whole document), 'browse' (Browse first: above the
    threshold, and always for CityJSONSeq), 'citygml' (the existing GML path) or 'error'."""
    fmt = getattr(summary, "format", "") if summary is not None else ""
    if fmt == "CityJSONSeq":
        return "browse", "CityJSONSeq: Browse first"
    if fmt == "JSON":
        return "error", "Not a CityJSON file (its \"type\" is not \"CityJSON\"). Nothing was imported."
    if fmt != "CityJSON":
        return "citygml", ""
    if summary.size_mb > max_mb:   # a single document is indexed in memory: the Load-all size limit holds
        return "error", TOO_LARGE.format(size_mb=summary.size_mb, max_mb=max_mb)
    n = summary.features_estimate
    if n > threshold_features:
        return "browse", f"{n:,} buildings: Browse first above {threshold_features}"
    return "cityjson", ""


def load_all_refusal(summary, max_features: int = LOAD_ALL_MAX_FEATURES, max_mb: float = LOAD_ALL_MAX_MB) -> str:
    """Why Load all is not offered for this file ('' when it is): the GML limits, and never for
    CityJSONSeq."""
    if getattr(summary, "format", "") == "CityJSONSeq":
        return SEQ_NO_LOAD_ALL
    if summary.size_mb > max_mb:
        return f"{summary.size_mb:,.0f} MB, limit {max_mb:,.0f} MB"
    n = summary.features_estimate
    if n > max_features:
        return f"{'' if summary.exact else '~'}{n:,} features, limit {max_features:,}"
    return ""


def lod_choices(lods: Sequence[str]) -> List[str]:
    """The file's LoD texts, the default first: the highest LoD2 ("2.2" before "2.0"), else the
    highest; the others follow in ascending order (the GML Load default is LoD2)."""
    lods = sorted(set(lods), key=lambda t: (float(t), t))
    if not lods:
        return []
    two = [t for t in lods if t.split(".")[0] == "2"]
    first = (two or lods)[-1]
    return [first] + [t for t in lods if t != first]


def georeference(doc: dict) -> Tuple[str, Tuple[float, float, float]]:
    """``(referenceSystem text, origin)``: the file CRS as written, and the T01 origin (rounded centre,
    Z = min Z) of ``metadata.geographicalExtent``, else of the vertices."""
    meta = doc.get("metadata") or {}
    crs = meta.get("referenceSystem") or ""
    ext = meta.get("geographicalExtent")
    if isinstance(ext, list) and len(ext) == 6 and all(isinstance(x, (int, float)) for x in ext):
        lower, upper = ext[:3], ext[3:]
    else:
        s, t, vs = doc["transform"]["scale"], doc["transform"]["translate"], doc.get("vertices") or ()
        if not vs:
            return str(crs), (0.0, 0.0, 0.0)
        lower = [min(v[i] for v in vs) * s[i] + t[i] for i in range(3)]
        upper = [max(v[i] for v in vs) * s[i] + t[i] for i in range(3)]
    return str(crs), tuple(float(c) for c in file_origin(lower, upper))


def _has_geometry(node) -> bool:
    if any(g.polygons for g in node.geometries.values()):
        return True
    if any(g.polygons for b in node.boundaries for g in b.geometries.values()):
        return True
    return any(_has_geometry(c) for c in node.spaces)


def with_geometry(roots) -> Tuple[list, int]:
    """``(roots that have geometry, number left out)``: a building without the chosen LoD."""
    kept = [r for r in roots if _has_geometry(r)]
    return kept, len(roots) - len(kept)


def missing_lod_notice(lod: Optional[str], n: int) -> str:
    return f"no LoD{lod}: {n}" if lod else f"no geometry: {n}"


def unloaded_lods(doc: dict, lod: Optional[str]) -> Dict[str, int]:
    """Building geometries left in the file because their LoD text is not ``lod``: ``{"1.2": 3}``."""
    out: Dict[str, int] = {}
    if lod is None:
        return out
    for o in (doc.get("CityObjects") or {}).values():
        if isinstance(o, dict) and o.get("type") in READ_TYPES:
            for g in o.get("geometry") or ():
                if isinstance(g, dict) and g.get("type") == INSTANCE:
                    continue    # its LoD is its template's; a furniture left out is counted as missing
                text = str(g.get("lod", "")) if isinstance(g, dict) else ""
                if text != lod:
                    out[text] = out.get(text, 0) + 1
    return out


def not_loaded_items(counts: Dict[str, int], unloaded: Dict[str, int], lod: Optional[str],
                     missing: int) -> List[str]:
    """Short texts for the import summary: skipped kinds, LoDs not loaded, buildings without the LoD."""
    items = [f"{n} {kind}" for kind, n in sorted(counts.items())]
    items += [f"{n} LoD{t} geometr{'y' if n == 1 else 'ies'}" for t, n in sorted(unloaded.items())]
    if missing:
        items.append(missing_lod_notice(lod, missing))
    return items


def skipped_counts(skipped: Sequence[str]) -> Dict[str, int]:
    """The reader's ``skipped`` entries counted by kind: "2 BuildingRoom" adds 2, "BuildingRoom ID" 1,
    "InteriorWallSurface of Building 'B'" 1, "inner shell of ..." 1."""
    counts: Dict[str, int] = {}
    for entry in skipped:
        head, _, rest = entry.partition(" ")
        if head.isdigit() and rest:
            counts[rest] = counts.get(rest, 0) + int(head)
            continue
        kind = entry.split(" of ")[0] if " of " in entry else head
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# -- the CityJSON files the scene came from (CJ-03 export guard) ---------------------------------

def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def sources(world) -> Dict[str, dict]:
    try:
        data = json.loads(world.get(SOURCES_KEY) or "{}") if world is not None else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_source(world, path: str, partial: bool, loaded: Optional[Dict[str, str]] = None,
                    stat=None) -> None:
    """Record that the scene was (partly: Browse first, one LoD, skipped kinds) built from ``path``.

    CJ-06: ``loaded`` {gml_id: CityJSON id} of the top-level buildings built now (save into the source
    counts a loaded one missing from the scene as deleted); ``stat`` (``cityjson_merge.file_stat``) of
    the file, kept from the first load while buildings of it are loaded."""
    data = sources(world)
    key = _key(path)
    old = data.get(key) or {}
    entry = {"partial": bool(partial or old.get("partial"))}
    ids = dict(old.get("loaded") or {})
    if old.get("stat") and ids:
        entry["stat"] = old["stat"]
    elif stat is not None:
        entry["stat"] = list(stat)
    ids.update(loaded or {})
    if ids:
        entry["loaded"] = ids
    data[key] = entry
    world[SOURCES_KEY] = json.dumps(data, sort_keys=True)


def track_loaded(world, path: str, add: Optional[Dict[str, str]] = None, remove: Sequence[str] = (),
                 stat=None) -> None:
    """CJ-06: change the loaded set of a recorded source (Unload removes, a save adds the new
    buildings and drops the deleted ones) and, after a save, its file stamp."""
    data = sources(world)
    key = _key(path)
    if key not in data:
        return
    entry = dict(data[key])
    ids = {k: v for k, v in (entry.get("loaded") or {}).items() if k not in set(remove)}
    ids.update(add or {})
    entry["loaded"] = ids
    if stat is not None:
        entry["stat"] = list(stat)
    data[key] = entry
    world[SOURCES_KEY] = json.dumps(data, sort_keys=True)


def source_conflict(world, target: str) -> str:
    """The recorded source ``target`` would overwrite ('' when none)."""
    key = _key(target)
    for src in sources(world):
        if src == key or (os.path.exists(src) and os.path.exists(target) and os.path.samefile(src, target)):
            return src
    return ""


def partly_loaded(world) -> List[str]:
    return [p for p, v in sources(world).items() if (v or {}).get("partial")]
