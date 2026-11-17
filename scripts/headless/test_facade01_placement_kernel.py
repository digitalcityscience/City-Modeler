"""Facade-authoring ticket 01: headless placement kernel regression.

Proves ``shared/facade_placement.place_semantic_element`` authors one Window
entirely in pure Python - no UI, no operator, no selection - and that the result
is a well-formed, independent, export-valid paired opening (ADR 0004 D1/D2/D5/D7/
D10). This is both the ticket-01 acceptance test and the deep-learning-readiness
proof: the three arguments are constructed as plain data and fed straight to the
service.

Steps:
  1. Import a real LoD3 CityGML 3 Building (use_semantic_objects ON) so a
     WallSurface is its own exploded object.
  2. Build a SurfaceContext from one wall's exterior face (a minimal inline
     resolver - ticket 02 generalizes this).
  3. Call place_semantic_element with a Window definition + a centred placement.
  4. Assert: own mesh-data block (not shared), freshly minted ids, opening link
     on both fill and wall ring, parented under the wall, structure mirrors the
     imported/exploded opening shape.
  5. Export to CityGML 3.0 AND 2.0: the ring/opening sync guard must pass and
     xmllint --schema must exit 0 for each.

Usage:
    SP=/tmp/cgml-probe-facade01
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_facade01_placement_kernel.py -- "$SP/out"
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
from mathutils import Vector

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
    """A WallSurface object and one of its own exterior (non-interior-ring)
    face indices - the host face a new Window is cut into."""
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
    """Minimal inline resolver (ticket 02 generalizes): a min-corner frame on
    the host face."""
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


def all_polygon_ids():
    ids = set()
    for mat in bpy.data.materials:
        pid = str(mat.get("gml_polygon_id") or "").strip()
        if pid:
            ids.add(pid)
    return ids


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


def main():
    a = args()
    out_dir = a[0] if a else "/tmp/facade01-out"
    os.makedirs(out_dir, exist_ok=True)

    fresh_import(FIXTURE_V3)
    wall, face_index = a_wall_with_exterior_face()
    ctx = build_surface_context(wall, face_index)

    ids_before = all_polygon_ids()
    objs_before = {o.name for o in bpy.data.objects}
    wall_mesh = wall.data

    from city_modeler.shared.facade_placement import (
        place_semantic_element,
        FacadeElementDefinition,
        PlacementRequest,
        OPENING_LINK_KEY,
    )

    definition = FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2)
    placement = PlacementRequest(position_uv=(0.5, 0.5), source="manual_draw")

    fill = place_semantic_element(definition, ctx, placement)

    # --- structural assertions -------------------------------------------
    check("a new fill object was created", fill is not None and fill.name not in objs_before)
    check("fill is parented under the WallSurface", fill.parent is wall,
          f"parent={fill.parent}")
    check("fill has its own mesh-data block (no linked duplicate)",
          fill.data is not wall_mesh and fill.data.users == 1,
          f"data={fill.data.name} users={fill.data.users}")

    fill_mat = fill.data.materials[0] if fill.data.materials else None
    fill_pid = str(fill_mat.get("gml_polygon_id") or "") if fill_mat else ""
    check("fill polygon id is freshly minted (not a reused source id)",
          bool(fill_pid) and fill_pid not in ids_before, f"pid={fill_pid!r}")
    check("fill carries opening structure metadata",
          str(fill.get("structure_part")) == "opening"
          and str(fill.get("filling_parent_surface_id") or "") == str(wall.get("gml_id") or ""),
          f"structure_part={fill.get('structure_part')} "
          f"filling_parent={fill.get('filling_parent_surface_id')} wall={wall.get('gml_id')}")

    link = str(fill.get(OPENING_LINK_KEY) or "")
    check("fill carries an opening link", bool(link), f"link={link!r}")
    ring_linked = any(
        str(m.get(OPENING_LINK_KEY) or "") == link and m.get("Interior")
        for m in wall.data.materials
    )
    check("the wall interior ring carries the same opening link (D2)", bool(link) and ring_linked)

    # --- centred-prism regression ---------------------------------------
    # 1e-5: Blender stores float32 vertices; ~30 m from the origin one step is 2-4 um
    # The host opening ring is the source-plane contract.  It stays exactly
    # where the cutter placed it; only the new Window is given thickness.
    ring_face = next(
        face for face in wall.data.polygons
        if str(wall.data.materials[face.material_index].get(OPENING_LINK_KEY) or "") == link
    )
    ring_before = [wall.matrix_world @ wall.data.vertices[vi].co.copy() for vi in ring_face.vertices]
    check("authored opening ring remains on its original host-wall plane",
          all(abs((vertex - ring_before[0]).dot(ctx.normal_world)) < 1e-5 for vertex in ring_before))

    signed = [(fill.matrix_world @ vertex.co - ring_before[0]).dot(ctx.normal_world)
              for vertex in fill.data.vertices]
    check("authored Window depth is 0.04 m", abs(max(signed) - min(signed) - 0.04) < 1e-5,
          f"range=({min(signed):.6f}, {max(signed):.6f})")
    check("authored Window extends +/- 0.02 m along the host normal",
          abs(min(signed) + 0.02) < 1e-5 and abs(max(signed) - 0.02) < 1e-5,
          f"range=({min(signed):.6f}, {max(signed):.6f})")

    # --- export + schema validation --------------------------------------
    for version, harness, wrapper in (
        ("3.0", HARNESS_V3, "wrapper_v3.xsd"),
        ("2.0", HARNESS_V2, "wrapper_v2.xsd"),
    ):
        out_path = os.path.join(out_dir, f"facade01_authored_v{version.replace('.', '')}.gml")
        succeeded, err = export_result(out_path, version)
        check(f"export CityGML {version} passes the ring/opening sync guard", succeeded, err)
        if succeeded:
            run_xmllint(f"v{version}", Path(out_path), harness, wrapper)

    # Re-import the CityGML 3 output: custom Blender authoring properties do
    # not participate in this assertion, so this proves the prism itself—not
    # just in-memory state—survives the round trip.
    v3_path = Path(out_dir) / "facade01_authored_v30.gml"
    if v3_path.exists():
        authored_id = str(fill.get("gml_id"))
        fresh_import(v3_path)
        restored = [obj for obj in bpy.data.objects
                    if str(obj.get("gml_id") or "") == authored_id and obj.type == "MESH"]
        if not restored:
            check("export/re-import preserves authored Window prism", False, "authored Window not found")
        else:
            restored_signed = [
                (obj.matrix_world @ vertex.co - ring_before[0]).dot(ctx.normal_world)
                for obj in restored for vertex in obj.data.vertices
            ]
            check("export/re-import preserves authored Window prism",
                  abs(max(restored_signed) - min(restored_signed) - 0.04) < 1e-5
                  and abs(min(restored_signed) + 0.02) < 1e-5
                  and abs(max(restored_signed) - 0.02) < 1e-5,
                  f"range=({min(restored_signed):.6f}, {max(restored_signed):.6f})")
            restored_materials = [mat for obj in restored for mat in obj.data.materials if mat]
            restored_alpha = []
            for mat in restored_materials:
                bsdf = next((node for node in mat.node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None) \
                    if mat.use_nodes and mat.node_tree else None
                if bsdf is not None and bsdf.inputs.get("Alpha") is not None:
                    restored_alpha.append(float(bsdf.inputs["Alpha"].default_value))
            # transparency is written as 1 - alpha and read back as 1 - transparency,
            # so the authored glass alpha survives the round trip unchanged
            from city_modeler.shared.facade_placement import _AUTHORED_WINDOW_GLASS_RGBA
            authored_alpha = _AUTHORED_WINDOW_GLASS_RGBA[3]
            check("export/re-import preserves readable Window glass transparency",
                  any(abs(alpha - authored_alpha) < 1e-5 for alpha in restored_alpha),
                  f"alphas={restored_alpha} authored={authored_alpha}")

    if FAILURES:
        print(f"\n[FAIL] facade ticket 01 NOT satisfied ({len(FAILURES)} failure(s)):")
        for m in FAILURES:
            print(f"  {m}")
        sys.exit(1)
    print("\n[ALL PASS] facade ticket 01 placement kernel satisfied")


main()
