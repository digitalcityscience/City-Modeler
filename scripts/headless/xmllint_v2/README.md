# CityGML 2 real-XSD validation harness

Committed schema entry point + XML catalog for validating exporter output
against the vendored CityGML 2 schemas with genuine `xmllint --schema`.

Motivation (ticket 18): the add-on's built-in `xsd_validate` toggle is not a
real schema oracle. Its "Structure Check" is an internal semantic-rule checker,
and its "XSD Validation" phase is broken for CityGML 2 — the vendored profile
schema imports thematic modules but never declares `core:CityModel`, so every
file reports *"No matching global declaration available for the validation
root"*. This harness replaces that proxy with real validation.

## Files

- `wrapper_v2.xsd` — imports the three modules real exporter output always
  mixes (`building` + `appearance` + `generics`). `cityGMLBase.xsd` (declares
  `core:CityModel`) and the GML 3.1.1 base come in transitively over plain
  `http` from `building.xsd`. `schemaLocation`s are relative to the wrapper, so
  xmllint resolves them from any working directory.
- `catalog.xml` — redirects the unreachable OASIS xAL import
  (`http://docs.oasis-open.org/election/external/xAL.xsd`) to the vendored
  `Schema/citygml_2/xAL/xAL.xsd`.

## Requirements

- `xmllint` on `PATH` (libxml2).
- Network over plain `http` for the transitive GML/cityGMLBase imports (this
  add-on does not vendor them). This xmllint build reaches
  `http://schemas.opengis.net/...`; `https` is not required.

## Run

The automated headless test drives it end to end (export via Blender, then
xmllint via subprocess for `use_semantic_objects` True and False):

```bash
SP=/tmp/cgml-probe-18
mkdir -p "$SP/bscripts/addons"
ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \
  --python scripts/headless/test_ticket18_xmllint_validation.py -- "$SP/out"
```

Or validate an existing CityGML 2 file directly:

```bash
XML_CATALOG_FILES=$PWD/scripts/headless/xmllint_v2/catalog.xml \
  xmllint --noout --schema scripts/headless/xmllint_v2/wrapper_v2.xsd path/to/output.gml
```

Exit `0` = valid. The harness is a genuine oracle: an illegal element makes it
exit `3`.
