"""T03: regenerate the standard performance sets with one command.

  python scripts/perf/make_perf_sets.py [--source-dir DIR] [--out DIR] [--only S M]

Sources: buildings_lod{2,3}_citygml_{2_0,3_0}.gml from --source-dir
(default: $CGML_PERF_SOURCE_DIR, else ~/Desktop/blender_test_files/buildings).
Output: --out (default: $CGML_PERF_DATA, else <repo>/../perf-data), outside
the repository. Every set is generated in its own process so its time and peak
RSS are measured separately; the results go to <out>/manifest.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

LOD2_V3 = "buildings_lod2_citygml_3_0.gml"
LOD3_V3 = "buildings_lod3_citygml_3_0.gml"
LOD2_V2 = "buildings_lod2_citygml_2_0.gml"

# name: (source file, building count, extra generator options)
SETS = {
    "S": (LOD2_V3, 300, []),
    "M": (LOD2_V3, 5000, []),
    "L": (LOD2_V3, 20000, []),
    "M-lod3": (LOD3_V3, 5000, []),
    "M-noenv": (LOD2_V3, 5000, ["--strip-envelopes"]),
    "M-lod0": (LOD2_V3, 5000, ["--with-lod0"]),
    "M-gml2": (LOD2_V2, 5000, []),
    # Small variants for the correctness tests (T08/T09): no scale needed there.
    "S-noenv": (LOD2_V3, 300, ["--strip-envelopes"]),
    "S-lod0": (LOD2_V3, 300, ["--with-lod0"]),
}


def default_source_dir() -> Path:
    env = os.environ.get("CGML_PERF_SOURCE_DIR")
    return Path(env) if env else Path.home() / "Desktop" / "blender_test_files" / "buildings"


def default_out_dir() -> Path:
    env = os.environ.get("CGML_PERF_DATA")
    return Path(env) if env else REPO.parent / "perf-data"


def set_path(out_dir: Path, name: str) -> Path:
    return out_dir / f"{name}.gml"


def main(argv=None):
    p = argparse.ArgumentParser(description="Regenerate the T03 performance sets")
    p.add_argument("--source-dir", type=Path, default=default_source_dir())
    p.add_argument("--out", type=Path, default=default_out_dir())
    p.add_argument("--only", nargs="+", choices=list(SETS), help="generate only these sets")
    a = p.parse_args(argv)

    out = a.out.resolve()
    if out == REPO or REPO in out.parents:
        p.error(f"--out must be outside the repository: {out}")
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")).get("sets", {})

    for name in a.only or SETS:
        src_name, count, options = SETS[name]
        src = a.source_dir / src_name
        if not src.is_file():
            p.error(f"source not found: {src}")
        stats_file = out / f".{name}.stats.json"
        cmd = [sys.executable, str(HERE / "make_tiled_citygml.py"), str(src),
               str(set_path(out, name)), "--count", str(count), "--stats-json", str(stats_file),
               *options]
        t0 = time.perf_counter()
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        stats = json.loads(stats_file.read_text(encoding="utf-8"))
        stats_file.unlink()
        stats["wall_seconds"] = round(time.perf_counter() - t0, 3)
        manifest[name] = stats
        print(f"{name:8s} {stats['features']:6d} features  {stats['bytes'] / 2**20:8.1f} MB  "
              f"{stats['seconds']:6.2f} s  peak RSS {stats['peak_rss_mb']:6.1f} MB")

    manifest_path.write_text(json.dumps({
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_dir": str(a.source_dir),
        "sets": manifest,
    }, indent=2), encoding="utf-8")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
