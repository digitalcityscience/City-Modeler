# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Canonical semantic subtype resolver for CityGML building details.

Decides whether a Window/Door/BuildingInstallation feature is a Window,
Door, Balcony, Chimney, generic BuildingInstallation, or Unknown, and which
rung of the priority chain decided it. Pure Python (no bpy, no XML), shared
by both CityGML 2 and CityGML 3 readers so the priority chain lives in one
place.

Strict subtype policy (appearance ticket 01): a Balcony/Chimney verdict can
only come from explicit feature type, then class, then function, then
usage, then a recognized local installation codelist entry, in that order.
``gml:name`` and generic attributes are accepted as parameters (callers
already pass them, and the importer keeps storing them as object data) but
are never consulted for classification - no fallback rung reads them. This
also means there is no mechanism here that lets an unrecognized ADE element
influence the verdict; the resolver simply has no input path for one.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


CANONICAL_TYPES = (
    "Window",
    "Door",
    "Balcony",
    "Chimney",
    "BuildingInstallation",
    "Unknown",
)

_UNKNOWN = "Unknown"
_GENERIC_INSTALLATION = "BuildingInstallation"

_FEATURE_TYPE_MATCHES = {
    "window": "Window",
    "windowsurface": "Window",
    "door": "Door",
    "doorsurface": "Door",
    "balcony": "Balcony",
    "balconysurface": "Balcony",
    "chimney": "Chimney",
    "chimneysurface": "Chimney",
}

# BuildingInstallation's class/function/usage elements are gml:CodeType (a
# free-form string plus an optional codeSpace attribute) in both CityGML 2
# and 3 - see building.xsd, BuildingInstallationType. Unlike bldg:class on
# Building itself, the standard does not ship a fixed enumeration for
# installation subtypes, so there is no external authority code to defer to.
# This is this project's own local codelist for BuildingInstallation class/
# function/usage values. It is the source of truth for the ticket-03 fixtures
# under docs/agent/semantic-hierarchy/fixtures/ - do not repurpose these codes
# for anything else (Building class/function codes describe the Building, not a
# BuildingInstallation, and use an unrelated numbering range).
INSTALLATION_CLASS_CODES = {
    "5010": "Balcony",
    "5020": "Loggia",
    "5030": "Chimney",
    "5040": "Dormer",
    "5050": "Outside stairs",
    "5060": "Antenna",
    "5070": "Other building installation",
}
INSTALLATION_FUNCTION_CODES = {**INSTALLATION_CLASS_CODES}
INSTALLATION_USAGE_CODES = {**INSTALLATION_CLASS_CODES}

# B44: the code list CityGML itself names for BuildingInstallation function/usage
# (OGC 12-019 Annex C.3, SIG3D code list "BuildingInstallation_function",
# http://www.sig3d.org/codelists/standard/building/2.0/BuildingInstallation_function.xml;
# the same codes serve usage). The local 5010/5030 list above is kept only to
# recognise files written before B44.
INSTALLATION_FUNCTION_CODESPACE = (
    "http://www.sig3d.org/codelists/standard/building/2.0/BuildingInstallation_function.xml"
)
SIG3D_INSTALLATION_FUNCTION_CODES = {
    "1000": "balcony",
    "1010": "winter garden",
    "1020": "arcade",
    "1030": "chimney (part of a building)",
    "1040": "tower (part of a building)",
    "1050": "column",
    "1060": "stairs",
    "1070": "others",
}

# The one codelist code an exporter should write for a canonical type that has
# no source provenance at all (ticket 04: an object authored fresh in
# Blender, never imported from CityGML - cgml_subtype_origin absent). Written
# as bldg:function with INSTALLATION_FUNCTION_CODESPACE (B44). Only canonical
# types the SIG3D list covers get an entry - Window/Door/BuildingInstallation
# (generic)/Unknown are never written this way, since writing a codelist value
# for those would fabricate semantics CityGML itself has real vocabulary for
# (Window/Door are dedicated feature types) or that were never resolved to a
# specific subtype in the first place.
CANONICAL_TYPE_INSTALLATION_CODE = {
    "Balcony": "1000",
    "Chimney": "1030",
}

# class/function/usage free-text matching. Out-of-scope-for-ticket-appearance-01
# entries (window/door/buildinginstallation synonyms) are pre-existing,
# unrelated to the Balcony/Chimney subtype resolver this ticket tightens, and
# are left as they were. The Balcony/Chimney/Loggia entries, however, are the
# ticket's actual scope: they are restricted to exactly the local installation
# codelist's description text (INSTALLATION_CLASS_CODES below) - no additional
# synonyms like the old "flue" - so that class/function/usage matching for
# Balcony/Chimney recognizes only a real codelist entry, never a loose synonym.
_ATTRIBUTE_MATCHES = {
    "balcony": "Balcony",
    "loggia": "Balcony",
    "chimney": "Chimney",
    "window": "Window",
    "windowsurface": "Window",
    "door": "Door",
    "doorsurface": "Door",
    "buildinginstallation": "BuildingInstallation",
}

# Balcony/Chimney matching (ticket appearance-01: strict policy) recognizes
# only the local installation codelist above - both its numeric code ("5010")
# and its description text ("Balcony", case/punctuation-insensitive). Of the
# seven codelist entries, only the ones that name a canonical Balcony/Chimney
# subtype resolve here; Dormer/Outside stairs/Antenna/Other building
# installation are recognized codelist entries but have no canonical-type
# mapping, so a class/function/usage value of e.g. "5070" correctly falls
# through to the generic BuildingInstallation rung rather than resolving.
_CODE_MATCHES = {
    code: _ATTRIBUTE_MATCHES[description.lower()]
    for code, description in INSTALLATION_CLASS_CODES.items()
    if description.lower() in _ATTRIBUTE_MATCHES
}
# B44: SIG3D function/usage codes; not for bldg:class, whose SIG3D list gives
# 1000 another meaning ("outer characteristics").
_FUNCTION_CODE_MATCHES = {"1000": "Balcony", "1030": "Chimney"}


@dataclass(frozen=True)
class SemanticTypeVerdict:
    canonical_type: str
    origin: str
    source_feature_type: str


def resolve_semantic_type(
    *,
    source_feature_type: str = "",
    class_value: str = "",
    function_value: str = "",
    usage_value: str = "",
    generic_attributes: Mapping[str, object] | None = None,
    name: str = "",
) -> SemanticTypeVerdict:
    """Resolve a CityGML building detail to a canonical semantic subtype.

    The resolver accepts already-extracted strings and stays independent from
    Blender and XML libraries so both schema readers can share it.

    ``generic_attributes`` and ``name`` are accepted for call-site convenience
    (callers already have a ``gml:name`` and generic attributes in hand from
    parsing) but are intentionally never consulted here - the strict policy
    (appearance ticket 01) only lets feature type, then class, then function,
    then usage, then a recognized local installation codelist entry decide a
    Balcony/Chimney verdict. Neither parameter, nor any unrecognized ADE
    element, can influence the result.
    """

    source_feature_type = str(source_feature_type or "")
    checks = (
        ("feature_type", source_feature_type),
        ("class", class_value),
        ("function", function_value),
        ("usage", usage_value),
    )
    for origin, value in checks:
        resolved = _resolve_value(value, feature_type=(origin == "feature_type"))
        if not resolved and origin in ("function", "usage"):
            resolved = _FUNCTION_CODE_MATCHES.get(_normalize(value), "")
        if resolved:
            return SemanticTypeVerdict(resolved, origin, source_feature_type)

    if _is_installation_feature(source_feature_type):
        return SemanticTypeVerdict(_GENERIC_INSTALLATION, "generic", source_feature_type)

    return SemanticTypeVerdict(_UNKNOWN, "unknown", source_feature_type)


def _resolve_value(value: object, *, feature_type: bool = False) -> str:
    normalized = _normalize(value)
    if not normalized:
        return ""
    if feature_type:
        return _FEATURE_TYPE_MATCHES.get(normalized, "")
    if normalized in _ATTRIBUTE_MATCHES:
        return _ATTRIBUTE_MATCHES[normalized]
    return _CODE_MATCHES.get(normalized, "")


def _is_installation_feature(value: object) -> bool:
    normalized = _normalize(value)
    return normalized in {"buildinginstallation", "intbuildinginstallation"}


def _normalize(value: object) -> str:
    text = str(value or "").strip().lower()
    if ":" in text:
        text = text.rsplit(":", 1)[1]
    return re.sub(r"[^a-z0-9]+", "", text)
