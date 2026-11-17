# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/opening_types.py
"""Ready-made window and door sizes for Draw Window / Draw Door (B07).

Doors use DIN 18100 nominal shell sizes (width 0.885 / 1.01 / 1.135 m,
height 2.135 m). Windows have no single German standard size; these are
common example sizes, editable in F9 after placing. The first entry of each
kind is the default (same as the old fixed default for windows).

B81: the Entrance door is a role, not only a size. CityGML gives a door an address
(2.0 ``bldg:Door/bldg:address``, 3.0 ``con:DoorSurface/con:address``: "the entrance
of a building"), and an entrance stands on the ground (ADR 0004 D3: a notch in the
wall's outline). A balcony / upper-floor door floats (ring + filling) and has none.
"""

OPENING_TYPES = {
    # key: (kind, label, width m, height m)
    "W_120x140": ("Window", "Window 1.20 × 1.40", 1.20, 1.40),
    "W_100x120": ("Window", "Window 1.00 × 1.20", 1.00, 1.20),
    "W_80x100": ("Window", "Small window 0.80 × 1.00", 0.80, 1.00),
    "W_200x140": ("Window", "Wide window 2.00 × 1.40", 2.00, 1.40),
    "W_100x220": ("Window", "French window 1.00 × 2.20", 1.00, 2.20),
    "W_300x80": ("Window", "Strip window 3.00 × 0.80", 3.00, 0.80),
    "D_101x2135": ("Door", "Door 1.01 × 2.135 (DIN 18100)", 1.01, 2.135),
    "D_0885x2135": ("Door", "Narrow door 0.885 × 2.135 (DIN 18100)", 0.885, 2.135),
    "D_1135x2135": ("Door", "Entrance door 1.135 × 2.135 (on the ground, building address)", 1.135, 2.135),
    "D_201x2135": ("Door", "Double door 2.01 × 2.135", 2.01, 2.135),
}


def items(kind):
    """Enum items ``(key, label, description)`` of one kind, default first."""
    return [(key, label, f"{w:.3g} m wide, {h:.4g} m high")
            for key, (k, label, w, h) in OPENING_TYPES.items() if k == kind]


def size(key, kind):
    """``(width, height)`` of ``key`` when it is a ``kind`` type, else ``None``."""
    entry = OPENING_TYPES.get(key)
    return (entry[2], entry[3]) if entry and entry[0] == kind else None


ENTRANCE = "D_1135x2135"


def door_address_owner(door):
    """B81: the object whose ``bldg:address:*`` a door writes: its own (an imported door's), else for
    an entrance door the nearest ancestor that has one (its building); ``None`` for any other object
    (or ``None``)."""
    if door is None or str(door.get("cgml3_feature") or "") not in ("Door", "DoorSurface"):
        return None
    o = door
    while o is not None:
        if any(str(k).startswith("bldg:address:") for k in o.keys()):
            return o
        if not door.get("cgml_entrance_door"):
            return None
        o = o.parent
    return None
