"""The last geometry check as rows the Edit tab lists and the viewport draws. No bpy.

Input is the JSON ``agent_api.validate`` / the CityDoctor button store in the scene
(``{"buildings": {gml_id: {"checks", "details", "citydoctor"}}}``). Each row carries the finding's
points in file coordinates: ``rings`` (polygons) or ``edges`` (open shell edges); a row without
points (a CityDoctor count) can only select its building. A later source (val3dity from the
database) only has to produce the same ``details`` shape.
"""
from __future__ import annotations

LABELS = {
    "GE_P_NON_PLANAR_POLYGON_DISTANCE_PLANE": "Not planar",
    "GE_R_SELF_INTERSECTION": "Ring crosses itself",
    "CGML_FILLING_WITHOUT_HOLE": "Window/door without hole",
    "GE_S_NOT_CLOSED": "Shell not closed",
    "CGML_SHELL_OPEN": "Shell open (no solid)",
}


def rows(last: dict) -> list[dict]:
    """``[{"building", "code", "label", "value", "rings", "edges"}]`` in building order."""
    out = []
    for gid, b in sorted((last or {}).get("buildings", {}).items()):
        for code, det in sorted((b.get("details") or {}).items()):
            label = LABELS.get(code, code)
            if isinstance(det, dict):  # the open shell: one row with its edges
                edges = [(e["a"], e["b"]) for e in det.get("edges", [])]
                value = f"{det.get('open_edges', len(edges))} edges"
                if det.get("t_junctions"):
                    value += f", {det['t_junctions']} T"
                out.append(_row(gid, code, label, value, [], edges))
                continue
            for d in det:
                value = f"{d['value_m'] * 1000:.0f} mm" if "value_m" in d else d.get("type", "")
                out.append(_row(gid, code, label, value, d.get("rings", []), []))
        for code, n in sorted((b.get("citydoctor") or {}).items()):
            out.append(_row(gid, code, f"CityDoctor {code}", f"x{n}", [], []))
    return out


def _row(gid, code, label, value, rings, edges):
    return {"building": gid, "code": code, "label": label, "value": value, "rings": rings, "edges": edges}


def to_scene(points, offset):
    """File coordinates -> scene coordinates (the exporter adds ``offset``)."""
    ox, oy, oz = offset
    return [(p[0] - ox, p[1] - oy, p[2] - oz) for p in points]


def segments(row, offset) -> list:
    """Line segment end points (scene coordinates) that outline ``row``: ring sides, open edges."""
    pts = []
    for ring in row["rings"]:
        r = to_scene(ring, offset)
        for a, b in zip(r, r[1:] + r[:1]):
            pts += [a, b]
    for a, b in row["edges"]:
        pts += to_scene([a, b], offset)
    return pts


def targets(row, offset) -> list:
    """The scene points a face (all corners) or vertex must sit on to belong to ``row``."""
    pts = [p for ring in row["rings"] for p in ring] + [p for e in row["edges"] for p in e]
    return to_scene(pts, offset)
