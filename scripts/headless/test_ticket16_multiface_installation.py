"""Ticket 16: CityGML 2 multi-face BuildingInstallation export must produce
exactly ONE lod{N}Geometry property per populated LoD, wrapping a single
gml:MultiSurface with all faces as gml:surfaceMember children.

Before the fix, a six-face Chimney exported as six sibling bldg:lod3Geometry
elements (each a MultiSurface of one surfaceMember), which violates the
CityGML 2 AbstractBuildingInstallationType content model.

Runs the Railway CityGML 2 fixture through import/export with semantic
objects ON and OFF, then asserts XML structure (not just feature counts).

Usage:
    SP=/tmp/cgml-probe-16
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket16_multiface_installation.py -- \\
      /tmp/cgml-out-16
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
RAILWAY_V2 = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML2_Building_only.gml"
INSTALL_V2 = REPO_ROOT / "test_files" / "Building" / "SemanticHierarchy_Installations_LoD3_CityGML2.gml"

GML_NS = "http://www.opengis.net/gml"
BLDG_NS = "http://www.opengis.net/citygml/building/2.0"

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _local(tag):
    if isinstance(tag, str) and tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def check(label, condition, detail):
    status = "OK" if condition else "FAIL"
    print(f"[ticket16][{label}] {status}: {detail}")
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def do_roundtrip(fixture, semantic, out_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = semantic
    res = bpy.ops.cgml3.import_gml_file(filepath=str(fixture))
    assert res == {'FINISHED'}, f"import failed: {res}"
    res = bpy.ops.cgml3.export_gml_file(filepath=str(out_path), citygml_version='2.0')
    assert res == {'FINISHED'}, f"export failed: {res}"


def find_installations(root):
    """Return list of BuildingInstallation elements (any nesting)."""
    return [e for e in root.iter() if _local(e.tag) in ("BuildingInstallation", "IntBuildingInstallation")]


def install_members(inst):
    """Return (by_lod_tag_count, total_surfaceMembers) for one installation."""
    lod_geoms = [c for c in list(inst)
                 if _local(c.tag).startswith("lod") and _local(c.tag).endswith("Geometry")]
    by_lod = {}
    for lg in lod_geoms:
        by_lod.setdefault(_local(lg.tag), []).append(lg)
    total_members = 0
    for lg in lod_geoms:
        total_members += sum(1 for d in lg.iter() if _local(d.tag) == "surfaceMember")
    return by_lod, total_members


def inspect(out_path, label, min_installs, expected_members_by_gid=None):
    root = ET.parse(out_path).getroot()
    installs = find_installations(root)
    check(label, len(installs) >= min_installs,
          f"found {len(installs)} BuildingInstallation feature(s) (>= {min_installs})")

    seen = {}
    for inst in installs:
        gid = inst.get(f"{{{GML_NS}}}id") or inst.get(f"{{http://www.opengis.net/gml/3.2}}id") or "<no-id>"
        by_lod, total_members = install_members(inst)
        seen[gid] = total_members

        # Exactly one geometry property per populated LoD (the ticket's core assertion).
        for lod_tag, elems in by_lod.items():
            check(label, len(elems) == 1,
                  f"install {gid}: exactly one <{lod_tag}> (found {len(elems)})")
        check(label, total_members >= 1,
              f"install {gid}: {total_members} surfaceMember(s) under lod geometry")

    # Per-installation expected counts: guards against both face loss and
    # over-grouping (single-face installs must NOT absorb sibling faces).
    if expected_members_by_gid:
        for gid, want in expected_members_by_gid.items():
            got = seen.get(gid)
            check(label, got == want,
                  f"install {gid}: {got} surfaceMember(s), expected {want}")


def main():
    out_dir = Path(args()[0]) if args() else Path("/tmp/cgml-out-16")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Railway: two six-face Chimneys — the headline multi-face bug.
    for semantic, name in ((True, "semantic-on"), (False, "semantic-off")):
        out_path = out_dir / f"railway_{name}.gml"
        do_roundtrip(RAILWAY_V2, semantic, out_path)
        inspect(out_path, f"railway/{name}", min_installs=2, expected_members_by_gid={
            "UUID_7c0c3af1-8b20-43b9-b759-10a36ab1c017": 6,
            "UUID_5fd32809-87e4-4846-a4a5-0d0d05a53a0b": 6,
        })

    # 2) Installations fixture: five separate single-face installations.
    #    Verifies single-face installs keep exactly one surfaceMember and that
    #    the fix does NOT over-group faces from sibling installations into one
    #    property. Feature-level gml:id is preserved by ticket 11.
    for semantic, name in ((True, "semantic-on"), (False, "semantic-off")):
        out_path = out_dir / f"installations_{name}.gml"
        do_roundtrip(INSTALL_V2, semantic, out_path)
        inspect(out_path, f"installations/{name}", min_installs=5, expected_members_by_gid={
            "SEMHIER_INST_CLASS_BALCONY_V2": 1,
            "SEMHIER_INST_FUNCTION_CHIMNEY_V2": 1,
            "SEMHIER_INST_NAME_BALCONY_V2": 1,
            "SEMHIER_INST_BARE_V2": 1,
            "SEMHIER_INST_CONFLICT_FUNCTION_BEATS_NAME_V2": 1,
        })

    print("\n=== ticket16 summary ===")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL {f}")
        print(f"\nRESULT: FAILED ({len(FAILURES)} checks)")
        sys.exit(1)
    print("RESULT: PASSED")


main()
