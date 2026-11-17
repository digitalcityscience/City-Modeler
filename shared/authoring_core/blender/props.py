"""Registration anchor for authoring-core Blender types.

No PropertyGroup is registered for the record itself: ADR 0009 stores it as
a plain ID property (``storage.py``), not a typed PropertyGroup -- the
benchmark that decision was based on ruled that option out (5x the file
size, ~4x the undo cost of the chosen option). This module exists so the
package layout (PLAN.md §2.2) has one place to register a future *typed
mirror* for UI-editable feature fields (ADR 0009, point 2: "Typed
PropertyGroups may be added later only for UI-editable feature fields,
mirroring the JSON, never replacing it"). It currently registers nothing.
"""


def register():
    pass


def unregister():
    pass
