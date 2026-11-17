# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# common/wheel_repair.py
"""Put back bundled wheel files that Blender's wheel sync half-deleted.

Why this exists (2026-11-04): ``addon_utils.disable()`` calls
``extensions_refresh()`` while the add-on is disabled, so Blender's wheel
manager removes this add-on's wheels. On Windows the running Blender holds
pyproj/lxml/psycopg DLLs open, so the removal stops halfway: package
folders vanish but ``*.dist-info`` stays, and the wheel manager treats a
wheel with a ``dist-info`` as installed and never reinstalls it. Result:
``No module named 'pyproj'`` until someone repairs it by hand.

``repair()`` runs at register: for every bundled wheel that fits this
Python/OS it extracts only the files missing from the extensions
site-packages (existing files, possibly locked, are never touched). No bpy
import here, so it is unit-testable.
"""

from __future__ import annotations

import os
import platform
import sys
import zipfile
from pathlib import Path


def _platform_ok(tag: str, system: str, machine: str) -> bool:
    if tag == "any":
        return True
    machine = machine.lower()
    if system == "Windows":
        return tag == ("win_amd64" if machine in ("amd64", "x86_64") else "win_arm64")
    if system == "Darwin":
        return tag.startswith("macosx") and (tag.endswith("universal2") or tag.endswith(machine))
    if system == "Linux":
        return "linux" in tag and tag.endswith(machine)
    return False


def compatible(filename: str, py: tuple = sys.version_info[:2],
               system: str = platform.system(), machine: str = platform.machine()) -> bool:
    """True when the wheel ``filename`` (name-ver-py-abi-plat.whl) fits this interpreter."""
    parts = filename[:-4].split("-") if filename.endswith(".whl") else []
    if len(parts) < 5:
        return False
    py_tag, plat_tags = parts[-3], parts[-1].split(".")
    py_ok = any(t in ("py3", f"cp{py[0]}{py[1]}") for t in py_tag.split("."))
    return py_ok and any(_platform_ok(t, system, machine) for t in plat_tags)


def repair(wheel_dir, site_packages, **tags) -> list:
    """Extract missing files of broken compatible wheels; return the wheel names repaired."""
    wheel_dir, site_packages = Path(wheel_dir), Path(site_packages)
    if not wheel_dir.is_dir() or not site_packages.is_dir():
        return []
    repaired = []
    for whl in sorted(wheel_dir.glob("*.whl")):
        if not compatible(whl.name, **tags):
            continue
        with zipfile.ZipFile(whl) as zf:
            # Every file, not just the top-level folder: before Blender's next
            # start the folder can still exist holding only the locked files.
            missing = [n for n in zf.namelist() if not n.endswith("/") and not (site_packages / n).exists()]
            for name in missing:
                target = site_packages / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        if missing:
            repaired.append(whl.name)
    return repaired


def extension_site_packages(extensions_root: str) -> str:
    """Blender's wheel target, as in ``addon_utils._extension_sync_wheels``."""
    return os.path.join(extensions_root, ".local", "lib",
                        "python{:d}.{:d}".format(*sys.version_info[:2]), "site-packages")
