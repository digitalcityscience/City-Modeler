"""Polygon-identity commit rules for RoofSurface topology edits (CAC-P1.7,
PLAN.md §5, go/no-go G1b).

``decide(before, after_groups)`` is a pure function: given the
``PolygonRecord``s a feature had *before* a topology edit and the
polygon-shaped ``FaceGroup``s the mesh has *after* the edit (already grouped
into loops by whatever Blender-side code runs ahead of this - grouping
faces into islands is not this module's job), it decides what happens to
each polygon's identity:

- a key claimed by exactly one surviving group keeps its id (vertex move,
  triangulate-then-dissolve back to one face - the group is simply
  re-validated and its coordinates updated);
- a key claimed by more than one surviving group is a split: the first
  group (input order) keeps the id, every other group mints a fresh one;
- a key no longer claimed by any group is removed (this is also how a
  coplanar merge/dissolve falls out: the group that used to carry the
  dissolved-away key just stops appearing, with no special-case code);
- a group with no key (or a key this feature never had) mints a fresh id;
- a group whose points are not planar within tolerance is refused rather
  than committed, with a reason - it never becomes a ``PolygonRecord``.

Nothing here touches Blender: identity math runs on plain ``(x, y, z)``
tuples, and ``decide`` returns a ``CommitPlan`` describing what to do, never
mutating anything itself.

Planarity/frame math is reused from ``shared.facade_surface_resolver``
(``compute_facade_frame``) rather than reimplemented here: that module's own
docstring establishes it is pure Python with no ``bpy``/``mathutils`` at
module level (only its live-mesh wrappers import those lazily inside
functions), so importing it does not pull Blender into this domain module
and does not trip the R1 guard (``test_cac_domain_has_no_bpy``), which scans
each file's own import statements, not its transitive imports.
"""
from dataclasses import dataclass

from shared.facade_surface_resolver import compute_facade_frame

from .ids import mint_gml_id, ring_id
from .records import PolygonRecord, RingRecord

Point3 = tuple


@dataclass(frozen=True)
class FaceGroup:
    """One polygon-shaped island of faces after a topology edit, in world
    coordinates. ``poly_key`` is the polygon id the group's faces still
    carry, or ``None`` when they carry none (or a mix)."""

    poly_key: str | None
    exterior: tuple            # tuple[Point3, ...]
    interiors: tuple = ()      # tuple[tuple[Point3, ...], ...]


@dataclass(frozen=True)
class Refusal:
    """A group that was not committed, and why."""

    poly_key: str | None
    reason: str


@dataclass(frozen=True)
class CommitPlan:
    kept: tuple = ()      # tuple[PolygonRecord] - same id, geometry updated
    minted: tuple = ()    # tuple[PolygonRecord] - freshly minted id
    removed: tuple = ()   # tuple[str] - polygon_ids with no surviving group
    refused: tuple = ()   # tuple[Refusal] - groups rejected, never committed
    report: tuple = ()    # tuple[str] - one human-readable note per event

    def polygon(self, polygon_id):
        return next((p for p in self.kept + self.minted if p.polygon_id == polygon_id), None)


# --------------------------------------------------------------------------
# Planarity - reuses compute_facade_frame for the exterior ring; the
# interior-ring check below is a small local addition (compute_facade_frame
# only validates the single loop it is given, and it is only ever given the
# exterior here), not a reimplementation of that module's own math.
# --------------------------------------------------------------------------
_INTERIOR_PLANAR_ABS_TOL = 1e-4
_INTERIOR_PLANAR_REL_TOL = 1e-3


def _bbox_diagonal(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    span = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return sum(c * c for c in span) ** 0.5


def _check_planar(group):
    """Validate ``group`` and return its ``FacadeFrame``, or raise
    ``ValueError`` (message doubles as the refusal reason) if it is not
    planar within tolerance."""
    frame = compute_facade_frame(list(group.exterior))
    if not group.interiors:
        return frame
    all_points = list(group.exterior) + [p for ring in group.interiors for p in ring]
    tol = max(_INTERIOR_PLANAR_ABS_TOL, _INTERIOR_PLANAR_REL_TOL * _bbox_diagonal(all_points))
    for ring in group.interiors:
        for p in ring:
            deviation = abs(sum((p[i] - frame.origin[i]) * frame.normal[i] for i in range(3)))
            if deviation > tol:
                raise ValueError(
                    f"non-planar interior ring: a vertex deviates {deviation:.6g}m from "
                    f"the exterior's plane (tolerance {tol:.6g}m) - reject and report"
                )
    return frame


def _polygon_record(group, polygon_id, feature_id):
    return PolygonRecord(
        polygon_id=polygon_id,
        feature_id=feature_id,
        exterior=RingRecord(ring_id=ring_id(polygon_id, 0), role="exterior",
                             coords=tuple(group.exterior)),
        interiors=tuple(
            RingRecord(ring_id=ring_id(polygon_id, i + 1), role="interior", coords=tuple(ring))
            for i, ring in enumerate(group.interiors)
        ),
        geometry_state="committed",
    )


def _commit(group, polygon_id, feature_id, target, refused, report, note):
    try:
        _check_planar(group)
    except ValueError as exc:
        refused.append(Refusal(poly_key=group.poly_key, reason=str(exc)))
        report.append(f"polygon {polygon_id!r} refused: {exc}")
        return
    target.append(_polygon_record(group, polygon_id, feature_id))
    report.append(note)


def decide(before, after_groups, *, id_factory=None):
    """Pure: ``before`` is this feature's current ``PolygonRecord``s,
    ``after_groups`` the ``FaceGroup``s found after a topology edit.
    ``id_factory`` (default: ``mint_gml_id``) mints fresh polygon ids;
    tests pass a deterministic one."""
    mint = id_factory or (lambda: mint_gml_id("ID"))

    before_by_id = {p.polygon_id: p for p in before}
    default_feature_id = before[0].feature_id if before else ""

    groups_by_key = {}
    unkeyed = []
    for group in after_groups:
        if group.poly_key is not None and group.poly_key in before_by_id:
            groups_by_key.setdefault(group.poly_key, []).append(group)
        else:
            unkeyed.append(group)

    kept, minted, removed, refused, report = [], [], [], [], []

    for polygon_id, source in before_by_id.items():
        groups = groups_by_key.get(polygon_id, [])
        if not groups:
            removed.append(polygon_id)
            report.append(f"polygon {polygon_id!r} removed: no surviving face group carries its key")
            continue
        first, *rest = groups
        _commit(first, polygon_id, source.feature_id, kept, refused, report,
                note=f"polygon {polygon_id!r} kept: geometry updated")
        for extra in rest:
            new_id = mint()
            _commit(extra, new_id, source.feature_id, minted, refused, report,
                    note=f"polygon {polygon_id!r} split: duplicate key claimed by another face "
                         f"group, minted {new_id!r}")

    for group in unkeyed:
        new_id = mint()
        reason = "no key" if group.poly_key is None else f"unknown key {group.poly_key!r}"
        _commit(group, new_id, default_feature_id, minted, refused, report,
                note=f"polygon {new_id!r} minted: face group has {reason}")

    return CommitPlan(
        kept=tuple(kept), minted=tuple(minted), removed=tuple(removed),
        refused=tuple(refused), report=tuple(report),
    )
