# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Record <-> legacy consistency validator (CAC-P3.3, PLAN.md P3.3).

Read-only. Detects drift between a stored ``FeatureRecord``/``PolygonRecord``
set (P3.2/P3.2a's authoring lifecycle) and the live legacy Blender state the
exporter still reads, plus drift between a *committed* record's world-frame
coordinates and the live mesh (G/R/S, Align, a parent transform - none of
which Blender lets a P3.0 guard refuse).

This module never repairs anything and never touches Blender: it consumes
plain facts built by ``blender/consistency_view.py`` (the adapter) - a
freshly re-derived ``LegacyScene`` (the same oracle P1.3/P3.2 use, built by
``domain/legacy_reader.py``) and a ``GeometryFact`` of live world-space
coordinates keyed the same way the record already is (``opening_link`` for
rings - see ``blender/usecases.py``). Comparing the *stored* record against
a *fresh* read of the same live state is exactly what ``create_feature``'s
own dual-write derivation guarantees can't drift for relational fields; this
is that same comparison, run later, after tools P3.0 cannot guard against
have had a chance to move things.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Opening kinds this slice knows about (kinds.py: Window enabled, Door
# declared-but-disabled). Not read from the FeatureKind registry directly -
# a disabled kind can still appear in imported/legacy records.
OPENING_KINDS = ("Window", "Door")

COORD_DECIMALS = 4  # 0.1 mm, world frame (PLAN.md A4; matches cac_harness.py)

# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------
ERROR = "error"
WARNING = "warning"

MISSING_OBJECT = "missing_object"
DUPLICATE_GML_ID = "duplicate_gml_id"
DUPLICATE_RECORD = "duplicate_record"
FOREIGN_RECORD = "foreign_record"
KIND_MISMATCH = "kind_mismatch"
SEMANTIC_TYPE_MISMATCH = "semantic_type_mismatch"
LOD_MISMATCH = "lod_mismatch"
PARENT_MISMATCH = "parent_id_mismatch"
HOST_MISMATCH = "host_id_mismatch"
OWNER_MISMATCH = "owner_id_mismatch"
OPENING_LINK_MISMATCH = "opening_link_mismatch"
POLYGON_IDS_MISMATCH = "polygon_ids_mismatch"
MISSING_RING_PAIRING = "missing_ring_pairing"
DANGLING_RING = "dangling_ring"
ORPHAN_OPENING = "orphan_opening"
UNRECORDED_HOST = "unrecorded_host"
COORDS_STALE = "coords_stale"
GEOMETRY_UNRESOLVED = "geometry_unresolved"

# Findings at or above this severity block export pre-flight (P3.3 policy);
# everything else is reported only. coords_stale is deliberately not here
# (PLAN.md P3.3: "a mismatch is a coords_stale finding, not an error") -
# G/R/S drift is expected until P4's commit lifecycle exists.
BLOCKING = (
    MISSING_OBJECT, DUPLICATE_GML_ID, DUPLICATE_RECORD, FOREIGN_RECORD,
    KIND_MISMATCH, PARENT_MISMATCH, HOST_MISMATCH, OPENING_LINK_MISMATCH,
    POLYGON_IDS_MISMATCH, MISSING_RING_PAIRING, DANGLING_RING, UNRECORDED_HOST,
)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str           # ERROR | WARNING
    message: str
    gml_id: str = ""        # the feature concerned; "" if scene-wide
    related: tuple = ()     # related object/surface/polygon/ring ids, sorted


def is_blocking(findings) -> bool:
    return any(f.severity == ERROR for f in findings)


# --------------------------------------------------------------------------
# Inputs the adapter builds (blender/consistency_view.py)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LegacyFact:
    """What a fresh read of live legacy state says about one feature."""

    exists: bool
    feature: object = None       # FeatureRecord, freshly derived; None if not exists
    polygon_ids: tuple = ()      # polygon ids legacy state currently associates with it


@dataclass(frozen=True)
class GeometryFact:
    """Live world-space coordinates, keyed the way records already are.

    ``polygons``: polygon_id -> tuple[(x, y, z), ...] exterior loop, or
    ``None`` if the identifying material/face could not be found.
    ``rings``: ring's ``opening_link`` -> tuple[(x, y, z), ...] interior loop,
    or ``None`` - the same key ``create_feature`` matches rings by, since an
    imported ring's ``ring_id`` is not guaranteed stable across tessellation.
    ``generic_rings`` (CAC-P6.1a): host polygon_id -> tuple[tuple[(x, y, z), ...], ...],
    one entry per interior-ring face on that polygon that carries no
    ``opening_link`` (an ordinary hole - a Roof skylight cutout, not a
    Window/Door opening), in deterministic face order. There is no stable
    per-ring key for these the way ``opening_link`` is for a Window's ring,
    so they are matched to a polygon's ``interiors`` positionally instead.
    """

    polygons: dict = field(default_factory=dict)
    rings: dict = field(default_factory=dict)
    generic_rings: dict = field(default_factory=dict)


def _coordinate_digest(coords):
    """Same rounding convention as ``scripts/headless/cac_harness.py:polygon_digest``."""
    text = ";".join(
        " ".join(f"{round(c, COORD_DECIMALS) + 0.0:.{COORD_DECIMALS}f}" for c in point)
        for point in coords
    )
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def coords_match(a, b) -> bool:
    """True when two committed coordinate loops are the same point set within tolerance."""
    if a is None or b is None:
        return a is None and b is None
    return _coordinate_digest(a) == _coordinate_digest(b)


# --------------------------------------------------------------------------
# Per-feature validation
# --------------------------------------------------------------------------

def _geometry_findings(feature, polygons, geometry):
    findings = []
    for polygon in polygons:
        if polygon.geometry_state != "committed":
            continue
        live = geometry.polygons.get(polygon.polygon_id)
        if live is None:
            findings.append(Finding(
                GEOMETRY_UNRESOLVED, WARNING,
                f"polygon {polygon.polygon_id!r} of {feature.gml_id!r}: live exterior face "
                "could not be resolved (object or material missing)",
                feature.gml_id, (polygon.polygon_id,),
            ))
        elif not coords_match(polygon.exterior.coords, live):
            findings.append(Finding(
                COORDS_STALE, WARNING,
                f"polygon {polygon.polygon_id!r} of {feature.gml_id!r}: committed coordinates "
                "no longer match the live mesh (moved after commit?)",
                feature.gml_id, (polygon.polygon_id,),
            ))
        for ring in polygon.interiors:
            if not ring.coords or not ring.opening_link:
                continue
            live_ring = geometry.rings.get(ring.opening_link)
            if live_ring is None:
                findings.append(Finding(
                    GEOMETRY_UNRESOLVED, WARNING,
                    f"ring for opening_link {ring.opening_link!r} of {feature.gml_id!r}: "
                    "live interior-ring face could not be resolved",
                    feature.gml_id, (polygon.polygon_id, ring.opening_link),
                ))
            elif not coords_match(ring.coords, live_ring):
                findings.append(Finding(
                    COORDS_STALE, WARNING,
                    f"ring for opening_link {ring.opening_link!r} of {feature.gml_id!r}: "
                    "committed coordinates no longer match the live mesh",
                    feature.gml_id, (polygon.polygon_id, ring.opening_link),
                ))
    return findings


def validate_feature(feature, polygons, legacy, geometry) -> tuple:
    """Findings for one stored ``feature``/``polygons`` pair.

    ``legacy`` is a ``LegacyFact`` for ``feature.gml_id`` (a fresh read, not
    the record). ``geometry`` is a ``GeometryFact`` covering at least this
    feature's own polygons/rings.
    """
    if not legacy.exists:
        return (Finding(
            MISSING_OBJECT, ERROR,
            f"record references {feature.gml_id!r} but no live object carries that identity",
            feature.gml_id,
        ),)

    lf = legacy.feature
    findings = []

    if feature.kind != lf.kind:
        findings.append(Finding(
            KIND_MISMATCH, ERROR,
            f"{feature.gml_id!r}: record kind {feature.kind!r} != live kind {lf.kind!r}",
            feature.gml_id,
        ))
    record_semantic = feature.semantic_type or feature.kind
    legacy_semantic = lf.semantic_type or lf.kind
    if record_semantic != legacy_semantic:
        findings.append(Finding(
            SEMANTIC_TYPE_MISMATCH, WARNING,
            f"{feature.gml_id!r}: record semantic_type {record_semantic!r} != "
            f"live {legacy_semantic!r}",
            feature.gml_id,
        ))
    if feature.parent_id != lf.parent_id:
        findings.append(Finding(
            PARENT_MISMATCH, ERROR,
            f"{feature.gml_id!r}: record parent_id {feature.parent_id!r} != "
            f"live {lf.parent_id!r}",
            feature.gml_id,
        ))
    if feature.host_id != lf.host_id:
        findings.append(Finding(
            HOST_MISMATCH, ERROR,
            f"{feature.gml_id!r}: record host_id {feature.host_id!r} != live {lf.host_id!r}",
            feature.gml_id,
        ))
    if feature.owner_id != lf.owner_id:
        findings.append(Finding(
            OWNER_MISMATCH, WARNING,
            f"{feature.gml_id!r}: record owner_id {feature.owner_id!r} != live {lf.owner_id!r}",
            feature.gml_id,
        ))
    if feature.opening_link != lf.opening_link:
        findings.append(Finding(
            OPENING_LINK_MISMATCH, ERROR,
            f"{feature.gml_id!r}: record opening_link {feature.opening_link!r} != "
            f"live {lf.opening_link!r}",
            feature.gml_id,
        ))
    if feature.lod is not None and lf.lod is not None and feature.lod != lf.lod:
        findings.append(Finding(
            LOD_MISMATCH, WARNING,
            f"{feature.gml_id!r}: record lod {feature.lod!r} != live lod {lf.lod!r}",
            feature.gml_id,
        ))
    if set(feature.polygon_ids) != set(legacy.polygon_ids):
        findings.append(Finding(
            POLYGON_IDS_MISMATCH, ERROR,
            f"{feature.gml_id!r}: record polygon ids {sorted(feature.polygon_ids)} != "
            f"live {sorted(legacy.polygon_ids)}",
            feature.gml_id,
        ))
    if feature.kind in OPENING_KINDS and not feature.host_id and not feature.parent_id:
        findings.append(Finding(
            ORPHAN_OPENING, WARNING,
            f"{feature.gml_id!r}: {feature.kind} has no host/parent surface "
            "(CityGML 2 export would drop it silently, see P2.5 orphan-opening note)",
            feature.gml_id,
        ))

    findings.extend(_geometry_findings(feature, polygons, geometry))
    return tuple(findings)


def _ring_pairing_findings(record_set):
    """C3 ring pairing, both directions:

    - an opening's ``opening_link`` must resolve to one of its host's own
      rings, when the host is itself in ``record_set`` (a broken
      ``opening_link``, or a RingRecord that was dropped without the
      opening's own record being updated);
    - a wall's own RingRecord ``opening_link`` must resolve to a live
      opening feature - X-deleting a record-backed Window (AB-02) removes
      its own record entirely (per-object storage, ADR 0009) but leaves
      the host's ring behind, which nothing else here would catch.
    """
    findings = []
    opening_links = {f.opening_link for f in record_set.features if f.opening_link}
    for feature in record_set.features:
        for polygon in record_set.polygons_of(feature.gml_id):
            for ring in polygon.interiors:
                if ring.opening_link and ring.opening_link not in opening_links:
                    findings.append(Finding(
                        DANGLING_RING, ERROR,
                        f"{feature.gml_id!r}: polygon {polygon.polygon_id!r} carries a RingRecord for "
                        f"opening_link {ring.opening_link!r} but no live opening claims it "
                        "(deleted Window/Door without its ring being removed, AB-02)",
                        feature.gml_id, (polygon.polygon_id, ring.opening_link),
                    ))
    for feature in record_set.features:
        if feature.kind not in OPENING_KINDS or not feature.opening_link:
            continue
        host = record_set.feature(feature.host_id) if feature.host_id else None
        if host is None:
            continue  # host not in this scope; not this validator's business
        host_polygons = record_set.polygons_of(host.gml_id)
        paired = any(
            ring.opening_link == feature.opening_link
            for polygon in host_polygons
            for ring in polygon.interiors
        )
        if not paired:
            findings.append(Finding(
                MISSING_RING_PAIRING, ERROR,
                f"{feature.gml_id!r}: host {host.gml_id!r} has no RingRecord for "
                f"opening_link {feature.opening_link!r}",
                feature.gml_id, (host.gml_id,),
            ))
    return findings


def _unrecorded_host_findings(record_set, legacy_of):
    """A record-backed opening whose host exists live but carries no record
    of its own (CAC-P4.10, AB-20).

    Every supported path that authors an opening record also writes the host
    wall's record (``create_feature``), and every edit of one refuses a
    legacy-only host (``edit_session._resolve_window_and_wall``). An opening
    record naming a live, record-less host is therefore always a partial
    write - exactly what a failed Draw left behind before P4.10 - and its
    missing host-side ring pairing is invisible to ``_ring_pairing_findings``,
    which only checks hosts that are in the RecordSet.
    """
    findings = []
    for feature in record_set.features:
        if feature.kind not in OPENING_KINDS or not feature.host_id:
            continue
        if record_set.feature(feature.host_id) is not None:
            continue
        if legacy_of(feature.host_id).exists:
            findings.append(Finding(
                UNRECORDED_HOST, ERROR,
                f"{feature.gml_id!r}: host {feature.host_id!r} exists but carries no authoring "
                "record, so its ring for this opening is not recorded (partial create?)",
                feature.gml_id, (feature.host_id,),
            ))
    return findings


def _referenced_object_findings(record_set, legacy_of):
    """A record's ``host_id``/``parent_id``/``owner_id`` must resolve to a
    live object - unlike the relational *value* checks in ``validate_feature``
    (which compare record vs. legacy on the *same* object and so cannot see
    a since-deleted ancestor, since a legacy custom property survives on the
    surviving object regardless of whether the one it names still exists)."""
    findings = []
    for feature in record_set.features:
        for role, ref_id in (("host_id", feature.host_id), ("parent_id", feature.parent_id),
                              ("owner_id", feature.owner_id)):
            if ref_id and not legacy_of(ref_id).exists:
                findings.append(Finding(
                    MISSING_OBJECT, ERROR,
                    f"{feature.gml_id!r}: {role} {ref_id!r} references no live object",
                    feature.gml_id, (ref_id,),
                ))
    return findings


def validate_record_set(record_set, legacy_of, geometry_of, registry_findings=()) -> tuple:
    """Findings for every feature in ``record_set``, plus cross-feature and
    registry-sourced findings (P2.4 duplicate/foreign records).

    ``legacy_of(gml_id) -> LegacyFact`` and ``geometry_of(feature) ->
    GeometryFact`` are callables so the adapter can build them lazily/once
    per object rather than for the whole scene up front.
    """
    findings = []
    for feature in record_set.features:
        polygons = record_set.polygons_of(feature.gml_id)
        findings.extend(validate_feature(feature, polygons, legacy_of(feature.gml_id), geometry_of(feature)))
    findings.extend(_ring_pairing_findings(record_set))
    findings.extend(_unrecorded_host_findings(record_set, legacy_of))
    findings.extend(_referenced_object_findings(record_set, legacy_of))
    findings.extend(from_registry_findings(registry_findings))
    return tuple(findings)


# --------------------------------------------------------------------------
# P2.4 registry findings (duplicate/foreign records, duplicate/missing ids)
# --------------------------------------------------------------------------

_REGISTRY_SEVERITY = {
    "duplicate_gml_id": ERROR,
    "duplicate_record": ERROR,
    "foreign_record": ERROR,
    "missing_gml_id": WARNING,
}
_REGISTRY_CODE = {
    "duplicate_gml_id": DUPLICATE_GML_ID,
    "duplicate_record": DUPLICATE_RECORD,
    "foreign_record": FOREIGN_RECORD,
    "missing_gml_id": "missing_gml_id",
}


def from_registry_findings(registry_findings) -> tuple:
    """Translate ``domain/registry_core.py::Finding`` (P2.4) into this
    module's ``Finding`` shape, so a preflight can report both from one list."""
    return tuple(
        Finding(
            _REGISTRY_CODE.get(f.code, f.code),
            _REGISTRY_SEVERITY.get(f.code, WARNING),
            f.message,
            f.gml_id,
            tuple(f.keys),
        )
        for f in registry_findings
    )
