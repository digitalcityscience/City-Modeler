"""T03: benchmark helpers that run without Blender (phase timer, comparison, sets)."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERF = ROOT / "scripts" / "perf"


def _load(name):
    if str(PERF) not in sys.path:
        sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location(f"t03_{name}", PERF / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load("bench_import")
compare = _load("compare_results")
sets = _load("make_perf_sets")
procmem = _load("procmem")


def test_phase_timer_wraps_and_restores(monkeypatch):
    import types
    builder = types.ModuleType("fake_addon.builder")
    builder.read = lambda: "r"
    builder.build = lambda: "b"
    builder.paint = lambda: "p"
    monkeypatch.setitem(sys.modules, "fake_addon.builder", builder)
    originals = (builder.read, builder.build, builder.paint)
    times = iter([0.0, 1.0, 1.0, 4.0, 2.0, 2.5])
    monkeypatch.setattr(bench.time, "perf_counter", lambda: next(times))
    timer = bench._PhaseTimer("fake_addon", (("builder", "read", "read"), ("builder", "build", "build"),
                                             ("builder", "paint", "materials")))
    assert (builder.read(), builder.build(), builder.paint()) == ("r", "b", "p")
    assert timer.phases(5.0) == {"read": 1.0, "build": 3.0, "other": 1.0}
    assert timer.seconds["materials"] == 0.5 and timer.calls == {"read": 1, "build": 1, "materials": 1}
    timer.restore()
    assert (builder.read, builder.build, builder.paint) == originals


def _report(**sets_):
    return {"branch": "b", "commit": "c", "sets": sets_}


def test_compare_rows_and_incomplete_sets():
    base = _report(S={"completed": True, "total_s": 10.0, "longest_main_thread_block_s": 10.0,
                      "materials_s": 2.0, "peak_rss_mb": 1000.0, "objects": 4200},
                   L={"completed": False, "reason": "time limit"})
    new = _report(S={"completed": True, "total_s": 5.0, "longest_main_thread_block_s": 0.1,
                     "materials_s": 2.0, "peak_rss_mb": 1100.0, "objects": 4200},
                  L={"completed": True, "total_s": 600.0, "longest_main_thread_block_s": 0.2,
                     "materials_s": 1.0, "peak_rss_mb": 9000.0, "objects": 280000})
    rows = compare.compare(base, new)
    by = {(r[0], r[1]): r for r in rows}
    assert by[("S", "total (s)")][2:] == ("10.0", "5.0", "-50.0%")
    assert by[("S", "peak RSS (MB)")][4] == "+10.0%"
    assert by[("S", "objects")][2:] == ("4,200", "4,200", "+0.0%")
    assert by[("L", "status")][2:4] == ("not completed", "completed")
    assert by[("L", "total (s)")][2:] == ("-", "600.0", "")
    md = compare.render(rows, "base", "new", markdown=True)
    assert md.splitlines()[0] == "| set | metric | base | new | change |"


def test_compare_cli(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_report(S={"completed": True, "total_s": 1.0, "objects": 1})))
    b.write_text(json.dumps(_report(S={"completed": True, "total_s": 2.0, "objects": 1})))
    compare.main([str(a), str(b)])
    assert "+100.0%" in capsys.readouterr().out


def test_standard_sets_definition():
    assert {n: (s[1], tuple(s[2])) for n, s in sets.SETS.items()} == {
        "S": (300, ()), "M": (5000, ()), "L": (20000, ()), "M-lod3": (5000, ()),
        "M-noenv": (5000, ("--strip-envelopes",)), "M-lod0": (5000, ("--with-lod0",)),
        "M-gml2": (5000, ()),
        "S-noenv": (300, ("--strip-envelopes",)), "S-lod0": (300, ("--with-lod0",)),
    }
    assert sets.SETS["M-lod3"][0] == "buildings_lod3_citygml_3_0.gml"
    assert sets.SETS["M-gml2"][0] == "buildings_lod2_citygml_2_0.gml"


def test_sets_refuse_output_inside_repo(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        sets.main(["--source-dir", str(tmp_path), "--out", str(ROOT / "perf-data")])


def test_peak_rss_is_positive():
    assert procmem.peak_rss_bytes() > 0
