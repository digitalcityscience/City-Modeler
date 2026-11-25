"""gml:id validation and minting for authoring records.

No new id scheme (PLAN.md §2.3, ADR 0004 D5): minted ids use the forms the
add-on already writes - ``ID_<uuid4 hex>`` for features and polygons
(shared/facade_placement.py), ``OPENINGLINK_<uuid4 hex>`` for opening links,
and ``<polygon id>_<n>_`` for ring ids (exporter canonical renumbering).
Every placement mints fresh ids; nothing is reused (D5).
"""
import re
import uuid

# The id alphabet the writers produce (io/writer/helpers.py make_gml_id):
# letters, digits, "_", ".", "-", not starting with a digit, "-" or ".".
_AUTHORED_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

# gml:id is xs:ID = NCName (XML 1.0 5th edition "Name" without ':'): first a letter or "_",
# then letters, digits, ".", "-", "_". Unicode letters count; the dot is allowed.
_NAME_START = ("A-Z_a-zÀ-ÖØ-öø-˿Ͱ-ͽͿ-῿‌‍"
               "⁰-↏Ⰰ-⿯、-퟿豈-﷏ﷰ-�\U00010000-\U000EFFFF")
_NCNAME = re.compile(f"[{_NAME_START}][{_NAME_START}0-9.·̀-ͯ‿⁀-]*")


def is_ncname(value):
    """True when ``value`` can be written as a gml:id as it stands.

    The one id check of the add-on: the CityJSON reader (``gml_id_for``) and both GML writers
    (``ensure_xs_id``, ``make_gml_id``) ask it, and none of them renames an id it accepts.
    """
    return isinstance(value, str) and bool(_NCNAME.fullmatch(value))


def is_valid_gml_id(value):
    """True for a usable record id: a non-empty string without whitespace.

    Deliberately lenient, because imported ids are kept verbatim and source
    files are not always NCName-clean. Use ``is_authored_gml_id`` for ids
    this add-on mints.
    """
    return isinstance(value, str) and bool(value) and not any(c.isspace() for c in value)


def is_authored_gml_id(value):
    """True when ``value`` matches the alphabet of ids the writers produce."""
    return isinstance(value, str) and bool(_AUTHORED_ID.match(value))


def _hex(uuid_factory):
    return (uuid_factory or uuid.uuid4)().hex


def mint_gml_id(prefix="ID", uuid_factory=None):
    """Fresh feature/polygon id ``<prefix>_<uuid4 hex>``."""
    if not _AUTHORED_ID.match(prefix):
        raise ValueError(f"invalid id prefix {prefix!r}")
    return f"{prefix}_{_hex(uuid_factory)}"


def mint_opening_link(uuid_factory=None):
    """Fresh fill <-> ring pairing key (ADR 0004 D2)."""
    return f"OPENINGLINK_{_hex(uuid_factory)}"


def ring_id(polygon_id, index):
    """Canonical ring id as the exporter writes it: ``<polygon id>_<index>_``."""
    if not is_valid_gml_id(polygon_id):
        raise ValueError(f"invalid polygon id {polygon_id!r}")
    if not isinstance(index, int) or index < 0:
        raise ValueError(f"invalid ring index {index!r}")
    return f"{polygon_id}_{index}_"
