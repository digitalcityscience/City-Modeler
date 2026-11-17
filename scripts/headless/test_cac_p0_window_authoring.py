"""CAC-P0.5: lock headless Window authoring identity and its export.

Follows test_facade01_placement_kernel.py: import Railway LoD3 CityGML 3
(use_semantic_objects on), take the first WallSurface with an exterior
face, and call place_semantic_element for a Window - no UI, no operator.

Checked directly (not only via the golden):
- fresh gml_id / polygon id, own mesh block, fill parented under the wall,
  filling_parent_surface_id = wall gml_id,
- cgml_opening_link on the fill and on exactly one wall interior-ring material,
- export 2.0 and 3.0: the new Window is nested under that WallSurface and
  xmllint passes.
The normalised identity summary and both export fingerprints are locked in
golden/cac/window_authoring.json.

Stamp-from-flat-source and depth-0 cases are AB-03/AB-04 xfails (P0.7).

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p0_window_authoring.py -- <out_dir> [--update-goldens]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

FIXTURE = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
FILL_KEYS = ("gml_id", "cgml3_feature", "cgml_semantic_type", "cgml_source_role",
             "filling_parent_surface_id", "cgml_opening_link", "structure_part", "lod", "cgml3_lod")
MATERIAL_KEYS = ("con_surface_id", "gml_polygon_id", "gml_ring_id", "SurfaceTyp",
                 "opening_gml_id", "Interior", "cgml_opening_link", "lod")


def props(idblock, keys):
    return {k: h._plain(idblock[k]) for k in keys if k in idblock}


def main():
    import bpy

    R = h.Results(__file__)
    h.update_goldens_requested()
    out = h.out_dir()
    source_ids = {i for ids in h.collect_ids(FIXTURE.read_text(encoding="utf-8")).values() for i in ids}

    h.import_fixture(FIXTURE, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    wall_id = str(wall.get("gml_id") or "")
    ctx = h.surface_context(wall, face_index)
    objects_before = {o.name for o in bpy.data.objects}
    ids_before = {str(o.get("gml_id")) for o in bpy.data.objects if o.get("gml_id")}
    polys_before = {str(m.get("gml_polygon_id")) for m in bpy.data.materials if m.get("gml_polygon_id")}

    from city_modeler.shared.facade_placement import (
        OPENING_LINK_KEY, FacadeElementDefinition, PlacementRequest, place_semantic_element,
    )

    # AB-17 fix: (0.5, 0.5) on this wall/face lands in the Railway arched
    # ground cut-out, outside the face's real solid area - find a position
    # that actually lands on solid wall instead of hand-picking a magic
    # number (test-setup coordinate change only, no golden touched).
    position_uv = h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1)
    fill = place_semantic_element(
        FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2),
        ctx,
        PlacementRequest(position_uv=position_uv, source="manual_draw"),
    )

    # --- identity --------------------------------------------------------
    R.check("fill_created", fill is not None and fill.name not in objects_before, f"fill={fill}")
    fill_id = str(fill.get("gml_id") or "")
    R.check("fill_gml_id_fresh", bool(fill_id) and fill_id not in ids_before and fill_id not in source_ids,
            f"gml_id={fill_id!r}")
    fill_polys = [str(m.get("gml_polygon_id") or "") for m in fill.data.materials if m]
    R.check("fill_polygon_ids_fresh", bool(fill_polys) and all(p and p not in polys_before for p in fill_polys),
            f"polygon ids={fill_polys}")
    R.check("fill_parent_is_wall", fill.parent is wall, f"parent={fill.parent}")
    R.check("fill_own_mesh", fill.data is not wall.data and fill.data.users == 1,
            f"data={fill.data.name} users={fill.data.users}")
    R.check("fill_filling_parent_surface_id", str(fill.get("filling_parent_surface_id") or "") == wall_id,
            f"filling_parent_surface_id={fill.get('filling_parent_surface_id')!r} wall={wall_id!r}")
    link = str(fill.get(OPENING_LINK_KEY) or "")
    ring_mats = [m for m in wall.data.materials
                 if m and m.get("Interior") and str(m.get(OPENING_LINK_KEY) or "") == link]
    R.check("opening_link_on_fill_and_one_ring", bool(link) and len(ring_mats) == 1,
            f"link={link!r} ring materials={[m.name for m in ring_mats]}")

    identity = {
        "wall": {"name": wall.name, "gml_id": wall_id},
        "fill": {
            "props": props(fill, FILL_KEYS),
            "materials": [props(m, MATERIAL_KEYS) for m in fill.data.materials if m],
            "vertex_count": len(fill.data.vertices),
            "face_count": len(fill.data.polygons),
        },
        "ring_materials": [props(m, MATERIAL_KEYS) for m in ring_mats],
    }

    # --- export ----------------------------------------------------------
    golden = {"identity": h.normalise_json(identity, source_ids)}
    for version in ("3.0", "2.0"):
        tag = f"v{version[0]}"
        path = out / f"window_authoring_{tag}.gml"
        text = h.export_scene(path, version)
        fp = h.export_fingerprint(text, source_ids | {fill_id})
        R.check(f"export_{tag}_window_nested_under_wall", [wall_id, fill_id] in fp["opening_nesting"],
                f"pairs with the new Window: {[p for p in fp['opening_nesting'] if fill_id in p]}")
        lint = h.xmllint(path, version)
        R.check(f"export_{tag}_xmllint", lint["exit"] == 0, str(lint))
        golden[f"export_{tag}"] = dict(h.export_fingerprint(text, source_ids), xmllint=lint)

    R.check("golden", *h.compare_golden("window_authoring", golden))
    R.finish()


main()
