"""B45: balcony / chimney boundary surfaces follow the CityGML definitions (A3).

construction.xsd (3.0): OuterFloorSurface = outer shell, pointing upwards ("the floor of a
loggia"); OuterCeilingSurface = outer shell, pointing downwards; RoofSurface delimits roof
parts. core.xsd: ClosureSurface = virtual surface closing a volume. So:
- balcony: slab top OuterFloorSurface, slab underside OuterCeilingSurface, the slab face on the
  host wall ClosureSurface, slab edges WallSurface; B101: the railing is flat faces (no top, no
  underside), a WallSurface of its own facing away from the wall
- chimney: top RoofSurface, sides WallSurface, base (on the roof plane, B91) ClosureSurface
Every closure polygon of the balcony lies on the host wall plane or at floor level; the
chimney's closure faces down. One surface per class (B101: the balcony has two WallSurfaces,
slab edges and railing), XSD-valid, 2.0 and 3.0.

Usage (isolated profile, see tickets/README.md):
  BLENDER_USER_SCRIPTS=%TEMP%\\cgml-b01\\bscripts blender -b --factory-startup --python-use-system-env \\
      --python-exit-code 1 --python scripts/headless/test_b45_installation_surface_classes.py -- <out_dir>
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402
from test_b37_no_bookkeeping_attributes import author_everything  # noqa: E402
from test_b40_installation_surfaces import installations, ln  # noqa: E402

WANT = {"Balcony": {"OuterFloorSurface", "OuterCeilingSurface", "WallSurface", "ClosureSurface"},
        "Chimney": {"RoofSurface", "WallSurface", "ClosureSurface"}}


def closure_polys(path, name):
    """[[(x, y, z), ...]] of the ClosureSurface polygons of installation ``name``."""
    out = []
    for inst in ET.parse(path).iter():
        if ln(inst) != "BuildingInstallation" or next((c.text for c in inst if ln(c) == "name"), "") != name:
            continue
        for surf in inst.iter():
            if ln(surf) != "ClosureSurface":
                continue
            for pos in surf.iter():
                if ln(pos) == "posList":
                    v = [float(t) for t in pos.text.split()]
                    out.append([tuple(v[i:i + 3]) for i in range(0, len(v), 3)])
    return out


def roof_z(root, x, y):
    """Height of ``root``'s roof at world (x, y), or inf (no roof there)."""
    from mathutils import Vector
    for o in root.children_recursive:
        if o.get("cgml3_feature") == "RoofSurface" and o.type == 'MESH':
            inv = o.matrix_world.inverted()
            hit, loc, _n, _i = o.ray_cast(inv @ Vector((x, y, 1e4)), (inv.to_3x3() @ Vector((0, 0, -1))).normalized())
            if hit:
                return (o.matrix_world @ loc).z
    return float("inf")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[0] if argv else tempfile.mkdtemp(prefix="b45_")
    os.makedirs(out, exist_ok=True)
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    b = author_everything()
    front = next(o for o in b.children_recursive if o.get("cgml3_feature") == "WallSurface"
                 and (o.matrix_world.to_3x3() @ o.data.polygons[0].normal).y < -0.5)
    wall_y = (front.matrix_world @ front.data.vertices[0].co).y
    balcony = next(o for o in bpy.data.objects if o.get("cgml_semantic_type") == "Balcony")
    floor_z = min((balcony.matrix_world @ v.co).z for v in balcony.data.vertices) + 0.2  # slab is 0.2 m

    # B82: the class sets above held while every balcony was inside out (slab top ClosureSurface,
    # underside RoofSurface); check the orientation and the slab's two big faces directly.
    import bmesh
    from importlib import import_module
    from mathutils import Vector, geometry
    ia = import_module(f"{addon}.ops.installation_authoring")

    def signed_volume(mesh):
        bm = bmesh.new()
        bm.from_mesh(mesh)
        vol = bm.calc_volume(signed=True)
        bm.free()
        return vol

    chimney = next(o for o in bpy.data.objects if o.get("cgml_semantic_type") == "Chimney")
    check("Chimney: faces point outwards", signed_volume(chimney.data) > 0, f"{signed_volume(chimney.data):.3f}")
    for kind, mirror in (("PROJECTING", False), ("CORNER", False), ("CORNER", True), ("FRENCH", False)):
        for n in ((0, -1, 0), (1, 0, 0), (0.6, 0.8, 0)):
            name = f"{kind}{' mirrored' if mirror else ''} balcony facing {n}"
            boxes = ia.balcony_boxes(Vector(), Vector(n), kind, 3.0, 1.4, 1.0, mirror)
            strips = [b for b in boxes if isinstance(b[0], str)]
            check(f"{name}: B101 railing is one strip of flat faces", len(strips) == 1, str(len(strips)))
            for quad in (q for s in strips for q in ia._strip_quads(s)):  # base = the wall point (0, 0, 0)
                normal, mid = geometry.normal(quad), sum(quad, Vector()) / 4
                check(f"{name}: railing face points away from the wall point", normal.dot(mid) > 0,
                      f"{normal[:]} at {mid[:]}")
            if kind == 'FRENCH':  # no slab
                continue
            mesh = ia._boxes_mesh("b82_probe", [b for b in boxes if not isinstance(b[0], str)])
            vol = signed_volume(mesh)
            floors = [p for p in mesh.polygons if p.normal.z > 0.5 and abs(p.center.z) < 1e-6]
            bpy.data.meshes.remove(mesh)
            check(f"{name}: slab outwards", vol > 0, f"{vol:.3f}")
            check(f"{name}: one floor face", len(floors) == 1, str(len(floors)))  # B91: the corner slab is one L
    slab = sorted(balcony.data.polygons, key=lambda p: -p.area)[:2]
    got = {round((balcony.matrix_world @ p.center).z - floor_z, 2):
           balcony.data.materials[p.material_index]["surface_type"] for p in slab}
    check("balcony slab: top OuterFloorSurface, underside OuterCeilingSurface",
          got == {0.0: "OuterFloorSurface", -0.2: "OuterCeilingSurface"}, str(got))

    for v in ("2.0", "3.0"):
        p = os.path.join(out, f"b45_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        check(f"{v}: export", res == {'FINISHED'}, str(res))
        inst = installations(p)
        for name, classes in WANT.items():
            direct, surfs, _func, _ids = inst.get(name, ([], Counter(), None, []))
            check(f"{v} {name}: classes {sorted(classes)}", set(surfs) == classes, str(dict(surfs)))
            per_class = {c: 2 if (name, c) == ("Balcony", "WallSurface") else 1 for c in classes}  # B101 railing
            check(f"{v} {name}: one surface per class (balcony: slab edges + railing)", dict(surfs) == per_class,
                  str(dict(surfs)))
        for ring in closure_polys(p, "Balcony"):
            on_wall = all(abs(y - wall_y) < 1e-3 for _x, y, _z in ring)
            at_floor = all(abs(z - floor_z) < 1e-3 for _x, _y, z in ring)
            check(f"{v} Balcony: closure polygon on the wall plane or at floor level", on_wall or at_floor,
                  f"wall_y={wall_y:.3f} floor_z={floor_z:.3f} ring={ring[:2]}")
        chim = closure_polys(p, "Chimney")
        check(f"{v} Chimney: one closure polygon (the base)", len(chim) == 1, str(len(chim)))
        for ring in chim:  # B91: the base follows the roof slope, nothing of the chimney below the roof
            gap = [z - roof_z(b, x, y) for x, y, z in ring]
            check(f"{v} Chimney: base on the roof plane", gap and max(abs(g) for g in gap) < 1e-3, str(gap))
        valid, msg = xsd_valid(v, p)
        check(f"{v}: XSD valid", valid, msg[:300])

    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAIL: {FAILURES}")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
