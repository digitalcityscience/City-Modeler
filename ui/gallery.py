# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/gallery.py
"""Thumbnail pickers for the sidebar (B06): roof types, later opening types.

Icons are PNGs in ui/icons (made by scripts/make_roof_icons.py). Each
gallery is a WindowManager enum drawn with ``template_icon_view``, so a
click opens a grid of thumbnails, like Bonsai's type manager.
"""

import os

import bpy
from bpy.props import EnumProperty

from ..shared import opening_types
from ..shared.roof_shapes import ROOF_TYPES

ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")
_previews = None
_items = {}  # Blender needs Python to keep dynamic enum items alive


def _icon(key):
    return _previews[key].icon_id if _previews is not None and key in _previews else 0


def has_icon(key):
    return _previews is not None and key in _previews


def roof_items(self, context):
    if "roof" not in _items:
        _items["roof"] = [(code, name, f"AdV roofType {code}", _icon(f"roof_{code}"), i)
                          for i, (code, name) in enumerate(ROOF_TYPES.items())]
    return _items["roof"]


def register():
    global _previews
    import bpy.utils.previews
    _previews = bpy.utils.previews.new()
    from ..ops.installation_authoring import BALCONY_TYPES, CHIMNEY_TYPES
    keys = ([f"roof_{code}" for code in ROOF_TYPES] + [f"balcony_{t[0]}" for t in BALCONY_TYPES]
            + [f"chimney_{t[0]}" for t in CHIMNEY_TYPES])  # B28: scripts/make_installation_icons.py
    for key in keys:
        path = os.path.join(ICON_DIR, f"{key}.png")
        if os.path.exists(path):
            _previews.load(key, path, 'IMAGE')
    _items.clear()
    bpy.types.WindowManager.cgml_roof_choice = EnumProperty(name="Roof", items=roof_items)
    bpy.types.WindowManager.cgml_window_type = EnumProperty(name="Window", items=opening_types.items("Window"))
    bpy.types.WindowManager.cgml_door_type = EnumProperty(name="Door", items=opening_types.items("Door"))


def unregister():
    global _previews
    del bpy.types.WindowManager.cgml_roof_choice
    del bpy.types.WindowManager.cgml_window_type
    del bpy.types.WindowManager.cgml_door_type
    _items.clear()
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
