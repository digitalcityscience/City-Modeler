"""
Fetch external dependencies defined in dependencies.yaml.

Usage:
    python scripts/fetch_deps.py [--manifest dependencies.yaml] [--output-dir .]
                                 [--tools-only | --python-only]

Environment variables:
    CI_JOB_TOKEN        - GitLab CI job token for authenticated downloads
    GITLAB_TOKEN        - Fallback personal/project token for local use
"""

import argparse
import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    try:
        # pip might have vendored yaml
        from pip._vendor import yaml  # type: ignore
    except ImportError:
        yaml = None


# ---------------------------------------------------------------------------
# Minimal YAML parser (subset) when PyYAML is unavailable
# ---------------------------------------------------------------------------

def _parse_yaml_minimal(text):
    """
    Minimal recursive-descent YAML parser supporting:
      - nested dicts, lists, scalars
      - list items with '- key: val' or '- scalar'
      - comments and blank lines
    """
    lines = text.splitlines()
    # Pre-process: remove comments and blank lines, track indents
    entries = []
    for raw in lines:
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith('#'):
            continue
        indent = len(stripped) - len(stripped.lstrip())
        content = stripped.lstrip()
        entries.append((indent, content))

    def _parse_value(s):
        s = s.strip()
        if not s:
            return None
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1]
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1]
        if s.isdigit():
            return int(s)
        try:
            return float(s)
        except ValueError:
            pass
        return s

    def _parse_block(pos, base_indent):
        """Parse a block at a given indentation level. Returns (result, next_pos)."""
        if pos >= len(entries):
            return None, pos

        indent, content = entries[pos]

        # Determine if this block is a list or a dict
        if content.startswith('- '):
            return _parse_list(pos, base_indent)
        else:
            return _parse_dict(pos, base_indent)

    def _parse_list(pos, base_indent):
        result = []
        while pos < len(entries):
            indent, content = entries[pos]
            if indent < base_indent:
                break
            if indent > base_indent:
                break
            if not content.startswith('- '):
                break

            item_text = content[2:].strip()
            if ':' in item_text and not item_text.startswith('http'):
                # Check if colon is part of a key:value or inside a URL
                colon_idx = item_text.index(':')
                key_part = item_text[:colon_idx]
                if ' ' not in key_part and '/' not in key_part:
                    # Dict item in list: parse as inline + continuation
                    obj = {}
                    k, v = item_text.split(':', 1)
                    k = k.strip()
                    v = v.strip()
                    if v:
                        obj[k] = _parse_value(v)
                    else:
                        pos += 1
                        child, pos = _parse_block(pos, indent + 2)
                        obj[k] = child
                        result.append(obj)
                        continue
                    pos += 1
                    # Continuation keys at deeper indent
                    while pos < len(entries):
                        ni, nc = entries[pos]
                        if ni <= indent:
                            break
                        if nc.startswith('- '):
                            break
                        if ':' in nc:
                            ck, cv = nc.split(':', 1)
                            obj[ck.strip()] = _parse_value(cv.strip())
                        pos += 1
                    result.append(obj)
                    continue

            # Simple scalar list item
            result.append(_parse_value(item_text))
            pos += 1

        return result, pos

    def _parse_dict(pos, base_indent):
        result = {}
        while pos < len(entries):
            indent, content = entries[pos]
            if indent < base_indent:
                break
            if indent > base_indent:
                # Shouldn't happen at top-level call, skip
                pos += 1
                continue
            if content.startswith('- '):
                break

            if ':' not in content:
                pos += 1
                continue

            # Split on first colon (but be careful with URLs)
            colon_idx = content.index(':')
            key = content[:colon_idx].strip()
            val_str = content[colon_idx + 1:].strip()

            if val_str:
                result[key] = _parse_value(val_str)
                pos += 1
            else:
                # Value is a nested block
                pos += 1
                if pos < len(entries):
                    next_indent = entries[pos][0]
                    if next_indent > indent:
                        child, pos = _parse_block(pos, next_indent)
                        result[key] = child
                    else:
                        result[key] = None
                else:
                    result[key] = None

        return result, pos

    result, _ = _parse_block(0, 0)
    return result if result else {}


def load_manifest(path):
    """Load and parse the dependencies.yaml manifest."""
    text = Path(path).read_text(encoding='utf-8')
    if yaml:
        return yaml.safe_load(text)
    return _parse_yaml_minimal(text)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _get_auth_token(auth_type=None):
    """Retrieve authentication token from environment.
    
    Auth types:
      - gitlab_job_token: uses GITLAB_TOKEN or CI_JOB_TOKEN
    """
    return os.environ.get('GITLAB_TOKEN') or os.environ.get('CI_JOB_TOKEN', '')


def _download(url, auth=None, dest_path=None):
    """Download a URL, optionally with GitLab token auth. Returns bytes or writes to dest_path."""
    headers = {}
    if auth and auth != 'none':
        token = _get_auth_token(auth)
        if token:
            print(f"  Using auth: {auth} ({len(token)} chars)")
            if os.environ.get('GITLAB_TOKEN'):
                headers['PRIVATE-TOKEN'] = token
            else:
                headers['JOB-TOKEN'] = token
        else:
            env_var = 'GITLAB_TOKEN'
            print(f"  ERROR: No {env_var} available!")
            print(f"  Ensure {env_var} CI variable is set and NOT marked as 'Protected'")
            print(f"  (unless this branch is a protected branch).")

    req = urllib.request.Request(url, headers=headers)
    print(f"  Downloading: {url}")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                raise RuntimeError(
                    f"Received HTML instead of binary data from {url}. "
                    f"This usually means authentication failed. "
                    f"Set GITLAB_TOKEN env var or ensure CI_JOB_TOKEN is available."
                )
            if dest_path:
                with open(dest_path, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                return dest_path
            return resp.read()
    except urllib.error.HTTPError as e:
        print(f"  ERROR: HTTP {e.code} for {url}")
        raise


def _extract_zip(data_or_path, target_dir, strip_components=0, source_subpath=None):
    """Extract a zip archive to target_dir.
    
    Args:
        source_subpath: If set, only extract members whose path (after stripping
                        the top-level folder) starts with this subpath. The subpath
                        itself is also stripped from the output.
    """
    if isinstance(data_or_path, (str, Path)):
        zf = zipfile.ZipFile(data_or_path)
    else:
        zf = zipfile.ZipFile(io.BytesIO(data_or_path))

    with zf:
        members = zf.namelist()
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)

        for member in members:
            if member.endswith('/'):
                continue
            parts = Path(member).parts

            # Apply source_subpath filter: match members containing the subpath segment
            if source_subpath:
                # Find the subpath segment in the member parts
                subpath_parts = Path(source_subpath).parts
                found = False
                for idx in range(len(parts) - len(subpath_parts)):
                    if parts[idx:idx + len(subpath_parts)] == subpath_parts:
                        # Strip everything up to and including the subpath
                        remaining = parts[idx + len(subpath_parts):]
                        if remaining:
                            rel = Path(*remaining)
                            found = True
                        break
                if not found:
                    continue
            elif strip_components and len(parts) > strip_components:
                rel = Path(*parts[strip_components:])
            elif strip_components:
                continue
            else:
                rel = Path(member)

            out_path = target / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(out_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)


# ---------------------------------------------------------------------------
# Version marker for incremental downloads
# ---------------------------------------------------------------------------

def _version_file(target_dir):
    return Path(target_dir) / '.dep_version'


def _is_current(target_dir, version):
    vf = _version_file(target_dir)
    if vf.exists():
        return vf.read_text(encoding='utf-8').strip() == version
    return False


def _mark_version(target_dir, version):
    vf = _version_file(target_dir)
    vf.write_text(version, encoding='utf-8')


# ---------------------------------------------------------------------------
# Tool fetching
# ---------------------------------------------------------------------------

def fetch_tools(manifest, output_dir):
    """Download and extract all tool dependencies."""
    tools = manifest.get('tools', {})
    for name, spec in tools.items():
        version = str(spec['version'])
        url = spec['url'].format(version=version)
        target_dir_template = spec.get('target_dir', name)
        target_dir = Path(output_dir) / target_dir_template.format(version=version)
        strip = int(spec.get('strip_components', 0))
        source_subpath = spec.get('source_subpath')
        auth = spec.get('auth')
        preserve = spec.get('preserve', [])
        if isinstance(preserve, str):
            preserve = [preserve]

        print(f"\n[TOOL] {name} v{version}")

        if _is_current(target_dir, version):
            print(f"  Already up-to-date at {target_dir}")
            continue

        # Backup preserved files
        preserved_files = {}
        for fname in preserve:
            fpath = target_dir / fname
            if fpath.exists():
                preserved_files[fname] = fpath.read_bytes()

        # Clean existing directory
        if target_dir.exists():
            print(f"  Removing outdated {target_dir}")
            shutil.rmtree(target_dir)

        # Download
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            _download(url, auth=auth, dest_path=tmp_path)
            print(f"  Extracting to {target_dir}")
            _extract_zip(tmp_path, target_dir, strip_components=strip,
                         source_subpath=source_subpath)
            _mark_version(target_dir, version)

            # Restore preserved files
            for fname, data in preserved_files.items():
                fpath = target_dir / fname
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(data)
                print(f"  Preserved: {fname}")

            print(f"  Done.")
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Python module fetching
# ---------------------------------------------------------------------------

WHEELS_DIR_NAME = 'wheels'

MANIFEST_WHEELS_PATTERN = re.compile(r'^wheels\s*=\s*\[[^\]]*\]', re.MULTILINE)


class WheelFetchError(RuntimeError):
    """Raised when a declared (module, python version, platform) wheel can't be fetched intact."""


def fetch_python_modules(manifest, output_dir):
    """Download one intact .whl per (module, python version, platform) into wheels/.

    Each declared combination is downloaded with its own platform-constrained
    `pip download` call. A failure for any single combination fails the whole
    build loudly (raises WheelFetchError) rather than silently falling back to
    an unconstrained download that would resolve to the CI runner's platform.

    Returns the filenames of the wheels written, for `update_manifest_wheels`.
    """
    modules = manifest.get('python_modules', [])
    py_config = manifest.get('python', {})
    py_versions = py_config.get('versions', ['3.11'])
    py_platforms = py_config.get('platforms', ['win_amd64'])

    if not modules:
        print("\n[PYTHON] No modules defined.")
        return []

    wheels_dir = Path(output_dir) / WHEELS_DIR_NAME
    wheels_dir.mkdir(parents=True, exist_ok=True)

    # Wheels from a previously declared version would otherwise linger here and
    # be picked up as if they were still declared.
    for stale in wheels_dir.glob('*.whl'):
        stale.unlink()

    fetched = []
    # Pure-Python modules (`pure: true`) ship one py3-none-any wheel that covers every
    # declared interpreter and platform, so it is downloaded once.
    for mod in modules:
        if _is_pure(mod):
            fetched.append(
                _fetch_one_wheel(mod['name'], str(mod['version']), py_versions[0], py_platforms[0], wheels_dir)
            )
    for py_ver in py_versions:
        for plat in py_platforms:
            for mod in modules:
                if _is_pure(mod):
                    continue
                fetched.append(
                    _fetch_one_wheel(mod['name'], str(mod['version']), py_ver, plat, wheels_dir)
                )
    return sorted(set(fetched))


def _is_pure(mod):
    return str(mod.get('pure', '')).strip().lower() in ('true', 'yes', '1')


_MANYLINUX_PLATFORM = re.compile(r'^manylinux_2_(\d+)_(.+)$')
# Oldest glibc a manylinux_2_x wheel may target and still be offered for a newer one.
_OLDEST_MANYLINUX_GLIBC_MINOR = 17


def _compatible_platforms(plat):
    """Platform tags pip may answer `plat` with, newest first.

    pip treats every `--platform` value literally. A manylinux_2_28 target also runs
    wheels built for older glibc (psycopg-binary only publishes manylinux_2_17), so
    those tags are offered as well. macOS needs no expansion: pip already accepts
    older deployment targets for a macosx_* platform.
    """
    match = _MANYLINUX_PLATFORM.match(plat)
    if not match:
        return [plat]
    minor, arch = int(match.group(1)), match.group(2)
    tags = [f"manylinux_2_{m}_{arch}" for m in range(minor, _OLDEST_MANYLINUX_GLIBC_MINOR - 1, -1)]
    if minor >= _OLDEST_MANYLINUX_GLIBC_MINOR:
        tags.append(f"manylinux2014_{arch}")
    return tags


def update_manifest_wheels(manifest_path, wheel_filenames):
    """Rewrite blender_manifest.toml's `wheels` array to list exactly wheel_filenames.

    The array is generated rather than hand-maintained because a wheel's filename
    cannot be derived from the platform requested for it: pip answers
    `--platform macosx_14_0_arm64` with a `macosx_10_13_universal2` wheel, and
    `--platform manylinux_2_28_x86_64` with a `manylinux_2_26_x86_64.manylinux_2_28_x86_64` one.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise WheelFetchError(f"blender_manifest.toml not found: {manifest_path}")

    entries = ''.join(
        f'  "./{WHEELS_DIR_NAME}/{name}",\n' for name in sorted(wheel_filenames)
    )
    replacement = f"wheels = [\n{entries}]" if entries else "wheels = []"

    text = manifest_path.read_text(encoding='utf-8')
    new_text, count = MANIFEST_WHEELS_PATTERN.subn(lambda _match: replacement, text, count=1)
    if count != 1:
        raise WheelFetchError(
            f"Could not find a `wheels = [...]` array to update in {manifest_path}"
        )

    manifest_path.write_text(new_text, encoding='utf-8')
    print(f"\n[MANIFEST] {manifest_path.name}: listed {len(wheel_filenames)} wheel(s)")


def _fetch_one_wheel(mod_name, mod_version, py_ver, plat, wheels_dir):
    """Download exactly one intact wheel for (mod_name, mod_version, py_ver, plat) into wheels_dir.

    Returns the resolved wheel's filename, whose tags may differ from the ones requested.
    """
    pkg_spec = f"{mod_name}=={mod_version}"
    print(f"\n[PYTHON] {pkg_spec} (python {py_ver}, {plat})")

    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [
            sys.executable, '-m', 'pip', 'download',
            '--no-deps',
            '--only-binary=:all:',
            '--python-version', py_ver,
        ]
        for compatible in _compatible_platforms(plat):
            cmd += ['--platform', compatible]
        cmd += ['--dest', tmp_dir, pkg_spec]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise WheelFetchError(
                f"pip download failed for {mod_name}=={mod_version} "
                f"(python {py_ver}, platform {plat}):\n{result.stderr.strip()}"
            )

        downloaded = sorted(Path(tmp_dir).glob('*.whl'))
        if len(downloaded) != 1:
            raise WheelFetchError(
                f"pip download for {mod_name}=={mod_version} "
                f"(python {py_ver}, platform {plat}) produced {len(downloaded)} wheel file(s), "
                f"expected exactly 1: {[p.name for p in downloaded]}"
            )

        whl = downloaded[0]
        dest_path = wheels_dir / whl.name
        shutil.move(str(whl), str(dest_path))
        print(f"    -> {WHEELS_DIR_NAME}/{whl.name}")
        return whl.name


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Fetch external dependencies')
    parser.add_argument('--manifest', default='dependencies.yaml',
                        help='Path to dependencies.yaml')
    parser.add_argument('--output-dir', default='.',
                        help='Base output directory')
    parser.add_argument('--tools-only', action='store_true',
                        help='Only fetch tool dependencies')
    parser.add_argument('--python-only', action='store_true',
                        help='Only fetch Python modules')
    args = parser.parse_args()

    manifest_path = Path(args.output_dir) / args.manifest
    if not manifest_path.exists():
        # Try relative to script
        manifest_path = Path(__file__).parent.parent / args.manifest
    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {args.manifest}")
        sys.exit(1)

    manifest = load_manifest(manifest_path)
    output_dir = args.output_dir

    try:
        if not args.python_only:
            fetch_tools(manifest, output_dir)

        if not args.tools_only:
            wheel_filenames = fetch_python_modules(manifest, output_dir)
            # Beside the wheels, since the manifest references them relatively.
            update_manifest_wheels(Path(output_dir) / 'blender_manifest.toml', wheel_filenames)
    except WheelFetchError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    print("\n✓ All dependencies fetched successfully.")


if __name__ == '__main__':
    main()
