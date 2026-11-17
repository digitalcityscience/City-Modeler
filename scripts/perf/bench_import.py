"""T03/T08: import and browse benchmark for the generated performance sets.

Driver (plain Python): runs one fresh ``blender -b`` per set with a time
limit, collects the worker results and writes
``<perf-data>/results/<date>-<branch>[-browse].json``:

  python scripts/perf/bench_import.py --blender "C:/.../blender.exe" [--mode import|browse]
      [--sets S M] [--file X.gml] [--data DIR] [--timeout-min 15] [--label NAME] [--load N]

Worker (inside Blender, started by the driver):

  blender -b --python scripts/perf/bench_import.py -- --worker <set.gml> <result.json>
  blender -b --python scripts/perf/bench_import.py -- --worker-browse <set.gml> <result.json> <N>

``--mode import`` (default, set S): total time of ``cgml3.import_gml_file``,
time spent in the builder's phases (read, plan, build, post_import; the rest is
``other``), the time spent in ``apply_materials_uvs`` (materials, part of
build), peak RSS, object/mesh counts and the longest main-thread block.

``--mode browse`` (T08, default set L): index time, the longest main-thread
block while indexing (the worker thread runs; the main thread ticks the job
every 50 ms as the modal timer does - longest tick and largest gap between
ticks), time to the first boxes, peak RSS, proxy object count, then ``N``
(default 6) random buildings loaded: time, bytes read, objects; finally the
time to reopen the file from the index cache.

Run it with an isolated Blender profile (BLENDER_USER_RESOURCES), never on
the profile of a running interactive Blender.
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from procmem import peak_rss_bytes  # noqa: E402

# Builder functions timed by the import benchmark: (module, attribute, phase). The callers look them up
# at call time, so wrapping the module attribute is enough. "materials" runs inside "build".
TIMED = (
    ("common.unified_builder", "read_document", "read"),
    ("shared.builder_payload", "plan_document", "plan"),
    ("common.unified_builder", "build_feature", "build"),
    ("common.builder_support.v3.materials", "apply_materials_uvs", "materials"),
    ("common.builder_support.v2.materials", "apply_materials_uvs", "materials"),
    ("common.import_landing", "finish_import", "post_import"),
)
LONGEST_BLOCK_NOTE = (
    "A full import runs synchronously inside one operator call, so the "
    "longest main-thread block equals the whole import time."
)
BROWSE_NOTE = (
    "Browse: the index is built in a worker thread; the longest main-thread "
    "block is the longest tick (draining results, writing the proxy mesh); "
    "the largest gap between ticks includes the 50 ms timer interval."
)


# --------------------------------------------------------------------------- worker
class _PhaseTimer:
    """Accumulates the time and calls of the ``TIMED`` builder functions per phase."""

    def __init__(self, addon, targets=TIMED):
        self.seconds = {}
        self.calls = {}
        self._patched = []
        for name, attr, phase in targets:
            module = sys.modules.get(f"{addon}.{name}") or __import__(f"{addon}.{name}", fromlist=["x"])
            original = getattr(module, attr, None)
            if original is not None:
                setattr(module, attr, self._wrap(original, phase))
                self._patched.append((module, attr, original))

    def _wrap(self, func, phase):
        def timed(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                self.seconds[phase] = self.seconds.get(phase, 0.0) + time.perf_counter() - t0
                self.calls[phase] = self.calls.get(phase, 0) + 1
        return timed

    def restore(self):
        for module, attr, original in self._patched:
            setattr(module, attr, original)

    def phases(self, total):
        """Seconds per phase (materials left out: it is part of build); ``other`` is the rest of ``total``."""
        out = {p: round(s, 3) for p, s in self.seconds.items() if p != "materials"}
        out["other"] = round(max(0.0, total - sum(out.values())), 3)
        return out


def _worker(gml, result_path):
    import bpy
    sys.path.insert(0, os.path.join(HERE, "..", "tests"))
    from addon_test_utils import ensure_addon_enabled

    result = {"file": os.path.abspath(gml), "bytes": os.path.getsize(gml)}
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(f"[bench] add-on not available: {addon}")
    result["addon_module"] = addon
    result["rss_before_mb"] = round(peak_rss_bytes() / 2**20, 1)
    result["streaming_path"] = False        # no streaming importer any more (Load all is one pass, <= 200 MB)

    timer = _PhaseTimer(addon)
    t0 = time.perf_counter()
    try:
        status = bpy.ops.cgml3.import_gml_file(filepath=os.path.abspath(gml))
    finally:
        total = time.perf_counter() - t0
        timer.restore()
    result["status"] = sorted(status)
    result["total_s"] = round(total, 3)
    result["phases_s"] = timer.phases(total)
    # inside build, see TIMED
    result["materials_s"] = round(timer.seconds.get("materials", 0.0), 3)
    result["materials_calls"] = timer.calls.get("materials", 0)
    result["longest_main_thread_block_s"] = round(total, 3)
    result["longest_block_note"] = LONGEST_BLOCK_NOTE
    result["peak_rss_mb"] = round(peak_rss_bytes() / 2**20, 1)
    objs = bpy.data.objects
    result["objects"] = len(objs)
    result["mesh_objects"] = sum(1 for o in objs if o.type == "MESH")
    result["meshes"] = len(bpy.data.meshes)
    result["materials"] = len(bpy.data.materials)
    result["polygons"] = sum(len(m.polygons) for m in bpy.data.meshes)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"[bench] {json.dumps(result)}")


def _worker_browse(gml, result_path, n_load):
    import random
    import tempfile

    import bpy
    sys.path.insert(0, os.path.join(HERE, "..", "tests"))
    from addon_test_utils import ensure_addon_enabled

    result = {"file": os.path.abspath(gml), "bytes": os.path.getsize(gml), "mode": "browse"}
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    if not ok:
        raise SystemExit(f"[bench] add-on not available: {addon}")
    result["addon_module"] = addon
    cache_dir = tempfile.mkdtemp(prefix="cgml3_bench_cache_")
    os.environ["CGML3_INDEX_CACHE_DIR"] = cache_dir
    browse = __import__(f"{addon}.ops.browse_gml", fromlist=["x"])
    job = __import__(f"{addon}.common.background_job", fromlist=["x"])
    result["rss_before_mb"] = round(peak_rss_bytes() / 2**20, 1)

    # index in the worker thread; the main thread ticks like the modal timer
    session, drv = browse.start_browse(bpy.context, gml, use_cache=True, manual=True)
    while not drv.tick():
        time.sleep(job.TICK_INTERVAL_S)
    result["index_s"] = round(drv.stats["seconds"], 3)
    result["ticks"] = drv.stats["ticks"]
    result["longest_main_thread_block_s"] = round(drv.stats["max_tick_s"], 4)
    result["max_tick_gap_s"] = round(drv.stats["max_gap_s"], 4)
    result["max_tick_gap_at_s"] = drv.stats.get("max_gap_at_s")
    result["tick_interval_s"] = job.TICK_INTERVAL_S
    result["first_boxes_s"] = round(session.stats.get("first_boxes_s", float("nan")), 3)
    result["max_proxy_write_s"] = round(session.stats.get("max_proxy_write_s", 0.0), 4)
    result["peak_rss_mb"] = round(peak_rss_bytes() / 2**20, 1)
    result["features"] = len(session.entries)
    result["preview"] = session.preview()
    result["risks"] = session.index.risk_counts()
    result["proxy_objects"] = sum(1 for o in bpy.data.objects if o.get("cgml3_browse_path"))
    proxy = session.proxy()
    result["proxy_faces"] = len(proxy.data.polygons) if proxy else 0

    picks = random.Random(6).sample(range(len(session.entries)), min(n_load, len(session.entries)))
    browse.set_selection(session, picks, 'SET')
    t0 = time.perf_counter()
    status = bpy.ops.cgml3.browse_load(mode='LOD2')
    result["load_status"] = sorted(status)
    result["load_s"] = round(time.perf_counter() - t0, 3)
    result["load_features"] = session.last_load.get("features")
    result["load_bytes_read"] = session.last_load.get("bytes_read")
    result["load_objects"] = session.last_load.get("objects")
    result["load_message"] = session.last_load.get("message")
    result["peak_rss_after_load_mb"] = round(peak_rss_bytes() / 2**20, 1)

    t0 = time.perf_counter()
    bpy.ops.cgml3.browse_gml_file(filepath=os.path.abspath(gml))
    reopened = browse.get_session(gml)
    result["reopen_s"] = round(time.perf_counter() - t0, 3)
    result["reopen_from_cache"] = bool(reopened and reopened.from_cache)
    result["status"] = ["FINISHED"] if session.complete and status == {'FINISHED'} else ["FAILED"]
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"[bench] {json.dumps(result)}")


# --------------------------------------------------------------------------- driver
def _git(*args):
    import subprocess
    try:
        return subprocess.run(["git", *args], cwd=HERE, text=True, capture_output=True,
                              check=True).stdout.strip()
    except Exception:
        return ""


def _driver(argv):
    import argparse
    import subprocess
    import tempfile

    from make_perf_sets import SETS, default_out_dir, set_path

    p = argparse.ArgumentParser(description="Benchmark CityGML imports in background Blender")
    p.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    p.add_argument("--data", default=str(default_out_dir()))
    p.add_argument("--mode", choices=("import", "browse"), default="import")
    # Full imports of M/L are not a goal of the series (browse-first, T08/T09)
    # and need >10 GB RAM; benchmark them only on request. Browse defaults to L.
    p.add_argument("--sets", nargs="*", default=None, choices=list(SETS))
    p.add_argument("--load", type=int, default=6, help="browse: buildings to load (default 6)")
    p.add_argument("--file", action="append", default=[], metavar="PATH",
                   help="also benchmark this CityGML file (named after its stem); repeatable")
    p.add_argument("--timeout-min", type=float, default=15.0)
    p.add_argument("--label", help="result name suffix (default: current git branch)")
    p.add_argument("--out", help="result file (default: <data>/results/<date>-<label>.json)")
    a = p.parse_args(argv)
    if a.sets is None:
        a.sets = ["L"] if a.mode == "browse" else ["S"]
        if a.file and a.mode == "browse" and "--sets" not in argv:
            a.sets = []

    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    label = a.label or branch
    suffix = "-browse" if a.mode == "browse" else ""
    out = a.out or os.path.join(a.data, "results",
                                f"{time.strftime('%Y-%m-%d')}-{label.replace('/', '_')}{suffix}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    report = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "branch": branch,
        "commit": _git("rev-parse", "--short", "HEAD"),
        "blender": a.blender,
        "user_resources": os.environ.get("BLENDER_USER_RESOURCES", ""),
        "timeout_s": a.timeout_min * 60,
        "mode": a.mode,
        "note": LONGEST_BLOCK_NOTE if a.mode == "import" else BROWSE_NOTE,
        "sets": {},
    }
    from pathlib import Path
    targets = [(name, str(set_path(Path(a.data), name))) for name in a.sets]
    targets += [(Path(f).stem, os.path.abspath(f)) for f in a.file]
    for name, gml in targets:
        if not os.path.isfile(gml):
            p.error(f"missing file {gml}; run make_perf_sets.py first")
        with tempfile.TemporaryDirectory() as tmp:
            res_file = os.path.join(tmp, "result.json")
            log_file = os.path.join(os.path.dirname(out), f".{name}.log")
            worker = ["--worker", gml, res_file] if a.mode == "import" else \
                ["--worker-browse", gml, res_file, str(a.load)]
            cmd = [a.blender, "-b", "--python-exit-code", "1",
                   "--python", os.path.abspath(__file__), "--", *worker]
            t0 = time.perf_counter()
            with open(log_file, "w", encoding="utf-8", errors="replace") as log:
                proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = proc.wait(timeout=a.timeout_min * 60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    code = None
            wall = round(time.perf_counter() - t0, 1)
            if code is None:
                entry = {"completed": False, "reason": f"time limit {a.timeout_min:g} min exceeded"}
            elif code != 0 or not os.path.isfile(res_file):
                entry = {"completed": False, "reason": f"blender exit code {code}", "log": log_file}
            else:
                with open(res_file, encoding="utf-8") as f:
                    entry = json.load(f)
                entry["completed"] = entry.get("status") == ["FINISHED"]
            entry["wall_s"] = wall
        report["sets"][name] = entry
        print(_row(name, entry))
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    print(f"result: {out}")


def _row(name, e):
    if not e.get("completed"):
        return f"{name:8s} NOT COMPLETED ({e.get('reason', e.get('status'))}, wall {e['wall_s']} s)"
    if e.get("mode") == "browse":
        return (f"{name:8s} index {e['index_s']:6.1f} s  longest block {e['longest_main_thread_block_s'] * 1000:5.0f} ms  "
                f"max gap {e['max_tick_gap_s'] * 1000:5.0f} ms  first boxes {e['first_boxes_s']:.2f} s  "
                f"peak RSS {e['peak_rss_mb']:7.1f} MB  proxy objects {e['proxy_objects']}  "
                f"load {e['load_features']} in {e['load_s']:.2f} s, {e['load_bytes_read'] / 1024:.0f} KB  "
                f"reopen {e['reopen_s']:.2f} s{' (cache)' if e['reopen_from_cache'] else ''}")
    return (f"{name:8s} {e['total_s']:8.1f} s  peak RSS {e['peak_rss_mb']:8.1f} MB  "
            f"objects {e['objects']:7d}  longest block {e['longest_main_thread_block_s']:.1f} s  "
            f"phases {e['phases_s']}  (materials {e.get('materials_s', 0):.1f} s of build)")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if args[:1] == ["--worker"]:
        _worker(args[1], args[2])
    elif args[:1] == ["--worker-browse"]:
        _worker_browse(args[1], args[2], int(args[3]) if len(args) > 3 else 6)
    else:
        _driver(args)
