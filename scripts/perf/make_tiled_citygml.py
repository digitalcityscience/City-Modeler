"""T03: tile a small CityGML 1.0/2.0/3.0 file into a large synthetic one.

Every copy of the source city model is shifted by ``(i*dx, j*dy)`` on a grid,
gets unique ``gml:id`` values (``<id>_t<copy>``) and matching ``xlink:href`` /
appearance references, and shifted feature envelopes. The CityModel envelope
is computed for the whole output.

The source is read once with ``iterparse`` and each top-level member is
compiled into a format template; copies are rendered one member at a time and
written straight to the output file, so memory depends on the source size,
never on the output size.

Options:
  --strip-envelopes  drop every gml:boundedBy envelope (feature and CityModel)
  --with-lod0        add a real LoD0 footprint (``lod0MultiSurface`` in 3.0,
                     ``bldg:lod0FootPrint`` in 2.0) built from the LoD2
                     GroundSurface polygons; features without a ground
                     surface get none. Bounding boxes are never written as LoD0.
                     Refused for CityGML 1.0 sources, which have no LoD0.

Usage:
  python scripts/perf/make_tiled_citygml.py SRC.gml OUT.gml --count 5000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from procmem import peak_rss_bytes  # noqa: E402

GML_NS = ("http://www.opengis.net/gml/3.2", "http://www.opengis.net/gml")
XLINK_NS = "http://www.w3.org/1999/xlink"
CORE3_NS = "http://www.opengis.net/citygml/3.0"
BLDG2_NS = "http://www.opengis.net/citygml/building/2.0"

COORD_TAGS = {"posList", "pos", "lowerCorner", "upperCorner"}
# Attributes and element texts that point at a gml:id with "#id".
REF_ATTRS = {f"{{{XLINK_NS}}}href", "uri", "ring"}
REF_TEXT_TAGS = {"target"}
FOOTPRINT_OWNERS = {"Building", "BuildingPart"}

# Children that precede the LoD0 footprint property in the XSD content model
# (AbstractFeature/CityObject properties, then the space or building attributes
# before lod0). The footprint is inserted before the first child not listed.
_PRE_LOD0_COMMON = {
    "description", "descriptionReference", "identifier", "name", "metaDataProperty",
    "creationDate", "terminationDate", "validFrom", "validTo", "externalReference",
    "generalizesTo", "relativeToTerrain", "relativeToWater", "relatedTo",
    "appearance", "dynamizer", "genericAttribute", "adeOfAbstractCityObject",
    "adeOfAbstractFeature", "adeOfAbstractFeatureWithLifespan",
}
PRE_LOD0_V3 = _PRE_LOD0_COMMON | {"spaceType", "volume", "area", "boundary", "lod0Point"}
PRE_LOD0_V2 = _PRE_LOD0_COMMON | {
    "stringAttribute", "intAttribute", "doubleAttribute", "dateAttribute", "uriAttribute",
    "measureAttribute", "genericAttributeSet",
    "class", "function", "usage", "yearOfConstruction", "yearOfDemolition", "roofType",
    "measuredHeight", "storeysAboveGround", "storeysBelowGround",
    "storeyHeightsAboveGround", "storeyHeightsBelowGround",
}


def _split(tag: str) -> tuple[str, str]:
    if tag[:1] == "{":
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return (_esc_text(s).replace('"', "&quot;").replace("\n", "&#10;")
            .replace("\r", "&#13;").replace("\t", "&#9;"))


def _fmt_literal(s: str) -> str:
    """Make literal text safe inside a str.format template."""
    return s.replace("{", "{{").replace("}", "}}")


def _decimals(token: str) -> int:
    _, dot, frac = token.partition(".")
    return len(frac) if dot else 0


@dataclass
class Bounds:
    lo: list = field(default_factory=lambda: [math.inf] * 3)
    hi: list = field(default_factory=lambda: [-math.inf] * 3)

    def add(self, coords, dim):
        for k in range(min(dim, 3)):
            vals = coords[k::dim]
            if vals:
                self.lo[k] = min(self.lo[k], min(vals))
                self.hi[k] = max(self.hi[k], max(vals))

    def union(self, other, dx=0.0, dy=0.0):
        shift = (dx, dy, 0.0)
        for k in range(3):
            self.lo[k] = min(self.lo[k], other.lo[k] + shift[k])
            self.hi[k] = max(self.hi[k], other.hi[k] + shift[k])

    @property
    def empty(self):
        return self.lo[0] == math.inf


@dataclass
class MemberTemplate:
    template: str           # str.format template: {S} = id suffix, {n:.kf} = coords
    xs: list                # source X values, fields 0..len(xs)-1
    ys: list                # source Y values, fields len(xs)..
    bounds: Bounds
    is_feature: bool
    footprints: int = 0

    def render(self, suffix: str, dx: float, dy: float) -> str:
        args = [x + dx for x in self.xs]
        args.extend(y + dy for y in self.ys)
        return self.template.format(*args, S=suffix)


class _Compiler:
    """Serializes one member subtree into a MemberTemplate."""

    def __init__(self, prefixes: dict, ids: set, gml_ns: str, version: str,
                 strip_envelopes: bool, with_lod0: bool, default_dim: int):
        self.prefixes = prefixes
        self.ids = ids
        self.gml_ns = gml_ns
        self.version = version
        self.strip_envelopes = strip_envelopes
        self.with_lod0 = with_lod0
        self.default_dim = default_dim

    def compile(self, elem, is_feature: bool) -> MemberTemplate:
        self.out = []
        self.xs = []
        self.ys = []
        self.bounds = Bounds()
        self.footprints = 0
        self._elem(elem, self.default_dim)
        return MemberTemplate("".join(self.out), self.xs, self.ys, self.bounds,
                              is_feature, self.footprints)

    # -- serialization -------------------------------------------------
    def _qname(self, tag: str) -> str:
        ns, local = _split(tag)
        if not ns:
            return local
        prefix = self.prefixes.get(ns)
        if prefix is None:
            # declared on the root element by write_tiled (see _root_namespaces)
            taken = set(self.prefixes.values())
            n = len(self.prefixes)
            while f"ns{n}" in taken:
                n += 1
            prefix = f"ns{n}"
            self.prefixes[ns] = prefix
        return f"{prefix}:{local}" if prefix else local

    def _elem(self, elem, dim: int, id_tail: str = ""):
        ns, local = _split(elem.tag)
        if (self.strip_envelopes and local == "boundedBy" and ns == self.gml_ns):
            return
        dim = int(elem.get("srsDimension", dim))
        out = self.out
        out.append("<" + self._qname(elem.tag))
        for key, value in elem.attrib.items():
            out.append(f' {self._qname(key)}="')
            akey_ns, akey_local = _split(key)
            if akey_local == "id" and akey_ns == self.gml_ns:
                out.append(_fmt_literal(_esc_attr(value + id_tail)) + "{S}")
            elif key in REF_ATTRS and value.startswith("#") and value[1:] in self.ids:
                out.append(_fmt_literal(_esc_attr(value)) + "{S}")
            else:
                out.append(_fmt_literal(_esc_attr(value)))
            out.append('"')
        children = list(elem)
        text = elem.text or ""
        if not children and not text:
            out.append("/>")
        else:
            out.append(">")
            if local in COORD_TAGS and ns == self.gml_ns:
                self._coords(text, dim, " ")
            elif local == "coordinates" and ns == self.gml_ns:
                self._coords_v2(text, dim, elem.get("cs", ","), elem.get("ts", " "))
            elif local in REF_TEXT_TAGS and text.strip().startswith("#") \
                    and text.strip()[1:] in self.ids:
                out.append(_fmt_literal(_esc_text(text.strip())) + "{S}")
            else:
                out.append(_fmt_literal(_esc_text(text)))
            footprint_at = None
            if self.with_lod0 and local in FOOTPRINT_OWNERS and not id_tail:
                footprint_at = self._footprint_position(children)
            for idx, child in enumerate(children):
                if idx == footprint_at:
                    self._footprint(elem, dim)
                self._elem(child, dim, id_tail)
                out.append(_fmt_literal(_esc_text(child.tail or "")))
            if footprint_at == len(children):
                self._footprint(elem, dim)
            out.append(f"</{self._qname(elem.tag)}>")

    def _coords(self, text: str, dim: int, sep: str):
        tokens = text.split()
        if not tokens:
            return
        values = [float(t) for t in tokens]
        self.bounds.add(values, dim)
        parts = []
        for k, token in enumerate(tokens):
            axis = k % dim
            if axis == 0:
                parts.append("\x00X%d:%d\x01" % (len(self.xs), _decimals(token)))
                self.xs.append(values[k])
            elif axis == 1:
                parts.append("\x00Y%d:%d\x01" % (len(self.ys), _decimals(token)))
                self.ys.append(values[k])
            else:
                parts.append(token)
        self.out.append(sep.join(parts))

    def _coords_v2(self, text: str, dim: int, cs: str, ts: str):
        tuples = [t for t in text.strip().split(ts) if t.strip()]
        flat = []
        for t in tuples:
            flat.extend(v.strip() for v in t.split(cs))
        dim = len(tuples[0].split(cs)) if tuples else dim
        values = [float(t) for t in flat]
        self.bounds.add(values, dim)
        rendered = []
        for i in range(0, len(flat), dim):
            tup = []
            for axis, token in enumerate(flat[i:i + dim]):
                if axis == 0:
                    tup.append("\x00X%d:%d\x01" % (len(self.xs), _decimals(token)))
                    self.xs.append(values[i + axis])
                elif axis == 1:
                    tup.append("\x00Y%d:%d\x01" % (len(self.ys), _decimals(token)))
                    self.ys.append(values[i + axis])
                else:
                    tup.append(token)
            rendered.append(cs.join(tup))
        self.out.append(ts.join(rendered))

    # -- LoD0 footprint ------------------------------------------------
    def _footprint_position(self, children):
        pre = PRE_LOD0_V3 if self.version == "3.0" else PRE_LOD0_V2
        for idx, child in enumerate(children):
            ns, local = _split(child.tag)
            if local in ("lod0MultiSurface", "lod0FootPrint"):
                return None  # real LoD0 already present
            if local == "boundedBy":
                if ns == self.gml_ns:
                    continue
                return idx
            if local not in pre:
                return idx
        return len(children)

    def _ground_polygons(self, owner):
        polys = []

        def walk(elem, inside_ground):
            for child in elem:
                ns, local = _split(child.tag)
                if local in FOOTPRINT_OWNERS:
                    continue  # nested parts get their own footprint
                if local == "GroundSurface":
                    walk(child, True)
                elif local == "Polygon" and ns == self.gml_ns and inside_ground:
                    polys.append(child)
                else:
                    walk(child, inside_ground)

        walk(owner, False)
        return polys

    def _footprint(self, owner, dim):
        polys = self._ground_polygons(owner)
        if not polys:
            return
        g = self.prefixes[self.gml_ns]
        fid = owner.get(f"{{{self.gml_ns}}}id", "feature")
        if self.version == "3.0":
            prop = self._qname(f"{{{CORE3_NS}}}lod0MultiSurface")
        else:
            prop = self._qname(f"{{{BLDG2_NS}}}lod0FootPrint")
        out = self.out
        out.append(f'<{prop}><{g}:MultiSurface {g}:id="{_fmt_literal(_esc_attr(fid))}_lod0{{S}}">')
        for poly in polys:
            out.append(f"<{g}:surfaceMember>")
            self._elem(_upward(poly, self.gml_ns, dim), dim, id_tail="_lod0")
            out.append(f"</{g}:surfaceMember>")
        out.append(f"</{g}:MultiSurface></{prop}>")
        self.footprints += 1


def _signed_area_xy(values, dim):
    xs, ys = values[0::dim], values[1::dim]
    return sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1)) / 2.0


def _upward(poly, gml_ns, dim):
    """Copy of a ground polygon whose rings face up (exterior counter-clockwise)."""
    import copy
    poly = copy.deepcopy(poly)
    for ring_prop in poly:
        _, prop_local = _split(ring_prop.tag)
        want_ccw = prop_local == "exterior"
        for pos_list in ring_prop.iter(f"{{{gml_ns}}}posList"):
            d = int(pos_list.get("srsDimension", dim))
            tokens = (pos_list.text or "").split()
            if len(tokens) < 3 * d:
                continue
            area = _signed_area_xy([float(t) for t in tokens], d)
            if (area > 0) != want_ccw:
                tuples = [tokens[i:i + d] for i in range(0, len(tokens), d)]
                pos_list.text = " ".join(t for tup in reversed(tuples) for t in tup)
    return poly


# ---------------------------------------------------------------------------
_COORD_FIELD = re.compile("\x00([XY])(\\d+):(\\d+)\x01")


def _finalize(tpl: MemberTemplate) -> MemberTemplate:
    """Turn the coordinate sentinels into positional format fields (X first, then Y).

    NUL cannot occur in XML text, so the sentinels never collide with data.
    """
    n_x = len(tpl.xs)

    def field_for(m):
        index = int(m.group(2)) + (n_x if m.group(1) == "Y" else 0)
        return "{%d:.%sf}" % (index, m.group(3))

    tpl.template = _COORD_FIELD.sub(field_for, tpl.template)
    return tpl


@dataclass
class Source:
    version: str
    gml_ns: str
    root_tag: str
    root_attrib: dict
    namespaces: list           # [(prefix, uri)] in declaration order
    prefixes: dict             # uri -> prefix
    header_children: list      # serialized non-member root children (gml:name...)
    members: list              # MemberTemplate
    srs_name: str
    srs_dim: int

    @property
    def features_per_copy(self):
        return sum(1 for m in self.members if m.is_feature)

    @property
    def bounds(self):
        b = Bounds()
        for m in self.members:
            if not m.bounds.empty:
                b.union(m.bounds)
        return b


def citygml_version(root_tag: str, gml_ns: str) -> str:
    """"1.0", "2.0" or "3.0" from the CityModel namespace, else from the GML namespace."""
    ns = _split(root_tag)[0]
    for version in ("1.0", "2.0", "3.0"):
        if ns.startswith("http://www.opengis.net/citygml/") and ns.endswith("/" + version):
            return version
    return "3.0" if gml_ns == GML_NS[0] else "2.0"


def load_source(path: str, *, strip_envelopes=False, with_lod0=False) -> Source:
    namespaces, prefixes = [], {}
    ids = set()
    # Pass 1: namespaces, ids, root envelope (cheap, the source is small).
    root = None
    depth = 0
    for event, item in ET.iterparse(path, events=("start-ns", "start", "end")):
        if event == "start-ns":
            prefix, uri = item
            namespaces.append((prefix, uri))
            prefixes.setdefault(uri, prefix)
        elif event == "start":
            depth += 1
            if root is None:
                root = item
            for k, v in item.attrib.items():
                if k.endswith("}id") and _split(k)[0] in GML_NS:
                    ids.add(v)
        else:
            depth -= 1
            if depth == 1:
                item.clear()
    gml_ns = next((u for u in GML_NS if u in prefixes), GML_NS[0])
    version = citygml_version(root.tag if root is not None else "", gml_ns)
    if with_lod0 and version == "1.0":
        # LoD0 (bldg:lod0FootPrint / lod0RoofEdge) only exists from CityGML 2.0 on.
        raise ValueError("--with-lod0 needs a CityGML 2.0 or 3.0 source; CityGML 1.0 has no LoD0")
    if gml_ns not in prefixes:
        prefixes[gml_ns] = "gml"
        namespaces.append(("gml", gml_ns))

    srs_name, srs_dim = "", 3
    members, header_children = [], []
    root_tag, root_attrib = None, {}
    compiler = None
    depth = 0
    # Pass 2: compile each top-level child as soon as it is complete.
    for event, elem in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            if depth == 1:
                root_tag, root_attrib = elem.tag, dict(elem.attrib)
                compiler = _Compiler(prefixes, ids, gml_ns, version,
                                     strip_envelopes, with_lod0, 3)
            continue
        depth -= 1
        if depth != 1:
            continue
        ns, local = _split(elem.tag)
        if local == "boundedBy" and ns == gml_ns:
            env = elem.find(f"{{{gml_ns}}}Envelope")
            if env is not None:
                srs_name = env.get("srsName", "")
                srs_dim = int(env.get("srsDimension", 3))
                compiler.default_dim = srs_dim
        elif local.endswith("Member") or local == "featureMembers":
            is_feature = local in ("cityObjectMember", "featureMember")
            members.append(_finalize(compiler.compile(elem, is_feature)))
        else:
            # gml:name, gml:description, ...: written once, unshifted
            header_children.append(_finalize(compiler.compile(elem, False)).render("", 0.0, 0.0))
        elem.clear()
    if not srs_name:
        srs_name = _first_srs_name(path)
    return Source(version, gml_ns, root_tag, root_attrib, namespaces, prefixes,
                  header_children, members, srs_name, srs_dim)


def _first_srs_name(path):
    for _, elem in ET.iterparse(path, events=("start",)):
        if elem.get("srsName"):
            return elem.get("srsName")
    return ""


@dataclass
class Plan:
    copies: int
    cols: int
    dx: float
    dy: float
    count: int

    def shift(self, k):
        return (k % self.cols) * self.dx, (k // self.cols) * self.dy


def make_plan(source: Source, count: int, dx=None, dy=None, gap=20.0) -> Plan:
    per = source.features_per_copy
    if per == 0:
        raise ValueError("source has no cityObjectMember/featureMember")
    copies = math.ceil(count / per)
    cols = math.ceil(math.sqrt(copies))
    b = source.bounds
    if dx is None:
        dx = math.ceil((b.hi[0] - b.lo[0] + gap) / 10.0) * 10.0
    if dy is None:
        dy = math.ceil((b.hi[1] - b.lo[1] + gap) / 10.0) * 10.0
    return Plan(copies, cols, float(dx), float(dy), count)


def _emitted(source: Source, plan: Plan):
    """Yield (copy, member) pairs in output order, stopping at plan.count features."""
    left = plan.count
    for k in range(plan.copies):
        for m in source.members:
            if m.is_feature:
                if left <= 0:
                    continue
                left -= 1
            yield k, m


def _root_namespaces(source: Source) -> dict:
    """prefix -> uri for the root element: the source declarations plus every
    prefix the compiler had to invent, so no written prefix is ever unbound."""
    declared = dict(source.namespaces)
    for uri, prefix in source.prefixes.items():
        if declared.get(prefix) != uri:
            declared.setdefault(prefix, uri)
    return declared


def write_tiled(source: Source, plan: Plan, out_path: str, *, strip_envelopes=False) -> dict:
    model = Bounds()
    for k, m in _emitted(source, plan):
        if not m.bounds.empty:
            model.union(m.bounds, *plan.shift(k))

    gml = source.prefixes[source.gml_ns]
    q = _Compiler(source.prefixes, set(), source.gml_ns, source.version, False, False, 3)._qname
    features = footprints = 0
    with open(out_path, "w", encoding="utf-8", newline="\n", buffering=1 << 20) as f:
        f.write("<?xml version='1.0' encoding='UTF-8'?>\n")
        f.write("<" + q(source.root_tag))
        for prefix, uri in _root_namespaces(source).items():
            f.write(f' xmlns{":" + prefix if prefix else ""}="{_esc_attr(uri)}"')
        for key, value in source.root_attrib.items():
            f.write(f' {q(key)}="{_esc_attr(value)}"')
        f.write(">\n")
        for child in source.header_children:
            f.write("  " + child + "\n")
        if not strip_envelopes and not model.empty:
            dim = source.srs_dim
            lo = " ".join(f"{v:.3f}" for v in model.lo[:dim])
            hi = " ".join(f"{v:.3f}" for v in model.hi[:dim])
            srs = f' srsName="{_esc_attr(source.srs_name)}"' if source.srs_name else ""
            f.write(f'  <{gml}:boundedBy><{gml}:Envelope{srs} srsDimension="{dim}">'
                    f"<{gml}:lowerCorner>{lo}</{gml}:lowerCorner>"
                    f"<{gml}:upperCorner>{hi}</{gml}:upperCorner>"
                    f"</{gml}:Envelope></{gml}:boundedBy>\n")
        for k, m in _emitted(source, plan):
            dx, dy = plan.shift(k)
            f.write("  " + m.render(f"_t{k}", dx, dy) + "\n")
            if m.is_feature:
                features += 1
                footprints += m.footprints
        f.write(f"</{q(source.root_tag)}>\n")
    return {
        "features": features,
        "footprints": footprints,
        "copies": plan.copies,
        "grid": [plan.cols, math.ceil(plan.copies / plan.cols)],
        "step": [plan.dx, plan.dy],
        "envelope": None if model.empty or strip_envelopes else [model.lo, model.hi],
    }


def generate(src, out, count, *, dx=None, dy=None, gap=20.0,
             strip_envelopes=False, with_lod0=False) -> dict:
    t0 = time.perf_counter()
    source = load_source(src, strip_envelopes=strip_envelopes, with_lod0=with_lod0)
    plan = make_plan(source, count, dx, dy, gap)
    stats = write_tiled(source, plan, out, strip_envelopes=strip_envelopes)
    stats.update({
        "source": os.path.abspath(src),
        "output": os.path.abspath(out),
        "citygml_version": source.version,
        "strip_envelopes": strip_envelopes,
        "with_lod0": with_lod0,
        "bytes": os.path.getsize(out),
        "seconds": round(time.perf_counter() - t0, 3),
    })
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument("--count", type=int, required=True, help="number of top-level features")
    p.add_argument("--dx", type=float, help="grid step in X (default: source width + gap)")
    p.add_argument("--dy", type=float, help="grid step in Y (default: source height + gap)")
    p.add_argument("--gap", type=float, default=20.0)
    p.add_argument("--strip-envelopes", action="store_true")
    p.add_argument("--with-lod0", action="store_true")
    p.add_argument("--stats-json", help="also write the stats to this file")
    a = p.parse_args(argv)
    try:
        stats = generate(a.source, a.output, a.count, dx=a.dx, dy=a.dy, gap=a.gap,
                         strip_envelopes=a.strip_envelopes, with_lod0=a.with_lod0)
    except ValueError as exc:
        p.error(str(exc))
    stats["peak_rss_mb"] = round(peak_rss_bytes() / 2**20, 1)
    print(json.dumps(stats, indent=2))
    if a.stats_json:
        with open(a.stats_json, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)


if __name__ == "__main__":
    main()
