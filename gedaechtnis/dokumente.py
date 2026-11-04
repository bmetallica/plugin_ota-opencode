"""Dokumente je Mensch: hochladen, im Hintergrund auslesen, durchsuchen.

Ein Dokument wird einmal verarbeitet und dann je Sitzung freigegeben
(`dokument_freigabe`) oder für alle Sitzungen (`fuer_alle`). opencode sucht
darin mit `document_search` und sieht nur, was für seine Sitzung frei ist.

Das Auslesen läuft in einem eigenen Prozess (auslesen.py) mit Grenzen für
Zeit und Speicher. Zugangsdaten im Text werden geschwärzt statt das
Dokument abzulehnen: Ein Handbuch mit einem Beispiel-Token soll trotzdem
nutzbar sein. Fehlen Embeddings (Server aus), sucht die Wortsuche, und die
Pflege holt das Einbetten nach.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import resource
import subprocess
import sys
import threading
import uuid
from typing import Any

from . import auslesen, einbettung, einstellungen, filter, speicher
from .db import verbindung
from .speicher import Abgelehnt

log = logging.getLogger("gedaechtnis.dokumente")

ABSCHNITT = 1000
UEBERLAPPUNG = 150
ZEITLIMIT = 180
SPEICHER_GRENZE = 1536 * 1024 * 1024
# Ein Treffer, wie er an das Modell geht: lang genug für den Zusammenhang.
TREFFER_LAENGE = 1200

_wecker = threading.Event()


def typ_von(name: str) -> str:
    endung = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if endung in ("doc", "xls", "ppt"):
        raise Abgelehnt(f".{endung} ist das alte Office-Format. Bitte als .{endung}x oder PDF speichern.")
    if endung not in auslesen.TYPEN:
        raise Abgelehnt(f"Dateien vom Typ .{endung or '?'} werden nicht unterstützt. Möglich: "
                        "PDF, DOCX, XLSX, PPTX, ODT, ODS, ODP, HTML, TXT, MD, CSV und andere Textdateien.")
    return endung


def _belegt(conn, mensch: str) -> int:
    z = conn.execute("SELECT coalesce(sum(groesse), 0) + coalesce(sum(zeichen), 0) AS n "
                     "FROM dokument WHERE mensch = %s", (mensch,)).fetchone()
    return int(z["n"])


def stand(mensch: str) -> dict[str, int]:
    w = einstellungen.werte()
    with verbindung(mensch) as conn:
        return {"belegt": _belegt(conn, mensch), "grenze": int(w["dokument_kontingent_mb"]) * 1048576,
                "datei_grenze": int(w["dokument_mb"]) * 1048576}


def hochladen(mensch: str, name: str, daten: bytes) -> dict[str, Any]:
    name = re.sub(r"[\x00-\x1f/\\]", "_", (name or "").strip())[-200:] or "Dokument"
    typ = typ_von(name)
    w = einstellungen.werte()
    if not daten:
        raise Abgelehnt("Die Datei ist leer.")
    if len(daten) > int(w["dokument_mb"]) * 1048576:
        raise Abgelehnt(f"Zu groß: höchstens {w['dokument_mb']} MB je Datei.")
    pruefsumme = hashlib.sha256(daten).digest()
    with verbindung(mensch) as conn:
        conn.execute("INSERT INTO mensch (id) VALUES (%s) ON CONFLICT DO NOTHING", (mensch,))
        da = conn.execute("SELECT id, name FROM dokument WHERE mensch = %s AND hash = %s",
                          (mensch, pruefsumme)).fetchone()
        if da:
            raise Abgelehnt(f"Dieses Dokument ist schon da: „{da['name']}“.")
        if _belegt(conn, mensch) + len(daten) > int(w["dokument_kontingent_mb"]) * 1048576:
            raise Abgelehnt(f"Deine Dokumente belegen schon fast {w['dokument_kontingent_mb']} MB. "
                            "Erst Altes löschen.")
        neu = uuid.uuid4()
        conn.execute("INSERT INTO dokument (id, mensch, name, typ, groesse, hash, original) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                     (neu, mensch, name, typ, len(daten), pruefsumme, daten))
        conn.execute("INSERT INTO protokoll (mensch, aktion, anzahl) VALUES (%s, 'dokument_neu', %s)",
                     (mensch, len(daten)))
    _wecker.set()
    return {"id": str(neu), "name": name}


def liste(mensch: str) -> list[dict[str, Any]]:
    with verbindung(mensch) as conn:
        return conn.execute(
            "SELECT d.id, d.name, d.typ, d.groesse, d.status, d.fehler, d.zeichen, d.geschwaerzt, "
            "d.fuer_alle, d.angelegt, "
            "(SELECT count(*) FROM dokument_abschnitt a WHERE a.dokument = d.id) AS abschnitte, "
            "(SELECT count(*) FROM dokument_freigabe f WHERE f.dokument = d.id) AS freigaben "
            "FROM dokument d WHERE d.mensch = %s ORDER BY d.angelegt DESC", (mensch,)).fetchall()


def original(mensch: str, dok: str) -> dict[str, Any] | None:
    with verbindung(mensch) as conn:
        return conn.execute("SELECT name, typ, original FROM dokument WHERE mensch = %s AND id = %s",
                            (mensch, dok)).fetchone()


def loeschen(mensch: str, dok: str) -> None:
    with verbindung(mensch) as conn:
        conn.execute("DELETE FROM dokument WHERE mensch = %s AND id = %s", (mensch, dok))
        conn.execute("INSERT INTO protokoll (mensch, aktion) VALUES (%s, 'dokument_geloescht')", (mensch,))


def alle_loeschen(mensch: str) -> int:
    with verbindung(mensch) as conn:
        return conn.execute("DELETE FROM dokument WHERE mensch = %s", (mensch,)).rowcount


def fuer_alle(mensch: str, dok: str, wert: bool) -> None:
    with verbindung(mensch) as conn:
        conn.execute("UPDATE dokument SET fuer_alle = %s WHERE mensch = %s AND id = %s",
                     (bool(wert), mensch, dok))


def freigeben(mensch: str, sitzung: str, dok: str, wert: bool) -> None:
    if sitzung == speicher.HAUPT:
        raise Abgelehnt("Für das Hauptgedächtnis gibt es „für alle Sitzungen“.")
    with verbindung(mensch) as conn:
        if not conn.execute("SELECT 1 FROM dokument WHERE mensch = %s AND id = %s", (mensch, dok)).fetchone() \
                or not conn.execute("SELECT 1 FROM sitzung WHERE mensch = %s AND kennung = %s",
                                    (mensch, sitzung)).fetchone():
            raise Abgelehnt("Dieses Dokument oder diese Sitzung gibt es nicht.")
        if wert:
            conn.execute("INSERT INTO dokument_freigabe (mensch, sitzung, dokument) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (mensch, sitzung, dok))
        else:
            conn.execute("DELETE FROM dokument_freigabe WHERE mensch = %s AND sitzung = %s AND dokument = %s",
                         (mensch, sitzung, dok))


def freigegeben(mensch: str, sitzung: str) -> set[str]:
    with verbindung(mensch) as conn:
        return {str(z["dokument"]) for z in conn.execute(
            "SELECT dokument FROM dokument_freigabe WHERE mensch = %s AND sitzung = %s",
            (mensch, sitzung)).fetchall()}


# --------------------------------------------------------------------------
# Was eine Sitzung sieht, und die Suche darin
# --------------------------------------------------------------------------

_SICHTBAR = ("SELECT id FROM dokument d WHERE d.mensch = %s AND d.status = 'fertig' AND (d.fuer_alle "
             "OR EXISTS (SELECT 1 FROM dokument_freigabe f WHERE f.dokument = d.id AND f.sitzung = %s))")


def fuer_sitzung(mensch: str, sitzung: str) -> list[dict[str, Any]]:
    """Die Dokumente, die diese Sitzung lesen darf, auch die noch in Arbeit."""
    with verbindung(mensch) as conn:
        return conn.execute(
            "SELECT d.id, d.name, d.typ, d.status, d.fuer_alle, "
            "(SELECT count(*) FROM dokument_abschnitt a WHERE a.dokument = d.id) AS abschnitte "
            "FROM dokument d WHERE d.mensch = %s AND (d.fuer_alle OR EXISTS (SELECT 1 FROM "
            "dokument_freigabe f WHERE f.dokument = d.id AND f.sitzung = %s)) ORDER BY d.name",
            (mensch, sitzung)).fetchall()


def suchen(mensch: str, sitzung: str, anfrage: str, grenze: int = 8) -> list[dict[str, Any]]:
    anfrage = (anfrage or "").strip()
    grenze = max(1, min(int(grenze or 8), 20))
    if not anfrage:
        return []
    rang: dict[int, float] = {}
    zeilen: dict[int, dict[str, Any]] = {}
    with verbindung(mensch) as conn:
        q = speicher._tsquery(anfrage)
        if q:
            for i, a in enumerate(conn.execute(
                    "SELECT a.id, a.quelle, a.inhalt, a.nummer, d.name FROM dokument_abschnitt a "
                    "JOIN dokument d ON d.id = a.dokument "
                    "CROSS JOIN (SELECT to_tsquery('german', %s) || to_tsquery('simple', %s) AS query) q "
                    f"WHERE a.mensch = %s AND a.dokument IN ({_SICHTBAR}) AND a.such_text @@ q.query "
                    "ORDER BY ts_rank(a.such_text, q.query) DESC LIMIT 40",
                    (q, q, mensch, mensch, sitzung)).fetchall()):
                rang[a["id"]] = rang.get(a["id"], 0) + 1 / (60 + i)
                zeilen[a["id"]] = a
        vektor = einbettung.einbetten([anfrage], zeitlimit=5.0, suche=True)
        if vektor:
            for i, a in enumerate(conn.execute(
                    "SELECT a.id, a.quelle, a.inhalt, a.nummer, d.name FROM dokument_abschnitt a "
                    "JOIN dokument d ON d.id = a.dokument "
                    f"WHERE a.mensch = %s AND a.dokument IN ({_SICHTBAR}) AND a.modell = %s "
                    "AND a.einbettung IS NOT NULL ORDER BY a.einbettung <=> %s::vector LIMIT 40",
                    (mensch, mensch, sitzung, einbettung.modell(),
                     einbettung.als_text(vektor[0]))).fetchall()):
                rang[a["id"]] = rang.get(a["id"], 0) + 1 / (60 + i)
                zeilen[a["id"]] = a
    reihe = sorted(rang, key=lambda k: rang[k], reverse=True)
    return [zeilen[k] for k in reihe[:grenze]]


def treffer_text(a: dict[str, Any]) -> str:
    quelle = f", {a['quelle']}" if a["quelle"] else ""
    inhalt = re.sub(r"\s+\n", "\n", a["inhalt"]).strip()
    if len(inhalt) > TREFFER_LAENGE:
        inhalt = inhalt[:TREFFER_LAENGE] + " …"
    return f"[{a['name']}{quelle}]\n{inhalt}"


# --------------------------------------------------------------------------
# Verarbeitung im Hintergrund
# --------------------------------------------------------------------------

def _grenzen() -> None:  # im Kindprozess, vor dem Start
    resource.setrlimit(resource.RLIMIT_AS, (SPEICHER_GRENZE, SPEICHER_GRENZE))
    resource.setrlimit(resource.RLIMIT_CPU, (ZEITLIMIT, ZEITLIMIT))


def _auslesen(daten: bytes, typ: str) -> list[dict[str, str]]:
    try:
        p = subprocess.run([sys.executable, "-m", "gedaechtnis.auslesen", typ], input=daten,
                           capture_output=True, timeout=ZEITLIMIT + 20, preexec_fn=_grenzen)
    except subprocess.TimeoutExpired as exc:
        raise Abgelehnt("Das Auslesen dauerte zu lange.") from exc
    try:
        erg = json.loads(p.stdout or b"{}")
    except ValueError:
        erg = {}
    if "teile" not in erg:
        raise Abgelehnt(erg.get("fehler") or "Das Auslesen ist abgebrochen (zu groß oder beschädigt).")
    return erg["teile"]


def zerlegen(teile: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Abschnitte von etwa ABSCHNITT Zeichen, an Absätzen getrennt und mit
    etwas Überlappung, damit ein Satz an der Grenze in beiden steht."""
    raus: list[tuple[str, str]] = []
    for teil in teile:
        absaetze = [a.strip() for a in re.split(r"\n\s*\n|\n(?=\S)", teil["text"]) if a.strip()]
        puffer = ""
        for a in absaetze:
            while len(a) > ABSCHNITT * 2:  # ein Riesenabsatz (Tabelle ohne Leerzeilen)
                raus.append((teil["quelle"], (puffer + "\n" + a[:ABSCHNITT]).strip()))
                puffer, a = "", a[ABSCHNITT - UEBERLAPPUNG:]
            if puffer and len(puffer) + len(a) > ABSCHNITT:
                raus.append((teil["quelle"], puffer.strip()))
                puffer = puffer[-UEBERLAPPUNG:].split(" ", 1)[-1] + "\n" + a
            else:
                puffer = (puffer + "\n" + a) if puffer else a
        if puffer.strip():
            raus.append((teil["quelle"], puffer.strip()))
    return raus


def verarbeiten(dok: dict[str, Any]) -> None:
    """Ein Dokument: auslesen, schwärzen, zerlegen, einbetten, speichern."""
    mensch, dok_id = str(dok["mensch"]), dok["id"]
    try:
        teile = _auslesen(bytes(dok["original"]), dok["typ"])
        geschwaerzt = 0
        for t in teile:
            t["text"], n = filter.schwaerzen(t["text"])
            geschwaerzt += n
        abschnitte = zerlegen(teile)
        if not abschnitte:
            raise Abgelehnt("Im Dokument steht kein Text.")
        vektoren: list[list[float] | None] = [None] * len(abschnitte)
        modell = einbettung.modell()
        if modell:
            for i in range(0, len(abschnitte), 32):
                v = einbettung.einbetten([a[1] for a in abschnitte[i:i + 32]], zeitlimit=120.0)
                if not v:
                    break
                vektoren[i:i + len(v)] = v
        with verbindung(mensch) as conn:
            conn.execute("DELETE FROM dokument_abschnitt WHERE dokument = %s", (dok_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO dokument_abschnitt (mensch, dokument, nummer, quelle, inhalt, einbettung, modell) "
                    "VALUES (%s, %s, %s, %s, %s, %s::vector, %s)",
                    [(mensch, dok_id, i, q[:200], text, einbettung.als_text(v) if v else None,
                      modell if v else None)
                     for i, ((q, text), v) in enumerate(zip(abschnitte, vektoren))])
            conn.execute("UPDATE dokument SET status = 'fertig', fehler = NULL, zeichen = %s, "
                         "geschwaerzt = %s WHERE id = %s",
                         (sum(len(a[1]) for a in abschnitte), geschwaerzt, dok_id))
        log.info("Dokument verarbeitet: %d Abschnitte (%s)", len(abschnitte), dok["typ"])
    except Abgelehnt as exc:
        with verbindung(mensch) as conn:
            conn.execute("UPDATE dokument SET status = 'fehler', fehler = %s WHERE id = %s",
                         (str(exc)[:500], dok_id))
        log.info("Dokument nicht lesbar (%s): %s", dok["typ"], str(exc)[:120])


def einmal() -> int:
    """Das nächste wartende Dokument verarbeiten. 1, wenn eines da war."""
    with verbindung(system=True) as conn:
        dok = conn.execute(
            "UPDATE dokument SET status = 'liest' WHERE id = (SELECT id FROM dokument "
            "WHERE status = 'wartet' ORDER BY angelegt LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, mensch, typ, original").fetchone()
    if not dok:
        return 0
    verarbeiten(dok)
    return 1


def neu_starten() -> None:
    """Nach einem Neustart: Was mitten im Lesen war, noch einmal."""
    with verbindung(system=True) as conn:
        conn.execute("UPDATE dokument SET status = 'wartet' WHERE status = 'liest'")


def schleife() -> None:
    """Im eigenen Thread (haupt.py): wartet auf neue Dokumente."""
    neu_starten()
    while True:
        try:
            while einmal():
                pass
        except Exception as exc:  # noqa: BLE001 — die Schleife darf nie sterben
            log.warning("Verarbeitung fehlgeschlagen: %s", exc)
        _wecker.wait(30)
        _wecker.clear()


def nachholen_einbettung(modell: str, menge: int = 64) -> int:
    """Für die Pflege: Abschnitte ohne (aktuelle) Einbettung."""
    with verbindung(system=True) as conn:
        offen = conn.execute("SELECT id, inhalt FROM dokument_abschnitt WHERE modell IS DISTINCT FROM %s "
                             "LIMIT %s", (modell, menge)).fetchall()
    if not offen:
        return 0
    vektoren = einbettung.einbetten([a["inhalt"] for a in offen], zeitlimit=120.0)
    if not vektoren:
        return 0
    with verbindung(system=True) as conn:
        for a, v in zip(offen, vektoren):
            conn.execute("UPDATE dokument_abschnitt SET einbettung = %s::vector, modell = %s WHERE id = %s",
                         (einbettung.als_text(v), modell, a["id"]))
    return len(offen)
