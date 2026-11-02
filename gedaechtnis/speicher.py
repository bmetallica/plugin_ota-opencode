"""Speichern, Suchen, Ändern — immer für einen Menschen und eine Sitzung.

Die Werkzeuge (mcp_dienst.py) und die Ansicht (ansicht.py) rufen nur hier
hinein. Jede Funktion öffnet ihre Transaktion mit `db.verbindung(mensch)`;
die Datenbank lässt dann nur dessen Zeilen durch. Die Sitzung trennt dieses
Modul selbst (WHERE sitzung = …): Das ist Ordnung, keine Sicherheitsgrenze —
es sind alles Daten desselben Menschen.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import einbettung, einstellungen, filter
from .db import verbindung

ARTEN = ("fakt", "vorliebe", "entscheidung", "ablauf", "stand", "notiz")
# Vorlieben und Fakten altern nicht; ein Stand der Arbeit schon.
ZEITLOS = ("fakt", "vorliebe")
LAENGE = 2000
AEHNLICH = 0.92


class Abgelehnt(Exception):
    """Die Meldung geht so an das Modell bzw. den Menschen."""


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _protokoll(conn, mensch: str, aktion: str, sitzung: str | None = None,
               eintrag: str | None = None, anzahl: int | None = None) -> None:
    conn.execute("INSERT INTO protokoll (mensch, aktion, sitzung, eintrag, anzahl) "
                 "VALUES (%s, %s, %s, %s, %s)", (mensch, aktion, sitzung, eintrag, anzahl))


# --------------------------------------------------------------------------
# Sitzungen
# --------------------------------------------------------------------------

def sitzung_merken(mensch: str, kennung: str, titel: str, verzeichnis: str,
                   arbeitsplatz: str | None, arbeitsplatz_name: str) -> None:
    """Legt die Sitzung an oder frischt Titel, Verzeichnis und Aktivität auf."""
    with verbindung(mensch) as conn:
        conn.execute(
            "INSERT INTO sitzung (mensch, kennung, titel, verzeichnis, arbeitsplatz, arbeitsplatz_name) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (mensch, kennung) DO UPDATE SET "
            "  titel = CASE WHEN EXCLUDED.titel <> '' THEN EXCLUDED.titel ELSE sitzung.titel END, "
            "  verzeichnis = CASE WHEN EXCLUDED.verzeichnis <> '' THEN EXCLUDED.verzeichnis ELSE sitzung.verzeichnis END, "
            "  arbeitsplatz = EXCLUDED.arbeitsplatz, arbeitsplatz_name = EXCLUDED.arbeitsplatz_name, "
            "  zuletzt_aktiv = now()",
            (mensch, kennung[:200], titel[:300], verzeichnis[:500], arbeitsplatz, arbeitsplatz_name[:200]))


def sitzungen(mensch: str, arbeitsplaetze: list[str] | None = None) -> list[dict[str, Any]]:
    """Die Sitzungen eines Menschen, neueste zuerst; auf Wunsch nur die, die
    zuletzt in einem dieser Arbeitsplätze benutzt wurden."""
    sql = ("SELECT s.*, (SELECT count(*) FROM eintrag e WHERE e.mensch = s.mensch "
           "AND e.sitzung = s.kennung) AS anzahl FROM sitzung s WHERE s.mensch = %s")
    werte: list[Any] = [mensch]
    if arbeitsplaetze is not None:
        sql += " AND s.arbeitsplatz = ANY(%s::uuid[])"
        werte.append(arbeitsplaetze)
    sql += " ORDER BY s.zuletzt_aktiv DESC"
    with verbindung(mensch) as conn:
        return conn.execute(sql, werte).fetchall()


def sitzung(mensch: str, kennung: str) -> dict[str, Any] | None:
    with verbindung(mensch) as conn:
        return conn.execute("SELECT * FROM sitzung WHERE mensch = %s AND kennung = %s",
                            (mensch, kennung)).fetchone()


def sitzung_umbenennen(mensch: str, kennung: str, titel: str) -> None:
    with verbindung(mensch) as conn:
        conn.execute("UPDATE sitzung SET eigener_titel = %s WHERE mensch = %s AND kennung = %s",
                     (titel.strip()[:300] or None, mensch, kennung))


def sitzung_loeschen(mensch: str, kennung: str) -> int:
    with verbindung(mensch) as conn:
        n = conn.execute("SELECT count(*) AS n FROM eintrag WHERE mensch = %s AND sitzung = %s",
                         (mensch, kennung)).fetchone()["n"]
        conn.execute("DELETE FROM sitzung WHERE mensch = %s AND kennung = %s", (mensch, kennung))
        _protokoll(conn, mensch, "sitzung_geloescht", kennung, anzahl=n)
    return n


def alles_loeschen(mensch: str) -> int:
    with verbindung(mensch) as conn:
        n = conn.execute("SELECT count(*) AS n FROM eintrag WHERE mensch = %s", (mensch,)).fetchone()["n"]
        conn.execute("DELETE FROM sitzung WHERE mensch = %s", (mensch,))
        _protokoll(conn, mensch, "alles_geloescht", anzahl=n)
    return n


# --------------------------------------------------------------------------
# Einträge
# --------------------------------------------------------------------------

def kurz(eintrag_id: Any) -> str:
    return str(eintrag_id).replace("-", "")[:8]


def _finden(conn, mensch: str, sitzung: str | None, kennung: str) -> dict[str, Any]:
    """Ein Eintrag über seine Kurzkennung (8 Zeichen) oder die ganze UUID."""
    k = kennung.strip().strip("[]").lower().replace("-", "")
    if not re.fullmatch(r"[0-9a-f]{4,32}", k):
        raise Abgelehnt(f"„{kennung}“ ist keine Kennung eines Eintrags.")
    sql = "SELECT * FROM eintrag WHERE mensch = %s AND replace(id::text, '-', '') LIKE %s"
    werte: list[Any] = [mensch, k + "%"]
    if sitzung is not None:
        sql += " AND sitzung = %s"
        werte.append(sitzung)
    treffer = conn.execute(sql + " LIMIT 2", werte).fetchall()
    if not treffer:
        raise Abgelehnt(f"Keinen Eintrag [{kennung}] gefunden.")
    if len(treffer) > 1:
        raise Abgelehnt(f"[{kennung}] ist nicht eindeutig — bitte mehr Zeichen angeben.")
    return treffer[0]


def _pruefen(inhalt: str, art: str) -> tuple[str, str]:
    inhalt = (inhalt or "").strip()
    if not inhalt:
        raise Abgelehnt("Der Eintrag ist leer.")
    if len(inhalt) > LAENGE:
        raise Abgelehnt(f"Zu lang ({len(inhalt)} Zeichen, höchstens {LAENGE}). Kürzer fassen, "
                        "ein Gedanke je Eintrag.")
    art = (art or "notiz").strip().lower()
    if art not in ARTEN:
        art = "notiz"
    grund = filter.geheimnis(inhalt)
    if grund:
        raise Abgelehnt(f"Nicht gespeichert: Der Text enthält offenbar Zugangsdaten ({grund}). "
                        "Keine Zugangsdaten speichern.")
    return inhalt, art


def _schlagworte(roh: Any) -> list[str]:
    if isinstance(roh, str):
        roh = re.split(r"[,;]", roh)
    if not isinstance(roh, list):
        return []
    return sorted({str(s).strip().lower()[:40] for s in roh if str(s).strip()})[:10]


def speichern(mensch: str, sitzung: str, inhalt: str, art: str, schlagworte: Any = None,
              quelle: str = "werkzeug") -> tuple[str, str]:
    """('neu'|'aktualisiert', Kurzkennung). Sehr Ähnliches wird aktualisiert."""
    inhalt, art = _pruefen(inhalt, art)
    worte = _schlagworte(schlagworte)
    vektor = einbettung.einbetten([inhalt])
    modell = einbettung.modell() if vektor else None
    w = einstellungen.werte()
    with verbindung(mensch) as conn:
        doppelt = None
        if vektor:
            doppelt = conn.execute(
                "SELECT id, 1 - (einbettung <=> %s::vector) AS nah FROM eintrag "
                "WHERE mensch = %s AND sitzung = %s AND modell = %s "
                "ORDER BY einbettung <=> %s::vector LIMIT 1",
                (einbettung.als_text(vektor[0]), mensch, sitzung, modell,
                 einbettung.als_text(vektor[0]))).fetchone()
            if doppelt and doppelt["nah"] < AEHNLICH:
                doppelt = None
        if doppelt is None:
            doppelt = conn.execute(
                "SELECT id FROM eintrag WHERE mensch = %s AND sitzung = %s "
                "AND lower(inhalt) = lower(%s) LIMIT 1", (mensch, sitzung, inhalt)).fetchone()
        if doppelt:
            conn.execute(
                "UPDATE eintrag SET inhalt = %s, art = %s, "
                "schlagworte = (SELECT ARRAY(SELECT DISTINCT unnest(schlagworte || %s::text[]))), "
                "einbettung = %s::vector, modell = %s, geaendert = now(), quelle = %s WHERE id = %s",
                (inhalt, art, worte, einbettung.als_text(vektor[0]) if vektor else None, modell,
                 quelle, doppelt["id"]))
            _protokoll(conn, mensch, "aktualisiert", sitzung, doppelt["id"])
            return "aktualisiert", kurz(doppelt["id"])
        zahlen = conn.execute(
            "SELECT count(*) AS alle, count(*) FILTER (WHERE sitzung = %s) AS hier "
            "FROM eintrag WHERE mensch = %s", (sitzung, mensch)).fetchone()
        if zahlen["hier"] >= w["kontingent_sitzung"]:
            raise Abgelehnt(f"Das Gedächtnis dieser Sitzung ist voll ({zahlen['hier']} Einträge). "
                            "Erst Überholtes mit memory_forget entfernen.")
        if zahlen["alle"] >= w["kontingent_mensch"]:
            raise Abgelehnt(f"Dein Gedächtnis ist voll ({zahlen['alle']} Einträge über alle "
                            "Sitzungen). In OTA unter Mein Konto → Gedächtnis aufräumen.")
        neu = uuid.uuid4()
        conn.execute(
            "INSERT INTO eintrag (id, mensch, sitzung, art, inhalt, schlagworte, einbettung, modell, quelle) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)",
            (neu, mensch, sitzung, art, inhalt, worte,
             einbettung.als_text(vektor[0]) if vektor else None, modell, quelle))
        _protokoll(conn, mensch, "gespeichert", sitzung, str(neu))
    return "neu", kurz(neu)


def aendern(mensch: str, sitzung: str | None, kennung: str, inhalt: str | None = None,
            art: str | None = None, schlagworte: Any = None, angeheftet: bool | None = None,
            stand: str | None = None, quelle: str = "werkzeug") -> str:
    """Ändern. `sitzung=None`: in jeder eigenen (Ansicht). `stand`: der Wert
    von `geaendert`, den die Ansicht gesehen hat — passt er nicht mehr, hat
    jemand anderes (opencode) inzwischen geändert."""
    with verbindung(mensch) as conn:
        e = _finden(conn, mensch, sitzung, kennung)
        if stand and e["geaendert"].isoformat() != stand:
            raise Abgelehnt("Der Eintrag wurde inzwischen geändert. Bitte neu laden.")
        neu_inhalt, neu_art = _pruefen(inhalt if inhalt is not None else e["inhalt"],
                                       art if art is not None else e["art"])
        vektor = e["einbettung"]
        modell = e["modell"]
        if neu_inhalt != e["inhalt"]:
            v = einbettung.einbetten([neu_inhalt])
            vektor = einbettung.als_text(v[0]) if v else None
            modell = einbettung.modell() if v else None
        conn.execute(
            "UPDATE eintrag SET inhalt = %s, art = %s, schlagworte = %s, angeheftet = %s, "
            "einbettung = %s::vector, modell = %s, geaendert = now(), quelle = %s WHERE id = %s",
            (neu_inhalt, neu_art,
             _schlagworte(schlagworte) if schlagworte is not None else e["schlagworte"],
             e["angeheftet"] if angeheftet is None else bool(angeheftet),
             vektor, modell, quelle, e["id"]))
        _protokoll(conn, mensch, "geaendert", e["sitzung"], e["id"])
        return kurz(e["id"])


def vergessen(mensch: str, sitzung: str | None, kennungen: list[str]) -> int:
    n = 0
    with verbindung(mensch) as conn:
        for k in kennungen:
            e = _finden(conn, mensch, sitzung, k)
            conn.execute("DELETE FROM eintrag WHERE id = %s", (e["id"],))
            _protokoll(conn, mensch, "geloescht", e["sitzung"], e["id"])
            n += 1
    return n


def uebertragen(mensch: str, kennungen: list[str], ziel: str, verschieben: bool) -> int:
    """Einträge in eine andere eigene Sitzung kopieren oder verschieben."""
    w = einstellungen.werte()
    with verbindung(mensch) as conn:
        if not conn.execute("SELECT 1 FROM sitzung WHERE mensch = %s AND kennung = %s",
                            (mensch, ziel)).fetchone():
            raise Abgelehnt("Diese Sitzung gibt es nicht.")
        hier = conn.execute("SELECT count(*) AS n FROM eintrag WHERE mensch = %s AND sitzung = %s",
                            (mensch, ziel)).fetchone()["n"]
        n = 0
        for k in kennungen:
            e = _finden(conn, mensch, None, k)
            if e["sitzung"] == ziel:
                continue
            if verschieben:
                conn.execute("UPDATE eintrag SET sitzung = %s, geaendert = now() WHERE id = %s",
                             (ziel, e["id"]))
            else:
                if hier + n >= w["kontingent_sitzung"]:
                    raise Abgelehnt("Die Zielsitzung ist voll.")
                conn.execute(
                    "INSERT INTO eintrag (id, mensch, sitzung, art, inhalt, schlagworte, angeheftet, "
                    "einbettung, modell, quelle) SELECT %s, mensch, %s, art, inhalt, schlagworte, "
                    "angeheftet, einbettung, modell, 'ansicht' FROM eintrag WHERE id = %s",
                    (uuid.uuid4(), ziel, e["id"]))
            n += 1
        _protokoll(conn, mensch, "verschoben" if verschieben else "kopiert", ziel, anzahl=n)
    return n


def eintraege(mensch: str, sitzung: str) -> list[dict[str, Any]]:
    with verbindung(mensch) as conn:
        return conn.execute(
            "SELECT id, art, inhalt, schlagworte, angeheftet, angelegt, geaendert, genutzt, quelle "
            "FROM eintrag WHERE mensch = %s AND sitzung = %s "
            "ORDER BY angeheftet DESC, geaendert DESC", (mensch, sitzung)).fetchall()


def ausfuhr(mensch: str, sitzung: str | None = None) -> list[dict[str, Any]]:
    """Alles (oder eine Sitzung) für den Export, ohne Vektoren."""
    with verbindung(mensch) as conn:
        sql = "SELECT * FROM sitzung WHERE mensch = %s"
        werte: list[Any] = [mensch]
        if sitzung:
            sql += " AND kennung = %s"
            werte.append(sitzung)
        raus = []
        for s in conn.execute(sql + " ORDER BY angelegt", werte).fetchall():
            e = conn.execute(
                "SELECT id, art, inhalt, schlagworte, angeheftet, angelegt, geaendert FROM eintrag "
                "WHERE mensch = %s AND sitzung = %s ORDER BY angelegt", (mensch, s["kennung"])).fetchall()
            raus.append({"sitzung": s, "eintraege": e})
        return raus


# --------------------------------------------------------------------------
# Suchen und Abrufen
# --------------------------------------------------------------------------

def _tsquery(anfrage: str) -> str:
    """Wörter mit ODER verknüpft: Ein Modell fragt in ganzen Sätzen, und
    UND hieße dann fast immer „nichts gefunden“."""
    worte = []
    for w in re.findall(r"\w{2,}", anfrage.lower())[:24]:
        worte.append(f"{w}:*" if len(w) >= 4 else w)
    return " | ".join(dict.fromkeys(worte))


def _gewicht(e: dict[str, Any], jetzt_: datetime) -> float:
    g = 1.0
    if e["art"] not in ZEITLOS:
        tage = max(0.0, (jetzt_ - e["geaendert"]).total_seconds() / 86400)
        g *= 0.5 + 0.5 * math.pow(2, -tage / 30)
    return g * (1 + min(e["genutzt"], 20) * 0.01)


def suchen(mensch: str, sitzung: str, anfrage: str, grenze: int = 10) -> list[dict[str, Any]]:
    anfrage = (anfrage or "").strip()
    grenze = max(1, min(int(grenze or 10), 50))
    rang: dict[Any, float] = {}
    zeilen: dict[Any, dict[str, Any]] = {}
    with verbindung(mensch) as conn:
        q = _tsquery(anfrage)
        if q:
            for i, e in enumerate(conn.execute(
                    "SELECT e.*, ts_rank(e.such_text, q.query) AS r FROM eintrag e "
                    "CROSS JOIN (SELECT to_tsquery('german', %s) || to_tsquery('simple', %s) AS query) q "
                    "WHERE e.mensch = %s AND e.sitzung = %s AND (e.such_text @@ q.query "
                    "  OR %s = ANY(e.schlagworte)) ORDER BY r DESC LIMIT 30",
                    (q, q, mensch, sitzung, anfrage.lower())).fetchall()):
                rang[e["id"]] = rang.get(e["id"], 0) + 1 / (60 + i)
                zeilen[e["id"]] = e
        vektor = einbettung.einbetten([anfrage], zeitlimit=5.0, suche=True) if anfrage else None
        if vektor:
            v = einbettung.als_text(vektor[0])
            for i, e in enumerate(conn.execute(
                    "SELECT * FROM eintrag WHERE mensch = %s AND sitzung = %s AND modell = %s "
                    "AND einbettung IS NOT NULL ORDER BY einbettung <=> %s::vector LIMIT 30",
                    (mensch, sitzung, einbettung.modell(), v)).fetchall()):
                rang[e["id"]] = rang.get(e["id"], 0) + 1 / (60 + i)
                zeilen[e["id"]] = e
    j = jetzt()
    reihe = sorted(rang, key=lambda k: rang[k] * _gewicht(zeilen[k], j), reverse=True)
    return [zeilen[k] for k in reihe[:grenze]]


def abrufen(mensch: str, sitzung: str, anfrage: str = "", budget: int = 800,
            vorschau: bool = False) -> list[dict[str, Any]]:
    """Was zu Beginn in den Kontext soll: angeheftet, dann passend, dann neu —
    bis das Budget (grob 4 Zeichen je Token) erschöpft ist."""
    zeichen = max(200, min(int(budget or 800), 8000)) * 4
    with verbindung(mensch) as conn:
        alle = conn.execute(
            "SELECT * FROM eintrag WHERE mensch = %s AND sitzung = %s", (mensch, sitzung)).fetchall()
    j = jetzt()
    angeheftet = sorted((e for e in alle if e["angeheftet"]), key=lambda e: e["geaendert"], reverse=True)
    passend = suchen(mensch, sitzung, anfrage, 30) if anfrage else []
    rest = sorted(alle, key=lambda e: _gewicht(e, j) * e["geaendert"].timestamp(), reverse=True)
    raus, gesehen, laenge = [], set(), 0
    for e in [*angeheftet, *passend, *rest]:
        if e["id"] in gesehen:
            continue
        zeile = len(zeile_fuer(e))
        if laenge + zeile > zeichen and raus:
            if e["angeheftet"]:
                continue
            break
        raus.append(e)
        gesehen.add(e["id"])
        laenge += zeile
    if raus and not vorschau:
        with verbindung(mensch) as conn:
            conn.execute("UPDATE eintrag SET genutzt = genutzt + 1, zuletzt_genutzt = now() "
                         "WHERE id = ANY(%s)", ([e["id"] for e in raus],))
    return raus


def zeile_fuer(e: dict[str, Any]) -> str:
    """Eine Zeile je Eintrag: wenig Tokens, alles Nötige."""
    datum = e["geaendert"].strftime("%Y-%m-%d")
    marke = ", angeheftet" if e.get("angeheftet") else ""
    return f"[{kurz(e['id'])}] ({e['art']}, {datum}{marke}) {e['inhalt']}"
