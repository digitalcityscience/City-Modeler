"""Central CityGML schema-version classification helpers."""

from __future__ import annotations


def _normalized_version(version: object) -> str:
    """Return a safely normalized version string."""
    try:
        return str(version or "").strip()
    except Exception:
        return ""


def is_citygml3(version: object) -> bool:
    """Return whether ``version`` denotes a CityGML 3.x schema."""
    return _normalized_version(version).startswith("3")


def is_citygml2(version: object) -> bool:
    """Return whether ``version`` denotes a CityGML 2.x schema."""
    return _normalized_version(version).startswith("2")


def is_citygml2_0(version: object) -> bool:
    """Return whether ``version`` is exactly the legacy CityGML 2.0 value.

    Unlike :func:`is_citygml3`/:func:`is_citygml2`, this is a strict ``==``
    with no whitespace/``None`` normalization: it exists to preserve call
    sites that historically did an exact dict-key or ``==`` comparison
    against the literal ``"2.0"`` string, not a prefix check.
    """
    return version == "2.0"
