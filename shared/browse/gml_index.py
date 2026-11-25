# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/gml_index.py
"""Feature index of a CityGML file in one expat pass (T08, pure Python, no bpy).

For every top-level ``cityObjectMember`` / ``featureMember`` the index keeps:

- id, type and ``gml:name``;
- the bbox, computed from the feature's own coordinates (``posList``, ``pos``,
  ``coordinates``; ``srsDimension`` is inherited from the enclosing
  geometry). The feature's ``gml:boundedBy`` envelope is only read to check
  it: a difference above 1 cm sets ``envelope_mismatch``;
- the LoD property names (``lod2Solid``, ``lod2MultiSurface``, ...) and, when
  the feature has real LoD0 geometry, its exterior rings in 2D;
- the byte range ``[start, end)`` of the member element in the file;
- risk flags: references to ids outside the feature, ImplicitGeometry, an
  ``srsName`` that differs from the file CRS.

At file level: the root start tag (its namespace declarations are needed to
cut fragments), the CityModel envelope and CRS, and the byte ranges of the
CityModel-level ``appearanceMember`` elements with the features they target.

The file is read in 1 MB chunks and never held in memory as a whole.
"""

from __future__ import annotations

import os
import re
import time
import xml.parsers.expat
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

INDEX_FORMAT = 2  # 2: GroundSurface footprint rings (B14)
CHUNK_BYTES = 1 << 20
ENVELOPE_TOLERANCE_M = 0.01

GML_NAMESPACES = frozenset({"http://www.opengis.net/gml", "http://www.opengis.net/gml/3.2"})
XLINK_HREF = "http://www.w3.org/1999/xlink}href"
GML_ID_SUFFIX = "}id"
MEMBER_TAGS = frozenset({"cityObjectMember", "featureMember"})
APPEARANCE_MEMBER_TAG = "appearanceMember"
COORD_TAGS = frozenset({"posList", "pos", "coordinates"})
LOD0_GEOMETRY_PROPS = frozenset({"lod0MultiSurface", "lod0FootPrint", "lod0RoofEdge"})
APPEARANCE_TARGET_TAGS = frozenset({"target", "ring"})
# Single-byte encodings that keep ASCII markup at its byte position.
ASCII_COMPATIBLE_ENCODINGS = ("utf-8", "utf8", "us-ascii", "ascii", "iso-8859-", "latin", "windows-125", "cp125")

_LOD_PROP = re.compile(r"^lod(\d)[A-Z]")
_TAG_END = re.compile(rb"""(?:[^>"']|"[^"]*"|'[^']*')*>""")
_XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")
_EPSG_CODE = re.compile(r"EPSG(?::|::|/0/|\.xml#)(\d+)", re.IGNORECASE)

ProgressFn = Callable[[int, int, int], None]
BatchFn = Callable[[List["FeatureIndexEntry"], "FileIndex"], None]
CancelFn = Callable[[], bool]


class GmlIndexError(Exception):
    """The file cannot be browsed (encoding, not CityGML, broken XML)."""


class IndexCancelled(Exception):
    """The caller asked to stop."""


@dataclass
class FeatureIndexEntry:
    gml_id: str
    feature_type: str
    name: str = ""
    start: int = 0
    end: int = 0
    # (minx, miny, minz, maxx, maxy, maxz) from the feature's coordinates
    bbox: Optional[Tuple[float, float, float, float, float, float]] = None
    envelope_mismatch: bool = False
    has_envelope: bool = False
    lods: Tuple[str, ...] = ()
    # exterior rings of real LoD0 geometry, flat [x0, y0, x1, y1, ...]
    lod0: List[List[float]] = field(default_factory=list)
    external_xlink: int = 0
    implicit_geometry: bool = False
    srs_mismatch: bool = False
    # exterior rings of the GroundSurface polygons (2D preview when there is no LoD0)
    footprint: List[List[float]] = field(default_factory=list)
    # CityJSON (CJ-05): the LoD texts as written in the file ("1.2", "2.2"); empty for GML and DB
    lod_texts: Tuple[str, ...] = ()

    @property
    def lod_levels(self) -> Tuple[int, ...]:
        """LoD levels with displayable geometry (terrain intersection curves excluded)."""
        levels = set()
        for prop in self.lods:
            m = _LOD_PROP.match(prop)
            if m and "TerrainIntersection" not in prop:
                levels.add(int(m.group(1)))
        return tuple(sorted(levels))

    @property
    def has_lod0(self) -> bool:
        return bool(self.lod0)

    def risks(self) -> List[str]:
        out = []
        if self.bbox is None:
            out.append("no coordinates")
        if self.external_xlink:
            out.append("geometry referenced outside the feature")
        if self.implicit_geometry:
            out.append("implicit geometry")
        if self.srs_mismatch:
            out.append("srsName differs from the file CRS")
        if self.envelope_mismatch:
            out.append("envelope differs from geometry")
        return out

    @property
    def risky(self) -> bool:
        return bool(self.bbox is None or self.external_xlink or self.implicit_geometry or self.srs_mismatch)

    def to_list(self) -> list:
        return [self.gml_id, self.feature_type, self.name, self.start, self.end,
                list(self.bbox) if self.bbox else None, int(self.envelope_mismatch),
                int(self.has_envelope), list(self.lods), self.lod0, self.external_xlink,
                int(self.implicit_geometry), int(self.srs_mismatch), self.footprint, list(self.lod_texts)]

    @classmethod
    def from_list(cls, v: Sequence) -> "FeatureIndexEntry":
        return cls(gml_id=v[0], feature_type=v[1], name=v[2], start=int(v[3]), end=int(v[4]),
                   bbox=tuple(v[5]) if v[5] else None, envelope_mismatch=bool(v[6]),
                   has_envelope=bool(v[7]), lods=tuple(v[8]), lod0=[list(r) for r in v[9]],
                   external_xlink=int(v[10]), implicit_geometry=bool(v[11]), srs_mismatch=bool(v[12]),
                   footprint=[list(r) for r in v[13]] if len(v) > 13 else [],
                   lod_texts=tuple(v[14]) if len(v) > 14 else ())


@dataclass
class AppearanceRange:
    """A CityModel-level appearanceMember and the features it targets."""
    start: int
    end: int
    feature_indices: Tuple[int, ...] = ()


@dataclass
class FileIndex:
    path: str
    size: int = 0
    mtime_ns: int = 0
    version: str = "unknown"
    root_name: str = ""
    root_start: int = 0
    root_open_end: int = 0
    srs_name: str = ""
    envelope: Optional[Tuple[Tuple[float, ...], Tuple[float, ...]]] = None
    entries: List[FeatureIndexEntry] = field(default_factory=list)
    appearances: List[AppearanceRange] = field(default_factory=list)
    skipped_members: int = 0
    seconds: float = 0.0
    # CityJSON (CJ-05): what is not in the index, by kind ("WaterBody", "unreadable line")
    skipped_kinds: Dict[str, int] = field(default_factory=dict)
    # CityJSON (CJ-05): the parsed single-document file, kept for Load; never cached
    document: Optional[dict] = field(default=None, repr=False, compare=False)

    @property
    def has_lod0(self) -> bool:
        return any(e.lod0 for e in self.entries)

    def bounds(self) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
        """Union of the feature bboxes as ``(mins, maxs)``."""
        boxes = [e.bbox for e in self.entries if e.bbox]
        if not boxes:
            return None
        mins = tuple(min(b[k] for b in boxes) for k in range(3))
        maxs = tuple(max(b[k + 3] for b in boxes) for k in range(3))
        return mins, maxs

    def risk_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for e in self.entries:
            for r in e.risks():
                counts[r] = counts.get(r, 0) + 1
        return counts

    def to_dict(self, entries: bool = True) -> dict:
        d = {
            "format": INDEX_FORMAT,
            "path": self.path, "size": self.size, "mtime_ns": self.mtime_ns,
            "version": self.version, "root_name": self.root_name,
            "root_start": self.root_start, "root_open_end": self.root_open_end,
            "srs_name": self.srs_name,
            "envelope": [list(self.envelope[0]), list(self.envelope[1])] if self.envelope else None,
            "skipped_members": self.skipped_members, "seconds": self.seconds,
            "skipped_kinds": dict(self.skipped_kinds),
            "appearances": [[a.start, a.end, list(a.feature_indices)] for a in self.appearances],
        }
        if entries:
            d["entries"] = [e.to_list() for e in self.entries]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FileIndex":
        if d.get("format") != INDEX_FORMAT:
            raise ValueError(f"index format {d.get('format')} != {INDEX_FORMAT}")
        env = d.get("envelope")
        return cls(
            path=d["path"], size=int(d["size"]), mtime_ns=int(d["mtime_ns"]),
            version=d["version"], root_name=d["root_name"],
            root_start=int(d["root_start"]), root_open_end=int(d["root_open_end"]),
            srs_name=d.get("srs_name", ""),
            envelope=(tuple(env[0]), tuple(env[1])) if env else None,
            skipped_members=int(d.get("skipped_members", 0)), seconds=float(d.get("seconds", 0.0)),
            skipped_kinds=dict(d.get("skipped_kinds") or {}),
            entries=[FeatureIndexEntry.from_list(v) for v in d["entries"]],
            appearances=[AppearanceRange(int(a[0]), int(a[1]), tuple(a[2])) for a in d["appearances"]],
        )


# --------------------------------------------------------------------------- helpers
def normalize_srs(srs: str) -> str:
    """Comparable form of an srsName: ``EPSG:<code>`` when one is found, else the lowercase text."""
    s = (srs or "").strip()
    m = _EPSG_CODE.search(s)
    if m:
        return f"EPSG:{m.group(1)}"
    return s.lower()


def citygml_version_from_namespaces(uris) -> str:
    """'1.0', '2.0', '3.0' or 'unknown' from declared namespace URIs (core first)."""
    uris = [u for u in uris if u]
    for v in ("3.0", "2.0", "1.0"):
        if f"http://www.opengis.net/citygml/{v}" in uris:
            return v
    for v in ("3.0", "2.0", "1.0"):
        if any(u.startswith("http://www.opengis.net/citygml/") and u.endswith("/" + v) for u in uris):
            return v
    return "unknown"


def check_encoding(head: bytes) -> str:
    """Encoding from the XML declaration; raises for encodings that break byte offsets."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or head[:4] in (b"<\x00?\x00", b"\x00<\x00?"):
        raise GmlIndexError("UTF-16 files cannot be browsed; convert the file to UTF-8 first")
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    m = _XML_ENCODING.match(head)
    enc = m.group(1).decode("ascii").lower() if m else "utf-8"
    if not enc.startswith(ASCII_COMPATIBLE_ENCODINGS):
        raise GmlIndexError(f"Encoding {enc} cannot be browsed; convert the file to UTF-8 first")
    return enc


def tag_end(buf: bytes, pos: int) -> int:
    """Offset just past the ``>`` that closes the tag starting at ``pos`` (quote aware)."""
    m = _TAG_END.match(buf, pos)
    if not m:
        raise GmlIndexError(f"unterminated tag at byte {pos}")
    return m.end()


def _qname_at(buf: bytes, pos: int) -> str:
    """Raw qualified name of the tag starting with ``<`` at ``pos``."""
    m = re.compile(rb"<([^\s/>]+)").match(buf, pos)
    if not m:
        raise GmlIndexError(f"no tag name at byte {pos}")
    return m.group(1).decode("utf-8")


def _coord_values(text: str, local: str, cs: str, ts: str) -> List[str]:
    if local == "coordinates":
        out: List[str] = []
        for tup in (text.split(ts) if ts and not ts.isspace() else text.split()):
            out.extend(v for v in tup.split(cs or ",") if v.strip())
        return out
    return text.split()


def _effective_dim(n: int, dim: int) -> int:
    if dim not in (2, 3):
        dim = 3
    if n % dim:
        if n % 3 == 0:
            return 3
        if n % 2 == 0:
            return 2
    return dim


class _Feature:
    __slots__ = ("start", "gml_id", "ftype", "name", "mins", "maxs", "found",
                 "env_lower", "env_upper", "lods", "lod0", "footprint", "ring", "ids", "hrefs",
                 "implicit", "srs")

    def __init__(self, start):
        self.start = start
        self.gml_id = ""
        self.ftype = ""
        self.name = None
        self.mins = [float("inf")] * 3
        self.maxs = [float("-inf")] * 3
        self.found = False
        self.env_lower = None
        self.env_upper = None
        self.lods = set()
        self.lod0 = []
        self.footprint = []
        self.ring = None
        self.ids = set()
        self.hrefs = set()
        self.implicit = False
        self.srs = set()

    def add_coords(self, vals: List[str], dim: int) -> Optional[List[float]]:
        n = len(vals)
        if not n:
            return None
        d = _effective_dim(n, dim)
        usable = n - n % d
        if usable < d:
            return None
        try:
            for k in range(min(d, 3)):
                axis = [float(v) for v in vals[k:usable:d]]
                lo, hi = min(axis), max(axis)
                if lo < self.mins[k]:
                    self.mins[k] = lo
                if hi > self.maxs[k]:
                    self.maxs[k] = hi
        except ValueError:
            return None
        if d == 2:
            # 2D geometry: Z unknown, keep 0 so the box stays flat
            if 0.0 < self.mins[2]:
                self.mins[2] = 0.0
            if 0.0 > self.maxs[2]:
                self.maxs[2] = 0.0
        self.found = True
        if self.ring is not None:
            xy = []
            for i in range(0, usable, d):
                xy.append(float(vals[i]))
                xy.append(float(vals[i + 1]))
            return xy
        return None


def _envelope_mismatch(f: _Feature) -> bool:
    if not (f.found and f.env_lower and f.env_upper):
        return False
    dims = min(len(f.env_lower), len(f.env_upper), 3)
    for k in range(dims):
        if abs(f.env_lower[k] - f.mins[k]) > ENVELOPE_TOLERANCE_M:
            return True
        if abs(f.env_upper[k] - f.maxs[k]) > ENVELOPE_TOLERANCE_M:
            return True
    return False


# --------------------------------------------------------------------------- indexer
def index_file(
    path: str,
    *,
    progress: Optional[ProgressFn] = None,
    on_batch: Optional[BatchFn] = None,
    cancel: Optional[CancelFn] = None,
    chunk_bytes: int = CHUNK_BYTES,
) -> FileIndex:
    """Build the :class:`FileIndex` of ``path`` in one pass.

    ``progress(bytes_done, bytes_total, n_features)`` and ``on_batch(new_entries,
    partial_index)`` are called after every chunk (the partial index already
    has the root tag, version and CityModel envelope once they were read);
    ``cancel()`` returning True raises :class:`IndexCancelled`.
    """
    t0 = time.perf_counter()
    st = os.stat(path)
    idx = FileIndex(path=os.path.abspath(path), size=st.st_size, mtime_ns=st.st_mtime_ns)
    parser = xml.parsers.expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True

    entries = idx.entries
    pending: List[FeatureIndexEntry] = []
    ns_uris: List[str] = []
    id_owner: Dict[str, int] = {}
    app_targets: List[set] = []
    feature_srs: List[set] = []
    first_srs = [""]

    # parser state
    depth = 0
    dims = [3]
    cur: Optional[_Feature] = None
    cur_app: Optional[list] = None  # [start, targets]
    text: Optional[list] = None
    coord_ctx = ("", ",", " ")  # local name, cs, ts
    env_depth = 0        # depth of the boundedBy being read (0 = none)
    env_corners: dict = {}
    lod0_depth = 0       # depth of the lod0 geometry property (0 = none)
    exterior_depth = 0   # depth of the gml:exterior inside it
    ground_depth = 0     # depth of the GroundSurface being read (0 = none)
    name_pending = False
    implicit_depth = 0
    refpoint_depth = 0
    target_text: Optional[list] = None

    # byte buffer to locate tag ends: previous and current chunk
    buf = b""
    buf_off = 0

    def bytes_at(pos: int) -> Tuple[bytes, int]:
        if pos >= buf_off:
            return buf, pos - buf_off
        with open(path, "rb") as fh:
            fh.seek(pos)
            return fh.read(1 << 20), 0

    def start(name, attrs):
        nonlocal depth, cur, cur_app, text, coord_ctx, env_depth, env_corners, lod0_depth
        nonlocal exterior_depth, name_pending, implicit_depth, refpoint_depth, target_text, ground_depth
        depth += 1
        ns, _, local = name.rpartition("}")
        dim = attrs.get("srsDimension")
        if dim is not None:
            try:
                dims.append(int(dim))
            except ValueError:
                dims.append(dims[-1])
        else:
            dims.append(dims[-1])
        srs = attrs.get("srsName")
        if srs and not first_srs[0]:
            first_srs[0] = srs

        if depth == 1:
            b, rel = bytes_at(parser.CurrentByteIndex)
            idx.root_start = parser.CurrentByteIndex
            idx.root_name = _qname_at(b, rel)
            idx.root_open_end = idx.root_start + (tag_end(b, rel) - rel)
            idx.version = citygml_version_from_namespaces(ns_uris + [ns])
            return
        if depth == 2:
            if local in MEMBER_TAGS:
                cur = _Feature(parser.CurrentByteIndex)
            elif local == APPEARANCE_MEMBER_TAG:
                cur_app = [parser.CurrentByteIndex, set()]
            elif local == "boundedBy" and ns in GML_NAMESPACES:
                env_depth = depth
                env_corners = {}
                if srs:
                    idx.srs_name = srs
            return
        if depth == 3 and local == "Envelope" and env_depth and cur is None and srs:
            idx.srs_name = srs

        if cur_app is not None:
            href = attrs.get(XLINK_HREF)
            if href:
                cur_app[1].add(href.lstrip("#"))
            if local in APPEARANCE_TARGET_TAGS:
                uri = attrs.get("uri")
                if uri:
                    cur_app[1].add(uri.lstrip("#"))
                target_text = []
            return

        f = cur
        if f is None:
            if env_depth and local in ("lowerCorner", "upperCorner"):
                text = []
                coord_ctx = (local, ",", " ")
            return
        if depth == 3:
            f.ftype = local
            for k, v in attrs.items():
                if k.endswith(GML_ID_SUFFIX):
                    f.gml_id = v
                    break
        for k, v in attrs.items():
            if k.endswith(GML_ID_SUFFIX):
                f.ids.add(v)
            elif k == XLINK_HREF and v.startswith("#"):
                f.hrefs.add(v[1:])
        if srs:
            f.srs.add(srs)
        if depth == 4:
            if local == "boundedBy" and ns in GML_NAMESPACES:
                env_depth = depth
                env_corners = {}
            elif local == "name" and ns in GML_NAMESPACES and f.name is None:
                name_pending = True
                text = []
                return
        if local.startswith("lod") and _LOD_PROP.match(local):
            f.lods.add(local)
            if local in LOD0_GEOMETRY_PROPS and not lod0_depth:
                lod0_depth = depth
        elif local == "ImplicitGeometry":
            f.implicit = True
            implicit_depth = implicit_depth or depth
        elif local == "referencePoint" and implicit_depth:
            refpoint_depth = depth
        elif local == "GroundSurface" and not ground_depth:
            ground_depth = depth
        elif local == "exterior" and (lod0_depth or ground_depth) and ns in GML_NAMESPACES and not exterior_depth:
            exterior_depth = depth
            f.ring = []
        if ns in GML_NAMESPACES:
            if local in COORD_TAGS:
                text = []
                coord_ctx = (local, attrs.get("cs", ","), attrs.get("ts", " "))
            elif env_depth and local in ("lowerCorner", "upperCorner"):
                text = []
                coord_ctx = (local, ",", " ")

    def chars(data):
        if text is not None:
            text.append(data)
        elif target_text is not None:
            target_text.append(data)

    def end(name):
        nonlocal depth, cur, cur_app, text, env_depth, lod0_depth, exterior_depth
        nonlocal name_pending, implicit_depth, refpoint_depth, target_text, ground_depth
        ns, _, local = name.rpartition("}")
        dim = dims.pop()
        f = cur
        if text is not None and (local == coord_ctx[0] or (name_pending and local == "name")):
            joined = "".join(text)
            text = None
            if name_pending:
                name_pending = False
                if f is not None:
                    f.name = joined.strip()
            elif env_depth and local in ("lowerCorner", "upperCorner"):
                try:
                    env_corners[local] = tuple(float(v) for v in joined.split())
                except ValueError:
                    pass
            # implicit geometry: only the reference point is in world coordinates
            elif f is not None and not (implicit_depth and not refpoint_depth):
                xy = f.add_coords(_coord_values(joined, local, coord_ctx[1], coord_ctx[2]), dim)
                if xy and f.ring is not None:
                    f.ring.extend(xy)
        if target_text is not None and local in APPEARANCE_TARGET_TAGS:
            val = "".join(target_text).strip()
            if val and cur_app is not None:
                cur_app[1].add(val.lstrip("#"))
            target_text = None

        if env_depth and depth == env_depth:
            lower, upper = env_corners.get("lowerCorner"), env_corners.get("upperCorner")
            if f is not None:
                f.env_lower, f.env_upper = lower, upper
            elif lower and upper and depth == 2:
                idx.envelope = (lower, upper)
            env_depth = 0
        if exterior_depth and depth == exterior_depth:
            if f is not None and f.ring and len(f.ring) >= 6:
                (f.lod0 if lod0_depth else f.footprint).append(f.ring)
            if f is not None:
                f.ring = None
            exterior_depth = 0
        if lod0_depth and depth == lod0_depth:
            lod0_depth = 0
        if ground_depth and depth == ground_depth:
            ground_depth = 0
        if refpoint_depth and depth == refpoint_depth:
            refpoint_depth = 0
        if implicit_depth and depth == implicit_depth:
            implicit_depth = 0

        if depth == 2:
            if f is not None:
                b, rel = bytes_at(parser.CurrentByteIndex)
                stop = parser.CurrentByteIndex + (tag_end(b, rel) - rel)
                _finish_feature(f, stop)
                cur = None
            elif cur_app is not None:
                b, rel = bytes_at(parser.CurrentByteIndex)
                stop = parser.CurrentByteIndex + (tag_end(b, rel) - rel)
                idx.appearances.append(AppearanceRange(cur_app[0], stop))
                app_targets.append(cur_app[1])
                cur_app = None
        depth -= 1

    def _finish_feature(f: _Feature, stop: int):
        if not f.ftype:
            idx.skipped_members += 1
            return
        entry = FeatureIndexEntry(
            gml_id=f.gml_id, feature_type=f.ftype, name=f.name or "",
            start=f.start, end=stop,
            bbox=(tuple(f.mins) + tuple(f.maxs)) if f.found else None,
            envelope_mismatch=_envelope_mismatch(f),
            has_envelope=bool(f.env_lower and f.env_upper),
            lods=tuple(sorted(f.lods)), lod0=f.lod0, footprint=f.footprint,
            external_xlink=len(f.hrefs - f.ids),
            implicit_geometry=f.implicit,
        )
        n = len(entries)
        for i in f.ids:
            id_owner[i] = n
        entries.append(entry)
        feature_srs.append(f.srs)
        pending.append(entry)

    def ns_decl(prefix, uri):
        if uri:
            ns_uris.append(uri)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartNamespaceDeclHandler = ns_decl

    done = 0
    with open(path, "rb") as fh:
        first = True
        while True:
            if cancel is not None and cancel():
                raise IndexCancelled()
            chunk = fh.read(chunk_bytes)
            if first:
                check_encoding(chunk[:512])
                first = False
            # keep the previous chunk so tag ends that started there can be found
            last = buf[-chunk_bytes:] if buf else b""
            buf_off = done - len(last)
            buf = last + chunk
            try:
                parser.Parse(chunk, not chunk)
            except xml.parsers.expat.ExpatError as exc:
                raise GmlIndexError(f"XML error: {exc}") from exc
            done += len(chunk)
            if progress is not None:
                progress(done, idx.size, len(entries))
            if on_batch is not None and pending:
                on_batch(list(pending), idx)
            pending.clear()
            if not chunk:
                break

    if idx.version == "unknown":
        idx.version = citygml_version_from_namespaces(ns_uris)
    if not idx.srs_name:
        idx.srs_name = first_srs[0]
    file_srs = normalize_srs(idx.srs_name) if idx.srs_name else ""
    if file_srs:
        for entry, names in zip(entries, feature_srs):
            if any(normalize_srs(s) != file_srs for s in names):
                entry.srs_mismatch = True
    for app, targets in zip(idx.appearances, app_targets):
        app.feature_indices = tuple(sorted({id_owner[t] for t in targets if t in id_owner}))
    idx.seconds = round(time.perf_counter() - t0, 3)
    return idx
