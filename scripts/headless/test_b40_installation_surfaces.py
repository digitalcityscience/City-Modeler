"""B40: a balcony / chimney is written with its own boundary surfaces.

Hamburg's LoD3 installations (buildings_lod3_citygml_2_0 / 3_0) carry their geometry as
boundary surfaces: 2.0 bldg:boundedBy, 3.0 core:boundary, RoofSurface on top and one
WallSurface for the sides. Ours had one "BuildingInstallation" material, so the writers
put all faces straight into lod3Geometry (2.0) / lod3MultiSurface (3.0) with no semantics.

- balcony: WallSurface (sides; B101: the flat railing a second one), OuterCeilingSurface (underside);
  chimney: RoofSurface + WallSurface; one surface per class, no geometry on the
  installation itself (2.0 and 3.0)
- function 1000 / 1030 (SIG3D, B44), every gml:id unique, both files XSD-valid
- ids stay the same in a second export

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b40_installation_surfaces.py -- <out_dir>
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402

GML_IDS = ("{http://www.opengis.net/gml}id", "{http://www.opengis.net/gml/3.2}id")


def ln(el):
    return el.tag.split("}")[1]


def gid(el):
    return next((el.get(k) for k in GML_IDS if el.get(k)), "")


def installations(path):
    """{gml:name: (direct geometry props, {surface class: count}, function, [surface ids])}"""
    out = {}
    for inst in ET.parse(path).iter():
        if ln(inst) != "BuildingInstallation":
            continue
        name = next((c.text for c in inst if ln(c) == "name"), "")
        direct = [ln(c) for c in inst if ln(c).startswith("lod")]
        surfs = [e for e in inst.iter() if ln(e).endswith("Surface") and not ln(e).startswith(("Multi", "Composite", "lod"))]
        func = next((c.text for c in inst if ln(c) == "function"), None)
        out[name] = (direct, Counter(ln(s) for s in surfs), func, [gid(s) for s in surfs])
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b40_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    if not ok:
        sys.exit(1)
    author_everything()

    want = {"Balcony": ({"WallSurface", "OuterCeilingSurface"}, "1000"),  # B101: flat railing, no top
            "Chimney": ({"RoofSurface", "WallSurface"}, "1030")}
    first = {}
    for run in ("a", "b"):
        for v in ("2.0", "3.0"):
            p = os.path.join(out, f"b40_{run}_v{v[0]}.gml")
            res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
            check(f"{run}: export {v}", res == {'FINISHED'}, str(res))
            inst = installations(p)
            if run == "b":
                check(f"{v}: installation surface ids the same in a second export",
                      {k: x[3] for k, x in inst.items()} == first[v])
                continue
            first[v] = {k: x[3] for k, x in inst.items()}
            for name, (classes, code) in want.items():
                direct, surfs, func, _ids = inst.get(name, ([], Counter(), None, []))
                check(f"{v} {name}: no geometry on the installation itself", not direct, str(direct))
                check(f"{v} {name}: own surfaces {sorted(classes)}", classes <= set(surfs), str(dict(surfs)))
                extra = {"WallSurface": 1} if name == "Balcony" else {}  # B101: the railing is a surface of its own
                check(f"{v} {name}: one surface per class", surfs and all(
                    n == 1 + extra.get(c, 0) for c, n in surfs.items()), str(dict(surfs)))
                check(f"{v} {name}: function {code}", func == code, str(func))
            ids = [gid(e) for e in ET.parse(p).iter() if gid(e)]
            dup = [i for i, n in Counter(ids).items() if n > 1]
            check(f"{v}: every gml:id unique", not dup, str(dup[:3]))
            valid, msg = xsd_valid(v, p)
            check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
