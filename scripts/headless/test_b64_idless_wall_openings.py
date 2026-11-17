"""B64 (AB-17): windows of id-less walls stay on their wall.

CitySmith CS1_lod3 (CityGML 2.0, WallSurfaces and Windows without gml:id), merged and semantic
import, whole scene:
- semantic import: every one of the 25 Windows is a child of its Wall- or RoofSurface (was: of the Building)
- export 3.0: XSD valid (was 25 errors: con:Window written as the Building's boundary), all 25
  Windows inside a WallSurface's con:fillingSurface; export 2.0: XSD valid, 25 Windows under
  bldg:opening of a WallSurface
- importing twice gives the same wall ids (stable)

Old code: the Windows hang off the Building, the 3.0 export has 25 XSD errors.

Usage (isolated profile, see tickets/README.md):
  blender -b --factory-startup --python-use-system-env --python-exit-code 1 \\
      --python scripts/headless/test_b64_idless_wall_openings.py -- <out_dir>
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

CS1 = os.path.abspath(os.path.join(HERE, "..", "..", "test_files", "Building", "CitySmith", "CS1_lod3.gml"))


def load(sem):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.context.scene.cgml3.use_semantic_objects = sem
    bpy.ops.cgml3.import_gml_file(filepath=CS1)


def windows_in(text, version):
    """Count Windows inside a Wall- or RoofSurface (3.0 fillingSurface / 2.0 opening)."""
    hosts = re.findall(r"<(?:con|bldg):(Wall|Roof)Surface\b(.*?)</(?:con|bldg):\1Surface>", text, re.S)
    tag = "con:fillingSurface" if version == "3.0" else "bldg:opening"
    return sum(len(re.findall(rf"<{tag}>\s*<(?:con|bldg):Window(?:Surface)?\b", body)) for _t, body in hosts)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b64_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(addon)
    api = import_module(addon + ".shared.agent_api")

    load(True)
    wins = [o for o in bpy.data.objects if o.get("cgml3_feature") == "Window"]
    check("25 windows, each a child of its Wall/RoofSurface (5 are roof windows)", len(wins) == 25 and all(
        w.parent is not None and w.parent.get("cgml3_feature") in ("WallSurface", "RoofSurface") for w in wins),
        str(sorted({(w.parent.get("cgml3_feature") if w.parent else None) for w in wins})))
    walls1 = sorted(str(o.get("gml_id")) for o in bpy.data.objects if o.get("cgml3_feature") == "WallSurface")

    for sem in (True, False):
        load(sem)
        for v in ("3.0", "2.0"):
            p = os.path.join(out, f"cs1_{'sem' if sem else 'merged'}_{v}.gml")
            r = api.export(p, v)
            text = open(p, encoding="utf-8").read()
            valid, msg = xsd_valid(v, p)
            check(f"{'sem' if sem else 'merged'} {v}: XSD valid", r["ok"] and valid, msg[:200])
            check(f"{'sem' if sem else 'merged'} {v}: 25 windows inside their surfaces", windows_in(text, v) == 25,
                  str(windows_in(text, v)))

    load(True)
    walls2 = sorted(str(o.get("gml_id")) for o in bpy.data.objects if o.get("cgml3_feature") == "WallSurface")
    check("wall ids stable across imports", walls1 == walls2)

    print("ALL PASS" if not FAILURES else f"FAILED: {len(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


main()
