"""Explicit export scope and the written/skipped record (B56, agent plan A1).

The writers export the selected features (plus their hierarchy) when something is selected, else the
whole scene. A forgotten selection used to export one building silently. Now the export operators take
``scope`` (SCENE / SELECTION / IDS): the selection is set for the writer and restored afterwards, and
the written top-level features are read back from the file, so the report and
``scene["cgml_last_export"]`` say what was written and what was skipped.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

SCOPES = ("SCENE", "SELECTION", "IDS")
LAST_EXPORT_KEY = "cgml_last_export"


_CITYGML_OBJECT_KEYS = ("cgml3_feature", "gml_id", "ModelType", "structure_type", "structure_part",
                        "cgml_semantic_type", "feature_type")
_CITYGML_MATERIAL_KEYS = ("SurfaceType", "surface_type", "gml_polygon_id", "con_surface_id")


def is_plain_blender_object(obj, feature_type_prop="ModelType") -> bool:
    """B79: an object without any CityGML marking (the startup Cube, a helper plane). The writers used
    to export it as a Building named after the object; now they skip it and the report names it."""
    keys = _CITYGML_OBJECT_KEYS + ((feature_type_prop,) if feature_type_prop else ())
    try:
        if any(str(obj.get(k) or "").strip() for k in keys):
            return False
        for mat in getattr(getattr(obj, "data", None), "materials", None) or ():
            if mat is not None and any(str(mat.get(k) or "").strip() for k in _CITYGML_MATERIAL_KEYS):
                return False
    except Exception:
        return False  # unreadable: keep the old behaviour
    return True


def plain_objects(scene) -> list[str]:
    """Names of the visible meshes / curves / empties the writers skip as not CityGML (B79)."""
    from .export_helpers import is_export_helper
    out = []
    for obj in scene.objects:
        try:
            if obj.type in ("MESH", "CURVE", "EMPTY") and obj.visible_get() and not is_export_helper(obj)                     and is_plain_blender_object(obj):
                out.append(obj.name)
        except Exception:
            pass
    return out


def _feature_name(obj) -> str:
    try:
        return str(obj.get("cgml3_feature") or obj.get("feature_type") or "").strip()
    except Exception:
        return ""


def _gml_id(obj) -> str:
    try:
        return str(obj.get("gml_id") or "").strip()
    except Exception:
        return ""


def top_level_feature_objects(scene) -> dict:
    """``{gml_id: object}`` of the scene's top-level features (no feature ancestor)."""
    from .export_helpers import is_export_helper

    out: dict = {}
    for obj in scene.objects:
        feat, gid = _feature_name(obj), _gml_id(obj)
        if not feat or not gid or gid.endswith(("_outer", "_geom")) or is_export_helper(obj):
            continue
        parent = obj.parent
        while parent is not None and not _feature_name(parent):
            parent = parent.parent
        if parent is None:
            out.setdefault(gid, obj)
    return out


def top_level_features(scene) -> dict[str, str]:
    """``{gml_id: feature type}`` of the scene's top-level features (no feature ancestor)."""
    return {gid: _feature_name(obj) for gid, obj in top_level_feature_objects(scene).items()}


def parse_ids(text) -> list[str]:
    """GML ids from a list or a comma/semicolon/whitespace separated string."""
    if isinstance(text, (list, tuple)):
        return [str(s).strip() for s in text if str(s).strip()]
    return [s for s in str(text or "").replace(";", ",").replace(",", " ").split() if s]


def validate_scope(view_layer, scene, scope: str, gml_ids=()) -> set:
    """The ids IDS selects; ``ValueError`` for an unknown scope, an empty selection with SELECTION,
    and ids that are not top-level features of the scene with IDS (nothing is written then)."""
    if scope not in SCOPES:
        raise ValueError(f"unknown export scope {scope!r} (use one of {', '.join(SCOPES)})")
    if scope == "SELECTION" and not any(o.select_get(view_layer=view_layer) for o in view_layer.objects):
        raise ValueError("export scope SELECTION but nothing is selected")
    if scope != "IDS":
        return set()
    ids = parse_ids(gml_ids)
    if not ids:
        raise ValueError("export scope IDS needs at least one GML id")
    known = top_level_features(scene)
    missing = [i for i in ids if i not in known]
    if missing:
        raise ValueError("not a top-level feature of the scene: " + ", ".join(missing[:10]))
    return set(ids)


@contextmanager
def export_selection(view_layer, scene, scope: str, gml_ids=()):
    """Set the view-layer selection the writers read for ``scope``; restore it afterwards.
    Raises ``ValueError`` like ``validate_scope``."""
    wanted = validate_scope(view_layer, scene, scope, gml_ids)
    before = [o for o in view_layer.objects if o.select_get(view_layer=view_layer)]
    if scope != "SELECTION":
        for o in before:
            o.select_set(False, view_layer=view_layer)
        for o in view_layer.objects:
            if wanted and _gml_id(o) in wanted and _feature_name(o):
                o.select_set(True, view_layer=view_layer)
    try:
        yield
    finally:
        if scope != "SELECTION":
            for o in view_layer.objects:
                o.select_set(False, view_layer=view_layer)
            for o in before:
                try:
                    o.select_set(True, view_layer=view_layer)
                except RuntimeError:  # removed or no longer in the view layer
                    pass


def written_feature_ids(path: str) -> list[str]:
    """gml:id of every top-level feature (child of a cityObjectMember) in the written file."""
    import xml.etree.ElementTree as ET

    gml_id = "{http://www.opengis.net/gml}id"
    gml32_id = "{http://www.opengis.net/gml/3.2}id"
    ids, depth_member = [], None
    depth = 0
    for event, el in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            if el.tag.endswith("}cityObjectMember"):
                depth_member = depth
            elif depth_member is not None and depth == depth_member + 1:
                gid = el.get(gml32_id) or el.get(gml_id)
                if gid:
                    ids.append(gid)
        else:
            if depth_member is not None and depth == depth_member:
                depth_member = None
            depth -= 1
            if depth_member is None:
                el.clear()
    return ids


def record_export(scene, path: str, version: str, scope: str, target: str = "file") -> dict:
    """Compare the written file with the scene, store the JSON record on the scene and return it."""
    written = written_feature_ids(path)
    features = top_level_features(scene)
    written_set = set(written)
    skipped = [gid for gid in features if gid not in written_set]
    by_type: dict[str, int] = {}
    for gid in written:
        t = features.get(gid, "?")
        by_type[t] = by_type.get(t, 0) + 1
    rec = {
        "path": path, "version": version, "scope": scope, "target": target,
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "written_count": len(written), "skipped_count": len(skipped), "not_citygml": plain_objects(scene),
        "written": written, "skipped": skipped, "written_by_type": by_type,
        "xsd": "not run", "geometry_checked": False,  # B57: say what was not checked
    }
    scene[LAST_EXPORT_KEY] = json.dumps(rec)
    return rec


def summary(rec: dict) -> str:
    """One line for the operator report."""
    text = f"{rec['written_count']} feature(s) written, {rec['skipped_count']} skipped (scope {rec['scope']})"
    if rec["skipped"]:
        text += ": skipped " + ", ".join(rec["skipped"][:5]) + (" ..." if len(rec["skipped"]) > 5 else "")
    plain = rec.get("not_citygml") or []
    if plain:
        text += (f"; {len(plain)} Blender object(s) without a CityGML type not written: "
                 + ", ".join(plain[:5]) + (" ..." if len(plain) > 5 else "") + " (Make CityGML turns one into a building)")
    return text


def last_export(scene) -> dict | None:
    raw = scene.get(LAST_EXPORT_KEY)
    return json.loads(raw) if raw else None
