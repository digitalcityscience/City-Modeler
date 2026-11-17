"""CAC-P1.5: record storage adapter (plain ID property) and the flag.

Checks the acceptance criteria on the CAC-P1.5 task card, against ADR 0009
(accepted at G1): write -> save -> reload -> read equals; the add-on
disable/enable cycle keeps the data (there is no registered PropertyGroup to
lose it in the first place -- that's the point of the plain-ID-property
choice); flag off means nothing writes records during an ordinary import;
and a corrupted record value (bad JSON, or a dict that fails
``RecordSet.validate()``) is read back as "no record", not raised.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p1_storage.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

FIXTURE = h.FIXTURES / "Building" / "Railway_Scene_LoD3_CityGML3_Building_only.gml"


def _sample_record_set():
    from city_modeler.shared.authoring_core.domain.records import (
        FeatureRecord, PolygonRecord, RecordSet, RingRecord,
    )

    ring = RingRecord(ring_id="POLY_1_0_", role="exterior",
                       coords=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)))
    polygon = PolygonRecord(polygon_id="POLY_1", feature_id="WALL_1", exterior=ring,
                             geometry_state="committed", extra={"note": "from future add-on"})
    feature = FeatureRecord(gml_id="WALL_1", kind="WallSurface", provenance="promoted",
                             polygon_ids=("POLY_1",), params={"width": 1.2})
    return RecordSet(features=(feature,), polygons=(polygon,))


def check_round_trip(R):
    from city_modeler.shared.authoring_core.blender import storage

    h.load_addon()
    import bpy

    obj = bpy.data.objects.new("cac_storage_probe", None)
    bpy.context.collection.objects.link(obj)

    R.check("no_record_before_write", storage.read_record(obj) is None
            and not storage.has_record(obj))

    original = _sample_record_set()
    storage.write_record(obj, original)
    read_back = storage.read_record(obj)
    R.check("read_after_write_equals", read_back == original,
            f"{read_back!r} != {original!r}")
    R.check("has_record_true", storage.has_record(obj))

    path = h.out_dir() / "cac_storage_probe.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    reloaded = bpy.data.objects.get("cac_storage_probe")
    R.check("object_survives_reload", reloaded is not None)
    if reloaded is not None:
        after_reload = storage.read_record(reloaded)
        R.check("read_after_reload_equals", after_reload == original,
                f"{after_reload!r} != {original!r}")

        bpy.ops.preferences.addon_disable(module=h.ADDON)
        bpy.ops.preferences.addon_enable(module=h.ADDON)
        after_cycle = storage.read_record(reloaded)
        R.check("read_after_addon_disable_enable_equals", after_cycle == original,
                f"{after_cycle!r} != {original!r}")


def check_corrupt_values_read_as_none(R):
    from city_modeler.shared.authoring_core.blender import storage

    h.load_addon()
    import bpy

    bad_json = bpy.data.objects.new("cac_storage_bad_json", None)
    bpy.context.collection.objects.link(bad_json)
    bad_json[storage.RECORD_KEY] = "{not valid json"
    R.check("invalid_json_is_no_record", storage.read_record(bad_json) is None
            and not storage.has_record(bad_json))

    bad_record = bpy.data.objects.new("cac_storage_bad_record", None)
    bpy.context.collection.objects.link(bad_record)
    # valid JSON, valid dict shape, but gml_id="" fails RecordSet.validate()
    bad_record[storage.RECORD_KEY] = (
        '{"record_version":1,"features":'
        '[{"gml_id":"","kind":"Window"}],"polygons":[]}'
    )
    R.check("invalid_record_is_no_record", storage.read_record(bad_record) is None
            and not storage.has_record(bad_record))


def check_flag_off_writes_nothing_on_import(R):
    from city_modeler.shared.authoring_core.blender import flag, storage

    os.environ.pop("CGML_AUTHORING_CORE", None)
    h.import_fixture(FIXTURE, "3.0", use_semantic_objects=True)
    import bpy

    R.check("flag_off_by_default", not flag.is_enabled())
    with_record = [o.name for o in bpy.data.objects if storage.RECORD_KEY in o.keys()]
    R.check("no_records_written_on_import", not with_record,
            f"{len(with_record)} objects carry a record: {with_record[:5]}")


def check_flag_env_override(R):
    from city_modeler.shared.authoring_core.blender import flag

    h.load_addon()
    os.environ["CGML_AUTHORING_CORE"] = "1"
    try:
        R.check("flag_on_via_env", flag.is_enabled())
    finally:
        os.environ.pop("CGML_AUTHORING_CORE", None)
    R.check("flag_off_after_env_cleared", not flag.is_enabled())


def main():
    R = h.Results(__file__)
    check_round_trip(R)
    check_corrupt_values_read_as_none(R)
    check_flag_off_writes_nothing_on_import(R)
    check_flag_env_override(R)
    R.finish()


main()
