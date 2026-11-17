"""Ticket 18: lock in real xmllint XSD validation for ticket 11's CityGML 2
semantic-object export.

Ticket 11's original XSD coverage (test_ticket11_xsd.py) relied on the
exporter's `xsd_validate` toggle. Ticket 13 established that toggle is NOT a
real schema oracle — its "Structure Check" is an internal semantic-rule
checker, and its "XSD Validation" phase is broken for CityGML 2 (the vendored
profile schema never declares core:CityModel, so it reports "No matching
global declaration available for the validation root" on every file — the
ticket 11 infra bug). This test replaces that proxy with genuine
`xmllint --schema` against the vendored CityGML 2 schemas.

It runs the ticket 11 scenario (import the Railway LoD3 CityGML 2 fixture,
export back to CityGML 2) with `use_semantic_objects` both True and False,
then shells out to the system `xmllint` for each output and asserts exit 0.
The exact command and exit code are printed for every run.

xmllint is not part of Blender's bundled Python, but it is on the host PATH,
so this script invokes it via subprocess. The schema entry point is the
committed wrapper at scripts/headless/xmllint_v2/wrapper_v2.xsd (imports
building + appearance + generics); catalog.xml in the same directory redirects
the unreachable OASIS xAL import to the vendored copy. cityGMLBase.xsd and the
GML 3.1.1 base are fetched transitively over plain http, so this test needs
network access (same requirement as ticket 13/16's companion xmllint pass).

Usage:
    SP=/tmp/cgml-probe-18
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket18_xmllint_validation.py -- "$SP/out"
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_V2 = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML2_Building_only.gml"
HARNESS = REPO_ROOT / "scripts" / "headless" / "xmllint_v2"
WRAPPER = HARNESS / "wrapper_v2.xsd"
CATALOG = HARNESS / "catalog.xml"

# ticket 16 fixed the multi-face BuildingInstallation content-model defect that
# used to make this fixture fail real xmllint at the second lod2Geometry
# (exit 3). If validation regresses to that specific failure, say so explicitly
# so it is not confused with a brand-new bug.
TICKET16_SIGNATURE = "lod2Geometry"

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(label, condition, detail):
    print(f"[ticket18][{label}] {'OK' if condition else 'FAIL'}: {detail}")
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def export_fixture(semantic, out_path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = semantic
    res = bpy.ops.cgml3.import_gml_file(filepath=str(FIXTURE_V2))
    assert res == {'FINISHED'}, f"import failed: {res}"
    # Deliberately do NOT set xsd_validate: this test is the schema oracle, not
    # the add-on's internal checker.
    res = bpy.ops.cgml3.export_gml_file(filepath=str(out_path), citygml_version='2.0')
    assert res == {'FINISHED'}, f"export failed: {res}"


def validate(out_path, label):
    xmllint = shutil.which("xmllint")
    if not xmllint:
        check(label, False, "xmllint not found on PATH (cannot run real XSD validation)")
        return
    cmd = [xmllint, "--noout", "--schema", str(WRAPPER), str(out_path)]
    # the local schema mirror first: libxml2 >= 2.14 cannot fetch the http imports itself
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cac_harness
    catalogs = f"{cac_harness.schema_cache_catalog()} {CATALOG}"
    env = dict(os.environ, XML_CATALOG_FILES=catalogs)
    printable = f"XML_CATALOG_FILES={catalogs} " + " ".join(cmd)
    print(f"[ticket18][{label}] command: {printable}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    print(f"[ticket18][{label}] xmllint exit code: {proc.returncode}")
    if proc.stdout.strip():
        print(f"[ticket18][{label}] stdout: {proc.stdout.strip()}")
    if proc.stderr.strip():
        print(f"[ticket18][{label}] stderr: {proc.stderr.strip()}")
    if proc.returncode != 0 and TICKET16_SIGNATURE in proc.stderr:
        print(f"[ticket18][{label}] NOTE: failure mentions '{TICKET16_SIGNATURE}' — "
              "this looks like a regression of the ticket 16 multi-face "
              "BuildingInstallation defect, NOT a new bug.")
    check(label, proc.returncode == 0,
          f"xmllint --schema exit {proc.returncode} (expected 0)")


def main():
    out_dir = Path(args()[0]) if args() else Path("/tmp/cgml-out-18")
    out_dir.mkdir(parents=True, exist_ok=True)

    check("harness", WRAPPER.is_file(), f"wrapper schema present: {WRAPPER}")
    check("harness", CATALOG.is_file(), f"catalog present: {CATALOG}")
    check("harness", FIXTURE_V2.is_file(), f"ticket 11 fixture present: {FIXTURE_V2}")

    for semantic, name in ((True, "semantic-on"), (False, "semantic-off")):
        out_path = out_dir / f"ticket11_{name}.gml"
        export_fixture(semantic, out_path)
        validate(out_path, name)

    print("\n=== ticket18 summary ===")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL {f}")
        print(f"\nRESULT: FAILED ({len(FAILURES)} checks)")
        sys.exit(1)
    print("RESULT: PASSED")


main()
