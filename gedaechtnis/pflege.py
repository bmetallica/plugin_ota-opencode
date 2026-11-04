"""Nächtliche Pflege, um 03:30 (GEDAECHTNIS_PFLEGE_UHRZEIT):

* Sitzungen ohne Aktivität seit `ablauf_tage` löschen (0: nie), nie das
  Hauptgedächtnis.
* Einträge und Abschnitte von Dokumenten ohne Einbettung nachholen, auch
  nach einem Modellwechsel.
* Widerrufene Tokens nach 7 Tagen, das Protokoll nach 180 Tagen löschen.

Läuft unter `system=True`: Pflege geht über alle Menschen.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from psycopg.types.json import Jsonb

from . import dokumente, einbettung, einstellungen, speicher
from .db import verbindung

log = logging.getLogger("gedaechtnis.pflege")


def einmal() -> dict[str, int]:
    w = einstellungen.werte()
    erg = {"sitzungen": 0, "eingebettet": 0, "tokens": 0}
    with verbindung(system=True) as conn:
        if w["ablauf_tage"]:
            erg["sitzungen"] = conn.execute(
                "DELETE FROM sitzung WHERE zuletzt_aktiv < now() - make_interval(days => %s) "
                "AND kennung <> %s",  # das Hauptgedächtnis läuft nie ab
                (int(w["ablauf_tage"]), speicher.HAUPT)).rowcount
        erg["tokens"] = conn.execute(
            "DELETE FROM token WHERE widerrufen < now() - interval '7 days'").rowcount
        conn.execute("DELETE FROM protokoll WHERE zeit < now() - interval '180 days'")
    modell = einbettung.modell()
    if modell:
        while True:
            with verbindung(system=True) as conn:
                offen = conn.execute(
                    "SELECT id, inhalt FROM eintrag WHERE modell IS DISTINCT FROM %s LIMIT 64",
                    (modell,)).fetchall()
            if not offen:
                break
            vektoren = einbettung.einbetten([e["inhalt"] for e in offen], zeitlimit=60.0)
            if not vektoren:
                break
            with verbindung(system=True) as conn:
                for e, v in zip(offen, vektoren):
                    conn.execute("UPDATE eintrag SET einbettung = %s::vector, modell = %s WHERE id = %s",
                                 (einbettung.als_text(v), modell, e["id"]))
            erg["eingebettet"] += len(offen)
        # Dasselbe für die Abschnitte der Dokumente.
        while (n := dokumente.nachholen_einbettung(modell)):
            erg["eingebettet"] += n
    with verbindung(system=True) as conn:
        conn.execute("INSERT INTO einstellung (schluessel, wert) VALUES ('pflege_zuletzt', %s) "
                     "ON CONFLICT (schluessel) DO UPDATE SET wert = EXCLUDED.wert",
                     (Jsonb({"zeit": datetime.now().isoformat(timespec="seconds"), **erg}),))
    log.info("Pflege: %s", erg)
    return erg


async def schleife() -> None:
    uhrzeit = os.environ.get("GEDAECHTNIS_PFLEGE_UHRZEIT", "03:30")
    stunde, minute = (int(x) for x in uhrzeit.split(":"))
    zuletzt = None
    nachholen = 0.0
    while True:
        await asyncio.sleep(60)
        jetzt = datetime.now()
        try:
            if (jetzt.hour, jetzt.minute) >= (stunde, minute) and zuletzt != jetzt.date():
                zuletzt = jetzt.date()
                await asyncio.to_thread(einmal)
            # Zwischendurch stündlich: Einbettungen nachholen, die beim
            # Speichern fehlten (Server kurz weg).
            elif einbettung.modell() and jetzt.timestamp() - nachholen > 3600:
                nachholen = jetzt.timestamp()
                await asyncio.to_thread(_nachholen)
        except Exception as exc:  # noqa: BLE001 — die Schleife darf nie sterben
            log.warning("Pflege fehlgeschlagen: %s", exc)


def _nachholen() -> None:
    with verbindung(system=True) as conn:
        offen = conn.execute(
            "SELECT (SELECT count(*) FROM eintrag WHERE modell IS DISTINCT FROM %s) "
            "+ (SELECT count(*) FROM dokument_abschnitt WHERE modell IS DISTINCT FROM %s) AS n",
            (einbettung.modell(), einbettung.modell())).fetchone()["n"]
    if offen:
        einmal()
