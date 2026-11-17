import importlib.util
import os

import bpy


DEFAULT_ADDON_MODULES = (
    # How Blender names the add-on when installed as an extension, which is how it is
    # shipped and how its lxml/pyproj wheels get loaded.
    "bl_ext.user_default.city_modeler",
    "city_modeler",
    "blender_city_modeler",
)


def addon_module_candidates() -> tuple[str, ...]:
    override = os.environ.get("ADDON_MODULE", "").strip()
    if override:
        return (override,)
    return DEFAULT_ADDON_MODULES


def footprint(width=10.0, depth=8.0, centre=(0.0, 0.0, 0.0), angle=0.0) -> str:
    """B34: ``points`` for cgml.building_new: the rectangle the old script default built
    (``width`` along the X axis turned by ``angle``, around ``centre``), as JSON."""
    import json
    import math
    cx, cy, cz = centre
    c, s = math.cos(angle), math.sin(angle)
    corners = ((-width / 2, -depth / 2), (width / 2, -depth / 2), (width / 2, depth / 2), (-width / 2, depth / 2))
    return json.dumps([[cx + x * c - y * s, cy + x * s + y * c, cz] for x, y in corners])


def ensure_addon_enabled() -> tuple[bool, str]:
    for candidate in addon_module_candidates():
        if candidate in bpy.context.preferences.addons:
            return True, candidate

        if importlib.util.find_spec(candidate) is None:
            continue

        try:
            bpy.ops.preferences.addon_enable(module=candidate)
        except Exception as exc:
            return False, f"addon_enable failed for {candidate}: {exc}"

        if candidate in bpy.context.preferences.addons:
            return True, candidate

    return False, "No known add-on module is installed or importable"
