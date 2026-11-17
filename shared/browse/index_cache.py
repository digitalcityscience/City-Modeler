# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/index_cache.py
"""On-disk cache of feature indexes (T08, pure Python, no bpy).

Location: ``%LOCALAPPDATA%/city_modeler/index`` (Windows) or
``$XDG_CACHE_HOME/city_modeler/index`` (``~/.cache``);
``CGML3_INDEX_CACHE_DIR`` overrides it (tests, benchmarks). The key
hashes the absolute path, size, mtime and the first and last MiB of the
file, so any change of the file gives a new key and the old entry is never
used again. Old entries are removed by :func:`prune` (size limit, oldest first).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
from typing import Optional

from .gml_index import INDEX_FORMAT, FileIndex

APP_DIR = "city_modeler"
EDGE_BYTES = 1 << 20
SUFFIX = ".idx.jsonl.gz"  # header line + one line per feature


def default_cache_dir() -> str:
    override = os.environ.get("CGML3_INDEX_CACHE_DIR")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    if not base:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, APP_DIR, "index")


def cache_key(path: str) -> str:
    """Hash of path, size, mtime and the first/last MiB of ``path``."""
    st = os.stat(path)
    h = hashlib.sha1()
    h.update(f"v{INDEX_FORMAT}|{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}|".encode("utf-8"))
    with open(path, "rb") as fh:
        h.update(fh.read(EDGE_BYTES))
        if st.st_size > EDGE_BYTES:
            fh.seek(max(EDGE_BYTES, st.st_size - EDGE_BYTES))
            h.update(fh.read(EDGE_BYTES))
    return h.hexdigest()


def cache_file(path: str, cache_dir: Optional[str] = None) -> str:
    return os.path.join(cache_dir or default_cache_dir(), cache_key(path) + SUFFIX)


def load(path: str, cache_dir: Optional[str] = None) -> Optional[FileIndex]:
    """The cached index of ``path``, or None (missing, stale or unreadable)."""
    try:
        target = cache_file(path, cache_dir)
    except OSError:
        return None
    if not os.path.isfile(target):
        return None
    try:
        with gzip.open(target, "rt", encoding="utf-8") as fh:
            header = json.loads(fh.readline())
            header["entries"] = [json.loads(line) for line in fh if line.strip()]
        idx = FileIndex.from_dict(header)
        st = os.stat(path)
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None
    if len(idx.entries) != header.get("entry_count", -1):
        return None  # truncated file
    if idx.size != st.st_size or idx.mtime_ns != st.st_mtime_ns:
        return None
    try:
        os.utime(target)  # most recently used survives pruning
    except OSError:
        pass
    return idx


def save(idx: FileIndex, cache_dir: Optional[str] = None, max_mb: Optional[float] = None) -> Optional[str]:
    """Write ``idx`` atomically; returns the cache file or None when it could not be written."""
    try:
        target = cache_file(idx.path, cache_dir)
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        header = idx.to_dict(entries=False)
        header["entry_count"] = len(idx.entries)
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as gz, \
                io.TextIOWrapper(gz, encoding="utf-8") as text:
            # one JSON line per feature: small encoder calls release the GIL
            # in between (one big json.dump holds it for the whole index and
            # stalls Blender's main thread), and no copy of the whole index
            text.write(json.dumps(header, separators=(",", ":")) + "\n")
            for e in idx.entries:
                text.write(json.dumps(e.to_list(), separators=(",", ":")) + "\n")
        os.replace(tmp, target)
    except OSError:
        return None
    if max_mb is not None:
        prune(max_mb, cache_dir, keep=target)
    return target


def _entries(cache_dir: str):
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(SUFFIX):
            p = os.path.join(cache_dir, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((st.st_mtime, st.st_size, p))
    return out


def size_bytes(cache_dir: Optional[str] = None) -> int:
    return sum(s for _, s, _ in _entries(cache_dir or default_cache_dir()))


def prune(max_mb: float, cache_dir: Optional[str] = None, keep: Optional[str] = None) -> int:
    """Delete the oldest cache files until the cache is below ``max_mb``; returns the count."""
    folder = cache_dir or default_cache_dir()
    files = sorted(_entries(folder))
    total = sum(s for _, s, _ in files)
    limit = max(0.0, float(max_mb)) * 2**20
    removed = 0
    for _, size, p in files:
        if total <= limit:
            break
        if keep and os.path.normcase(p) == os.path.normcase(keep):
            continue
        try:
            os.remove(p)
        except OSError:
            continue
        total -= size
        removed += 1
    return removed


def clear(cache_dir: Optional[str] = None) -> int:
    return prune(0.0, cache_dir)
