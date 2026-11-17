# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Ancestor-aware record checksum (CAC-P4.1).

Pure function(s) that turn a plain snapshot of a record-backed feature's
committed state (world transform + world-space geometry + relation facts)
into a single deterministic checksum, so later Phase 4 tasks (the commit
lifecycle P4.2, the guarded-delete wrapper P4.4, the transform-commit gate
P4.5) can detect drift by comparing two checksums instead of re-deriving
the comparison themselves.

No bpy, bmesh, mathutils or gpu import (R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``). This module never
touches a Blender object - ``blender/checksum_view.py`` is the one place
that reads ``matrix_world``/walks the ``.parent`` chain and converts what
it finds into the plain values a ``RecordSnapshot`` holds (D-7c).

Do not rely on an object's own local location/rotation/scale alone: a
committed ring's coordinates are stored in world frame (PLAN.md A4, D-2),
so an ancestor (e.g. a Building) moving without the WallSurface itself
changing must still change the effective checksum. ``TransformSnapshot``
is built from a fully composed ``matrix_world`` (Blender bakes the parent
chain into it already) precisely so this case is covered without this
module walking ancestors itself.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Same rounding convention as domain/consistency.py's COORD_DECIMALS (0.1 mm,
# world frame, PLAN.md A4) - kept independent rather than imported so this
# module has no dependency on consistency.py (P4.1 non-goal: consistency.py
# may reuse this checksum later, not the other way around).
COORD_DECIMALS = 4

# Matrix components are floats accumulated through Blender's own transform
# math; round before hashing so float noise from an unrelated recompute
# (e.g. a depsgraph update that touches the same object without moving it)
# does not register as drift.
MATRIX_DECIMALS = 6


class ChecksumError(ValueError):
    """A snapshot could not be built or hashed (malformed input shape)."""


# --------------------------------------------------------------------------
# snapshot shapes (plain data only - built by blender/checksum_view.py)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TransformSnapshot:
    """One object's transform as a flattened 4x4 matrix, row-major.

    Callers choose *which* matrix to flatten: a local ``matrix_basis`` (this
    object's own transform, parent excluded) or a world ``matrix_world``
    (parent chain baked in). The distinction is what lets a test prove an
    ancestor-only move changes the world snapshot but not the local one.
    """

    matrix: tuple = ()  # 16 floats

    def __post_init__(self):
        if len(self.matrix) != 16:
            raise ChecksumError(f"matrix must have 16 components, got {len(self.matrix)}")


@dataclass(frozen=True)
class GeometrySnapshot:
    """World-space coordinates a checksum should cover.

    ``polygons``: polygon_id -> tuple[(x, y, z), ...] exterior loop.
    ``rings``: a ring key (``opening_link``, matching ``domain/consistency.py``'s
    convention) -> tuple[(x, y, z), ...] interior loop.
    """

    polygons: dict = field(default_factory=dict)
    rings: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RecordSnapshot:
    """Everything one feature's commit-drift checksum is computed from.

    ``relations`` is a tuple of ``(key, value)`` plain facts (e.g.
    ``("host_id", "WALL1")``) - a tuple rather than a dict so equal snapshots
    built with keys in a different order still compare equal (order is
    normalized by sorting before hashing, not by construction order).
    """

    gml_id: str
    transform: TransformSnapshot
    geometry: GeometrySnapshot = field(default_factory=GeometrySnapshot)
    relations: tuple = ()


# --------------------------------------------------------------------------
# deterministic serialization
# --------------------------------------------------------------------------

def _round_component(value, decimals) -> str:
    # ``+ 0.0`` folds -0.0 to 0.0 so a value that rounds to zero from either
    # side of it serializes identically (matches domain/consistency.py).
    return f"{round(float(value), decimals) + 0.0:.{decimals}f}"


def _coords_text(coords) -> str:
    return ";".join(
        " ".join(_round_component(c, COORD_DECIMALS) for c in point)
        for point in coords
    )


def _geometry_text(geometry: GeometrySnapshot) -> str:
    parts = [f"P:{pid}={_coords_text(coords)}" for pid, coords in sorted(geometry.polygons.items())]
    parts += [f"R:{rid}={_coords_text(coords)}" for rid, coords in sorted(geometry.rings.items())]
    return "|".join(parts)


def _matrix_text(transform: TransformSnapshot) -> str:
    return ",".join(_round_component(c, MATRIX_DECIMALS) for c in transform.matrix)


def _relations_text(relations) -> str:
    return "|".join(f"{key}={value}" for key, value in sorted(relations, key=lambda pair: pair[0]))


def snapshot_text(snapshot: RecordSnapshot) -> str:
    """Deterministic plain-text serialization of ``snapshot``.

    Field order is fixed and every collection is sorted before joining, so
    two snapshots describing the same state serialize identically
    regardless of the order their inputs were built in.
    """
    return "\n".join((
        f"gml_id={snapshot.gml_id}",
        f"matrix={_matrix_text(snapshot.transform)}",
        f"geometry={_geometry_text(snapshot.geometry)}",
        f"relations={_relations_text(snapshot.relations)}",
    ))


def compute_checksum(snapshot: RecordSnapshot) -> str:
    """A deterministic checksum for ``snapshot`` (sha256 hex digest of its
    serialized text)."""
    return hashlib.sha256(snapshot_text(snapshot).encode("utf-8")).hexdigest()


def checksums_match(a: RecordSnapshot, b: RecordSnapshot) -> bool:
    return compute_checksum(a) == compute_checksum(b)
