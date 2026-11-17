"""Run the authoring-core (CAC) headless tests, one Blender process each.

Usage (inside Blender, as in PLAN.md §8.1)::

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/run_cac_suite.py -- <out_dir> [--only p0] [--update-goldens]

It also runs under plain Python when ``BLENDER_EXE`` is set. Options for
checking the runner itself: ``--tests-dir <dir>``, ``--registry <json>``.

Every ``test_cac_*.py`` writes ``<out_dir>/<stem>.result.json`` (see
cac_harness.Results). Each check id is classified against
scripts/tests/known_arch_bugs.json (strict expected failures):

    pass   check ok, not listed          xfail  check failed, listed
    fail   check failed, not listed      xpass  check ok, listed  -> suite fails

A missing or unreadable result file is a ``fail`` for the whole script.
Exit code 0 only when there is no ``fail`` and no ``xpass``.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HEADLESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HEADLESS_DIR.parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "scripts" / "tests" / "known_arch_bugs.json"
TIMEOUT_S = 1800


def parse_args(argv):
    opts = {"out_dir": None, "only": None, "update_goldens": False,
            "tests_dir": HEADLESS_DIR, "registry": DEFAULT_REGISTRY}
    it = iter(argv)
    for arg in it:
        if arg == "--only":
            opts["only"] = next(it)
        elif arg == "--update-goldens":
            opts["update_goldens"] = True
        elif arg == "--tests-dir":
            opts["tests_dir"] = Path(next(it))
        elif arg == "--registry":
            opts["registry"] = Path(next(it))
        elif arg.startswith("--"):
            raise SystemExit(f"unknown option {arg}")
        else:
            opts["out_dir"] = Path(arg)
    if opts["out_dir"] is None:
        raise SystemExit("usage: run_cac_suite.py -- <out_dir> [--only p0] [--update-goldens]")
    return opts


_AB_ID = re.compile(r"^AB-\d{2}$")
_REGISTRY_FIELDS = {"test_ids", "reason", "fixed_in"}


def validate_registry(data):
    """Schema problems of a known_arch_bugs.json document (empty list = valid).

    ``{"AB-01": {"test_ids": ["<script stem>::<check>", ...], "reason": "...",
    "fixed_in": "P6"}}``; a test id may belong to one AB entry only.
    """
    if not isinstance(data, dict):
        return ["top level must be an object"]
    problems, owner = [], {}
    for ab, entry in data.items():
        if not _AB_ID.match(ab):
            problems.append(f"{ab}: key must look like AB-nn")
        if not isinstance(entry, dict):
            problems.append(f"{ab}: entry must be an object")
            continue
        if set(entry) != _REGISTRY_FIELDS:
            problems.append(f"{ab}: fields must be exactly {sorted(_REGISTRY_FIELDS)}")
        for field in ("reason", "fixed_in"):
            if not isinstance(entry.get(field), str) or not entry.get(field, "").strip():
                problems.append(f"{ab}: {field} must be a non-empty string")
        test_ids = entry.get("test_ids")
        if not isinstance(test_ids, list) or not test_ids:
            problems.append(f"{ab}: test_ids must be a non-empty list")
            continue
        for tid in test_ids:
            if not isinstance(tid, str) or not re.match(r"^test_cac_\w+::\S.*$", tid):  # check labels may contain spaces
                problems.append(f"{ab}: bad test id {tid!r} (expected test_cac_<name>::<check>)")
            elif tid in owner:
                problems.append(f"{ab}: {tid} already listed under {owner[tid]}")
            else:
                owner[tid] = ab
    return problems


def load_registry(path):
    """``{test_id: AB id}`` from known_arch_bugs.json; an invalid file stops the suite."""
    data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else {}
    problems = validate_registry(data)
    if problems:
        raise SystemExit("invalid known_arch_bugs.json:\n  " + "\n  ".join(problems))
    return {tid: ab for ab, entry in data.items() for tid in entry["test_ids"]}


def classify(checks, expected_failures):
    """``{test_id: status}`` for one script's checks (see module docstring)."""
    statuses = {}
    for test_id, result in checks.items():
        listed = test_id in expected_failures
        if result["ok"]:
            statuses[test_id] = "xpass" if listed else "pass"
        else:
            statuses[test_id] = "xfail" if listed else "fail"
    return statuses


def suite_exit_code(statuses):
    return 1 if any(s in ("fail", "xpass") for s in statuses.values()) else 0


def discover(tests_dir, only):
    pattern = f"test_cac_{only}_*.py" if only else "test_cac_*.py"
    return sorted(Path(tests_dir).glob(pattern))


def blender_exe():
    try:
        import bpy  # noqa: F401  (only to find the running binary)
        return bpy.app.binary_path
    except ImportError:
        exe = os.environ.get("BLENDER_EXE")
        if not exe:
            raise SystemExit("not running inside Blender and BLENDER_EXE is not set")
        return exe


def run_script(exe, script, out_dir, update_goldens):
    result_path = out_dir / f"{script.stem}.result.json"
    if result_path.exists():
        result_path.unlink()
    cmd = [exe, "--background", "--factory-startup", "--python-exit-code", "1",
           "--python", str(script), "--", str(out_dir)]
    if update_goldens:
        cmd.append("--update-goldens")
    log_path = out_dir / f"{script.stem}.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        try:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    try:
        checks = json.loads(result_path.read_text(encoding="utf-8"))["checks"]
    except (OSError, ValueError, KeyError) as exc:
        checks = {f"{script.stem}::<result>": {
            "ok": False, "detail": f"no result file ({exc}); exit={code}; log={log_path}"}}
    return checks, code


def main(argv):
    opts = parse_args(argv)
    if opts["update_goldens"] and os.environ.get("CAC_ALLOW_GOLDEN_UPDATE") != "1":
        raise SystemExit("--update-goldens refused: set CAC_ALLOW_GOLDEN_UPDATE=1")
    out_dir = opts["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = load_registry(opts["registry"])
    scripts = discover(opts["tests_dir"], opts["only"])
    if not scripts:
        print("[cac] no tests found")
        return 1
    # cac_harness.xmllint raises inside the checks; without this ~37 checks fail one by one
    # and look like an order-dependent suite (2026-11-04)
    import shutil
    if not shutil.which("xmllint"):
        raise SystemExit("[cac] xmllint not found on PATH (libxml2 >= 2.14, e.g. anaconda3\\Library\\bin)")

    exe = blender_exe()
    all_statuses, details = {}, {}
    for script in scripts:
        print(f"[cac] running {script.name}")
        checks, code = run_script(exe, script, out_dir, opts["update_goldens"])
        statuses = classify(checks, expected)
        for test_id, status in statuses.items():
            print(f"  {status:<5} {test_id}")
            if status in ("fail", "xpass"):
                details[test_id] = checks[test_id]["detail"]
        all_statuses.update(statuses)

    summary = {s: sum(1 for v in all_statuses.values() if v == s)
               for s in ("pass", "fail", "xfail", "xpass")}
    exit_code = suite_exit_code(all_statuses)
    report = {"summary": summary, "exit_code": exit_code, "statuses": all_statuses, "details": details}
    (out_dir / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[cac] {summary} -> exit {exit_code} (results: {out_dir / 'results.json'})")
    return exit_code


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    code = main(args)
    sys.stdout.flush()
    # Blender ignores sys.exit() codes from --python scripts unless forced.
    os._exit(code)
