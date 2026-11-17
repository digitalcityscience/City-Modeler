"""Pure gml_id index core for the GML object registry (CAC-P2.1, P2.4).

``build_index`` turns ``(key, gml_id, kind)`` entries into a ``GmlIndex``;
``findings`` reports duplicate / missing ids and duplicate / foreign
records (PLAN.md §2.6 C3: Shift+D cannot be refused, so it is detected).
``key`` is an opaque, sortable handle chosen by the caller (the Blender
registry uses ``obj.name_full`` as a handle only); identity always comes
from ``gml_id``, never from the key (PLAN.md R2). Output is deterministic:
it does not depend on the order of the entries.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GmlIndex:
    by_id: dict = field(default_factory=dict)       # gml_id -> key (smallest key if duplicated)
    duplicates: dict = field(default_factory=dict)  # gml_id -> tuple of all keys (sorted, >= 2)
    missing: tuple = ()                             # keys without a gml_id (sorted)
    kinds: dict = field(default_factory=dict)       # key -> kind

    def keys_for(self, gml_id):
        """Every key indexed under ``gml_id`` (sorted); empty if unknown."""
        if gml_id in self.duplicates:
            return self.duplicates[gml_id]
        key = self.by_id.get(gml_id)
        return (key,) if key is not None else ()


def build_index(entries):
    """``GmlIndex`` for ``entries`` = iterable of ``(key, gml_id, kind)``.

    An empty or whitespace ``gml_id`` puts the key into ``missing``. A key
    given twice is a caller error (``ValueError``).
    """
    keys_by_id, missing, kinds = {}, [], {}
    for key, gml_id, kind in entries:
        if key in kinds:
            raise ValueError(f"duplicate registry key {key!r}")
        kinds[key] = str(kind or "")
        gml_id = str(gml_id or "").strip()
        if gml_id:
            keys_by_id.setdefault(gml_id, []).append(key)
        else:
            missing.append(key)
    by_id, duplicates = {}, {}
    for gml_id in sorted(keys_by_id):
        keys = tuple(sorted(keys_by_id[gml_id]))
        by_id[gml_id] = keys[0]
        if len(keys) > 1:
            duplicates[gml_id] = keys
    return GmlIndex(
        by_id=by_id,
        duplicates=duplicates,
        missing=tuple(sorted(missing)),
        kinds=dict(sorted(kinds.items())),
    )


# --------------------------------------------------------------------------
# Findings (CAC-P2.4): duplicate / missing ids and duplicate / foreign records
# --------------------------------------------------------------------------
DUPLICATE_GML_ID = "duplicate_gml_id"
MISSING_GML_ID = "missing_gml_id"
DUPLICATE_RECORD = "duplicate_record"
FOREIGN_RECORD = "foreign_record"
_CODE_ORDER = (DUPLICATE_GML_ID, MISSING_GML_ID, DUPLICATE_RECORD, FOREIGN_RECORD)


@dataclass(frozen=True)
class Finding:
    code: str
    gml_id: str   # the id concerned; "" for missing_gml_id
    keys: tuple   # objects involved (sorted)
    message: str


def record_conflicts(record_entries):
    """``(duplicate_records, foreign_records)`` for ``(key, own_gml_id, record_feature_ids)``.

    ``duplicate_records``: feature gml_id -> keys (sorted, >= 2) of objects
    whose records both describe it (Shift+D, Separate, AB-14 Stamp).
    ``foreign_records``: key -> (own_gml_id, record ids) for an object whose
    own gml_id is set but not among its record's features.
    """
    holders, foreign = {}, {}
    for key, own_id, feature_ids in record_entries:
        own_id = str(own_id or "").strip()
        ids = tuple(str(i) for i in feature_ids)
        for fid in set(ids):
            holders.setdefault(fid, []).append(key)
        if own_id and own_id not in ids:
            foreign[key] = (own_id, ids)
    duplicates = {fid: tuple(sorted(keys)) for fid, keys in sorted(holders.items()) if len(keys) > 1}
    return duplicates, dict(sorted(foreign.items()))


def findings(index, record_entries=()):
    """Deterministic, sorted ``Finding`` tuple for ``index`` plus the records' conflicts."""
    found = [Finding(DUPLICATE_GML_ID, gid, keys, f"gml_id {gid!r} is carried by {len(keys)} objects")
             for gid, keys in index.duplicates.items()]
    found += [Finding(MISSING_GML_ID, "", (key,), f"{index.kinds.get(key) or 'object'} {key!r} has no gml_id")
              for key in index.missing]
    duplicate_records, foreign_records = record_conflicts(record_entries)
    found += [Finding(DUPLICATE_RECORD, fid, keys, f"{len(keys)} objects carry a record for {fid!r}")
              for fid, keys in duplicate_records.items()]
    found += [Finding(FOREIGN_RECORD, own_id, (key,),
                      f"{key!r} (gml_id {own_id!r}) carries a record for {list(ids)}")
              for key, (own_id, ids) in foreign_records.items()]
    return tuple(sorted(found, key=lambda f: (_CODE_ORDER.index(f.code), f.gml_id, f.keys)))
