# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Depsgraph-driven transform-drift dirty marker for record-backed objects
(CAC-P4.5, D-7b).

Detection itself is not new: P3.3's existing ``coords_stale`` finding
(``domain/consistency.py``) already compares each record-backed feature's
committed (world-space) coordinates against its *live* world-space geometry
(``blender/consistency_view.py::_polygon_world_coords`` reads
``obj.matrix_world`` directly), so it already catches both a Window's own
G/R/S and an ancestor-only move (Wall/Building) without any change here -
see the CAC-P4.5 task card and PLAN.md's G1 risk note. This module does not
add a second detector; it only mirrors that same finding into a small
in-memory cache, lazily, on demand.

R4 (handlers are never authoritative): the depsgraph/undo/redo/load handlers
below do nothing but flip an in-memory ``bool`` ("something may have
changed, recompute next time someone asks") - marking only, no scan, no
read of any record/mesh, and definitely no write to one. The actual P3.3
scan (``consistency_view.run_validation``) only ever runs lazily, inside
``recompute_dirty``, the first time ``dirty_gml_ids``/``is_dirty`` is asked
after a mark (deliberately *not* eagerly inside the handler itself - CAC-P2's
registry has its own "handler marks dirty, a later read rebuilds" contract,
``blender/registry.py::GmlRegistry``, and running a full scene scan on every
single depsgraph tick would defeat that same discipline for no benefit, since
nothing needs the answer between transforms). The only code allowed to
resolve drift is ``edit_session.commit_transform_drift``, driven by the
explicit ``cgml.commit_transform_drift`` use case
(``ops/authoring_core_ops.py``); nothing here is capable of that.

The cache is a UI/reporting convenience only (e.g. "commit all dirty",
``target='ALL_DIRTY'`` on the operator, or a future N-panel badge) - it is
never consulted by the export gate or by the commit use case itself, both
of which always recompute the authoritative comparison fresh
(``consistency_view.export_preflight`` / ``edit_session.commit_transform_
drift``). That split matters for determinism across undo/redo and file
load: this cache is rebuilt from scratch (never patched incrementally)
whenever it is next read after a mark, so it can never be "more stale" than
the next read, and if it is ever wrong or simply never rebuilt (handler not
registered, an exception mid-rebuild), the worst outcome is a stale UI hint
- never a wrong commit or a wrongly-allowed export, because neither of
those paths trusts it.
"""
from __future__ import annotations

from ..domain import consistency

_DIRTY_GML_IDS: set = set()
_STALE = True  # nothing has been scanned yet; the next read must scan once.


def _ensure_fresh() -> None:
    global _STALE
    if _STALE:
        recompute_dirty()


def dirty_gml_ids() -> frozenset:
    """The gml_ids this module's cache currently believes are drifted.

    UI/reporting only - see module docstring. Callers that must be correct
    (export, the commit use case) recompute from scratch instead."""
    _ensure_fresh()
    return frozenset(_DIRTY_GML_IDS)


def is_dirty(gml_id) -> bool:
    _ensure_fresh()
    return str(gml_id or "").strip() in _DIRTY_GML_IDS


def recompute_dirty(objects=None) -> frozenset:
    """Rebuild the cache from a fresh P3.3 scan and return the new set.

    Read-only (R4): the only mutation is to this module's own cache. Called
    lazily by ``_ensure_fresh`` - a handler never calls this directly, it
    only ever marks the cache stale (``_mark_stale`` below)."""
    global _STALE
    from . import consistency_view  # lazy: ring_opening_sync (its dependency) imports bpy

    findings = consistency_view.run_validation(objects)
    _DIRTY_GML_IDS.clear()
    _DIRTY_GML_IDS.update(
        f.gml_id for f in findings if f.code == consistency.COORDS_STALE and f.gml_id
    )
    _STALE = False
    return frozenset(_DIRTY_GML_IDS)


def reset() -> None:
    global _STALE
    _DIRTY_GML_IDS.clear()
    _STALE = True


def _mark_stale(*_args) -> None:
    global _STALE
    _STALE = True


# Every registered handler does exactly the same one thing - flip the stale
# bit - regardless of which Blender event fired it (marking only, R4).
_on_depsgraph_update = _mark_stale
_on_load_post = _mark_stale
_on_undo_redo_post = _mark_stale

_HANDLERS = {
    "depsgraph_update_post": _on_depsgraph_update,
    "load_post": _on_load_post,
    "undo_post": _on_undo_redo_post,
    "redo_post": _on_undo_redo_post,
}


def _remove_ours(handler_list, fn):
    # Match by name + module, not identity: a reloaded module brings new
    # function objects, and "fn not in list" would append a second copy
    # (same convention as blender/registry.py's own handler bookkeeping).
    for existing in list(handler_list):
        if getattr(existing, "__name__", None) == fn.__name__ and \
                getattr(existing, "__module__", None) == fn.__module__:
            handler_list.remove(existing)


def register():
    import bpy

    reset()
    for attr, fn in _HANDLERS.items():
        handler_list = getattr(bpy.app.handlers, attr)
        _remove_ours(handler_list, fn)
        handler_list.append(fn)


def unregister():
    import bpy

    for attr, fn in _HANDLERS.items():
        handler_list = getattr(bpy.app.handlers, attr)
        _remove_ours(handler_list, fn)
    reset()
