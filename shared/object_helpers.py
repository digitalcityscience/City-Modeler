# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
"""Small duck-typed helpers shared by semantic object code."""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional


def safe_get(block: Any, key: str, default: Any = None) -> Any:
    """Read a custom property, returning ``default`` for inaccessible blocks."""
    try:
        return block.get(key, default)
    except Exception:
        return default


def face_material(mesh: Any, poly: Any) -> Any:
    """The material a mesh polygon's ``material_index`` points at, or ``None``
    if the index is out of range (an empty/mismatched material slot list)."""
    materials = mesh.materials
    idx = poly.material_index
    return materials[idx] if 0 <= idx < len(materials) else None


def breadth_first_descendants(
    obj: Any,
    should_descend: Optional[Callable[[Any], bool]] = None,
) -> Iterator[Any]:
    """Yield ``obj`` descendants in child-list breadth-first order.

    ``should_descend`` controls whether a yielded child contributes its own
    children. It lets callers retain domain-specific traversal boundaries
    without reimplementing the FIFO walk.
    """
    queue = list(getattr(obj, "children", []) or [])
    while queue:
        child = queue.pop(0)
        yield child
        if should_descend is None or should_descend(child):
            queue.extend(list(getattr(child, "children", []) or []))
