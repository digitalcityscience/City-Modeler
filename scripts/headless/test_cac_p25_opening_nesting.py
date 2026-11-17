"""CAC-P2.5.2: imported openings keep their source host surface on export.

Desired behaviour (PLAN.md §5 Phase 2.5, opening-host-resolution.md §5):
every opening of the source file is exported with the same gml:id, nested
under the boundary surface that encloses it in the source. With the
``split_wall_roof_surfaces`` export option a wall is written as one surface
per polygon (``<wall id>_<polygon id>``); the opening must then be nested
under one of those entries.

Checks are grouped per scenario and opening type (Window / Door), so the
15 Railway Windows that are nested correctly today pass, while the Doors and
the AltOpening openings are registered under AB-12 (CityGML 3) and AB-13
(CityGML 2) in scripts/tests/known_arch_bugs.json.

Authored openings (Draw Window, Draw Door floating / ground notch, Stamp
Window) on Railway v3: the fill object's gml_id is the opening's only id,
so every opening id key on its materials must equal it (AB-09: the
Openings Cutter mints a second ``opening_gml_id``, Stamp inherits the
source's), and the export must nest that id under the host wall (AB-12 /
AB-13).

The split-wall scenario is the regression guard for e8bd419 (ring-matched
openings go to the split entry that owns their hole); its Window check
must pass before and after the P2.5 fixes. It runs for CityGML 3 only: the
CityGML 2 export of Railway writes no split entries with the option on, so a
v2 variant would repeat the plain check. ``..._split_entries_exercised``
fails if the v3 export stops splitting, so the guard cannot go weak.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p25_opening_nesting.py -- <out_dir>
"""
import os
import sys
import traceback
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

B = h.FIXTURES / "Building"
FIXTURES = {
    "railway_v2": (B / "Railway_Scene_LoD3_CityGML2_Building_only.gml", "2.0"),
    "railway_v3": (B / "Railway_Scene_LoD3_CityGML3_Building_only.gml", "3.0"),
    "altopening_v3": (B / "AltOpeningSpelling_LoD3_CityGML3.gml", "3.0"),
}
KIND = {"Window": "window", "WindowSurface": "window", "Door": "door", "DoorSurface": "door"}


def gml_id(elem):
    for key in h.GML_IDS:
        if elem.get(key):
            return elem.get(key)
    return ""


def opening_hosts(xml_text):
    """``{opening gml:id: (window|door, enclosing boundary surface gml:id or None)}``."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    parents = {child: parent for parent in root.iter() for child in parent}
    result = {}
    for elem in root.iter():
        name = h.local_name(elem.tag) if isinstance(elem.tag, str) else ""
        if name not in h.OPENINGS or not gml_id(elem):
            continue
        host, node = None, parents.get(elem)
        while node is not None:
            if h.local_name(node.tag) in h.BOUNDARY_SURFACES:
                host = gml_id(node) or None
                break
            node = parents.get(node)
        result[gml_id(elem)] = (KIND[name], host)
    return result


def nesting_problems(source, exported, kind, split):
    """Openings of ``kind`` not nested under their source host; ``[]`` when all are."""
    problems = []
    for oid, (k, host) in sorted(source.items()):
        if k != kind:
            continue
        if oid not in exported:
            problems.append(f"{oid}: missing")
            continue
        got = exported[oid][1]
        ok = got == host or (split and got is not None and host is not None and got.startswith(host + "_"))
        if not ok:
            problems.append(f"{oid}: under {got}, source host {host}")
    return problems


def export(path, version, split):
    import bpy

    res = bpy.ops.cgml3.export_gml_file(
        filepath=str(path), citygml_version=version, split_wall_roof_surfaces=split)
    if res != {"FINISHED"}:
        raise AssertionError(f"export failed for {path}: {res}")
    return path.read_text(encoding="utf-8")


def scenario(R, out, fixture, semantic, target, split=False):
    path, version = FIXTURES[fixture]
    tag = f"{fixture}_{'sem' if semantic else 'merged'}_to_v{target[0]}{'_split' if split else ''}"
    source = opening_hosts(path.read_text(encoding="utf-8"))
    kinds = sorted({k for k, _ in source.values()})
    try:
        h.import_fixture(path, version, use_semantic_objects=semantic)
        exported = opening_hosts(export(out / f"{tag}.gml", target, split))
    except Exception:
        for kind in kinds:
            R.check(f"{tag}_{kind}s_nested", False, traceback.format_exc()[-600:])
        return
    if split:
        moved = sum(1 for oid, (_, host) in source.items()
                    if host and oid in exported and (exported[oid][1] or "").startswith(host + "_"))
        R.check(f"{tag}_entries_exercised", moved > 0,
                f"{moved} openings nested under a split entry <host>_<polygon>")
    for kind in kinds:
        problems = nesting_problems(source, exported, kind, split)
        count = sum(1 for k, _ in source.values() if k == kind)
        R.check(f"{tag}_{kind}s_nested", not problems,
                f"{count - len(problems)}/{count} nested; " + "; ".join(problems[:8]))


OPENING_ID_KEYS = ("opening_gml_id", "opening_id", "con_opening_id")


def author(case):
    """Place one authored opening on the first Railway v3 wall; returns (fill, wall)."""
    from city_modeler.shared.facade_placement import (
        FacadeElementDefinition, PlacementRequest, place_semantic_element,
        surface_world_point_from_uv,
    )

    wall, face_index = h.first_wall_with_exterior_face()
    ctx = h.surface_context(wall, face_index)
    if case == "draw_window":
        definition = FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2)
    elif case == "draw_door_floating":
        definition = FacadeElementDefinition(semantic_type="Door", width=0.2, height=0.2,
                                             door_touches_ground=False)
    elif case == "draw_door_ground":
        # This face has a wide arched cut-out along the ground; only the
        # strip at u = 9.6..10.8 m reaches the ground edge.
        definition = FacadeElementDefinition(semantic_type="Door", width=0.4, height=2.0,
                                             door_touches_ground=True)
    else:
        definition = FacadeElementDefinition(semantic_type="Window", width=0.2, height=0.2)
    # AB-17 fix: the old fixed (0.3, 0.5) lands outside this fixture's real
    # solid area (Railway arched cut-out) for the interior-ring strategies;
    # the ground-Door strip above already accounts for the real geometry.
    if case == "draw_door_ground":
        uv = (10.2 / ctx.width, 0.5 / ctx.height)
    else:
        uv = h.solid_area_position_uv(wall, face_index, ctx, 0.1, 0.1)
    fill = place_semantic_element(definition, ctx, PlacementRequest(position_uv=uv, source="manual_draw"))
    if case == "stamp_window":
        target_uv = h.solid_area_position_uv(
            wall, face_index, ctx, 0.1, 0.1,
            avoid=[(uv[0] * ctx.width, uv[1] * ctx.height, 0.1, 0.1)])
        target = surface_world_point_from_uv(ctx, target_uv)
        fill = place_semantic_element(source_object=fill, surface_context=ctx, position_world=target)
    return fill, wall


def authored_scenario(R, out, case):
    tag = f"authored_{case}"
    try:
        h.import_fixture(FIXTURES["railway_v3"][0], "3.0", use_semantic_objects=True)
        fill, wall = author(case)
        fill_id, wall_id = str(fill.get("gml_id") or ""), str(wall.get("gml_id") or "")
        other = sorted({f"{key}={m[key]}" for m in fill.data.materials if m
                        for key in OPENING_ID_KEYS if key in m and str(m[key]) != fill_id})
        R.check(f"{tag}_single_opening_id", bool(fill_id) and not other,
                f"gml_id={fill_id!r}; other opening ids on its materials: {other[:4]}")
        for target in ("3.0", "2.0"):
            exported = opening_hosts(export(out / f"{tag}_to_v{target[0]}.gml", target, False))
            got = exported.get(fill_id, (None, "<missing>"))[1]
            R.check(f"{tag}_to_v{target[0]}_nested_by_gml_id", got == wall_id,
                    f"{fill_id} under {got}, host {wall_id}")
    except Exception:
        R.check(f"{tag}_single_opening_id", False, traceback.format_exc()[-600:])


def main():
    from pathlib import Path

    R = h.Results(__file__)
    out = Path(h.out_dir())
    for fixture in ("railway_v3", "railway_v2", "altopening_v3"):
        for semantic in (True, False):
            for target in ("3.0", "2.0"):
                scenario(R, out, fixture, semantic, target)
    scenario(R, out, "railway_v3", True, "3.0", split=True)
    for case in ("draw_window", "draw_door_floating", "draw_door_ground", "stamp_window"):
        authored_scenario(R, out, case)
    R.finish()


main()
