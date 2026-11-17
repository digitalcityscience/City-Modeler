"""B47: no fabricated Building function; a missing one is reported at export (A5).

CityGML (2.0 Annex C / 3.0) makes bldg:function optional; AdV's profile requires it. The add-on
never invents a code: an authored building without a function is exported without one, and the
export reports a WARNING naming it. The AdV list the card offers is the official one
(BuildingFunctionTypeAdV.xml, 301 codes; RoofTypeTypeAdV.xml), not the model-written draft.

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b47_building_function.py -- <out_dir>
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

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402


def export_capturing(path, version):
    """Export; returns the console text (Blender prints operator reports to stdout)."""
    log = path + ".log"
    sys.stdout.flush()
    saved = os.dup(1)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.dup2(fd, 1)
    try:
        bpy.ops.cgml3.export_gml_file(filepath=path, citygml_version=version)
        sys.stdout.flush()
    finally:
        os.dup2(saved, 1)
        os.close(fd)
        os.close(saved)
    return open(log, encoding="utf-8", errors="replace").read()


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b47_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    codelists = import_module(f"{addon}.shared.codelists")
    au = import_module(f"{addon}.shared.attribute_utils")

    adv = codelists.codes("ADV", "bldg:function")
    check("AdV function list is the official one (301 codes)", len(adv) == 301, str(len(adv)))
    check("deleted AdV code 31001_2465 is gone", "31001_2465" not in adv)
    check("AdV labels", adv.get("31001_1000") == "Wohngebäude" and adv.get("31001_2020") == "Bürogebäude",
          f"{adv.get('31001_1000')} {adv.get('31001_2020')}")
    check("AdV roof list is the official one", len(codelists.codes("ADV", "bldg:roofType")) == 15)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    b.name = "Haus_ohne_Funktion"
    check("helper names the building without a function", au.features_without_function([b]) == [b.name],
          str(au.features_without_function([b])))
    for v in ("3.0", "2.0"):
        p = os.path.join(out, f"nofunc_v{v[0]}.gml")
        text = export_capturing(p, v)
        gml = open(p, encoding="utf-8").read()
        check(f"{v}: no function written", not re.search(r"<bldg:function", gml))
        check(f"{v}: export warns about the missing function", "Haus_ohne_Funktion" in text and "function" in text.lower(),
              text[-400:])
    b["bldg:function"] = "31001_1000"
    check("helper: nothing missing once set", au.features_without_function([b]) == [])
    p = os.path.join(out, "func_v3.gml")
    text = export_capturing(p, "3.0")
    check("3.0: no warning once the function is set", "without" not in text.lower(), text[-400:])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
