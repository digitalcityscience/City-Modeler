# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/citygml_upgrade.py
"""CityGML 1.0 -> 2.0 namespaces and the CityGML 2.0 -> 3.0 IR upgrade (T11c).

The one place where older CityGML is brought forward:

- 1.0 -> 2.0 is a namespace rewrite (2026-10-23 decision, verified on Hamburg
  LoD2 data). ``translate_1_to_2`` rewrites bytes (browse fragments, ``Load
  all`` copies); ``namespace_1_to_2`` maps one URI (the IR reader).
- 2.0 -> 3.0 rewrites a ``SpaceNode`` tree read from a 2.0 (or 1.0) file:

  - 2.0-only feature types via ``CITYGML2_CONCEPTS`` (``IntBuildingInstallation``
    -> ``BuildingInstallation`` + ``interior``, ``Room`` -> ``BuildingRoom``, ...);
    the 2.0 name is kept in ``extra["concept_origin"]`` for writing back;
  - ``lod4*`` -> ``lod3*`` (``lod3_equivalent``, citydb-tool ``use_lod4_as_lod3``).
    An existing lod3 property wins: the lod4 geometry is kept in
    ``extra["unmapped_geometries"]`` and reported as a conflict;
  - ``lodNTerrainIntersection`` -> ``lodNTerrainIntersectionCurve`` (renamed in 3.0);
  - ``lodNGeometry`` (installations, furniture) is NOT mapped: no source for
    the 3.0 equivalent is available locally, so the key stays as read (only
    ``lod4`` -> ``lod3``) and every occurrence is reported;
  - ``lod0FootPrint`` / ``lod0RoofEdge`` -> a GroundSurface / RoofSurface
    boundary with ``lod0MultiSurface`` (id ``<feature id>_lod0FootPrint``).

  Every renamed key is listed in the feature's ``lod_origin`` (comma
  separated, sorted). ``schema_version`` stays as read. The report is a list
  of ``{"gml_id", "from", "to", "note"}`` dicts.

No bpy, no XML library.
"""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import List, Optional

try:
    from .citygml_ir import (CITYGML2_CONCEPTS, GeometryNode, IRError, SpaceNode,
                             ThematicSurfaceNode, lod3_equivalent)
except ImportError:  # Direct-file loading used by pure-Python unit tests.
    from shared.citygml_ir import (CITYGML2_CONCEPTS, GeometryNode, IRError, SpaceNode,
                                   ThematicSurfaceNode, lod3_equivalent)


# ---------------------------------------------------------------------------
# CityGML 1.0 -> 2.0 namespaces
# ---------------------------------------------------------------------------

# Only modules verified with real data.
NAMESPACES_1_TO_2 = (
    (b"http://www.opengis.net/citygml/1.0", b"http://www.opengis.net/citygml/2.0"),
    (b"http://www.opengis.net/citygml/building/1.0", b"http://www.opengis.net/citygml/building/2.0"),
    (b"http://www.opengis.net/citygml/appearance/1.0", b"http://www.opengis.net/citygml/appearance/2.0"),
    (b"http://www.opengis.net/citygml/generics/1.0", b"http://www.opengis.net/citygml/generics/2.0"),
    (b"http://www.opengis.net/citygml/cityobjectgroup/1.0", b"http://www.opengis.net/citygml/cityobjectgroup/2.0"),
)
_NS_1_TO_2 = {old.decode("ascii"): new.decode("ascii") for old, new in NAMESPACES_1_TO_2}
_NS_1_0 = re.compile(rb"http://www\.opengis\.net/citygml/(?:[a-z]+/)?1\.0")


def translate_1_to_2(data: bytes) -> bytes:
    for old, new in NAMESPACES_1_TO_2:
        data = data.replace(old, new)
    return data


def untranslated_1_0_namespaces(data: bytes) -> List[str]:
    """CityGML 1.0 namespaces left after :func:`translate_1_to_2` (no 2.0 mapping)."""
    return sorted({m.group(0).decode("ascii") for m in _NS_1_0.finditer(data)})


def namespace_1_to_2(uri: str) -> str:
    """The 2.0 namespace of a 1.0 ``uri``; any other URI unchanged."""
    return _NS_1_TO_2.get(uri, uri)


# ---------------------------------------------------------------------------
# CityGML 2.0 -> 3.0
# ---------------------------------------------------------------------------

_LOD0_SURFACES = {"lod0FootPrint": "GroundSurface", "lod0RoofEdge": "RoofSurface"}
_KEY = re.compile(r"^lod([0-4])([A-Z]\w*)$")


def _report(report, gml_id, old, new, note=""):
    if report is not None:
        report.append({"gml_id": gml_id, "from": old, "to": new, "note": note})


def _upgrade_key(key: str, geometry: GeometryNode, gml_id: str, report) -> str:
    match = _KEY.match(key)
    if not match:
        return key
    lod, kind = match.groups()
    if kind == "TerrainIntersection":
        kind = "TerrainIntersectionCurve"
    elif kind == "Geometry":
        # No verified 2.0 -> 3.0 rule for lodNGeometry (only compiled citydb-tool
        # jars are available, 3.0 has no such property): the name is kept.
        _report(report, gml_id, key, key, f"{key} has no verified 3.0 property "
                f"(holds {geometry.gml_type or 'unknown'!r}); kept as read")
    try:
        return lod3_equivalent(f"lod{lod}{kind}")
    except IRError:   # not a CityGML property; validate() reports it
        return key


def _upgrade_geometries(node, report, lod0_to_surfaces=True):
    """(geometries, renamed keys, extra, lod0 properties for new surfaces)."""
    geometries, renamed, lod0 = {}, [], []
    extra = node.extra
    # lod4 last, so a lod3 property read from the file always wins.
    items = sorted(node.geometries.items(), key=lambda kv: kv[0].startswith("lod4"))
    for key, geometry in items:
        if lod0_to_surfaces and key in _LOD0_SURFACES:
            lod0.append((key, geometry))
            continue
        new = _upgrade_key(key, geometry, node.gml_id, report)
        if new in geometries:
            _report(report, node.gml_id, key, new, f"conflict: {new} already present; "
                    f"{key} kept in extra['unmapped_geometries']")
            extra = _unmapped(extra, key, geometry)
            continue
        geometries[new] = geometry
        if new != key:
            renamed.append(key)
            _report(report, node.gml_id, key, new)
    return geometries, renamed, extra, lod0


def _unmapped(extra: dict, key: str, geometry: GeometryNode) -> dict:
    extra = copy.deepcopy(extra)
    extra.setdefault("unmapped_geometries", {})[key] = geometry.to_dict()
    return extra


def _lod_origin(node, renamed) -> str:
    keys = set(filter(None, node.lod_origin.split(","))) | set(renamed)
    return ",".join(sorted(keys))


def _lod0_surface(node, key, geometry, report) -> ThematicSurfaceNode:
    surface_type = _LOD0_SURFACES[key]
    gml_id = f"{node.gml_id}_{key}"
    _report(report, node.gml_id, key, f"{surface_type}.lod0MultiSurface", gml_id)
    return ThematicSurfaceNode(gml_id=gml_id, surface_type=surface_type,
                               geometries={"lod0MultiSurface": geometry},
                               schema_version=node.schema_version, source_element=key,
                               parent_id=node.gml_id, lod_origin=key)


def _upgrade_surface(node: ThematicSurfaceNode, report) -> ThematicSurfaceNode:
    geometries, renamed, extra, _ = _upgrade_geometries(node, report, lod0_to_surfaces=False)
    return replace(node, geometries=geometries, extra=extra,
                   lod_origin=_lod_origin(node, renamed),
                   openings=tuple(_upgrade_space(o, report) for o in node.openings))


def _upgrade_space(node, report):
    geometries, renamed, extra, lod0 = _upgrade_geometries(node, report)
    changes = {}
    upgrade = CITYGML2_CONCEPTS.get(getattr(node, "feature_type", ""))
    if upgrade is not None and isinstance(node, SpaceNode):
        extra = copy.deepcopy(extra)
        extra["concept_origin"] = node.feature_type
        changes.update(feature_type=upgrade.target, interior=node.interior or upgrade.interior)
        _report(report, node.gml_id, node.feature_type, upgrade.target)
    elif upgrade is not None:   # BridgeConstructionElement is a constructive element
        extra = copy.deepcopy(extra)
        extra["concept_origin"] = node.feature_type
        changes.update(feature_type=upgrade.target)
        _report(report, node.gml_id, node.feature_type, upgrade.target)
    boundaries = tuple(_upgrade_surface(b, report) for b in node.boundaries)
    boundaries += tuple(_lod0_surface(node, k, g, report) for k, g in lod0)
    if hasattr(node, "spaces"):
        changes.update(spaces=tuple(_upgrade_space(s, report) for s in node.spaces),
                       constructive_elements=tuple(_upgrade_space(c, report)
                                                   for c in node.constructive_elements))
    else:
        changes.update(openings=tuple(_upgrade_space(o, report) for o in node.openings))
    return replace(node, geometries=geometries, extra=extra, boundaries=boundaries,
                   lod_origin=_lod_origin(node, renamed), **changes)


def upgrade_citygml2(root: SpaceNode, report: Optional[list] = None) -> SpaceNode:
    """``root`` (read from CityGML 2.0 or 1.0) rewritten to CityGML 3.0
    concepts; see the module docstring. ``report`` collects what changed."""
    return _upgrade_space(root, report)
