# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026

from . import generic_attributes_ui
from . import citygml_attributes_panel
from . import header_panel
from . import gallery
from . import tools
from . import attribute_card
from . import theme_view
from . import city_modeler_view


def register():
    gallery.register()  # header_panel: the add-on registers it first (parent of the step panels)
    generic_attributes_ui.register()
    tools.register()
    attribute_card.register()
    theme_view.register()
    city_modeler_view.register()
    # The CityGML Attributes tab is intentionally disabled in the GUI.
    # Keep the module in the codebase so it can be re-enabled later if needed.


def unregister():
    city_modeler_view.unregister()
    theme_view.unregister()
    attribute_card.unregister()
    tools.unregister()
    gallery.unregister()
    # The panel is not registered while the tool is intentionally hidden from the GUI.
    generic_attributes_ui.unregister()


__all__ = ["register", "unregister"]
