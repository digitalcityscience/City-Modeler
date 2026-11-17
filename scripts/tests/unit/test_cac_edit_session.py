"""Explicit edit/commit lifecycle - pure parts only (CAC-P4.2).

The Blender-integrated begin/commit/cancel flow needs a real ``bpy`` object
(registry resolution, mesh vertex writes) and is exercised by
``scripts/headless/test_cac_p4_edit_commit.py``. This file covers only the
parts of ``shared/authoring_core/blender/edit_session.py`` that do not touch
Blender: the matrix flatten/restore round trip and the pure checksum
no-op/changed decision ``commit()`` acts on.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.authoring_core.blender import edit_session as es  # noqa: E402

IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class _FakeMatrix(list):
    """Iterates as four row sequences, like a ``mathutils.Matrix``."""

    def __init__(self, flat):
        super().__init__(tuple(flat[i:i + 4]) for i in range(0, 16, 4))


def test_flatten_matrix_round_trips_identity():
    flat = es._flatten_matrix(_FakeMatrix(IDENTITY))
    assert flat == IDENTITY


def test_flatten_matrix_round_trips_translation():
    translated = (
        1.0, 0.0, 0.0, 3.0,
        0.0, 1.0, 0.0, -2.0,
        0.0, 0.0, 1.0, 0.5,
        0.0, 0.0, 0.0, 1.0,
    )
    flat = es._flatten_matrix(_FakeMatrix(translated))
    assert flat == translated


def test_matrix_from_flat_row_layout():
    """``_matrix_from_flat`` needs a real ``mathutils.Matrix`` (Blender-only,
    exercised by the headless test); this only checks the pure row split it
    hands to it."""
    rows = tuple(IDENTITY[i * 4:(i + 1) * 4] for i in range(4))
    assert rows == (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def test_decide_no_op_when_checksums_equal():
    assert es._decide("abc", "abc") == es.NO_OP


def test_decide_changed_when_checksums_differ():
    assert es._decide("abc", "def") == es.CHANGED


def test_active_session_none_when_absent():
    assert es.active_session("NO-SUCH-GML-ID") is None


def test_edit_session_dataclass_defaults():
    session = es.EditSession(
        gml_id="WIN1", window_name="Window", wall_gml_id="WALL1",
        wall_name="Wall", checksum="deadbeef",
    )
    assert session.window_mesh_state == ((), ())
    assert session.wall_mesh_state == ((), ())


def test_commit_result_fields():
    result = es.CommitResult(status="no_op", message="nothing changed", gml_id="WIN1")
    assert result.status == "no_op"
    assert result.gml_id == "WIN1"


def test_commit_without_begin_raises():
    try:
        es.commit("NO-SUCH-SESSION")
    except es.EditSessionError as exc:
        assert "no open edit session" in str(exc)
    else:
        raise AssertionError("expected EditSessionError")


def test_cancel_without_begin_raises():
    try:
        es.cancel("NO-SUCH-SESSION")
    except es.EditSessionError as exc:
        assert "no open edit session" in str(exc)
    else:
        raise AssertionError("expected EditSessionError")
