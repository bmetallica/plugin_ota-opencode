"""Wer fragt? Tokens der Arbeitsplätze und Signaturen von OTA.

Zwei Wege herein, beide ohne eigene Anmeldung:

* **Werkzeuge (`/mcp`)** — opencode schickt das Token des Arbeitsplatzes als
  Bearer. Es ist an genau einen Menschen gebunden; welche Sitzung, sagt das
  Plugin ota-sitzung in OTA.
* **Ansicht (`/ansicht/…`)** — der Browser kommt über OTA. Traefik fragt OTA
  (forwardAuth), und OTA setzt Kopfzeilen mit dem Menschen, seinen
  Arbeitsplätzen und einer HMAC-Signatur mit dem Dienstgeheimnis. Ohne
  gültige Signatur glaubt die Ansicht keiner Kopfzeile — auch nicht einer,
  die ein anderer Dienst im Netz ota_erweiterungen selbst setzt.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import unquote

from . import einstellungen
from .db import verbindung

_zwischen: dict[bytes, tuple[float, "Wer | None"]] = {}
_SEKUNDEN = 30.0


@dataclass(frozen=True)
class Wer:
    mensch: str
    arbeitsplatz: str = ""
    arbeitsplatz_name: str = ""


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def token_ausstellen(mensch: str, name: str, arbeitsplatz: str, arbeitsplatz_name: str) -> str:
    """Ein neues Token für einen Arbeitsplatz; ältere desselben werden widerrufen."""
    token = secrets.token_urlsafe(32)
    with verbindung(system=True) as conn:
        conn.execute("INSERT INTO mensch (id, name) VALUES (%s, %s) "
                     "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name", (mensch, name[:200]))
        conn.execute("UPDATE token SET widerrufen = now() WHERE arbeitsplatz = %s AND widerrufen IS NULL",
                     (arbeitsplatz,))
        conn.execute("INSERT INTO token (hash, mensch, arbeitsplatz, arbeitsplatz_name) "
                     "VALUES (%s, %s, %s, %s)", (_hash(token), mensch, arbeitsplatz, arbeitsplatz_name[:200]))
    _zwischen.clear()
    return token


def token_widerrufen(arbeitsplatz: str) -> int:
    with verbindung(system=True) as conn:
        n = conn.execute("UPDATE token SET widerrufen = now() WHERE arbeitsplatz = %s "
                         "AND widerrufen IS NULL", (arbeitsplatz,)).rowcount
    _zwischen.clear()
    return n


def aktive_arbeitsplaetze() -> list[str]:
    with verbindung(system=True) as conn:
        return [str(z["arbeitsplatz"]) for z in conn.execute(
            "SELECT DISTINCT arbeitsplatz FROM token WHERE widerrufen IS NULL").fetchall()]


def aus_token(kopf: str) -> Wer | None:
    """Den Menschen hinter `Authorization: Bearer …` — oder None."""
    if not kopf.lower().startswith("bearer "):
        return None
    token = kopf[7:].strip()
    if not token:
        return None
    h = _hash(token)
    eintrag = _zwischen.get(h)
    if eintrag and time.monotonic() - eintrag[0] < _SEKUNDEN:
        return eintrag[1]
    with verbindung(system=True) as conn:
        z = conn.execute(
            "SELECT t.mensch, t.arbeitsplatz, t.arbeitsplatz_name FROM token t "
            "JOIN mensch m ON m.id = t.mensch "
            "WHERE t.hash = %s AND t.widerrufen IS NULL AND NOT m.gesperrt", (h,)).fetchone()
    wer = Wer(str(z["mensch"]), str(z["arbeitsplatz"]), z["arbeitsplatz_name"]) if z else None
    if len(_zwischen) > 5000:
        _zwischen.clear()
    _zwischen[h] = (time.monotonic(), wer)
    return wer


def vergessen() -> None:
    """Nach Sperren, Löschen, Widerrufen: nichts Zwischengespeichertes mehr."""
    _zwischen.clear()


def dienst_ok(kopf: str) -> bool:
    """Ist das OTA (Bearer = Dienstgeheimnis)?"""
    geheim = einstellungen.dienstgeheimnis()
    return bool(geheim) and hmac.compare_digest(kopf.encode(), f"Bearer {geheim}".encode())


@dataclass(frozen=True)
class Betrachter:
    mensch: str
    name: str
    arbeitsplaetze: tuple[str, ...]


def signatur(geheim: str, mensch: str, name: str, arbeitsplaetze: str, zeit: str) -> str:
    """Muss genau so in OTA berechnet werden (api/ota/erweiterungen.py)."""
    nachricht = f"{mensch}|{name}|{arbeitsplaetze}|{zeit}".encode()
    return hmac.new(geheim.encode(), nachricht, hashlib.sha256).hexdigest()


def aus_kopfzeilen(kopf: dict[str, str]) -> Betrachter | None:
    geheim = einstellungen.dienstgeheimnis()
    if not geheim:
        return None
    mensch = kopf.get("x-ota-nutzer", "")
    name = kopf.get("x-ota-name", "")
    plaetze = kopf.get("x-ota-arbeitsplaetze", "")
    zeit = kopf.get("x-ota-zeit", "")
    sig = kopf.get("x-ota-signatur", "")
    if not (mensch and zeit and sig):
        return None
    try:
        if abs(time.time() - int(zeit)) > 300:
            return None
    except ValueError:
        return None
    if not hmac.compare_digest(sig, signatur(geheim, mensch, name, plaetze, zeit)):
        return None
    with verbindung(system=True) as conn:
        z = conn.execute("SELECT gesperrt FROM mensch WHERE id = %s", (mensch,)).fetchone()
        if z and z["gesperrt"]:
            return None
        if not z:
            conn.execute("INSERT INTO mensch (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (mensch, unquote(name)[:200]))
    return Betrachter(mensch, unquote(name), tuple(p for p in plaetze.split(",") if p))
