"""Real xmllint --schema validation for the appearance ticket 03 CityGML 3
fixtures (docs/agent/appearance/issues/03-appearance-fixtures.md).

The fixture manifest originally noted these files were only validated ad hoc
against the vendored Schema/citygml_3 XSDs, with no committed harness (unlike
the CityGML 2 fixtures, which ticket 18's scripts/headless/xmllint_v2/
harness already covers). This script closes that gap using the same
approach: a wrapper schema + XML catalog, validated with the real system
xmllint. No bpy/Blender needed - these are static fixture files, not
exporter output.

Usage:
    python3 scripts/headless/xmllint_v3/validate_appearance_fixtures.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS = REPO_ROOT / "scripts" / "headless" / "xmllint_v3"
WRAPPER = HARNESS / "wrapper_v3.xsd"
CATALOG = HARNESS / "catalog.xml"
FIXTURES_DIR = REPO_ROOT / "test_files" / "Building"

FAILURES = []


def check(label, condition, detail):
    print(f"[appearance-v3-xmllint][{label}] {'OK' if condition else 'FAIL'}: {detail}")
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def validate(fixture_path):
    label = fixture_path.name
    xmllint = shutil.which("xmllint")
    if not xmllint:
        check(label, False, "xmllint not found on PATH (cannot run real XSD validation)")
        return
    cmd = [xmllint, "--noout", "--schema", str(WRAPPER), str(fixture_path)]
    env = dict(os.environ, XML_CATALOG_FILES=str(CATALOG))
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[appearance-v3-xmllint][{label}] xmllint exit code: {proc.returncode}")
        if proc.stdout.strip():
            print(f"[appearance-v3-xmllint][{label}] stdout: {proc.stdout.strip()}")
        if proc.stderr.strip():
            print(f"[appearance-v3-xmllint][{label}] stderr: {proc.stderr.strip()}")
    check(label, proc.returncode == 0, f"xmllint --schema exit {proc.returncode} (expected 0)")


def main():
    check("harness", WRAPPER.is_file(), f"wrapper schema present: {WRAPPER}")
    check("harness", CATALOG.is_file(), f"catalog present: {CATALOG}")

    fixtures = sorted(FIXTURES_DIR.glob("SemanticHierarchy_Appearance_*_CityGML3.gml"))
    check("harness", len(fixtures) > 0, f"found appearance v3 fixtures under {FIXTURES_DIR}")

    for fixture_path in fixtures:
        validate(fixture_path)

    print("\n=== appearance-v3-xmllint summary ===")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL {f}")
        print(f"\nRESULT: FAILED ({len(FAILURES)} checks)")
        sys.exit(1)
    print(f"RESULT: PASSED ({len(fixtures)} fixtures validated)")


main()
