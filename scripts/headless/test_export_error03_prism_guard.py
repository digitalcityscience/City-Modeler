"""Headless regression for export-error ticket 03 (centered-prism guard).

``_make_centered_prism`` (``shared/facade_placement.py``) used to only check
``len(base) < 3`` - the raw vertex *index* count of the base face - which
silently accepted a face whose indices resolve to fewer than 3 *distinct*
world coordinates (Pattern A from the findings doc,
``docs/agent/export-error/opening-ring-degeneracy-findings.md``) or to zero
area, then either silently returned early or extruded it into a permanent
degenerate 6-face solid. It now validates the base with ticket 01's shared
``validate_ring`` helper (``shared/writer_geometry.py``) and raises
``ValueError`` instead - loudly, per the project's no-silent-fallback stance.

This function is shared by two callers with different error-reporting
conventions (the ticket text), both exercised here for real:

- Draw Window authoring: ``place_semantic_element`` ->
  ``_place_interior_ring_opening`` -> ``_make_centered_prism``. Its operator
  (``ops/facade_authoring.py`` ``CGML_OT_facade_draw.execute``) already
  catches ``except (ValueError, RuntimeError, NotImplementedError)`` and
  ``self.report``'s an ERROR (unmodified by this ticket) - exercised here by
  calling ``place_semantic_element`` directly, the same real, non-UI entry
  point facade ticket 01's own test uses. Openings_Cutter's own
  ``cleaned_polygon_points`` only dedupes *consecutive* duplicate points (the
  literal "A -> B -> B -> A" pattern), so a rectangle built from a
  degenerate ``SurfaceContext`` whose horizontal/vertical axes are parallel
  comes out collinear - 4 distinct points, zero area - and slips past that
  guard untouched, reaching ``_make_centered_prism`` exactly as before this
  ticket.
- Import-time synthetic depth (``apply_synthetic_opening_depth``, called
  with no try/except by ``common/unified_builder._give_openings_depth``),
  fed a single-polygon Window mesh collapsed
  to Pattern A (2 distinct points, 4 indices) directly - never touches
  Openings_Cutter at all, so this is the gap the findings doc calls the
  actual root-cause fix site.

Usage:
    SP=/tmp/cgml-probe-export-error03
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_export_error03_prism_guard.py
"""
import addon_utils
import bpy
from mathutils import Vector

ADDON = "city_modeler"
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"[PASS] {label}")
    else:
        msg = f"[FAIL] {label}" + (f" - {detail}" if detail else "")
        print(msg)
        FAILURES.append(msg)


def wall(name, y):
    """A 4m x 4m WallSurface quad facing +Y (same template as export-error
    ticket 02's headless test)."""
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([(-4, y, 0), (4, y, 0), (4, y, 4), (-4, y, 4)], [], [[0, 1, 2, 3]])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "WallSurface"
    obj["gml_id"] = f"{name}_ID"
    material = bpy.data.materials.new(f"{name}_material")
    material["con_surface_id"] = obj["gml_id"]
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def surface_context(wall_obj):
    """A degenerate SurfaceContext for ``wall_obj``: horizontal and vertical
    axes are parallel, so any rectangle built from it is collinear (zero
    area) rather than merely point-collapsed - the case
    ``cleaned_polygon_points``' consecutive-duplicate dedup can't catch."""
    from city_modeler.shared.facade_placement import SurfaceContext

    y = wall_obj.matrix_world.translation.y
    return SurfaceContext(
        object_name=wall_obj.name,
        semantic_id=str(wall_obj.get("gml_id") or ""),
        semantic_type="WallSurface",
        building_id="",
        face_indices=[0],
        origin_world=Vector((0.0, y, 2.0)),
        normal_world=Vector((0.0, -1.0, 0.0)),
        horizontal_axis_world=Vector((1.0, 0.0, 0.0)),
        vertical_axis_world=Vector((1.0, 0.0, 0.0)),
        width=8.0,
        height=4.0,
    )


def pattern_a_window(name):
    """A single-polygon Window mesh whose 4 vertex indices resolve to only 2
    distinct world positions (findings doc Pattern A: A -> B -> B -> A) -
    exactly the shape ``apply_synthetic_opening_depth`` receives with no
    Openings_Cutter guard in between."""
    a = (-1.0, -0.1, -0.5)
    b = (1.0, -0.1, -0.5)
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([a, b, b, a], [], [[0, 1, 2, 3]])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "WindowSurface"
    obj["gml_id"] = f"{name}_ID"
    material = bpy.data.materials.new(f"{name}_material")
    material["gml_polygon_id"] = f"{name}_POLY"
    mesh.materials.append(material)
    return obj


def test_draw_window_path_rejects_collinear_base():
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition,
        PlacementRequest,
        place_semantic_element,
    )

    host = wall("dw_wall", 0.0)
    ctx = surface_context(host)
    definition = FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.4)
    placement = PlacementRequest(position_uv=(0.5, 0.5), source="manual_draw")

    objects_before = set(bpy.data.objects.keys())

    raised = None
    try:
        place_semantic_element(definition, ctx, placement)
    except ValueError as e:
        raised = e
    except Exception as e:  # pragma: no cover - would itself be a test failure
        raised = e

    check("Draw Window: collinear base raises ValueError", isinstance(raised, ValueError), f"raised={raised!r}")
    error = str(raised) if raised is not None else ""
    check(
        "Draw Window: error explains the degeneracy",
        "degenerate" in error.lower() or "distinct" in error.lower(),
        f"error={error!r}",
    )

    new_objects = [bpy.data.objects[n] for n in (set(bpy.data.objects.keys()) - objects_before)]
    fill_candidates = [o for o in new_objects if o.type == "MESH" and o.data is not host.data]
    check(
        "Draw Window: no degenerate 6-face prism was produced from the collinear base",
        all(len(o.data.polygons) < 6 for o in fill_candidates),
        f"fill candidate face counts={[len(o.data.polygons) for o in fill_candidates]}",
    )


def test_synthetic_depth_path_rejects_pattern_a():
    from city_modeler.shared.facade_placement import (
        SYNTHETIC_OPENING_DEPTH_KEY,
        apply_synthetic_opening_depth,
    )

    win = pattern_a_window("syn_pattern_a")
    verts_before = len(win.data.vertices)
    polys_before = len(win.data.polygons)

    raised = None
    try:
        apply_synthetic_opening_depth(win, Vector((0.0, -1.0, 0.0)), "Window")
    except ValueError as e:
        raised = e
    except Exception as e:  # pragma: no cover - would itself be a test failure
        raised = e

    check("Synthetic depth: Pattern A base raises ValueError", isinstance(raised, ValueError), f"raised={raised!r}")
    error = str(raised) if raised is not None else ""
    check(
        "Synthetic depth: error names the object and the reason",
        win.name in error and "distinct point" in error,
        f"error={error!r}",
    )
    check(
        "Synthetic depth: mesh left untouched (not exploded into a prism)",
        len(win.data.vertices) == verts_before and len(win.data.polygons) == polys_before,
        f"before verts={verts_before} polys={polys_before}; "
        f"after verts={len(win.data.vertices)} polys={len(win.data.polygons)}",
    )
    check(
        "Synthetic depth: no synthetic-depth marker set on refusal",
        win.get(SYNTHETIC_OPENING_DEPTH_KEY) is None,
        f"marker={win.get(SYNTHETIC_OPENING_DEPTH_KEY)!r}",
    )


def test_valid_flat_opening_still_prisms_via_synthetic_depth():
    """No-regression baseline: a clean flat opening must still extrude into
    the correct 4 cm centred prism exactly as before this ticket."""
    from city_modeler.shared.facade_placement import apply_synthetic_opening_depth

    verts = [(-1, -0.1, -0.5), (1, -0.1, -0.5), (1, -0.1, 0.5), (-1, -0.1, 0.5)]
    mesh = bpy.data.meshes.new("clean_window_mesh")
    mesh.from_pydata(verts, [], [[0, 1, 2, 3]])
    obj = bpy.data.objects.new("clean_window", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml3_feature"] = "WindowSurface"
    obj["gml_id"] = "clean_window_ID"
    material = bpy.data.materials.new("clean_window_material")
    material["gml_polygon_id"] = "clean_window_POLY"
    mesh.materials.append(material)

    applied = apply_synthetic_opening_depth(obj, Vector((0.0, -1.0, 0.0)), "Window")

    check("Clean source: synthetic depth applied", applied is True)
    check(
        "Clean source: prism has 8 vertices / 6 faces",
        len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6,
        f"verts={len(obj.data.vertices)} polys={len(obj.data.polygons)}",
    )


def main():
    addon_utils.enable(ADDON, default_set=False)

    test_draw_window_path_rejects_collinear_base()
    test_synthetic_depth_path_rejects_pattern_a()
    test_valid_flat_opening_still_prisms_via_synthetic_depth()

    if FAILURES:
        print(f"\n[FAIL] {len(FAILURES)} failure(s):")
        for m in FAILURES:
            print(f"  {m}")
        import sys
        sys.exit(1)
    print("\n[ALL PASS] centered-prism guard (export-error ticket 03) satisfied")


main()
