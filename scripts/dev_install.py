#!/usr/bin/env python3
"""
Install this checkout as a Blender extension into every local Blender >= 5.0
install found on the current machine (Windows, macOS or Linux).

It builds the extension zip from the working tree (blender_manifest.toml)
and installs it via Blender's own `extension` CLI, so the result is exactly
what a normal user-facing install produces - no ad-hoc symlinks or manual
folder copies, and it works with whatever Blender resource layout the local
OS uses.

Usage:
    python scripts/dev_install.py                # find + install everywhere (copy, via zip)
    python scripts/dev_install.py --list          # just show what was found
    python scripts/dev_install.py --blender PATH  # add an extra blender exe
    python scripts/dev_install.py --skip-fetch-deps
    python scripts/dev_install.py --link          # link instead of copy (see below)

Wire the copy mode into git hooks (see .githooks/) for it to re-run
automatically after `git clone`/`git pull`:

    git config core.hooksPath .githooks

--link: live-edit mode
    Instead of building a zip and installing a copy, this creates a symlink
    (junction on Windows) at ~/blender-dev-repos/city_modeler
    pointing at this checkout, so file edits are visible to Blender
    immediately - no rebuild/reinstall step. One-time manual setup is still
    needed in Blender itself:

      Preferences > Get Extensions > Repositories > + > Add Local Repository
      Custom Directory: ~/blender-dev-repos
      then enable "VCS 3DCityDB Importer/Exporter" from that repository.

    After editing files, disable and re-enable the extension to pick up the
    change (F3 "Reload Scripts" does NOT reliably reload this addon's
    submodules). On Windows, uninstalling the extension from Blender's UI
    can delete the symlink TARGET (this checkout) - only ever disable it,
    never uninstall, while using --link.
"""
import argparse
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION_ID = "city_modeler"
MIN_VERSION = (5, 0)


def find_candidate_executables():
    candidates = []

    env_extra = os.environ.get("BLENDER_EXECUTABLES", "")
    if env_extra:
        candidates.extend(p for p in env_extra.split(os.pathsep) if p)

    on_path = shutil.which("blender")
    if on_path:
        candidates.append(on_path)

    system = platform.system()
    if system == "Windows":
        candidates.extend(glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe"))
        candidates.extend(glob.glob(r"C:\Program Files (x86)\Blender Foundation\Blender*\blender.exe"))
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.extend(glob.glob(os.path.join(local, "Programs", "Blender Foundation", "Blender*", "blender.exe")))
    elif system == "Darwin":
        candidates.extend(glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender"))
        home = str(Path.home())
        candidates.extend(glob.glob(os.path.join(home, "Applications", "Blender*.app", "Contents", "MacOS", "Blender")))
    else:
        candidates.extend(glob.glob("/opt/blender*/blender"))
        candidates.extend(glob.glob("/usr/local/blender*/blender"))
        candidates.append("/snap/bin/blender")
        candidates.append("/usr/bin/blender")
        candidates.append("/usr/local/bin/blender")

    seen = set()
    result = []
    for c in candidates:
        p = Path(c)
        if not p.exists():
            continue
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        result.append(rp)
    return result


def get_version(executable):
    try:
        out = subprocess.run(
            [executable, "--version"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return None
    m = re.search(r"Blender\s+(\d+)\.(\d+)", out)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def ensure_wheels(skip_fetch_deps):
    wheels_dir = REPO_ROOT / "wheels"
    if wheels_dir.is_dir() and any(wheels_dir.iterdir()):
        return True
    if skip_fetch_deps:
        print(f"warning: {wheels_dir} is empty and --skip-fetch-deps was given; "
              "the build may fail if the manifest's wheels are missing.")
        return True
    print("wheels/ is empty; fetching python wheels via scripts/fetch_deps.py ...")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "fetch_deps.py"), "--python-only"],
        cwd=str(REPO_ROOT),
    )
    return result.returncode == 0


def install_into(executable, version):
    label = f"Blender {version[0]}.{version[1]} ({executable})"
    with tempfile.TemporaryDirectory(prefix="citygml_dev_install_") as tmp:
        zip_path = os.path.join(tmp, f"{EXTENSION_ID}.zip")
        build = subprocess.run(
            [executable, "--command", "extension", "build",
             "--source-dir", str(REPO_ROOT), "--output-filepath", zip_path],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            print(f"[FAIL] {label}: build failed\n{build.stdout}\n{build.stderr}")
            return False

        def run_install():
            return subprocess.run(
                [executable, "--command", "extension", "install-file",
                 "--repo", "user_default", "--enable", zip_path],
                capture_output=True, text=True,
            )

        install = run_install()
        if install.returncode != 0:
            # Most likely already installed - remove the old copy and retry once.
            subprocess.run(
                [executable, "--command", "extension", "remove",
                 "--repo", "user_default", EXTENSION_ID],
                capture_output=True, text=True,
            )
            install = run_install()
        if install.returncode != 0:
            print(f"[FAIL] {label}: install failed\n{install.stdout}\n{install.stderr}")
            return False

    print(f"[OK]   {label}")
    return True


def create_link():
    local_repo = Path.home() / "blender-dev-repos"
    dest = local_repo / EXTENSION_ID
    local_repo.mkdir(parents=True, exist_ok=True)

    if dest.is_symlink() or dest.exists():
        if dest.resolve() == REPO_ROOT.resolve():
            print(f"already linked: {dest} -> {REPO_ROOT}")
            return True
        print(f"error: {dest} already exists and isn't linked to this checkout; "
              "remove it by hand first if you want to relink.")
        return False

    if platform.system() == "Windows":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(REPO_ROOT)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"error: junction failed\n{result.stdout}\n{result.stderr}")
            return False
    else:
        os.symlink(REPO_ROOT, dest)

    print(f"linked: {dest} -> {REPO_ROOT}")
    print(f"\nOne-time Blender setup: Preferences > Get Extensions > Repositories > +\n"
          f"> Add Local Repository, Custom Directory = {local_repo}, then enable\n"
          f"\"VCS 3DCityDB Importer/Exporter\" from that repository.\n"
          f"After edits: disable/re-enable the extension (not F3 Reload Scripts).\n"
          f"Never uninstall it from Blender's UI while linked - on Windows that can\n"
          f"delete this checkout.")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="only show detected Blender installs")
    parser.add_argument("--blender", action="append", default=[], help="extra blender executable path (repeatable)")
    parser.add_argument("--skip-fetch-deps", action="store_true")
    parser.add_argument("--link", action="store_true", help="symlink into a local repo instead of build+install a copy")
    args = parser.parse_args()

    if args.link:
        if not ensure_wheels(args.skip_fetch_deps):
            print("failed to fetch python wheels; aborting.")
            return 1
        return 0 if create_link() else 1

    executables = find_candidate_executables() + [str(Path(p)) for p in args.blender]
    versioned = []
    for exe in executables:
        v = get_version(exe)
        if v is None:
            print(f"skip (unreadable version): {exe}")
            continue
        if v < MIN_VERSION:
            print(f"skip ({v[0]}.{v[1]} < {MIN_VERSION[0]}.{MIN_VERSION[1]}): {exe}")
            continue
        versioned.append((exe, v))

    if not versioned:
        print("No Blender >= 5.0 installation found on this machine.")
        print("Pass --blender <path-to-blender> if it isn't on PATH or in a standard location.")
        return 1

    if args.list:
        for exe, v in versioned:
            print(f"{v[0]}.{v[1]}\t{exe}")
        return 0

    if not ensure_wheels(args.skip_fetch_deps):
        print("failed to fetch python wheels; aborting.")
        return 1

    ok = True
    for exe, v in versioned:
        ok = install_into(exe, v) and ok

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
