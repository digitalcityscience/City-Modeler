"""Rule R1 (CAC-P1.8): shared/authoring_core/domain/** never imports bpy,
bmesh, mathutils or gpu - in any form, at any nesting level."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOMAIN = ROOT / "shared" / "authoring_core" / "domain"
FORBIDDEN = {"bpy", "bmesh", "mathutils", "gpu"}


def forbidden_imports(source, filename="<source>"):
    """``[(line, module)]`` of forbidden imports anywhere in ``source``."""
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", ""))
              in ("import_module", "__import__") and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            names = [node.args[0].value]
        else:
            continue
        found += [(node.lineno, n) for n in names if n.split(".")[0] in FORBIDDEN]
    return found


def test_domain_package_exists():
    assert (DOMAIN / "__init__.py").is_file()


@pytest.mark.parametrize("path", sorted(DOMAIN.rglob("*.py")), ids=lambda p: p.name)
def test_domain_module_has_no_blender_imports(path):
    assert forbidden_imports(path.read_text(encoding="utf-8"), str(path)) == []


@pytest.mark.parametrize("planted", [
    "import bpy",
    "import bmesh, json",
    "from mathutils import Vector",
    "from gpu.types import GPUShader",
    "def f():\n    import bpy.types",
    "import importlib\nimportlib.import_module('bpy')",
    "__import__('bmesh')",
])
def test_guard_detects_planted_import(planted):
    assert forbidden_imports(planted)


@pytest.mark.parametrize("allowed", [
    "import json",
    "from .ids import mint_gml_id",
    "from . import bpy_names",          # relative, not the bpy module
    "import bpyutils",                  # different top-level name
])
def test_guard_allows_other_imports(allowed):
    assert forbidden_imports(allowed) == []
