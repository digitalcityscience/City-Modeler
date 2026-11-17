"""Refusal guard for legacy authoring tools on record-backed objects (CAC-P3.0).

PLAN.md §2.6: a legacy tool that changes geometry, identity, semantic type,
ownership, host relation or topology must not mutate legacy state behind a
record. Until a tool is migrated onto a record use case it calls
``refuse_if_record_backed`` as its first step (C2: one guard, no ad-hoc
checks) and cancels with the returned message.

What an object "touches" (C1) is relation-aware, never a sibling scan:
the object itself, its ancestor chain (host wall, Building/BuildingPart),
all its descendants (a wall's openings, a Building's members) and the host
named by ``filling_parent_surface_id`` (an opening not parented under its
wall). A caller whose tool reaches further -- LoD promotion rewrites the
whole top-level hierarchy -- passes that scope's root as well.

Flag off: always ``None`` (C6, byte-for-byte legacy behaviour). Flag on: a
malformed ``_cgml_record`` fails closed (refused, reported as malformed),
unlike readers, which treat it as "no record" (ADR 0009). The guard never
writes and never raises; an internal error refuses the operation.
"""
import traceback

from . import flag, storage

HOST_ID_KEY = "filling_parent_surface_id"


def _key(obj):
    pointer = getattr(obj, "as_pointer", None)
    return pointer() if callable(pointer) else id(obj)


def _ancestors(obj):
    seen = set()
    cur = getattr(obj, "parent", None)
    while cur is not None and _key(cur) not in seen:
        seen.add(_key(cur))
        yield cur
        cur = getattr(cur, "parent", None)


def _descendants(obj):
    seen = set()
    stack = list(getattr(obj, "children", None) or ())
    while stack:
        cur = stack.pop()
        if _key(cur) in seen:
            continue
        seen.add(_key(cur))
        yield cur
        stack.extend(getattr(cur, "children", None) or ())


def _default_lookup(gml_id):
    from .registry import REGISTRY

    return REGISTRY.get_all(gml_id)


def touched_objects(objects, *, lookup=None):
    """Every object the C1 closure of ``objects`` reaches, deduplicated."""
    lookup = lookup or _default_lookup
    found = {}

    def add(obj):
        if obj is not None:
            found.setdefault(_key(obj), obj)

    for obj in objects or ():
        if obj is None:
            continue
        add(obj)
        for other in _ancestors(obj):
            add(other)
        for other in _descendants(obj):
            add(other)
        host_id = str(obj.get(HOST_ID_KEY) or "").strip()
        for host in (lookup(host_id) if host_id else ()):
            add(host)
            for other in _ancestors(host):
                add(other)
    return list(found.values())


def _describe(obj):
    gml_id = str(obj.get("gml_id") or "").strip() or "no gml_id"
    return f"'{obj.name}' ({gml_id})"


def plan_guarded_delete(objects, *, resolve=None):
    """Partition ``objects`` for the guarded native-delete wrapper (CAC-P4.4,
    D-7a): unlike ``refuse_if_record_backed`` (all-or-nothing refusal of any
    record-backed object), this is the mechanism itself - it decides which
    objects route through a delete use case, which block the whole
    operation, and which fall through to native delete unchanged.

    Returns ``(routable, blocking, legacy)``:

    - ``routable``: ``[(obj, gml_id), ...]`` - record-backed Windows whose
      delete is pre-validated (``domain/delete_lifecycle.plan_window_delete``
      raises nothing) - a dry run, since that function only plans, never
      mutates. Calling ``usecases.delete_feature(gml_id)`` for each is
      expected to succeed.
    - ``blocking``: ``[(obj, reason), ...]`` - record-backed objects with no
      available delete use case: any kind other than Window, a malformed
      record, or a Window whose plan itself fails (e.g. a corrupted ring or
      missing host). A non-empty ``blocking`` means the whole operation must
      refuse and delete nothing (all-or-nothing, D-7a) - checked before any
      routable object is actually deleted.
    - ``legacy``: objects carrying no record at all - fall through to
      native ``object.delete`` unchanged.

    Flag off: everything is ``legacy`` (nothing routes or blocks), matching
    C6/R6 pass-through parity - the caller decides what "flag off" means for
    the operator as a whole; this function itself does not check the flag,
    since its caller (``cgml.guarded_delete``) already does.

    ``resolve(gml_id, fallback_name) -> obj`` overrides ``registry.resolve``
    (tests inject a fake).
    """
    from ..domain.delete_lifecycle import DeleteFeatureError, plan_window_delete

    def _default_resolve(gml_id, fallback_name):
        from .registry import resolve as registry_resolve

        return registry_resolve(gml_id, fallback_name)

    resolve = resolve or _default_resolve
    routable, blocking, legacy = [], [], []
    for obj in objects or ():
        if obj is None:
            continue
        state = storage.record_state(obj)
        if state == storage.RECORD_NONE:
            legacy.append(obj)
            continue
        if state == storage.RECORD_MALFORMED:
            blocking.append((obj, "has a malformed authoring record"))
            continue
        record = storage.read_record(obj)
        gml_id = str(obj.get("gml_id") or "").strip()
        feature = record.feature(gml_id) if record else None
        if feature is None:
            blocking.append((obj, "record does not name this object's gml_id"))
            continue
        if feature.kind != "Window":
            blocking.append((obj, f"kind {feature.kind!r} has no delete use case yet"))
            continue
        wall = resolve(feature.host_id, obj.parent.name if obj.parent else "")
        wall_record = storage.read_record(wall) if wall is not None else None
        try:
            plan_window_delete(gml_id, record, feature.host_id, wall_record)
        except DeleteFeatureError as e:
            blocking.append((obj, str(e)))
            continue
        routable.append((obj, gml_id))
    return routable, blocking, legacy


def refuse_if_record_backed(objects, op_name, *, lookup=None):
    """A user-facing refusal message, or ``None`` if ``op_name`` may run.

    ``lookup(gml_id) -> list`` resolves the id-named host; it defaults to
    the GmlRegistry (tests inject a fake).
    """
    if not flag.is_enabled():
        return None
    try:
        offenders = []
        for obj in touched_objects(objects, lookup=lookup):
            state = storage.record_state(obj)
            if state != storage.RECORD_NONE:
                offenders.append((state != storage.RECORD_MALFORMED, obj.name, obj, state))
        if not offenders:
            return None
        offenders.sort(key=lambda row: row[:2])
        _ok_first, _name, obj, state = offenders[0]
        if state == storage.RECORD_MALFORMED:
            reason = (f"has a malformed authoring record ({storage.RECORD_KEY} cannot be read); "
                      "repair or remove it before using this tool")
        else:
            reason = ("is record-backed (experimental authoring core); "
                      "this tool does not update records yet")
        more = f" (+{len(offenders) - 1} more)" if len(offenders) > 1 else ""
        return f"{op_name}: refused - {_describe(obj)} {reason}{more}"
    except Exception as exc:  # fail closed: a guard bug must not let a mutation through
        traceback.print_exc()
        return f"{op_name}: refused - record guard failed ({exc}); see the console"
