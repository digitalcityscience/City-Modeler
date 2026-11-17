import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("wheel_repair_under_test", ROOT / "common" / "wheel_repair.py")
wheel_repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wheel_repair)

WIN = dict(py=(3, 13), system="Windows", machine="AMD64")


def make_wheel(path: Path, files: dict) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_compatible_picks_only_this_python_and_os():
    ok = wheel_repair.compatible
    assert ok("pyproj-3.7.2-cp313-cp313-win_amd64.whl", **WIN)
    assert ok("keyring-25.7.0-py3-none-any.whl", **WIN)
    assert ok("tzdata-2026.5-py2.py3-none-any.whl", **WIN)
    assert not ok("pyproj-3.7.2-cp311-cp311-win_amd64.whl", **WIN)
    assert not ok("pyproj-3.7.2-cp313-cp313-manylinux_2_28_x86_64.whl", **WIN)
    assert ok("lxml-6.0.2-cp313-cp313-macosx_10_13_universal2.whl", py=(3, 13), system="Darwin", machine="arm64")
    assert ok("pyproj-3.7.2-cp313-cp313-macosx_14_0_arm64.whl", py=(3, 13), system="Darwin", machine="arm64")
    assert not ok("pyproj-3.7.2-cp313-cp313-macosx_13_0_x86_64.whl", py=(3, 13), system="Darwin", machine="arm64")
    assert ok("lxml-6.0.2-cp313-cp313-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl",
              py=(3, 13), system="Linux", machine="x86_64")


def test_repair_restores_half_deleted_wheel_and_keeps_existing_files(tmp_path):
    wheels, site = tmp_path / "wheels", tmp_path / "site"
    wheels.mkdir()
    site.mkdir()
    make_wheel(wheels / "pyproj-3.7.2-cp313-cp313-win_amd64.whl", {
        "pyproj/__init__.py": "fresh",
        "pyproj/_core.pyd": "bin",
        "pyproj.libs/proj.dll": "dll",
        "pyproj-3.7.2.dist-info/RECORD": "",
    })
    make_wheel(wheels / "pyproj-3.7.2-cp311-cp311-win_amd64.whl", {"pyproj/__init__.py": "wrong python"})
    # The broken state seen on 2026-11-04: dist-info and the locked .libs remain, the package is gone.
    (site / "pyproj-3.7.2.dist-info").mkdir()
    (site / "pyproj.libs").mkdir()
    (site / "pyproj.libs" / "proj.dll").write_text("locked original")

    assert wheel_repair.repair(wheels, site, **WIN) == ["pyproj-3.7.2-cp313-cp313-win_amd64.whl"]
    assert (site / "pyproj" / "__init__.py").read_text() == "fresh"
    assert (site / "pyproj" / "_core.pyd").exists()
    assert (site / "pyproj.libs" / "proj.dll").read_text() == "locked original"  # never overwritten
    assert (site / "pyproj-3.7.2.dist-info" / "RECORD").exists()


def test_repair_is_a_no_op_when_everything_is_installed(tmp_path):
    wheels, site = tmp_path / "wheels", tmp_path / "site"
    wheels.mkdir()
    (site / "keyring").mkdir(parents=True)
    (site / "keyring" / "__init__.py").write_text("installed")
    make_wheel(wheels / "keyring-25.7.0-py3-none-any.whl", {"keyring/__init__.py": "x"})
    assert wheel_repair.repair(wheels, site, **WIN) == []
    assert (site / "keyring" / "__init__.py").read_text() == "installed"


def test_repair_fills_a_folder_that_kept_only_its_locked_files(tmp_path):
    wheels, site = tmp_path / "wheels", tmp_path / "site"
    wheels.mkdir()
    (site / "lxml").mkdir(parents=True)
    (site / "lxml" / "etree.pyd").write_text("locked")
    make_wheel(wheels / "lxml-6.0.2-cp313-cp313-win_amd64.whl",
               {"lxml/__init__.py": "init", "lxml/etree.pyd": "new"})
    assert wheel_repair.repair(wheels, site, **WIN) == ["lxml-6.0.2-cp313-cp313-win_amd64.whl"]
    assert (site / "lxml" / "__init__.py").read_text() == "init"
    assert (site / "lxml" / "etree.pyd").read_text() == "locked"
