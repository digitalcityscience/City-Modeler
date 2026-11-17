"""Snapshot real Blender objects into the views the pure legacy reader reads.

Read-only. A view lists only the materials that the object's faces use, in
slot order: a Building keeps all its material slots after the semantic
explode, and those unused slots are not polygons of the Building.
"""
from ..domain.legacy_reader import MaterialView, ObjectView, read_legacy_scene


def _plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def _props(idblock):
    return {key: _plain(idblock[key]) for key in idblock.keys()}


def used_materials(obj):
    """Materials used by at least one face of ``obj`` (mesh objects only). An opening whose
    depth is display-only (B49, ``cgml_synthetic_opening_depth``) exports one polygon - its
    first face - so the view lists only that material."""
    if obj.type != "MESH" or obj.data is None:
        return ()
    if obj.get("cgml_synthetic_opening_depth") and len(obj.data.polygons) > 1:
        used = {obj.data.polygons[0].material_index}
    else:
        used = {poly.material_index for poly in obj.data.polygons}
    mats = obj.data.materials
    return tuple(mats[i] for i in sorted(used) if 0 <= i < len(mats) and mats[i] is not None)


def object_view(obj):
    return ObjectView(
        name=obj.name,
        type=obj.type,
        props=_props(obj),
        parent=obj.parent.name if obj.parent else None,
        materials=tuple(MaterialView(m.name, _props(m)) for m in used_materials(obj)),
    )


def scene_views(objects=None):
    import bpy

    return [object_view(o) for o in (bpy.data.objects if objects is None else objects)]


def read_scene(objects=None):
    """LegacyScene (records + notes) for ``objects`` (default: all objects)."""
    return read_legacy_scene(scene_views(objects))
