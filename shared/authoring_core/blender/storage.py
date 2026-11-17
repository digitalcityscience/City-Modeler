"""Blender storage for authoring records: one JSON document per Object.

ADR 0009 (accepted at G1): a plain ID property, not a registered
PropertyGroup. Only this module reads or writes ``RECORD_KEY``. A malformed
value -- invalid JSON, a dict ``records.py`` can't parse, or one that fails
``RecordSet.validate()`` -- is treated as "no record", never raised into
callers (ADR 0009, Consequences: Corruption).
"""
import json

from ..domain.records import RecordError, RecordSet

# Leading underscore: Blender hides an ID-property key starting with "_"
# from the N-panel Custom Properties list (still a plain ID property,
# still visible via obj.keys() / the Python console -- this only keeps it
# out of the panel a user would otherwise hand-edit by accident).
RECORD_KEY = "_cgml_record"


def read_record(obj):
    """``RecordSet`` stored on ``obj``, or ``None`` if absent or unreadable."""
    raw = obj.get(RECORD_KEY)
    if not raw:
        return None
    try:
        record_set = RecordSet.from_dict(json.loads(raw))
    except (ValueError, TypeError, RecordError):
        return None
    if record_set.validate():
        return None
    return record_set


def has_record(obj):
    return read_record(obj) is not None


RECORD_NONE = "none"
RECORD_OK = "ok"
RECORD_MALFORMED = "malformed"


def record_state(obj):
    """``RECORD_NONE`` (no record key), ``RECORD_OK`` (a readable record) or
    ``RECORD_MALFORMED`` (the key is present but unreadable).

    Readers keep treating a malformed value as "no record" (ADR 0009);
    guards (``guards.py``) use this to fail closed on it instead.
    """
    if RECORD_KEY not in obj.keys():
        return RECORD_NONE
    try:
        readable = read_record(obj) is not None
    except Exception:  # any shape read_record does not anticipate is malformed too
        readable = False
    return RECORD_OK if readable else RECORD_MALFORMED


def write_record(obj, record_set):
    """Write ``record_set`` (a ``RecordSet``) onto ``obj`` as one JSON document."""
    obj[RECORD_KEY] = json.dumps(record_set.to_dict(), separators=(",", ":"))


def clear_record(obj):
    if RECORD_KEY in obj.keys():
        del obj[RECORD_KEY]
