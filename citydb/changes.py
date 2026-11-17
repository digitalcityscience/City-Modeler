# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/changes.py
"""Pending changes between the Blender scene and a 3DCityDB city model (B84, no bpy).

The scene is a working copy: nothing reaches the database until a commit.
A top-level feature of the scene is

- ``new``      not tracked: it was never loaded from or committed to this city model;
- ``changed``  tracked, and it differs from what was loaded / committed last;
- ``synced``   tracked and unchanged;
- ``deleted``  tracked, but no longer in the scene (deleted in Blender, not unloaded).

"Tracked" is ``{objectid: feature id}`` kept on the scene (``ops.commit_citydb``).
A feature id of 0 means "in the database, row unknown".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .update import inspect_export

NEW, CHANGED, DELETED, SYNCED = "new", "changed", "deleted", "synced"
PENDING = (NEW, CHANGED, DELETED)
LABELS = {NEW: "New, not in database", CHANGED: "Changed here", DELETED: "Deleted here, still in database",
          SYNCED: "Same as database"}


@dataclass(frozen=True)
class Change:
    kind: str
    objectid: str
    fid: int = 0    # feature.id the scene knows; 0: unknown or not in the database
    name: str = ""  # Blender object name ("" for a deleted feature)


def classify(features: Mapping[str, Tuple[str, bool]], tracked: Mapping[str, int]) -> List[Change]:
    """``features``: ``{gml:id: (object name, unchanged since load/commit)}`` of the scene."""
    out = [Change(NEW, gid, 0, name) if gid not in tracked
           else Change(SYNCED if same else CHANGED, gid, int(tracked[gid]), name)
           for gid, (name, same) in features.items()]
    out += [Change(DELETED, gid, int(fid)) for gid, fid in tracked.items() if gid not in features]
    return out


def pending(changes: Sequence[Change]) -> List[Change]:
    return [c for c in changes if c.kind in PENDING]


def conflicts(changes: Sequence[Change], active: Mapping[str, Sequence[int]]) -> Dict[str, str]:
    """``{objectid: why the commit must not run}``; ``active``: active feature ids per objectid now.

    A commit only replaces rows the scene was loaded from: a new feature must not
    exist yet, a changed or deleted one must still be the row that was loaded.
    """
    out: Dict[str, str] = {}
    for c in changes:
        ids = sorted(int(i) for i in active.get(c.objectid, ()))
        now = "id " + ", ".join(str(i) for i in ids)
        if c.kind == NEW and ids:
            out[c.objectid] = f"already in the database ({now}), but not loaded from it"
        elif c.kind == CHANGED and not ids:
            out[c.objectid] = "no longer in the database (removed by someone else)"
        elif c.kind in (CHANGED, DELETED) and ids and (len(ids) > 1 or (c.fid and ids != [c.fid])):
            out[c.objectid] = f"changed in the database since it was loaded (now {now})"
    return out


def summary(changes: Sequence[Change]) -> str:
    """``2 new · 1 changed · 1 deleted`` (pending kinds only)."""
    n = {k: sum(1 for c in changes if c.kind == k) for k in PENDING}
    return " · ".join(f"{n[k]} {k}" for k in PENDING if n[k]) or "no changes"


def export_problem(path: str, objectids: Sequence[str]) -> Optional[str]:
    """Why the temporary export cannot be committed, or None: it must hold exactly ``objectids``."""
    try:
        members, internal = inspect_export(path)
    except (OSError, ET.ParseError) as exc:
        return f"The exported file could not be read: {exc}"
    missing = sorted(set(objectids) - set(members))
    extra = sorted(set(members) - set(objectids))
    if missing or extra or len(members) != len(set(members)):
        return ("The export does not hold exactly the features to commit"
                + (f"; missing (hidden?): {', '.join(missing[:3])}" if missing else "")
                + (f"; unexpected: {', '.join(extra[:3])}" if extra else "")
                + ("; the same gml:id twice (duplicated object?)" if len(members) != len(set(members)) else ""))
    if internal:
        return "The export carries add-on properties as generic attributes: " + ", ".join(internal[:5])
    return None
