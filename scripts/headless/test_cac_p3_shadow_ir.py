"""Shadow IR check: record-derived IR vs. the legacy exporter (CAC-P3.5, PLAN.md P3.5).

``domain/ir_adapter.py::opening_node_from_records`` builds an ``OpeningNode``
+ the host WallSurface's ``RingNode`` from the authoritative record (P3.2).
This compares that shadow IR against the *actual* exported CityGML 2 and 3
documents for the same authored Window - gml:id, host surface nesting,
interior ring presence, and polygon ids (PLAN.md P3.5's exact scope; no
geometry comparison is required by the card - P3.3 already covers
coordinate drift). Read-only: no XML writer change. A mismatch here is
reported as a finding, not silently patched.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p3_shadow_ir.py -- <out_dir>
"""
import os
import sys
import traceback
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

RAILWAY_V3 = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"
SIZE = 0.3


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _prepare():
    h.import_fixture(RAILWAY_V3, "3.0", use_semantic_objects=True)
    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    return wall, face_index, ctx


def _place_window(wall, face_index, ctx, generator=None):
    from city_modeler.shared.authoring_core.blender.usecases import create_feature
    from city_modeler.shared.facade_placement import FacadeElementDefinition, PlacementRequest

    uv = h.solid_area_position_uv(wall, face_index, ctx, SIZE / 2, SIZE / 2)
    definition = FacadeElementDefinition(semantic_type="Window", width=SIZE, height=SIZE, depth=0.05)
    placement = PlacementRequest(position_uv=uv, source="manual_draw")
    kwargs = {"generator": generator} if generator else {}
    return create_feature("Window", ctx, definition, placement, **kwargs)


def _shadow_ir(wall, fill):
    from city_modeler.shared.authoring_core.blender.consistency_view import merged_record_set
    from city_modeler.shared.authoring_core.domain.ir_adapter import opening_node_from_records

    record_set = merged_record_set([wall, fill])
    window_gml_id = str(fill.get("gml_id"))
    opening, host_ring, host_gml_id = opening_node_from_records(record_set, window_gml_id)
    window_feature = record_set.feature(window_gml_id)
    return opening, host_ring, host_gml_id, window_feature


def _raw_ir(xml_text):
    """``{feature_ids_by_name, opening_nesting, polygon_ids, interior_rings}``
    read straight off the exported XML, with **no id normalisation** -
    unlike ``cac_harness.export_fingerprint`` (P0.4's structural
    fingerprint, which deliberately folds freshly-minted ids into
    ``*_FRESH<n>`` tokens for cross-run comparison). P3.5 needs the
    opposite: the *exact* record gml_id/polygon_id must appear verbatim in
    the export, so this reads the raw text directly."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    parent = {child: elem for elem in root.iter() for child in elem}

    def gid(elem):
        return next((elem.get(a) for a in h.GML_IDS if elem.get(a) is not None), None)

    feature_ids_by_name, polygon_ids, interior_rings, nesting = {}, set(), {}, []
    for elem in root.iter():
        name = h.local_name(elem.tag)
        ident = gid(elem)
        if name == "Polygon":
            if ident:
                polygon_ids.add(ident)
            n = sum(1 for child in elem if h.local_name(child.tag) == "interior")
            if n:
                interior_rings[ident] = n
        elif ident is not None and name not in h.NON_FEATURE_ELEMENTS:
            feature_ids_by_name.setdefault(name, set()).add(ident)
        if name in h.OPENINGS:
            up = parent.get(elem)
            while up is not None and h.local_name(up.tag) not in h.BOUNDARY_SURFACES:
                up = parent.get(up)
            nesting.append([gid(up) if up is not None else None, ident])
    return {
        "feature_ids_by_name": feature_ids_by_name,
        "opening_nesting": nesting,
        "polygon_ids": polygon_ids,
        "interior_rings": interior_rings,
    }


def _mismatches(opening, host_ring, host_gml_id, window_feature, fp):
    """Every way the shadow IR disagrees with the raw export (empty = match)."""
    problems = []

    opening_tag_ids = {gid for name, ids in fp["feature_ids_by_name"].items()
                        for gid in ids if name in ("Window", "WindowSurface")}
    if opening.gml_id not in opening_tag_ids:
        problems.append(f"gml:id {opening.gml_id!r} not found as an exported Window/WindowSurface element")

    if [host_gml_id, opening.gml_id] not in fp["opening_nesting"]:
        problems.append(
            f"host nesting [{host_gml_id!r}, {opening.gml_id!r}] not found in export "
            f"(host surface nesting mismatch); export has {fp['opening_nesting']!r}"
        )

    if host_ring is None:
        problems.append("record has no host RingRecord for this Window's opening_link")
    else:
        if host_ring.polygon_id not in fp["polygon_ids"]:
            problems.append(f"host ring's polygon_id {host_ring.polygon_id!r} not among exported polygon ids")
        elif fp["interior_rings"].get(host_ring.polygon_id, 0) < 1:
            problems.append(f"host polygon {host_ring.polygon_id!r} exports no gml:interior ring")

    for polygon_id in window_feature.polygon_ids:
        if polygon_id not in fp["polygon_ids"]:
            problems.append(f"Window polygon_id {polygon_id!r} not among exported polygon ids")

    return problems


def shadow_ir_matches_draw_window_export_v3(R):
    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    opening, host_ring, host_gml_id, window_feature = _shadow_ir(wall, fill)

    text = h.export_scene(h.out_dir() / "p3_shadow_ir_draw_v3.gml", "3.0")
    fp = _raw_ir(text)
    problems = _mismatches(opening, host_ring, host_gml_id, window_feature, fp)
    R.check("shadow_ir_matches_draw_window_export_v3", not problems,
            f"problems={problems} opening={opening} host_ring={host_ring} host_gml_id={host_gml_id!r}")


def shadow_ir_matches_draw_window_export_v2(R):
    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx)
    opening, host_ring, host_gml_id, window_feature = _shadow_ir(wall, fill)

    text = h.export_scene(h.out_dir() / "p3_shadow_ir_draw_v2.gml", "2.0")
    fp = _raw_ir(text)
    problems = _mismatches(opening, host_ring, host_gml_id, window_feature, fp)
    R.check("shadow_ir_matches_draw_window_export_v2", not problems,
            f"problems={problems} opening={opening} host_ring={host_ring} host_gml_id={host_gml_id!r}")


def shadow_ir_matches_stamp_window_export_v3(R):
    _flag(True)
    wall, face_index, ctx = _prepare()
    fill = _place_window(wall, face_index, ctx, generator="semantic_stamp")
    opening, host_ring, host_gml_id, window_feature = _shadow_ir(wall, fill)

    text = h.export_scene(h.out_dir() / "p3_shadow_ir_stamp_v3.gml", "3.0")
    fp = _raw_ir(text)
    problems = _mismatches(opening, host_ring, host_gml_id, window_feature, fp)
    R.check("shadow_ir_matches_stamp_window_export_v3", not problems,
            f"problems={problems} opening={opening} host_ring={host_ring} host_gml_id={host_gml_id!r}")


def main():
    R = h.Results(__file__)
    checks = [
        shadow_ir_matches_draw_window_export_v3,
        shadow_ir_matches_draw_window_export_v2,
        shadow_ir_matches_stamp_window_export_v3,
    ]
    for fn in checks:
        try:
            fn(R)
        except Exception:
            R.check(fn.__name__, False, "exception:\n" + "".join(traceback.format_exc().splitlines(True)[-15:]))
    R.finish()


main()
