"""T03: compare two bench_import.py result files.

  python scripts/perf/compare_results.py BASE.json NEW.json [--markdown]

Prints one row per set and metric with the change from BASE to NEW. A set that
did not complete in one of the files is shown as "not completed".
"""
from __future__ import annotations

import argparse
import json

METRICS = (
    ("total_s", "total (s)"),
    ("longest_main_thread_block_s", "longest block (s)"),
    ("materials_s", "materials (s)"),
    ("peak_rss_mb", "peak RSS (MB)"),
    ("objects", "objects"),
)


def _fmt(value):
    if value is None:
        return "-"
    return f"{value:,.1f}" if isinstance(value, float) else f"{value:,}"


def _change(old, new):
    if old is None or new is None:
        return ""
    if old == 0:
        return "" if new == 0 else "new"
    pct = (new - old) / old * 100.0
    return f"{pct:+.1f}%"


def compare(base: dict, new: dict) -> list[tuple[str, str, str, str, str]]:
    rows = []
    names = list(base.get("sets", {}))
    names += [n for n in new.get("sets", {}) if n not in names]
    for name in names:
        b = base["sets"].get(name)
        n = new["sets"].get(name)
        b_ok = bool(b and b.get("completed"))
        n_ok = bool(n and n.get("completed"))
        if not (b_ok and n_ok):
            rows.append((name, "status",
                         "completed" if b_ok else ("not completed" if b else "missing"),
                         "completed" if n_ok else ("not completed" if n else "missing"), ""))
            if not (b_ok or n_ok):
                continue
        for key, label in METRICS:
            old = b.get(key) if b_ok else None
            cur = n.get(key) if n_ok else None
            rows.append((name, label, _fmt(old), _fmt(cur), _change(old, cur)))
    return rows


def render(rows, base_label, new_label, markdown=False) -> str:
    header = ("set", "metric", base_label, new_label, "change")
    if markdown:
        lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
        lines += ["| " + " | ".join(r) + " |" for r in rows]
        return "\n".join(lines)
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in [header, *rows])


def _label(report, path):
    return f"{report.get('branch', path)} ({report.get('commit', '?')})"


def main(argv=None):
    p = argparse.ArgumentParser(description="Compare two benchmark result files")
    p.add_argument("base")
    p.add_argument("new")
    p.add_argument("--markdown", action="store_true")
    a = p.parse_args(argv)
    with open(a.base, encoding="utf-8") as f:
        base = json.load(f)
    with open(a.new, encoding="utf-8") as f:
        new = json.load(f)
    print(render(compare(base, new), _label(base, a.base), _label(new, a.new), a.markdown))


if __name__ == "__main__":
    main()
