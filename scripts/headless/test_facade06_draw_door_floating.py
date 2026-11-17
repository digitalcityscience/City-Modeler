"""Facade-authoring ticket 06 (floating-door slice): headless regression.

Proves the ADR 0004 D3 ground-vs-floating classifier
(``shared/facade_placement.door_touches_ground``) and that a *floating*
Door reuses ``InteriorRingOpeningStrategy`` exactly like a Window (own
mesh-data, freshly minted ids, opening link on both fill and wall ring,
independent semantic material) - while a ground-touching Door still cleanly
raises ``NotImplementedError`` (the ``ExteriorNotchStrategy`` is a
follow-up, not yet implemented). Also proves the explicit
``door_touches_ground`` override forces either topology regardless of the
drawn rectangle's position, and that both CityGML 2.0 and 3.0 export pass
the ring/opening sync guard + xmllint for the floating case.

Usage:
    SP=/tmp/cgml-probe-facade06
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_facade06_draw_door_floating.py -- "$SP/out"
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # cac_harness

import bpy

import cac_harness  # noqa: E402  (is_clean_face)

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_V3 = REPO_ROOT / "test_files" / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
HARNESS_V2 = REPO_ROOT / "scripts" / "headless" / "xmllint_v2"
HARNESS_V3 = REPO_ROOT / "scripts" / "headless" / "xmllint_v3"

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def ok(label):
    print(f"[PASS] {label}")


def check(label, condition, detail=""):
    if condition:
        ok(label)
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def fresh_import(gml):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=str(gml))
    assert res == {'FINISHED'}, f"import failed: {res}"


def a_wall_with_exterior_face():
    for obj in bpy.data.objects:
        feat = str(obj.get("cgml3_feature") or "")
        if feat != "WallSurface" or obj.type != "MESH":
            continue
        for face in obj.data.polygons:
            mat = obj.data.materials[face.material_index] if 0 <= face.material_index < len(obj.data.materials) else None
            if mat is not None and not mat.get("Interior") and mat.get("con_surface_id") \
                    and cac_harness.is_clean_face(obj, face):
                return obj, face.index
    raise AssertionError("fixture has no WallSurface with an exterior face")


def build_surface_context(wall, face_index):
    """Minimal inline resolver, mirroring ticket01's test (ticket 02 generalizes)."""
    from city_modeler.shared.facade_placement import SurfaceContext

    mesh = wall.data
    poly = mesh.polygons[face_index]
    mw = wall.matrix_world
    wps = [mw @ mesh.vertices[vi].co.copy() for vi in poly.vertices]
    n = (mw.to_3x3() @ poly.normal).normalized()
    v0 = wps[0]
    axis_u = (wps[1] - v0).normalized()
    axis_v = n.cross(axis_u).normalized()
    us = [(wp - v0).dot(axis_u) for wp in wps]
    vs = [(wp - v0).dot(axis_v) for wp in wps]
    min_u, min_v = min(us), min(vs)
    origin = v0 + axis_u * min_u + axis_v * min_v
    width = max(us) - min_u
    height = max(vs) - min_v

    building = wall
    while building is not None and str(building.get("cgml3_feature") or "") not in ("Building", "BuildingPart"):
        building = building.parent

    return SurfaceContext(
        object_name=wall.name,
        semantic_id=str(wall.get("gml_id") or ""),
        semantic_type="WallSurface",
        building_id=str(building.get("gml_id")) if building else "",
        face_indices=[face_index],
        origin_world=origin,
        normal_world=n,
        horizontal_axis_world=axis_u,
        vertical_axis_world=axis_v,
        width=width,
        height=height,
    )


def export_result(path, version):
    try:
        res = bpy.ops.cgml3.export_gml_file(filepath=str(path), citygml_version=version)
    except Exception as e:
        return False, str(e)
    return (res == {'FINISHED'}), ("" if res == {'FINISHED'} else f"result={res}")


def run_xmllint(label, out_path, harness_dir=None, wrapper_name=None):
    # cac_harness adds the local schema mirror: libxml2 >= 2.14 cannot fetch the http imports itself
    version = "3.0" if "3" in label.split()[0] else "2.0"
    try:
        lint = cac_harness.xmllint(out_path, version)
    except AssertionError as e:
        check(f"xmllint {label} exit 0", False, str(e))
        return
    check(f"xmllint {label} exit 0", lint["exit"] == 0, f"exit {lint['exit']}, {lint['errors']} error(s)")


def test_classifier_pure():
    """The ADR 0004 D3 classifier itself, in isolation from any mesh/export."""
    from city_modeler.shared.facade_placement import (
        DOOR_GROUND_TOLERANCE_M,
        FacadeElementDefinition,
        PlacementRequest,
        SurfaceContext,
        door_touches_ground,
    )
    from mathutils import Vector

    ctx = SurfaceContext(
        object_name="dummy", semantic_id="", semantic_type="WallSurface", building_id="",
        face_indices=[0], origin_world=Vector((0, 0, 0)), normal_world=Vector((0, -1, 0)),
        horizontal_axis_world=Vector((1, 0, 0)), vertical_axis_world=Vector((0, 0, 1)),
        width=4.0, height=3.0,
    )

    # Clearly floating: centred well above the bottom edge.
    floating_def = FacadeElementDefinition(semantic_type="Door", width=0.9, height=1.0)
    floating_placement = PlacementRequest(position_uv=(0.5, 0.7))
    check("classifier: centred high door is floating",
          door_touches_ground(ctx, floating_placement, floating_def) is False)

    # Exactly at the tolerance boundary (bottom_v == 0): touches ground.
    ground_def = FacadeElementDefinition(semantic_type="Door", width=0.9, height=1.0)
    ground_placement = PlacementRequest(position_uv=(0.5, (1.0 / 2.0) / ctx.height))
    check("classifier: door whose bottom sits exactly at v=0 touches ground",
          door_touches_ground(ctx, ground_placement, ground_def) is True)

    # Just outside the tolerance band, above ground: floating.
    just_floating_v = ((1.0 / 2.0) + DOOR_GROUND_TOLERANCE_M * 2.0) / ctx.height
    just_floating_def = FacadeElementDefinition(semantic_type="Door", width=0.9, height=1.0)
    just_floating_placement = PlacementRequest(position_uv=(0.5, just_floating_v))
    check("classifier: door just above the tolerance band is floating",
          door_touches_ground(ctx, just_floating_placement, just_floating_def) is False)

    # Explicit override always wins, regardless of geometry.
    forced_floating = FacadeElementDefinition(semantic_type="Door", width=0.9, height=1.0, door_touches_ground=False)
    check("classifier: door_touches_ground=False override forces floating even at v=0",
          door_touches_ground(ctx, ground_placement, forced_floating) is False)
    forced_ground = FacadeElementDefinition(semantic_type="Door", width=0.9, height=1.0, door_touches_ground=True)
    check("classifier: door_touches_ground=True override forces ground even when floating",
          door_touches_ground(ctx, floating_placement, forced_ground) is True)


def test_ground_door_notch(ctx):
    """A ground-touching Door is cut as an exterior notch (ExteriorNotchStrategy,
    implemented in 9a41201), not an interior ring: no opening link."""
    from city_modeler.shared.facade_placement import (
        OPENING_LINK_KEY,
        FacadeElementDefinition,
        PlacementRequest,
        place_semantic_element,
    )

    definition = FacadeElementDefinition(semantic_type="Door", width=0.9, height=2.1)
    # Bottom of the door lands at v=0 (the wall's own bottom edge).
    v = (2.1 / 2.0) / ctx.height
    placement = PlacementRequest(position_uv=(0.5, v), source="manual_draw")
    fill = place_semantic_element(definition, ctx, placement)
    check("ground-touching Door is placed as an exterior notch",
          fill is not None and str(fill.get("cgml_authoring_topology")) == "ground_notch",
          f"topology={fill.get('cgml_authoring_topology') if fill else None}")
    check("ground-notch Door carries no interior-ring opening link",
          fill is not None and not fill.get(OPENING_LINK_KEY))


def main():
    a = args()
    out_dir = a[0] if a else "/tmp/facade06-out"
    os.makedirs(out_dir, exist_ok=True)

    test_classifier_pure()

    fresh_import(FIXTURE_V3)
    wall, face_index = a_wall_with_exterior_face()
    ctx = build_surface_context(wall, face_index)

    test_ground_door_notch(ctx)

    # the notch reshaped the wall: the floating Door starts from an untouched one
    fresh_import(FIXTURE_V3)
    wall, face_index = a_wall_with_exterior_face()
    ctx = build_surface_context(wall, face_index)

    ids_before = {
        str(mat.get("gml_polygon_id") or "").strip()
        for mat in bpy.data.materials if str(mat.get("gml_polygon_id") or "").strip()
    }
    objs_before = {o.name for o in bpy.data.objects}
    wall_mesh = wall.data

    from city_modeler.shared.facade_placement import (
        OPENING_LINK_KEY,
        FacadeElementDefinition,
        PlacementRequest,
        place_semantic_element,
    )

    # A floating door: comfortably clear of the wall's bottom edge regardless
    # of this fixture's actual wall height.
    elem_h = min(1.0, ctx.height * 0.3)
    definition = FacadeElementDefinition(semantic_type="Door", width=0.9, height=elem_h)
    placement = PlacementRequest(position_uv=(0.5, 0.85), source="manual_draw")

    fill = place_semantic_element(definition, ctx, placement)

    check("a new fill object was created", fill is not None and fill.name not in objs_before)
    check("fill is parented under the WallSurface", fill.parent is wall, f"parent={fill.parent}")
    check("fill has its own mesh-data block (no linked duplicate)",
          fill.data is not wall_mesh and fill.data.users == 1,
          f"data={fill.data.name} users={fill.data.users}")
    check("fill is tagged as a Door", str(fill.get("semantic_type")) == "Door",
          f"semantic_type={fill.get('semantic_type')!r}")
    check("fill's cgml3_feature is a Door surface type",
          str(fill.get("cgml3_feature") or "").strip().lower() in ("door", "doorsurface"),
          f"cgml3_feature={fill.get('cgml3_feature')!r}")

    fill_mat = fill.data.materials[0] if fill.data.materials else None
    fill_pid = str(fill_mat.get("gml_polygon_id") or "") if fill_mat else ""
    check("fill polygon id is freshly minted (not a reused source id)",
          bool(fill_pid) and fill_pid not in ids_before, f"pid={fill_pid!r}")

    link = str(fill.get(OPENING_LINK_KEY) or "")
    check("fill carries an opening link", bool(link), f"link={link!r}")
    ring_linked = any(
        str(m.get(OPENING_LINK_KEY) or "") == link and m.get("Interior")
        for m in wall.data.materials
    )
    check("the wall interior ring carries the same opening link (D2)", bool(link) and ring_linked)

    # --- ring/opening pairing recognises the floating Door -----------------
    from city_modeler.shared.ring_opening_sync import (
        find_ring_opening_pairs,
        is_ring_paired_opening,
    )

    building = wall
    while building is not None and str(building.get("cgml3_feature") or "") not in ("Building", "BuildingPart"):
        building = building.parent
    check("is_ring_paired_opening recognises the floating Door", is_ring_paired_opening(fill))
    pairs = find_ring_opening_pairs(building) if building is not None else []
    check("find_ring_opening_pairs includes the floating Door",
          any(p.opening is fill for p in pairs), f"{len(pairs)} pair(s) found")

    # --- edit-exit validation scope now covers the floating Door -----------
    from city_modeler.shared.edit_exit_validation import is_relevant_for_edit_exit

    check("edit-exit validation treats the floating Door as relevant", is_relevant_for_edit_exit(fill))

    # --- export + schema validation -----------------------------------------
    for version, harness, wrapper in (
        ("3.0", HARNESS_V3, "wrapper_v3.xsd"),
        ("2.0", HARNESS_V2, "wrapper_v2.xsd"),
    ):
        out_path = os.path.join(out_dir, f"facade06_authored_door_v{version.replace('.', '')}.gml")
        succeeded, err = export_result(out_path, version)
        check(f"export CityGML {version} passes the ring/opening sync guard", succeeded, err)
        if succeeded:
            run_xmllint(f"v{version}", Path(out_path), harness, wrapper)

    if FAILURES:
        print(f"\n[FAIL] facade ticket 06 (floating-door slice) NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] facade ticket 06 floating-door slice satisfied")


main()
