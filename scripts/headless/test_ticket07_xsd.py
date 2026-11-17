"""Ticket 07: the semantic-object export must validate against the
CityGML 3.0 XSD schema."""
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
    res = bpy.ops.cgml3.export_gml_file(filepath=out_path)
    print("export result:", res)
    assert res == {'FINISHED'}, res


main()
