# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""CAC-P3b.5: record-first semantic reads for the supported record-backed
WallSurface/Window slice, real Blender.

The P3b.5 audit (docs/agent/authoring-core/phase3b-design-note.md S13,
P3b.5 row; audit table in this same note) found NO legacy semantic read
reachable from the supported production path that needs to move to
records - every direct mesh/material read is either the create-time
mechanism that *produces* a record from fresh geometry, a documented
drift-detection compatibility read (Class B), or already record-first
(``read_feature_view``, in place since P1.6). This file is the fault-
injection regression suite the audit called for: it corrupts legacy
mirrors (face-material ``gml_polygon_id``, Blender parenting) on objects
whose stored records stay valid and correct. Host/opening relation
resolution is confirmed record-first (reparenting a Window away from its
host wall does not change export nesting). For the three cases that
corrupt a legacy identity mirror while records stay valid or a required
record relation is deliberately blanked, the discovered (and correct)
behaviour is not "export silently uses the record and ignores the
corrupted mirror" - it is the P3.3 record/legacy consistency gate
(``consistency_view.py`` / ``domain/consistency.py``, already wired into
both writers' export pre-flight) hard-refusing the export with a named
mismatch finding. That *is* the S12 failure policy in action: a
disagreeing legacy mirror, or a missing required relation, is surfaced as
an explicit, named error - never silently reconciled either way.

Test 3 (``p3b5_gs_surface_corrupted_material_mirror_refuses_before_dispatch``)
targets the one real coupling the audit flagged as worth watching (S10/
finding H): GS.3.2's per-surface writer seam still matches an eligible
surface's geom_buf entries by the legacy material ``gml_polygon_id``
mirror before folding them into the IR-written result. It confirms the
P3.3 consistency gate runs and refuses *before* that dispatch decision is
ever reached, closing the flagged risk for this class of corruption.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3b5_record_first_semantics.py -- <out_dir>
"""
import os
import sys
import traceback
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))
from shared.authoring_core.blender import storage  # noqa: E402
from shared.authoring_core.domain.records import (  # noqa: E402
    FeatureRecord, PolygonRecord, RecordSet, RingRecord,
)

WORLD_EXT = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0))
RING_A = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
WIN_EXT = ((0.5, 0.5, 0.0), (1.5, 0.5, 0.0), (1.5, 1.5, 0.0), (0.5, 1.5, 0.0))
LEGACY_WORLD_EXT = ((20.0, 0.0, 0.0), (24.0, 0.0, 0.0), (24.0, 3.0, 0.0), (20.0, 3.0, 0.0))


# --------------------------------------------------------------------------
# helpers (same conventions as test_cac_gs3_2_writer_dispatch.py /
# test_cac_p0_known_bugs.py - each headless test file is self-contained)
# --------------------------------------------------------------------------

def element_by_id(xml_text, gml_id):
    root = ET.fromstring(xml_text.encode("utf-8"))
    for elem in root.iter():
        if any(elem.get(a) == gml_id for a in h.GML_IDS):
            return elem
    return None


def rings_and_openings(xml_text, surface_id):
    """(interior ring count, nested opening count) under one exported surface."""
    elem = element_by_id(xml_text, surface_id)
    if elem is None:
        return None, None
    names = [h.local_name(e.tag) for e in elem.iter()]
    return names.count("interior"), sum(names.count(o) for o in h.OPENINGS)


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


def _corrupt_polygon_id_materials(*objs):
    """Overwrite every material ``gml_polygon_id`` on the given objects with
    an unrelated, wrong value - the record-backed path must not notice."""
    for obj in objs:
        for mat in obj.data.materials:
            if mat is not None and "gml_polygon_id" in mat:
                mat["gml_polygon_id"] = "CORRUPTED_" + mat["gml_polygon_id"]


def build_ir_building():
    """Building1/Wall1/Win1 - fully IR-eligible (same shape P5.6/GS.3.2
    already use). Wall has one hosted Window, one interior ring."""
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


def build_mixed_gs_building():
    """Building2: Wall2A (record-backed, GS.3.2-eligible) + Win2A, Wall2B
    (plain legacy, no record - keeps Building2 as a whole NOT IR-eligible,
    so the writer must additionally compute the GS per-surface groups for
    Wall2A). Same shape as test_cac_gs3_2_writer_dispatch.py's Building2."""
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

    # Plain legacy WallSurface, deliberately no stored record - keeps
    # Building2 out of whole-Building Phase-5 IR eligibility so the writer
    # falls through to computing the GS per-surface groups for Wall2A.
    _mesh_object(
        "Wall2B", LEGACY_WORLD_EXT, parent=building_obj, polygon_id="P_WALL2B",
        gml_id="WALL2B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_a, win_a


def build_mixed_gs_building_two_windows():
    """CAC-P3b.6: same shape as ``build_mixed_gs_building`` but Wall2A
    hosts TWO record-backed Windows (WIN2A, WIN2C), each with its own
    interior ring/opening_link - the N-Window case P3b.6's consistency-
    gate contract (P3b.6 brief S6) must hold for, not just the single-
    Window case the original P3b.5 audit exercised."""
    building_obj = _building_object("Building2", gml_id="BLD2", cgml3_feature="Building")
    wall_a = _mesh_object(
        "Wall2A", WORLD_EXT, parent=building_obj, polygon_id="P_WALL2A",
        gml_id="WALL2A", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    ring_c = ((2.5, 0.5, 0.0), (3.5, 0.5, 0.0), (3.5, 1.5, 0.0), (2.5, 1.5, 0.0))
    win_a = _mesh_object(
        "Window2A", WIN_EXT, parent=wall_a, polygon_id="P_WIN2A",
        gml_id="WIN2A", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL2A", cgml_opening_link="LINK2A",
    )
    win_c = _mesh_object(
        "Window2C", ring_c, parent=wall_a, polygon_id="P_WIN2C",
        gml_id="WIN2C", cgml3_feature="Window", cgml3_lod=3,
        filling_parent_surface_id="WALL2A", cgml_opening_link="LINK2C",
    )
    wall_a_record = FeatureRecord(
        gml_id="WALL2A", kind="WallSurface", semantic_type="WallSurface",
        parent_id="BLD2", owner_id="BLD2", lod=3, lod_origin="authored",
        provenance="imported", polygon_ids=("P_WALL2A",),
    )
    wall_a_polygon = PolygonRecord(
        polygon_id="P_WALL2A", feature_id="WALL2A", geometry_state="committed",
        exterior=RingRecord("P_WALL2A_0_", "exterior", WORLD_EXT),
        interiors=(
            RingRecord("P_WALL2A_1_", "interior", RING_A, opening_link="LINK2A"),
            RingRecord("P_WALL2A_2_", "interior", ring_c, opening_link="LINK2C"),
        ),
    )
    storage.write_record(wall_a, RecordSet(features=(wall_a_record,), polygons=(wall_a_polygon,)))
    for win_obj, gml_id, poly_id, ring, link in (
        (win_a, "WIN2A", "P_WIN2A", WIN_EXT, "LINK2A"),
        (win_c, "WIN2C", "P_WIN2C", ring_c, "LINK2C"),
    ):
        win_record = FeatureRecord(
            gml_id=gml_id, kind="Window", semantic_type="Window",
            parent_id="WALL2A", host_id="WALL2A", owner_id="BLD2",
            opening_link=link, lod=3, lod_origin="authored",
            provenance="authored", polygon_ids=(poly_id,), generator="facade.window.rect",
        )
        win_polygon = PolygonRecord(
            polygon_id=poly_id, feature_id=gml_id, geometry_state="committed",
            exterior=RingRecord(f"{poly_id}_0_", "exterior", ring),
        )
        storage.write_record(win_obj, RecordSet(features=(win_record,), polygons=(win_polygon,)))

    # Plain legacy WallSurface, deliberately no stored record - keeps
    # Building2 out of whole-Building Phase-5 IR eligibility so the writer
    # falls through to computing the GS per-surface groups for Wall2A.
    _mesh_object(
        "Wall2B", LEGACY_WORLD_EXT, parent=building_obj, polygon_id="P_WALL2B",
        gml_id="WALL2B", cgml3_feature="WallSurface", cgml3_lod=3,
    )
    return building_obj, wall_a, win_a, win_c


def _reset_scene():
    h.load_addon()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)


def _export(version, tag):
    out = h.out_dir() / f"p3b5_{tag}_{version.replace('.', '')}_{os.getpid()}.gml"
    text = h.export_scene(str(out), version)
    return out, text


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def p3b5_corrupted_material_polygon_id_refuses_explicitly(R):
    """Whole-Building D-6 IR export (P5.6/P5.7): corrupting the legacy
    material ``gml_polygon_id`` mirror on the wall's and window's meshes,
    with records left untouched, must never be silently tolerated (export
    quietly using the corrupted mirror, or quietly using the record while
    hiding the disagreement) - discovered behaviour: the P3.3 record/
    legacy consistency gate (``consistency_view.py``/``domain/
    consistency.py``, already wired into both writers' export pre-flight)
    hard-refuses the export with a named ``polygon_ids_mismatch`` finding
    for both surfaces. This *is* the record-first failure policy (S12):
    a disagreeing legacy mirror is surfaced as an explicit, named error,
    never silently reconciled either way."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(False)
        _reset_scene()
        _building_obj, wall_obj, window_obj = build_ir_building()
        _corrupt_polygon_id_materials(wall_obj, window_obj)
        bpy.context.view_layer.update()

        raised, message = False, ""
        try:
            _export(version, "corrupt_poly_id")
        except RuntimeError as exc:
            raised, message = True, str(exc)

        ok = (
            raised
            and "polygon_ids_mismatch" in message
            and "WALL1" in message and "WIN1" in message
        )
        R.check(
            f"v{version[0]} p3b5_corrupted_material_polygon_id_refuses_explicitly", ok,
            f"raised={raised} message={message[:300]}",
        )


def p3b5_reparented_window_host_resolution_uses_record_not_parenting(R):
    """Re-parent the record-backed Window directly under the Building
    object (still inside the Building's descendant scope, so it is still
    discovered - PLAN.md's scene-containment use of parenting is
    unaffected/legitimate) instead of under its host wall. The stored
    ``host_id``/``parent_id`` still say ``WALL1``. The IR builder must
    still nest the Window under Wall1 by ``host_id`` alone; Blender
    parenting must play no role in host/opening relation resolution."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(False)
        _reset_scene()
        building_obj, _wall_obj, window_obj = build_ir_building()
        window_obj.parent = building_obj  # no longer parented under Wall1
        bpy.context.view_layer.update()

        out, text = _export(version, "reparented_window")
        interiors, openings = rings_and_openings(text, "WALL1")
        lint = h.xmllint(out, version)
        ok = (
            'gml:id="WIN1"' in text
            and interiors == 1 and openings == 1
            and lint["exit"] == 0
        )
        R.check(
            f"v{version[0]} p3b5_reparented_window_host_resolution_uses_record_not_parenting", ok,
            f"win_present={'gml:id=\"WIN1\"' in text} interiors={interiors} openings={openings} lint={lint}",
        )


def p3b5_gs_surface_corrupted_material_mirror_refuses_before_dispatch(R):
    """GS.3.2 per-surface path (flag ON, Building2 not whole-Building
    IR-eligible because of legacy Wall2B): corrupt Wall2A's and Win2A's
    material ``gml_polygon_id`` mirrors AFTER their records are committed.
    The audit (phase3b-design-note.md S3/S10) flagged this mirror as the
    one thing GS.3.2's writer seam still matches on to filter a GS-
    eligible surface's geom_buf entries out of the legacy write loop - a
    real risk that a corrupted mirror could cause the surface to be
    duplicated or dropped. Discovered behaviour: the P3.3 record/legacy
    consistency gate runs before that dispatch decision is ever reached,
    so the corruption is caught and the whole export explicitly refused
    (named ``polygon_ids_mismatch``) before the GS surface-dispatch/geom_
    buf-filter seam runs at all - closing the flagged risk for this class
    of corruption without any writer-dispatch change."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(True)
        _reset_scene()
        _building_obj, wall_a, win_a = build_mixed_gs_building()
        _corrupt_polygon_id_materials(wall_a, win_a)
        bpy.context.view_layer.update()

        raised, message = False, ""
        try:
            _export(version, "gs_corrupt_poly_id")
        except RuntimeError as exc:
            raised, message = True, str(exc)

        ok = (
            raised
            and "polygon_ids_mismatch" in message
            and "WALL2A" in message and "WIN2A" in message
        )
        R.check(
            f"v{version[0]} p3b5_gs_surface_corrupted_material_mirror_refuses_before_dispatch", ok,
            f"raised={raised} message={message[:300]}",
        )


def p3b5_missing_required_host_relation_refuses_explicitly(R):
    """A Window record with its ``host_id``/``parent_id`` blanked out
    (deliberately corrupted after an otherwise normal placement, while the
    live legacy mirror - Blender parenting, ``filling_parent_surface_id``
    - still correctly says ``WALL1``) must never be silently patched back
    together from that legacy mirror. Discovered behaviour: the P3.3
    consistency gate names the exact mismatch (``host_id_mismatch``,
    ``parent_id_mismatch``, ``orphan_opening``) and refuses the export -
    exactly the S12 policy ("explicit failure ... not silent semantic
    reconstruction from mesh/material state")."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(False)
        _reset_scene()
        _building_obj, _wall_obj, window_obj = build_ir_building()
        record_set = storage.read_record(window_obj)
        broken_feature = FeatureRecord(
            gml_id="WIN1", kind="Window", semantic_type="Window",
            parent_id="", host_id="", owner_id="BLD1",
            opening_link="LINK1", lod=3, lod_origin="authored",
            provenance="authored", polygon_ids=("P_WIN1",), generator="facade.window.rect",
        )
        storage.write_record(window_obj, RecordSet(features=(broken_feature,), polygons=record_set.polygons))
        bpy.context.view_layer.update()

        raised, message = False, ""
        try:
            _export(version, "missing_host_relation")
        except RuntimeError as exc:
            raised, message = True, str(exc)

        ok = (
            raised
            and "host_id_mismatch" in message
            and "WIN1" in message
        )
        R.check(
            f"v{version[0]} p3b5_missing_required_host_relation_refuses_explicitly", ok,
            f"raised={raised} message={message[:300]}",
        )


def p3b6_gs_surface_n_window_corrupted_sibling_mirror_refuses_before_dispatch(R):
    """CAC-P3b.6 consistency-gate contract, N-Window case (P3b.6 brief S6):
    Wall2A hosts TWO record-backed Windows (WIN2A, WIN2C). Only WIN2C's
    material ``gml_polygon_id`` mirror is corrupted; WIN2A's stays correct.
    The P3.3 gate must still refuse the WHOLE export explicitly (named
    ``polygon_ids_mismatch`` for WIN2C) before the GS surface-dispatch/
    geom_buf-filter seam runs - never a partial export that writes WIN2A
    while silently dropping or misfiltering WIN2C, and never a silent
    fallback that reconstructs WIN2C's identity from geometry. This is the
    N-Window generalization of ``p3b5_gs_surface_corrupted_material_mirror_
    refuses_before_dispatch`` P3b.6's brief S6 asked for."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(True)
        _reset_scene()
        _building_obj, _wall_a, win_a, win_c = build_mixed_gs_building_two_windows()
        _corrupt_polygon_id_materials(win_c)  # WIN2A's mirror stays correct
        bpy.context.view_layer.update()

        raised, message = False, ""
        try:
            _export(version, "gs_n_window_corrupt_sibling")
        except RuntimeError as exc:
            raised, message = True, str(exc)

        ok = (
            raised
            and "polygon_ids_mismatch" in message
            and "WIN2C" in message
        )
        R.check(
            f"v{version[0]} p3b6_gs_surface_n_window_corrupted_sibling_mirror_refuses_before_dispatch", ok,
            f"raised={raised} message={message[:300]}",
        )


def p3b6_gs_surface_n_window_valid_exports_all_openings_once(R):
    """CAC-P3b.6 control case for the check above: with BOTH windows'
    records and mirrors valid, the GS per-surface path exports Wall2A once
    and each of its two hosted Windows exactly once - confirming the gate
    passing (not just refusing) still produces the correct N-Window
    result, and that the fault-injection check above is actually
    exercising the gate, not some unrelated failure."""
    for version in ("2.0", "3.0"):
        _flag(True)
        _gs_flag(True)
        _reset_scene()
        build_mixed_gs_building_two_windows()
        bpy.context.view_layer.update()

        out, text = _export(version, "gs_n_window_valid")
        ids = h.collect_ids(text)
        window_tag = "WindowSurface" if version == "3.0" else "Window"
        wall_ids = ids.get("WallSurface", ())
        window_ids = ids.get(window_tag, ())
        lint = h.xmllint(out, version)
        ok = (
            list(wall_ids).count("WALL2A") == 1
            and list(window_ids).count("WIN2A") == 1
            and list(window_ids).count("WIN2C") == 1
            and lint["exit"] == 0
        )
        R.check(
            f"v{version[0]} p3b6_gs_surface_n_window_valid_exports_all_openings_once", ok,
            f"wall_ids={wall_ids} window_ids={window_ids} lint={lint}",
        )


def main():
    R = h.Results(__file__)
    checks = [
        p3b5_corrupted_material_polygon_id_refuses_explicitly,
        p3b5_reparented_window_host_resolution_uses_record_not_parenting,
        p3b5_gs_surface_corrupted_material_mirror_refuses_before_dispatch,
        p3b5_missing_required_host_relation_refuses_explicitly,
        p3b6_gs_surface_n_window_corrupted_sibling_mirror_refuses_before_dispatch,
        p3b6_gs_surface_n_window_valid_exports_all_openings_once,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
