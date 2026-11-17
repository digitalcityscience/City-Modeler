"""T11d: golden scenes of the shared builder (regression baseline of test_t11d_builder / test_t11d_cleanup).

``scripts/headless/golden/t11d_scenes.json.gz`` holds, per scenario label, the object-level scene summary
(``t11a_import_snapshot.object_detail``). It was first made with the old importers (removed in T11d d3-C)
and refreshed from the builder when its legacy mode went (the builder had matched them, apart from the
documented differences). ``import_new`` is the builder as the tests drive it.

Refresh after an accepted builder change: run both tests with ``CGML3_GOLDEN_REFRESH=1``; they store what
the builder builds instead of comparing and write the file at the end.
"""
import gzip
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

import t11a_import_snapshot as snap  # noqa: E402
from addon_test_utils import ensure_addon_enabled  # noqa: E402

GOLDEN_PATH = os.path.join(HERE, "golden", "t11d_scenes.json.gz")


def reset(props=None):
    bpy.ops.wm.read_homefile(use_empty=True)
    ok, addon = ensure_addon_enabled()
    assert ok, addon
    bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.cgml3.xsd_validate = False
    for key, value in (props or {}).items():
        setattr(bpy.context.scene.cgml3, key, value)
    return addon


def detail():
    return snap.normalize_generated_ids(
        {"detail": {o.name: snap.object_detail(o) for o in bpy.data.objects}}, list(bpy.data.objects))["detail"]


def import_new(path, props=None, with_data=False):
    """``(notices, detail, built)`` of the builder; ``notices`` is the exception when the import is refused.
    ``with_data``: hand the file's bytes to the importer (the Browse fragment path)."""
    addon = reset(props)
    builder = importlib.import_module(addon + ".common.unified_builder")
    auto = importlib.import_module(addon + ".common.import_auto")
    built = []
    original = builder.build_feature

    def counting(payload, ctx):
        built.append(payload.gml_id)
        return original(payload, ctx)

    builder.build_feature = counting
    try:
        try:
            data = None
            if with_data:
                with open(path, "rb") as fh:
                    data = fh.read()
            notices = auto.import_citygml_auto(path, bpy.context, data=data)
        except RuntimeError as exc:        # ImportNotSupported / ImportTooLarge
            notices = exc
    finally:
        builder.build_feature = original
    return notices, detail(), built


def refreshing() -> bool:
    """``CGML3_GOLDEN_REFRESH=1``: store the builder's scenes instead of comparing them."""
    return os.environ.get("CGML3_GOLDEN_REFRESH") == "1"


def load():
    with gzip.open(GOLDEN_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def save(golden):
    os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
    with gzip.open(GOLDEN_PATH, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(golden, f, sort_keys=True, separators=(",", ":"))
    print("written", GOLDEN_PATH, os.path.getsize(GOLDEN_PATH), "bytes")
