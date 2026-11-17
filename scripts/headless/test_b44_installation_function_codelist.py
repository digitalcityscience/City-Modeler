"""B44: authored balcony / chimney carry a SIG3D function code with its codeSpace.

- 2.0 and 3.0: bldg:function 1000 (balcony) / 1030 (chimney), codeSpace = SIG3D
  BuildingInstallation_function list (OGC 12-019 Annex C.3); XSD-valid
- re-import of the 3.0 export: the installations resolve to Balcony / Chimney again,
  by their function code (no name sniffing), and keep the codeSpace

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b44_installation_function_codelist.py -- <out_dir>
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402

CODESPACE = "http://www.sig3d.org/codelists/standard/building/2.0/BuildingInstallation_function.xml"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b44_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    author_everything()
    paths = {}
    for v in ("2.0", "3.0"):
        p = paths[v] = os.path.join(out, f"b44_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        text = open(p, encoding="utf-8").read()
        funcs = sorted(re.findall(r'<bldg:function codeSpace="([^"]+)">(\d+)</bldg:function>', text))
        check(f"{v}: function 1000 / 1030 with the SIG3D codeSpace",
              funcs == [(CODESPACE, "1000"), (CODESPACE, "1030")], str(funcs))
        check(f"{v}: no local 5010 / 5030 code", not re.search(r">50[13]0<", text))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    res = bpy.ops.cgml3.import_gml_file(filepath=paths["3.0"])
    check("re-import", res == {'FINISHED'}, str(res))
    inst = {str(o.get("cgml_semantic_type")): o for o in bpy.data.objects
            if o.get("cgml3_feature") == "BuildingInstallation"}
    check("re-import: Balcony and Chimney resolved", set(inst) == {"Balcony", "Chimney"}, str(sorted(inst)))
    for kind, code in (("Balcony", "1000"), ("Chimney", "1030")):
        o = inst.get(kind)
        if o is None:
            continue
        check(f"re-import {kind}: resolved by function", o.get("cgml_subtype_origin") == "function",
              str(o.get("cgml_subtype_origin")))
        check(f"re-import {kind}: function {code} + codeSpace kept",
              str(o.get("bldg:function")) == code and o.get("cgml3_codespace:bldg:function") == CODESPACE,
              f"{o.get('bldg:function')} {o.get('cgml3_codespace:bldg:function')}")

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
