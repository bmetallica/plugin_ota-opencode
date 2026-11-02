"""Einstellungen: was der Admin in OTA einträgt, und das Kopplungsgeheimnis.

OTA schickt die Werte des Formulars per `PUT /ota/v1/konfiguration`; sie
liegen in der Tabelle `einstellung`. Das Dienstgeheimnis, mit dem OTA sich
ausweist und die Kopfzeilen der Ansicht signiert, ebenso. Der
Kopplungsschlüssel dagegen kommt aus der Umgebung (`.env`, von setup.sh
erzeugt) — er ist der Beweis, dass der Admin Zugriff auf diesen Host hat.
"""
from __future__ import annotations

import os
import time
from typing import Any

from psycopg.types.json import Jsonb

from . import db

VORGABEN: dict[str, Any] = {
    "einbettung_url": "",
    "einbettung_modell": "",
    "einbettung_schluessel": "",
    "praefix_eintrag": "",
    "praefix_suche": "",
    "ablauf_tage": 180,
    "kontingent_mensch": 5000,
    "kontingent_sitzung": 500,
    "speichern_fragen": True,
}

# Das JSON-Schema für OTAs Formular (Manifest). Reihenfolge = Anzeige.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "einbettung_url": {
            "type": "string", "format": "uri", "title": "Embedding-Server",
            "description": "OpenAI-kompatibel (/v1/embeddings), mit /v1, z. B. "
                           "http://192.168.1.5:8000/v1 (vLLM) oder http://192.168.1.5:8080/v1 "
                           "(llama.cpp, auch hinter llama-swap). Leer: nur Wortsuche."},
        "einbettung_modell": {
            "type": "string", "title": "Embedding-Modell",
            "description": "z. B. BAAI/bge-m3 (vLLM) oder nomic-embed-text (llama.cpp)"},
        "einbettung_schluessel": {
            "type": "string", "title": "API-Schlüssel", "x-geheim": True,
            "description": "Nur, wenn der Server einen verlangt."},
        "praefix_eintrag": {
            "type": "string", "title": "Präfix für Einträge",
            "description": "Manche Modelle erwarten einen Vorsatz, z. B. nomic-embed-text "
                           "„search_document: “. Leer bei bge-m3 und den meisten anderen."},
        "praefix_suche": {
            "type": "string", "title": "Präfix für Suchanfragen",
            "description": "z. B. nomic-embed-text „search_query: “."},
        "ablauf_tage": {
            "type": "integer", "minimum": 0, "maximum": 3650, "title": "Ablauf (Tage)",
            "description": "Sitzungen ohne Aktivität so lange aufheben. 0: nie löschen."},
        "kontingent_mensch": {
            "type": "integer", "minimum": 10, "maximum": 1000000, "title": "Einträge je Mensch"},
        "kontingent_sitzung": {
            "type": "integer", "minimum": 10, "maximum": 100000, "title": "Einträge je Sitzung"},
        "speichern_fragen": {
            "type": "boolean", "title": "Vor dem Speichern fragen",
            "description": "opencode fragt bei jedem Speichern um Erlaubnis."},
    },
}

_zwischen: dict[str, Any] = {"zeit": 0.0, "werte": None}


def _lesen(schluessel: str) -> Any:
    with db.verbindung(system=True) as conn:
        zeile = conn.execute("SELECT wert FROM einstellung WHERE schluessel = %s",
                             (schluessel,)).fetchone()
    return zeile["wert"] if zeile else None


def _schreiben(schluessel: str, wert: Any) -> None:
    with db.verbindung(system=True) as conn:
        conn.execute("INSERT INTO einstellung (schluessel, wert) VALUES (%s, %s) "
                     "ON CONFLICT (schluessel) DO UPDATE SET wert = EXCLUDED.wert",
                     (schluessel, Jsonb(wert)))


def werte() -> dict[str, Any]:
    """Die Einstellungen, für ein paar Sekunden zwischengespeichert."""
    if _zwischen["werte"] is None or time.monotonic() - _zwischen["zeit"] > 5:
        gespeichert = _lesen("konfiguration") or {}
        _zwischen["werte"] = {**VORGABEN, **{k: v for k, v in gespeichert.items() if k in VORGABEN}}
        _zwischen["zeit"] = time.monotonic()
    return dict(_zwischen["werte"])


def setzen(neu: dict[str, Any]) -> dict[str, Any]:
    """Prüfen und speichern. Ein Geheimnis, das nicht mitkommt, bleibt."""
    alt = werte()
    for k, v in neu.items():
        if k not in VORGABEN:
            continue
        art = SCHEMA["properties"][k]["type"]
        if art == "integer":
            p = SCHEMA["properties"][k]
            try:
                v = int(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{k}: eine ganze Zahl") from exc
            if not p.get("minimum", v) <= v <= p.get("maximum", v):
                raise ValueError(f"{k}: zwischen {p['minimum']} und {p['maximum']}")
        elif art == "boolean":
            v = bool(v)
        else:
            if v is None:
                continue
            # Ein Präfix endet oft mit Leerzeichen („search_query: “).
            v = str(v)[:200] if k.startswith("praefix_") else str(v).strip()
        alt[k] = v
    if alt["einbettung_url"]:
        alt["einbettung_url"] = alt["einbettung_url"].rstrip("/")
        if not alt["einbettung_url"].startswith(("http://", "https://")):
            raise ValueError("Der Embedding-Server beginnt mit http:// oder https://")
    _schreiben("konfiguration", alt)
    _zwischen["werte"] = None
    return alt


def oeffentlich() -> dict[str, Any]:
    """Für OTA: ohne Geheimnisse."""
    w = werte()
    w["einbettung_schluessel"] = ""
    return w


def dienstgeheimnis() -> str:
    return str(_lesen("dienstgeheimnis") or "")


def dienstgeheimnis_setzen(geheimnis: str) -> None:
    _schreiben("dienstgeheimnis", geheimnis)


def kopplungsschluessel() -> str:
    return os.environ.get("GEDAECHTNIS_KOPPLUNGSSCHLUESSEL", "").strip()
