"""Derived gml_id -> Object registry (CAC-P2.2).

One index over ``bpy.data.objects`` that never trusts object names (R2):
an object's identity is its record's gml_id when it carries one (flag on,
read through ``storage``), else its legacy ``obj["gml_id"]``. The index is
derived state only:

- ``load_post`` / ``undo_post`` / ``redo_post`` rebuild it, because those
  replace every Object (stored references become invalid);
- ``depsgraph_update_post`` only marks it dirty (R4: no handler writes
  records or touches meshes); the next ``get`` rebuilds lazily;
- ``get`` validates the stored reference (``ReferenceError``, or an object
  whose identity changed) and rebuilds once on a mismatch.

``bpy`` is imported lazily, so ``identity_of`` and ``GmlRegistry`` with an
injected object source are unit-testable with fake objects.
"""
import traceback

from ..domain.registry_core import build_index
from ..domain.registry_core import findings as registry_findings
from . import flag, storage


def _legacy_id(obj):
    return str(obj.get("gml_id") or "").strip()


def identity_of(obj, use_records):
    """``(gml_id, kind, has_record)`` of ``obj``; ``gml_id`` is ``""`` if it has none.

    With ``use_records`` a stored record decides the identity only where the
    legacy ``gml_id`` does not contradict it: a record describing another
    gml_id (AB-14, Shift+D + re-mint) never changes who the object is.
    """
    legacy = _legacy_id(obj)
    kind = str(obj.get("cgml3_feature") or "")
    record_set = storage.read_record(obj) if use_records else None
    if record_set is None or not record_set.features:
        return legacy, kind, False
    feature = record_set.feature(legacy) if legacy else record_set.features[0]
    if feature is None:  # foreign record: keep the object's own identity
        return legacy, kind, True
    return feature.gml_id, feature.kind or kind, True


def _all_objects():
    import bpy

    return bpy.data.objects


def _key(obj):
    return getattr(obj, "name_full", None) or obj.name


class GmlRegistry:
    def __init__(self, objects=None, records_enabled=None):
        self._objects = objects or _all_objects
        self._records_enabled = records_enabled or flag.is_enabled
        self._index = None
        self._refs = {}
        self._dirty = True
        self.rebuild_count = 0

    # -- state -------------------------------------------------------------
    def records_enabled(self):
        return bool(self._records_enabled())

    def mark_dirty(self):
        self._dirty = True

    @property
    def dirty(self):
        return self._dirty

    def clear(self):
        """Drop every object reference (unregister); the next read rebuilds."""
        self._index = None
        self._refs = {}
        self._dirty = True

    def rebuild(self):
        use_records = self.records_enabled()
        entries, refs = [], {}
        for obj in self._objects():
            gml_id, kind, has_record = identity_of(obj, use_records)
            if not (gml_id or kind or has_record):
                continue  # not a CityGML object (camera, helper mesh, ...)
            key = _key(obj)
            refs[key] = obj
            entries.append((key, gml_id, kind))
        self._index = build_index(entries)
        self._refs = refs
        self._dirty = False
        self.rebuild_count += 1
        return self._index

    def index(self):
        if self._dirty or self._index is None:
            self.rebuild()
        return self._index

    # -- lookups -------------------------------------------------------------
    def _valid(self, obj, gml_id, use_records):
        try:
            return identity_of(obj, use_records)[0] == gml_id
        except ReferenceError:  # object removed since the last rebuild
            return False

    def get_all(self, gml_id):
        """Every object whose identity is ``gml_id`` (sorted by key).

        A miss or a stale reference rebuilds once (unless this call already
        rebuilt): not every change fires ``depsgraph_update_post`` (e.g. a
        script linking a new object), so "dirty" alone is not enough.
        """
        gml_id = str(gml_id or "").strip()
        if not gml_id:
            return []
        use_records = self.records_enabled()
        rebuilt = self._dirty or self._index is None
        index = self.index()
        while True:
            objs = [self._refs[k] for k in index.keys_for(gml_id)]
            valid = [o for o in objs if self._valid(o, gml_id, use_records)]
            if (objs and len(valid) == len(objs)) or rebuilt:
                return valid
            index = self.rebuild()
            rebuilt = True

    def get(self, gml_id):
        """The object with identity ``gml_id`` (first by key if duplicated), or ``None``."""
        objs = self.get_all(gml_id)
        return objs[0] if objs else None

    # -- reporting (P2.4) -----------------------------------------------------
    def findings(self):
        """Duplicate / missing ids, and (flag on) duplicate / foreign records.

        Report only (D-4: the policy is decided in P4); always from a fresh
        rebuild, since it is an explicit, infrequent call.
        """
        index = self.rebuild()
        record_entries = []
        if self.records_enabled():
            for key, obj in self._refs.items():
                record_set = storage.read_record(obj)
                if record_set is not None and record_set.features:
                    record_entries.append((key, _legacy_id(obj), tuple(f.gml_id for f in record_set.features)))
        return registry_findings(index, record_entries)


REGISTRY = GmlRegistry()


_logged = set()


def _log_once(message):
    if message not in _logged:
        _logged.add(message)
        print(f"[CityGML] {message}")


def _object_by_name(name):
    import bpy

    return bpy.data.objects.get(name)


def resolve(gml_id, fallback_name="", *, registry=None, by_name=None):
    """The object a semantic reference ``(gml_id, fallback_name)`` points at.

    The one place production code turns a stored reference into an Object
    (P2.3). Identity first: the registry's object for ``gml_id`` (for a
    duplicated id, the one still named ``fallback_name`` if any, else the
    first). The name is a fallback for objects the registry cannot find
    by id (no ``gml_id`` of their own, e.g. a wall whose SurfaceContext id
    came from its face material), and it is refused if that object is
    another feature.
    """
    registry = registry or REGISTRY
    by_name = by_name or _object_by_name
    gml_id = str(gml_id or "").strip()
    if gml_id:
        candidates = registry.get_all(gml_id)
        if len(candidates) > 1:
            _log_once(f"gml_id {gml_id!r} is carried by {len(candidates)} objects "
                      f"({', '.join(o.name for o in candidates)}); resolving by name {fallback_name!r} first")
            return next((o for o in candidates if o.name == fallback_name), candidates[0])
        if candidates:
            return candidates[0]
    if not fallback_name:
        return None
    obj = by_name(fallback_name)
    if obj is None or not gml_id:
        return obj
    own_id = identity_of(obj, registry.records_enabled())[0]
    if own_id and own_id != gml_id:
        return None  # the name now belongs to another feature
    _log_once(f"no object with gml_id {gml_id!r}; resolved by name {fallback_name!r}")
    return obj


# --------------------------------------------------------------------------
# Handlers (registered from the add-on's register(); idempotent across reloads)
# --------------------------------------------------------------------------
def _rebuild_handler(*_args):
    try:
        REGISTRY.rebuild()
    except Exception:
        traceback.print_exc()  # a handler must never break load/undo


def _cac_registry_on_load_post(*args):
    _rebuild_handler(*args)


def _cac_registry_on_undo_post(*args):
    _rebuild_handler(*args)


def _cac_registry_on_redo_post(*args):
    _rebuild_handler(*args)


def _cac_registry_on_depsgraph_update_post(*_args):
    REGISTRY.mark_dirty()  # R4: never writes, never rebuilds here


_HANDLERS = {
    "load_post": _cac_registry_on_load_post,
    "undo_post": _cac_registry_on_undo_post,
    "redo_post": _cac_registry_on_redo_post,
    "depsgraph_update_post": _cac_registry_on_depsgraph_update_post,
}


def _remove_ours(handler_list, fn_name):
    # Match by name + module, not identity: a reloaded module brings new
    # function objects, and "fn not in list" would append a second copy.
    for fn in list(handler_list):
        if getattr(fn, "__name__", None) == fn_name and getattr(fn, "__module__", None) == __name__:
            handler_list.remove(fn)


def register():
    import bpy
    from bpy.app.handlers import persistent

    for list_name, fn in _HANDLERS.items():
        handler_list = getattr(bpy.app.handlers, list_name)
        _remove_ours(handler_list, fn.__name__)
        handler_list.append(persistent(fn))
    REGISTRY.clear()


def unregister():
    import bpy

    for list_name, fn in _HANDLERS.items():
        _remove_ours(getattr(bpy.app.handlers, list_name), fn.__name__)
    REGISTRY.clear()
