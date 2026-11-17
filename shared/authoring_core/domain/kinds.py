"""Feature-kind registry (CAC-P3.1).

Declares the semantic differences between feature types (host surface,
what the host gets carved into, which generator/param shape applies) so a
future generic ``create_feature`` lifecycle (P3.2) can read them instead of
branching on hard-coded Window/Door logic throughout the authoring path.

This is a registry of data, not behavior. ``generator`` is a name string -
the same kind of value already written to ``FeatureRecord.generator``
(shared/authoring_core/domain/records.py) - never a callable or the
Blender-dependent kernel itself (``shared/facade_placement.py``). Pure
Python only: no bpy, bmesh, mathutils or gpu (rule R1, enforced by
scripts/tests/unit/test_cac_domain_has_no_bpy.py).

Door is declared but ``enabled=False`` - this task does not implement Door
authoring. Its ``host_effect`` already lists both variants
``place_semantic_element`` distinguishes today (floating Door: interior
ring, same as Window; ground-touching Door: an exterior notch cut into the
host's boundary, no ring) so P3.2 has the vocabulary without this task
choosing between them.
"""
from dataclasses import dataclass, field


class KindRegistryError(ValueError):
    """An unknown kind was looked up, or a kind was registered twice."""


@dataclass(frozen=True)
class FeatureKind:
    name: str
    enabled: bool
    host_kinds: tuple = ()
    host_effect: tuple = ()
    generator: str = ""
    param_schema: dict = field(default_factory=dict)
    owner_rule: str = ""


_REGISTRY: dict = {}


def register(kind: FeatureKind) -> FeatureKind:
    """Add ``kind`` to the registry; raises on a duplicate ``name``."""
    if kind.name in _REGISTRY:
        raise KindRegistryError(f"feature kind {kind.name!r} is already registered")
    _REGISTRY[kind.name] = kind
    return kind


def get(name: str) -> FeatureKind:
    """The registered ``FeatureKind`` for ``name``; raises if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KindRegistryError(f"unknown feature kind {name!r}") from None


def all_kinds() -> tuple:
    """Every registered ``FeatureKind``, sorted by name (deterministic)."""
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def is_enabled(name: str) -> bool:
    return get(name).enabled


def ensure_enabled(name: str) -> FeatureKind:
    """The ``FeatureKind`` for ``name``; raises if it exists but is disabled."""
    kind = get(name)
    if not kind.enabled:
        raise KindRegistryError(f"feature kind {name!r} is disabled")
    return kind


def allows_host(name: str, host_kind: str) -> bool:
    return host_kind in get(name).host_kinds


def ensure_host_allowed(name: str, host_kind: str) -> FeatureKind:
    """The ``FeatureKind`` for ``name``; raises if ``host_kind`` isn't in its ``host_kinds``."""
    kind = get(name)
    if host_kind not in kind.host_kinds:
        raise KindRegistryError(f"feature kind {name!r} cannot host on {host_kind!r}")
    return kind


def validate_params(name: str, params: dict) -> tuple:
    """Error strings for ``params`` against ``name``'s ``param_schema``; empty if valid.

    Checks shape only (required keys present, no unknown keys) - it does not
    coerce or type-check values.
    """
    kind = get(name)
    errors = [f"missing required param {pname!r}" for pname, spec in kind.param_schema.items()
              if spec.get("required") and pname not in params]
    errors += [f"unknown param {pname!r}" for pname in params if pname not in kind.param_schema]
    return tuple(errors)


WINDOW = register(FeatureKind(
    name="Window",
    enabled=True,
    host_kinds=("WallSurface",),
    host_effect=("interior_ring",),
    generator="rectangular_facade_window",
    param_schema={
        "width": {"type": "float", "required": True},
        "height": {"type": "float", "required": True},
        "depth": {"type": "float", "required": True},
    },
    owner_rule="host",
))

DOOR = register(FeatureKind(
    name="Door",
    enabled=False,
    host_kinds=("WallSurface",),
    host_effect=("interior_ring", "exterior_notch"),  # floating vs. ground-touching
    generator="",
    param_schema={
        "width": {"type": "float", "required": True},
        "height": {"type": "float", "required": True},
        "depth": {"type": "float", "required": True},
    },
    owner_rule="host",
))
