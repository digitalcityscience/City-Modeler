# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""
CityGML Surface Type Definitions
Definiert alle Surface Types aus CityGML 2.0 und 3.0 Schemas
"""

from ...shared.semantic_appearance import SEMANTIC_PALETTE

# =========================
# CityGML 2.0 Building Surface Types
# =========================
BUILDING_SURFACES_V2 = {
    # B25: order = list order in the UI (WallSurface first = default); the enum number
    # is the position here, so append new types at the end of their group only.
    # One-line descriptions follow OGC 12-019 (CityGML 2.0) §10.3.3 and OGC 20-010
    # (3.0) data dictionary; the full quotes are in tickets-building/B25-*.md.
    # Exterior shell (LoD2+ in 2.0, 12-019 §10.3.1 rule 4)
    "WallSurface": {
        "description": "Outer wall: part of the facade seen from outside",
        "color": (0.55, 0.55, 0.55, 1.0),
        "lod": "1-4",
        "geometry": "vertical"
    },
    "RoofSurface": {
        "description": "Main roof surface (dormers and chimneys are installations)",
        "color": SEMANTIC_PALETTE["RoofSurface"].color,  # B99: the importer's fallback roof colour, one red for drawn and DB roofs
        "lod": "1-4",
        "geometry": "sloped upward"
    },
    "GroundSurface": {
        "description": "Ground plate: the footprint the building stands on",
        "color": (0.30, 0.30, 0.30, 1.0),
        "lod": "1-4",
        "geometry": "horizontal, bottom"
    },
    "OuterCeilingSurface": {
        "description": "Outer surface facing down, e.g. ceiling of a loggia or a passage",
        "color": (0.80, 0.70, 0.20, 1.0),
        "lod": "2-4",
        "geometry": "horizontal downward, exterior"
    },
    "OuterFloorSurface": {
        "description": "Outer surface facing up, e.g. floor of a loggia or a terrace",
        "color": (0.60, 0.80, 0.40, 1.0),
        "lod": "2-4",
        "geometry": "horizontal upward, exterior"
    },
    "ClosureSurface": {
        "description": "Virtual surface that closes an open side so the volume is closed (open garage, hangar)",
        "color": (0.90, 0.90, 0.20, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
    # Interior (LoD4 / 3.0 rooms): out of scope (Kapsam karari), kept for old data
    "FloorSurface": {
        "description": "Interior floor surfaces",
        "color": (0.50, 0.50, 0.25, 1.0),
        "lod": "4",
        "geometry": "horizontal upward, interior"
    },
    "CeilingSurface": {
        "description": "Interior ceiling surfaces",
        "color": (0.70, 0.70, 0.50, 1.0),
        "lod": "4",
        "geometry": "horizontal downward, interior"
    },
    "InteriorWallSurface": {
        "description": "Interior wall surfaces",
        "color": (0.65, 0.65, 0.60, 1.0),
        "lod": "4",
        "geometry": "vertical, interior"
    },
    # Openings: not boundary surfaces; the surface list hides them (OPENING_TYPES)
    "Window": {
        "description": "Window opening (fills a wall opening with glass)",
        "color": (0.40, 0.70, 0.90, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
    "Door": {
        "description": "Door opening (fills a wall opening with door)",
        "color": (0.55, 0.35, 0.15, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
}

# =========================
# CityGML 2.0 Bridge Surface Types
# =========================
BRIDGE_SURFACES_V2 = {
    "RoofSurface": {
        "description": "Bridge roof surfaces",
        "color": (0.94, 0.17, 0.04, 1.0),  # #f02b09
        "lod": "1-4",
        "geometry": "sloped upward"
    },
    "WallSurface": {
        "description": "Bridge wall surfaces",
        "color": (0.42, 0.41, 0.38, 1.0),  # #6c6960
        "lod": "1-4",
        "geometry": "vertical"
    },
    "GroundSurface": {
        "description": "Bridge ground surfaces",
        "color": (0.26, 0.29, 0.30, 1.0),  # #434b4d
        "lod": "1-4",
        "geometry": "horizontal, bottom"
    },
    "ClosureSurface": {
        "description": "Virtual closure surfaces",
        "color": (0.90, 0.90, 0.20, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
    "OuterFloorSurface": {
        "description": "Bridge deck/outer floor surfaces",
        "color": (0.49, 0.65, 0.33, 1.0),  # #7da554
        "lod": "1-4",
        "geometry": "horizontal upward"
    },
    "OuterCeilingSurface": {
        "description": "Bridge outer ceiling surfaces",
        "color": (0.79, 0.71, 0.19, 1.0),  # #c9b430
        "lod": "1-4",
        "geometry": "horizontal downward"
    },
    # LOD4 Interior Surfaces
    "FloorSurface": {
        "description": "Interior floor surfaces",
        "color": (0.50, 0.50, 0.25, 1.0),
        "lod": "4",
        "geometry": "horizontal upward, interior"
    },
    "CeilingSurface": {
        "description": "Interior ceiling surfaces",
        "color": (0.70, 0.70, 0.50, 1.0),
        "lod": "4",
        "geometry": "horizontal downward, interior"
    },
    "InteriorWallSurface": {
        "description": "Interior wall surfaces",
        "color": (0.65, 0.65, 0.60, 1.0),
        "lod": "4",
        "geometry": "vertical, interior"
    },
    # Openings
    "Window": {
        "description": "Window opening in bridge structure",
        "color": (0.40, 0.70, 0.90, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
    "Door": {
        "description": "Door opening in bridge structure",
        "color": (0.55, 0.35, 0.15, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
    # CityGML ComplexTypes for Bridge Objects
    "BridgeConstructionElement": {
        "description": "Essential structural component (pylons, anchorages) - cannot be removed",
        "color": (0.20, 0.30, 0.50, 1.0),  # Blue for structural
        "lod": "1-4",
        "geometry": "structural component"
    },
    "BridgeInstallation": {
        "description": "Non-structural part (stairs, antennas, railways) - removable without collapse",
        "color": (0.60, 0.40, 0.20, 1.0),  # Brown for installations
        "lod": "2-4",
        "geometry": "installation component"
    },
    "IntBridgeInstallation": {
        "description": "Interior installation with specific function (stairs, railings, radiators, pipes)",
        "color": (0.50, 0.50, 0.70, 1.0),  # Light blue for interior
        "lod": "4",
        "geometry": "interior component"
    },
    "BridgeRoom": {
        "description": "Closed interior space within bridge structure",
        "color": (0.70, 0.60, 0.50, 1.0),  # Beige for rooms
        "lod": "4",
        "geometry": "enclosed space"
    },
    "BridgeFurniture": {
        "description": "Interior furniture objects within bridge rooms",
        "color": (0.80, 0.70, 0.40, 1.0),  # Light brown for furniture
        "lod": "4",
        "geometry": "furniture object"
    },
    "AbstractBridge": {
        "description": "Generic bridge structure (Bridge or BridgePart)",
        "color": (0.40, 0.40, 0.40, 1.0),  # Gray for generic
        "lod": "1-4",
        "geometry": "bridge structure"
    },
}

# =========================
# CityGML 2.0 Tunnel Surface Types
# =========================
TUNNEL_SURFACES_V2 = {
    "RoofSurface": {
        "description": "Tunnel roof (separates interior from above)",
        "color": (0.85, 0.35, 0.15, 1.0),
        "lod": "1-4",
        "geometry": "sloped upward"
    },
    "WallSurface": {
        "description": "Tunnel walls (separates interior from sides)",
        "color": (0.50, 0.45, 0.40, 1.0),
        "lod": "1-4",
        "geometry": "vertical"
    },
    "GroundSurface": {
        "description": "Tunnel floor (separates interior from below)",
        "color": (0.35, 0.35, 0.35, 1.0),
        "lod": "1-4",
        "geometry": "horizontal, bottom"
    },
    "ClosureSurface": {
        "description": "Virtual closure (e.g., tunnel entrance)",
        "color": (0.90, 0.85, 0.25, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
    # LOD4 Interior Surfaces
    "OuterFloorSurface": {
        "description": "Outer floor surfaces",
        "color": (0.55, 0.75, 0.40, 1.0),
        "lod": "4",
        "geometry": "horizontal upward, exterior"
    },
    "OuterCeilingSurface": {
        "description": "Outer ceiling surfaces",
        "color": (0.75, 0.65, 0.25, 1.0),
        "lod": "4",
        "geometry": "horizontal downward, exterior"
    },
    "FloorSurface": {
        "description": "Interior floor surfaces",
        "color": (0.45, 0.45, 0.30, 1.0),
        "lod": "4",
        "geometry": "horizontal upward, interior"
    },
    "CeilingSurface": {
        "description": "Interior ceiling surfaces",
        "color": (0.65, 0.65, 0.50, 1.0),
        "lod": "4",
        "geometry": "horizontal downward, interior"
    },
    "InteriorWallSurface": {
        "description": "Interior wall surfaces",
        "color": (0.60, 0.60, 0.55, 1.0),
        "lod": "4",
        "geometry": "vertical, interior"
    },
    # Openings
    "Window": {
        "description": "Window opening in tunnel structure",
        "color": (0.40, 0.70, 0.90, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
    "Door": {
        "description": "Door opening in tunnel structure",
        "color": (0.55, 0.35, 0.15, 1.0),
        "lod": "3-4",
        "geometry": "vertical, within wall"
    },
}

# =========================
# CityGML 2.0 WaterBody Surface Types
# =========================
WATERBODY_SURFACES_V2 = {
    "WaterSurface": {
        "description": "Water surface (separates water from air)",
        "color": (0.20, 0.50, 0.80, 1.0),
        "lod": "1-4",
        "geometry": "horizontal, top"
    },
    "WaterGroundSurface": {
        "description": "Water ground surface (bottom of water body)",
        "color": (0.15, 0.35, 0.55, 1.0),
        "lod": "1-4",
        "geometry": "terrain surface"
    },
    "WaterClosureSurface": {
        "description": "Closure surface between water bodies",
        "color": (0.80, 0.85, 0.30, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
}

# =========================
# CityGML 2.0 Feature-specific Surface Types
# =========================
CITYFURNITURE_SURFACES_V2 = {
    "CityFurniture": {
        "description": "City furniture object surface",
        "color": (0.70, 0.50, 0.30, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
}

VEGETATION_SURFACES_V2 = {
    "VegetationObject": {
        "description": "General vegetation object",
        "color": (0.30, 0.70, 0.30, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
    "PlantCover": {
        "description": "Plant cover / biotope surface",
        "color": (0.25, 0.65, 0.25, 1.0),
        "lod": "1-4",
        "geometry": "terrain following"
    },
    "SolitaryVegetationObject": {
        "description": "Solitary vegetation (e.g., single tree)",
        "color": (0.20, 0.55, 0.20, 1.0),
        "lod": "1-4",
        "geometry": "volumetric"
    },
}

LANDUSE_SURFACES_V2 = {
    "LandUse": {
        "description": "Land use surface",
        "color": (0.85, 0.75, 0.55, 1.0),
        "lod": "1-4",
        "geometry": "terrain surface"
    },
}

# =========================
# Generic Fallback Surface Type
# =========================
GENERIC_SURFACES_V2 = {
    "GenericSurface": {
        "description": "Generic surface (fallback when no specific type applies)",
        "color": (0.50, 0.50, 0.50, 1.0),
        "lod": "1-4",
        "geometry": "any"
    },
}

TRANSPORTATION_SURFACES_V2 = {
    "TrafficArea": {
        "description": "Area used by traffic: driving lane, footway, cycle lane, rail track",
        "color": (0.40, 0.40, 0.40, 1.0),
        "lod": "1-4",
        "geometry": "horizontal surface"
    },
    "AuxiliaryTrafficArea": {
        "description": "Area next to traffic: kerb, median, green strip",
        "color": (0.60, 0.60, 0.60, 1.0),
        "lod": "1-4",
        "geometry": "horizontal surface"
    },
}

# =========================
# CityGML 3.0 Enhancements (Placeholder für zukünftige Erweiterung)
# =========================
BUILDING_SURFACES_V3 = BUILDING_SURFACES_V2.copy()
BRIDGE_SURFACES_V3 = BRIDGE_SURFACES_V2.copy()
TUNNEL_SURFACES_V3 = TUNNEL_SURFACES_V2.copy()
WATERBODY_SURFACES_V3 = WATERBODY_SURFACES_V2.copy()
CITYFURNITURE_SURFACES_V3 = CITYFURNITURE_SURFACES_V2.copy()
VEGETATION_SURFACES_V3 = VEGETATION_SURFACES_V2.copy()
LANDUSE_SURFACES_V3 = LANDUSE_SURFACES_V2.copy()
TRANSPORTATION_SURFACES_V3 = {
    **TRANSPORTATION_SURFACES_V2,
    "Marking": {  # tran:Marking exists only in 3.0
        "description": "Road marking: lane line, crossing (CityGML 3.0 only)",
        "color": (0.92, 0.88, 0.35, 1.0),
        "lod": "1-4",
        "geometry": "surface marking"
    },
}
GENERIC_SURFACES_V3 = GENERIC_SURFACES_V2.copy()

# =========================
# Feature Type Registry
# =========================
FEATURE_TYPES_V2 = {
    "BUILDING": BUILDING_SURFACES_V2,
    "BRIDGE": BRIDGE_SURFACES_V2,
    "TUNNEL": TUNNEL_SURFACES_V2,
    "WATERBODY": WATERBODY_SURFACES_V2,
    "CITYFURNITURE": CITYFURNITURE_SURFACES_V2,
    "VEGETATION": VEGETATION_SURFACES_V2,
    "LANDUSE": LANDUSE_SURFACES_V2,
    "TRANSPORTATION": TRANSPORTATION_SURFACES_V2,
}

FEATURE_TYPES_V3 = {
    "BUILDING": BUILDING_SURFACES_V3,
    "BRIDGE": BRIDGE_SURFACES_V3,
    "TUNNEL": TUNNEL_SURFACES_V3,
    "WATERBODY": WATERBODY_SURFACES_V3,
    "CITYFURNITURE": CITYFURNITURE_SURFACES_V3,
    "VEGETATION": VEGETATION_SURFACES_V3,
    "LANDUSE": LANDUSE_SURFACES_V3,
    "TRANSPORTATION": TRANSPORTATION_SURFACES_V3,
}

# =========================
# Helper Functions
# =========================
def get_surface_types(feature_type="BUILDING", version="2.0", lod=None):
    """
    Gibt die Surface Types für einen bestimmten Feature Type zurück
    
    Args:
        feature_type: Feature Type (z.B. "BUILDING", "BRIDGE", "TUNNEL", etc.)
        version: "2.0" oder "3.0" (CityGML Version)
        lod: Optional: Filter nach LOD (z.B. "1-3" für Basis-Surfaces)
    
    Returns:
        Dictionary mit Surface Types
    """
    registry = FEATURE_TYPES_V3 if version == "3.0" else FEATURE_TYPES_V2
    surfaces = registry.get(feature_type, {})
    
    if lod:
        # Filter nach LOD
        if lod == "1-3":
            # Nur Basis-Surfaces (kein LOD4)
            return {k: v for k, v in surfaces.items() if v["lod"] != "4"}
        elif lod == "4":
            # Alle Surfaces (inkl. LOD4)
            return surfaces
    
    return surfaces

def get_surface_color(surface_type, feature_type="BUILDING", version="2.0"):
    """Gibt die Farbe für einen Surface Type zurück"""
    surfaces = get_surface_types(feature_type, version)
    return surfaces.get(surface_type, {}).get("color", (1.0, 1.0, 0.2, 1.0))

OPENING_TYPES = frozenset({"Window", "Door"})  # openings, not boundary surfaces


def get_enum_items(feature_type="BUILDING", version="2.0", lod="1-3", exclude=()):
    """
    Erzeugt Blender EnumProperty Items für die Surface Types
    
    Args:
        feature_type: Feature Type (z.B. "BUILDING", "BRIDGE", etc.)
        version: "2.0" oder "3.0"
        lod: "1-3" (nur Basis) oder "4" (inkl. Interior)
    
    Returns:
        List of tuples für EnumProperty
    """
    surfaces = get_surface_types(feature_type, version, lod)
    # B25: table order (most used first); number = position in the full table, so a
    # stored value keeps its meaning whatever the LoD filter.
    full = list(get_surface_types(feature_type, version))
    items = [(name, name, info["description"], full.index(name)) for name, info in surfaces.items()
             if name not in exclude]
    return items if items else [("NONE", "None", "No surfaces available", 0)]
