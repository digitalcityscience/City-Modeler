"""
Version bump utility for the Blender add-on.

Reads the current version from version.json and increments it
based on the branch context:
  - release-v* branches: bump PATCH
  - master branch: bump rc suffix (e.g. 0.9.0-rc.1 -> 0.9.0-rc.2)

Usage:
    python scripts/version_bump.py [--patch | --minor | --major | --rc]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

VERSION_JSON = Path(__file__).parent.parent / 'version.json'
MANIFEST_FILE = Path(__file__).parent.parent / 'blender_manifest.toml'
MANIFEST_VERSION_PATTERN = re.compile(
    r'(^version\s*=\s*")([^"]+)(")',
    re.MULTILINE,
)


def _manifest_version():
    """Read the version declared in blender_manifest.toml."""
    match = MANIFEST_VERSION_PATTERN.search(MANIFEST_FILE.read_text(encoding='utf-8'))
    if not match:
        print("ERROR: Could not find version field in blender_manifest.toml")
        sys.exit(1)
    return match.group(2)


def get_current_version():
    """Read current version from version.json (primary) or blender_manifest.toml (fallback)."""
    if VERSION_JSON.exists():
        data = json.loads(VERSION_JSON.read_text(encoding='utf-8'))
        return f"{data['major']}.{data['minor']}.{data['patch']}"
    return _manifest_version()


def set_version(major, minor, patch):
    """Write new version to version.json and the Blender manifest."""
    version = f"{major}.{minor}.{patch}"
    manifest_text, manifest_replacements = MANIFEST_VERSION_PATTERN.subn(
        f'\\g<1>{version}\\g<3>',
        MANIFEST_FILE.read_text(encoding='utf-8'),
        count=1,
    )
    if manifest_replacements != 1:
        print("ERROR: Could not find version field in blender_manifest.toml")
        sys.exit(1)

    VERSION_JSON.write_text(
        json.dumps({"major": major, "minor": minor, "patch": patch}, indent=2) + '\n',
        encoding='utf-8'
    )
    MANIFEST_FILE.write_text(manifest_text, encoding='utf-8')
    print(f"Version set to {version}")


def detect_bump_type():
    """Auto-detect bump type from CI_COMMIT_BRANCH."""
    branch = os.environ.get('CI_COMMIT_BRANCH', '')
    if branch.startswith('release-v'):
        return 'patch'
    return 'patch'  # Default: patch bump


def main():
    parser = argparse.ArgumentParser(description='Bump add-on version')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--major', action='store_true')
    group.add_argument('--minor', action='store_true')
    group.add_argument('--patch', action='store_true')
    args = parser.parse_args()

    version = get_current_version()
    parts = version.split('.')
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if args.major:
        major += 1
        minor = 0
        patch = 0
    elif args.minor:
        minor += 1
        patch = 0
    elif args.patch:
        patch += 1
    else:
        bump_type = detect_bump_type()
        if bump_type == 'patch':
            patch += 1
        elif bump_type == 'minor':
            minor += 1
            patch = 0

    set_version(major, minor, patch)
    return f"{major}.{minor}.{patch}"


if __name__ == '__main__':
    main()
