"""B94: new elements carry their own gml:id; long code lists have a search popup.

- authored window, door, balcony, chimney (and every surface): a non-empty, unique ``gml_id`` on the
  object right after placement, and that same id is written as ``gml:id`` in the 2.0 and 3.0 export
- ``cgml.attr_search`` (the search button next to a long dropdown) writes the picked code like the
  dropdown does: building function by code, a balcony's class through the element field
- SIG3D building class 1110-1130 and function 1110 match sig3d.org (citydb-tool had them shifted)

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b94_ids_search.py -- <out_dir>
"""
import os
import sys
import tempfile
from collections import Counter
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything, export  # noqa: E402
from test_b88_element_semantics import select  # noqa: E402


class Labels:
    """A layout that records every ``label(text=...)`` in draw order."""
    def __init__(self, out):
        object.__setattr__(self, "out", out)

    def __setattr__(self, name, value):
        pass

    def __getattr__(self, name):
        def call(*a, **k):
            if name == "label":
                self.out.append(k.get("text", ""))
            return (self, self) if name == "panel" else self
        return call


def labels(card):
    out = []
    card.CGML3_PT_Attributes.draw(type("P", (), {"layout": Labels(out)})(), bpy.context)
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b94_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    codelists = import_module(f"{addon}.shared.codelists")

    cls = codelists.codes("OGC", "bldg:class")
    fn = codelists.codes("OGC", "bldg:function")
    check("SIG3D class 1110-1130 as on sig3d.org",
          (cls.get("1110"), cls.get("1120"), cls.get("1130"))
          == ("maintenance and waste management", "healthcare", "communicating"), str(cls))
    check("SIG3D function 1110 summer house", fn.get("1110") == "summer house")

    b = author_everything()
    kids = list(b.children_recursive)
    elements = {k: next((o for o in kids if o.get("cgml_semantic_type") == k
                         or o.get("cgml3_feature") == f"{k}Surface"), None)
                for k in ("Window", "Door", "Balcony", "Chimney")}
    for k, o in elements.items():
        check(f"{k} placed with a gml_id", o is not None and str(o.get("gml_id") or "").strip(),
              f"{o and o.name}: {o and o.get('gml_id')}")
    ids = [str(o.get("gml_id") or "") for o in [b] + kids if o.type == 'MESH' or o is b]
    dup = [i for i, n in Counter(ids).items() if n > 1 and i]
    check("no two objects share an id", not dup, str(dup))
    check("every surface / element has an id", all(ids), str([o.name for o in kids if not o.get("gml_id")]))

    paths = export(out, "b94")
    for v, p in paths.items():
        text = open(p, encoding="utf-8").read()
        missing = [k for k, o in elements.items() if o and f'gml:id="{o["gml_id"]}"' not in text]
        check(f"{v}: the objects' ids are the written gml:ids", not missing, str(missing))

    wm = bpy.context.window_manager
    select(b)
    r = bpy.ops.cgml.attr_search('EXEC_DEFAULT', prop="cgml_attr_function", code="31001_2463")
    check("search: building function by code", r == {'FINISHED'} and b.get("bldg:function") == "31001_2463",
          f"{r} {b.get('bldg:function')}")
    select(elements["Balcony"])
    r = bpy.ops.cgml.attr_search('EXEC_DEFAULT', prop="cgml_elem_0", code="1050")
    check("search: balcony class via the element field", r == {'FINISHED'}
          and elements["Balcony"].get("bldg:class") == "1050" and wm.cgml_elem_0 == "1050",
          f"{r} {elements['Balcony'].get('bldg:class')}")

    # B95: the card shows the LoD2-DE core first; AdV has no class / usage, so they stay hidden unless set
    card = import_module(f"{addon}.ui.attribute_card")
    select(b)
    for k in ("bldg:class", "bldg:usage"):
        b.pop(k, None)
    shown = labels(card)
    basic = shown[:shown.index("More")]
    check("AdV building: basic rows are function, roof type, height, storeys",
          card.list_of(b) == "ADV" and [s for s in basic if s in card.LABELS.values()]
          == ["Function", "Roof type", "Height", "Storeys"], str(shown))
    check("AdV building: no Class / Usage row", "Class" not in shown and "Usage" not in shown, str(shown))
    b["bldg:class"] = "1000"
    check("a class from the file is still shown (under More)", "Class" in labels(card)[labels(card).index("More"):])

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
