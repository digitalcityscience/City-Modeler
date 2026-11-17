"""B42: the georeference World is found by its keys, not by the name "World".

- import, rename World -> "Site", export: coordinates still match the source (1 mm)
- the exporter's envelope CRS is still the source's
- a fresh scene without keys still gets a "World" from georef_world()

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b42_world_by_keys.py
"""
import os
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
GML = os.path.join(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"), "buildings_lod2_citygml_3_0.gml")
TMP = tempfile.mkdtemp(prefix="b42_")


def polygons(path):
    """{polygon gml:id: set of exterior points rounded to 1 mm}, GML 3.1.1 or 3.2."""
    import xml.etree.ElementTree as ET
    out = {}
    for poly in ET.parse(path).getroot().iter():
        if not poly.tag.endswith("}Polygon"):
            continue
        ns = poly.tag[:poly.tag.index("}") + 1]
        pos = poly.find(f".//{ns}exterior//{ns}posList")
        if pos is None or not poly.get(ns + "id"):
            continue
        v = [float(t) for t in pos.text.split()]
        out[poly.get(ns + "id")] = {tuple(round(c, 3) for c in v[i:i + 3]) for i in range(0, len(v), 3)}
    return out


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    gr = import_module(f"{addon}.shared.georef")
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in list(bpy.data.worlds):
        bpy.data.worlds.remove(w)
    check("empty scene: world() is None", gr.world() is None)
    w = gr.georef_world(bpy.context.scene)
    check("georef_world creates 'World'", w is not None and w.name == "World" and bpy.context.scene.world is w)

    res = bpy.ops.cgml3.import_gml_file(filepath=GML)
    check("import finished", res == {'FINISHED'}, str(res))
    w = bpy.data.worlds["World"]
    w.name = "Site"
    check("renamed world still found", gr.world() is w, str(gr.world()))
    bpy.data.worlds.new("World")  # a key-less "World" must not win over the one with the keys
    check("key-less 'World' does not shadow it", gr.world() is w, str(gr.world()))

    for o in bpy.context.view_layer.objects:
        o.select_set(True)
    for v in ("3.0", "2.0"):
        out = os.path.join(TMP, f"renamed_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=out, citygml_version=v)
        check(f"{v}: export finished", res == {'FINISHED'}, str(res))
        head = open(out, encoding="utf-8").read(4000) if os.path.exists(out) else ""
        check(f"{v}: envelope in the source CRS", "25832" in head, head[:300])
        src, got = polygons(GML), polygons(out) if os.path.exists(out) else {}
        common = set(src) & set(got)
        bad = [i for i in common if src[i] != got[i]]
        check(f"{v}: polygons compared", len(common) >= 0.9 * len(src), f"{len(common)} of {len(src)}")
        check(f"{v}: coordinates match the source (1 mm)", not bad, str(bad[:3]))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
