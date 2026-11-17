"""Ticket 11: the CityGML 2 semantic-object export must validate against
the CityGML 2.0 XSD schema.

NOTE (ticket 18): this script only drives the exporter's built-in
`xsd_validate` toggle, which is NOT a real schema oracle — its "Structure
Check" is an internal semantic-rule checker and its "XSD Validation" phase is
broken for CityGML 2 (the vendored profile schema never declares
core:CityModel). Asserting the export op returns FINISHED here proves the run
completes, not that the output is schema-valid. Genuine `xmllint --schema`
validation of this exact scenario now lives in
`test_ticket18_xmllint_validation.py`; treat that as the authoritative XSD
oracle for the CityGML 2 semantic export."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def main():
    gml, out_dir = args()[0], args()[1]
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "xsd_check.gml")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, res

    bpy.context.scene.cgml3.xsd_validate = True
    res = bpy.ops.cgml3.export_gml_file(filepath=out_path, citygml_version='2.0')
    print("export result:", res)
    assert res == {'FINISHED'}, res


main()
