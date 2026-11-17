"""Single reader for the authoring-core experimental flag (PLAN.md §4.8).

No other code reads ``experimental_authoring_core`` or ``CGML_AUTHORING_CORE``
directly.
"""
import os


def _addon_prefs():
    import bpy

    module_name = (__package__ or __name__).split(".")[0]
    for key in (module_name, "CityGML-3_Importer-Exporter"):
        entry = bpy.context.preferences.addons.get(key)
        if entry is not None and hasattr(entry, "preferences"):
            return entry.preferences
    return None


def is_enabled():
    """True when the experimental record-backed authoring path is on.

    ``CGML_AUTHORING_CORE=1`` (headless runs) is checked first, then the
    add-on preference ``experimental_authoring_core`` (default off).
    """
    if os.environ.get("CGML_AUTHORING_CORE") == "1":
        return True
    prefs = _addon_prefs()
    return bool(prefs is not None and getattr(prefs, "experimental_authoring_core", False))


def gs_surface_dispatch_enabled():
    """True when the per-surface IR export path is on (PLAN.md GS.3.2,
    generalized to N Windows by P3b.2, flag disposition decided by P3b.6 -
    docs/agent/authoring-core/phase3b-design-note.md §4/§5, STATUS.md
    P3b.6).

    Flag contract (P3b.6, narrow by design - do not read this as "all
    record-backed features use per-surface IR"):

    - Default OFF, unchanged by P3b.6. Not promoted to default-on.
    - Scope: WallSurface + N record-backed Window(s) only (the frozen
      P3b slice, phase3b-design-note.md §6). Depth-authored Windows, Door,
      RoofSurface, Balcony and BuildingInstallation are out of scope and
      always stay legacy regardless of this flag.
    - Building-level D-6 (``export_eligibility.py::can_export_building_
      from_ir``) always wins: this flag is only ever consulted by a writer
      for a Building D-6 has already found NOT IR-eligible. An eligible
      Building never reaches this flag's check.
    - Any surface this flag's dispatch does not itself report eligible
      (``gs_surface_eligibility.py::can_export_surface_from_ir``, plus the
      writer-adapter's own degenerate-geometry safety net) stays on the
      ordinary legacy export path unaffected by this flag being on.
    - Status: supported opt-in for the frozen slice above (P3b.6
      disposition), not merely experimental scaffolding - kept behind a
      flag pending broader real-fixture evidence (phase3b-design-note.md
      §14 item 6), not because the mechanism itself is unproven.

    Independent of ``is_enabled()`` above - a caller must check both where
    relevant, never reuse ``provenance`` as a stand-in for either flag
    (GS.3.2 task item 1).

    ``CGML_GS_SURFACE_DISPATCH=1`` (headless runs) is checked first, then
    the add-on preference ``experimental_gs_surface_dispatch`` (default
    off). The preference/env-var name is unchanged by P3b.6 (no rename -
    phase3b-design-note.md §5 leaves a rename as a possible later follow-
    up once real-fixture evidence exists, not decided here).
    """
    if os.environ.get("CGML_GS_SURFACE_DISPATCH") == "1":
        return True
    prefs = _addon_prefs()
    return bool(prefs is not None and getattr(prefs, "experimental_gs_surface_dispatch", False))
