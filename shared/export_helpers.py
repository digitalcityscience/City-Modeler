# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
Shared export helper functions used by both File-Export and DB-Export operators.
Eliminates duplication between CGML3_OT_ExportGMLFile and CGML3_OT_ExportToDB.
"""
import datetime
import bpy


def log_text_to_blender(name_prefix: str, content: str):
    """Write log content to a Blender text block."""
    name = f"{name_prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    tb.clear()
    tb.write(content[:2_000_000])


def is_export_helper(obj) -> bool:
    """True for add-on helper objects that are never CityGML features
    (the Browse first index preview, see ops/browse_gml.py)."""
    try:
        return bool(obj.get("cgml3_browse_path"))
    except Exception:
        return False


def object_is_viewport_visible(obj, ctx=None) -> bool:
    """Return False for objects hidden in the Outliner/current viewport."""
    if obj is None:
        return False

    view_layer = None
    try:
        view_layer = getattr(ctx, "view_layer", None)
    except Exception:
        view_layer = None
    if view_layer is None:
        try:
            view_layer = bpy.context.view_layer
        except Exception:
            view_layer = None

    try:
        if bool(getattr(obj, "hide_viewport", False)):
            return False
    except Exception:
        pass

    try:
        if view_layer is not None:
            if obj.hide_get(view_layer=view_layer):
                return False
        elif obj.hide_get():
            return False
    except TypeError:
        try:
            if obj.hide_get():
                return False
        except Exception:
            pass
    except Exception:
        pass

    try:
        if view_layer is not None:
            return bool(obj.visible_get(view_layer=view_layer))
        return bool(obj.visible_get())
    except TypeError:
        try:
            return bool(obj.visible_get())
        except Exception:
            return True
    except Exception:
        return True
