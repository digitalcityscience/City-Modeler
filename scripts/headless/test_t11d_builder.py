"""T11d: the shared builder builds the golden scenes, plus its own rules.

``golden/t11d_scenes.json.gz`` (see ``t11d_golden.py``) holds the object-level scene summary of each
scenario; the builder must give the same summaries. Also checked:

- the builder really ran (``build_feature`` was called) for supported files;
- content outside the add-on's scope (a CityGML 2.0 Room, ImplicitGeometry next to explicit geometry) is
  skipped with a notice and the rest is built; a file with nothing to build is refused;
- import filters (gml:id, feature type, LoD, bbox);
- the builder reads a feature's own values, filters LoD for real and reads the 2.0 address;
- Load all: the size limit (lowered by parameter) and the large-file warning.

Usage (isolated profile, see tickets/README.md):
  blender -b --python-exit-code 1 --python scripts/headless/test_t11d_builder.py -- [extra.gml ...]
"""
import importlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import t11a_import_snapshot as snap  # noqa: E402
import t11d_golden as g  # noqa: E402

FILES = [
    "test_files/Building/SemanticHierarchy_Installations_LoD3_CityGML2.gml",
    "test_files/Building/SemanticHierarchy_Installations_LoD3_CityGML3.gml",
    "test_files/Building/SemanticHierarchy_Appearance_FullAppearance_LoD3_CityGML2.gml",
    "test_files/Building/SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML2.gml",
    "test_files/Building/Railway_Scene_LoD3_CityGML2_Building_only.gml",
    "test_files/Building/CitySmith/CS1_lod2.gml",
    "test_files/Building/CitySmith/CS2_lod3_enhanced.gml",
    "test_files/BuildingParts/BuildingParts_citygml2.gml",
    "test_files/BuildingParts/BuildingParts_citygml3.gml",
    "test_files/Bridge/Bridge_citygml3.gml",
    "test_files/Building/ExportError_CityDoctorRegression_LoD3_CityGML3.gml",
    "test_files/Building/SemanticHierarchy_Appearance_FullAppearance_LoD3_CityGML3.gml",
    "test_files/Building/AltOpeningSpelling_LoD3_CityGML3.gml",
    "test_files/Railway/Railway_citygml2.gml",
    "test_files/Railway/Railway_citygml3.gml",
    "test_files/CityFurniture/Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml",
    "test_files/CityFurniture/Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml",
    "test_files/CityFurniture/Implicit_CityGML2.gml",
    "test_files/CityFurniture/Implicit_CityGML3.gml",
]

SQ = "0 0 0 4 0 0 4 4 0 0 4 0 0 0 0"


def road_doc(schema):
    """A Road with a TrafficArea (2.0) / a Section, TrafficSpace and TrafficArea (3.0). The old importers
    build these differently (the 2.0 one drops the Road itself), so only the builder is checked."""
    v = "2.0" if schema == "2.0" else "3.0"
    gml = "http://www.opengis.net/gml" if schema == "2.0" else "http://www.opengis.net/gml/3.2"
    area = ('<tran:TrafficArea gml:id="TA"><tran:surfaceMaterial>asphalt</tran:surfaceMaterial>'
            '<core:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="TAP">'
            f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{SQ}</gml:posList></gml:LinearRing>'
            '</gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></core:lod2MultiSurface>'
            '</tran:TrafficArea>')
    road = ('<tran:Road gml:id="R"><tran:class>1</tran:class>'
            '<core:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="RP">'
            f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{SQ.replace("0 0 0", "0 0 1", 1)}'
            '</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>'
            '</core:lod2MultiSurface>')
    if schema == "2.0":
        road += f'<tran:trafficArea>{area}</tran:trafficArea>'
    else:
        road += ('<tran:section><tran:Section gml:id="S"><tran:trafficSpace><tran:TrafficSpace gml:id="TS">'
                 f'<tran:granularity>lane</tran:granularity><tran:trafficArea>{area}</tran:trafficArea>'
                 '</tran:TrafficSpace></tran:trafficSpace></tran:Section></tran:section>')
    road += '</tran:Road>'
    return (f'<?xml version="1.0" encoding="UTF-8"?><core:CityModel xmlns:core="http://www.opengis.net/citygml/{v}" '
            f'xmlns:tran="http://www.opengis.net/citygml/transportation/{v}" xmlns:gml="{gml}">'
            f'<core:cityObjectMember>{road}</core:cityObjectMember></core:CityModel>')


MIXED_FURNITURE = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:frn="http://www.opengis.net/citygml/cityfurniture/2.0"
 xmlns:gml="http://www.opengis.net/gml">
<core:cityObjectMember><frn:CityFurniture gml:id="F_IMPL"><frn:class>1000</frn:class>
<frn:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="EP"><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">0 0 0 2 0 0 2 2 0 0 2 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</gml:surfaceMember></gml:MultiSurface></frn:lod2MultiSurface>
<frn:lod3ImplicitRepresentation><core:ImplicitGeometry><core:transformationMatrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</core:transformationMatrix>
<core:relativeGMLGeometry><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="IP"><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">0 0 0 1 0 0 1 1 0 0 1 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</gml:surfaceMember></gml:MultiSurface></core:relativeGMLGeometry>
<core:referencePoint><gml:Point><gml:pos>5 5 0</gml:pos></gml:Point></core:referencePoint>
</core:ImplicitGeometry></frn:lod3ImplicitRepresentation></frn:CityFurniture></core:cityObjectMember></core:CityModel>
"""

ROOM = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml">
<core:cityObjectMember><bldg:Building gml:id="B_ROOM">
<bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="P1"><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">0 0 0 4 0 0 4 4 0 0 4 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface>
<bldg:interiorRoom><bldg:Room gml:id="R1"><bldg:lod4Solid><gml:Solid><gml:exterior><gml:CompositeSurface>
<gml:surfaceMember><gml:Polygon gml:id="PR1"><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">1 1 0 2 1 0 2 2 0 1 2 0 1 1 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</gml:surfaceMember></gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod4Solid></bldg:Room></bldg:interiorRoom>
</bldg:Building></core:cityObjectMember></core:CityModel>
"""

ADDRESS = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
 xmlns:gml="http://www.opengis.net/gml" xmlns:xAL="urn:oasis:names:tc:ciq:xsdschema:xAL:2.0">
<core:cityObjectMember><bldg:Building gml:id="B_ADDR">
<bldg:lod2MultiSurface><gml:MultiSurface><gml:surfaceMember><gml:Polygon gml:id="P1"><gml:exterior><gml:LinearRing>
<gml:posList srsDimension="3">0 0 0 4 0 0 4 4 0 0 4 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon>
</gml:surfaceMember></gml:MultiSurface></bldg:lod2MultiSurface>
<bldg:address><core:Address><core:xalAddress><xAL:AddressDetails><xAL:Country><xAL:CountryName>DE</xAL:CountryName>
<xAL:Locality><xAL:LocalityName>Hamburg</xAL:LocalityName><xAL:Thoroughfare><xAL:ThoroughfareNumber>5</xAL:ThoroughfareNumber>
<xAL:ThoroughfareName>Hauptstrasse</xAL:ThoroughfareName></xAL:Thoroughfare></xAL:Locality></xAL:Country></xAL:AddressDetails>
</core:xalAddress></core:Address></bldg:address>
</bldg:Building></core:cityObjectMember></core:CityModel>
"""
ONLY_TYPE = {"use_feature_type_filter": True, "import_buildings": False, "import_bridges": False,
             "import_tunnels": False, "import_vegetation": False, "import_water": False,
             "import_transportation": False, "import_cityfurniture": False, "import_landuse": False,
             "import_relief": False, "import_generics": False}
SAMPLES = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
# (file, label, scene properties); every one must give the golden scene
FILTER_SCENARIOS = [
    ("test_files/BuildingParts/BuildingParts_citygml2.gml", "2.0 gml:id of the part",
     {"use_gmlid_filter": True, "gmlid_filter": "B_PART_TEST_CHILD"}),
    ("test_files/BuildingParts/BuildingParts_citygml3.gml", "3.0 gml:id of the building",
     {"use_gmlid_filter": True, "gmlid_filter": "[B_PART_TEST_PARENT_V3]"}),
    ("test_files/Building/SemanticHierarchy_Installations_LoD3_CityGML2.gml", "2.0 gml:id list",
     {"use_gmlid_filter": True,
      "gmlid_filter": "SEMHIER_BUILDING_V2, SEMHIER_INST_CLASS_BALCONY_V2"}),
    ("test_files/Building/SemanticHierarchy_Installations_LoD3_CityGML2.gml", "2.0 type filter: buildings",
     dict(ONLY_TYPE, import_buildings=True)),
    ("test_files/Building/SemanticHierarchy_Installations_LoD3_CityGML3.gml", "3.0 type filter: bridges only",
     dict(ONLY_TYPE, import_bridges=True)),
    ("test_files/Bridge/Bridge_citygml3.gml", "3.0 bridge with the bridge filter",
     dict(ONLY_TYPE, import_bridges=True)),
    ("test_files/Building/CitySmith/CS1_multiLOD.gml", "2.0 LoD 2 only",
     {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": True, "lod_3": False, "lod_4": False}),
    ("test_files/Building/CitySmith/CS1_multiLOD.gml", "2.0 LoD 1 only",
     {"use_lod_filter": True, "lod_0": False, "lod_1": True, "lod_2": False, "lod_3": False, "lod_4": False}),
    ("test_files/Building/SemanticHierarchy_Appearance_MixedLoD_LoD3_CityGML2.gml", "2.0 LoD 3 only",
     {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": False, "lod_3": True, "lod_4": False}),
    (SAMPLES + r"\buildings_lod3_citygml_3_0.gml", "3.0 LoD 2 only",
     {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": True, "lod_3": False, "lod_4": False}),
    (SAMPLES + r"\buildings_lod3_citygml_3_0.gml", "3.0 LoD 3 only",
     {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": False, "lod_3": True, "lod_4": False}),
    ("test_files/Railway/Railway_citygml2.gml", "2.0 type filter: transportation",
     dict(ONLY_TYPE, import_transportation=True)),
    ("test_files/Railway/Railway_citygml3.gml", "3.0 type filter: transportation",
     dict(ONLY_TYPE, import_transportation=True)),
    ("test_files/Railway/Railway_citygml3.gml", "3.0 type filter: buildings only (leaves the Railway out)",
     dict(ONLY_TYPE, import_buildings=True)),
    ("test_files/CityFurniture/Railway_Scene_LoD3_CityGML2_CityFurniture_only.gml",
     "2.0 type filter: city furniture", dict(ONLY_TYPE, import_cityfurniture=True)),
    ("test_files/CityFurniture/Railway_Scene_LoD3_CityGML3_CityFurniture_only.gml",
     "3.0 gml:id of the furniture", {"use_gmlid_filter": True, "gmlid_filter": "GMLID_7431654_78949_792"}),
    (SAMPLES + r"\buildings_lod2_citygml_2_0.gml", "2.0 bbox of the first building",
     {"use_bbox_filter": True, "bbox_min_x": 564700.0, "bbox_min_y": 5934100.0,
      "bbox_max_x": 564800.0, "bbox_max_y": 5934260.0}),
    (SAMPLES + r"\buildings_lod2_citygml_3_0.gml", "3.0 bbox of the first building",
     {"use_bbox_filter": True, "bbox_min_x": 564700.0, "bbox_min_y": 5934100.0,
      "bbox_max_x": 564800.0, "bbox_max_y": 5934260.0}),
    (SAMPLES + r"\buildings_lod2_citygml_2_0.gml", "2.0 bbox + type + gml:id",
     {"use_bbox_filter": True, "bbox_min_x": 564700.0, "bbox_min_y": 5934100.0,
      "bbox_max_x": 565800.0, "bbox_max_y": 5935260.0, "use_gmlid_filter": True,
      "gmlid_filter": "DEHHALKAJ0000yAZ,DEHHALKAJ000179L", **ONLY_TYPE, "import_buildings": True}),
]


failures = []
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
GOLDEN = g.load()


def check(ok, message):
    print(("  [ok]   " if ok else "  [FAIL] ") + message)
    if not ok:
        failures.append(message)


def scene_equals_golden(key, path, props=None):
    """Builder scene against the golden scene."""
    notices, new, calls = g.import_new(path, props)
    check(not isinstance(notices, Exception), f"{key}: import finished ({notices if isinstance(notices, Exception) else 'ok'})")
    check(bool(calls) or not new, f"{key}: built {len(calls)} feature(s) from the IR")
    if g.refreshing():
        GOLDEN[key] = new
        return
    if key not in GOLDEN:
        check(False, f"{key}: no golden scene")
        return
    problems = snap.detail_diff(GOLDEN[key], new)
    check(not problems, f"{key}: {len(new)} objects, equal to the golden scene"
          + ("" if not problems else f" ({problems[:3]})"))


def filter_scenarios():
    for rel, label, props in FILTER_SCENARIOS:
        path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        if not os.path.exists(path):
            print(f"{label}: {path} not available")
            continue
        key = f"{os.path.basename(path)} - {label}"
        print(key)
        scene_equals_golden(key, path, props)


def main():
    for rel in FILES + ARGS_FILES:
        path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        name = os.path.basename(path)
        print(name)
        scene_equals_golden(name, path)

    filter_scenarios()

    tmp = tempfile.mkdtemp(prefix="t11d_")
    room = os.path.join(tmp, "room.gml")
    with open(room, "w", encoding="utf-8") as f:
        f.write(ROOM)
    print("room.gml (interior Room is out of scope: skipped, the Building shell is built)")
    notices, new, calls = g.import_new(room)
    ok = not isinstance(notices, Exception)
    check(ok and calls == ["B_ROOM"], f"room.gml: only the Building is built ({calls})")
    check(ok and any("Room" in n for n in notices), "room.gml: the notice names the Room")
    check(not any(d["props"].get("cgml3_feature") == "Room" for d in new.values()), "room.gml: no Room object")
    notices, new, calls = g.import_new(room, {"use_gmlid_filter": True, "gmlid_filter": "B_ROOM"})
    check(not isinstance(notices, Exception) and bool(new), "room.gml with a gml:id filter: the Building is built")

    for schema in ("2.0", "3.0"):
        road = os.path.join(tmp, f"road_{schema}.gml")
        with open(road, "w", encoding="utf-8") as f:
            f.write(road_doc(schema))
        print(f"road_{schema}.gml (Road with its parts, builder only)")
        _n, new, calls = g.import_new(road)
        by_type = {d["props"].get("cgml3_feature"): (n, d) for n, d in new.items()}
        check({"Road", "TrafficArea"} <= set(by_type), f"road_{schema}.gml: Road and TrafficArea objects built "
              f"({sorted(map(str, by_type))}, {len(calls)} payloads)")
        if {"Road", "TrafficArea"} <= set(by_type):
            road_name, road_obj = by_type["Road"]
            _area_name, area_obj = by_type["TrafficArea"]
            check(road_obj["props"].get("tran:class") == "1" and "tran:surfaceMaterial" not in road_obj["props"],
                  f"road_{schema}.gml: the Road keeps its own values")
            check(area_obj["props"].get("tran:surfaceMaterial") == "asphalt" and "tran:class" not in area_obj["props"],
                  f"road_{schema}.gml: the TrafficArea keeps its own values")
            check(area_obj.get("parent") == road_name,
                  f"road_{schema}.gml: the TrafficArea is parented to the Road ({area_obj.get('parent')!r})")
            check(road_obj.get("polys") == 1 and area_obj.get("polys") == 1,
                  f"road_{schema}.gml: each polygon is built once")

    for name in ("Implicit_CityGML2.gml", "Implicit_CityGML3.gml"):
        print(name + " (default builder: template instances)")
        g.import_new(os.path.join(ROOT, "test_files/CityFurniture", name))
        objs = {o.name: o for o in bpy.data.objects if o.type == 'MESH'}
        check(sorted(objs) == ["F_A", "F_B", "F_C", "F_D"], f"{name}: four furniture objects ({sorted(objs)})")
        if sorted(objs) == ["F_A", "F_B", "F_C", "F_D"]:
            check(objs["F_A"].data is objs["F_B"].data is objs["F_C"].data and objs["F_D"].data is not objs["F_A"].data,
                  f"{name}: instances of one template share a mesh (by id and by shape)")
            check(bool(objs["F_A"].data.get("ImplicitGeometry")), f"{name}: the template mesh is flagged")
            loc = lambda o: tuple(round(c, 3) for c in o.matrix_world.translation)
            check(loc(objs["F_A"]) != loc(objs["F_D"]) and loc(objs["F_D"]) != (0.0, 0.0, 0.0),
                  f"{name}: every instance, also the single one, is placed ({loc(objs['F_A'])}, {loc(objs['F_D'])})")
            check(abs(objs["F_A"].matrix_world.to_scale().x - 2.0) < 1e-6
                  and abs(objs["F_C"].matrix_world.to_scale().z - 3.0) < 1e-6,
                  f"{name}: scale comes from the transformationMatrix")
            check(all("cgml3_implicit_template_id" in o.keys() for o in objs.values()),
                  f"{name}: each instance names its template (the old importer named the first one only)")

    implicit = os.path.join(tmp, "mixed_furniture.gml")
    with open(implicit, "w", encoding="utf-8") as f:
        f.write(MIXED_FURNITURE)
    print("mixed_furniture.gml (ImplicitGeometry next to explicit geometry is not built: refused)")
    notices, new, calls = g.import_new(implicit)
    check(isinstance(notices, RuntimeError) and "3DCityDB v5" in str(notices) and not calls and not new,
          "mixed_furniture.gml: nothing to build, refused with the 3DCityDB v5 pointer")

    print("the builder's own rules (the old importers differed)")
    path = os.path.join(ROOT, FILES[0])
    _n, clean, _ = g.import_new(path)
    building = [n for n, d in clean.items() if d["props"].get("cgml3_feature") == "Building"][0]
    check("bldg:class" not in clean[building]["props"],
          "the Building does not inherit bldg:class from its installation")

    cs = os.path.join(ROOT, "test_files/Building/CitySmith/CS1_multiLOD.gml")
    lod2 = {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": True, "lod_3": False, "lod_4": False}
    _n, every, _ = g.import_new(cs)
    _n, only, _ = g.import_new(cs, lod2)
    polys = lambda d: sum(o.get("polys", 0) for o in d.values())
    check(polys(only) < polys(every), f"the LoD filter drops the polygons of other LoDs ({polys(only)} < {polys(every)})")

    address = os.path.join(tmp, "address.gml")
    with open(address, "w", encoding="utf-8") as f:
        f.write(ADDRESS)
    _n, clean, _ = g.import_new(address)
    keys = lambda d: sorted(k for o in d.values() for k in o["props"] if k.startswith("bldg:address:"))
    check("bldg:address:street" in keys(clean), "the builder reads the 2.0 xAL:AddressDetails")

    print("Load all limit (lowered by parameter) and the large-file warning")
    addon = g.reset()
    builder = importlib.import_module(addon + ".common.unified_builder")
    limits = importlib.import_module(addon + ".shared.load_limits")
    sample = os.path.join(ROOT, FILES[0])
    size_mb = os.path.getsize(sample) / (1024 * 1024)
    try:
        builder.import_with_builder(sample, bpy.context, schema="2.0", max_mb=size_mb / 2)
        refused = None
    except limits.ImportTooLarge as exc:
        refused = str(exc)
    check(refused is not None and "Browse first" in refused and "3DCityDB v5" in refused,
          "a file above the limit is refused with the Browse first / 3DCityDB v5 pointer")
    check(not bpy.data.objects, "the refused file leaves the scene untouched")
    collections = len(bpy.data.collections)
    try:
        builder.import_with_builder(sample, bpy.context, schema="2.0", max_features=0)
        refused = None
    except limits.ImportTooLarge as exc:
        refused = str(exc)
    check(refused is not None and "features" in refused and "3DCityDB v5" in refused,
          "a file above the feature limit is refused with the Browse first / 3DCityDB v5 pointer")
    check(not bpy.data.objects and len(bpy.data.collections) == collections,
          "the file refused for its feature count leaves the scene untouched (no objects, no collection)")
    big =os.path.join(ROOT, "..", "perf-data", "S.gml")
    if os.path.exists(big):
        notices, _d, _c = g.import_new(big, {"import_streaming_threshold_mb": 1.0, "use_gmlid_filter": True,
                                             "gmlid_filter": "DEHHALKAJ0000yAZ"})
        check(not isinstance(notices, Exception) and any("Large file" in n for n in notices),
              f"S.gml above the threshold: the import runs with a memory warning ({notices})")

    # T11f (2): bytes handed over by a Browse fragment give the scene the file path gives
    for rel in FILES[:3]:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        _n, from_file, _c = g.import_new(path)
        _n, from_data, _c = g.import_new(path, with_data=True)
        problems = snap.detail_diff(from_file, from_data)
        check(from_file and not problems, f"{os.path.basename(path)}: import from bytes = import from file "
              f"({len(from_file)} objects{'' if not problems else ', ' + str(problems[:3])})")

    if g.refreshing():
        g.save(GOLDEN)
    print("T11d builder:", "FAILED" if failures else "all checks passed")
    sys.exit(1 if failures else 0)


ARGS_FILES = [a for a in ARGS if not a.startswith("--")]
main()
