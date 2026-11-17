"""T10: write back - citydb-tool options, log parsing (real 1.3.0 logs), plan check,
confirmation text, eligibility and the check of the temporary export."""
import importlib
import sys
import types
from pathlib import Path

import pytest

from citydb import update
from citydb.catalog import CatalogRow, InvalidId

ROOT = Path(__file__).resolve().parents[3]

# citydb-tool 1.3.0 output from the local trial (2026-10-24), shortened
PREVIEW_TERMINATE_2 = """\
[09:45:37 INFO] Import is running in preview mode. Features will not be imported.
[09:45:37 INFO] [1|1] Importing file /data/t10_probe/b1_roof_plus2.gml.
[09:45:37 INFO] Deleting 2 duplicates from database in preview mode...
[09:45:37 INFO] Successfully deleted duplicates. Importing features...
[09:45:37 INFO] Preview of features to be imported:
[09:45:37 INFO] bldg:Building: 1
[09:45:37 INFO] con:GroundSurface: 1
[09:45:37 INFO] con:RoofSurface: 1
[09:45:37 INFO] con:WallSurface: 9
[09:45:37 INFO] Total execution time: 01 s.
[09:45:37 INFO] citydb successfully completed.
"""

REAL_TERMINATE_1 = """\
[09:44:39 INFO] [1|1] Importing file /data/t10_probe/b1_roof_plus2.gml.
[09:44:39 INFO] Deleting 1 duplicates from database...
[09:44:39 INFO] Successfully deleted duplicates. Importing features...
[09:44:39 INFO] Import summary:
[09:44:39 INFO] bldg:Building: 1
[09:44:39 INFO] con:GroundSurface: 1
[09:44:39 INFO] con:RoofSurface: 1
[09:44:39 INFO] con:WallSurface: 9
[09:44:39 INFO] Total execution time: 01 s.
"""

SKIP_LOG = """\
[09:44:31 INFO] [1|1] Importing file /data/t10_probe/b1_roof_plus2.gml.
[09:44:31 INFO] All features to be imported are duplicates. Skipping input file.
[09:44:31 INFO] No features imported.
"""


def row(fid, objectid="DEHHALKAJ0000yAZ", lods=("lod2Solid",)):
    return CatalogRow(id=fid, objectid=objectid, classname="Building", lods=lods)


# --------------------------------------------------------------------------- options
@pytest.mark.parametrize("mode, tool", [("TERMINATE", "terminate"), ("DELETE", "delete"), ("SKIP", "skip")])
def test_import_options_always_recompute_extent_and_start_now(mode, tool):
    for preview in (True, False):
        o = update.import_options(mode, preview=preview)
        assert o["import_mode"] == tool
        assert o["compute_extent"] is True
        assert o["creation_date"] == "now"
        assert o["preview"] is preview
        assert o["reason_for_update"] == update.DEFAULT_REASON


def test_import_options_reject_unknown_mode():
    with pytest.raises(ValueError):
        update.import_options("import_all", preview=True)


# --------------------------------------------------------------------------- log
def test_parse_preview_counts_duplicates_and_types():
    rep = update.parse_import_log(PREVIEW_TERMINATE_2)
    assert rep.duplicates == 2 and rep.preview and not rep.skipped_file
    assert rep.counts == {"bldg:Building": 1, "con:GroundSurface": 1, "con:RoofSurface": 1, "con:WallSurface": 9}
    assert rep.imported == 12
    assert rep.counts_text() == "WallSurface 9, Building 1, GroundSurface 1, RoofSurface 1"
    assert rep.problems == []


def test_parse_real_run_and_skip():
    rep = update.parse_import_log(REAL_TERMINATE_1)
    assert rep.duplicates == 1 and not rep.preview and rep.counts["bldg:Building"] == 1
    rep = update.parse_import_log(SKIP_LOG)
    assert rep.skipped_file and rep.duplicates == 0 and rep.counts == {}
    assert rep.counts_text() == "nothing"


def test_parse_collects_errors():
    rep = update.parse_import_log("[10:00:00 ERROR] Failed to import feature X.\n[10:00:00 WARN] odd")
    assert rep.problems == ["Failed to import feature X.", "odd"]


# --------------------------------------------------------------------------- plan
def test_plan_ok_when_confirmed_rows_match():
    rep = update.parse_import_log(PREVIEW_TERMINATE_2)
    assert update.plan_problem("TERMINATE", [43, 13], [row(13), row(43)], rep) is None


def test_plan_refuses_without_confirmation():
    rep = update.parse_import_log(PREVIEW_TERMINATE_2)
    assert "confirm" in update.plan_problem("DELETE", [], [row(13), row(43)], rep)


def test_plan_refuses_when_db_changed():
    rep = update.parse_import_log(PREVIEW_TERMINATE_2)
    msg = update.plan_problem("TERMINATE", [13], [row(13), row(43)], rep)
    assert "database changed" in msg and "id 13" in msg


def test_plan_refuses_when_preview_disagrees():
    rep = update.parse_import_log(REAL_TERMINATE_1)  # 1 duplicate
    assert "would replace 1" in update.plan_problem("TERMINATE", [13, 43], [row(13), row(43)], rep)


def test_plan_refuses_terminated_feature():
    rep = update.parse_import_log(PREVIEW_TERMINATE_2)
    assert "no longer active" in update.plan_problem("TERMINATE", [13], [], rep)


def test_skip_needs_no_confirmation():
    rep = update.parse_import_log(SKIP_LOG)
    assert update.plan_problem("SKIP", [], [row(13)], rep) is None


def test_confirm_lines_name_feature_rows_and_target_without_password():
    lines = update.confirm_lines("TERMINATE", "DEHHALKAJ0000yAZ", [row(13)], "citydb@127.0.0.1:55433/citydb/citydb")
    assert lines[0] == "This will replace feature DEHHALKAJ0000yAZ (id 13) in citydb@127.0.0.1:55433/citydb/citydb."
    many = update.confirm_lines("DELETE", "X", [row(43, "X", ("lod3Solid",)), row(13, "X")], "w")
    text = " ".join(many)
    assert "2 active rows" in text and "LoD2 (id 13), LoD3 (id 43)" in text and "ALL of them" in text


def test_new_active_row_is_the_inserted_one():
    assert update.new_active_row([1, 79], [row(13), row(91)]).id == 91
    assert update.new_active_row([1], [row(1)]) is None


# --------------------------------------------------------------------------- eligibility
KEY = "citydb://citydb@127.0.0.1:55433/citydb/citydb"
GOOD = {"cgml3_source_kind": "citydb", "cgml3_db_feature_id": 13, "cgml3_objectid": "DEHHALKAJ0000yAZ",
        "cgml3_source_path": KEY}


def test_eligible_db_root():
    assert update.eligibility(GOOD, KEY) == (True, "")


@pytest.mark.parametrize("change, words", [
    ({"cgml3_source_kind": "file"}, "Not loaded from 3DCityDB"),
    ({"cgml3_db_feature_id": None}, "No 3DCityDB feature id"),
    ({"cgml3_objectid": "a b"}, "No objectid"),
    ({"cgml3_source_path": "citydb://other"}, "another 3DCityDB"),
])
def test_not_eligible_says_why(change, words):
    ok, reason = update.eligibility({**GOOD, **change}, KEY)
    assert not ok and words in reason


@pytest.mark.parametrize("bad", ["", "a;b", "x'y", "1 OR 1=1"])
def test_objectid_validation(bad):
    with pytest.raises(InvalidId):
        update.validate_objectid(bad)


# --------------------------------------------------------------------------- export check
HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n<CityModel xmlns="http://www.opengis.net/citygml/3.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:bldg="http://www.opengis.net/citygml/building/3.0" '
        'xmlns:gen="http://www.opengis.net/citygml/generics/3.0">\n')


def _member(gid, generic=""):
    gen = (f'<genericAttribute><gen:StringAttribute><gen:name>{generic}</gen:name>'
           f'<gen:value>v</gen:value></gen:StringAttribute></genericAttribute>') if generic else ""
    return (f'<cityObjectMember><bldg:Building gml:id="{gid}">{gen}<boundary><bldg:RoofSurface gml:id="{gid}_r"/>'
            f'</boundary></bldg:Building></cityObjectMember>\n')


def _write(tmp_path, *members):
    p = tmp_path / "x.gml"
    p.write_text(HEAD + "".join(members) + "</CityModel>\n", encoding="utf-8")
    return str(p)


def test_export_check_accepts_one_feature_with_its_objectid(tmp_path):
    p = _write(tmp_path, _member("DEHHALKAJ0000yAZ", "Gemeindeschluessel"))
    assert update.inspect_export(p) == (["DEHHALKAJ0000yAZ"], [])
    assert update.export_problem(p, "DEHHALKAJ0000yAZ") is None


def test_export_check_refuses_other_ids_counts_and_internal_props(tmp_path):
    assert "differs" in update.export_problem(_write(tmp_path, _member("ID_new")), "DEHHALKAJ0000yAZ")
    assert "2 top-level" in update.export_problem(_write(tmp_path, _member("A"), _member("B")), "A")
    msg = update.export_problem(_write(tmp_path, _member("A", "cgml3_db_feature_id")), "A")
    assert "cgml3_db_feature_id" in msg
    bad = tmp_path / "bad.gml"
    bad.write_text("<CityModel>", encoding="utf-8")
    assert "could not be read" in update.export_problem(str(bad), "A")


# --------------------------------------------------------------------------- citydb-tool command
def _load_cli():
    pkg = "cgml3_t10_cli_under_test"
    for name, path in ((pkg, ROOT), (f"{pkg}.ops", ROOT / "ops"), (f"{pkg}.common", ROOT / "common")):
        mod = types.ModuleType(name)
        mod.__path__ = [str(path)]
        sys.modules.setdefault(name, mod)
    return importlib.import_module(f"{pkg}.ops.citydb_cli")


def test_import_command_carries_preview_and_metadata_not_the_password(tmp_path):
    cli = _load_cli()
    gml = tmp_path / "in.gml"
    gml.write_text("<x/>")
    tool = cli.CityDBTool(types.SimpleNamespace(use_docker=False, docker_image="", citydb_exe="citydb"))
    calls = []
    tool._run = lambda args, cwd=None, timeout=None, env=None, **kw: calls.append((args, env, kw)) or (True, "")
    tool.import_citygml_to_db(str(gml), host="h", port="5432", db="d", user="u", password="s3cret",
                              **update.import_options("TERMINATE", preview=True))
    args, env, kw = calls[0]
    joined = " ".join(args)
    assert "--import-mode terminate" in joined and "--compute-extent" in args and "--preview" in args
    assert "--creation-date now" in joined and update.DEFAULT_REASON in args
    assert "s3cret" not in joined and env["CITYDB_PASSWORD"] == "s3cret"
    assert kw == {}  # no streaming without on_output (older fakes keep working)
    tool.import_citygml_to_db(str(gml), host="h", port="5432", db="d", user="u", password="s3cret",
                              **update.import_options("DELETE", preview=False), on_output=print)
    args, _env, kw = calls[1]
    assert "--preview" not in args and "--import-mode" in args and "on_output" in kw
