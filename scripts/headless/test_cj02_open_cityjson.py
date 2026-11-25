"""CJ-02: CityJSON opens through the same Open button and the same builder as CityGML.

- buildings_lod2 / lod3 CityJSON: the scene has the same objects (gml_id, feature type, face count) as
  the same buildings opened from CityGML 3.0; World CRS/origin equal; cgml_source_format = CityJSON;
  the scene exports as CityGML 3.0, XSD-valid, with the same Building/surface/installation counts
- multi-LoD fixture: LoD 2.2 by default, "no LoD2.2: 1"; LoD 1.2 when chosen; the file's ids (B.1, W.B1)
  are the gml_ids and the exported gml:ids; the CityJSON address is on the object
- CJ-05: CityJSONSeq opens with Browse first; a dropped CityJSON (browse operator) is previewed

Usage (isolated profile, see tickets/README.md):
  scripts/headless/run_regression.sh <out_dir> test_cj02_open_cityjson.py
"""
import json
import os
import re
import sys
import tempfile
from collections import Counter
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, os.path.join(HERE, "pydeps"))

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402

SAMPLES = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
MULTI = os.path.join(HERE, "..", "..", "test_files", "CityJSON", "cj02_multi_lod.city.json")
SEQ = os.path.join(HERE, "..", "..", "test_files", "CityJSON", "cj05_seq_small.city.jsonl")
FAILURES = []


def check(label, condition, detail=""):
    print(("[PASS] " if condition else "[FAIL] ") + label + ("" if condition else f" - {detail}"))
    if not condition:
        FAILURES.append(label)


def clean():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for w in bpy.data.worlds:
        for k in [k for k in w.keys() if k.startswith("cgml")]:
            del w[k]


XAL_ONLY = {"bldg:address:localityType", "bldg:address:streetType", "con:height:uom",
            "cgml3_cityjson_lods", "cgml3_cityjson_source", "cgml3_cityjson_fingerprint"}
# GML-only detail; CJ-03 LoD-text and CJ-06 source bookkeeping


def building_props():
    """Per Building: its custom properties (what the attribute card shows)."""
    def plain(v):
        v = v.to_dict() if hasattr(v, "to_dict") else v.to_list() if hasattr(v, "to_list") else v
        return v if isinstance(v, (int, float, str)) else json.dumps(v, sort_keys=True)
    return {o["gml_id"]: {k: plain(v) for k, v in o.items() if k not in XAL_ONLY}
            for o in bpy.data.objects if o.get("cgml3_feature") == "Building"}


def scene_print():
    """Per gml_id: (feature type, face count); plus the World georeference."""
    objs = {o["gml_id"]: (str(o.get("cgml3_feature")), len(o.data.polygons) if o.type == 'MESH' else 0)
            for o in bpy.data.objects if o.get("gml_id")}
    w = bpy.data.worlds.get("World")
    geo = {k: w[k] for k in ("cgml_CRS", "cgml_X-Origin", "cgml_Y-Origin", "cgml_Z-Origin")} if w else {}
    return objs, geo


def export_counts(addon, path):
    from importlib import import_module as im
    res = bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version="3.0")
    ok, msg = im(f"{addon}.ops.validate").validate_citygml3_xsd(path)
    skipped = any(word in msg for word in ("Hinweis", "Warnung", "übersprungen", "skipped"))
    text = open(path, encoding="utf-8").read()
    tags = Counter(re.findall(r"<((?:bldg|con):(?:Building|BuildingInstallation|WallSurface|RoofSurface|"
                              r"GroundSurface|OuterCeilingSurface|ClosureSurface))[\s>]", text))
    return res == {'FINISHED'} and ok and not skipped, msg, tags, text


def pair(addon, out, lod):
    gml = os.path.join(SAMPLES, f"buildings_lod{lod}_citygml_3_0.gml")
    cjf = os.path.join(SAMPLES, f"buildings_lod{lod}_cityjson_2_0.city.json")
    clean()
    check(f"lod{lod}: GML 3.0 opens", bpy.ops.cgml3.import_gml_file(filepath=gml) == {'FINISHED'})
    gml_objs, gml_geo = scene_print()
    gml_props = building_props()
    _ok, _msg, gml_tags, _text = export_counts(addon, os.path.join(out, f"cj02_lod{lod}_from_gml.gml"))
    clean()
    check(f"lod{lod}: CityJSON opens", bpy.ops.cgml3.import_gml_file(filepath=cjf) == {'FINISHED'})
    cj_objs, cj_geo = scene_print()
    cj_props = building_props()
    diff = {g: sorted(set(cj_props.get(g, {}).items()) ^ set(gml_props[g].items())) for g in gml_props
            if cj_props.get(g) != gml_props[g]}
    check(f"lod{lod}: Building properties (attribute card) as from GML 3.0", not diff and cj_props, str(diff)[:600])
    check(f"lod{lod}: same objects, types and face counts as GML 3.0", cj_objs == gml_objs and len(cj_objs) > 3,
          f"{len(cj_objs)} vs {len(gml_objs)}; differ: "
          f"{sorted((k, cj_objs.get(k), gml_objs.get(k)) for k in set(cj_objs) | set(gml_objs) if cj_objs.get(k) != gml_objs.get(k))[:8]}")
    check(f"lod{lod}: feature classes equal", Counter(v[0] for v in cj_objs.values()) == Counter(v[0] for v in gml_objs.values()),
          str(Counter(v[0] for v in cj_objs.values())))
    check(f"lod{lod}: World CRS and origin as from GML", cj_geo == gml_geo and cj_geo.get("cgml_CRS") == "EPSG:25832",
          f"{cj_geo} vs {gml_geo}")
    check(f"lod{lod}: World cgml_source_format", bpy.data.worlds["World"].get("cgml_source_format") == "CityJSON")
    ok, msg, tags, _text = export_counts(addon, os.path.join(out, f"cj02_lod{lod}_from_cityjson.gml"))
    check(f"lod{lod}: CityGML 3.0 export is XSD-valid", ok, msg[:300])
    check(f"lod{lod}: export has the same Building/surface/installation counts as from GML", tags == gml_tags and tags,
          f"{dict(tags)} vs {dict(gml_tags)}")


def multi_lod(addon, out):
    builder = import_module(f"{addon}.common.unified_builder")
    clean()
    check("multi-LoD: opens (default LoD)", bpy.ops.cgml3.import_gml_file(filepath=MULTI) == {'FINISHED'})
    blds = {o["gml_id"]: o for o in bpy.data.objects if o.get("cgml3_feature") == "Building"}
    check("multi-LoD: default LoD 2.2 builds B.1 and B3, not B2", len(blds) == 2 and "B3" in blds and "B2" not in blds,
          str(sorted(blds)))
    b1 = blds.get("B.1")   # a valid gml:id: the file's id is the gml_id, nothing is mapped (K2 fix)
    check("multi-LoD: the file's id B.1 is the gml_id, no mapped id stored",
          b1 is not None and "cgml3_cityjson_id" not in b1 and "cgml3_cityjson_surface_ids" not in b1, str(sorted(blds)))
    if b1 is not None:
        check("multi-LoD: CityJSON address on the object", b1.get("bldg:address:street") == "Pilatuspool"
              and b1.get("bldg:address:postCode") == "20355", str({k: b1[k] for k in b1.keys() if "address" in k}))
        walls = [o for o in b1.children_recursive if o.get("cgml3_feature") == "WallSurface"]
        check("multi-LoD: semantic surfaces built", len(walls) >= 1, str([o.name for o in b1.children_recursive]))
    ok, msg, _tags, text = export_counts(addon, os.path.join(out, "cj02_multi_lod.gml"))
    check("multi-LoD: 3.0 export XSD-valid", ok, msg[:300])
    check("multi-LoD: 3.0 export has gml:id B.1 and W.B1, no bookkeeping properties",
          "cgml3_cityjson" not in text and 'gml:id="B.1"' in text and 'gml:id="W.B1"' in text)

    clean()
    res = bpy.ops.cgml3.import_gml_file(filepath=MULTI, cityjson_lod="1.2")
    blds = sorted(o["gml_id"] for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    check("multi-LoD: LoD 1.2 when chosen builds B.1 and B2", res == {'FINISHED'} and blds == ["B.1", "B2"], str(blds))

    clean()
    notices, not_loaded = builder.import_cityjson_with_builder(MULTI, bpy.context, lod="2.2")
    check("multi-LoD: summary counts", not_loaded == ["1 BuildingRoom", "1 WaterBody", "2 LoD1.2 geometries",
                                                      "no LoD2.2: 1"], str(not_loaded))
    check("multi-LoD: notices name the skip and the scope", any(n == "no LoD2.2: 1" for n in notices)
          and any("3DCityDB v5" in n for n in notices), str(notices))


def seq_and_dropped(addon):
    """CJ-05: CityJSONSeq is browsed, and the drop path previews CityJSON itself (details: test_cj05)."""
    browse = import_module(f"{addon}.ops.browse_gml")
    clean()
    res = bpy.ops.cgml3.import_gml_file(filepath=SEQ)
    s = browse.get_session(SEQ)
    check("CityJSONSeq opens with Browse first", res == {'FINISHED'} and s is not None and len(s.entries) == 6, str(res))
    check("CityJSONSeq: nothing imported before Show", not any(o.get("gml_id") for o in bpy.data.objects))
    bpy.ops.cgml3.browse_close()
    clean()
    res = bpy.ops.cgml3.browse_gml_file(filepath=MULTI, use_cache=False)
    s = browse.get_session(MULTI)
    n = sum(1 for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    check("drop path (browse operator) previews CityJSON: 3 outlines, no building yet",
          res == {'FINISHED'} and s is not None and len(s.entries) == 3 and n == 0, f"{res} {n}")
    bpy.ops.cgml3.browse_select(action='ALL')
    bpy.ops.cgml3.browse_load()
    n = sum(1 for o in bpy.data.objects if o.get("cgml3_feature") == "Building")
    check("Show loads the default LoD 2.2: B.1 and B3, B2 is counted", n == 2 and "no LoD2.2: 1" in s.last_load["warnings"],
          f"{n} {s.last_load.get('warnings')}")
    bpy.ops.cgml3.browse_close()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="cj02_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    for lod in (2, 3):
        pair(addon, out, lod)
    multi_lod(addon, out)
    seq_and_dropped(addon)
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
