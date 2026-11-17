"""T03: check the generated sets with the add-on's own validators.

Every set must be well-formed (ops/validate_auto.well_formed_citygml_auto);
the sets given with --xsd are also validated against the bundled XSDs
(ops/validate_auto.validate_citygml_auto). Runs in Blender, which provides the
add-on package and its lxml wheel:

  blender -b --python scripts/perf/validate_sets.py -- <perf-data dir> [--xsd S]

Exits 1 when any check fails.
"""
import argparse
import importlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

from addon_test_utils import ensure_addon_enabled  # noqa: E402

from make_perf_sets import SETS, set_path  # noqa: E402  (same folder)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("data_dir")
    p.add_argument("--sets", nargs="+", default=list(SETS))
    p.add_argument("--xsd", nargs="*", default=["S"])
    a = p.parse_args(argv)

    ok, addon = ensure_addon_enabled()
    if not ok:
        print(f"[validate-sets] ERROR: {addon}")
        sys.exit(1)
    validate_auto = importlib.import_module(f"{addon}.ops.validate_auto")

    from pathlib import Path
    failures = []
    for name in a.sets:
        path = str(set_path(Path(a.data_dir), name))
        t0 = time.perf_counter()
        good, msg = validate_auto.well_formed_citygml_auto(path)
        print(f"  [{'OK' if good else 'FAIL'}] {name} well-formed ({time.perf_counter() - t0:.1f} s): {msg}")
        if not good:
            failures.append(f"{name} well-formed")
        if name in a.xsd:
            t0 = time.perf_counter()
            good, msg = validate_auto.validate_citygml_auto(path)
            print(f"  [{'OK' if good else 'FAIL'}] {name} XSD ({time.perf_counter() - t0:.1f} s): {msg}")
            if not good:
                failures.append(f"{name} XSD")
    print(f"[validate-sets] {'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    sys.exit(1 if failures else 0)


main()
