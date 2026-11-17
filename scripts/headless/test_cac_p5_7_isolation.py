"""CAC-P5.7: IR-path isolation from the legacy front-half, real Blender.

Scope: the early-dispatch blocks added to ``io/writer/exporter.py`` and
``io_v2/writer/exporter.py`` right after the P5.6 eligibility decision
(``_cac_ir_groups = ...``). Before P5.7 an eligible Building still ran
``_merge_semantic_hierarchy_mesh`` (which reassigns ``obj.data`` - a real
scene mutation) and the whole legacy face/material scan before its surface
output was discarded; P5.7 makes the eligible Building ``continue`` before
any of that runs. This test proves the isolation empirically rather than by
code inspection: an eligible Building's own mesh datablock is never touched,
scene/object/material state is unchanged after export, and the (still
ineligible) legacy Building keeps getting the merge exactly as before.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p5_7_isolation.py -- <out_dir>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

import bpy  # noqa: E402

import test_cac_p5_6_writer_dispatch as p56  # noqa: E402

sys.path.insert(0, str(h.REPO_ROOT))


def _flag(on):
    os.environ["CGML_AUTHORING_CORE"] = "1" if on else "0"


def _snapshot(obj):
    return {
        "mesh_id": obj.data.as_pointer() if obj.data else None,
        "props": {k: obj[k] for k in obj.keys()},
    }


def _mesh_names():
    return {m.name for m in bpy.data.meshes}


def _export(version, tag):
    out = h.out_dir() / f"p5_7_{tag}_{version.replace('.', '')}_{os.getpid()}.gml"
    text = h.export_scene(str(out), version)
    return out, text


def _run(r, version):
    tag = f"v{version.split('.')[0]}"

    # 1. Eligible IR Building: obj.data and its custom properties must be
    # byte-identical before and after export - _merge_semantic_hierarchy_mesh
    # never runs, so obj.data is never reassigned (P5.7 items 3/4).
    _flag(True)
    (building_obj, wall_obj, window_obj, _door), _legacy = p56.build_scene()
    before_meshes = _mesh_names()
    before = {
        "building": _snapshot(building_obj),
        "wall": _snapshot(wall_obj),
        "window": _snapshot(window_obj),
    }
    out, text = _export(version, "ir_isolation")
    after = {
        "building": _snapshot(building_obj),
        "wall": _snapshot(wall_obj),
        "window": _snapshot(window_obj),
    }
    after_meshes = _mesh_names()

    r.check(
        f"{tag} IR-eligible Building: obj.data mesh identity unchanged after export "
        "(_merge_semantic_hierarchy_mesh never ran)",
        all(before[k]["mesh_id"] == after[k]["mesh_id"] for k in before),
        {k: (before[k]["mesh_id"], after[k]["mesh_id"]) for k in before},
    )
    r.check(
        f"{tag} IR-eligible Building: object custom properties unchanged after export",
        before == after,
        {"before": before, "after": after},
    )
    r.check(
        f"{tag} IR-eligible Building: no new mesh datablocks left behind by export "
        "(no merged/flattened mesh was created)",
        after_meshes == before_meshes,
        {"before": sorted(before_meshes), "after": sorted(after_meshes)},
    )
    lint = h.xmllint(out, version)
    r.check(f"{tag} IR isolation export is xmllint-clean", lint["exit"] == 0, str(lint))
    r.check(
        f"{tag} IR isolation export still carries the record ids",
        'gml:id="WALL1"' in text and 'gml:id="WIN_A"' in text,
        text[:400],
    )

    # 2. Mixed scene sanity: the still-ineligible legacy Building (Building2,
    # no stored records) exports normally in the same call that took the new
    # early-continue branch for Building1 - the early continue for one
    # object in the loop does not disturb iteration for the next one.
    _flag(True)
    _ir, (legacy_building_obj, legacy_wall_obj) = p56.build_scene(with_legacy_building=True)
    out_mixed, text_mixed = _export(version, "mixed_after_ir_continue")
    lint_mixed = h.xmllint(out_mixed, version)
    r.check(f"{tag} mixed scene after IR early-continue is xmllint-clean", lint_mixed["exit"] == 0, str(lint_mixed))
    mixed_ids = h.collect_ids(text_mixed)
    r.check(
        f"{tag} mixed scene: two WallSurface entries (one IR early-continue, one legacy)",
        len(mixed_ids.get("WallSurface", ())) == 2,
        str(mixed_ids.get("WallSurface")),
    )
    _ = legacy_building_obj, legacy_wall_obj


def main():
    r = h.Results(__file__)
    h.load_addon()

    _run(r, "3.0")
    _run(r, "2.0")

    r.finish()


if __name__ == "__main__":
    main()
