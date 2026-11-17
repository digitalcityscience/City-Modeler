# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Blender adapter for the CAC-P4.1 ancestor-aware checksum.

The one place that reads ``matrix_world``/``matrix_basis`` and walks an
object's ``.parent`` chain, converting what it finds into the plain values
``domain/checksum.py`` hashes (D-7c: the domain module stays bpy-free, R1).
No checksum math and no drift decision live here - this module only builds
snapshots; comparing them is ``domain.checksum.checksums_match``.

Duck-typed on ``obj`` (``.matrix_world``, ``.matrix_basis``, ``.parent``,
``.name``), like ``blender/accessor.py``/``blender/consistency_view.py``,
so it is exercised in unit tests against a fake object instead of a real
``bpy`` one.
"""
from __future__ import annotations

from ..domain import checksum


def _flatten_matrix(matrix) -> tuple:
    """Row-major 16-tuple for a 4x4 matrix.

    Works for a real ``mathutils.Matrix`` (iterates as four row Vectors) and
    for any plain 4x4 sequence-of-sequences a test hands in instead.
    """
    return tuple(float(value) for row in matrix for value in row)


def local_transform_snapshot(obj) -> checksum.TransformSnapshot:
    """Snapshot of ``obj``'s own local transform (``matrix_basis``), parent excluded."""
    return checksum.TransformSnapshot(matrix=_flatten_matrix(obj.matrix_basis))


def world_transform_snapshot(obj) -> checksum.TransformSnapshot:
    """Snapshot of ``obj``'s fully composed world transform.

    ``matrix_world`` already bakes the whole ancestor chain in (Blender
    recomputes it from every parent's own transform), so this alone is what
    makes an ancestor-only move change the checksum - see
    ``domain/checksum.py``'s module docstring.
    """
    return checksum.TransformSnapshot(matrix=_flatten_matrix(obj.matrix_world))


def ancestor_chain(obj) -> tuple:
    """``obj``'s ancestor names, root-first, ``obj`` itself last.

    Not hashed by itself (``matrix_world`` already reflects every ancestor's
    transform) - this is provenance for later Phase 4 tasks that need to
    name *which* ancestor moved, and a defensive break on a parent cycle
    (guards against an infinite walk on malformed/cyclic Blender state).
    """
    chain = []
    seen = set()
    node = obj
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        chain.append(getattr(node, "name", ""))
        node = getattr(node, "parent", None)
    return tuple(reversed(chain))


def record_snapshot(obj, gml_id, geometry=None, relations=()) -> checksum.RecordSnapshot:
    """``RecordSnapshot`` for ``obj`` using its world transform.

    ``geometry`` is an already-built ``checksum.GeometrySnapshot`` (world
    coordinates come from a mesh reader such as
    ``blender/consistency_view.py::geometry_fact``, not from this module -
    P4.1 does not add another geometry extraction path); ``relations`` is a
    tuple of ``(key, value)`` plain facts such as
    ``("host_id", feature.host_id)``.
    """
    return checksum.RecordSnapshot(
        gml_id=gml_id,
        transform=world_transform_snapshot(obj),
        geometry=geometry if geometry is not None else checksum.GeometrySnapshot(),
        relations=tuple(relations),
    )
