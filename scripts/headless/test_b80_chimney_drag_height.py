"""B80: chimney like New Building: drag its rectangle on the roof face, then move the mouse / type its height;
five usual German chimney types.

- drag_to_chimney: the dragged rectangle (width x, depth y), each at least 0.2 m
- chimney_boxes / make_chimney: top at ``above_ridge`` over the ridge, a rectangle; the base is a
  ClosureSurface; B102: plates are one flat face, no hood posts, pots open at the bottom
- the lift (New Building's height phase): mouse up raises, typed digits + Enter set it, then
  chimney_add places it with that height
- Paste of a chimney from before B80 (no depth) stays square

Usage (isolated profile, see tickets/README.md):
  bash scripts/headless/run_regression.sh <out> test_b80_chimney_drag_height.py
"""
import os
import sys
import types
from importlib import import_module

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from addon_test_utils import ensure_addon_enabled, footprint  # noqa: E402
from test_b24_header_tool_strip import check, FAILURES  # noqa: E402
from test_b01_authored_building_export import xsd_valid  # noqa: E402


def zs(obj):
    return [(obj.matrix_world @ v.co).z for v in obj.data.vertices]


def main():
    ok, addon = ensure_addon_enabled()
    check("add-on enabled", ok, addon)
    ia = import_module(f"{addon}.ops.installation_authoring")

    roof = {"host": "B", "normal": (0, 0, 1)}
    c = ia.drag_to_chimney(roof, (0, 0, 9), (1.2, 0.5, 9), 'SQUARE', 0.6, 0.6)
    check("drag: width x depth of the rectangle, centred", abs(c["size"] - 1.2) < 1e-6
          and abs(c["depth"] - 0.5) < 1e-6 and abs(c["point"][0] - 0.6) < 1e-6, str(c))
    c = ia.drag_to_chimney(roof, (0, 0, 9), (1.2, 0.0, 9), 'SQUARE', 0.6, 0.6)
    check("a flat drag: depth at least 0.2 m", abs(c["depth"] - 0.2) < 1e-6, str(c))
    check("five types", [t[1] for t in ia.CHIMNEY_TYPES]
          == ["Brick", "Cover plate", "Rain hood", "Double flue", "Steel pipe"])

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.cgml.building_new(points=footprint())
    b = bpy.context.active_object
    ridge = max(z for o in b.children_recursive if o.get("cgml3_feature") == "RoofSurface" for z in zs(o))
    faces = {"SQUARE": 6, "COVER": 6, "HOOD": 7, "WIDE": 16, "ROUND": 18}  # B102: flat plates, no posts
    for i, (kind, _n, _d) in enumerate(ia.CHIMNEY_TYPES):
        ch = ia.make_chimney(bpy.context, b, (-3.0 + 1.5 * i, 0.0, ridge), (0, 0, 1), kind, 1.0, 1.2, 0.6)
        xs = [(ch.matrix_world @ v.co).x for v in ch.data.vertices]
        ys = [(ch.matrix_world @ v.co).y for v in ch.data.vertices]
        check(f"{kind}: {faces[kind]} faces, top 1.2 m above the ridge",
              len(ch.data.polygons) == faces[kind] and abs(max(zs(ch)) - (ridge + 1.2)) < 1e-4,
              f"{len(ch.data.polygons)} faces, top {max(zs(ch))}")
        if kind == 'SQUARE':
            check("Brick: 1.0 x 0.6 m rectangle", abs(max(xs) - min(xs) - 1.0) < 1e-4
                  and abs(max(ys) - min(ys) - 0.6) < 1e-4, f"{max(xs) - min(xs)} x {max(ys) - min(ys)}")
        if kind == 'COVER':
            classes = {m["SurfaceTyp"] for m in ch.data.materials}
            check("Cover plate: base closure, flat plate the roof (B102)",
                  {"ClosureSurface", "RoofSurface", "WallSurface"} == classes, str(classes))
        check(f"{kind}: params keep width and depth", ch["cgml_install_params"].to_dict()
              == {"kind": kind, "size": 1.0, "depth": 0.6, "above_ridge": 1.2})
    import tempfile
    out = tempfile.mkdtemp(prefix="b80_")
    for v in ("2.0", "3.0"):  # B102: flat plates and open-bottomed pots export valid
        p = os.path.join(out, f"b80_v{v[0]}.gml")
        res = bpy.ops.cgml3.export_gml_file(filepath=p, citygml_version=v)
        valid, msg = xsd_valid(v, p)
        check(f"{v}: five chimney types export XSD valid", res == {'FINISHED'} and valid, f"{res} {msg[:300]}")

    # the lift: New Building's height phase in the chimney drag
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = b

    class Lift(ia._FaceDraw):
        ADD, RAISE = "chimney_add", True

        def drag_args(self, _b):
            return {"host": b.name, "point": (0.0, 2.0, ridge), "normal": (0, 0, 1), "kind": 'COVER',
                    "size": 0.8, "depth": 0.5, "above_ridge": 0.8}

    ctx = types.SimpleNamespace(region_data=types.SimpleNamespace(view_distance=10.0),
                                region=types.SimpleNamespace(height=500),
                                area=types.SimpleNamespace(tag_redraw=lambda: None), workspace=None)

    def ev(t, value='NOTHING', y=0):
        return types.SimpleNamespace(type=t, value=value, alt=False, mouse_region_x=0, mouse_region_y=y)

    lift = Lift()
    check("release starts the height phase", lift._start_lift(ctx, None) == {'RUNNING_MODAL'})
    lift._lift_modal(ctx, ev('MOUSEMOVE', y=100))
    lift._lift_modal(ctx, ev('MOUSEMOVE', y=150))
    check("mouse up 50 px raises it (0.04 m/px)", abs(lift._args["above_ridge"] - 2.8) < 1e-6,
          str(lift._args["above_ridge"]))
    for key in ("ONE", "PERIOD", "FIVE"):
        lift._lift_modal(ctx, ev(key, 'PRESS'))
    check("typed 1.5", abs(lift._args["above_ridge"] - 1.5) < 1e-6, str(lift._args["above_ridge"]))
    n = len(b.children)
    res = lift._lift_modal(ctx, ev('RET', 'PRESS'))
    ch = bpy.context.active_object
    check("Enter places it", res == {'FINISHED'} and len(b.children) == n + 1 and ch.get("cgml_semantic_type")
          == "Chimney", str(res))
    check("... with the typed height", abs(max(zs(ch)) - (ridge + 1.5)) < 1e-4, f"{max(zs(ch))} vs {ridge + 1.5}")

    # a chimney from before B80 (no depth) pastes square
    ch["cgml_install_params"] = {"kind": 'SQUARE', "size": 0.9, "above_ridge": 0.5}
    from mathutils import Vector
    _op, args, why = ia.paste_args(bpy.context, ch, Vector((1.0, 1.0, ridge + 30)), Vector((0, 0, -1)))
    check("old chimney pastes square", args is not None and args["depth"] == 0.9, f"{args} {why}")


main()
print(f"B80 chimney drag height: {'ALL PASS' if not FAILURES else f'{len(FAILURES)} FAILED'}")
if FAILURES:
    sys.exit(1)
