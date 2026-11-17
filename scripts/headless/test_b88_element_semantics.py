"""B88: the attribute card shows the clicked element's own fields and both writers export them.

Authored building with roof, window, door, balcony, chimney. Through the card's window-manager
proxies (as the widgets do):
- wall / roof: Material and Radio material; window: Glazing, Frame, Radio material; door: Material
  -> generic attributes on that surface / opening, in 2.0 (bldg:WallSurface, bldg:Window ...) and
  3.0 (con:WallSurface, con:WindowSurface ...); nothing lands on the other surfaces
- balcony: Class and Usage from the SIG3D lists with their codeSpaces; the function code the
  writers derive (1000) is kept and stays in schema order (class, function, usage)
- building: Class (SIG3D list, also when the building's list is AdV) and Storeys below
- two walls selected: one change goes to both; "not set" removes the attribute again
- XSD valid; re-importing either export brings the wall / roof / window values back onto the same
  surfaces only, and exporting that again (2.0 and 3.0) writes the same attributes, no bookkeeping
- the panel draws for every element kind

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b88_element_semantics.py -- <out_dir>
"""
import os
import re
import sys
import tempfile
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402

SIG3D = "http://www.sig3d.org/codelists/standard/building/2.0/"


def select(*objs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def element_xml(text, tag, gml_id=None):
    """The XML of the first ``tag`` element (with ``gml_id`` when given), up to its end tag."""
    start = text.find(f'<{tag} gml:id="{gml_id}"' if gml_id else f"<{tag} ")
    return text[start:text.find(f"</{tag}>", start)] if start >= 0 else ""


def pairs(xml):
    """Sorted (name, value) of every generic attribute in ``xml`` (2.0 and 3.0 spelling)."""
    return sorted(re.findall(r"<gen:name>([^<]*)</gen:name>\s*<gen:value>([^<]*)</gen:value>", xml)
                  + re.findall(r'<gen:\w+Attribute name="([^"]*)">\s*<gen:value>([^<]*)</gen:value>', xml))


def generics(xml):
    """{name: value} of the generic attributes of one element."""
    return dict(pairs(xml))


def own(xml, *nested):
    """``xml`` without its nested openings (their attributes are not the surface's own)."""
    for tag in nested:
        xml = re.sub(rf"<{tag}[ >].*?</{tag}>", "", xml, flags=re.S)
    return xml


class Recorder:
    """A layout that accepts every call (the panel only has to draw without an error)."""
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def __iter__(self):  # ``header, body = layout.panel(...)``
        return iter((self, self))

    def __setattr__(self, name, value):  # ``op.prop = ...`` on a returned operator must not shadow row.prop
        if name == "layout":
            object.__setattr__(self, name, value)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b88_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    card = import_module(f"{addon}.ui.attribute_card")
    wm = bpy.context.window_manager

    b = author_everything()
    kids = list(b.children_recursive)
    walls = [o for o in kids if o.get("cgml3_feature") == "WallSurface"]
    front = next(o for o in walls if any(c.get("cgml3_feature") == "WindowSurface" for c in o.children_recursive))
    side = next(o for o in walls if o is not front)
    roof = next(o for o in kids if o.get("cgml3_feature") == "RoofSurface")
    window = next(o for o in kids if o.get("cgml3_feature") == "WindowSurface")
    door = next(o for o in kids if o.get("cgml3_feature") == "DoorSurface")
    balcony = next(o for o in kids if o.get("cgml_semantic_type") == "Balcony")
    ground = next(o for o in kids if o.get("cgml3_feature") == "GroundSurface")

    select(ground)
    check("ground surface: no element fields, the building's card stays", card.elements(bpy.context) == []
          and card.targets(bpy.context) == [b])

    select(front, side)
    check("two walls: both are the card's elements", card.elements(bpy.context) == [front, side])
    wm.cgml_elem_0 = "masonry"
    wm.cgml_elem_1 = "brick"
    select(side)
    check("the second wall got the value too", wm.cgml_elem_0 == "masonry" and wm.cgml_elem_1 == "brick")
    wm.cgml_elem_1 = card.NONE
    check("'not set' removes it again", wm.cgml_elem_1 == card.NONE and wm.cgml_elem_0 == "masonry")
    select(front, side)
    check("different values read as 'differs'", wm.cgml_elem_1 == "__mixed__", str(wm.cgml_elem_1))

    select(roof)
    wm.cgml_elem_0 = "clayTile"
    select(window)
    wm.cgml_elem_0, wm.cgml_elem_1, wm.cgml_elem_2 = "doubleLowE", "pvc", "glass"
    select(door)
    wm.cgml_elem_0 = "wood"
    select(balcony)
    check("balcony: the function the writers derive is shown", wm.cgml_elem_1 == "1000", str(wm.cgml_elem_1))
    wm.cgml_elem_0 = "1000"  # class: outer characteristics
    wm.cgml_elem_2 = "1000"  # usage: balcony
    check("balcony: the derived function is kept as data", balcony.get("bldg:function") == "1000")
    select(b)
    wm.cgml_attr_class = "1000"
    wm.cgml_attr_storeysBelow = 1
    check("building class from the SIG3D list although the roof makes the list AdV",
          card.list_of(b) == "ADV" and b.get("bldg:class") == "1000"
          and "sig3d" in str(b.get("cgml3_codespace:bldg:class")), f"{card.list_of(b)} {dict(b.items())}")

    for o in (b, front, roof, window, door, balcony):
        select(o)
        try:
            panel = Recorder()
            panel.layout = Recorder()
            card.CGML3_PT_Attributes.draw(panel, bpy.context)
            drawn = ""
        except Exception as e:  # noqa: BLE001
            drawn = repr(e)
        check(f"panel draws for {o.get('cgml_semantic_type') or o.get('cgml3_feature')}", not drawn, drawn)

    select(b)
    paths = {}
    for v, wall_tag, roof_tag, win_tag, door_tag in (("2.0", "bldg:WallSurface", "bldg:RoofSurface", "bldg:Window",
                                                     "bldg:Door"),
                                                    ("3.0", "con:WallSurface", "con:RoofSurface", "con:WindowSurface",
                                                     "con:DoorSurface")):
        p = paths[v] = os.path.join(out, f"b88_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:400])
        front_xml = element_xml(text, wall_tag, front["gml_id"])
        got = generics(own(front_xml, "bldg:opening", "con:fillingSurface"))
        check(f"{v}: front wall material + radio material", got == {"material": "masonry", "radioMaterial": "brick"},
              str(got))
        got = generics(own(element_xml(text, wall_tag, side["gml_id"]), "bldg:opening", "con:fillingSurface"))
        check(f"{v}: side wall material only", got == {"material": "masonry"}, str(got))
        got = generics(element_xml(text, roof_tag, roof["gml_id"]))
        check(f"{v}: roof material", got == {"material": "clayTile"}, str(got))
        got = generics(element_xml(front_xml, win_tag))
        check(f"{v}: window glazing, frame, radio material",
              got == {"glazing": "doubleLowE", "frameMaterial": "pvc", "radioMaterial": "glass"}, str(got))
        got = generics(element_xml(front_xml, door_tag))
        check(f"{v}: door material", got == {"material": "wood"}, str(got))
        check(f"{v}: no other surface carries a material", text.count(">masonry<") == 2 and text.count(">clayTile<") == 1
              and text.count(">doubleLowE<") == 1 and text.count(">wood<") == 1)
        inst = element_xml(text, "bldg:BuildingInstallation", balcony["gml_id"])
        codes = re.findall(r'<bldg:(class|function|usage) codeSpace="([^"]+)">(\d+)<', inst)
        check(f"{v}: balcony class, function, usage in schema order with SIG3D codeSpaces",
              codes == [("class", SIG3D + "BuildingInstallation_class.xml", "1000"),
                        ("function", SIG3D + "BuildingInstallation_function.xml", "1000"),
                        ("usage", SIG3D + "BuildingInstallation_usage.xml", "1000")], str(codes))
        head = text[:text.find("boundedBy>", text.find("<bldg:Building ")) if v == "2.0" else text.find("<boundary>")]
        check(f"{v}: building class and storeys below ground",
              re.search(r'<bldg:class codeSpace="[^"]*sig3d[^"]*">1000<', text) is not None
              and "<bldg:storeysBelowGround>1</bldg:storeysBelowGround>" in text, head[-600:])

    wall_field, radio_field = card.codelists.element_fields("WallSurface")
    roof_field = card.codelists.element_fields("RoofSurface")[0]
    for v in ("2.0", "3.0"):  # re-import: the values are back on the same surfaces only, and survive both writers
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        res = bpy.ops.cgml3.import_gml_file(filepath=paths[v])
        check(f"{v} re-import", res == {'FINISHED'}, str(res))
        by = {}
        for o in bpy.data.objects:
            by.setdefault(card.codelists.element_kind(str(o.get("cgml3_feature") or "")), []).append(o)
        got = sorted((card.element_read(o, wall_field), card.element_read(o, radio_field)) for o in by["wall"])
        check(f"{v} re-import: two walls carry their material, one its radio material, the others nothing",
              got == [("", ""), ("", ""), ("masonry", ""), ("masonry", "brick")], str(got))
        got = sorted(card.element_read(o, roof_field) for o in by["roof"])
        check(f"{v} re-import: one roof face carries its material", got == ["", "clayTile"], str(got))
        got = [card.element_read(o, card.codelists.element_fields("WindowSurface")[0]) for o in by["window"]]
        check(f"{v} re-import: the window carries its glazing", got == ["doubleLowE"], str(got))
        source = pairs(open(paths[v], encoding="utf-8").read())
        check(f"{v}: eight generic attributes in the export", len(source) == 8, str(source))
        for v2 in ("2.0", "3.0"):
            p = os.path.join(out, f"b88_v{v[0]}_to_v{v2[0]}.gml")
            bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v2)
            got = pairs(open(p, encoding="utf-8").read())
            check(f"{v} -> {v2}: the same generic attributes with the same values, no bookkeeping added",
                  got == source, str(got))

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
