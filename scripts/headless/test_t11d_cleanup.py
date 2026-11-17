"""T11d cleanup: edge cases of the shared builder, checked against golden scenes.

Every synthetic document is imported through the builder and the scene summaries are compared with the
golden ones (``golden/t11d_scenes.json.gz``, see ``t11d_golden.py``). Also: implicit instances below a
transformed parent keep their world placement, an exported implicit scene reads back as the same scene,
and the F3 exports equal the golden ones apart from generated ids.

Usage (isolated profile, see tickets/README.md):
  blender -b --python-exit-code 1 --python scripts/headless/test_t11d_cleanup.py
"""
import importlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import t11a_import_snapshot as snap  # noqa: E402
import t11d_golden as g  # noqa: E402
from addon_test_utils import ensure_addon_enabled  # noqa: E402

failures = []
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
GOLDEN = g.load()


def check(ok, message):
    print(("  [ok]   " if ok else "  [FAIL] ") + message)
    if not ok:
        failures.append(message)


SQ = "0 0 0 4 0 0 4 4 0 0 4 0 0 0 0"
SQ_HOLE = "1 1 0 1 2 0 2 2 0 2 1 0 1 1 0"


def poly(pid, ring=SQ, hole=None):
    interior = (f'<gml:interior><gml:LinearRing><gml:posList srsDimension="3">{hole}</gml:posList>'
                '</gml:LinearRing></gml:interior>') if hole else ""
    ident = f' gml:id="{pid}"' if pid else ""
    return (f'<gml:Polygon{ident}><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">{ring}'
            f'</gml:posList></gml:LinearRing></gml:exterior>{interior}</gml:Polygon>')


def ms(pid, **kw):
    return f'<gml:MultiSurface><gml:surfaceMember>{poly(pid, **kw)}</gml:surfaceMember></gml:MultiSurface>'


def head(schema):
    if schema == "2.0":
        return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" '
                'xmlns:tran="http://www.opengis.net/citygml/transportation/2.0" '
                'xmlns:frn="http://www.opengis.net/citygml/cityfurniture/2.0" '
                'xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml">')
    return ('<core:CityModel xmlns:core="http://www.opengis.net/citygml/3.0" '
            'xmlns:tran="http://www.opengis.net/citygml/transportation/3.0" '
            'xmlns:frn="http://www.opengis.net/citygml/cityfurniture/3.0" '
            'xmlns:bldg="http://www.opengis.net/citygml/building/3.0" xmlns:gml="http://www.opengis.net/gml/3.2">')


def document(schema, *members):
    body = "".join(f"<core:cityObjectMember>{m}</core:cityObjectMember>" for m in members)
    return '<?xml version="1.0" encoding="UTF-8"?>' + head(schema) + body + "</core:CityModel>"


def implicit(fid, schema, lod="lod3", pid="IP", matrix="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1", point="10 10 0",
             hole=None):
    ident = f' gml:id="{fid}"' if fid else ""
    return (f'<frn:CityFurniture{ident}><frn:{lod}ImplicitRepresentation><core:ImplicitGeometry>'
            f'<core:transformationMatrix>{matrix}</core:transformationMatrix>'
            f'<core:relativeGMLGeometry>{ms(pid, hole=hole)}</core:relativeGMLGeometry>'
            f'<core:referencePoint><gml:Point><gml:pos>{point}</gml:pos></gml:Point></core:referencePoint>'
            f'</core:ImplicitGeometry></frn:{lod}ImplicitRepresentation></frn:CityFurniture>')


def lod2(ns, pid, z=0):
    ring = SQ.replace("0 0 0", f"0 0 {z}", 1)
    return f'<{ns}:lod2MultiSurface>{ms(pid, ring=ring)}</{ns}:lod2MultiSurface>'


def road2(rid, areas, own=True, aux=""):
    ident = f' gml:id="{rid}"' if rid else ""
    return (f'<tran:Road{ident}>' + (lod2("tran", "RP_" + str(rid)) if own else "")
            + "".join(f'<tran:trafficArea><tran:TrafficArea{(" gml:id=" + chr(34) + a[0] + chr(34)) if a[0] else ""}>'
                      f'<tran:function>{a[2]}</tran:function>{lod2("tran", a[1], 1)}</tran:TrafficArea></tran:trafficArea>'
                      for a in areas) + aux + '</tran:Road>')


def road3(rid, sections, own=True):
    sec = ""
    for sid, spaces in sections:
        inner = ""
        for tsid, areas in spaces:
            ts_ident = f' gml:id="{tsid}"' if tsid else ""
            inner += (f'<tran:trafficSpace><tran:TrafficSpace{ts_ident}><tran:granularity>lane</tran:granularity>'
                      + "".join(f'<tran:trafficArea><tran:TrafficArea{(" gml:id=" + chr(34) + a[0] + chr(34)) if a[0] else ""}>'
                                f'<tran:function>{a[2]}</tran:function>{lod2("core", a[1], 1)}</tran:TrafficArea></tran:trafficArea>'
                                for a in areas) + '</tran:TrafficSpace></tran:trafficSpace>')
        sec += f'<tran:section><tran:Section gml:id="{sid}">{inner}</tran:Section></tran:section>'
    return f'<tran:Road gml:id="{rid}">' + (lod2("core", "RP_" + rid) if own else "") + sec + '</tran:Road>'


CURVE_ROAD = ('<tran:Road gml:id="R_CURVE"><tran:lod0Network><gml:GeometricComplex><gml:element>'
              '<gml:LineString><gml:posList srsDimension="3">0 0 0 5 5 0</gml:posList></gml:LineString>'
              '</gml:element></gml:GeometricComplex></tran:lod0Network></tran:Road>')

AUX = ('<tran:auxiliaryTrafficArea><tran:AuxiliaryTrafficArea gml:id="AUX1">' + lod2("tran", "AUXP", 2)
       + '</tran:AuxiliaryTrafficArea></tran:auxiliaryTrafficArea>')

DOCS = {
    # label: (schema, xml, scene properties)
    "2.0 Road, three TrafficAreas (one without id, one aux)":
        ("2.0", document("2.0", road2("R1", [("TA1", "P_TA1", "1"), (None, "P_TA2", "2"), ("TA3", "P_TA3", "3")],
                                      aux=AUX)), {}),
    "2.0 two Roads, TrafficAreas, no own geometry":
        ("2.0", document("2.0", road2("R1", [("TA1", "P1", "1"), ("TA2", "P2", "2")], own=False),
                         road2("R2", [("TA3", "P3", "3")], own=False)), {}),
    "3.0 Road, two Sections, TrafficSpaces, TrafficAreas (one without id)":
        ("3.0", document("3.0", road3("R1", [("S1", [("TS1", [("TA1", "P1", "1"), (None, "P2", "2")])]),
                                            ("S2", [("TS2", [("TA3", "P3", "3")]), (None, [("TA4", "P4", "4")])])])),
         {}),
    "3.0 Road without own geometry, Sections only":
        ("3.0", document("3.0", road3("R1", [("S1", [("TS1", [("TA1", "P1", "1")])])], own=False)), {}),
    "2.0 Road with only lod0Network": ("2.0", document("2.0", CURVE_ROAD), {}),
    "2.0 TrafficArea twice with the same gml:id":
        ("2.0", document("2.0", road2("R1", [("DUP", "P1", "1"), ("DUP", "P2", "2")])), {}),
    "2.0 two CityFurniture with the same gml:id":
        ("2.0", document("2.0", implicit("DUP", "2.0", pid="IP1"), implicit("DUP", "2.0", pid="IP2", point="20 20 0")), {}),
    "2.0 implicit, template in LoD 3, filter LoD 2 only":
        ("2.0", document("2.0", implicit("F1", "2.0")),
         {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": True, "lod_3": False, "lod_4": False}),
    "3.0 implicit, template in LoD 3, filter LoD 2 only":
        ("3.0", document("3.0", implicit("F1", "3.0")),
         {"use_lod_filter": True, "lod_0": False, "lod_1": False, "lod_2": True, "lod_3": False, "lod_4": False}),
    "2.0 implicit with gml:id filter (other furniture left out)":
        ("2.0", document("2.0", implicit("F1", "2.0"), implicit("F2", "2.0", pid="IP2", point="30 30 0")),
         {"use_gmlid_filter": True, "gmlid_filter": "F2"}),
    "3.0 implicit with bbox filter":
        ("3.0", document("3.0", implicit("F1", "3.0", point="10 10 0"), implicit("F2", "3.0", pid="IP2", point="500 500 0")),
         {"use_bbox_filter": True, "bbox_min_x": 0.0, "bbox_min_y": 0.0, "bbox_max_x": 100.0, "bbox_max_y": 100.0}),
    "2.0 implicit template with a hole":
        ("2.0", document("2.0", implicit("F1", "2.0", hole=SQ_HOLE)), {}),
    "3.0 implicit template with a hole":
        ("3.0", document("3.0", implicit("F1", "3.0", hole=SQ_HOLE)), {}),
    "2.0 implicit without gml:id": ("2.0", document("2.0", implicit(None, "2.0")), {}),
}



def compare_documents(tmp):
    for label, (schema, xml, props) in DOCS.items():
        path = os.path.join(tmp, "doc.gml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        print(label)
        notices, new, calls = g.import_new(path, props)
        if "lod0Network" in label:
            check(isinstance(notices, RuntimeError) and not new,
                  f"{label}: curve-only feature is out of scope, refused ({type(notices).__name__})")
            continue
        check(not isinstance(notices, Exception), f"{label}: builder import finished ({notices})")
        print(f"  builder: {len(calls)} payload(s), objects {sorted(new)}")
        if g.refreshing():
            GOLDEN[label] = new
        else:
            problems = snap.detail_diff(GOLDEN[label], new) if label in GOLDEN else [f"no golden scene for {label}"]
            for problem in problems[:12]:
                print("  DIFF", problem)
            check(not problems, f"{label}: scene equals the golden one")
        if "template with a hole" in label:
            check(any(d.get("polys") for d in new.values()), f"{label}: the template polygon is built")


def world_of_children(parent_matrix_scale=3.0):
    """Implicit instance below a parent object that has a scale and a location of its own."""
    from mathutils import Matrix
    g.reset()
    mesh = bpy.data.meshes.new("m")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    parent = bpy.data.objects.new("P", mesh.copy())
    child = bpy.data.objects.new("C", mesh)
    for ob in (parent, child):
        bpy.context.scene.collection.objects.link(ob)
    parent.matrix_world = Matrix.Translation((100, 0, 0)) @ Matrix.Scale(parent_matrix_scale, 4)
    bpy.context.view_layer.update()
    wanted = Matrix.Translation((10, 20, 30))
    child.matrix_world = wanted
    return parent, child, wanted


def parent_checks():
    print("parent with a matrix of its own, implicit child")
    parent, child, wanted = world_of_children()
    child.parent = parent                      # what the builder did
    bpy.context.view_layer.update()
    shifted = (child.matrix_world.translation - wanted.translation).length
    print(f"  plain 'child.parent = parent': world shifted by {shifted:.3f}")
    check(shifted > 1.0, "the plain assignment shifts a child below a transformed parent (the risk is real)")
    parent, child, wanted = world_of_children()
    builder = importlib.import_module(ensure_addon_enabled()[1] + ".common.unified_builder")
    builder._parent_keep_world(child, parent)
    bpy.context.view_layer.update()
    shifted = (child.matrix_world.translation - wanted.translation).length
    check(shifted < 1e-6, f"_parent_keep_world keeps the child's world position (shift {shifted:.2e})")


def world_matrices():
    """World matrices relative to the first object's location: a re-import may pick another scene origin
    (the exported reference points start at the lowest z), which moves every object by the same offset."""
    objs = sorted(bpy.data.objects, key=lambda o: o.name)
    base = objs[0].matrix_world.translation.copy()
    out = {}
    for o in objs:
        m = o.matrix_world.copy()
        m.translation -= base
        out[o.name] = tuple(round(v, 4) for row in m for v in row)
    return out


def roundtrip(tmp):
    for name in ("Implicit_CityGML2.gml", "Implicit_CityGML3.gml"):
        print(name + " import -> export -> import (default builder)")
        src = os.path.join(ROOT, "test_files/CityFurniture", name)
        _r, first, _c = g.import_new(src)
        first_matrices = world_matrices()
        first_meshes = len({o.data.name for o in bpy.data.objects if o.type == 'MESH'})
        out = os.path.join(tmp, "out_" + name)
        version = "2.0" if "CityGML2" in name else "3.0"
        res = bpy.ops.cgml3.export_gml_file(filepath=out, citygml_version=version)
        check(res == {'FINISHED'} and os.path.exists(out), f"{name}: export finished")
        if not os.path.exists(out):
            continue
        text = open(out, encoding="utf-8").read()
        n_templates = text.count("relativeGMLGeometry")
        n_matrix = text.count("transformationMatrix") // 2
        print(f"  exported: {n_templates} relativeGMLGeometry, {n_matrix} transformationMatrix, "
              f"{text.count('xlink:href')} xlink:href")
        check(n_matrix == 4, f"{name}: four instances exported with their own matrix ({n_matrix})")
        _r, second, _c = g.import_new(out)
        names1, names2 = sorted(first), sorted(second)
        second_matrices = world_matrices()
        check(first_matrices == second_matrices,
              f"{name}: every instance keeps its world matrix after the round trip")
        if first_matrices != second_matrices:
            for n in sorted(set(first_matrices) | set(second_matrices)):
                print("   ", n, first_matrices.get(n), "->", second_matrices.get(n))
        second_meshes = len({o.data.name for o in bpy.data.objects if o.type == 'MESH'})
        check(first_meshes == second_meshes, f"{name}: template meshes {first_meshes} -> {second_meshes}")
        check(len(names1) == len(names2), f"{name}: same object count after the round trip ({len(names1)} / {len(names2)})")
        check(sorted(d.get("polys") for d in first.values()) == sorted(d.get("polys") for d in second.values()),
              f"{name}: polygon counts per object are the same after the round trip")


def export_skeleton(text):
    import re
    text = re.sub(r'gml:id="[^"]*"', 'gml:id=""', text)
    text = re.sub(r'xlink:href="#[^"]*"', 'xlink:href="#"', text)
    text = re.sub(r"<((?:\w+:)?creationDate)>[^<]*", r"<\1>", text)
    # sorted lines: the semantic split numbers its objects with random ids, which can reorder siblings
    return sorted(text.splitlines())


def f3_export(tmp, name="CS1_lod3.gml", version="3.0"):
    """F3: an installation without gml:id gets other generated surface ids from the builder. The export
    must still be the same document apart from those ids (golden: the export when the baseline was made)."""
    import hashlib
    import re
    key = f"export {name} {version}"
    print(f"{key}, golden vs builder (F3)")
    src = os.path.join(ROOT, "test_files/Building/CitySmith", name)
    g.import_new(src)
    out = os.path.join(tmp, f"{version}_{name}")
    res = bpy.ops.cgml3.export_gml_file(filepath=out, citygml_version=version)
    check(res == {'FINISHED'}, f"{key}: export finished")
    text = open(out, encoding="utf-8").read()
    lines = export_skeleton(text)
    ids = re.findall(r'gml:id="([^"]*)"', text)
    summary = {"ids": len(ids), "unique": len(set(ids)) == len(ids), "lines": len(lines),
               "sha": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()}
    if g.refreshing():
        GOLDEN[key] = summary
        return
    old = GOLDEN[key]
    check(summary["ids"] == old["ids"], f"{key}: same number of gml:id ({old['ids']} / {summary['ids']})")
    check(summary["unique"] or not old["unique"], f"{key}: the builder's exported ids are unique")
    check(summary["sha"] == old["sha"], f"{key}: equal to the golden export apart from gml:id values "
          f"({old['lines']} / {summary['lines']} lines)")


def main():
    tmp = tempfile.mkdtemp(prefix="t11d_cleanup_")
    compare_documents(tmp)
    parent_checks()
    for name in ("CS1_lod3.gml", "CS1_lod1.gml"):
        for version in ("3.0", "2.0"):
            f3_export(tmp, name, version)
    roundtrip(tmp)
    if g.refreshing():
        g.save(GOLDEN)
    print("T11d cleanup:", "FAILED" if failures else "all checks passed")
    sys.exit(1 if failures else 0)


main()
