"""B32: our World keys are cgml_CRS / cgml_X-Origin / cgml_Y-Origin / cgml_Z-Origin / cgml_Z-EPSG.

- a .blend with our pre-B32 keys (CRS, X-Origin, ...) is migrated on load: new keys, legacy keys gone
- a .blend with only CRS but CityGML features is migrated too
- a World holding only CityJSONEditor-style CRS + X_Origin (no features) is NOT adopted
- import writes only the new keys; export coordinates still match the source

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b32_own_world_keys.py
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402

GML = os.path.join(os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings"), "buildings_lod2_citygml_3_0.gml")
GML_NS = "{http://www.opengis.net/gml/3.2}"
LEGACY = {"CRS": "EPSG:25832", "X-Origin": 564800.0, "Y-Origin": 5934300.0, "Z-Origin": 17.0, "Z-EPSG": "7837"}
TMP = tempfile.mkdtemp(prefix="b32_")


def fresh():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in list(bpy.data.worlds):
        bpy.data.worlds.remove(w)
    bpy.context.scene.world = bpy.data.worlds.new("World")
    return bpy.context.scene.world


def save_and_reopen(name):
    path = os.path.join(TMP, name)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)  # load_post runs the migration
    return bpy.data.worlds["World"]


def polygons(path):
    """{polygon gml:id: set of exterior points rounded to 1 mm}"""
    out = {}
    for poly in ET.parse(path).getroot().iter(GML_NS + "Polygon"):
        pos = poly.find(f".//{GML_NS}exterior//{GML_NS}posList")
        if pos is None or not poly.get(GML_NS + "id"):
            continue
        v = [float(t) for t in pos.text.split()]
        out[poly.get(GML_NS + "id")] = {tuple(round(c, 3) for c in v[i:i + 3]) for i in range(0, len(v), 3)}
    return out


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    gr = import_module(f"{addon}.shared.georef")
    header = import_module(f"{addon}.ui.header_panel")
    check("key names", tuple(getattr(gr, n, None) for n in ("CRS", "X_ORIGIN", "Y_ORIGIN", "Z_ORIGIN", "Z_EPSG")) ==
          ("cgml_CRS", "cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin", "cgml_Z-EPSG"))

    # 1. our legacy keys (hyphen origin) -> migrated
    w = fresh()
    for k, v in LEGACY.items():
        w[k] = v
    w = save_and_reopen("legacy.blend")
    check("legacy: values under the new names",
          all(w.get("cgml_" + k) == v for k, v in LEGACY.items()), str(dict(w.items())))
    check("legacy: old names removed", not any(k in w for k in LEGACY), str(list(w.keys())))
    check("legacy: sidebar EPSG", header.scene_epsg(bpy.context.scene) == "EPSG:25832")

    # 2. only CRS, but CityGML features in the file -> ours
    w = fresh()
    w["CRS"] = "EPSG:25833"
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object["cgml3_feature"] = "Building"
    w = save_and_reopen("legacy_crs_only.blend")
    check("CRS-only with features: migrated", w.get("cgml_CRS") == "EPSG:25833" and "CRS" not in w,
          str(dict(w.items())))

    # 3. CityJSONEditor-style World, no CityGML features -> not adopted
    w = fresh()
    w["CRS"], w["X_Origin"], w["Y_Origin"] = "EPSG:3857", 1000000.0, 7000000.0
    w = save_and_reopen("cityjsoneditor.blend")
    check("CityJSONEditor keys left alone", w.get("CRS") == "EPSG:3857" and "X_Origin" in w,
          str(dict(w.items())))
    check("CityJSONEditor keys not adopted", not any(k.startswith("cgml_") for k in w.keys()),
          str(list(w.keys())))
    check("sidebar does not show the foreign CRS", header.scene_epsg(bpy.context.scene) is None,
          str(header.scene_epsg(bpy.context.scene)))

    # 4. import into that scene: our keys from the file, the foreign ones untouched; round-trip coords
    res = bpy.ops.cgml3.import_gml_file(filepath=GML)
    check("import finished", res == {'FINISHED'}, str(res))
    check("import wrote cgml_* from the file", "25832" in str(w.get("cgml_CRS"))
          and 564000 < float(w.get("cgml_X-Origin", 0)) < 565000, str(dict(w.items())))
    check("import left the foreign keys", w.get("CRS") == "EPSG:3857" and w.get("X_Origin") == 1000000.0)
    check("import wrote no legacy keys", not any(k in w for k in ("X-Origin", "Y-Origin", "Z-Origin")))
    for o in bpy.context.view_layer.objects:
        o.select_set(True)
    out = os.path.join(TMP, "roundtrip.gml")
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=out, citygml_version="3.0")
    except RuntimeError as exc:
        res = str(exc)
    check("export finished", res == {'FINISHED'}, str(res)[:300])
    src, got = polygons(GML), polygons(out) if os.path.exists(out) else {}
    common = set(src) & set(got)
    bad = [i for i in common if src[i] != got[i]]
    check("round-trip: polygons compared", len(common) >= 0.9 * len(src), f"{len(common)} of {len(src)}")
    check("round-trip: coordinates match the source (1 mm)", not bad, str(bad[:3]))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
