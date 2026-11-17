"""B60 (agent plan A5): optional CityDoctor run (installed by the user, never bundled). No bpy.

The user points the add-on at CityDoctor (the ? button beside the CityDoctor button, or
Preferences; else the ``CITYDOCTOR_VALIDATE_SH`` environment variable). Two forms work:

- B86: the unzipped official release (``CityDoctorValidation-<version>-win|lin|mac.zip``): the
  folder, or the ``start.bat`` / ``start.sh`` in it. The add-on runs the Java in ``runtime/`` with
  the jars in ``app/`` itself, the same way on every OS; there is no wrapper script to write.
- a validator command: ``<validator> <file.gml>`` writes ``<file.gml>.citydoctor-report.xml``
  (the contract the headless tests use).

Without it the button is greyed out; nothing fails.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ENV = "CITYDOCTOR_VALIDATE_SH"
CD_NS = {"cd": "http://www.citydoctor.eu"}
MAIN = "de.hft.stuttgart.citydoctor2.CityDoctorValidation"
DOWNLOAD_URL = ("https://transfer.hft-stuttgart.de/gitlab/citydoctor/citydoctorreleases/-/tree/master/"
                "CityDoctorValidation")
# the add-on's own check configuration (the sample one in the zip validates with streaming)
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "citydoctor_config.yml")
# per OS: name, release zip suffix, start script in the zip, where to unzip (example)
SETUP = {
    "nt": ("Windows", "-win.zip", "start.bat", r"C:\CityDoctor"),
    "darwin": ("macOS", "-mac.zip", "start.sh", "~/CityDoctor"),
    "posix": ("Linux", "-lin.zip", "start.sh", "~/CityDoctor"),
}


def os_key() -> str:
    return "nt" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "posix"


def path_hint(key: str | None = None) -> str:
    """Example of the path to set on this OS."""
    key = key or os_key()
    _name, _suffix, script, folder = SETUP[key]
    return folder + ("\\" if key == "nt" else "/") + script


def setup_lines(key: str | None = None) -> list[str]:
    """The setup steps for this OS, one short line each (Blender popup, Preferences)."""
    key = key or os_key()
    _name, suffix, _script, folder = SETUP[key]
    lines = [
        f"1. Download CityDoctorValidation-<newest version>{suffix}",
        "    (Java is inside the zip, nothing else to install)",
        f"2. Unzip it, for example to {folder}",
        f"3. Set the path below to {path_hint(key)}",
    ]
    if key == "darwin":
        lines.append(f"If macOS blocks it: xattr -dr com.apple.quarantine {folder}")
    return lines


def release_dir(path: str) -> str | None:
    """The folder of an unzipped CityDoctorValidation release when ``path`` is that folder or its
    start script, else None (``path`` is then a validator command)."""
    path = str(path or "")
    if os.path.isdir(path):
        folder = path
    elif os.path.basename(path).lower() in ("start.bat", "start.sh"):
        folder = os.path.dirname(path)
    else:
        return None
    return folder if glob.glob(os.path.join(folder, "app", "*.jar")) else None


def validator_path(pref: str = "") -> str | None:
    """The validator to run (release folder, its start script or a command), or ``None``."""
    for cand in (pref, os.environ.get(ENV, "")):
        cand = os.path.expandvars(os.path.expanduser(str(cand or "").strip()))
        if cand and (os.path.isfile(cand) or release_dir(cand)):
            return cand
    return None


def command(validator: str, gml_path: str):
    """``(argv, working directory)`` that validates ``gml_path`` and writes ``report_path``."""
    folder = release_dir(validator)
    if folder is None:
        return [validator, gml_path], None
    exe = "java.exe" if os.name == "nt" else "java"
    java = next((p for p in (os.path.join(folder, "runtime", "bin", exe),
                             os.path.join(folder, "runtime", "Contents", "Home", "bin", exe))
                 if os.path.isfile(p)), "java")  # the no-runtime zip: Java 17+ from PATH
    if java != "java" and not os.access(java, os.X_OK):
        try:  # unzip tools drop the executable bit on Linux and macOS
            os.chmod(java, os.stat(java).st_mode | 0o111)
        except OSError:
            pass
    own = os.path.join(folder, "config.yml")
    return [java, "-classpath", os.pathsep.join(("app/*", "plugin/*")), MAIN, "-in", gml_path,
            "-config", own if os.path.isfile(own) else CONFIG, "-xmlReport", report_path(gml_path),
            "-db_location", os.path.join(tempfile.gettempdir(), "citydoctor") + os.sep], folder


def report_path(gml_path: str) -> str:
    return str(gml_path) + ".citydoctor-report.xml"


def run(validator: str, gml_path: str, timeout: float = 900.0) -> str:
    """Validate ``gml_path``; returns the report path. ``RuntimeError`` when no report appears."""
    rep = report_path(gml_path)
    if os.path.exists(rep):
        os.remove(rep)
    argv, cwd = command(validator, gml_path)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(f"Could not start {argv[0]}: use the CityDoctor zip made for this system "
                           "(Java is inside) or install Java 17+")
    if not os.path.isfile(rep):
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"CityDoctor wrote no report (exit {proc.returncode}): " + " | ".join(tail))
    return rep


def _ns(root):
    tag = root.tag
    return {"cd": tag[1:tag.index("}")]} if tag.startswith("{") else CD_NS


def parse_report(path: str) -> dict:
    """``{"global": {code: n}, "buildings": {gml_id: {code: n}}}`` of a CityDoctor XML report.
    Buildings without errors are listed with ``{}``."""
    root = ET.parse(path).getroot()
    ns = _ns(root)
    glob = {e.get("name"): int(e.text or 0)
            for e in root.findall("./cd:global_statistics/cd:errors/cd:error", ns)}
    buildings = {}
    for rep in root.iter(f"{{{ns['cd']}}}building_report"):
        stats = rep.find("./cd:statistics/cd:error_statistics", ns)
        buildings[rep.get("gml_id")] = {e.get("name"): int(e.text or 0)
                                        for e in (stats if stats is not None else [])}
    return {"global": glob, "buildings": buildings}
