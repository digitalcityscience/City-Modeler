# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/fragment.py
"""Small CityGML made of selected features, cut by byte range (T08, no bpy).

A fragment is: the bytes before the root element (XML declaration), the root
start tag (all namespace declarations), a CityModel envelope, the selected
members and the CityModel-level appearances that target them (file order),
and the root end tag. Only those byte ranges of the source are read.

CityGML 1.0 is not known to the importers. Its namespaces are rewritten to
the 2.0 ones (2026-10-23 decision, verified on Hamburg LoD2 data); the same
rewrite turns a small 1.0 file into a 2.0 copy for ``Load all``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from .gml_index import FeatureIndexEntry, FileIndex
# CityGML 1.0 -> 2.0 namespaces live in one module (T11c).
from ..citygml_upgrade import (  # noqa: F401  (re-exported)
    NAMESPACES_1_TO_2, translate_1_to_2, untranslated_1_0_namespaces)

_MAX_PATTERN = max(len(a) for a, _ in NAMESPACES_1_TO_2)
_IMAGE_URI = re.compile(rb"(<(?:[A-Za-z_][\w.-]*:)?imageURI(?:\s[^>]*)?>)\s*([^<]*?)\s*(</)")
GML_NS_BY_VERSION = {"3.0": "http://www.opengis.net/gml/3.2"}
GML_NS_DEFAULT = "http://www.opengis.net/gml"


@dataclass
class Fragment:
    data: bytes
    bytes_read: int
    member_count: int
    appearance_count: int
    untranslated_namespaces: List[str] = field(default_factory=list)


def _absolute_image_uris(data: bytes, base_dir: str) -> bytes:
    """Make relative texture paths absolute: the fragment lives in a temp folder."""

    def repl(m):
        uri = m.group(2).decode("utf-8", "replace")
        if not uri or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", uri) or os.path.isabs(uri):
            return m.group(0)
        path = os.path.normpath(os.path.join(base_dir, uri.replace("\\", "/")))
        esc = path.replace("&", "&amp;").replace("<", "&lt;").encode("utf-8")
        return m.group(1) + esc + m.group(3)

    return _IMAGE_URI.sub(repl, data)


def _fmt(v: float) -> str:
    return repr(float(v))


def envelope_bounds(entries: Sequence[FeatureIndexEntry], origin: Optional[Sequence[float]] = None):
    """``(mins, maxs)`` of a fragment's CityModel envelope.

    Without ``origin`` it is the union of the entry bboxes. With ``origin``
    (the browse session origin) the envelope is widened to be centred on its
    X/Y and starts at its Z: the importers derive a new scene origin from the
    envelope centre (X/Y rounded, Z = min Z) whenever the scene CRS does not
    match the file - e.g. files without srsName - and this keeps that origin
    equal to the session origin for every fragment.
    """
    boxes = [e.bbox for e in entries if e.bbox]
    if not boxes:
        return None
    mins = [min(b[k] for b in boxes) for k in range(3)]
    maxs = [max(b[k + 3] for b in boxes) for k in range(3)]
    if origin is not None:
        for k in range(2):
            half = max(abs(mins[k] - origin[k]), abs(maxs[k] - origin[k]))
            mins[k], maxs[k] = origin[k] - half, origin[k] + half
        oz = float(origin[2]) if len(origin) > 2 else 0.0
        mins[2] = oz
        maxs[2] = max(maxs[2], oz)
    return tuple(mins), tuple(maxs)


def envelope_xml(srs_name: str, version: str, bounds) -> bytes:
    """CityModel ``gml:boundedBy`` for ``bounds``.

    The namespace is declared on the element itself, the root may bind
    ``gml`` elsewhere or to another prefix.
    """
    if bounds is None:
        return b""
    gml_ns = GML_NS_BY_VERSION.get(version, GML_NS_DEFAULT)
    mins, maxs = bounds
    srs = f' srsName="{srs_name}"' if srs_name else ""
    text = (
        f'<gml:boundedBy xmlns:gml="{gml_ns}"><gml:Envelope{srs} srsDimension="3">'
        f"<gml:lowerCorner>{' '.join(_fmt(v) for v in mins)}</gml:lowerCorner>"
        f"<gml:upperCorner>{' '.join(_fmt(v) for v in maxs)}</gml:upperCorner>"
        f"</gml:Envelope></gml:boundedBy>"
    )
    return text.encode("utf-8")


def appearance_ranges_for(index: FileIndex, feature_indices: Iterable[int]) -> List[Tuple[int, int]]:
    wanted = set(feature_indices)
    return [(a.start, a.end) for a in index.appearances if wanted.intersection(a.feature_indices)]


def build_fragment(
    index: FileIndex,
    feature_indices: Sequence[int],
    *,
    origin: Optional[Sequence[float]] = None,
    translate: Optional[bool] = None,
) -> Fragment:
    """Cut ``feature_indices`` of ``index`` into a stand-alone document.

    ``origin`` is the scene origin the fragment must keep (see
    :func:`envelope_bounds`). ``translate`` defaults to True for CityGML 1.0.
    """
    if translate is None:
        translate = index.version == "1.0"
    entries: List[FeatureIndexEntry] = [index.entries[i] for i in feature_indices]
    ranges = [(e.start, e.end) for e in entries]
    app_ranges = appearance_ranges_for(index, feature_indices)
    parts: List[bytes] = []
    read = 0
    with open(index.path, "rb") as fh:
        head = fh.read(index.root_open_end)
        read += len(head)
        if len(head) != index.root_open_end:
            raise ValueError("source file is shorter than its index")
        parts.append(head)
        parts.append(b"\n")
        parts.append(envelope_xml(index.srs_name, "2.0" if translate else index.version,
                                  envelope_bounds(entries, origin)))
        for start, end in sorted(set(ranges) | set(app_ranges)):
            fh.seek(start)
            chunk = fh.read(end - start)
            read += len(chunk)
            parts.append(b"\n")
            parts.append(chunk)
    root_name = index.root_name.encode("utf-8")
    parts.append(b"\n</" + root_name + b">\n")
    data = b"".join(parts)
    if app_ranges:
        data = _absolute_image_uris(data, os.path.dirname(index.path))
    untranslated: List[str] = []
    if translate:
        data = translate_1_to_2(data)
        untranslated = untranslated_1_0_namespaces(data)
    return Fragment(data=data, bytes_read=read, member_count=len(entries),
                    appearance_count=len(app_ranges), untranslated_namespaces=untranslated)


def write_translated_copy(src: str, dst: str, chunk_bytes: int = 1 << 20) -> List[str]:
    """Stream ``src`` (CityGML 1.0) to ``dst`` with 2.0 namespaces.

    Relative texture paths are made absolute (``dst`` is a temp file).
    Returns the 1.0 namespaces that have no 2.0 mapping. Every chunk is cut
    after its last line break so that namespaces and ``imageURI`` elements
    are not split; a chunk without line break keeps only the bytes a
    namespace could span.
    """
    base_dir = os.path.dirname(os.path.abspath(src))
    untranslated = set()
    carry = b""
    tail = [b""]  # end of the previous output: detection across chunk borders

    def emit(fout, data: bytes):
        out = _absolute_image_uris(translate_1_to_2(data), base_dir)
        untranslated.update(untranslated_1_0_namespaces(tail[0] + out))
        tail[0] = (tail[0] + out)[-(_MAX_PATTERN + 16):]
        fout.write(out)

    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            chunk = fin.read(chunk_bytes)
            data = carry + chunk
            if not chunk:
                emit(fout, data)
                break
            cut = data.rfind(b"\n") + 1
            if cut <= 0:
                # no line break: translate everything, hold back what could be
                # the start of a namespace (translated text is not touched again)
                data = translate_1_to_2(data)
                cut = max(0, len(data) - (_MAX_PATTERN - 1))
            emit(fout, data[:cut])
            carry = data[cut:]
    return sorted(untranslated)
