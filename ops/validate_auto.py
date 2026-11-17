# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Author: Virtual City Systems
# Year: 2026
# ops/validate_auto.py
"""
Automatische CityGML Version-Erkennung und Validierung
=======================================================

Erkennt automatisch ob CityGML 2.0 oder 3.0 und ruft die entsprechende
Validierungsfunktion auf.

Funktionen:
- detect_version_from_file: Liest Datei und erkennt Version
- validate_citygml_auto: Automatische Validierung basierend auf erkannter Version
"""

from __future__ import annotations

from typing import Tuple
from pathlib import Path

from ..shared.citygml_sniff import version_from_file
from xml.etree import ElementTree as ET

def _resolve_citygml_file(path: str) -> tuple[str, str | None]:
    """
    Robustheit: Falls versehentlich ein Pfad im Unterordner 'appearance' oder ein Ordner
    übergeben wird, versuchen wir den richtigen CityGML-Dateipfad zu rekonstruieren.

    Returns:
        (resolved_path, note)
    """
    p = Path(path)

    # 1) Falls Ordner übergeben wurde: suche eine .gml/.xml im Ordner
    if p.exists() and p.is_dir():
        candidates = sorted(list(p.glob("*.gml")) + list(p.glob("*.xml")))
        if not candidates:
            return (str(p), None)

        # Bevorzuge bekannte Namen
        for prefer in ("export.gml", "export_v2.gml"):
            c = p / prefer
            if c.exists():
                return (str(c), f"Pfad war Ordner; nutze '{c.name}'")

        # Sonst: neueste Datei
        newest = max(candidates, key=lambda x: x.stat().st_mtime)
        return (str(newest), f"Pfad war Ordner; nutze neueste Datei '{newest.name}'")

    # 2) Normalfall: Datei existiert
    if p.exists():
        return (str(p), None)

    # 3) Typischer Fehler: Pfad zeigt in .../appearance/<samefilename>
    if p.parent.name.lower() == "appearance":
        alt = p.parent.parent / p.name
        if alt.exists():
            return (str(alt), f"Pfad zeigte auf 'appearance'; nutze '{alt}'")

    # 4) Allgemeiner: irgendein 'appearance' Segment entfernen
    parts = list(p.parts)
    lowered = [x.lower() for x in parts]
    if "appearance" in lowered:
        i = lowered.index("appearance")
        alt = Path(*parts[:i], *parts[i + 1 :])
        if alt.exists():
            return (str(alt), f"Pfad enthielt 'appearance'; nutze '{alt}'")

    # 5) Keine Korrektur möglich
    return (str(p), None)


def detect_version_from_file(path: str) -> Tuple[str, str]:
    """
    Erkennt CityGML-Version aus Datei (Regel: shared.citygml_sniff).

    Returns:
        (version, message)
        version: '1.0', '2.0', '3.0' oder 'unknown'
    """
    orig_path = path
    path, note = _resolve_citygml_file(path)
    note_prefix = f"[Pfad korrigiert: {orig_path} -> {path}] " if note else ""

    try:
        version = version_from_file(path)
    except OSError as e:
        return 'unknown', f"{note_prefix}Datei nicht lesbar: {e}"

    if version == 'unknown':
        return 'unknown', f"{note_prefix}CityGML-Version konnte nicht erkannt werden"
    return version, f"{note_prefix}CityGML {version} erkannt"


def validate_citygml_auto(
    path: str,
    schema_base: str | Path | None = None,
) -> Tuple[bool, str]:
    """
    Automatische CityGML-Validierung.
    
    Erkennt Version und ruft entsprechende Validierung auf:
    - CityGML 2.0 → validate_citygml2_xsd
    - CityGML 3.0 → validate_citygml3_xsd
    - unknown → Fehler
    
    Args:
        path: Pfad zur CityGML-Datei
        schema_base: Optionaler Pfad zum Schema-Verzeichnis
    
    Returns:
        (ok, message)
        ok: True wenn Validierung erfolgreich
        message: Validierungsergebnis oder Fehlermeldung
    """
    path, _note = _resolve_citygml_file(path)

    # Version erkennen
    version, version_msg = detect_version_from_file(path)
    
    if version == 'unknown':
        return False, f"Version-Erkennung fehlgeschlagen: {version_msg}"
    
    # Version-spezifische Validierung
    if version == '3.0':
        from .validate import validate_citygml3_xsd

        # structure check first, then XSD (validate_citygml3_xsd does both)
        ok, msg = validate_citygml3_xsd(path, schema_base)
        return ok, f"CityGML 3.0: {msg}"
    
    elif version == '2.0':
        from .validate_citygml2 import well_formed_citygml2, validate_citygml2_xsd
        
        # Schnelltest
        ok, msg = well_formed_citygml2(path)
        if not ok:
            return False, f"CityGML 2.0 Struktur-Check: {msg}"
        
        # XSD-Validierung
        ok, msg = validate_citygml2_xsd(path, schema_base)
        return ok, f"CityGML 2.0: {msg}"
    
    else:
        return False, f"Unbekannte CityGML-Version: {version}"


def well_formed_citygml_auto(path: str) -> Tuple[bool, str]:
    """
    Automatischer Struktur-Check (kein XSD).
    
    Erkennt Version und ruft entsprechenden well_formed Check auf.
    
    Args:
        path: Pfad zur CityGML-Datei
    
    Returns:
        (ok, message)
    """

    path, _note = _resolve_citygml_file(path)
    
    # Version erkennen
    version, version_msg = detect_version_from_file(path)
    
    if version == 'unknown':
        return False, f"Version-Erkennung fehlgeschlagen: {version_msg}"
    
    # Version-spezifischer Check
    if version == '3.0':
        from .validate import well_formed_citygml3
        ok, msg = well_formed_citygml3(path)
        return ok, f"CityGML 3.0: {msg}"
    
    elif version == '2.0':
        from .validate_citygml2 import well_formed_citygml2
        ok, msg = well_formed_citygml2(path)
        return ok, f"CityGML 2.0: {msg}"
    
    else:
        return False, f"Unbekannte CityGML-Version: {version}"
