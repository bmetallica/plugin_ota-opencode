"""Embeddings vom eigenen Server (OpenAI-kompatibel, z. B. vLLM).

Fällt der Server aus, ist das kein Fehler für das Modell: gespeichert wird
ohne Einbettung, gesucht nur mit der Wortsuche, und die Pflege holt das
Einbetten nach (pflege.py).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from . import einstellungen

log = logging.getLogger("gedaechtnis.einbettung")

# Nach einem Fehler eine Weile nicht fragen: Sonst wartet jedes Speichern
# auf das Zeitlimit eines Servers, der gerade nicht da ist.
_PAUSE = 60.0
_zustand: dict[str, Any] = {"fehler_seit": 0.0, "meldung": ""}


def modell() -> str:
    """Kennung der aktuellen Einbettung — oder leer, wenn Embeddings aus sind.
    Die Präfixe gehören dazu: Mit anderem Vorsatz liegen die Vektoren
    woanders, und gemischt verglichen hieße das schlechte Treffer."""
    w = einstellungen.werte()
    if not (w["einbettung_url"] and w["einbettung_modell"]):
        return ""
    if w["praefix_eintrag"] or w["praefix_suche"]:
        return f"{w['einbettung_modell']}|{w['praefix_eintrag']}|{w['praefix_suche']}"
    return w["einbettung_modell"]


def _anfrage(texte: list[str], zeitlimit: float) -> list[list[float]]:
    w = einstellungen.werte()
    kopf = {"Authorization": f"Bearer {w['einbettung_schluessel']}"} if w["einbettung_schluessel"] else {}
    # Ohne Proxy aus der Umgebung: Der Server steht im eigenen Netz.
    with httpx.Client(timeout=zeitlimit, trust_env=False) as c:
        r = c.post(f"{w['einbettung_url']}/embeddings", headers=kopf,
                   json={"model": w["einbettung_modell"], "input": texte})
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    daten = sorted(r.json().get("data") or [], key=lambda d: d.get("index", 0))
    if len(daten) != len(texte):
        raise RuntimeError("Antwort passt nicht zur Anfrage")
    return [[float(x) for x in d["embedding"]] for d in daten]


def einbetten(texte: list[str], zeitlimit: float = 10.0, suche: bool = False) -> list[list[float]] | None:
    """Vektoren oder None (aus, Pause nach Fehler, Fehler). `suche`: eine
    Suchanfrage, kein Eintrag — mit dem Präfix für Suchanfragen."""
    if not modell() or not texte:
        return None
    w = einstellungen.werte()
    praefix = w["praefix_suche"] if suche else w["praefix_eintrag"]
    texte = [praefix + t for t in texte]
    if _zustand["fehler_seit"] and time.monotonic() - _zustand["fehler_seit"] < _PAUSE:
        return None
    try:
        vektoren = _anfrage(texte, zeitlimit)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        if not _zustand["fehler_seit"]:
            log.warning("Embedding-Server nicht erreichbar: %s", exc)
        _zustand.update(fehler_seit=time.monotonic(), meldung=str(exc)[:200])
        return None
    if _zustand["fehler_seit"]:
        log.info("Embedding-Server wieder erreichbar")
    _zustand.update(fehler_seit=0.0, meldung="")
    return vektoren


def testen() -> dict[str, Any]:
    """Für OTAs Knopf „Testen“ und die Gesundheit: ein Vektor, seine Länge."""
    if not modell():
        return {"an": False}
    beginn = time.monotonic()
    try:
        v = _anfrage(["Test"], 15.0)[0]
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        return {"an": True, "ok": False, "meldung": str(exc)[:300]}
    _zustand.update(fehler_seit=0.0, meldung="")
    return {"an": True, "ok": True, "dimension": len(v), "ms": int((time.monotonic() - beginn) * 1000)}


def zustand() -> dict[str, Any]:
    return {"an": bool(modell()), "erreichbar": not _zustand["fehler_seit"],
            "meldung": _zustand["meldung"]}


def als_text(v: list[float]) -> str:
    """pgvector nimmt Vektoren als Text '[1,2,3]'."""
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"
