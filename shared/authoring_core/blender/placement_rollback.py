# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Undo a failed record-backed placement exactly (CAC-P4.10, AB-20).

``create_feature`` runs a preflight before it touches anything, so a normal
refusal never needs this. It is the safety net for a failure *after*
``place_semantic_element`` has already cut the wall: a Blender operator that
returns ``CANCELLED`` pushes no undo step, so without an explicit restore the
half-built Window, the new ring face and the new materials would stay in the
scene and be folded into the next undo step.

Deliberately narrow - it restores exactly what the placement kernel is known
to write, not a generic transaction:

- the host wall's mesh datablock (ring/fill cut, new material slots, mesh
  custom properties such as ``cgml3_uv_start_by_ring``) - swapped back to a
  copy taken before placement, under its original name;
- every object, mesh, material, node group and image created since the
  snapshot (the fill object, its mesh, the cutter/glass materials);
- custom properties of every object in the wall's top-level feature and of
  those objects' mesh materials - LoD promotion (``lod``/``cgml3_lod``), the
  source material's id backfill in ``create_joined_surfaces``, and every
  object's ``_cgml_record`` (the records are custom properties);
- the wall's Edit/Object mode.

Callers mark the registry dirty afterwards (``restore`` does it).
"""
from __future__ import annotations

_NEW_ID_COLLECTIONS = ("objects", "meshes", "materials", "node_groups", "images")


def _pointer(idblock):
    return idblock.as_pointer()


def _plain(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_list"):
        return value.to_list()
    return value


def _props(idblock):
    return {key: _plain(idblock[key]) for key in idblock.keys()}


def _restore_props(idblock, saved) -> None:
    for key in [k for k in idblock.keys() if k not in saved]:
        del idblock[key]
    for key, value in saved.items():
        if key not in idblock or _plain(idblock[key]) != value:
            idblock[key] = value


def _mesh_signature(mesh):
    coords = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", coords)
    return (
        len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops),
        tuple(m.name if m else None for m in mesh.materials),
        tuple(round(c, 6) for c in coords),
        tuple(sorted(mesh.keys())),
    )


class PlacementSnapshot:
    """Captured by ``capture(wall)``; then exactly one of ``discard()`` (the
    placement succeeded) or ``restore()`` (it failed)."""

    def __init__(self, wall):
        import bpy

        from ...lod_helpers import lod_promotion_root

        self.wall = wall
        self.mode = wall.mode
        if wall.mode == "EDIT":
            wall.update_from_editmode()
        self.mesh_name = wall.data.name
        self.mesh_signature = _mesh_signature(wall.data)
        self.backup = wall.data.copy()
        self.ids = {
            name: {_pointer(idb) for idb in getattr(bpy.data, name)} for name in _NEW_ID_COLLECTIONS
        }
        root = lod_promotion_root(wall) or wall
        members = {_pointer(wall): wall, _pointer(root): root}
        stack = list(root.children)
        while stack:
            obj = stack.pop()
            members.setdefault(_pointer(obj), obj)
            stack.extend(obj.children)
        self.object_props = [(obj, _props(obj)) for obj in members.values()]
        materials = {}
        for obj in members.values():
            if obj.type == "MESH" and obj.data is not None:
                for mat in obj.data.materials:
                    if mat is not None:
                        materials.setdefault(_pointer(mat), mat)
        self.material_props = [(mat, _props(mat)) for mat in materials.values()]

    def discard(self) -> None:
        import bpy

        if self.backup is not None:
            bpy.data.meshes.remove(self.backup)
            self.backup = None

    def restore(self) -> None:
        import bpy

        from . import registry

        wall = self.wall
        if wall.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")

        for obj in [o for o in bpy.data.objects if _pointer(o) not in self.ids["objects"]]:
            bpy.data.objects.remove(obj, do_unlink=True)

        broken = wall.data
        if _pointer(broken) != _pointer(self.backup) and _mesh_signature(broken) != self.mesh_signature:
            for obj in bpy.data.objects:
                if obj.data is not None and _pointer(obj.data) == _pointer(broken):
                    obj.data = self.backup
            bpy.data.meshes.remove(broken)
            self.backup.name = self.mesh_name
            self.backup = None
        else:
            self.discard()

        # Anything created since the snapshot and now unused (the fill's mesh,
        # the cutter and glass materials, their node trees/images).
        for name in _NEW_ID_COLLECTIONS[1:]:
            collection = getattr(bpy.data, name)
            for idb in [i for i in collection if _pointer(i) not in self.ids[name] and i.users == 0]:
                collection.remove(idb)

        for obj, saved in self.object_props:
            _restore_props(obj, saved)
        for mat, saved in self.material_props:
            _restore_props(mat, saved)

        if self.mode == "EDIT" and wall.mode != "EDIT":
            bpy.context.view_layer.objects.active = wall
            bpy.ops.object.mode_set(mode="EDIT")
        registry.REGISTRY.mark_dirty()


def capture(wall) -> PlacementSnapshot:
    return PlacementSnapshot(wall)
