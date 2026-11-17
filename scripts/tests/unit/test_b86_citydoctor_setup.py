"""B86: CityDoctor setup - the unzipped official release (folder or start script) or a validator
command; setup text per OS."""
import os

import pytest

from shared import citydoctor


@pytest.fixture
def release(tmp_path):
    """An unzipped CityDoctorValidation-<v>-win|lin.zip as far as the add-on looks at it."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "CityDoctorValidation-3.18.3.jar").write_bytes(b"")
    (tmp_path / "start.bat").write_text("pause")
    (tmp_path / "start.sh").write_text("#!/bin/sh")
    return tmp_path


def test_release_folder_and_start_scripts_are_recognised(release):
    for cand in (release, release / "start.bat", release / "start.sh"):
        assert citydoctor.release_dir(str(cand)) == str(release)
        assert citydoctor.validator_path(str(cand)) == str(cand)
    assert citydoctor.release_dir(str(release / "app")) is None  # not the release folder itself


def test_release_command_runs_java_itself(release, tmp_path):
    gml = str(tmp_path / "in.gml")
    argv, cwd = citydoctor.command(str(release / "start.bat"), gml)
    assert cwd == str(release)
    assert argv[0] == "java"  # no runtime folder: Java from PATH
    assert argv[1:4] == ["-classpath", os.pathsep.join(("app/*", "plugin/*")), citydoctor.MAIN]
    assert argv[argv.index("-in") + 1] == gml
    assert argv[argv.index("-xmlReport") + 1] == citydoctor.report_path(gml)
    assert argv[argv.index("-config") + 1] == citydoctor.CONFIG and os.path.isfile(citydoctor.CONFIG)
    assert "-db_location" in argv


def test_bundled_runtime_and_own_config_win(release, tmp_path):
    exe = "java.exe" if os.name == "nt" else "java"
    (release / "runtime" / "bin").mkdir(parents=True)
    (release / "runtime" / "bin" / exe).write_bytes(b"")
    (release / "config.yml").write_text("x: 1")
    argv, _cwd = citydoctor.command(str(release), str(tmp_path / "in.gml"))
    assert argv[0] == str(release / "runtime" / "bin" / exe)
    assert argv[argv.index("-config") + 1] == str(release / "config.yml")
    assert os.access(argv[0], os.X_OK)  # the executable bit an unzip dropped is set again


def test_validator_command_is_called_as_before(tmp_path):
    wrapper = tmp_path / "validate.cmd"
    wrapper.write_text("@echo off")
    assert citydoctor.release_dir(str(wrapper)) is None
    assert citydoctor.validator_path(str(wrapper)) == str(wrapper)
    assert citydoctor.command(str(wrapper), "a.gml") == ([str(wrapper), "a.gml"], None)
    assert citydoctor.validator_path(str(tmp_path / "missing.cmd")) is None
    assert citydoctor.validator_path(str(tmp_path)) is None  # a folder that is no release


def test_setup_text_per_os():
    win, lin, mac = (citydoctor.setup_lines(k) for k in ("nt", "posix", "darwin"))
    assert "-win.zip" in win[0] and "C:\\CityDoctor\\start.bat" in win[3]
    assert "-lin.zip" in lin[0] and "~/CityDoctor/start.sh" in lin[3]
    assert "-mac.zip" in mac[0] and "~/CityDoctor/start.sh" in mac[3] and "xattr" in mac[-1]
    assert citydoctor.path_hint("nt") == "C:\\CityDoctor\\start.bat"
