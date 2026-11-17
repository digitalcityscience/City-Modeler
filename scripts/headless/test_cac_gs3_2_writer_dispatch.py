"""GS.3.2: flag-gated per-surface IR export dispatch, real v2/v3 export.

Scope: the actual wiring in ``io/writer/exporter.py`` and ``io_v2/writer/
exporter.py`` (the ``_cac_gs_surface_groups`` blocks next to the existing
CAC-P5.6/P5.7 ``_cac_ir_groups`` dispatch). Builds:

  - Building1/Wall1/Win1: fully IR-eligible (same shape as
    ``test_cac_p5_6_writer_dispatch.py``'s Building1) - proves Building-
    level IR dispatch still wins over the GS surface path when both would
    otherwise apply.
  - Building2/Wall2A (record-backed, GS-eligible)/Win2A/Wall2B (plain
    legacy, no record)/Door2 (legacy passthrough-shaped mesh): the core
    GS.3.2 proof - Building2 as a whole is NOT IR-eligible (Wall2B has no
    committed record), but Wall2A is individually GS-eligible and must
    export through the record/IR surface path while Wall2B and Door2 stay
    legacy, all in the same export, no duplication.
  - Building3/Wall3 (record-backed but geometrically degenerate exterior -
    collinear points, zero area): structurally GS-eligible per
    ``gs_surface_eligibility`` but rejected by the display-build safety
    net; must fall back to the legacy surface path without crashing the
    whole export.
  - Building4/Wall4 (record-backed, depth-authored Window - two polygons):
    GS-ineligible by construction; stays legacy.
  - Building5/Wall5 (record-backed, two hosted Windows, non-overlapping and
    correctly ring-paired): GS-eligible under the N-Window generalization
    (CAC-P3b.2, phase3b-design-note.md §7) - both WIN5A and WIN5B must
    export through the record/IR surface path, no duplication.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_gs3_2_writer_dispatch.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))
from shared.authoring_core.blender import display_builder  # noqa: E402
from shared.authoring_core.blender import display_regen  # noqa: E402
from shared.authoring_core.blender import gs_surface_dispatch as cac_gs_bpy_dispatch  # noqa: E402
from shared.authoring_core.blender import storage  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))

LEGACY_WORLD_EXT = ((20.0, 0.0, 0.0), (24.0, 0.0, 0.0), (24.0, 3.0, 0.0), (20.0, 3.0, 0.0))
DOOR_COORDS = ((22.0, 0.0, 0.0), (22.8, 0.0, 0.0), (22.8, 0.0, 2.0), (22.0, 0.0, 2.0))

DEPTH_WIN_FACES = (
    ((60.5, 0.0, 0.5), (61.5, 0.0, 0.5), (61.5, 0.0, 1.5), (60.5, 0.0, 1.5)),
    ((60.5, 0.1, 0.5), (61.5, 0.1, 0.5), (61.5, 0.1, 1.5), (60.5, 0.1, 1.5)),
)


def _addon_display_builder_module():
    """The add-on's OWN loaded ``display_builder`` module instance
    (``city_modeler.shared.authoring_core.blender.
    display_builder``) - a separate Python module object from the plain
    ``shared.authoring_core.blender.display_builder`` this test script
    imports directly off ``sys.path`` (REPO_ROOT prepended above). The
    running writer code inside the loaded add-on only ever sees ITS OWN
    module instance, so a fault must be injected there, not on this
    script's separately-imported copy."""
    for name, mod in list(sys.modules.items()):
        if name.endswith("shared.authoring_core.blender.display_builder") and name != "shared.authoring_core.blender.display_builder":
            return mod
    raise RuntimeError("add-on's display_builder module not found in sys.modules - load_addon() first")


def _force_display_build_error_for(surface_id):
    """Monkeypatch the running add-on's own ``display_builder.build_
    surface_display`` so it raises ``DisplayBuildError`` for exactly
    ``surface_id`` (every other surface builds normally). Simulates the
    degenerate-geometry safety net (task item 5) without needing genuinely
    degenerate coordinates - see ``build_degenerate_building``'s docstring
    for why real zero-area geometry cannot demonstrate this path. Returns
    ``(module, original_function)`` to pass to ``_restore_display_build``."""
    mod = _addon_display_builder_module()
    original = mod.build_surface_display

    def patched(record_set, sid):
        if sid == surface_id:
            raise mod.DisplayBuildError(f"forced failure for test: {sid!r}")
        return original(record_set, sid)

    mod.build_surface_display = patched
    return mod, original


def _restore_display_build(patch_state):
    mod, original = patch_state
    mod.build_surface_display = original


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _gs_flag(on):
    os.environ["CGML_GS_SURFACE_DISPATCH"] = "1" if on else "0"


def _building_object(name, **props):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["cgml_hierarchy_model"] = "semantic_objects"
    for key, value in props.items():
        obj[key] = value
    return obj


def _mesh_object(name, coords, parent=None, polygon_id=None, **props):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(coords), [], [list(range(len(coords)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    if polygon_id is not None:
        mat = bpy.data.materials.new(f"{name}_mat")
        mat["gml_polygon_id"] = polygon_id
        mesh.materials.append(mat)
    for key, value in props.items():
        obj[key] = value
    return obj


def _multi_face_mesh_object(name, faces, parent, polygon_ids, **props):
    """A mesh object with one quad face per entry in ``faces``, each face
    carrying its own material with ``gml_polygon_id`` from ``polygon_ids``
    (matching count/order) - the live-mesh shape P3.3's record/legacy
    consistency check expects for a multi-polygon (depth-authored) Window
    feature, unlike ``_mesh_object``'s single-face/single-material shape."""
    mesh = bpy.data.meshes.new(name)
    verts = [v for face in faces for v in face]
    face_indices = []
    offset = 0
    for face in faces:
        n = len(face)
        face_indices.append(list(range(offset, offset + n)))
        offset += n
    mesh.from_pydata(verts, [], face_indices)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    for i, poly_id in enumerate(polygon_ids):
        mat = bpy.data.materials.new(f"{name}_mat{i}")
        mat["gml_polygon_id"] = poly_id
        mesh.materials.append(mat)
        mesh.polygons[i].material_index = i
    for key, value in props.items():
        obj[key] = value
    return obj


def _legacy_mesh_object(name, coords, parent, polygon_id, surface_type, filling_parent_surface_id=None, **props):
    """A plain legacy mesh object recognized by the pure material-driven
    face scan (``io/writer/exporter.py``'s own ``st = ... mat.get(
    "SurfaceTyp")`` classification), for surface types the "default
    unclassified surface" fallback would otherwise mis-type as WallSurface
    - e.g. a legacy Door with no record at all. Classification lives on
    the MATERIAL (``SurfaceTyp``/``filling_parent_surface_id``), never on
    the object - the pure legacy per-face scan never reads object-level
    ``cgml3_feature`` (unlike the CAC record/IR or Door-passthrough
    adapters, which do)."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(coords), [], [list(range(len(coords)))])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    mat = bpy.data.materials.new(f"{name}_mat")
    mat["gml_polygon_id"] = polygon_id
    mat["SurfaceTyp"] = surface_type
    if filling_parent_surface_id:
        mat["filling_parent_surface_id"] = filling_parent_surface_id
    mesh.materials.append(mat)
    for key, value in props.items():
        obj[key] = value
    return obj


def build_ir_building():
    """Building1/Wall1/Win1 - fully IR-eligible, same shape as P5.6's own
    fixture. Proves Building-level dispatch still wins over the GS.3.2
    surface path when the whole Building already qualifies."""
    building_obj = _building_object("Building1", gml_id="BLD1", cgml3_feature="Building")
    wall_obj = _mesh_object(
        "Wall1", WORLD_EXT, parent=building_obj, polygon_id="P_WALL1",
        gml_id="WALL1", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    window_obj = _mesh_object(
        "Window1", WIN_EXT, parent=wall_obj, polygon_id="P_WIN1",
        gml_id="WIN1", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL1", cgml_opening_link="LINK1",
    )
    storage.write_record(building_obj, RecordSet(features=(FeatureRecord(gml_id="BLD1", kind="Building"),)))
    wall_record = FeatureRecord(
        gml_id="WALL1", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD1", owner_id="BLD1", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL1",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL1", feature_id="WALL1", geometry_state="committed",
        exterior=RingRecord("P_WALL1_0_", "exterior", WORLD_EXT),
        interiors=(RingRecord("P_WALL1_1_", "interior", RING_A, opening_link="LINK1"),),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))
    window_record = FeatureRecord(
        gml_id="WIN1", kind="Window", semantic_type="Window",
        parent_id="WALL1", host_id="WALL1", owner_id="BLD1",
        opening_link="LINK1", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN1",), generator="facade.window.rect",
    )
    window_polygon = PolygonRecord(
        polygon_id="P_WIN1", feature_id="WIN1", geometry_state="committed",
        exterior=RingRecord("P_WIN1_0_", "exterior", WIN_EXT),
    )
    storage.write_record(window_obj, RecordSet(features=(window_record,), polygons=(window_polygon,)))
    return building_obj, wall_obj, window_obj


def build_mixed_building(with_display_only=False):
    """Building2: Wall2A (record-backed, GS-eligible) + Win2A, Wall2B
    (plain legacy WallSurface, no record - makes Building2 as a whole NOT
    IR-eligible), Door2 (legacy Door mesh). The core GS.3.2 proof scenario
    (task item 8)."""
    building_obj = _building_object("Building2", gml_id="BLD2", cgml3_feature="Building")

    wall_a = _mesh_object(
        "Wall2A", WORLD_EXT, parent=building_obj, polygon_id="P_WALL2A",
        gml_id="WALL2A", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    win_a = _mesh_object(
        "Window2A", WIN_EXT, parent=wall_a, polygon_id="P_WIN2A",
        gml_id="WIN2A", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL2A", cgml_opening_link="LINK2A",
    )
    wall_a_record = FeatureRecord(
        gml_id="WALL2A", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD2", owner_id="BLD2", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL2A",),
    )
    wall_a_polygon = PolygonRecord(
        polygon_id="P_WALL2A", feature_id="WALL2A", geometry_state="committed",
        exterior=RingRecord("P_WALL2A_0_", "exterior", WORLD_EXT),
        interiors=(RingRecord("P_WALL2A_1_", "interior", RING_A, opening_link="LINK2A"),),
    )
    storage.write_record(wall_a, RecordSet(features=(wall_a_record,), polygons=(wall_a_polygon,)))
    win_a_record = FeatureRecord(
        gml_id="WIN2A", kind="Window", semantic_type="Window",
        parent_id="WALL2A", host_id="WALL2A", owner_id="BLD2",
        opening_link="LINK2A", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN2A",), generator="facade.window.rect",
    )
    win_a_polygon = PolygonRecord(
        polygon_id="P_WIN2A", feature_id="WIN2A", geometry_state="committed",
        exterior=RingRecord("P_WIN2A_0_", "exterior", WIN_EXT),
    )
    storage.write_record(win_a, RecordSet(features=(win_a_record,), polygons=(win_a_polygon,)))

    # Plain legacy WallSurface, deliberately no stored record at all - the
    # one non-Door feature that keeps Building2 out of Phase-5 whole-
    # Building IR eligibility (UNSUPPORTED_OR_INCOMPLETE_FEATURE). Carries
    # an explicit ``con_surface_id`` (material-level) so Door2's own
    # ``filling_parent_surface_id`` below can pair with it - the legacy
    # per-face scan's host-matching key is ``surf_id``, not gml_id/name.
    wall_b = _mesh_object(
        "Wall2B", LEGACY_WORLD_EXT, parent=building_obj, polygon_id="P_WALL2B",
        gml_id="WALL2B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    wall_b.data.materials[0]["con_surface_id"] = "SID_WALL2B"

    door_obj = _legacy_mesh_object(
        "Door2", DOOR_COORDS, wall_b, "P_DOOR2", "Door",
        filling_parent_surface_id="SID_WALL2B",
        gml_id="DOOR2", structure_part="opening", cgml3_lod=3,
    )

    display_obj = None
    if with_display_only:
        # GS.2 display-only auxiliary object for Wall2A - must never appear
        # in export output and must never be read as semantic input for GS
        # surface dispatch (task item 9). Its own mesh content is
        # irrelevant to this check; only the DISPLAY_ONLY_KEY marker and
        # scene-collection placement matter.
        display_obj = _mesh_object(
            "Wall2A_display", WORLD_EXT, parent=None,
        )
        display_obj[display_regen.DISPLAY_ONLY_KEY] = True
        try:
            bpy.context.scene.collection.objects.unlink(display_obj)
        except Exception:
            pass
        coll = bpy.data.collections.new("CAC GS Display Test")
        bpy.context.scene.collection.children.link(coll)
        coll.objects.link(display_obj)

    return building_obj, wall_a, win_a, wall_b, door_obj, display_obj


def build_degenerate_building():
    """Building3/Wall3: record-backed, structurally GS-eligible (a normal,
    non-degenerate rectangular wall, no hosted Window) - the "degenerate
    geometry" this scenario proves the safety net against is injected at
    test-run time (see ``_run_matrix``'s use of ``_force_display_build_
    error``), not baked into the fixture: a wall whose exterior ring is
    genuinely zero-area fails identically in the legacy writer's own
    ``_raise_if_degenerate`` guard (both check the same "zero area"
    condition), so it cannot demonstrate GS.3.2's *own* fallback path - a
    wall degenerate enough for ``build_surface_display`` to reject is, by
    construction here, geometry the legacy writer accepts fine, so the
    only way to reach "GS abandons the override, legacy takes over
    cleanly" is to make ``build_surface_display`` itself the thing that
    raises, independent of the wall's actual (valid) coordinates."""
    building_obj = _building_object("Building3", gml_id="BLD3", cgml3_feature="Building")
    wall_ext = ((40.0, 0.0, 0.0), (44.0, 0.0, 0.0), (44.0, 3.0, 0.0), (40.0, 3.0, 0.0))
    wall_obj = _mesh_object(
        "Wall3", wall_ext, parent=building_obj, polygon_id="P_WALL3",
        gml_id="WALL3", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    wall_record = FeatureRecord(
        gml_id="WALL3", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD3", owner_id="BLD3", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL3",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL3", feature_id="WALL3", geometry_state="committed",
        exterior=RingRecord("P_WALL3_0_", "exterior", wall_ext),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))
    # A second, always-legacy wall keeps Building3 itself IR-ineligible so
    # this test exercises the GS surface path being attempted and rejected,
    # not Building-level IR dispatch succeeding around it.
    _mesh_object(
        "Wall3B", LEGACY_WORLD_EXT, parent=building_obj, polygon_id="P_WALL3B",
        gml_id="WALL3B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_obj


def build_depth_window_building():
    """Building4/Wall4/Win4: Window has two committed polygons (depth-
    authored) - GS-ineligible by construction (WINDOW_POLYGON_COUNT)."""
    building_obj = _building_object("Building4", gml_id="BLD4", cgml3_feature="Building")
    wall_obj = _mesh_object(
        "Wall4", (
            (60.0, 0.0, 0.0), (64.0, 0.0, 0.0), (64.0, 3.0, 0.0), (60.0, 3.0, 0.0),
        ), parent=building_obj, polygon_id="P_WALL4",
        gml_id="WALL4", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    window_obj = _multi_face_mesh_object(
        "Window4", DEPTH_WIN_FACES, wall_obj, ("P_WIN4", "P_WIN4_B"),
        gml_id="WIN4", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL4", cgml_opening_link="LINK4",
    )
    wall_ring = ((60.5, 0.5, 0.5), (61.5, 0.5, 0.5), (61.5, 0.5, 1.5), (60.5, 0.5, 1.5))
    wall_record = FeatureRecord(
        gml_id="WALL4", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD4", owner_id="BLD4", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL4",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL4", feature_id="WALL4", geometry_state="committed",
        exterior=RingRecord("P_WALL4_0_", "exterior", (
            (60.0, 0.0, 0.0), (64.0, 0.0, 0.0), (64.0, 3.0, 0.0), (60.0, 3.0, 0.0),
        )),
        interiors=(RingRecord("P_WALL4_1_", "interior", wall_ring, opening_link="LINK4"),),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))
    window_record = FeatureRecord(
        gml_id="WIN4", kind="Window", semantic_type="Window",
        parent_id="WALL4", host_id="WALL4", owner_id="BLD4",
        opening_link="LINK4", lod=3, lod_origin="authored",
        provenance="authored", polygon_ids=("P_WIN4", "P_WIN4_B"), generator="facade.window.rect",
    )
    window_polygon_front = PolygonRecord(
        polygon_id="P_WIN4", feature_id="WIN4", geometry_state="committed",
        exterior=RingRecord("P_WIN4_0_", "exterior", DEPTH_WIN_FACES[0]),
    )
    window_polygon_back = PolygonRecord(
        polygon_id="P_WIN4_B", feature_id="WIN4", geometry_state="committed",
        exterior=RingRecord("P_WIN4_B_0_", "exterior", DEPTH_WIN_FACES[1]),
    )
    storage.write_record(
        window_obj,
        RecordSet(features=(window_record,), polygons=(window_polygon_front, window_polygon_back)),
    )
    # A sibling plain legacy wall (no record) keeps Building4 itself out of
    # whole-Building Phase-5 IR eligibility too - the general P5.2 IR
    # builder has no "one polygon per opening" restriction (that is a
    # GS.3.1 prototype-only scope limit), so without this sibling Building4
    # would be fully IR-eligible at the BUILDING level and this scenario
    # would prove nothing about GS.3.2's own, narrower rejection.
    _mesh_object(
        "Wall4B", (
            (100.0, 0.0, 0.0), (104.0, 0.0, 0.0), (104.0, 3.0, 0.0), (100.0, 3.0, 0.0),
        ), parent=building_obj, polygon_id="P_WALL4B",
        gml_id="WALL4B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_obj, window_obj


def build_two_window_building():
    """Building5/Wall5 with two hosted Windows, non-overlapping and each
    correctly ring-paired - GS-eligible under the N-Window generalization
    (CAC-P3b.2)."""
    building_obj = _building_object("Building5", gml_id="BLD5", cgml3_feature="Building")
    wall_ext = ((80.0, 0.0, 0.0), (86.0, 0.0, 0.0), (86.0, 3.0, 0.0), (80.0, 3.0, 0.0))
    wall_obj = _mesh_object(
        "Wall5", wall_ext, parent=building_obj, polygon_id="P_WALL5",
        gml_id="WALL5", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    ring1 = ((80.5, 0.5, 0.0), (81.5, 0.5, 0.0), (81.5, 1.5, 0.0), (80.5, 1.5, 0.0))
    ring2 = ((83.5, 0.5, 0.0), (84.5, 0.5, 0.0), (84.5, 1.5, 0.0), (83.5, 1.5, 0.0))
    win1_obj = _mesh_object(
        "Window5A", ring1, parent=wall_obj, polygon_id="P_WIN5A",
        gml_id="WIN5A", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL5", cgml_opening_link="LINK5A",
    )
    win2_obj = _mesh_object(
        "Window5B", ring2, parent=wall_obj, polygon_id="P_WIN5B",
        gml_id="WIN5B", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL5", cgml_opening_link="LINK5B",
    )
    wall_record = FeatureRecord(
        gml_id="WALL5", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD5", owner_id="BLD5", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL5",),
    )
    wall_polygon = PolygonRecord(
        polygon_id="P_WALL5", feature_id="WALL5", geometry_state="committed",
        exterior=RingRecord("P_WALL5_0_", "exterior", wall_ext),
        interiors=(
            RingRecord("P_WALL5_1_", "interior", ring1, opening_link="LINK5A"),
            RingRecord("P_WALL5_2_", "interior", ring2, opening_link="LINK5B"),
        ),
    )
    storage.write_record(wall_obj, RecordSet(features=(wall_record,), polygons=(wall_polygon,)))
    for win_obj, gml_id, poly_id, ring, link in (
        (win1_obj, "WIN5A", "P_WIN5A", ring1, "LINK5A"),
        (win2_obj, "WIN5B", "P_WIN5B", ring2, "LINK5B"),
    ):
        win_record = FeatureRecord(
            gml_id=gml_id, kind="Window", semantic_type="Window",
            parent_id="WALL5", host_id="WALL5", owner_id="BLD5",
            opening_link=link, lod=3, lod_origin="authored",
            provenance="authored", polygon_ids=(poly_id,), generator="facade.window.rect",
        )
        win_polygon = PolygonRecord(
            polygon_id=poly_id, feature_id=gml_id, geometry_state="committed",
            exterior=RingRecord(f"{poly_id}_0_", "exterior", ring),
        )
        storage.write_record(win_obj, RecordSet(features=(win_record,), polygons=(win_polygon,)))
    # A sibling plain legacy wall (no record) keeps Building5 itself out of
    # whole-Building Phase-5 IR eligibility too - see build_depth_window_
    # building's comment for why this is needed.
    _mesh_object(
        "Wall5B", (
            (120.0, 0.0, 0.0), (124.0, 0.0, 0.0), (124.0, 3.0, 0.0), (120.0, 3.0, 0.0),
        ), parent=building_obj, polygon_id="P_WALL5B",
        gml_id="WALL5B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_obj


def build_scene(scenario="mixed", with_display_only=False):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)

    result = {}
    if scenario in ("mixed", "all"):
        result["ir"] = build_ir_building()
        result["mixed"] = build_mixed_building(with_display_only=with_display_only)
    if scenario in ("degenerate", "all"):
        result["degenerate"] = build_degenerate_building()
    if scenario in ("depth", "all"):
        result["depth"] = build_depth_window_building()
    if scenario in ("two_window", "all"):
        result["two_window"] = build_two_window_building()
    bpy.context.view_layer.update()
    return result


def _export(version, tag):
    out = h.out_dir() / f"gs32_{tag}_{version.replace('.', '')}_{os.getpid()}.gml"
    text = h.export_scene(str(out), version)
    return out, text


def _run_matrix(r, version):
    tag = f"v{version.split('.')[0]}"
    window_tag = "WindowSurface" if version == "3.0" else "Window"
    door_tag = "DoorSurface" if version == "3.0" else "Door"

    # 1. GS flag OFF: Building2 (mixed) exports entirely legacy - no WALL2A
    #    or WIN2A record id anywhere (task item 1).
    _flag(False)
    _gs_flag(False)
    build_scene("mixed")
    obj_count_before = len(bpy.data.objects)
    out_off, text_off = _export(version, "flagoff")
    obj_count_after = len(bpy.data.objects)
    r.check(f"{tag} GS flag off: no scene-object mutation", obj_count_before == obj_count_after)
    r.check(
        f"{tag} GS flag off: WALL2A never uses the record id (all legacy)",
        'gml:id="WALL2A"' not in text_off, text_off[:300],
    )
    r.check(
        f"{tag} GS flag off: WIN2A never uses the record id (all legacy)",
        'gml:id="WIN2A"' not in text_off,
    )
    lint_off = h.xmllint(out_off, version)
    r.check(f"{tag} GS flag off: export is xmllint-clean", lint_off["exit"] == 0, str(lint_off))

    # 2. GS flag ON, Building1 fully IR-eligible: Building-level IR dispatch
    #    still wins (never routes through the GS surface path too - task
    #    item 2, "never both for the same surface").
    _flag(True)
    _gs_flag(True)
    build_scene("mixed")
    out_on, text_on = _export(version, "buildingwins")
    r.check(f"{tag} Building1 IR path: WALL1 uses the record id", 'gml:id="WALL1"' in text_on, text_on[:300])
    r.check(f"{tag} Building1 IR path: WIN1 uses the record id", 'gml:id="WIN1"' in text_on)
    ids_on = h.collect_ids(text_on)
    r.check(
        f"{tag} Building1 IR path: exactly one WALL1, no duplication",
        list(ids_on.get("WallSurface", ())).count("WALL1") == 1,
        str(ids_on.get("WallSurface")),
    )

    # 3-9. Core GS.3.2 proof: Building2 mixed scene, GS flag on.
    r.check(
        f"{tag} core proof: Building2's GS-eligible WALL2A uses the record id",
        'gml:id="WALL2A"' in text_on, text_on[:600],
    )
    r.check(f"{tag} core proof: WIN2A uses the record id", 'gml:id="WIN2A"' in text_on)
    wall_ids = ids_on.get("WallSurface", ())
    r.check(
        f"{tag} core proof: WALL2A appears exactly once (no duplicate)",
        list(wall_ids).count("WALL2A") == 1, str(wall_ids),
    )
    r.check(
        f"{tag} core proof: WALL1 (Building1 IR), WALL2A (GS surface IR) and one "
        "legacy-minted WallSurface (Wall2B) - three total, no duplication",
        len(wall_ids) == 3 and "WALL2B" not in wall_ids,
        str(wall_ids),
    )
    window_ids = ids_on.get(window_tag, ())
    r.check(
        f"{tag} core proof: exactly one {window_tag} for WIN2A (no duplicate)",
        list(window_ids).count("WIN2A") == 1, str(window_ids),
    )
    door_ids = ids_on.get(door_tag, ())
    r.check(
        f"{tag} core proof: legacy Door2 still exported exactly once",
        len(door_ids) == 1, str(door_ids),
    )
    lint_on = h.xmllint(out_on, version)
    r.check(f"{tag} core proof: export is xmllint-clean", lint_on["exit"] == 0, str(lint_on))

    # 10. Ring/opening nesting + committed coordinates: WIN2A's own exported
    #     posList carries the exact committed WIN_EXT coordinates (not the
    #     wall's, not some other surface's - the opening's own filling
    #     geometry, GS.3.2 task item 7 "no geometry matching").
    def _fmt_num(n):
        return str(int(n)) if float(n).is_integer() else str(n)

    win2a_coord_strs = [f"{_fmt_num(x)} {_fmt_num(y)} {_fmt_num(z)}" for (x, y, z) in WIN_EXT]
    r.check(
        f"{tag} committed coordinates: WIN2A's committed corner coordinates appear in the export",
        all(c in text_on for c in win2a_coord_strs),
        win2a_coord_strs,
    )

    # 11. Scene mutation check for the GS-on export too.
    obj_count_before2 = len(bpy.data.objects)
    _export(version, "mutationcheck")
    obj_count_after2 = len(bpy.data.objects)
    r.check(f"{tag} GS flag on: no scene-object mutation", obj_count_before2 == obj_count_after2)

    # 12. Display-only object stays out of export and out of GS dispatch input.
    build_scene("mixed", with_display_only=True)
    _, text_display = _export(version, "displayonly")
    r.check(
        f"{tag} display-only object absent from export, WALL2A/WIN2A still present",
        "Wall2A_display" not in text_display
        and 'gml:id="WALL2A"' in text_display
        and 'gml:id="WIN2A"' in text_display,
        text_display[:200],
    )

    # 13. GS.3.2's degenerate-geometry safety net (task item 5): a wall that
    #     is structurally GS-eligible but whose display-build fails (forced
    #     here via monkeypatch - see ``build_degenerate_building``'s
    #     docstring for why real zero-area coordinates cannot demonstrate
    #     this) falls back to the legacy surface path without crashing the
    #     whole Building export.
    build_scene("degenerate")
    _original_build = _force_display_build_error_for("WALL3")
    try:
        out_deg, text_deg = _export(version, "degenerate")
    finally:
        _restore_display_build(_original_build)
    r.check(
        f"{tag} degenerate wall: export completes without crashing",
        bool(text_deg),
    )
    r.check(
        f"{tag} degenerate wall: WALL3 never uses the record id (fell back to legacy)",
        'gml:id="WALL3"' not in text_deg, text_deg[:300],
    )
    lint_deg = h.xmllint(out_deg, version)
    r.check(f"{tag} degenerate wall: export is xmllint-clean", lint_deg["exit"] == 0, str(lint_deg))

    # 14. Depth-authored Window keeps its Building legacy.
    build_scene("depth")
    out_depth, text_depth = _export(version, "depthwindow")
    r.check(
        f"{tag} depth-authored Window: WALL4 never uses the record id",
        'gml:id="WALL4"' not in text_depth, text_depth[:300],
    )
    lint_depth = h.xmllint(out_depth, version)
    r.check(f"{tag} depth-authored Window: export is xmllint-clean", lint_depth["exit"] == 0, str(lint_depth))

    # 15. Two-Window wall (CAC-P3b.2 N-Window generalization): both hosted
    #     Windows export through the record/IR surface path, no duplication.
    build_scene("two_window")
    out_two, text_two = _export(version, "twowindow")
    r.check(
        f"{tag} two-Window wall: WALL5 uses the record id",
        'gml:id="WALL5"' in text_two, text_two[:300],
    )
    r.check(f"{tag} two-Window wall: WIN5A uses the record id", 'gml:id="WIN5A"' in text_two)
    r.check(f"{tag} two-Window wall: WIN5B uses the record id", 'gml:id="WIN5B"' in text_two)
    ids_two = h.collect_ids(text_two)
    r.check(
        f"{tag} two-Window wall: exactly one WALL5, no duplication",
        list(ids_two.get("WallSurface", ())).count("WALL5") == 1,
        str(ids_two.get("WallSurface")),
    )
    window_ids_two = ids_two.get(window_tag, ())
    r.check(
        f"{tag} two-Window wall: exactly one {window_tag} each for WIN5A/WIN5B (no duplicate)",
        list(window_ids_two).count("WIN5A") == 1 and list(window_ids_two).count("WIN5B") == 1,
        str(window_ids_two),
    )
    lint_two = h.xmllint(out_two, version)
    r.check(f"{tag} two-Window wall: export is xmllint-clean", lint_two["exit"] == 0, str(lint_two))

    # 16. CAC-P3b.6 mixed-scene proof (brief S14): every scenario Building
    #     in one scene together - Building1 (whole-Building IR), Building2
    #     (GS surface IR, one Window, legacy sibling wall + Door), Building3
    #     (GS-structurally-eligible wall whose display build is forced to
    #     fail - legacy fallback), Building4 (depth-authored Window - legacy
    #     fallback), Building5 (GS surface IR, two Windows, legacy sibling
    #     wall). Every feature must appear exactly once, with no cross-
    #     Building leakage and no duplicate surface/opening, and the whole
    #     export must stay xmllint-clean.
    build_scene("all")
    out_all, text_all = _export(version, "mixedscene")
    ids_all = h.collect_ids(text_all)
    wall_ids_all = ids_all.get("WallSurface", ())
    window_ids_all = ids_all.get(window_tag, ())
    door_ids_all = ids_all.get(door_tag, ())
    r.check(
        f"{tag} mixed-scene: Building1 IR wall+window present exactly once",
        list(wall_ids_all).count("WALL1") == 1 and list(window_ids_all).count("WIN1") == 1,
        f"wall={wall_ids_all} window={window_ids_all}",
    )
    r.check(
        f"{tag} mixed-scene: Building2 GS surface wall+window present exactly once, "
        "sibling legacy wall never uses the record id, Door2 exactly once",
        list(wall_ids_all).count("WALL2A") == 1
        and list(window_ids_all).count("WIN2A") == 1
        and "WALL2B" not in wall_ids_all
        and len(door_ids_all) == 1,
        f"wall={wall_ids_all} window={window_ids_all} door={door_ids_all}",
    )
    r.check(
        f"{tag} mixed-scene: Building3's structurally-GS-eligible windowless wall "
        "exports exactly once via the GS surface path (no forced display-build "
        "failure active in this composite scene, unlike check 13's own case)",
        list(wall_ids_all).count("WALL3") == 1, str(wall_ids_all),
    )
    r.check(
        f"{tag} mixed-scene: Building4 depth-authored-Window wall never uses the record id",
        "WALL4" not in wall_ids_all, str(wall_ids_all),
    )
    r.check(
        f"{tag} mixed-scene: Building5 GS surface wall present exactly once with both "
        "hosted Windows exactly once each, sibling legacy wall never uses the record id",
        list(wall_ids_all).count("WALL5") == 1
        and list(window_ids_all).count("WIN5A") == 1
        and list(window_ids_all).count("WIN5B") == 1
        and "WALL5B" not in wall_ids_all,
        f"wall={wall_ids_all} window={window_ids_all}",
    )
    r.check(
        f"{tag} mixed-scene: no cross-Building duplication - every record-backed "
        "gml:id in this scene appears at most once across walls+windows, and no "
        "always-legacy sibling wall ever uses the record id it was never given",
        all(list(wall_ids_all).count(i) <= 1 for i in ("WALL1", "WALL2A", "WALL3", "WALL5"))
        and all(list(window_ids_all).count(i) <= 1 for i in ("WIN1", "WIN2A", "WIN5A", "WIN5B"))
        and "WALL3B" not in wall_ids_all and "WALL4B" not in wall_ids_all,
        f"wall={wall_ids_all} window={window_ids_all}",
    )
    lint_all = h.xmllint(out_all, version)
    r.check(f"{tag} mixed-scene: export is xmllint-clean", lint_all["exit"] == 0, str(lint_all))


def main():
    r = h.Results(__file__)
    h.load_addon()

    _run_matrix(r, "3.0")
    _run_matrix(r, "2.0")

    r.finish()


if __name__ == "__main__":
    main()
