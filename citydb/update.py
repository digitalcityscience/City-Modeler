# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# citydb/update.py
"""Write one loaded feature back to 3DCityDB (T10, no bpy).

citydb-tool 1.3.0 behaviour this module is built on (local trial 2026-10-24,
see tickets/T10-selected-feature-update.md):

- ``--preview`` writes nothing; its log says ``Deleting N duplicates ... in
  preview mode`` and lists the features per type, so a dry run tells how many
  rows a real run would replace.
- A duplicate is every *active* row (``termination_date IS NULL``) whose
  objectid equals the file's ``gml:id``. ``terminate`` closes all of them (and
  their sub-features) and inserts the file as a new feature.id; ``delete``
  removes them physically; ``skip`` leaves the file out when they exist, so it
  never changes an existing feature.
- The envelope is taken from the file's ``gml:boundedBy`` unless
  ``--compute-extent`` is given, and ``creationDate`` from the file unless
  ``--creation-date`` is given.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .catalog import CatalogRow, InvalidId, _OBJECTID_TOKEN, versions_label

# operator modes -> citydb-tool --import-mode
TOOL_MODES = {"TERMINATE": "terminate", "DELETE": "delete", "SKIP": "skip"}
# real runs of these modes need an explicit confirmation
CONFIRM_MODES = frozenset({"TERMINATE", "DELETE"})
DEFAULT_REASON = "Updated in Blender (CityGML add-on)"
# custom properties of the add-on that must never reach the database as generic attributes
INTERNAL_PREFIX = "cgml3_"

MODE_LABELS = {
    "TERMINATE": "terminate (keeps the old version, recommended)",
    "DELETE": "delete (removes the old version)",
    "SKIP": "skip (writes nothing if the feature exists)",
}

_DUPLICATES = re.compile(r"Deleting (\d+) duplicates? from database( in preview mode)?", re.IGNORECASE)
_SKIPPED_FILE = re.compile(r"All features to be imported are duplicates", re.IGNORECASE)
_SECTION = re.compile(r"(Preview of features to be imported|Import summary):", re.IGNORECASE)
_COUNT = re.compile(r"\]\s+([A-Za-z][\w.-]*:[A-Za-z][\w.-]*):\s+(\d+)\s*$")
_LEVEL = re.compile(r"\[[^\]]*\b(ERROR|FATAL|WARN)\]\s*(.*)$")


def validate_objectid(objectid: str) -> str:
    objectid = (objectid or "").strip()
    if not objectid or not _OBJECTID_TOKEN.match(objectid):
        raise InvalidId(f"Invalid objectid: {objectid[:40]!r}")
    return objectid


# --------------------------------------------------------------------------- citydb-tool
def import_options(mode: str, *, preview: bool, reason: str = DEFAULT_REASON,
                   person: str = "") -> Dict[str, object]:
    """Keyword arguments for ``CityDBTool.import_citygml_to_db`` (besides file and connection).

    ``--compute-extent``: the box must follow the edited geometry, not the
    ``gml:boundedBy`` the exporter copied. ``--creation-date=now``: the new
    version starts when the old one is terminated.
    """
    if mode not in TOOL_MODES:
        raise ValueError(f"unknown update mode {mode}")
    opts: Dict[str, object] = {
        "import_mode": TOOL_MODES[mode],
        "compute_extent": True,
        "creation_date": "now",
        "preview": bool(preview),
    }
    if reason:
        opts["reason_for_update"] = reason
    if person:
        opts["updating_person"] = person
    return opts


@dataclass
class ImportReport:
    """What a ``citydb import citygml`` log says."""

    duplicates: int = 0
    preview: bool = False
    skipped_file: bool = False
    counts: Dict[str, int] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return sum(self.counts.values())

    def counts_text(self, limit: int = 4) -> str:
        items = sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))
        text = ", ".join(f"{k.split(':')[-1]} {v}" for k, v in items[:limit])
        if len(items) > limit:
            text += f", +{len(items) - limit} types"
        return text or "nothing"


def parse_import_log(text: str) -> ImportReport:
    rep = ImportReport()
    in_section = False
    for line in (text or "").splitlines():
        m = _DUPLICATES.search(line)
        if m:
            rep.duplicates += int(m.group(1))
            rep.preview = rep.preview or bool(m.group(2))
            continue
        if "preview mode" in line.lower():
            rep.preview = True
        if _SKIPPED_FILE.search(line):
            rep.skipped_file = True
            continue
        if _SECTION.search(line):
            in_section = True
            continue
        m = _COUNT.search(line) if in_section else None
        if m:
            rep.counts[m.group(1)] = rep.counts.get(m.group(1), 0) + int(m.group(2))
            continue
        m = _LEVEL.search(line)
        if m:
            rep.problems.append(m.group(2).strip()[:200])
        in_section = False
    return rep


# --------------------------------------------------------------------------- plan / confirmation
def plan_problem(mode: str, expected_ids: Sequence[int], active: Sequence[CatalogRow],
                 report: ImportReport) -> Optional[str]:
    """Why a real run must not start after the preview, or None.

    ``expected_ids`` are the rows the user confirmed. The run is refused when
    the database no longer matches them (another client changed it) or when
    citydb-tool would replace a different number of rows than it found.
    """
    active_ids = sorted(r.id for r in active)
    if not active_ids:
        return "The feature is no longer active in the database (terminated or deleted by someone else)"
    if mode in CONFIRM_MODES:
        if not expected_ids:
            return "No confirmed rows: open the confirmation dialog (Update / Replace)"
        if sorted(set(int(i) for i in expected_ids)) != active_ids:
            return (f"The database changed: active rows are {versions_label(active)}, "
                    f"you confirmed id {', '.join(str(i) for i in sorted(set(expected_ids)))}. "
                    "Run the dry run again")
        if report.duplicates != len(active_ids):
            return (f"citydb-tool would replace {report.duplicates} row(s), the catalog has "
                    f"{len(active_ids)}; nothing was written")
    if report.problems:
        return "citydb-tool preview reported: " + report.problems[0]
    return None


def confirm_lines(mode: str, objectid: str, active: Sequence[CatalogRow], where: str) -> List[str]:
    """Text of the confirmation dialog (no password: ``where`` is user@host:port/db/schema)."""
    verb = "terminate (keep as history)" if mode == "TERMINATE" else "permanently delete"
    ids = ", ".join(f"id {r.id}" for r in sorted(active, key=lambda r: r.id)) or "?"
    lines = [f"This will replace feature {objectid} ({ids}) in {where}."]
    if len(active) > 1:
        lines.append(f"{len(active)} active rows share this objectid: {versions_label(active)}.")
        lines.append(f"citydb-tool will {verb} ALL of them; only the exported version stays active.")
    else:
        lines.append(f"The current row will be {'terminated' if mode == 'TERMINATE' else 'deleted'}; "
                     "the exported model becomes a new row.")
    return lines


def new_active_row(before_ids: Iterable[int], after: Sequence[CatalogRow]) -> Optional[CatalogRow]:
    """The row the update inserted: active now, not active before (newest wins)."""
    before = set(int(i) for i in before_ids)
    fresh = [r for r in after if r.id not in before]
    return max(fresh, key=lambda r: r.id) if fresh else None


# --------------------------------------------------------------------------- eligibility
def eligibility(props: Mapping[str, object], scene_key: str) -> Tuple[bool, str]:
    """``(ok, reason)`` for the update button of a root object's custom properties."""
    if props.get("cgml3_source_kind") != "citydb":
        return False, "Not loaded from 3DCityDB (load it with DB Browse first)"
    fid = props.get("cgml3_db_feature_id")
    if fid in (None, ""):
        return False, "No 3DCityDB feature id on this object"
    try:
        validate_objectid(str(props.get("cgml3_objectid") or ""))
    except InvalidId:
        return False, "No objectid on this object"
    if scene_key and props.get("cgml3_source_path") != scene_key:
        return False, "Loaded from another 3DCityDB city model than the one picked in this scene"
    return True, ""


# --------------------------------------------------------------------------- export check
_GML_ID = "{http://www.opengis.net/gml/3.2}id"
_GML_ID_31 = "{http://www.opengis.net/gml}id"


def inspect_export(path: str) -> Tuple[List[str], List[str]]:
    """``(gml:ids of the top-level members, internal generic attribute names)`` of an export."""
    members: List[str] = []
    internal: List[str] = []
    depth = 0
    member_depth = -1
    name_next = False
    for event, el in ET.iterparse(path, events=("start", "end")):
        local = el.tag.rsplit("}", 1)[-1]
        if event == "start":
            depth += 1
            if local in ("cityObjectMember", "featureMember") and member_depth < 0:
                member_depth = depth
            elif member_depth > 0 and depth == member_depth + 1:
                members.append(el.get(_GML_ID) or el.get(_GML_ID_31) or "")
            name_next = local == "name" and el.tag.startswith("{http://www.opengis.net/citygml/generics")
            continue
        if name_next and (el.text or "").strip().startswith(INTERNAL_PREFIX):
            internal.append(el.text.strip())
        name_next = False
        if depth == member_depth:
            member_depth = -1
        depth -= 1
        if depth > 2:
            continue
        el.clear()
    return members, sorted(set(internal))


def export_problem(path: str, objectid: str) -> Optional[str]:
    """Why the temporary export cannot be written back, or None."""
    try:
        members, internal = inspect_export(path)
    except (OSError, ET.ParseError) as exc:
        return f"The exported file could not be read: {exc}"
    if len(members) != 1:
        return f"The export holds {len(members)} top-level features instead of 1"
    if members[0] != objectid:
        return f"The exported gml:id {members[0]!r} differs from the objectid {objectid!r}"
    if internal:
        return "The export carries add-on properties as generic attributes: " + ", ".join(internal[:5])
    return None
