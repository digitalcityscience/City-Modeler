# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/theme_view.py
"""B23: colour the viewport by an attribute (function, year of construction,
missing attributes). Viewport only: ``object.color`` + Solid shading "Object"
colour; materials and CityGML data are not touched. Off restores the colours
and the shading the views had before (kept in memory for this session).
"""

import colorsys
import zlib

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, Panel

from ..shared import codelists
from . import attribute_card as card

MODES = (('FUNCTION', "Function", "One colour per function code"),
         ('YEAR', "Year built", "Old (blue) to new (red); grey = unknown"),
         ('MISSING', "Missing attributes", "Red: function, height or year missing; green: complete"),
         ('OFF', "Off", "Back to the colours from before"))
GREY = (0.55, 0.55, 0.55, 1.0)
_saved = {"colors": {}, "shading": {}, "mode": 'OFF'}
_legend = []  # (colour, text) of the last run


def features(scene):
    """``[(object, its card target)]`` for every mesh under a Building / Transportation / CityFurniture."""
    out = []
    for o in scene.objects:
        if o.type == 'MESH':
            t = card.target(o)
            if t is not None:
                out.append((o, t))
    return out


def function_of(t):
    return str(t.get(f"{card.kind(t)}:function") or "")


def colour_for_code(code):
    """Stable, well spread hue per code (same code, same colour in every session)."""
    hue = (zlib.crc32(code.encode("utf-8")) % 360) / 360.0
    return (*colorsys.hsv_to_rgb(hue, 0.65, 0.9), 1.0)


def colour_for_year(year, lo, hi):
    if not year:
        return GREY
    f = 0.0 if hi <= lo else (year - lo) / (hi - lo)
    return (*colorsys.hsv_to_rgb(0.66 * (1.0 - f), 0.75, 0.9), 1.0)


def missing(t):
    names = []
    if not function_of(t):
        names.append("function")
    if card.kind(t) == "bldg":
        if not card.get_height(t):
            names.append("height")
        if not card.get_year(t):
            names.append("year")
    return names


def colours(scene, mode):
    """``({object name: rgba}, legend)`` for ``mode``."""
    pairs = features(scene)
    out, legend = {}, []
    if mode == 'FUNCTION':
        seen = {}
        for o, t in pairs:
            code = function_of(t)
            out[o.name] = colour_for_code(code) if code else GREY
            if code not in seen:
                label = codelists.codes(card.list_of(t), f"{card.kind(t)}:function").get(code, "") if code else ""
                seen[code] = (out[o.name], f"{code} {label}".strip() if code else "not set")
        legend = sorted(seen.values(), key=lambda x: x[1])
    elif mode == 'YEAR':
        years = [card.get_year(t) for _o, t in pairs if card.kind(t) == "bldg" and card.get_year(t)]
        lo, hi = (min(years), max(years)) if years else (0, 0)
        for o, t in pairs:
            out[o.name] = colour_for_year(card.get_year(t) if card.kind(t) == "bldg" else 0, lo, hi)
        legend = [(colour_for_year(lo, lo, hi), str(lo)), (colour_for_year(hi, lo, hi), str(hi)),
                  (GREY, "unknown")] if years else [(GREY, "no years in the scene")]
    elif mode == 'MISSING':
        for o, t in pairs:
            out[o.name] = (0.9, 0.2, 0.2, 1.0) if missing(t) else (0.25, 0.75, 0.3, 1.0)
        legend = [((0.9, 0.2, 0.2, 1.0), "function, height or year missing"), ((0.25, 0.75, 0.3, 1.0), "complete")]
    return out, legend


def _views(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        yield space


def apply(context, mode):
    """Colour the scene by ``mode``; 'OFF' restores what was there before the first colouring."""
    global _legend
    scene = context.scene
    if mode == 'OFF':
        for name, rgba in _saved["colors"].items():
            o = scene.objects.get(name)
            if o is not None:
                o.color = rgba
        for space in _views(context):
            prev = _saved["shading"].get(space.as_pointer())
            if prev is not None:
                space.shading.color_type = prev
        _saved.update(colors={}, shading={}, mode='OFF')
        _legend = []
        return 0
    new, _legend = colours(scene, mode)
    for name in new:
        if name not in _saved["colors"]:
            _saved["colors"][name] = tuple(scene.objects[name].color)
    for space in _views(context):
        _saved["shading"].setdefault(space.as_pointer(), space.shading.color_type)
        space.shading.color_type = 'OBJECT'
    for name, rgba in new.items():
        scene.objects[name].color = rgba
    _saved["mode"] = mode
    return len(new)


class CGML_OT_theme_view(Operator):
    """Colour the viewport by an attribute (viewport only; data and materials stay)"""
    bl_idname = "cgml.theme_view"
    bl_label = "Colour by Attribute"
    bl_options = {'REGISTER'}  # viewport colours only: no undo step for the data

    mode: EnumProperty(name="Colour by", items=MODES)

    def execute(self, context):
        n = apply(context, self.mode)
        if self.mode != 'OFF':
            self.report({'INFO'}, f"Coloured {n} object(s) by {self.mode.lower()} (viewport only)")
        return {'FINISHED'}


class CGML3_PT_ThemeView(Panel):
    bl_label = "Colour by Attribute"
    bl_idname = "CGML3_PT_ThemeView"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Edit"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        for ident, label, _d in MODES:
            row.operator("cgml.theme_view", text=label, depress=_saved["mode"] == ident and ident != 'OFF').mode = ident
        if _legend:
            col = layout.column(align=True)
            for _rgba, text in _legend[:12]:
                col.label(text=text, icon='SHADING_SOLID')
            if len(_legend) > 12:
                col.label(text=f"... {len(_legend) - 12} more")
        layout.label(text="Viewport only: data and materials stay", icon='INFO')


classes = (CGML_OT_theme_view, CGML3_PT_ThemeView)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
