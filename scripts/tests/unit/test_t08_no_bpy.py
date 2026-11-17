"""T08: modules that run in the browse worker thread never import bpy,
bmesh, mathutils or gpu - directly or through a relative import."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = {"bpy", "bmesh", "mathutils", "gpu", "gpu_extras", "bpy_extras"}
WORKER_MODULES = sorted((ROOT / "shared" / "browse").glob("*.py")) + [
    ROOT / "shared" / "citygml_sniff.py",
    ROOT / "shared" / "import_origin.py",
    # T09: DB browse worker (catalog queries, citydb-tool arguments)
    ROOT / "citydb" / "catalog.py",
    ROOT / "citydb" / "client.py",
]


def _imports(path: Path):
    """``(absolute names, resolved relative module paths)`` of one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    names, relative = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module)
            elif node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                parts = node.module.split(".") if node.module else []
                targets = [base.joinpath(*parts)]
                if not node.module:
                    targets = [base / a.name for a in node.names]
                else:
                    targets += [base.joinpath(*parts, a.name) for a in node.names]
                for t in targets:
                    for cand in (t.with_suffix(".py"), t / "__init__.py"):
                        if cand.is_file():
                            relative.append(cand.resolve())
        elif (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", ""))
              in ("import_module", "__import__") and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            names.append(node.args[0].value)
    return names, relative


def forbidden_in_graph(start: Path):
    """Forbidden imports reachable from ``start`` as ``[(file, module)]``."""
    seen, todo, bad = set(), [start.resolve()], []
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        names, rel = _imports(p)
        bad += [(p.name, n) for n in names if n.split(".")[0] in FORBIDDEN]
        todo += rel
    return bad


@pytest.mark.parametrize("path", WORKER_MODULES, ids=lambda p: p.name)
def test_worker_module_graph_has_no_blender_imports(path):
    assert forbidden_in_graph(path) == []


def test_guard_sees_relative_imports(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from .b import thing\n")
    (pkg / "b.py").write_text("def f():\n    import bpy\nthing = 1\n")
    assert forbidden_in_graph(pkg / "a.py") == [("b.py", "bpy")]


def test_bpy_side_modules_are_not_in_the_worker_set():
    names = {p.name for p in WORKER_MODULES}
    assert "browse_gml.py" not in names and "background_job.py" not in names
    assert {"gml_index.py", "fragment.py", "index_cache.py", "browse_worker.py",
            "proxy_geometry.py", "selection.py"} <= names
