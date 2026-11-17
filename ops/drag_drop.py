# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# ops/drag_drop.py
"""Drag & Drop handler for CityGML files"""

import bpy


class CityGMLDropHandler(bpy.types.FileHandler):
    """Handler for drag & drop of CityGML files into Blender.

    A dropped file opens as a preview (B14, like File > Import); the browse
    operator receives the dropped path and skips the file browser.
    ``poll_drop`` gets no file path, so a dropped ``.xml`` that is not
    CityGML is rejected by the operator (namespace check on the header).
    """
    bl_idname = "CGML3_FH_citygml"
    bl_label = "CityGML File Handler"
    bl_import_operator = "cgml3.browse_gml_file"
    bl_file_extensions = ".gml;.xml"

    @classmethod
    def poll_drop(cls, context):
        """Check if drop is allowed in current context"""
        return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


def register():
    """Register drag & drop handler"""
    bpy.utils.register_class(CityGMLDropHandler)


def unregister():
    """Unregister drag & drop handler"""
    bpy.utils.unregister_class(CityGMLDropHandler)


if __name__ == "__main__":
    register()
