# Semantic Hierarchy Appearance fixture manifest

Appearance ticket 03 (`docs/agent/appearance/issues/03-appearance-fixtures.md`). Each
scenario below exists as a paired `_CityGML2.gml` / `_CityGML3.gml` file in this
directory. All are LoD3, follow the existing `SemanticHierarchy_*` naming
convention, and validate against real `xmllint --schema` (CityGML 2 via
`scripts/headless/xmllint_v2/`, ticket 18; CityGML 3 via
`scripts/headless/xmllint_v3/`, run with
`python3 scripts/headless/xmllint_v3/validate_appearance_fixtures.py`).

Do not modify `SemanticHierarchy_Appearance_LoD3_CityGML{2,3}.gml` — pre-existing,
relied on by other work (ticket 13's round-trip matrix).

Texture fixtures reference `SemanticHierarchy_Appearance_facade.png` (a real,
loadable 4x4 PNG, committed alongside the `.gml` files in this directory — not
under `Appearance/`, which `.gitignore` excludes as a local-test-artifact
directory) and a deliberately absent `SemanticHierarchy_Appearance_missing.png`
(texture-fallback scenario only — never created on purpose).

## Scenarios

### `SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML{2,3}.gml`
Mixed-LoD geometry, no appearance anywhere (pure semantic fallback). WallSurface,
RoofSurface, and generic BuildingInstallation each appear twice — once at LoD2,
once at LoD3 — as separate features. Proves LoD level doesn't drive fallback
color: the LoD2 and LoD3 instances of the same class must resolve to the same
palette color. Also includes GroundSurface, Window, Door (LoD3 only — openings
don't exist at LoD2 in this schema), a class-resolved Balcony, and a
function-resolved Chimney, for full 8-class coverage.

### `SemanticHierarchy_Appearance_PartialAppearance_LoD3_CityGML{2,3}.gml`
Partial appearance within one object (User Story 5). The WallSurface has two
polygons in one `lod3MultiSurface`/`MultiSurface`: one carries a valid
`X3DMaterial`, the other has none. The RoofSurface likewise has two polygons,
one with a valid `ParameterizedTexture`, one without. Proves polygon-level
independence — the unstyled polygon must still fall back even though its
sibling in the same feature/mesh is styled. Window, Door, GroundSurface,
generic BuildingInstallation, Balcony (class), Chimney (function) are present
unstyled for 8-class coverage.

### `SemanticHierarchy_Appearance_TextureFallback_LoD3_CityGML{2,3}.gml`
Missing/broken texture reference with a valid `X3DMaterial` fallback present
(User Story 6). The WallSurface polygon and the Balcony (class-resolved)
polygon each carry a `ParameterizedTexture` whose `imageURI` points at a file
that does not exist, *and* a valid `X3DMaterial` targeting the same polygon.
Proves texture -> material fall-through: the polygon must resolve to the
material's color, not semantic fallback, since the texture target does resolve
to the polygon (making it an "invalid", not "absent", appearance). Window,
Door, RoofSurface, GroundSurface, generic BuildingInstallation, Chimney
(function) are present unstyled as a plain-fallback baseline.

### `SemanticHierarchy_Appearance_InstallationSubtype_LoD3_CityGML{2,3}.gml`
Balcony/Chimney resolved via `class`/`function`/`usage`/codelist, never via
explicit feature type (there is no dedicated Balcony/Chimney element in either
schema version — ticket 01's strict resolver only ever reaches Balcony/Chimney
through `BuildingInstallation`). Five installations, each named neutrally
(`Installation A`..`E`, never "Balcony"/"Chimney") so only the tested rung can
fire:
- `class=5010` -> Balcony
- `function=5030` -> Chimney
- `usage=5020` (Loggia synonym, numeric codelist code) -> Balcony
- `usage=Chimney` (codelist description text, not a feature-type match) -> Chimney
- bare (no class/function/usage) -> generic `BuildingInstallation`

Wall, Window, Door, RoofSurface, GroundSurface are present unstyled for 8-class
coverage.

### `SemanticHierarchy_Appearance_UnknownSubtype_LoD3_CityGML{2,3}.gml`
An unsupported/unrecognized `BuildingInstallation` subtype value stays generic,
never fabricated (User Story 13) or inferred from unreliable signals (User
Story 14). Four installations, all expected to resolve to generic
`BuildingInstallation`:
- `class=9999` — an entirely unrecognized numeric code
- `function=Awning` — an entirely unrecognized free-text value
- `class=5060` (Antenna) — a *recognized* local-codelist entry that has no
  Balcony/Chimney canonical-type mapping (see
  `shared/semantic_types.py::CANONICAL_TYPE_INSTALLATION_CODE`)
- `gml:name=Balcony`, no class/function/usage evidence — the ticket 01
  name-trap: a literal "Balcony" name must never sway the verdict

This scenario deliberately has no resolved Balcony/Chimney instance (that
would dilute the point); Wall, Window, Door, RoofSurface, GroundSurface are
present unstyled for the other 5 of the 8 classes.

### `SemanticHierarchy_Appearance_FullAppearance_LoD3_CityGML{2,3}.gml`
Fully valid source appearance on every one of the 8 classes (User Story 21) —
proves this work never regresses an appearance-rich import. WallSurface, Roof,
and Balcony use a real `ParameterizedTexture` (loadable image + full texture
coordinates); Window, Door, GroundSurface, generic BuildingInstallation, and
Chimney use a valid `X3DMaterial`. No polygon in this fixture should ever
resolve to semantic fallback.
