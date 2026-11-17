"""B22: attribute card (Building, Transportation, CityFurniture) + code lists, 2.0 and 3.0 round trip.

Per version (Hamburg LoD2 sample, AdV codes, no codeSpace in the source):
- the list is detected as AdV; the function dropdown lists AdV codes
- the card (window-manager proxies, as the widgets do) sets function, usage,
  roof type, storeys, height, year; adds text / integer / date generics
- a code outside the list stays: "(unknown code)" in the menu, written back as is
- two selected: one change goes to both; a different value reads as "differs"
- export: the elements carry the values, function/roofType carry the AdV
  codeSpace, height and year sit where the version wants them, generics are
  typed (string / int / date), XSD valid
- re-import of the export gives the same values and keeps the codeSpace
- switching the list to OGC moves the codeSpace to SIG3D; a template applies
- Transportation (Railway) and CityFurniture: function edited and exported
- the panel draws

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b22_attribute_card.py -- <out_dir>
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy  # noqa: E402

from building_harness import check, export_checked, finish  # noqa: E402

ADDON = "city_modeler"
BUILDINGS = os.environ.get("CITY_MODELER_SAMPLES") or os.path.expanduser("~/Desktop/blender_test_files/buildings")
REPO = Path(__file__).resolve().parents[2]
ADV_FN = "http://repository.gdi-de.org/schemas/adv/citygml/Codelisten/BuildingFunctionTypeAdV.xml"
ADV_ROOF = "http://repository.gdi-de.org/schemas/adv/citygml/Codelisten/RoofTypeTypeAdV.xml"


def fresh():
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def roots(feature="Building"):
    return sorted((o for o in bpy.context.scene.objects if o.get("cgml3_feature") == feature and o.parent is None),
                  key=lambda o: str(o.get("gml_id")))


def select(*objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0] if objs else None


def deselect():
    for o in bpy.context.selected_objects:
        o.select_set(False)


def feature_xml(text, gml_id):
    """The XML of the Building with ``gml_id`` (up to the next top-level member)."""
    i = text.find(f'gml:id="{gml_id}"')
    if i < 0:
        return ""
    j = text.find("cityObjectMember>", i)
    return text[i:j if j > 0 else len(text)]


class Rec:
    def __init__(self):
        object.__setattr__(self, "n", 0)

    def __setattr__(self, name, value):
        pass

    def __getattr__(self, name):
        def call(*a, **k):
            if name in ("row", "column", "box", "split"):
                return self
            if name == "panel":  # header, body
                return self, self
            if name in ("prop",) and len(a) >= 2:
                getattr(a[0], a[1])  # the widget reads its value: getters must work
            return type("P", (), {})()
        return call


def run_buildings(version, out_dir, card):
    wm = bpy.context.window_manager
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=os.path.join(BUILDINGS, f"buildings_lod2_citygml_{version.replace('.', '_')}.gml"))
    b1, b2 = roots()[:2]
    select(b1)
    check(f"{version}: list detected as AdV", card.list_of(b1) == "ADV", card.list_of(b1))
    items = card._code_items("function")(None, bpy.context)
    check(f"{version}: function menu lists AdV codes", any(i[0] == "31001_1010" for i in items), str(len(items)))

    wm.cgml_attr_function = "31001_1010"
    wm.cgml_attr_usage = "31001_1010"
    wm.cgml_attr_roofType = "3200"
    wm.cgml_attr_storeys = 3
    wm.cgml_attr_height = 12.5
    wm.cgml_attr_year = 1999
    check(f"{version}: proxies read back", (wm.cgml_attr_function, wm.cgml_attr_storeys, round(wm.cgml_attr_height, 2),
                                            wm.cgml_attr_year) == ("31001_1010", 3, 12.5, 1999),
          str((wm.cgml_attr_function, wm.cgml_attr_storeys, wm.cgml_attr_height, wm.cgml_attr_year)))
    for name, kind, value in (("note", 'STRING', "hello"), ("rooms", 'INT', "4"), ("surveyed", 'DATE', "2020-05-01")):
        res = bpy.ops.cgml.attr_generic_set(name=name, kind=kind, value=value)
        check(f"{version}: generic {name} ({kind}) added", res == {'FINISHED'}, str(res))
    try:
        bad = bpy.ops.cgml.attr_generic_set(name="when", kind='DATE', value="2020-13-45")
    except RuntimeError as exc:
        bad = str(exc)
    check(f"{version}: invalid date refused", bad != {'FINISHED'}, str(bad))

    b2["bldg:function"] = "99999_9999"
    select(b2)
    items = card._code_items("function")(None, bpy.context)
    check(f"{version}: unknown code shown, not dropped",
          any(i[0] == "99999_9999" and "unknown" in i[1] for i in items) and wm.cgml_attr_function == "99999_9999")

    select(b1, b2)
    check(f"{version}: two selected with different functions read as differs", wm.cgml_attr_function == "__mixed__",
          wm.cgml_attr_function)
    wm.cgml_attr_storeys = 5
    check(f"{version}: one change goes to both", b1.get("bldg:storeysAboveGround") == 5
          and b2.get("bldg:storeysAboveGround") == 5)
    wm.cgml_attr_storeys = 3
    select(b2)
    wm.cgml_attr_storeys = 0
    check(f"{version}: 0 clears the value", "bldg:storeysAboveGround" not in b2)
    select(b1)
    try:
        card.CGML3_PT_Attributes.draw(type("P", (), {"layout": Rec()})(), bpy.context)
        drew = True
    except Exception as exc:
        drew = str(exc)
    check(f"{version}: panel draws", drew is True, str(drew))

    deselect()
    path = os.path.join(out_dir, f"b22_{version.replace('.', '')}.gml")
    text = export_checked(path, version)
    x1, x2 = feature_xml(text, b1.get("gml_id")), feature_xml(text, b2.get("gml_id"))
    check(f"{version}: function with AdV codeSpace",
          re.search(rf'<bldg:function codeSpace="{re.escape(ADV_FN)}">31001_1010<', x1) is not None,
          (re.search(r"<bldg:function[^<]*<", x1) or [""])[0])
    check(f"{version}: roofType with AdV codeSpace",
          re.search(rf'<bldg:roofType codeSpace="{re.escape(ADV_ROOF)}">3200<', x1) is not None,
          (re.search(r"<bldg:roofType[^<]*<", x1) or [""])[0])
    check(f"{version}: unknown code written back unchanged", ">99999_9999<" in x2)
    check(f"{version}: storeys", "<bldg:storeysAboveGround>3<" in x1)
    if version == "2.0":
        check("2.0: measuredHeight", re.search(r'<bldg:measuredHeight uom="[^"]+">12\.5<', x1) is not None)
        check("2.0: yearOfConstruction", "<bldg:yearOfConstruction>1999<" in x1)
        check("2.0: typed generics", '<gen:stringAttribute name="note">' in x1 and '<gen:intAttribute name="rooms">' in x1
              and '<gen:dateAttribute name="surveyed">' in x1, re.findall(r"<gen:\w+ name=\"\w+\">", x1)[:6])
    else:
        check("3.0: con:height with value and references", re.search(r"<con:Height>.*highReference.*lowReference.*"
              r"status.*<con:value uom=\"[^\"]+\">12\.5<", x1, re.S) is not None)
        check("3.0: dateOfConstruction", re.search(r"<con:dateOfConstruction>1999-\d\d-\d\d<", x1) is not None)
        check("3.0: typed generics", all(re.search(rf"<gen:{t}>\s*<gen:name>{n}<", x1) for t, n in
              (("StringAttribute", "note"), ("IntAttribute", "rooms"), ("DateAttribute", "surveyed"))),
              re.findall(r"<gen:(\w+Attribute)>\s*<gen:name>(\w+)<", x1))

    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=path)
    r1 = next(o for o in roots() if o.get("gml_id") == x1 and False) if False else \
        next(o for o in roots() if f'gml:id="{o.get("gml_id")}"' in x1[:200])
    from city_modeler.shared.attribute_utils import CODE_SPACE_PREFIX
    check(f"{version}: re-import keeps values and codeSpace",
          (r1.get("bldg:function"), r1.get(CODE_SPACE_PREFIX + "bldg:function"), r1.get("bldg:storeysAboveGround"))
          == ("31001_1010", ADV_FN, 3), str((r1.get("bldg:function"), r1.get(CODE_SPACE_PREFIX + "bldg:function"))))
    select(r1)
    check(f"{version}: re-import: height, year, generics",
          round(wm.cgml_attr_height, 2) == 12.5 and wm.cgml_attr_year == 1999
          and card.generics(r1).get("note") == "hello" and card.generics(r1).get("rooms") == 4
          and card.generics(r1).get("surveyed") == "2020-05-01",
          str((wm.cgml_attr_height, wm.cgml_attr_year, card.generics(r1))))

    wm.cgml_attr_list = "OGC"
    check(f"{version}: list switch moves the codeSpace to SIG3D",
          "sig3d" in str(r1.get(CODE_SPACE_PREFIX + "bldg:function")) and r1.get("cgml3_codelist") == "OGC")
    res = bpy.ops.cgml.attr_template_apply(template="residential")
    check(f"{version}: template applies", res == {'FINISHED'} and r1.get("bldg:function") == "1000"
          and r1.get("bldg:roofType") == "1030" and r1.get("bldg:storeysAboveGround") == 2,
          str((r1.get("bldg:function"), r1.get("bldg:roofType"))))


def run_other(version, out_dir, card):
    wm = bpy.context.window_manager
    fresh()
    bpy.ops.cgml3.import_gml_file(filepath=str(REPO / "test_files" / "Railway" / f"Railway_citygml{version[0]}.gml"))
    rail = next((o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "Railway"), None)
    check(f"{version}: railway imported", rail is not None)
    # the furniture fixture is in EPSG:25832, the railway in 3068: B31 refuses a second CRS in one scene,
    # so the card test reads a copy that claims the railway's CRS
    src_frn = REPO / "test_files" / "CityFurniture" / f"Implicit_CityGML{version[0]}.gml"
    fresh_frn = str(Path(out_dir) / f"b22_frn_{version.replace('.', '')}.gml")
    Path(fresh_frn).write_text(src_frn.read_text(encoding="utf-8").replace(
        "urn:ogc:def:crs:EPSG::25832", "urn:ogc:def:crs,crs:EPSG::3068,crs:EPSG::5783"), encoding="utf-8")
    bpy.ops.cgml3.import_gml_file(filepath=fresh_frn)
    frn = next((o for o in bpy.context.scene.objects if o.get("cgml3_feature") == "CityFurniture"), None)
    check(f"{version}: city furniture imported", frn is not None)
    if rail is None or frn is None:
        return
    select(rail)
    check(f"{version}: railway card: function is a text field (no list in repo)",
          not card.codelists.has_codes("tran:function") and wm.cgml_attr_function_text == str(rail.get("tran:function")))
    wm.cgml_attr_function_text = "1800"
    select(frn)
    wm.cgml_attr_function_text = "1210"
    check(f"{version}: railway function set by the card", rail.get("tran:function") == "1800")
    # 3.0: exporting the selected Railway hits a degenerate polygon in the fixture (whole-scene export
    # is fine, also before B22); Railway is not needed now (user, 2026-11-05), so 3.0 exports furniture only.
    select(rail, frn) if version == "2.0" else select(frn)
    from building_harness import _xsd_valid
    path = Path(out_dir) / f"b22_other_{version.replace('.', '')}.gml"
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    except RuntimeError as exc:
        res = str(exc)
    check(f"{version}: railway + furniture export", res == {'FINISHED'}, str(res)[:300])
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    check(f"{version}: railway + furniture XSD valid", bool(text) and _xsd_valid(version, path)[0])
    if version == "2.0":
        check(f"{version}: railway function exported", "<tran:function>1800<" in text)
    check(f"{version}: furniture function exported", "<frn:function>1210<" in text)


def main():
    out_dir = sys.argv[sys.argv.index("--") + 1]
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    from city_modeler.ui import attribute_card as card
    for version in ("2.0", "3.0"):
        run_buildings(version, out_dir, card)
        run_other(version, out_dir, card)
    finish("B22 attribute card")


main()
