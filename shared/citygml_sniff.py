# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/citygml_sniff.py
"""Cheap header check whether a file is CityGML (pure Python, no bpy).

:func:`sniff_file` also summarises a file without parsing it: version, CRS,
size and an estimated number of top-level features (member tags counted in
three 64 KiB samples). The browse-first decision (T08) uses it.

CJ-02: a file whose content starts with ``{`` is JSON; its ``"type"`` says CityJSON or
CityJSONFeature (CityJSONSeq), whatever the extension. The whole file is scanned with a
regex (no JSON parse) for the Building count and the real LoD texts ("1.2", "2.2").
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

SNIFF_BYTES = 64 * 1024
CITYGML_NAMESPACE_PREFIX = b"http://www.opengis.net/citygml"

# Default browse-first thresholds (preferences can change them)
BROWSE_THRESHOLD_MB = 20.0
BROWSE_THRESHOLD_FEATURES = 30

_MEMBER = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?(?:cityObjectMember|featureMember)[\s>/]")
_SRS = re.compile(rb'srsName\s*=\s*["\']([^"\']+)["\']')
_CITYGML_URI = re.compile(r"http://www\.opengis\.net/citygml/(?:[A-Za-z]+/)?(1\.0|2\.0|3\.0)(?![\w.])")
_CITYGML_PATH = re.compile(r"citygml/(?:[A-Za-z]+/)?(1\.0|2\.0|3\.0)(?![\w.])", re.IGNORECASE)
_OPENGIS_URI = re.compile(rb"http://www\.opengis\.net/[^\s\"'<>]+")
_SCHEMA_LOCATION = re.compile(rb"schemaLocation\s*=\s*[\"']([^\"']*)[\"']")
_GML_NS_DECL = re.compile(rb"xmlns:gml\s*=\s*[\"'](http://www\.opengis\.net/gml(?:/3\.2)?)[\"']")
_CITYMODEL = re.compile(rb"<(?:[A-Za-z_][\w.-]*:)?CityModel[\s>/]")
_XSI_SCHEMA_LOCATION = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"

# CityJSON: top-level and object keys, read token by token (strings or bare numbers)
_JSON_TOKEN = re.compile(rb'"(type|lod|referenceSystem|version)"\s*:\s*("[^"]*"|[0-9.]+)')
_LOD_TEXT = re.compile(r"\d+(?:\.\d+)?")
# top-level types cityjson_reader reads (CJ-07 added the last four); kept here: the sniff imports nothing
_JSON_FEATURES = ("Building", "Road", "Railway", "TransportSquare", "CityFurniture")
JSON_SCAN_MAX_BYTES = 200 * 2**20     # LOAD_ALL_MAX_MB: a bigger file is refused before it is read
_JSON_CHUNK = 8 * 2**20
_JSON_OVERLAP = 1024                  # a token cut by a chunk boundary is read whole in the next one

VERSION_UNKNOWN = "unknown"
GML_31 = "http://www.opengis.net/gml"
GML_32 = "http://www.opengis.net/gml/3.2"
HEADER_BYTES = 256 * 1024


def _newest(texts, pattern=_CITYGML_URI) -> str:
    found = {m.group(1) for t in texts for m in pattern.finditer(t)}
    for version in ("3.0", "2.0", "1.0"):
        if version in found:
            return version
    return VERSION_UNKNOWN


def classify_version(namespaces=(), schema_location: str = "", gml_namespace: str = "") -> str:
    """The one CityGML version rule: ``"1.0" | "2.0" | "3.0" | "unknown"``.

    1. A CityGML namespace URI of the document (core or module, declared or used
       anywhere); 3.0 wins over 2.0 over 1.0.
    2. ``xsi:schemaLocation``.
    3. ``gml_namespace`` (pass it only for a ``CityModel`` root): GML 3.2 -> 3.0,
       GML 3.1 -> 2.0.
    """
    found = _newest(namespaces)
    if found == VERSION_UNKNOWN:
        found = _newest([schema_location or ""], _CITYGML_PATH)
    if found == VERSION_UNKNOWN:
        found = {GML_32: "3.0", GML_31: "2.0"}.get(gml_namespace, VERSION_UNKNOWN)
    return found


def version_from_bytes(head: bytes) -> str:
    """:func:`classify_version` on raw XML text: no parse, tolerates BOM and broken XML."""
    namespaces = [m.group(0).decode("ascii", "replace") for m in _OPENGIS_URI.finditer(head)]
    schema_location = " ".join(loc.decode("utf-8", "replace") for loc in _SCHEMA_LOCATION.findall(head))
    gml = _GML_NS_DECL.search(head)
    return classify_version(
        namespaces,
        schema_location,
        gml.group(1).decode("ascii") if gml and _CITYMODEL.search(head) else "",
    )


def version_from_root(root) -> str:
    """:func:`classify_version` on a parsed lxml or stdlib root element."""
    namespaces = {u for u in (getattr(root, "nsmap", None) or {}).values() if u}
    for elem in (root.iter() if hasattr(root, "iter") else (root,)):
        tag = getattr(elem, "tag", None)
        if isinstance(tag, str) and tag.startswith("{"):
            namespaces.add(tag[1:].split("}", 1)[0])
    gml = ""
    if str(root.tag).rsplit("}", 1)[-1] == "CityModel":
        gml = GML_32 if GML_32 in namespaces else GML_31 if GML_31 in namespaces else ""
    return classify_version(namespaces, root.get(_XSI_SCHEMA_LOCATION, "") or "", gml)


def version_from_file(path: str, max_bytes: int = HEADER_BYTES) -> str:
    """Version of a file from its header (no full parse, so cheap for huge files)."""
    with open(path, "rb") as fh:
        return version_from_bytes(fh.read(max_bytes))


def looks_like_citygml(path: str, max_bytes: int = SNIFF_BYTES) -> bool:
    """True when the first ``max_bytes`` contain a CityGML namespace."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(max_bytes)
    except OSError:
        return False
    return CITYGML_NAMESPACE_PREFIX in head.lower()


@dataclass
class SniffSummary:
    path: str
    size: int
    is_citygml: bool
    version: str = "unknown"      # '1.0', '2.0', '3.0'
    srs_name: str = ""
    features_estimate: int = 0
    exact: bool = False           # whole file was counted
    # 'CityGML', 'CityJSON', 'CityJSONSeq' (CityJSONFeature lines), 'JSON' (not CityJSON), '' (unknown)
    format: str = ""
    lods: tuple = ()              # CityJSON: the LoD texts of the file ("1.2", "2.2"), sorted

    @property
    def size_mb(self) -> float:
        return self.size / 2**20

    def label(self) -> str:
        """``CityGML 3.0 · EPSG:25832 · 412 MB · ~18,400 features``."""
        name = self.format if self.format in ("CityJSON", "CityJSONSeq", "JSON") else "CityGML"
        parts = [f"{name} {self.version}" if self.version != "unknown" else f"{name} (version unknown)"]
        if self.srs_name:
            parts.append(_short_srs(self.srs_name))
        parts.append(f"{self.size_mb:.0f} MB" if self.size_mb >= 10 else f"{self.size_mb:.1f} MB")
        n = self.features_estimate
        noun = "building" if name != "CityGML" else "feature"
        parts.append(f"{'' if self.exact else '~'}{n:,} {noun}{'s' if n != 1 else ''}")
        if self.lods:
            parts.append("LoD " + ", ".join(self.lods))
        return " · ".join(parts)


def _short_srs(srs: str) -> str:
    m = re.search(r"EPSG(?::|::|/0/)(\d+)", srs, re.IGNORECASE)
    if m:
        return f"EPSG:{m.group(1)}"
    # German ADV URNs (urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH): horizontal part
    m = re.search(r"ETRS89_UTM(3[1-3])", srs, re.IGNORECASE)
    if m:
        return f"EPSG:258{m.group(1)}"
    return srs.rsplit(":", 1)[-1] if srs.startswith("urn:") and len(srs) > 40 else srs


def _round_estimate(n: float) -> int:
    if n < 1000:
        return int(round(n))
    step = 100 if n < 100_000 else 1000
    return int(round(n / step) * step)


def sniff_file(path: str, sample_bytes: int = SNIFF_BYTES) -> SniffSummary:
    """Summary from at most three ``sample_bytes`` reads (start, 1/3, 2/3)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(sample_bytes)
        if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
            fh.seek(0)
            return _sniff_json(path, size, fh)
        samples = [head]
        exact = size <= 3 * sample_bytes
        if exact:
            samples = [head + fh.read()]
        else:
            for frac in (1 / 3, 2 / 3):
                fh.seek(int(size * frac))
                samples.append(fh.read(sample_bytes))
    summary = SniffSummary(path=path, size=size, is_citygml=CITYGML_NAMESPACE_PREFIX in head.lower(), exact=exact)
    summary.format = "CityGML" if summary.is_citygml else ""
    summary.version = version_from_bytes(head)
    m = _SRS.search(head)
    if m:
        summary.srs_name = m.group(1).decode("utf-8", "replace")
    count = sum(len(_MEMBER.findall(s)) for s in samples)
    if exact:
        summary.features_estimate = count
    else:
        scanned = sum(len(s) for s in samples)
        summary.features_estimate = _round_estimate(count * size / max(1, scanned))
    return summary


def _sniff_json(path: str, size: int, fh) -> SniffSummary:
    """CityJSON summary from a regex scan of the whole file (up to ``JSON_SCAN_MAX_BYTES``)."""
    exact = size <= JSON_SCAN_MAX_BYTES
    chunk = _JSON_CHUNK if exact else SNIFF_BYTES     # too large to open: the head names the format
    types, lods, first = set(), set(), {}
    buildings, buf, done = 0, b"", False
    while not done:
        data = fh.read(chunk)
        done = not data or not exact
        buf += data
        cut = len(buf) if done else max(0, len(buf) - _JSON_OVERLAP)
        for m in _JSON_TOKEN.finditer(buf):
            if m.start() >= cut:
                break
            key, value = m.group(1).decode("ascii"), m.group(2).strip(b'"').decode("utf-8", "replace")
            if key == "type":
                if value in _JSON_FEATURES:
                    buildings += 1
                elif value in ("CityJSON", "CityJSONFeature"):
                    types.add(value)
            elif key == "lod":
                if _LOD_TEXT.fullmatch(value):
                    lods.add(value)
            else:
                first.setdefault(key, value)
        buf = buf[cut:]
    # shortcut: the scan sees tokens, not the JSON tree; an attribute named "lod" or "type" would count too,
    # and so does the LoD of an object that is not a building. It feeds the Open dialog and the Browse-first
    # threshold; Browse first itself takes count and LoDs from the index (CJ-05, browse/cityjson_index)
    fmt = "CityJSONSeq" if "CityJSONFeature" in types else "CityJSON" if types else "JSON"
    return SniffSummary(path=path, size=size, is_citygml=False, version=first.get("version", VERSION_UNKNOWN),
                        srs_name=first.get("referenceSystem", ""), features_estimate=buildings, exact=exact,
                        format=fmt, lods=tuple(sorted(lods, key=lambda t: (float(t), t))))


def browse_recommended(
    summary: SniffSummary,
    threshold_mb: float = BROWSE_THRESHOLD_MB,
    threshold_features: int = BROWSE_THRESHOLD_FEATURES,
) -> bool:
    """True when the file is at or above the size threshold or has more features than the feature threshold.

    ``features_estimate`` is exact for files up to 3 sample reads (192 KB); larger files use the
    sampled estimate (``summary.exact`` False, shown with ``~``) and the decision follows it.
    Around the threshold of 30 buildings such files are tiny, so no extra full scan is done.
    """
    return summary.size_mb >= threshold_mb or summary.features_estimate > threshold_features


def summary_or_none(path: Optional[str]) -> Optional[SniffSummary]:
    if not path or not os.path.isfile(path):
        return None
    try:
        return sniff_file(path)
    except OSError:
        return None
