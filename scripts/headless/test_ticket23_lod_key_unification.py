"""Ticket 23 checklist item 2/3, run against real Blender.

Before this ticket, ``ui/citygml_attributes_panel.py`` read/wrote its own
``_lod`` custom property while ``shared/lod_helpers.py`` read the
``lod``/``cgml3_lod`` pair every writer/other tool actually uses - two
magic-string keys for the same "object LoD" concept, silently diverging.

This proves the panel now goes through ``lod_helpers.object_lod`` - the
single shared accessor - instead of its own ``_lod`` key: the panel's getter
and the shared helper agree, the panel's setter operator writes the shared
key, and no ``_lod`` property is ever created.

Usage (same staged-scripts-dir convention as the other semantic-hierarchy
headless tests; see docs/agent/2026-07-16-semantic-hierarchy-investigation.md):

    SP=/tmp/cgml-probe-21
    mkdir -p $SP/bscripts/addons
    ln -sfn <this-repo> $SP/bscripts/addons/city_modeler
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_ticket23_lod_key_unification.py
"""
import bpy

ADDON = "city_modeler"


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)

    import city_modeler.shared.lod_helpers as lod_helpers
    import city_modeler.ui.citygml_attributes_panel as panel

    panel.register()
    try:
        mesh = bpy.data.meshes.new("Probe")
        obj = bpy.data.objects.new("Probe", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj

        assert lod_helpers.object_lod(obj) is None
        assert panel.get_lod_level(obj) is None
        print("[PASS] no LoD set: shared accessor and panel getter both report None")

        res = bpy.ops.cgml3.set_lod(lod_level=3)
        assert res == {'FINISHED'}, f"set_lod failed: {res}"

        assert obj.get("_lod") is None, (
            "the panel's operator must no longer write the old '_lod' key"
        )
        assert obj["lod"] == 3, "the panel's operator must write the shared 'lod' key"
        print("[PASS] cgml3.set_lod writes the shared 'lod' key, not '_lod'")

        assert lod_helpers.object_lod(obj) == 3, "shared accessor must see the panel's write"
        assert panel.get_lod_level(obj) == 3, "panel getter must see its own write"
        print("[PASS] lod_helpers.object_lod() and panel.get_lod_level() agree (both 3)")

        # cgml3_lod is the shared accessor's other read key - the panel must
        # pick it up too, exactly like lod_helpers does, with no divergence.
        del obj["lod"]
        obj["cgml3_lod"] = 2
        assert lod_helpers.object_lod(obj) == 2
        assert panel.get_lod_level(obj) == 2
        print("[PASS] lod_helpers.object_lod() and panel.get_lod_level() agree on 'cgml3_lod' too (both 2)")
    finally:
        panel.unregister()

    print("\n[ALL PASS] ticket 23 LoD-key-unification checklist satisfied")


main()
