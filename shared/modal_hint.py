# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/modal_hint.py
"""B33: one status-bar format for every authoring modal, Blender style:
``LMB Place · Drag Size · Esc/RMB Cancel``. ``hint`` is pure (unit-testable)."""

import functools

SEP = " · "


def hint(*items) -> str:
    """``(key, action)`` pairs and plain strings (state such as "Height 9.0 m") joined with ' · '."""
    return SEP.join(f"{i[0]} {i[1]}" if isinstance(i, tuple) else str(i) for i in items if i)


def status(context, text=None):
    """Set the status-bar hint; no text clears it."""
    ws = getattr(context, "workspace", None)
    if ws is not None:
        ws.status_text_set(text)


def snap_note(context) -> str:
    """'Snap Vertex, Edge' when Blender's snap toggle is on, else '' (the header magnet decides)."""
    ts = getattr(getattr(context, "scene", None), "tool_settings", None)
    if ts is None or not getattr(ts, "use_snap", False):
        return ""
    els = sorted(str(e).replace("_", " ").title() for e in getattr(ts, "snap_elements", ()) or ())
    return "Snap " + ", ".join(els) if els else ""


def clears_status(fn):
    """Decorator for invoke / modal: an exception still clears the status bar.
    (Blender checks the argument count, so the wrapper spells out ``event``.)"""
    @functools.wraps(fn)
    def wrapper(self, context, event):
        try:
            return fn(self, context, event)
        except Exception:
            status(context)
            raise
    return wrapper
