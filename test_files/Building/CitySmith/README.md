# CitySmith multi-LoD building fixtures

Output of the `CitySmith` LoD generator (a separate project; its source is not
part of this add-on and is git-ignored at the repository root). The files are
copied here, under their original names, so fixture-backed tests do not depend
on an external working copy being present.

All files are **CityGML 2.0**. Two buildings, each emitted at several LoDs from
the same source model — which is what makes them useful: the *same* building at
LoD1, LoD2 and LoD3 lets an authoring change be checked against a reference
rather than against itself.

## Contents

| File | Building | Geometry properties | Walls / Roofs / Ground | Windows | Installations |
|---|---|---|---|---|---|
| `CS1_lod0.gml` | CS1 (+1 BuildingPart) | `lod0FootPrint` | – | – | – |
| `CS1_lod1.gml` | CS1 | `lod1Solid` | – | – | – |
| `CS1_lod2.gml` | CS1 | `lod2Solid`, `lod2MultiSurface` | 16 / 20 / 2 | – | – |
| `CS1_lod3.gml` | CS1 | `lod3Solid`, `lod3MultiSurface`, `lod3Geometry`, `lod3TerrainIntersection` | 17 / 21 / 2 | 25 | 2 |
| `CS1_lod3_enhanced.gml` | CS1 | as above | 17 / 21 / 2 | 25 | 2 |
| `CS1_multiLOD.gml` | CS1 | LoD0–LoD3 in one Building | 33 / 41 / 4 | 25 | 2 |
| `CS2_lod0.gml` | CS2 | `lod0FootPrint` | – | – | – |
| `CS2_lod1.gml` | CS2 | `lod1Solid` | – | – | – |
| `CS2_lod2.gml` | CS2 | `lod2Solid`, `lod2MultiSurface` | 1 / 1 / 1 | – | – |
| `CS2_lod3.gml` | CS2 | `lod3Solid`, `lod3MultiSurface`, `lod3TerrainIntersection` | 15 / 15 / 1 | – | 14 |
| `CS2_lod3_enhanced.gml` | CS2 | as above, plus `lod3Geometry` | 15 / 15 / 1 | – | 14 |
| `CS2_multiLOD.gml` | CS2 | LoD0–LoD3 in one Building | 16 / 16 / 2 | – | 14 |
| `CS*_multiLOD.citydoctor.xml` | – | CityDoctor validation reports for the multiLOD files | | | |

## What each pair is good for

**`_lod3` vs `_lod3_enhanced`** — the same geometry with and without semantics.
The plain file's installations carry no `bldg:function`; the enhanced file adds
`gml:id`s, `bldg:function` codes (SIG3D: balcony `1000`, chimney `1030`), a
`gen:stringAttribute name="type"`, and an aggregating `lod3Geometry`. This is
the direct fixture pair for ADR 0008:

- `CS1_lod3.gml` — installations must resolve to **generic
  `BuildingInstallation`** (no code to read, and the strict policy forbids
  deciding a subtype from `gen:stringAttribute` or `gml:name`).
- `CS1_lod3_enhanced.gml` — the `function=1000` installation must resolve to
  **`Balcony`**, and `function=1030` to **`Chimney`**.

**`CS1_lod2.gml` vs `CS1_lod3.gml`** — the same building without and with 25
Windows. The reference comparison for authored facade elements: adding windows
to the LoD2 model through `place_semantic_element` should converge on the LoD3
file's semantic structure (Window features under their WallSurface, correct
`lod3*` geometry properties), so any divergence is a defect in the authoring
path rather than a matter of opinion.

**`CS2_lod3*.gml`** — 14 installations on one building, all chimneys. The bulk
case for installation handling and for the single-`lod{N}Geometry` grouping
rule.

**`*_multiLOD.gml`** — several LoDs on one Building. Exercises LoD resolution
and the "preserve available higher-LoD details" export policy on a file where
more than one LoD is genuinely present.

## Geometry notes worth knowing before writing assertions

- The balcony in `CS1_lod3_enhanced.gml` is an **open shell**: 10 of its 32
  edges are used once, all lying in the wall-side plane. There is no polygon on
  the attachment plane. See `docs/agent/balcony_implementation/design.md` D2.
- The chimney in the same file uses `bldg:boundedBy` (`RoofSurface`,
  `WallSurface`) for its real polygons and an **xlink-only** `lod3Geometry`
  that references them (`<gml:surfaceMember xlink:href="#…"/>`). A reader that
  follows both without deduplication will count that geometry twice.

## Refreshing

Regenerate with CitySmith and copy the files here. Keep the names — tests and
the documents above reference them by name.
