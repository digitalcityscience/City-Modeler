# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/browse/__init__.py
"""Browse-first building blocks (T08), pure Python, no bpy.

``gml_index``     one expat pass: per feature bbox, LoDs, byte range, risks
``index_cache``   on-disk cache of indexes (key: path, size, mtime, head/tail hash)
``fragment``      small GML made of the selected byte ranges (+ 1.0 -> 2.0 namespaces)
``proxy_geometry`` flat vertex/face arrays of the single preview mesh
``selection``     selection limit and LoD summary

Everything here may run in a worker thread; a unit test checks that none of
these modules imports bpy, bmesh, mathutils or gpu.
"""
