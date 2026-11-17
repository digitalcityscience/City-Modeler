"""FeatureView: the semantic facts the UI needs about one object (P1.6).

``blender/accessor.py:read_feature_view`` builds this either from a stored
``RecordSet`` (``origin="record"``) or, when no record exists or the
experimental flag is off, from the object's legacy custom properties
(``origin="legacy"``) -- the exact computation
``ui/citygml_attributes_panel.py`` used before routing existed, so a
flag-off panel stays byte-for-byte identical (PLAN.md R6).

A record that does not describe the object it sits on (none of its
features has the object's ``gml_id`` -- e.g. copied by Semantic Stamp,
AB-14, or by Shift+D) is never shown: the view falls back to legacy and
carries a ``ViewFinding`` saying why.
"""
from dataclasses import dataclass, field

ORIGINS = ("record", "legacy")

RECORD_GML_ID_MISMATCH = "record_gml_id_mismatch"


@dataclass(frozen=True)
class ViewFinding:
    code: str
    detail: str = ""


@dataclass(frozen=True)
class FeatureView:
    origin: str                        # "record" | "legacy"
    feature_type: str | None = None
    gml_id: str | None = None
    semantic_type: str | None = None
    semantic_origin: str | None = None  # deciding rung (ticket 04); legacy only
    lod: int | None = None
    attributes: dict = field(default_factory=dict)
    boundary_surfaces: tuple = ()      # tuple[dict]: {"name", "type", "gml_id"}
    findings: tuple = ()               # tuple[ViewFinding]
