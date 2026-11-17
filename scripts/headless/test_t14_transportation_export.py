"""T14: Transportation parts stay inside their Road on export (both writers).

For test_files/coverage/Transportation_citygml{2,3}.gml, and a copy of the 3.0
file whose TrafficAreas use the schema-valid core:boundary (a built
TrafficSpace), export to CityGML 2.0 and 3.0 and check:
  - exactly the three top-level members (Road, Track, Square),
  - every part id of the Road (TrafficArea, AuxiliaryTrafficArea, Intersection,
    TrafficSpace) is written inside the Road element,
  - the export is XSD valid (the add-on's offline validators),
  - re-import gives the same polygon count per top-level feature and in total.

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-exit-code 1 \
      --python scripts/headless/test_t14_transportation_export.py -- <out_dir>
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

FAILURES = []


def check(cond, label):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


def load(path):
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.cgml3.xsd_validate = False
    bpy.ops.cgml3.import_gml_file(filepath=path, load_mode='ALL')
    return addon


def polygons():
    """{top-level gml_id: polygons of it and its descendants}, total."""
    tops = {}
    for o in bpy.data.objects:
        if o.get("gml_id") and o.parent is None:
            tops[str(o["gml_id"])] = sum(len(c.data.polygons) for c in (o, *o.children_recursive) if c.type == 'MESH')
    return tops, sum(len(o.data.polygons) for o in bpy.data.objects if o.type == 'MESH')


def validate(addon, path, version):
    if version == "3.0":
        return sys.modules[f"{addon}.ops.validate"].validate_citygml3_xsd(path)
    return sys.modules[f"{addon}.ops.validate_citygml2"].validate_citygml2_xsd(path, fallback_online=False)


def run_case(src, out_dir):
    name = os.path.splitext(os.path.basename(src))[0]
    addon = load(src)
    ref, ref_total = polygons()
    road = next(o for o in bpy.data.objects if o.get("cgml3_feature") == "Road")
    part_ids = sorted(str(c["gml_id"]) for c in road.children_recursive if c.get("gml_id"))
    for version in ("2.0", "3.0"):
        print(f"[t14] {name} -> {version}")
        load(src)
        path = os.path.join(out_dir, f"t14_{name}_to_{version}.gml")
        bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
        text = open(path, encoding="utf-8").read()
        members = len(re.findall(r"<(?:\w+:)?cityObjectMember>", text))
        check(members == 3, f"{name} {version}: 3 top-level members (got {members})")
        body = re.search(r"<tran:Road\b.*?</tran:Road>", text, re.S)
        missing = [i for i in part_ids if not body or f'gml:id="{i}"' not in body.group(0)]
        check(not missing, f"{name} {version}: parts {part_ids} inside the Road (missing {missing})")
        ok, msg = validate(addon, path, version)
        check(ok, f"{name} {version}: XSD valid ({msg.strip().splitlines()[-1][:200] if msg else ''})")
        load(path)
        got, got_total = polygons()
        check(got == ref, f"{name} {version}: polygons per feature {ref} -> {got}")
        check(got_total == ref_total, f"{name} {version}: polygons in the scene {ref_total} -> {got_total}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = os.path.abspath(argv[0] if argv else os.path.join(REPO, "t14_out"))
    os.makedirs(out_dir, exist_ok=True)
    coverage = os.path.join(REPO, "test_files", "coverage")
    v3 = os.path.join(coverage, "Transportation_citygml3.gml")
    # schema-valid 3.0: a TrafficArea is a core:boundary of its TrafficSpace
    boundary = os.path.join(out_dir, "Transportation_citygml3_boundary.gml")
    text = open(v3, encoding="utf-8").read()
    text = re.sub(r"(</?)tran:(?:auxiliaryT|t)rafficArea>", r"\1core:boundary>", text)
    open(boundary, "w", encoding="utf-8").write(text)
    for src in (os.path.join(coverage, "Transportation_citygml2.gml"), v3, boundary):
        run_case(src, out_dir)
    print(f"[t14] {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAIL'}")
    if FAILURES:
        sys.exit(1)


main()
