# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# shared/codelists.py
"""Code lists for the attribute card (B22). bpy-free.

``codelists/registry.json`` names the lists; each list is one JSON data file
``{"attributes": {"bldg:function": {"codeSpace": URI, "codes": {code: label}}}}``.
A new country is one data file plus one registry row: no code change.

``detect`` picks the list that fits the data best: a codeSpace hint or a value
pattern of the list scores high, a value found only in that list scores one.
Ties and no evidence give the registry's default (OGC).
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent / "codelists"


@lru_cache(maxsize=None)
def registry() -> tuple:
    rows = json.loads((DIR / "registry.json").read_text(encoding="utf-8"))["lists"]
    return tuple(rows)


def default_id() -> str:
    return next((r["id"] for r in registry() if r.get("default")), registry()[0]["id"])


@lru_cache(maxsize=None)
def _load(list_id: str) -> dict:
    row = next((r for r in registry() if r["id"] == list_id), None)
    if row is None:
        return {}
    return json.loads((DIR / row["file"]).read_text(encoding="utf-8")).get("attributes", {})


def codes(list_id: str, attr: str) -> dict:
    """``{code: label}`` of ``attr`` in the list (empty when the list has none)."""
    return dict(_load(list_id).get(attr, {}).get("codes", {}))


def code_space(list_id: str, attr: str) -> str:
    return str(_load(list_id).get(attr, {}).get("codeSpace", ""))


def has_codes(attr: str) -> bool:
    """True when any list knows ``attr`` (else the card shows a text field)."""
    return any(codes(r["id"], attr) for r in registry())


def detect(values: dict, code_spaces: dict | None = None) -> str:
    """The list ``values`` (``{attr: code}``) and their codeSpaces fit best."""
    scores = {}
    for row in registry():
        lid, score = row["id"], 0
        hints = [h.lower() for h in row.get("code_space_hints", [])]
        pattern = row.get("value_pattern") or ""
        for attr, value in values.items():
            value = str(value or "").strip()
            if not value:
                continue
            cs = str((code_spaces or {}).get(attr) or "").lower()
            if cs and any(h in cs for h in hints):
                score += 10
            if pattern and re.match(pattern, value):
                score += 10
            if value in codes(lid, attr) and not any(
                    value in codes(other["id"], attr) for other in registry() if other["id"] != lid):
                score += 1
        scores[lid] = score
    best = max(scores.values(), default=0)
    winners = [lid for lid, s in scores.items() if s == best]
    return default_id() if best == 0 or default_id() in winners else winners[0]


@lru_cache(maxsize=None)
def templates() -> tuple:
    path = DIR / "templates.json"
    return tuple(json.loads(path.read_text(encoding="utf-8"))["templates"]) if path.exists() else ()


@lru_cache(maxsize=None)
def _elements() -> dict:
    return json.loads((DIR / "elements.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def element_fields(feature: str) -> tuple:
    """B88: card fields of one building element (surface, opening, installation), from
    ``elements.json``: dicts with ``label``, ``codes`` and either ``attr`` (a CityGML attribute of
    the feature, with its ``codeSpace``) or ``name`` (a generic attribute). Empty for other features."""
    data = _elements()
    fields = data["elements"].get(data["features"].get(feature, ""), ())
    return tuple({**f, "codes": data["lists"][f["list"]]} for f in fields)


def element_kind(feature: str) -> str:
    """B88: "wall" / "roof" / "window" / "door" / "installation" ("" for the rest); Window and
    WindowSurface are the same kind."""
    data = _elements()
    return data["features"].get(feature, "")


if __name__ == "__main__":  # self-check: python shared/codelists.py
    assert default_id() == "OGC"
    assert detect({"bldg:function": "31001_1120"}) == "ADV"
    assert detect({"bldg:roofType": "3100"}) == "ADV"          # AdV-only roof code
    assert detect({"bldg:function": "1000"}) == "OGC"           # in both lists -> default
    assert detect({"bldg:function": "x"}, {"bldg:function": "http://repository.gdi-de.org/x"}) == "ADV"
    assert codes("ADV", "bldg:function")["31001_2100"].startswith("Gebäude für Gewerbe")
    assert not codes("OGC", "tran:function") and not has_codes("frn:function")
    assert [f["attr"] for f in element_fields("BuildingInstallation")] == ["bldg:class", "bldg:function", "bldg:usage"]
    assert element_kind("Window") == element_kind("WindowSurface") == "window" and not element_fields("GroundSurface")
    print("codelists ok")
