"""Feature index of a CityJSON file (CJ-05, pure Python, no bpy).

Same ``FileIndex`` as ``gml_index`` gives for GML, so the boxes, picking, cache and Unload of Browse
first work unchanged. One entry per building: a root ``Building`` (or a ``BuildingPart`` whose parent
is not in the file, as ``cityjson_reader`` reads it) together with its ``BuildingPart`` /
``BuildingInstallation`` children; and one per ``Road``, ``Railway``, ``TransportSquare`` and
``CityFurniture`` (CJ-07; a ``GeometryInstance`` gives the box of its placed template). Other types are
not indexed; ``FileIndex.skipped_kinds`` counts them.

- CityJSONSeq (first line the CityJSON object with ``transform``, then one ``CityJSONFeature`` per
  line): read line by line, never as a whole; an entry keeps the byte range ``[start, end)`` of its line.
  An empty line is ignored, an unreadable one is counted and skipped.
- A single CityJSON document: parsed in memory, up to ``LOAD_ALL_MAX_MB``; the parsed document stays on
  ``FileIndex.document`` for Load. Above the limit: 3DCityDB v5 (scale decision). It is parsed in small
  steps (:func:`load_stepwise`), so the worker thread never holds the GIL for long and Esc cancels.

``FeatureIndexEntry.gml_id`` is the id the builder gives the object (``cityjson_reader.gml_id_for``);
``lod_texts`` are the LoDs as written ("1.2", "2.2"). ``FileIndex.root_name`` is the format,
``version`` the CityJSON version, ``envelope`` ``metadata.geographicalExtent``.

Load: :func:`selection_document` builds a small CityJSON document of the picked buildings (their
objects and children, only the vertices they use, the file's ``transform``) for
``cityjson_reader.read_document``.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Iterable, List, Sequence, Tuple

from ..cityjson_open import TOO_LARGE
from ..cityjson_reader import (_SURFACE_DEPTH, BUILDING_TYPES, INSTANCE, ROOT_TYPES, CityJSONError,
                               check_document, gml_id_for)
from ..load_limits import LOAD_ALL_MAX_MB
from .gml_index import FeatureIndexEntry, FileIndex, GmlIndexError, IndexCancelled

FORMAT_DOCUMENT = "CityJSON"      # FileIndex.root_name
FORMAT_SEQ = "CityJSONSeq"
FORMATS = (FORMAT_DOCUMENT, FORMAT_SEQ)
BATCH_FEATURES = 200
UNREADABLE = "unreadable line"
VERTEX_SLICE = 1 << 18             # characters of "vertices" parsed per step
_BOM = b"\xef\xbb\xbf"
_LOD_TEXT = re.compile(r"\d+(?:\.\d+)?")
_WS = re.compile(r"[ \t\n\r]*")


def lod_order(text: str):
    return float(text), text


def lod_texts(entries: Iterable[FeatureIndexEntry]) -> List[str]:
    """The LoD texts of ``entries``, ascending ("1.2", "1.3", "2.2")."""
    return sorted({t for e in entries for t in e.lod_texts}, key=lod_order)


def _json(raw: bytes):
    try:
        return json.loads(raw[len(_BOM):] if raw.startswith(_BOM) else raw)
    except ValueError:      # JSONDecodeError, UnicodeDecodeError
        return None


def load_stepwise(text: str, beat=None, vertex_slice: int = VERTEX_SLICE) -> dict:
    """``json.loads(text)`` of a CityJSON document in small steps: the members of ``CityObjects`` one by
    one, ``vertices`` in slices. One call of the C parser holds the GIL until it returns (about 1 s per
    30 MB), which stalls Blender's main thread and ignores Esc; between the steps the main thread runs,
    and ``beat(characters done, total)`` may raise to cancel. Raises ``ValueError`` like ``json.loads``."""
    decode = json.JSONDecoder().raw_decode
    total = len(text)

    def skip(pos: int) -> int:
        return _WS.match(text, pos).end()

    def members(pos: int, value):
        """The object at ``pos``; ``value(key, pos) -> (parsed value, end)`` parses each member."""
        if text[pos:pos + 1] != "{":
            raise ValueError(f"expected an object at character {pos}")
        out, pos = {}, skip(pos + 1)
        if text[pos:pos + 1] == "}":
            return out, pos + 1
        while True:
            key, pos = decode(text, pos)
            pos = skip(pos)
            if not isinstance(key, str) or text[pos:pos + 1] != ":":
                raise ValueError(f"expected a member name and ':' at character {pos}")
            out[key], pos = value(key, skip(pos + 1))
            pos = skip(pos)
            if text[pos:pos + 1] == "}":
                return out, pos + 1
            if text[pos:pos + 1] != ",":
                raise ValueError(f"expected ',' or '}}' at character {pos}")
            pos = skip(pos + 1)

    def city_object(_key, pos: int):
        if beat is not None:
            beat(pos, total)
        return decode(text, pos)

    def vertices(pos: int):
        """The array of [x, y, z] arrays at ``pos``: a ``]`` in it closes a vertex or the array."""
        out, pos = [], skip(pos + 1)
        while text[pos:pos + 1] != "]":
            end = text.find("]", pos + vertex_slice)
            if end < 0:             # the array ends within this slice
                rest, stop = decode("[" + text[pos:])
                return out + rest, pos + stop - 1
            try:
                out += json.loads("[" + text[pos:end + 1] + "]")
            except ValueError:      # ``end`` closed the array itself
                out += json.loads("[" + text[pos:end] + "]")
                return out, end + 1
            pos = skip(end + 1)
            if text[pos:pos + 1] == ",":
                pos = skip(pos + 1)
                if text[pos:pos + 1] == "]":
                    raise ValueError(f"trailing ',' in 'vertices' at character {pos}")
            elif text[pos:pos + 1] != "]":
                raise ValueError(f"expected ',' or ']' at character {pos}")
            if beat is not None:
                beat(pos, total)
        return out, pos + 1

    def top(key, pos: int):
        if key == "CityObjects" and text[pos:pos + 1] == "{":
            return members(pos, city_object)
        if key == "vertices" and text[pos:pos + 1] == "[":
            return vertices(pos)
        return decode(text, pos)

    doc, pos = members(skip(0), top)
    if skip(pos) != total:
        raise ValueError(f"extra data at character {pos}")
    return doc


def _count(idx: FileIndex, kind: str) -> None:
    idx.skipped_kinds[kind] = idx.skipped_kinds.get(kind, 0) + 1


def _tree(oid: str, objects: dict, skipped: Dict[str, int]) -> List[dict]:
    """The objects of one building that are read: the root, its parts and installations (any depth).
    A child of another type is counted in ``skipped``."""
    out, seen, stack = [], {oid}, [oid]
    while stack:
        o = objects[stack.pop()]
        out.append(o)
        for cid in o.get("children") or ():
            child = objects.get(cid)
            if cid in seen or not isinstance(child, dict):
                continue
            seen.add(cid)
            if child.get("type") in BUILDING_TYPES[1:]:
                stack.append(cid)
            else:
                kind = str(child.get("type"))
                skipped[kind] = skipped.get(kind, 0) + 1
    return out


def _polygons(g: dict):
    """``(polygons, semantic value per polygon)`` of a geometry the reader reads, else None."""
    depth = _SURFACE_DEPTH.get(g.get("type"))
    if depth is None:
        return None
    b = g.get("boundaries") or []
    vals = (g.get("semantics") or {}).get("values")
    if depth == 2:
        polys = [p for shell in b for p in shell]
        vals = [v for sv, shell in zip(vals, b) for v in (sv or [None] * len(shell))] if vals else None
    else:
        polys = b
    return polys, vals or [None] * len(polys)


def _entry(oid: str, objects: dict, vertices: Sequence, scale, translate, skipped: Dict[str, int],
           start: int = 0, end: int = 0, templates=None) -> FeatureIndexEntry:
    root = objects[oid]
    gid = gml_id_for(oid)
    entry = FeatureIndexEntry(gml_id=gid, feature_type=str(root.get("type")), name=oid if gid != oid else "",
                              start=start, end=end)
    used, texts, props, lod0, ground = set(), set(), set(), [], {}
    placed = []     # world points of placed templates (GeometryInstance)
    try:
        for o in _tree(oid, objects, skipped):
            for g in o.get("geometry") or ():
                instance = g.get("type") == INSTANCE and root.get("type") == "CityFurniture"
                t = templates["templates"][g["template"]] if instance else g
                text = str(t.get("lod", ""))
                found = _polygons(t) if _LOD_TEXT.fullmatch(text) else None
                n = int(text.split(".")[0]) if found else -1
                if not 0 <= n <= 3:     # what cityjson_reader does not read is not offered either
                    continue
                if instance:
                    m, ref, tv = g["transformationMatrix"], vertices[g["boundaries"][0]], templates["vertices-templates"]
                    for x, y, z in (tv[i] for i in {i for poly in found[0] for ring in poly for i in ring}):
                        placed.append(tuple(m[4 * k] * x + m[4 * k + 1] * y + m[4 * k + 2] * z + m[4 * k + 3]
                                            + ref[k] * scale[k] + translate[k] for k in range(3)))
                    texts.add(text)
                    props.add(f"lod{n}ImplicitRepresentation")
                    continue
                surfaces = (g.get("semantics") or {}).get("surfaces") or ()
                for poly, v in zip(*found):
                    for ring in poly:
                        used.update(ring)
                    if n == 0:
                        lod0.append(poly[0])
                    elif v is not None and surfaces[v].get("type") == "GroundSurface":
                        ground.setdefault(text, []).append(poly[0])
                texts.add(text)
                props.add(f"lod{n}{'Solid' if g['type'] == 'Solid' else 'MultiSurface'}")
        if not (used or placed) or min(used, default=0) < 0:
            raise IndexError
        pts = [tuple(vertices[i][k] * scale[k] + translate[k] for k in range(3)) for i in used] + placed
        sx, sy, _sz = scale
        tx, ty, _tz = translate

        def flat(ring):
            return [c for i in ring for c in (vertices[i][0] * sx + tx, vertices[i][1] * sy + ty)]

        entry.bbox = (tuple(min(p[k] for p in pts) for k in range(3))
                      + tuple(max(p[k] for p in pts) for k in range(3)))
        entry.lod0 = [flat(r) for r in lod0]
        if ground:  # one outline per building: the highest LoD that has ground surfaces
            entry.footprint = [flat(r) for r in ground[max(ground, key=lod_order)]]
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        # a broken building keeps its place in the index without a box; the reader names the fault at Load
        entry.bbox, entry.lod0, entry.footprint = None, [], []
    entry.lod_texts = tuple(sorted(texts, key=lod_order))
    entry.lods = tuple(sorted(props))
    return entry


def _set_header(idx: FileIndex, header: dict) -> None:
    try:
        check_document(header)
    except CityJSONError as exc:
        raise GmlIndexError(f"Not a readable CityJSON file: {exc}") from exc
    idx.version = str(header.get("version"))
    meta = header.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    idx.srs_name = str(meta.get("referenceSystem") or "")
    ext = meta.get("geographicalExtent")
    if (isinstance(ext, list) and len(ext) == 6
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ext)):
        idx.envelope = (tuple(float(x) for x in ext[:3]), tuple(float(x) for x in ext[3:]))


def index_cityjson(path: str, *, progress=None, on_batch=None, cancel=None,
                   max_mb: float = LOAD_ALL_MAX_MB, batch_features: int = BATCH_FEATURES) -> FileIndex:
    """Build the :class:`FileIndex` of a CityJSON or CityJSONSeq file (same contract as
    ``gml_index.index_file``). ``on_batch`` is only called when the file states its extent
    (``metadata.geographicalExtent``): without it the session origin needs the whole index."""
    t0 = time.perf_counter()
    st = os.stat(path)
    idx = FileIndex(path=os.path.abspath(path), size=st.st_size, mtime_ns=st.st_mtime_ns)
    limit = int(max_mb * 2**20)
    too_large = GmlIndexError(TOO_LARGE.format(size_mb=st.st_size / 2**20, max_mb=max_mb))

    def beat(done: int, total: int) -> None:
        if cancel is not None and cancel():
            raise IndexCancelled()
        if progress is not None:
            progress(done, total, len(idx.entries))

    header, seq = None, False
    with open(path, "rb") as fh:
        first = fh.readline(limit + 1)
        if len(first) > limit:      # one line above the limit: a minified document that large
            raise too_large
        try:    # stepwise: a document written on one line is all in this line
            head = load_stepwise(first.decode("utf-8-sig"), beat)
        except ValueError:
            head = None
        if isinstance(head, dict) and head.get("type") == "CityJSONFeature":
            raise GmlIndexError("CityJSONSeq without its first line (the CityJSON object with 'transform'): "
                                "the features cannot be placed")
        if isinstance(head, dict) and head.get("type") == "CityJSON":
            header = head
            _set_header(idx, header)
            idx.root_open_end = len(first.rstrip(b"\r\n"))
            seq = _index_lines(fh, idx, header, len(first), beat, on_batch, batch_features)
    if seq:
        idx.root_name = FORMAT_SEQ
    else:
        if header is None:          # a document over several lines
            if st.st_size > limit:
                raise too_large
            beat(0, st.st_size)
            try:
                with open(path, encoding="utf-8-sig") as fh:    # decoded MiB by MiB, for the same reason
                    header = load_stepwise("".join(iter(lambda: fh.read(1 << 20), "")), beat)
            except ValueError as exc:
                raise GmlIndexError(f"Not a readable CityJSON file: {exc}") from exc
            if not isinstance(header, dict) or header.get("type") != "CityJSON":
                raise GmlIndexError('Not a CityJSON file (its "type" is not "CityJSON")')
            _set_header(idx, header)
        _index_document(idx, header, beat)
        # shortcut: the document is kept as Python objects for Load (about 4.5x the file size; while it
        # grows, the garbage collector's full passes stop the main thread, up to 0.4 s at 100 MB); keep the
        # text and a character range per CityObject instead when that hurts
        idx.root_name, idx.document = FORMAT_DOCUMENT, header
    idx.skipped_members = sum(idx.skipped_kinds.values())
    idx.seconds = round(time.perf_counter() - t0, 3)
    return idx


def _index_lines(fh, idx: FileIndex, header: dict, pos: int, beat, on_batch, batch_features: int) -> bool:
    """Index the CityJSONFeature lines after the header; False when there is none."""
    scale, translate = header["transform"]["scale"], header["transform"]["translate"]
    pending: List[FeatureIndexEntry] = []
    seen = False

    def flush():
        if pending and on_batch is not None and idx.envelope:
            on_batch(list(pending), idx)
        pending.clear()
        beat(pos, idx.size)

    for raw in fh:
        start, pos = pos, pos + len(raw)
        line = raw.rstrip(b"\r\n")
        if not line.strip():
            continue
        seen = True
        f = _json(line)
        objects = f.get("CityObjects") if isinstance(f, dict) and f.get("type") == "CityJSONFeature" else None
        root = objects.get(f.get("id")) if isinstance(objects, dict) and isinstance(f.get("id"), str) else None
        if not isinstance(root, dict):
            _count(idx, UNREADABLE)
        elif root.get("type") not in ROOT_TYPES:
            _count(idx, str(root.get("type")))
        else:
            entry = _entry(f["id"], objects, f.get("vertices") or (), scale, translate, idx.skipped_kinds,
                           start, start + len(line), header.get("geometry-templates"))
            idx.entries.append(entry)
            pending.append(entry)
        if len(pending) >= batch_features:
            flush()
    flush()
    return seen


def _index_document(idx: FileIndex, doc: dict, beat) -> None:
    objects, vertices = doc["CityObjects"], doc["vertices"]
    scale, translate = doc["transform"]["scale"], doc["transform"]["translate"]
    for n, (oid, o) in enumerate(objects.items()):
        if n % 256 == 0:
            beat(n, len(objects))
        if not isinstance(o, dict):
            _count(idx, "object without type")
        elif any(p in objects for p in o.get("parents") or ()):
            continue                # indexed with its root
        elif o.get("type") in ROOT_TYPES:
            idx.entries.append(_entry(oid, objects, vertices, scale, translate, idx.skipped_kinds,
                                      templates=doc.get("geometry-templates")))
        else:
            _count(idx, str(o.get("type")))
    beat(len(objects), len(objects))


# --------------------------------------------------------------------------- Load: the small document
def collect(objects: dict, roots: Iterable[str]) -> Dict[str, dict]:
    """``roots`` and everything below them (``children``), whatever the type: the reader counts what
    it leaves out."""
    out: Dict[str, dict] = {}
    stack = list(roots)
    while stack:
        oid = stack.pop()
        o = objects.get(oid)
        if oid in out or not isinstance(o, dict):
            continue
        out[oid] = o
        stack.extend(c for c in o.get("children") or () if isinstance(c, str))
    return out


def build_subdocument(header: dict, parts: Iterable[Tuple[dict, Sequence]]) -> dict:
    """A CityJSON document of ``parts`` = ``(CityObjects, the vertices their indices refer to)``:
    everything of ``header`` but its objects and vertices (``transform``, ``metadata``, ``version``),
    and only the vertices the objects use, re-indexed. The sources are not changed."""
    doc = {k: v for k, v in header.items() if k not in ("CityObjects", "vertices")}
    doc["type"] = "CityJSON"
    out_objects: Dict[str, dict] = {}
    out_vertices: list = []
    for objects, vertices in parts:
        remap: Dict[int, int] = {}
        nv = len(vertices)

        def walk(b):
            if isinstance(b, list):
                return [walk(x) for x in b]
            if isinstance(b, bool) or not isinstance(b, int):
                return b            # the reader's shape check names it
            j = remap.get(b)
            if j is None:
                if not 0 <= b < nv:
                    raise CityJSONError(f"vertex index {b} out of range (0..{nv - 1})")
                j = remap[b] = len(out_vertices)
                out_vertices.append(vertices[b])
            return j

        for oid, o in objects.items():
            o = dict(o)
            if isinstance(o.get("geometry"), list):
                o["geometry"] = [dict(g, boundaries=walk(g["boundaries"]))
                                 if isinstance(g, dict) and "boundaries" in g else g for g in o["geometry"]]
            if isinstance(o.get("address"), list):
                o["address"] = [
                    dict(a, location=dict(a["location"], boundaries=walk(a["location"]["boundaries"])))
                    if isinstance(a, dict) and isinstance(a.get("location"), dict)
                    and "boundaries" in a["location"] else a for a in o["address"]]
            out_objects[oid] = o
    doc["CityObjects"], doc["vertices"] = out_objects, out_vertices
    return doc


def selection_document(idx: FileIndex, indices: Iterable[int]) -> Tuple[dict, int]:
    """``(CityJSON document of the entries ``indices``, bytes read from the file)``."""
    entries = [idx.entries[i] for i in indices]
    if idx.root_name == FORMAT_SEQ:
        st = os.stat(idx.path)
        if (st.st_size, st.st_mtime_ns) != (idx.size, idx.mtime_ns):
            raise GmlIndexError("The file changed since it was indexed; browse it again")
        parts, n = [], idx.root_open_end
        with open(idx.path, "rb") as fh:
            header = _json(fh.read(idx.root_open_end))
            for e in entries:
                fh.seek(e.start)
                f = _json(fh.read(e.end - e.start))
                n += e.end - e.start
                if not isinstance(f, dict) or not isinstance(header, dict):
                    raise GmlIndexError("The file changed since it was indexed; browse it again")
                parts.append((f["CityObjects"], f.get("vertices") or ()))
        return build_subdocument(header, parts), n
    doc = idx.document
    st = os.stat(idx.path)
    stale = doc is not None and (st.st_size, st.st_mtime_ns) != (idx.size, idx.mtime_ns)
    if doc is None or stale:    # stale: the file changed since it was indexed (Save into source, CJ-06)
        try:
            with open(idx.path, encoding="utf-8-sig") as fh:
                doc = json.load(fh)
            check_document(doc)
        except (ValueError, CityJSONError) as exc:
            raise GmlIndexError(f"The file changed since it was indexed and cannot be read: {exc}") from exc
        if stale:               # keep the new document, so the file is read once
            idx.document, idx.size, idx.mtime_ns = doc, st.st_size, st.st_mtime_ns
    by_gml_id = {gml_id_for(oid): oid for oid in doc["CityObjects"]}
    try:
        roots = [by_gml_id[e.gml_id] for e in entries]
    except KeyError:
        raise GmlIndexError("The file changed since it was indexed; browse it again") from None
    return build_subdocument(doc, [(collect(doc["CityObjects"], roots), doc["vertices"])]), 0
