# CityGML 3 real-XSD validation harness

Committed schema entry point + XML catalog for validating CityGML 3 files
against the vendored CityGML 3.0 schemas with genuine `xmllint --schema`.
Mirrors `scripts/headless/xmllint_v2/` (ticket 18) for CityGML 2.

Motivation (appearance ticket 03/04 follow-up): the appearance fixture
manifest (`test_files/Building/SemanticHierarchy_Appearance_Fixtures_Manifest.md`)
originally noted the 6 new CityGML 3 appearance fixtures were only validated
ad hoc against the vendored `Schema/citygml_3` XSDs, with no committed
harness. This directory is that harness.

## Files

- `wrapper_v3.xsd` — imports `building` and `appearance` (the modules real
  fixtures/exporter output mix; CityGML 3 moved `WallSurface`/`RoofSurface`/
  `GroundSurface`/`Window`/`Door` into `con:*`, which `building.xsd` already
  imports transitively, along with `core.xsd` and the GML 3.2.1 base, over
  plain `http`). `schemaLocation`s are relative to the wrapper, so xmllint
  resolves them from any working directory.
- `catalog.xml` — redirects the xAL 3.0 import inside `core.xsd`
  (`urn:oasis:names:tc:ciq:xal:3`) to the vendored
  `Schema/citygml_3/xAL/3.0/xAL.xsd`.

## Requirements

- `xmllint` on `PATH` (libxml2).
- Network over plain `http` for the transitive `core.xsd`/GML imports (this
  add-on does not vendor a fetchable copy of `building.xsd`'s own
  network-facing imports) — same requirement as the v2 harness.

## Run

Validate a CityGML 3 file directly:

```bash
XML_CATALOG_FILES=$PWD/scripts/headless/xmllint_v3/catalog.xml \
  xmllint --noout --schema scripts/headless/xmllint_v3/wrapper_v3.xsd path/to/file.gml
```

Or run the automated check across all `SemanticHierarchy_Appearance_*_CityGML3.gml`
fixtures:

```bash
python3 scripts/headless/xmllint_v3/validate_appearance_fixtures.py
```

Exit `0` = valid. The harness is a genuine oracle: an illegal element makes it
exit `3`.
