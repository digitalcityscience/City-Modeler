# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Transient Building root (CAC-P5.3, PLAN.md P5.3).

Bridges the gap ``docs/agent/authoring-core/issues/CAC-P5.2.md`` flagged for
P5.3: ``domain/ir_adapter.py::building_ir_from_records`` needs a Building-kind
``FeatureRecord`` to anchor its walk, but no production code writes one
today - ``storage.write_record`` is never called for a Building object, only
for the Window/wall pairs ``usecases.create_feature`` commits. Until
CAC-P5.9's ``cgml.promote_building`` starts persisting a real one, this
module supplies a **transient, read-only** substitute derived fresh from
legacy Building state, for export planning/IR construction only:

    Blender Building object
            |
            v
    plain Building facts (blender/legacy_views.py::object_view, bpy-free)
            |
            v
    legacy-derived RecordSet (domain/legacy_reader.py::read_legacy_scene)
            |
            v
    transient Building root (this module)
            |
            v
    existing persisted Wall/Window records (blender/consistency_view.py)
            |
            v
    Building IR (domain/ir_adapter.py::building_ir_from_root)

The transient root is never written to any object's stored record (never
touches ``_cgml_record``/``storage.write_record``), never mints a new
``gml_id``, and never mutates Blender state - it exists only as an
in-memory ``FeatureRecord`` for one export-planning call. Pure Python only:
no bpy/bmesh/mathutils/gpu (rule R1, enforced by
``scripts/tests/unit/test_cac_domain_has_no_bpy.py``).
"""
from __future__ import annotations

from .records import validate_feature

# A Building root may be a Building or a BuildingPart (both are legitimate
# owners of boundary surfaces, PLAN.md P5.0's ``_OWNER_KINDS`` /
# ``legacy_reader.py::_OWNER_KINDS``). ``ir_adapter.building_ir_from_root``
# itself is stricter (``kind == "Building"`` only, unchanged from P5.2) -
# this module's job is only to *resolve* the root, not to decide what the
# IR builder accepts.
BUILDING_KINDS = ("Building", "BuildingPart")

PERSISTED = "persisted"
TRANSIENT = "transient"


class BuildingRootError(ValueError):
    """No transient or persisted Building root could be resolved."""


def transient_building_record(legacy_record_set, building_gml_id):
    """The transient, non-persisted Building ``FeatureRecord`` for
    ``building_gml_id``, read fresh from ``legacy_record_set``.

    ``legacy_record_set`` is a ``RecordSet`` already derived from live
    legacy Building state by ``domain/legacy_reader.py::read_legacy_scene``
    (via ``blender/legacy_views.py::read_scene`` on the real Blender side) -
    the same oracle every other feature kind's legacy facts already come
    from. A Building object satisfies ``feature_record_from_legacy``'s
    ordinary ``gml_id``/``cgml3_feature`` contract like any other object;
    nothing here is Building-specific parsing, only the identity/kind
    check that makes a bad or missing lookup fail loudly instead of
    quietly returning something not shaped like a Building root.

    Raises ``BuildingRootError`` when ``building_gml_id`` has no legacy
    feature at all, or when it resolves to a kind other than
    ``BUILDING_KINDS`` - a Building that is not IR-exportable yet, not a
    value to guess around.
    """
    feature = legacy_record_set.feature(building_gml_id)
    if feature is None:
        raise BuildingRootError(
            f"{building_gml_id!r}: no legacy object carries this gml_id; "
            "Building identity is not resolvable"
        )
    if feature.kind not in BUILDING_KINDS:
        raise BuildingRootError(
            f"{building_gml_id!r}: kind {feature.kind!r}, expected one of {BUILDING_KINDS}"
        )
    return feature


def resolve_building_root(persisted_record_set, legacy_record_set, building_gml_id):
    """``(FeatureRecord, source)`` for ``building_gml_id`` - ``source`` is
    ``PERSISTED`` or ``TRANSIENT``.

    Precedence (PLAN.md P5.3 section 6, idempotency): a persisted Building
    ``FeatureRecord`` - once CAC-P5.9's ``promote_building`` writes one into
    ``persisted_record_set`` - always wins over the transient legacy-derived
    root. No duplicate root is ever produced and no identity churn happens:
    the transient path is not even consulted when a persisted root already
    exists, so re-running this after promotion is a deterministic switch to
    the persisted root, not a merge of two candidate roots. Before
    promotion exists anywhere, ``persisted_record_set.feature(...)`` always
    misses for a Building id and this always falls through to
    ``transient_building_record``, unchanged in behavior release over
    release until P5.9 lands.

    Raises ``BuildingRootError`` when neither source resolves the Building,
    or when a persisted feature exists but is not a recognized Building
    kind (a persisted record naming a non-Building kind for this id is a
    structural problem this function refuses to paper over, not something
    it falls back to the transient root for).
    """
    persisted = persisted_record_set.feature(building_gml_id)
    if persisted is not None:
        if persisted.kind not in BUILDING_KINDS:
            raise BuildingRootError(
                f"{building_gml_id!r}: persisted kind {persisted.kind!r}, "
                f"expected one of {BUILDING_KINDS}"
            )
        return persisted, PERSISTED
    return transient_building_record(legacy_record_set, building_gml_id), TRANSIENT


def validate_building_root(building):
    """Problems (``list[str]``) with ``building`` as a Building root on its
    own terms - reuses ``domain/records.py::validate_feature`` (the same
    per-``FeatureRecord`` structural validator ``RecordSet.validate()``
    calls) rather than a second, parallel validator. Empty when valid."""
    return validate_feature(building)
