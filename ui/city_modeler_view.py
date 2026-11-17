"""City Modeler view (B75): a ready workspace so a user who opens Blender finds the tool, not Blender's
general layout. Outliner on the left (like QGIS' layer tree), the 3D view, Properties on the right.
A toggle in the top bar switches it; on by default (add-on preference).

B89: the CityGML panels are in the "Tool" category, which Blender shows both in the sidebar's Tool
tab and in the Properties editor's Tool tab (the first one, above Render). The City Modeler opens the
Properties editor on that tab and leaves the sidebar closed, so nothing covers the viewport.

Relationship lines (dashed parent lines) are switched off in the City Modeler's 3D view when the workspace is
created; the user can turn them on again in the Overlays menu."""

import bpy

NAME = "City Modeler"
_ADDON = __package__.rpartition(".")[0]
_TICK = 0.2  # seconds between layout steps; Blender resizes areas on the next redraw


def _prefs():
    addon = bpy.context.preferences.addons.get(_ADDON)
    return addon.preferences if addon else None


def enabled():
    prefs = _prefs()
    return bool(prefs and getattr(prefs, "city_modeler_view", False))


def _ctx(window, area):
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region)


def _arrange(window):
    """Turn the freshly duplicated screen into: Outliner | 3D view (+ sidebar) | Properties.
    Areas are looked up again after every operator (a closed area frees the struct a reference points
    to) and Blender sizes them only on the next redraw, so each step waits a tick (yield)."""
    screen = window.screen
    if not any(a.type == 'VIEW_3D' for a in screen.areas):
        max(screen.areas, key=lambda a: a.width * a.height).type = 'VIEW_3D'
    for _ in range(12):  # close one area per tick until only the largest 3D view is left
        views = sorted((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
        rest = [a for a in screen.areas if a.type != 'VIEW_3D'] + views[:-1]
        if not rest:
            break
        for area in rest:  # an area without an aligned neighbour refuses; try the next one
            try:
                with _ctx(window, area):
                    if bpy.ops.screen.area_close() == {'FINISHED'}:
                        break
            except RuntimeError:
                pass
        yield _TICK
    for factor, kind, pick in ((0.78, 'PROPERTIES', max), (0.18, 'OUTLINER', min)):  # B81: outliner ~14 % of the window
        view = next(a for a in screen.areas if a.type == 'VIEW_3D')
        with _ctx(window, view):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=factor)
        yield _TICK  # the new area has x = 0 until the redraw
        pick((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.x).type = kind
    view = next(a for a in screen.areas if a.type == 'VIEW_3D')
    space = view.spaces.active
    space.show_region_ui = False  # B89: the panels are in Properties > Tool; N still opens them here too
    space.show_region_toolbar = True
    space.show_region_tool_header = True
    space.overlay.show_relationship_lines = False  # dashed parent lines (window -> building origin) confuse new users; Overlays menu turns them back on
    for area in screen.areas:
        if area.type == 'PROPERTIES':
            area.spaces.active.context = 'TOOL'


_creating = []


def activate(window):
    workspace = bpy.data.workspaces.get(NAME)
    if workspace is not None:
        if window.workspace != workspace:
            window.workspace = workspace
    elif not _creating:
        steps = _create(window)
        _creating.append(steps)
        bpy.app.timers.register(lambda: _step(steps), first_interval=0.05)


def _step(steps):
    try:
        return next(steps)
    except StopIteration:
        pass
    except Exception as exc:  # a layout failure must never break Blender's start
        print(f"[CityGML] City Modeler view failed: {exc}")
    _creating.clear()
    return None


def _create(window):
    """Copy Layout into the City Modeler workspace. Blender applies a workspace switch only on the next
    event loop pass, so every switch waits a tick (yield) before touching the new screen."""
    base = bpy.data.workspaces.get("Layout")
    if base is not None and window.workspace != base:
        window.workspace = base
        yield _TICK
    before = {w.name for w in bpy.data.workspaces}
    with bpy.context.temp_override(window=window):
        bpy.ops.workspace.duplicate()  # copies the window's workspace, activates the copy
    yield _TICK
    new = [w for w in bpy.data.workspaces if w.name not in before]
    if not new:
        return
    new[0].name = NAME
    if window.workspace != new[0]:
        window.workspace = new[0]
    yield _TICK  # areas get their sizes on the redraw
    if window.workspace == new[0]:
        yield from _arrange(window)


def deactivate(window):
    if window.workspace.name != NAME:
        return
    other = bpy.data.workspaces.get("Layout") or next((w for w in bpy.data.workspaces if w.name != NAME), None)
    if other is not None:
        window.workspace = other


def apply():
    if bpy.app.background:
        return None
    wm = bpy.context.window_manager
    if wm is None:
        return 0.5  # Blender is still starting
    try:
        for window in wm.windows:
            (activate if enabled() else deactivate)(window)
    except Exception as exc:
        print(f"[CityGML] City Modeler view failed: {exc}")
    return None


def on_pref_update(_prefs, _context):
    apply()


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    if enabled():
        bpy.app.timers.register(apply, first_interval=0.1)


def draw_toggle(self, context):
    if context.region.alignment != 'RIGHT':
        return
    prefs = _prefs()
    if prefs is not None:
        self.layout.prop(prefs, "city_modeler_view", text="City Modeler", icon='HOME', toggle=True)


def register():
    bpy.types.TOPBAR_HT_upper_bar.prepend(draw_toggle)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if not bpy.app.background:
        bpy.app.timers.register(lambda: apply() if enabled() else None, first_interval=0.2)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.TOPBAR_HT_upper_bar.remove(draw_toggle)
