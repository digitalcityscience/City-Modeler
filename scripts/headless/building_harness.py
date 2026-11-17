"""Shared checks for the building-series headless tests (B04+).

    check(label, ok, detail)        record PASS/FAIL
    export_checked(path, version)   export the scene, XSD-validate (with a
                                    broken-copy negative control), return text
    finish(name)                    print the verdict, exit 1 on failures
"""
import re
import sys
from pathlib import Path

import bpy

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"[PASS] {label}")
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def _xsd_valid(version, path):
    if version == "3.0":
        from city_modeler.ops.validate import validate_citygml3_xsd as validate
    else:
        from city_modeler.ops.validate_citygml2 import validate_citygml2_xsd as validate
    ok, msg = validate(str(path))
    skipped = any(word in msg for word in ("Hinweis", "Warnung", "übersprungen", "skipped"))
    return ok and not skipped, msg


def export_checked(path, version):
    """Export the whole scene to ``path``; XSD-check it; return its text ('' on failure)."""
    path = Path(path)
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    except Exception as e:
        res = str(e)
    check(f"{version}: export finishes", res == {'FINISHED'}, f"result={res}")
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    valid, msg = _xsd_valid(version, path)
    check(f"{version}: XSD valid", valid, msg[:400])
    bad = path.with_name(path.stem + "_broken.gml")
    bad.write_text(re.sub(r"(<bldg:Building [^>]*>)", r"\1<bldg:notAnElement/>", text, count=1), encoding="utf-8")
    check(f"{version}: XSD check rejects a broken copy (negative control)", not _xsd_valid(version, bad)[0])
    return text


def count(text, tag):
    return len(re.findall(rf"<{tag}[ >]", text))


def finish(name):
    if FAILURES:
        print(f"\n[FAIL] {name} NOT satisfied ({len(FAILURES)} failure(s))")
        sys.exit(1)
    print(f"\n[ALL PASS] {name}")
