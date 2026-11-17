"""Prove the isolated extension and wheel paths in a CAC child process."""
import sys
from pathlib import Path

HEADLESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HEADLESS))
import cac_harness as h  # noqa: E402


def main():
    results = h.Results(__file__)
    try:
        # Same order as many CAC tests: alias import first, add-on enable later,
        # then an operator whose body uses the add-on's relative imports.
        import city_modeler.shared.authoring_core.blender.registry  # noqa: F401
        h.load_addon()
        h.import_fixture(h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml",
                         "3.0", use_semantic_objects=True)
        data = h.addon_environment()
        stamp = data["addon_source_stamp"] or {}
        ok = (
            data["addon_module"].startswith("bl_ext.user_default.")
            and "extensions" in data["addon_source"].lower()
            and "extensions" in data["pyproj_source"].lower()
            and data["pyproj_version"] == "3.7.2"
            and data["test_alias_is_same_module"]
            and data["alias_preserves_spec"]
            and bool(stamp.get("head"))
        )
        results.check("isolated_extension_loads_declared_pyproj", ok, str(data))
        print("CAC_ENVIRONMENT=" + str(data))
    except Exception as exc:
        results.check("isolated_extension_loads_declared_pyproj", False, repr(exc))
    results.finish()


main()
