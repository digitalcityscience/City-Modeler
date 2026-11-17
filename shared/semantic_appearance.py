# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Shared, version-independent CityGML source-appearance and semantic-fallback
policy (appearance ticket 02).

Defines the fixed fallback palette, the source-appearance validity and
precedence policy (texture -> material -> semantic fallback), and diagnostics
helpers used to report aggregated import results. Pure Python - no ``bpy``,
no XML parsing, no file I/O - so CityGML 2 and CityGML 3 readers (normal and
streaming) share one policy instead of reimplementing it and drifting apart.

Ring-to-polygon target resolution and Blender material/image creation stay in
the version-specific readers (``io/reader/materials.py``,
``io_v2/reader/materials.py``). This module only receives an already-resolved
polygon context (``PolygonAppearanceContext``) and returns a decision
(``AppearanceResolution``); it never touches ``bpy`` objects, XML elements,
material datablocks, images, or meshes.

This module has no callers yet - integration into the CityGML 2/3 importers
is tracked separately (appearance tickets 04/06/07).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal


# ---------------------------------------------------------------------------
# Fixed fallback palette
# ---------------------------------------------------------------------------


def _hex_to_rgba(hex_value: str) -> tuple[float, float, float, float]:
    """Convert a ``#RRGGBB`` string to an opaque (alpha 1.0) 0-1 float RGBA tuple."""

    hex_value = hex_value.lstrip("#")
    r = int(hex_value[0:2], 16) / 255.0
    g = int(hex_value[2:4], 16) / 255.0
    b = int(hex_value[4:6], 16) / 255.0
    return (r, g, b, 1.0)


@dataclass(frozen=True)
class PaletteEntry:
    material_label: str
    color: tuple[float, float, float, float]


# The eight supported classes' fixed fallback colors (spec table,
# docs/agent/appearance/README.md - Semantic palette). Opaque, non-editable,
# applied identically to Material.diffuse_color and the Principled BSDF Base
# Color by the version-specific material layers that consume this palette.
SEMANTIC_PALETTE: dict[str, PaletteEntry] = {
    "WallSurface": PaletteEntry("CGML_WallSurface", _hex_to_rgba("#C9C2B8")),
    "RoofSurface": PaletteEntry("CGML_RoofSurface", _hex_to_rgba("#A84A3A")),
    "GroundSurface": PaletteEntry("CGML_GroundSurface", _hex_to_rgba("#68635D")),
    "Window": PaletteEntry("CGML_Window", _hex_to_rgba("#245A78")),
    "Door": PaletteEntry("CGML_Door", _hex_to_rgba("#9A572D")),
    "Balcony": PaletteEntry("CGML_Balcony", _hex_to_rgba("#D6A83E")),
    "Chimney": PaletteEntry("CGML_Chimney", _hex_to_rgba("#70483E")),
    "BuildingInstallation": PaletteEntry("CGML_BuildingInstallation", _hex_to_rgba("#D77C2F")),
}


def get_palette_entry(semantic_type: str) -> PaletteEntry | None:
    """Look up the fixed fallback palette entry for a semantic type.

    Returns ``None`` for anything outside the eight supported classes -
    unsupported/unknown non-installation classes keep whatever neutral
    fallback already exists elsewhere; this module introduces no new
    "unknown" color.
    """

    return SEMANTIC_PALETTE.get(semantic_type)


# ---------------------------------------------------------------------------
# Frozen input/result contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextureDescriptor:
    """Normalized, already-resolved facts about a candidate source texture.

    ``target_resolves`` means the appearance target (surface/ring reference)
    resolves to this polygon/ring - source appearance alone, not yet
    promoted. The other three fields are what promote that source appearance
    to *effective appearance* (see CONTEXT.md).
    """

    target_resolves: bool
    image_exists: bool
    image_loads: bool
    has_usable_uv: bool


@dataclass(frozen=True)
class MaterialDescriptor:
    """Normalized, already-resolved facts about a candidate source X3DMaterial."""

    target_resolves: bool
    has_usable_property: bool


@dataclass(frozen=True)
class PolygonAppearanceContext:
    """Stateless input to :func:`resolve_appearance` for a single polygon.

    ``unknown_installation_subtype`` is a pass-through diagnostic signal set
    by the caller (from ``shared/semantic_types.py``'s verdict) when this
    polygon belongs to a ``BuildingInstallation`` whose class/function/usage
    value didn't match a recognized subtype; this module does not resolve
    subtypes itself.
    """

    semantic_type: str
    source_texture: TextureDescriptor | None = None
    source_material: MaterialDescriptor | None = None
    import_appearance_enabled: bool = True
    unknown_installation_subtype: bool = False


AppearanceKind = Literal["texture", "material", "fallback"]
FallbackReason = Literal["none", "absent", "invalid", "disabled"]

# Named once so the outcome vocabulary isn't repeated as bare string literals
# throughout the resolution policy below (mirrors shared/semantic_types.py's
# _UNKNOWN / _GENERIC_INSTALLATION convention).
KIND_TEXTURE: AppearanceKind = "texture"
KIND_MATERIAL: AppearanceKind = "material"
KIND_FALLBACK: AppearanceKind = "fallback"

FALLBACK_REASON_NONE: FallbackReason = "none"
FALLBACK_REASON_ABSENT: FallbackReason = "absent"
FALLBACK_REASON_INVALID: FallbackReason = "invalid"
FALLBACK_REASON_DISABLED: FallbackReason = "disabled"


@dataclass(frozen=True)
class AppearanceResolution:
    """Stateless output of :func:`resolve_appearance` for a single polygon."""

    kind: AppearanceKind
    semantic_type: str
    palette_color: tuple[float, float, float, float] | None
    texture: TextureDescriptor | None
    material: MaterialDescriptor | None
    fallback_reason: FallbackReason = "none"
    unknown_installation_subtype: bool = False


# ---------------------------------------------------------------------------
# Resolution policy: valid source texture -> valid source material -> fallback
# ---------------------------------------------------------------------------


def _texture_is_valid(texture: TextureDescriptor | None) -> bool:
    if texture is None:
        return False
    return (
        texture.target_resolves
        and texture.image_exists
        and texture.image_loads
        and texture.has_usable_uv
    )


def _material_is_valid(material: MaterialDescriptor | None) -> bool:
    if material is None:
        return False
    return material.target_resolves and material.has_usable_property


def resolve_appearance(context: PolygonAppearanceContext) -> AppearanceResolution:
    """Resolve one polygon's effective appearance per the shared precedence policy.

    ``valid source texture -> valid source material -> semantic fallback``.
    Source appearance alone (``target_resolves`` true but nothing else)
    never counts as valid - it falls through like a missing one.
    Disabling ``import_appearance_enabled`` forces fallback regardless of
    what source appearance would otherwise have been valid.
    """

    texture = context.source_texture
    material = context.source_material

    if context.import_appearance_enabled and _texture_is_valid(texture):
        return AppearanceResolution(
            kind=KIND_TEXTURE,
            semantic_type=context.semantic_type,
            palette_color=None,
            texture=texture,
            material=None,
            fallback_reason=FALLBACK_REASON_NONE,
            unknown_installation_subtype=context.unknown_installation_subtype,
        )

    if context.import_appearance_enabled and _material_is_valid(material):
        return AppearanceResolution(
            kind=KIND_MATERIAL,
            semantic_type=context.semantic_type,
            palette_color=None,
            texture=None,
            material=material,
            fallback_reason=FALLBACK_REASON_NONE,
            unknown_installation_subtype=context.unknown_installation_subtype,
        )

    # A descriptor whose target never resolved to this polygon/ring never
    # became source appearance for this polygon - the spec requires treating
    # that as absent, not as a broken attempt at *this* polygon's appearance
    # (ticket 02: "An XML appearance reference with no resolvable texture or
    # material target is treated as absent, not valid"). Only a descriptor
    # whose target does resolve here, but that still fails validity for some
    # other reason (missing image, no UV, no parseable X3DMaterial property),
    # counts as "invalid" - a genuine, warning-worthy appearance problem.
    targeted_this_polygon = (texture is not None and texture.target_resolves) or (
        material is not None and material.target_resolves
    )
    if not context.import_appearance_enabled:
        fallback_reason: FallbackReason = (
            FALLBACK_REASON_DISABLED if targeted_this_polygon else FALLBACK_REASON_ABSENT
        )
    elif targeted_this_polygon:
        fallback_reason = FALLBACK_REASON_INVALID
    else:
        fallback_reason = FALLBACK_REASON_ABSENT

    palette_entry = get_palette_entry(context.semantic_type)
    return AppearanceResolution(
        kind=KIND_FALLBACK,
        semantic_type=context.semantic_type,
        palette_color=palette_entry.color if palette_entry is not None else None,
        texture=None,
        material=None,
        fallback_reason=fallback_reason,
        unknown_installation_subtype=context.unknown_installation_subtype,
    )


# ---------------------------------------------------------------------------
# Diagnostics: the five report categories
# ---------------------------------------------------------------------------


@dataclass
class AppearanceDiagnosticsCounters:
    """Aggregated counts for the import completion report's five categories.

    ``source_textures_used``, ``source_materials_used``, and
    ``semantic_fallback_used`` are mutually exclusive and sum to the total
    number of resolutions tallied - one per :attr:`AppearanceResolution.kind`.
    ``missing_invalid_appearance`` is a *subset* of ``semantic_fallback_used``,
    not a fourth mutually-exclusive bucket: it narrows down to the fallbacks
    caused by broken/unusable source appearance that did target this
    polygon (``fallback_reason == "invalid"``), as opposed to fallbacks where
    no source appearance was present at all (``fallback_reason ==
    "absent"``) or appearance import was disabled (``fallback_reason ==
    "disabled"``). ``unknown_installation_subtypes`` is independent of all of
    the above - it counts polygons flagged via
    :attr:`PolygonAppearanceContext.unknown_installation_subtype`, regardless
    of resolution kind.
    """

    source_textures_used: int = 0
    source_materials_used: int = 0
    semantic_fallback_used: int = 0
    missing_invalid_appearance: int = 0
    unknown_installation_subtypes: int = 0

    def record(self, resolution: AppearanceResolution) -> None:
        if resolution.kind == KIND_TEXTURE:
            self.source_textures_used += 1
        elif resolution.kind == KIND_MATERIAL:
            self.source_materials_used += 1
        elif resolution.kind == KIND_FALLBACK:
            self.semantic_fallback_used += 1
            if resolution.fallback_reason == FALLBACK_REASON_INVALID:
                self.missing_invalid_appearance += 1
        else:  # pragma: no cover - AppearanceKind is exhaustive today
            raise ValueError(f"unknown resolution kind: {resolution.kind!r}")

        if resolution.unknown_installation_subtype:
            self.unknown_installation_subtypes += 1

    def merge(self, other: "AppearanceDiagnosticsCounters") -> None:
        """Add another counters instance's tallies into this one in place."""

        self.source_textures_used += other.source_textures_used
        self.source_materials_used += other.source_materials_used
        self.semantic_fallback_used += other.semantic_fallback_used
        self.missing_invalid_appearance += other.missing_invalid_appearance
        self.unknown_installation_subtypes += other.unknown_installation_subtypes


def tally_resolutions(resolutions: Iterable[AppearanceResolution]) -> AppearanceDiagnosticsCounters:
    """Build a fresh :class:`AppearanceDiagnosticsCounters` from a sequence of resolutions."""

    counters = AppearanceDiagnosticsCounters()
    for resolution in resolutions:
        counters.record(resolution)
    return counters


# ---------------------------------------------------------------------------
# Diagnostics: warning dedup / unknown-subtype summary cap
# ---------------------------------------------------------------------------


@dataclass
class DeduplicatedWarnings:
    """Collapses repeated identical warning values into one entry + a count.

    With ``cap`` set, :meth:`entries` returns only the first ``cap`` distinct
    values (insertion order) and :meth:`overflow_count` reports how many
    additional distinct values were dropped - used for the unknown-subtype
    summary, which caps at the first 20 distinct normalized values plus a
    count of additional uniques.
    """

    cap: int | None = None
    _counts: dict[str, int] = field(default_factory=dict, repr=False)
    _order: list[str] = field(default_factory=list, repr=False)

    def add(self, value: str) -> None:
        normalized = str(value).strip()
        if normalized not in self._counts:
            self._order.append(normalized)
        self._counts[normalized] = self._counts.get(normalized, 0) + 1

    @property
    def distinct_count(self) -> int:
        return len(self._order)

    def entries(self) -> list[tuple[str, int]]:
        """Distinct values in first-seen order, each paired with its occurrence count."""

        shown = self._order if self.cap is None else self._order[: self.cap]
        return [(value, self._counts[value]) for value in shown]

    def overflow_count(self) -> int:
        """Distinct values beyond ``cap`` that :meth:`entries` omitted (0 if uncapped)."""

        if self.cap is None:
            return 0
        return max(0, len(self._order) - self.cap)


UNKNOWN_SUBTYPE_SUMMARY_CAP = 20


def new_unknown_subtype_summary() -> DeduplicatedWarnings:
    """A :class:`DeduplicatedWarnings` pre-configured for the unknown-subtype report."""

    return DeduplicatedWarnings(cap=UNKNOWN_SUBTYPE_SUMMARY_CAP)
