# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""The scene georeference (CRS + origin), kept only in ``bpy.data.worlds["World"]``.

B32: our own key names (CityJSONEditor/Up3date also write World["CRS"]); every reader and writer
uses the constants below. Old files are migrated on load (``migrate_legacy_keys``).

B31: once CityGML features sit in the scene relative to the World origin, a source in another CRS
must not re-base the World (the features would then export at wrong coordinates).
"""

from __future__ import annotations

import re

try:
    import bpy
except ImportError:  # the exporters' CRS helpers are also imported without Blender
    bpy = None

from .load_limits import ImportNotSupported

WORLD = "World"
CRS = "cgml_CRS"
X_ORIGIN = "cgml_X-Origin"
Y_ORIGIN = "cgml_Y-Origin"
Z_ORIGIN = "cgml_Z-Origin"
Z_EPSG = "cgml_Z-EPSG"
# names before B32 -> ours
LEGACY_KEYS = {"CRS": CRS, "X-Origin": X_ORIGIN, "Y-Origin": Y_ORIGIN, "Z-Origin": Z_ORIGIN, "Z-EPSG": Z_EPSG}


def _has_keys(w) -> bool:
    return w is not None and (CRS in w or X_ORIGIN in w or "CRS" in w or "X-Origin" in w)


def world():
    """The georeference World, or None (readers: never create it).

    B42: found by our keys, not by name. A renamed "World" (or a file whose keys sit on
    another world) still georeferences; a "World" without keys is the fallback so the
    legacy migration and ``georef_world`` keep working.
    """
    named = bpy.data.worlds.get(WORLD)
    if _has_keys(named):
        return named
    try:
        current = bpy.context.scene.world
    except Exception:
        current = None
    if _has_keys(current):
        return current
    return next((w for w in bpy.data.worlds if _has_keys(w)), named)


def georef_world(scene=None):
    """The georeference World, created if missing; a world-less ``scene`` gets it.

    The scene's own world (e.g. "World.001") is never replaced: readers look only here.
    """
    w = world() or bpy.data.worlds.new(WORLD)
    if scene is not None and scene.world is None:
        scene.world = w
    return w


def set_world_float(w, key: str, value: float) -> None:
    w[key] = float(value)
    try:
        w.id_properties_ui(key).update(min=-1e12, max=1e12, default=0.0, precision=8, description=key)
    except Exception:
        pass


def migrate_legacy_keys() -> bool:
    """Rename pre-B32 keys in World when they are ours; True when something moved.

    Ours = the hyphen origin keys (CityJSONEditor writes ``X_Origin``) or CityGML features in the file;
    a World holding only another add-on's ``CRS`` is left alone.
    """
    w = world()
    if w is None or not any(k in w for k in LEGACY_KEYS):
        return False
    ours = "X-Origin" in w or "Y-Origin" in w or any(o.get("cgml3_feature") for o in bpy.data.objects)
    if not ours:
        return False
    for old, new in LEGACY_KEYS.items():
        if old in w:
            if new not in w:
                w[new] = w[old]
            del w[old]
    return True


if bpy is not None:
    @bpy.app.handlers.persistent
    def _on_load_post(*_args):
        try:
            if migrate_legacy_keys():
                print("[CityGML] World georeference keys renamed to cgml_* (B32)")
        except Exception as exc:
            print(f"[CityGML] World key migration failed: {exc}")


def register() -> None:
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    # bpy.data is restricted while the add-on registers: migrate the open file once it is not
    bpy.app.timers.register(lambda: _on_load_post() and None, first_interval=0.0)


def unregister() -> None:
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)


class CrsMismatch(ImportNotSupported):
    """The source CRS differs from the scene's; nothing was imported."""


def epsg(crs) -> str:
    """``EPSG:<h>[+<v>]`` or ``Unknown CRS``; a value already in that form is kept
    (normalize_crs_to_epsg would keep only its vertical code)."""
    s = str(crs or "").strip()
    if re.fullmatch(r"EPSG:\d+(\+\d+)?", s, re.IGNORECASE):
        return s.upper()
    from .materials_common import normalize_crs_to_epsg
    return normalize_crs_to_epsg(s) if s else "Unknown CRS"


def same_crs(a, b) -> bool:
    """Same horizontal EPSG (X/Y of one frame); the vertical part may be named or not."""
    ha, hb = epsg(a).split("+")[0], epsg(b).split("+")[0]
    return ha != "Unknown CRS" and ha == hb


def scene_crs_lock() -> str | None:
    """EPSG the scene is bound to, or None while re-basing is harmless.

    Bound = World has a known CRS and an X/Y origin and some object carries ``cgml3_feature``.
    """
    w = world()
    if w is None or X_ORIGIN not in w or Y_ORIGIN not in w:
        return None
    crs = epsg(w.get(CRS))
    if crs == "Unknown CRS":
        return None
    if not any(o.users and o.get("cgml3_feature") for o in bpy.data.objects):
        return None
    return crs


def crs_conflict(source_crs: str, lock: str | None) -> str | None:
    """The error text when a source in ``source_crs`` would re-base a scene bound to ``lock``, else None.

    shortcut: a source without a known CRS is let through (old behaviour); refuse it too if that bites.
    """
    src = epsg(source_crs)
    if lock is None or src == "Unknown CRS" or same_crs(src, lock):
        return None
    return (f"The source is in {src} but the scene is in {lock}; nothing was imported "
            f"(importing it would move the features already in the scene). "
            f"Open it in a new Blender file (File > New; the CRS is kept per file), or export it from 3DCityDB in {lock}.")


def check_crs(source_crs: str, lock: str | None = None) -> None:
    """Raise ``CrsMismatch`` for a source that would re-base a bound scene.

    ``lock``: ``scene_crs_lock()`` taken earlier (a worker thread must not read bpy data).
    """
    msg = crs_conflict(source_crs, scene_crs_lock() if lock is None else lock)
    if msg:
        raise CrsMismatch(msg)
