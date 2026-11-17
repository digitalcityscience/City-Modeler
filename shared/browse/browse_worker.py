# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/browse_worker.py
"""Worker side of ``Browse first`` (T08, runs in a thread, no bpy).

Loads the index from the cache or builds it, decides the local origin on the
first batch and turns every batch into proxy mesh arrays, so the main thread
only copies buffers into the mesh. Messages handed to ``post``:

``("origin", origin, version, srs_name)``  once, before the first batch
``("batch", entries, arrays)``             entries in file order, arrays already offset
``("final", index, arrays_or_None, from_cache)``  arrays replace everything when given
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from ..import_origin import center_origin
from . import index_cache
from .gml_index import FileIndex, index_file
from .proxy_geometry import build_proxy_arrays

Post = Callable[[tuple], None]


def decide_origin(envelope, entries) -> tuple:
    """T01 rule: CityModel envelope centre (X/Y rounded to 100 m, Z = min Z),
    else the centre of the given entries' bboxes."""
    if envelope:
        origin = center_origin(envelope[0], envelope[1])
        if origin is not None:
            return origin
    boxes = [e.bbox for e in entries if e.bbox]
    if boxes:
        mins = [min(b[k] for b in boxes) for k in range(3)]
        maxs = [max(b[k + 3] for b in boxes) for k in range(3)]
        return center_origin(mins, maxs)
    return (0.0, 0.0, 0.0)


def run_browse_index(
    path: str,
    post: Post,
    *,
    fixed_origin: Optional[Sequence[float]] = None,
    progress: Optional[Callable[[int, int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    cache_dir: Optional[str] = None,
    cache_max_mb: Optional[float] = None,
    use_cache: bool = True,
) -> FileIndex:
    """Index ``path`` (or load it from the cache) and post proxy data.

    ``use_cache=False`` ignores a cached index; the new one is cached anyway.
    """
    if use_cache:
        cached = index_cache.load(path, cache_dir)
        if cached is not None:
            origin = tuple(fixed_origin) if fixed_origin else decide_origin(cached.envelope, cached.entries)
            post(("origin", origin, cached.version, cached.srs_name))
            post(("final", cached, build_proxy_arrays(cached.entries, origin), True))
            return cached

    # only the counts of what was posted are kept (offsets of the next batch)
    state = {"origin": tuple(fixed_origin) if fixed_origin else None, "posted": False,
             "entries": 0, "vertices": 0, "loops": 0}

    def on_batch(entries, partial):
        if state["origin"] is None:
            state["origin"] = decide_origin(partial.envelope, entries)
        if not state["posted"]:
            post(("origin", state["origin"], partial.version, partial.srs_name))
            state["posted"] = True
        arrays = build_proxy_arrays(
            entries, state["origin"], first_feature=state["entries"],
            vertex_offset=state["vertices"], loop_offset=state["loops"])
        state["entries"] += len(entries)
        state["vertices"] += arrays.vertex_count
        state["loops"] += arrays.loop_count
        post(("batch", entries, arrays))

    idx = index_file(path, progress=progress, on_batch=on_batch, cancel=cancelled)
    if not state["posted"]:
        state["origin"] = state["origin"] or decide_origin(idx.envelope, idx.entries)
        post(("origin", state["origin"], idx.version, idx.srs_name))
    # srsName mismatches are known only after the pass; they change face states
    full = build_proxy_arrays(idx.entries, state["origin"]) if any(e.srs_mismatch for e in idx.entries) else None
    index_cache.save(idx, cache_dir, max_mb=cache_max_mb)
    post(("final", idx, full, False))
    return idx
