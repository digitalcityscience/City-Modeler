"""FeatureView routing (CAC-P1.6): record first, legacy fallback. No Blender.

``accessor.read_feature_view`` is duck-typed on ``obj`` (``.get``, ``.type``,
``.material_slots``), so it is exercised here with a fake object instead of
a real ``bpy`` one, mirroring how ``domain/legacy_reader.py`` is tested
against hand-built ``ObjectView``/``MaterialView`` fixtures.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import accessor, storage  # noqa: E402
from shared.authoring_core.domain.records import FeatureRecord, RecordSet  # noqa: E402
from shared.authoring_core.domain.view import RECORD_GML_ID_MISMATCH  # noqa: E402


class FakeMaterial:
    def __init__(self, name, props=None):
        self.name = name
        self._props = dict(props or {})

    def get(self, key, default=None):
        return self._props.get(key, default)


class FakeSlot:
    def __init__(self, material):
        self.material = material


class FakeObject:
    def __init__(self, name="Obj", type="MESH", material_slots=(), props=None):
        self.name = name
        self.type = type
        self.material_slots = material_slots
        self._props = dict(props or {})

    def get(self, key, default=None):
        return self._props.get(key, default)

    def __getitem__(self, key):
        return self._props[key]

    def __setitem__(self, key, value):
        self._props[key] = value

    def keys(self):
        return self._props.keys()


def _wall(gml_id="W1", lod=2):
    return FakeObject(props={"gml_id": gml_id, "cgml3_feature": "WallSurface", "lod": lod})


def test_none_object_returns_none(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    assert accessor.read_feature_view(None) is None


def test_legacy_view_when_no_record(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = _wall()
    view = accessor.read_feature_view(obj)
    assert (view.origin, view.gml_id, view.feature_type, view.lod) == ("legacy", "W1", "WallSurface", 2)


def test_legacy_boundary_surfaces_from_materials(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    mat = FakeMaterial("Wall_P1", {"SurfaceTyp": "WallSurface", "gml_polygon_id": "P1"})
    obj = FakeObject(props={"gml_id": "W1", "cgml3_feature": "WallSurface"},
                      material_slots=(FakeSlot(mat),))
    view = accessor.read_feature_view(obj)
    assert view.boundary_surfaces == ({"name": "Wall_P1", "type": "WallSurface", "gml_id": "P1"},)


def test_record_wins_over_disagreeing_legacy_properties(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = _wall()
    record = FeatureRecord(gml_id="W1", kind="RoofSurface", lod=4, provenance="authored",
                            params={"function": "authored-value"})
    storage.write_record(obj, RecordSet(features=(record,)))

    view = accessor.read_feature_view(obj)

    assert view.origin == "record"
    assert view.feature_type == "RoofSurface"      # not the legacy "WallSurface"
    assert view.lod == 4                            # not the legacy 2
    assert view.attributes.get("function") == "authored-value"


def test_flag_off_falls_back_to_legacy_even_with_a_record(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: False)
    obj = _wall()
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id="W1", kind="RoofSurface"),)))

    view = accessor.read_feature_view(obj)

    assert view.origin == "legacy"
    assert view.feature_type == "WallSurface"


def test_record_boundary_surfaces_are_empty_until_the_registry_exists(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = _wall()
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id="W1", kind="WallSurface"),)))

    view = accessor.read_feature_view(obj)

    assert view.origin == "record"
    assert view.boundary_surfaces == ()


def test_foreign_record_falls_back_to_legacy_with_a_finding(monkeypatch):
    # AB-14 shape: a stamped copy (gml_id W2) carries the source's record (W1).
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = _wall(gml_id="W2")
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id="W1", kind="RoofSurface", lod=4),)))

    view = accessor.read_feature_view(obj)

    assert (view.origin, view.gml_id, view.feature_type, view.lod) == ("legacy", "W2", "WallSurface", 2)
    assert [f.code for f in view.findings] == [RECORD_GML_ID_MISMATCH]
    assert "W1" in view.findings[0].detail and "W2" in view.findings[0].detail


def test_record_without_object_gml_id_falls_back_to_legacy_with_a_finding(monkeypatch):
    # Assign Object Part shape: gml_id moved off the mesh, record left behind.
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = FakeObject(props={"cgml3_feature": "WallSurface"})
    storage.write_record(obj, RecordSet(features=(FeatureRecord(gml_id="W1", kind="Building"),)))

    view = accessor.read_feature_view(obj)

    assert (view.origin, view.gml_id, view.feature_type) == ("legacy", None, "WallSurface")
    assert [f.code for f in view.findings] == [RECORD_GML_ID_MISMATCH]


def test_matching_feature_is_chosen_among_several(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    obj = _wall(gml_id="W2")
    storage.write_record(obj, RecordSet(features=(
        FeatureRecord(gml_id="W1", kind="RoofSurface"),
        FeatureRecord(gml_id="W2", kind="WallSurface", lod=3),
    )))

    view = accessor.read_feature_view(obj)

    assert (view.origin, view.gml_id, view.lod, view.findings) == ("record", "W2", 3, ())


def test_views_carry_no_findings_when_consistent(monkeypatch):
    monkeypatch.setattr(accessor.flag, "is_enabled", lambda: True)
    assert accessor.read_feature_view(_wall()).findings == ()
