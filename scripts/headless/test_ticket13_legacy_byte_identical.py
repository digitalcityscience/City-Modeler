"""Ticket 13, row 15: legacy single-mesh export (use_semantic_objects=False)
must be unchanged by tickets 06-12.

"Byte-identical" cannot mean literal diff -q: pre-ticket-06 code itself
generates fresh uuid4() gml:ids and a wall-clock creationDate on every
single export run (verified by running the pre-ticket-06 code twice and
diffing the two outputs - they differ in exactly the same way). What must
be unchanged is everything else: byte length, element counts, and the
document structure once ids/timestamps are normalized away.

Usage - run once against a worktree checked out at the last commit before
ticket 06 (the first ticket to touch import/export behaviour) to produce a
reference export, then again against the current working copy with the
flag off, and diff the normalized text:

    git worktree add /tmp/cgml-pre-ticket06 <pre-ticket-06 commit>
    SP=/tmp/cgml-probe-pre06
    mkdir -p $SP/bscripts/addons
    ln -sfn /tmp/cgml-pre-ticket06 $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket13_legacy_byte_identical.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml 2.0 \\
      /tmp/reference_v2.gml

    # then, against the current working copy (flag off is the default path
    # this script always exercises regardless of scene.cgml3.use_semantic_objects):
    BLENDER_USER_SCRIPTS=/tmp/cgml-probe-11/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket13_legacy_byte_identical.py -- \\
      test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml 2.0 \\
      /tmp/candidate_v2.gml

    python3 -c "
import re
def norm(p):
    t = open(p, encoding='utf-8').read()
    t = re.sub(r'gml:id=\"[^\"]*\"', 'gml:id=\"X\"', t)
    t = re.sub(r'<creationDate>[^<]*</creationDate>', '<creationDate>X</creationDate>', t)
    t = re.sub(r'#(UUID|ID)_[0-9a-fA-F]+', '#X', t)
    return t
a, b = norm('/tmp/reference_v2.gml'), norm('/tmp/candidate_v2.gml')
print('NORMALIZED MATCH:', a == b)
"
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def main():
    gml, version, out_path = args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = False
    res = bpy.ops.cgml3.import_gml_file(filepath=gml)
    assert res == {'FINISHED'}, res
    res = bpy.ops.cgml3.export_gml_file(filepath=out_path, citygml_version=version)
    assert res == {'FINISHED'}, res
    print(f"[ticket13-legacy] exported {out_path} ({os.path.getsize(out_path)} bytes)")


main()
