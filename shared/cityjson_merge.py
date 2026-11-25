"""Save into the source CityJSON file (CJ-06). No bpy.

Plan ``CITYJSON_INTEGRATION_PLAN_2026-11-17.md`` sections 3.2.1 ("an untouched object is never
converted") and 6 (CJ-06). Input: the source file as it is on disk plus the CJ-03 writer output
(``cityjson_writer.write_document``) of the changed and new buildings only. Output: the file with

- every untouched CityObject copied from the original JSON (other modules, out-of-scope objects,
  metadata, extensions, transform and geometry-templates too); only its vertex indices are renumbered
  when orphan vertices are dropped (a CityJSONSeq line that is not touched stays byte for byte);
- a changed building: per object, the geometry the reader loaded (the first surface geometry with the
  loaded LoD text; the reader reads no other, ``cityjson_reader._Reader.city_object``) is replaced by
  the scene's; every other geometry (other LoDs, a second geometry with the same text, templates) stays.
  Attributes the reader maps come from the scene (removed there = removed here), the others stay;
  a value equal to the original keeps the original JSON type. Extra keys of semantic surfaces are
  carried over by surface id (the reader's minted id ``<id>_g<geometry>_s<surface>`` when the file has
  none). A child that was loaded and is gone from the scene is removed; an unloaded or interior child
  stays. What cannot come back (appearance, inner shells, interior surfaces of a replaced geometry) is
  counted in the report's ``lost``;
- a deleted building removed with its children, and from every ``children`` / ``parents`` list;
- a new building added under its own id.

New vertices are quantised with the file's own transform (scale / translate unchanged), de-duplicated
against the pool, then orphan vertices are dropped and every index renumbered (the idea of CityJSONEditor's
``_remove_duplicate_vertices`` / ``_remove_orphan_vertices``, MIT; written anew here).

``merge_file`` checks the result (``check_merged``: the writer's self-check plus index ranges of every
geometry type and no orphan vertex; problems the original already had do not count), writes a temporary
file beside the source and renames it over the source only when the source is still the one that was
opened (size and mtime). Any problem raises ``MergeError`` and leaves the source as it was.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

try:
    from .cityjson_reader import (_ADDRESS, _DATES, _SURFACE_DEPTH, BUILDING_TYPES, INTERIOR_SURFACE_TYPES,
                                  NAME, OPENINGS, SURFACE_TYPES, THEMATIC, CityJSONError, gml_id_for, mapped_id)
    from .cityjson_writer import validate_document
    from .load_limits import LOAD_ALL_MAX_MB
except ImportError:  # pure-Python unit tests import ``shared`` as a top-level package
    from shared.cityjson_reader import (_ADDRESS, _DATES, _SURFACE_DEPTH, BUILDING_TYPES, INTERIOR_SURFACE_TYPES,
                                        NAME, OPENINGS, SURFACE_TYPES, THEMATIC, CityJSONError, gml_id_for, mapped_id)
    from shared.cityjson_writer import validate_document
    from shared.load_limits import LOAD_ALL_MAX_MB

VERSION = "2.0"
ROOT_TYPES = ("Building", "BuildingPart")
_SEM_RESERVED = ("type", "id", "parent", "children")
CHANGED_ON_DISK = ("{name} changed on disk since it was opened (size or time differs): not saved. "
                   "Open it again, or export the scene to another file.")


class MergeError(CityJSONError):
    """The merge or its self-check failed; nothing was written."""


# -- attributes ----------------------------------------------------------------------------------

def is_mapped(name: str, value) -> bool:
    """True when ``cityjson_reader`` turns this attribute into scene data (the mirror of its
    ``_attributes``); the others (null / list / object values) never reach the scene."""
    if name in _DATES:
        return isinstance(value, str)
    if name == NAME and (isinstance(value, str) or isinstance(value, list) and value
                         and all(isinstance(v, str) for v in value)):
        return True
    if name in THEMATIC and value is not None:
        return True
    return isinstance(value, (bool, int, float, str))


def _norm(v):
    """Value as the scene can carry it: "true" for True, 12.0 for 12 and "12" (type drift of the pivot)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    if isinstance(v, list):
        return tuple(_norm(x) for x in v)
    return json.dumps(v, sort_keys=True)


def merge_attributes(orig: Mapping, scene: Mapping, report: Optional[dict] = None) -> dict:
    """Original key order; unmapped keys from ``orig``; mapped keys from ``scene`` (the original value
    when they are equal); mapped keys missing in ``scene`` are dropped; new scene keys appended."""
    out = {}
    for k, v in orig.items():
        if not is_mapped(k, v):
            out[k] = v
            if report is not None:
                report["preserved"][f"attribute {k} (not read into the scene)"] += 1
        elif k in scene:
            out[k] = v if _norm(v) == _norm(scene[k]) else scene[k]
    for k, v in scene.items():
        out.setdefault(k, v)
    return out


# -- vertices ------------------------------------------------------------------------------------

class _Pool:
    """The file's vertex list; new world points are quantised with its transform and de-duplicated."""

    def __init__(self, vertices: list, transform: dict):
        self.vertices = list(vertices)
        self.s, self.t = transform["scale"], transform["translate"]
        self._lookup: Optional[Dict[tuple, int]] = None

    def add(self, world) -> int:
        if self._lookup is None:
            self._lookup = {tuple(v): i for i, v in enumerate(self.vertices)}
        key = tuple(int(round((world[k] - self.t[k]) / self.s[k])) for k in range(3))
        i = self._lookup.get(key)
        if i is None:
            i = self._lookup[key] = len(self.vertices)
            self.vertices.append(list(key))
        return i


def _remap(b, f: Callable[[int], int]) -> list:
    return [_remap(x, f) if isinstance(x, list) else f(x) for x in b]


def _indices(b, out: list) -> None:
    for x in b:
        if isinstance(x, list):
            _indices(x, out)
        else:
            out.append(x)


def _object_indices(o: dict) -> list:
    out: list = []
    for g in o.get("geometry") or ():
        if isinstance(g, dict):
            _indices(g.get("boundaries") or [], out)
    for a in o.get("address") or ():
        if isinstance(a, dict) and isinstance(a.get("location"), dict):
            _indices(a["location"].get("boundaries") or [], out)
    return out


def _convert(o: dict, nv: Callable[[int], int]) -> dict:
    """A deep copy of a written object with its vertex indices moved into the file's pool."""
    o = json.loads(json.dumps(o))
    for g in o.get("geometry") or ():
        g["boundaries"] = _remap(g["boundaries"], nv)
    for a in o.get("address") or ():
        if isinstance(a.get("location"), dict):
            a["location"]["boundaries"] = _remap(a["location"]["boundaries"], nv)
    return o


def _compact(doc: dict) -> int:
    """Drop vertices nobody uses and renumber every index in place (keeps their order). Returns the
    number dropped. ``doc["CityObjects"]`` must be the merge's own dict: objects are replaced, not edited."""
    objs, verts = doc["CityObjects"], doc["vertices"]
    n = len(verts)
    used = bytearray(n)
    for oid, o in objs.items():
        for i in _object_indices(o):
            if not (isinstance(i, int) and 0 <= i < n):
                raise MergeError(f"{o.get('type')} {oid!r}: vertex index {i!r} out of range (0..{n - 1})")
            used[i] = 1
    dropped = n - sum(used)
    if not dropped:
        return 0
    new, k = [-1] * n, 0
    for i in range(n):
        if used[i]:
            new[i] = k
            k += 1
    doc["vertices"] = [v for v, u in zip(verts, used) if u]
    f = new.__getitem__
    for oid, o in list(objs.items()):
        if not o.get("geometry") and not o.get("address"):
            continue
        o = dict(o)
        if o.get("geometry"):
            o["geometry"] = [dict(g, boundaries=_remap(g["boundaries"], f)) if isinstance(g, dict) and "boundaries" in g
                             else g for g in o["geometry"]]
        if o.get("address"):
            o["address"] = [dict(a, location=dict(a["location"], boundaries=_remap(a["location"]["boundaries"], f)))
                            if isinstance(a, dict) and isinstance(a.get("location"), dict) else a
                            for a in o["address"]]
        objs[oid] = o
    return dropped


def _extent(doc: dict, o: dict) -> Optional[list]:
    idx = [i for g in o.get("geometry") or () for i in _indices_of(g)]
    if not idx:
        return None
    s, t, vs = doc["transform"]["scale"], doc["transform"]["translate"], doc["vertices"]
    lo = [min(vs[i][k] for i in idx) * s[k] + t[k] for k in range(3)]
    hi = [max(vs[i][k] for i in idx) * s[k] + t[k] for k in range(3)]
    return [round(c, 6) for c in lo + hi]


def _indices_of(g) -> list:
    out: list = []
    _indices(g.get("boundaries") or [], out)
    return out


# -- one changed building ------------------------------------------------------------------------

def _subtree(objs: Mapping, rid: str) -> List[str]:
    out, stack = [], [rid]
    while stack:
        oid = stack.pop(0)
        if oid in objs and oid not in out:
            out.append(oid)
            stack += objs[oid].get("children") or []
    return out


def _count_lost(g: dict, lost: Counter) -> None:
    if "material" in g or "texture" in g:
        lost["appearance (material/texture) of a replaced geometry"] += 1
    if g.get("type") == "Solid" and len(g.get("boundaries") or ()) > 1:
        lost["inner shell of a replaced Solid"] += len(g["boundaries"]) - 1
    for s in (g.get("semantics") or {}).get("surfaces") or ():
        t = s.get("type")
        if t in INTERIOR_SURFACE_TYPES:
            lost[f"{t} of a replaced geometry"] += 1
        elif t not in SURFACE_TYPES and t not in OPENINGS:
            lost[f"semantic class {t} of a replaced geometry"] += 1


def _carry_surface_extras(oid: str, gi: int, old: dict, new: List[dict], report: dict) -> None:
    """Extra keys of the old geometry's semantic surfaces onto the scene's surfaces with the same id."""
    base, extras, alias = gml_id_for(oid), {}, {}
    for si, s in enumerate((old.get("semantics") or {}).get("surfaces") or ()):
        e = {k: v for k, v in s.items() if k not in _SEM_RESERVED}
        if e and isinstance(s.get("id"), str) and s["id"]:
            extras[s["id"]] = e
        elif e:
            extras[f"{base}_g{gi}_s{si}"] = e
            # a scene opened before dotted ids were kept minted this id from the mapped root id
            alias[f"{mapped_id(oid)}_g{gi}_s{si}"] = f"{base}_g{gi}_s{si}"
    if not extras:
        return
    found = set()
    for g in new:
        for s in (g.get("semantics") or {}).get("surfaces") or ():
            sid = s.get("id") if s.get("id") in extras else alias.get(s.get("id"))
            e = extras.get(sid)
            if e is None:
                continue
            found.add(sid)
            for k, v in e.items():
                if k not in s or _norm(s[k]) == _norm(v):
                    s[k] = v
                    report["preserved"][f"surface attribute {k}"] += 1
    for sid, e in extras.items():
        if sid not in found:
            for k in e:
                report["lost"][f"surface attribute {k} (surface not in the scene)"] += 1


def _merge_geometries(oid: str, old: list, new: list, loaded: Set[str], report: dict) -> list:
    groups: Dict[str, list] = {}
    for g in new:
        groups.setdefault(str(g.get("lod", "")), []).append(g)
    out, done = [], set()
    for gi, g in enumerate(old):
        t = str(g.get("lod", "")) if isinstance(g, dict) else ""
        surface = isinstance(g, dict) and g.get("type") in _SURFACE_DEPTH
        if surface and t not in done and (t in groups or t in loaded):
            done.add(t)   # the geometry the reader loaded: the first surface geometry with this text
            repl = groups.get(t, [])
            _carry_surface_extras(oid, gi, g, repl, report)
            _count_lost(g, report["lost"])
            out += repl
            continue
        out.append(g)
        if surface:
            report["kept_lods"][t] += 1
    for t, gs in groups.items():
        if t not in done:
            out += gs
    return out


def _addr_key(a: dict, pool: _Pool) -> tuple:
    fields = sorted((_ADDRESS[k], str(v)) for k, v in a.items() if k in _ADDRESS and v not in (None, ""))
    loc = a.get("location") if isinstance(a.get("location"), dict) else {}
    return tuple(fields), tuple(tuple(pool.vertices[i]) for i in loc.get("boundaries") or ())


def _merge_addresses(old: list, new: list, pool: _Pool) -> list:
    if len(old) == len(new) and all(isinstance(a, dict) and _addr_key(a, pool) == _addr_key(b, pool)
                                    for a, b in zip(old, new)):
        return old
    out = []
    for i, b in enumerate(new):
        a = old[i] if i < len(old) and isinstance(old[i], dict) else {}
        out.append({**b, **{k: v for k, v in a.items() if k not in _ADDRESS and k != "location" and k not in b}})
    return out


def _merge_object(oid: str, o: dict, w: dict, loaded: Set[str], pool: _Pool, report: dict) -> dict:
    out = dict(o)
    out["type"] = w["type"]
    attrs = merge_attributes(o.get("attributes") or {}, w.get("attributes") or {}, report)
    if attrs:
        out["attributes"] = attrs
    else:
        out.pop("attributes", None)
    if o.get("address") or w.get("address"):
        adr = _merge_addresses(o.get("address") or [], w.get("address") or [], pool)
        if adr:
            out["address"] = adr
        else:
            out.pop("address", None)
    out["geometry"] = _merge_geometries(oid, o.get("geometry") or [], w.get("geometry") or [], loaded, report)
    return out


def _fix_lod_texts(w: dict, loaded: Set[str]) -> None:
    """A geometry the writer gave the bare integer ("2", nothing to read the text from) takes the
    loaded text of that integer LoD ("2.2"), so it replaces the loaded geometry instead of joining it."""
    for g in w.get("geometry") or ():
        t = str(g.get("lod", ""))
        if t in loaded:
            continue
        same = [x for x in loaded if x.split(".")[0] == t.split(".")[0]]
        if len(same) == 1:
            g["lod"] = same[0]


def _merge_tree(objs: dict, wobjs: Mapping, rid: str, nv, pool: _Pool, loaded_lods: Mapping[str, Set[str]],
                report: dict, extents: Set[str]) -> None:
    osub, wsub = _subtree(objs, rid), _subtree(wobjs, rid)
    loaded = set().union(*(loaded_lods.get(i, ()) for i in (*osub, *wsub)))
    gone: Set[str] = set()
    for oid in osub:
        if oid in wsub or oid in gone:
            continue
        o = objs[oid]
        if o.get("type") in BUILDING_TYPES[1:] and any(
                isinstance(g, dict) and g.get("type") in _SURFACE_DEPTH and str(g.get("lod", "")) in loaded
                for g in o.get("geometry") or ()):
            gone.update(_subtree(objs, oid))   # loaded into the scene and not there any more: deleted there
    gone -= set(wsub)
    for oid in wsub:
        if oid in objs and oid not in osub:
            raise MergeError(f"{wobjs[oid].get('type')} {oid!r}: the file has this id outside building {rid!r}")
        w = _convert(wobjs[oid], nv)
        _fix_lod_texts(w, loaded)
        o = objs.get(oid) if oid in osub else None
        if o is None:
            merged = w
        else:
            merged = _merge_object(oid, o, w, set(loaded_lods.get(oid, ())) or loaded, pool, report)
            if "geographicalExtent" in o:
                extents.add(oid)
            kids = [c for c in o.get("children") or () if c not in gone]
            kids += [c for c in w.get("children") or () if c not in kids]
            if kids:
                merged["children"] = kids
            else:
                merged.pop("children", None)
            if o.get("parents"):
                merged["parents"] = o["parents"]
        objs[oid] = merged
    for oid in gone:
        objs.pop(oid, None)
    report["removed_children"] += len(gone)


# -- document ------------------------------------------------------------------------------------

def _new_report() -> dict:
    return {"changed": [], "new": [], "deleted": [], "lost": Counter(), "preserved": Counter(),
            "kept_lods": Counter(), "removed_children": 0, "orphans_removed": 0}


def merge_document(original: dict, written: dict, changed: Iterable[str] = (), new: Iterable[str] = (),
                   deleted: Iterable[str] = (), loaded_lods: Optional[Mapping[str, Iterable[str]]] = None
                   ) -> Tuple[dict, dict]:
    """``(merged document, report)``. ``changed`` / ``new`` / ``deleted``: CityJSON ids of top-level
    objects; ``written``: writer output holding (at least) the changed and new ones; ``loaded_lods``:
    {CityJSON id: LoD texts the scene loaded} of the scene's objects. ``original`` is not modified."""
    if str(original.get("version")) != VERSION:
        raise MergeError(f"CityJSON {original.get('version')}: save into the source is for CityJSON {VERSION} "
                         "files (upgrade with cjio, or export to another file)")
    loaded = {k: set(v) for k, v in (loaded_lods or {}).items()}
    objs = dict(original["CityObjects"])
    wobjs = written.get("CityObjects") or {}
    pool = _Pool(original["vertices"], original["transform"])
    ws, wt = (written.get("transform") or {}).get("scale"), (written.get("transform") or {}).get("translate")
    wverts, vmap = written.get("vertices") or [], {}

    def nv(i: int) -> int:
        if i not in vmap:
            v = wverts[i]
            vmap[i] = pool.add([v[k] * ws[k] + wt[k] for k in range(3)])
        return vmap[i]

    report, extents = _new_report(), set()
    for rid in deleted:
        if rid not in objs:
            raise MergeError(f"deleted building {rid!r} is not in the file")
        sub = set(_subtree(objs, rid))
        for oid in sub:
            del objs[oid]
        for oid, o in list(objs.items()):   # nobody names a removed object any more
            if sub & set(o.get("children") or ()) or sub & set(o.get("parents") or ()):
                o = dict(o)
                for key in ("children", "parents"):
                    if key in o:
                        o[key] = [c for c in o[key] if c not in sub]
                        if not o[key]:
                            del o[key]
                objs[oid] = o
        report["deleted"].append(rid)
        report["removed_children"] += len(sub) - 1
    for rid in changed:
        if rid not in objs:
            raise MergeError(f"changed building {rid!r} is not in the file")
        if rid not in wobjs:
            raise MergeError(f"changed building {rid!r} was not written from the scene")
        _merge_tree(objs, wobjs, rid, nv, pool, loaded, report, extents)
        report["changed"].append(rid)
    for rid in new:
        if rid not in wobjs:
            raise MergeError(f"new building {rid!r} was not written from the scene")
        for oid in _subtree(wobjs, rid):
            if oid in objs:
                raise MergeError(f"new {wobjs[oid].get('type')} {oid!r}: the file already has this id")
            objs[oid] = _convert(wobjs[oid], nv)
        report["new"].append(rid)
    doc = dict(original, CityObjects=objs, vertices=pool.vertices)
    report["orphans_removed"] = _compact(doc)
    for oid in extents:
        ext = _extent(doc, doc["CityObjects"][oid])
        if ext:
            doc["CityObjects"][oid] = dict(doc["CityObjects"][oid], geographicalExtent=ext)
    return doc, report


# -- self-check ----------------------------------------------------------------------------------

def check_merged(doc: dict) -> List[str]:
    """``cityjson_writer.validate_document`` on the surface geometries, plus the index range of every
    other geometry type and address location, and no vertex that nobody uses."""
    objs, nv = doc.get("CityObjects") or {}, len(doc.get("vertices") or ())
    surface = {oid: dict(o, geometry=[g for g in o["geometry"] if isinstance(g, dict) and g.get("type") in _SURFACE_DEPTH])
               if isinstance(o, dict) and o.get("geometry") else o for oid, o in objs.items()}
    problems = validate_document(dict(doc, CityObjects=surface))
    templates = len((doc.get("geometry-templates") or {}).get("templates") or ())
    used = bytearray(nv)
    for oid, o in objs.items():
        if not isinstance(o, dict):
            continue
        for i in _object_indices(o):
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < nv:
                used[i] = 1
            else:
                problems.append(f"{o.get('type')} {oid!r}: vertex index {i!r} out of range")
        for g in o.get("geometry") or ():
            if isinstance(g, dict) and g.get("type") == "GeometryInstance" and not (
                    isinstance(g.get("template"), int) and 0 <= g["template"] < templates):
                problems.append(f"{o.get('type')} {oid!r}: template {g.get('template')!r} out of range")
    if nv and not all(used):
        problems.append(f"{nv - sum(used)} vertices that nobody uses")
    return problems


def _new_problems(merged: dict, original: dict) -> List[str]:
    return sorted(set(check_merged(merged)) - set(check_merged(original)))


def _fail(problems: List[str]) -> MergeError:
    return MergeError("self-check failed, the source was not changed: " + "; ".join(problems[:5])
                      + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""))


# -- files ---------------------------------------------------------------------------------------

def file_stat(path: str) -> Tuple[int, int]:
    """``(size, mtime_ns)``: the source must still be this when it is replaced."""
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def is_seq(path: str) -> bool:
    """CityJSONSeq: a first line that is a whole CityJSON object, followed by more lines."""
    with open(path, "rb") as f:
        first = f.readline()
        try:
            head = json.loads(first)
        except ValueError:
            return False
        if not (isinstance(head, dict) and head.get("type") == "CityJSON"):
            return False
        return any(line.strip() for line in f)


def _sum(total: dict, part: dict) -> None:
    for k, v in part.items():
        if isinstance(v, Counter):
            total[k].update(v)
        elif isinstance(v, list):
            total[k] += v
        elif isinstance(v, int):
            total[k] += v


def _mini(header: dict, feature: Optional[dict] = None) -> dict:
    return {"type": "CityJSON", "version": header.get("version"), "transform": header["transform"],
            "CityObjects": (feature or {}).get("CityObjects") or {}, "vertices": (feature or {}).get("vertices") or []}


def _line(obj: dict, nl: bytes) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + nl


def _merge_seq(path: str, out, written: dict, changed: Set[str], new: List[str], deleted: Set[str],
               loaded_lods) -> dict:
    report = _new_report()
    report.update(untouched_buildings=0, untouched_objects=0)
    seen, done = set(), set()
    with open(path, "rb") as f:
        header_line = f.readline()
        header = json.loads(header_line)
        if str(header.get("version")) != VERSION:
            raise MergeError(f"CityJSON {header.get('version')}: save into the source is for CityJSON {VERSION} files")
        nl = b"\r\n" if header_line.endswith(b"\r\n") else b"\n"
        last = header_line
        if out:
            out.write(header_line)
        for line in f:
            if not line.strip():
                if out:
                    out.write(line)
                continue
            feat = json.loads(line)
            fid = feat.get("id") if isinstance(feat, dict) else None
            objects = feat.get("CityObjects") or {} if isinstance(feat, dict) else {}
            seen.update(objects)
            if fid in deleted:
                done.add(fid)
                report["deleted"].append(fid)
                report["removed_children"] += len(objects) - 1
                continue
            if fid in changed:
                done.add(fid)
                mini = _mini(header, feat)
                doc, part = merge_document(mini, written, changed=[fid], loaded_lods=loaded_lods)
                problems = _new_problems(doc, mini)
                if fid not in doc["CityObjects"]:
                    problems.append(f"feature {fid!r} lost its own object")
                if problems:
                    raise _fail([f"feature {fid!r}: {p}" for p in problems])
                _sum(report, part)
                line = _line(dict(feat, CityObjects=doc["CityObjects"], vertices=doc["vertices"]), nl)
            else:
                report["untouched_objects"] += len(objects)
                report["untouched_buildings"] += (objects.get(fid) or {}).get("type") in ROOT_TYPES
            if out:
                out.write(line)
            last = line
        missing = (changed | deleted) - done
        if missing:
            raise MergeError(f"not in the file: {', '.join(sorted(missing))}")
        for rid in new:
            doc, part = merge_document(_mini(header), written, new=[rid])
            clash = sorted(set(doc["CityObjects"]) & seen)
            if clash:
                raise MergeError(f"new building {rid!r}: the file already has the id(s) {', '.join(clash)}")
            problems = _new_problems(doc, _mini(header))
            if problems:
                raise _fail([f"new feature {rid!r}: {p}" for p in problems])
            _sum(report, part)
            if out:
                if not last.endswith(b"\n"):
                    out.write(nl)
                last = _line({"type": "CityJSONFeature", "id": rid, "CityObjects": doc["CityObjects"],
                              "vertices": doc["vertices"]}, nl)
                out.write(last)
    return report


def _merge_single(path: str, written, changed, new, deleted, loaded_lods) -> Tuple[dict, dict]:
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > LOAD_ALL_MAX_MB:
        raise MergeError(f"{size_mb:,.0f} MB: a single CityJSON document above {LOAD_ALL_MAX_MB:,.0f} MB is not "
                         "rewritten here (load it into 3DCityDB v5)")
    with open(path, encoding="utf-8") as f:
        original = json.load(f)
    doc, report = merge_document(original, written, changed, new, deleted, loaded_lods)
    problems = _new_problems(doc, original)
    if problems:
        raise _fail(problems)
    touched = set(report["changed"]) | set(report["new"])
    roots = [oid for oid, o in doc["CityObjects"].items() if not o.get("parents")]
    report["untouched_buildings"] = sum(1 for oid in roots if oid not in touched
                                        and doc["CityObjects"][oid].get("type") in ROOT_TYPES)
    sub = {i for r in touched for i in _subtree(doc["CityObjects"], r)}
    report["untouched_objects"] = sum(1 for oid in doc["CityObjects"] if oid not in sub)
    return doc, report


def merge_file(path: str, written: dict, changed: Iterable[str] = (), new: Iterable[str] = (),
               deleted: Iterable[str] = (), loaded_lods=None, expected_stat=None, dry_run: bool = False) -> dict:
    """Merge into ``path`` (CityJSON or CityJSONSeq) and, unless ``dry_run``, replace it atomically.
    ``expected_stat``: ``file_stat`` when the file was opened; a different file on disk is refused.
    Returns the report (lists and plain dicts; ``format``, ``untouched_buildings`` ...)."""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    st = file_stat(path)
    if expected_stat is not None and tuple(expected_stat) != st:
        raise MergeError(CHANGED_ON_DISK.format(name=name))
    changed, new, deleted = list(dict.fromkeys(changed)), list(dict.fromkeys(new)), list(dict.fromkeys(deleted))
    seq = is_seq(path)
    tmp = f"{path}.cj06-{os.getpid()}.tmp"
    try:
        if seq:
            with (open(tmp, "wb") if not dry_run else _Null()) as out:
                report = _merge_seq(path, None if dry_run else out, written, set(changed), new, set(deleted),
                                    loaded_lods)
        else:
            doc, report = _merge_single(path, written, changed, new, deleted, loaded_lods)
            if not dry_run:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        if not dry_run:
            if file_stat(path) != st:
                raise MergeError(CHANGED_ON_DISK.format(name=name))
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    report = {k: dict(v) if isinstance(v, Counter) else v for k, v in report.items()}
    report.update(path=path, format="CityJSONSeq" if seq else "CityJSON", self_check="passed",
                  dry_run=bool(dry_run), stat=list(st if dry_run else file_stat(path)))
    return report


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False
