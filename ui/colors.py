# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/colors.py
"""One colour rule for every sidebar button (B15).

Blender buttons have only two colours besides grey: highlighted (blue, the
``depress`` state) and alert (red). The add-on uses them by function:

- blue: adds or shows something (open, show, add window / roof / balcony, save)
- red: removes something (unshow, close, delete, unload)
- grey: selection, view and checks
"""

LEGEND = "Blue: add · Red: remove · Grey: view"


def add(layout, idname, **kw):
    """A blue button."""
    return layout.operator(idname, depress=True, **kw)


def remove_row(layout):
    """A row whose buttons are red."""
    row = layout.row(align=True)
    row.alert = True
    return row
