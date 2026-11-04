"""Die Ansicht, die OTA einbettet: opencode · Erinnerungen in der Seitenleiste
und im ⋯-Menü eines Arbeitsplatzes.

Wer schaut, steht in den von OTA signierten Kopfzeilen (zugang.py). Jeder
sieht nur sein Eigenes — Admins genauso. Änderungen gehen als POST mit der
Kopfzeile `X-OTA-Ansicht`; die kann eine fremde Seite ohne CORS nicht
setzen (Schutz gegen CSRF, zusätzlich zum SameSite-Cookie von OTA).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import dokumente, einstellungen, speicher, zugang
from .texte import TEXTE

router = APIRouter(prefix="/ansicht")
# Die Seite mit der Dokumentenbibliothek, in der Gesamtansicht neben dem
# Hauptgedächtnis.
DOKUMENTE = "_dokumente"
_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent / "vorlagen"),
                     autoescape=select_autoescape(["html"]))
_STATISCH = Path(__file__).parent / "statisch"
# Die Fassung von CSS und Skript in ihrer Adresse: Sonst nimmt der Browser
# nach einem Update noch Minuten lang das alte Skript aus seinem Cache, und
# neue Knöpfe tun nichts (so geschehen am 2026-11-04 beim Hochladen).
FASSUNG = hashlib.sha256(
    b"".join((_STATISCH / n).read_bytes() for n in ("ansicht.css", "ansicht.js"))).hexdigest()[:10]


def _betrachter(request: Request) -> zugang.Betrachter:
    b = zugang.aus_kopfzeilen({k.lower(): v for k, v in request.headers.items()})
    if b is None:
        raise HTTPException(403, "Nur über OTA erreichbar")
    return b


def _sprache(request: Request) -> str:
    return "en" if request.query_params.get("sprache") == "en" else "de"


def _seite(request: Request, b: zugang.Betrachter, arbeitsplatz: str | None) -> HTMLResponse:
    sprache = _sprache(request)
    t = TEXTE[sprache]
    if arbeitsplatz is not None and arbeitsplatz not in b.arbeitsplaetze:
        raise HTTPException(403, t["fremd"])
    speicher.haupt_anlegen(b.mensch)
    liste = speicher.sitzungen(b.mensch, [arbeitsplatz] if arbeitsplatz else None)
    # Das Hauptgedächtnis gehört zum Menschen, nicht zu einem Arbeitsplatz:
    # Es steht in der Gesamtansicht, nicht im ⋯-Menü eines Platzes.
    mit_haupt = arbeitsplatz is None
    gewaehlt = request.query_params.get("s") or (
        liste[0]["kennung"] if liste else (speicher.HAUPT if mit_haupt else ""))
    haupt = gewaehlt == speicher.HAUPT and mit_haupt
    if gewaehlt == DOKUMENTE and mit_haupt:
        return _antwort(_jinja.get_template("ansicht.html").render(
            **_gemeinsam(request, t, sprache, b, liste, arbeitsplatz), bibliothek=True,
            dokumente=dokumente.liste(b.mensch), dok_stand=dokumente.stand(b.mensch),
            aktuell=None, haupt=False))
    if haupt:
        aktuell = {"kennung": speicher.HAUPT, "titel": t["haupt"], "eigener_titel": None}
    else:
        aktuell = next((s for s in liste if s["kennung"] == gewaehlt), None)
    eintraege = speicher.eintraege(b.mensch, aktuell["kennung"]) if aktuell else []
    if haupt:
        # Was jede Sitzung beim Abruf davon bekommt: nur das Angeheftete.
        vorschau = [e for e in eintraege if e["angeheftet"]]
    else:
        vorschau = speicher.abrufen(b.mensch, aktuell["kennung"], vorschau=True) if aktuell else []
    alle = speicher.sitzungen(b.mensch) if aktuell else []
    zugriffe = speicher.zugriffe(b.mensch, aktuell["kennung"]) if aktuell and not haupt else []
    gelesen_von = speicher.gelesen_von(b.mensch, aktuell["kennung"]) if aktuell and not haupt else []
    # Die Dokumente und welche davon diese Sitzung lesen darf.
    alle_dok = dokumente.liste(b.mensch) if aktuell and not haupt else []
    frei = dokumente.freigegeben(b.mensch, aktuell["kennung"]) if alle_dok else set()
    return _antwort(_jinja.get_template("ansicht.html").render(
        **_gemeinsam(request, t, sprache, b, liste, arbeitsplatz),
        aktuell=aktuell, eintraege=eintraege, alle=alle, haupt=haupt,
        zugriffe=zugriffe, gelesen_von=gelesen_von, sitzung_dokumente=alle_dok, frei=frei,
        vorschau=[speicher.zeile_fuer(e) for e in vorschau], arten=speicher.ARTEN,
        laenge=speicher.LAENGE, bibliothek=False))


def _gemeinsam(request: Request, t: dict[str, str], sprache: str, b: zugang.Betrachter,
               liste: list[dict[str, Any]], arbeitsplatz: str | None) -> dict[str, Any]:
    """Was jede Seite braucht: Liste links, Sprache, Thema, Wege."""
    gruppen: dict[str, list[dict[str, Any]]] = {}
    for s in liste:
        gruppen.setdefault(s["arbeitsplatz_name"] or t["ohne_arbeitsplatz"], []).append(s)
    mit_haupt = arbeitsplatz is None
    return dict(
        t=t, sprache=sprache, thema="hell" if request.query_params.get("thema") == "hell" else "dunkel",
        gruppen=gruppen, mit_haupt=mit_haupt, haupt_kennung=speicher.HAUPT, dok_kennung=DOKUMENTE,
        haupt_anzahl=speicher.haupt_anzahl(b.mensch) if mit_haupt else 0,
        dok_anzahl=len(dokumente.liste(b.mensch)) if mit_haupt else 0,
        gewaehlt=request.query_params.get("s") or "", ablauf=einstellungen.werte()["ablauf_tage"],
        arbeitsplatz=arbeitsplatz, name=b.name, kurz=speicher.kurz,
        basis="../" if arbeitsplatz is not None else "",
        query=lambda **k: _query(request, **k), datum=_datum, mb=_mb, fassung=FASSUNG)


def _mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 104858 else f"{max(1, n // 1024)} KB"


def _antwort(html: str) -> HTMLResponse:
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; "
                                   "frame-ancestors 'self'; form-action 'self'",
        "Cache-Control": "no-store"})


def _query(request: Request, **neu: str) -> str:
    werte = {k: v for k, v in request.query_params.items() if k in ("sprache", "thema", "s")}
    werte.update({k: v for k, v in neu.items() if v is not None})
    from urllib.parse import urlencode
    return "?" + urlencode(werte)


def _datum(d: datetime | None) -> str:
    return d.strftime("%d.%m.%Y %H:%M") if d else ""


@router.get("/konto", response_class=HTMLResponse)
def konto(request: Request) -> HTMLResponse:
    return _seite(request, _betrachter(request), None)


@router.get("/arbeitsplatz/{platz}", response_class=HTMLResponse)
def arbeitsplatz(platz: str, request: Request) -> HTMLResponse:
    return _seite(request, _betrachter(request), platz)


@router.get("/statisch/{name}")
def statisch(name: str) -> Response:
    if name not in ("ansicht.css", "ansicht.js"):
        raise HTTPException(404)
    art = "text/css" if name.endswith(".css") else "text/javascript"
    return Response((_STATISCH / name).read_bytes(), media_type=art,
                    headers={"Cache-Control": "max-age=86400"})


@router.post("/aktion")
async def aktion(request: Request) -> JSONResponse:
    b = _betrachter(request)
    if request.headers.get("x-ota-ansicht") != "1":
        raise HTTPException(403, "Kopfzeile fehlt")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Kein JSON") from exc
    t = TEXTE["en" if body.get("sprache") == "en" else "de"]
    was = str(body.get("was") or "")
    s = str(body.get("sitzung") or "")
    if s and speicher.sitzung(b.mensch, s) is None:
        return JSONResponse({"ok": False, "meldung": t["keine_sitzung"]}, 404)
    try:
        if was == "neu":
            ergebnis, k = speicher.speichern(b.mensch, s, str(body.get("inhalt") or ""),
                                             str(body.get("art") or "notiz"), body.get("schlagworte"),
                                             quelle="ansicht")
            return JSONResponse({"ok": True, "meldung": t["gespeichert"] if ergebnis == "neu" else t["zusammengelegt"]})
        if was == "aendern":
            speicher.aendern(b.mensch, None, str(body.get("id")), inhalt=body.get("inhalt"),
                             art=body.get("art"), schlagworte=body.get("schlagworte"),
                             stand=body.get("stand") or None, quelle="ansicht")
            return JSONResponse({"ok": True, "meldung": t["geaendert"]})
        if was == "anheften":
            speicher.aendern(b.mensch, None, str(body.get("id")), angeheftet=bool(body.get("wert")),
                             quelle="ansicht")
            return JSONResponse({"ok": True})
        if was == "loeschen":
            n = speicher.vergessen(b.mensch, None, [str(i) for i in body.get("ids") or []])
            return JSONResponse({"ok": True, "meldung": t["geloescht_n"].format(n=n)})
        if was in ("kopieren", "verschieben"):
            n = speicher.uebertragen(b.mensch, [str(i) for i in body.get("ids") or []],
                                     str(body.get("ziel") or ""), was == "verschieben")
            return JSONResponse({"ok": True, "meldung": t["uebertragen_n"].format(n=n)})
        if was == "zugriff_geben":
            speicher.zugriff_geben(b.mensch, s, str(body.get("quelle") or ""))
            return JSONResponse({"ok": True, "meldung": t["zugriff_gegeben"]})
        if was == "zugriff_nehmen":
            speicher.zugriff_nehmen(b.mensch, s, str(body.get("quelle") or ""))
            return JSONResponse({"ok": True, "meldung": t["zugriff_genommen"]})
        if was == "umbenennen":
            speicher.sitzung_umbenennen(b.mensch, s, str(body.get("titel") or ""))
            return JSONResponse({"ok": True})
        if was == "sitzung_loeschen":
            n = speicher.sitzung_loeschen(b.mensch, s)
            return JSONResponse({"ok": True, "meldung": t["geloescht_n"].format(n=n), "weg": True})
        if was == "dokument_loeschen":
            dokumente.loeschen(b.mensch, str(body.get("id") or ""))
            return JSONResponse({"ok": True, "meldung": t["dokument_geloescht"]})
        if was == "dokument_fuer_alle":
            dokumente.fuer_alle(b.mensch, str(body.get("id") or ""), bool(body.get("wert")))
            return JSONResponse({"ok": True})
        if was == "dokument_freigabe":
            dokumente.freigeben(b.mensch, s, str(body.get("id") or ""), bool(body.get("wert")))
            return JSONResponse({"ok": True})
        if was == "alles_loeschen":
            if str(body.get("bestaetigung") or "").strip() != b.name:
                return JSONResponse({"ok": False, "meldung": t["bestaetigung_falsch"]}, 400)
            dokumente.alle_loeschen(b.mensch)
            n = speicher.alles_loeschen(b.mensch)
            return JSONResponse({"ok": True, "meldung": t["geloescht_n"].format(n=n), "weg": True})
    except speicher.Abgelehnt as exc:
        return JSONResponse({"ok": False, "meldung": str(exc)}, 400)
    raise HTTPException(400, "Unbekannte Aktion")


@router.post("/dokument")
async def dokument_hochladen(request: Request) -> JSONResponse:
    """Eine Datei als Rumpf, der Name in `X-Dateiname` (URL-kodiert). Ohne
    multipart: eine Abhängigkeit weniger, und gelesen wird nur bis zur Grenze."""
    b = _betrachter(request)
    if request.headers.get("x-ota-ansicht") != "1":
        raise HTTPException(403, "Kopfzeile fehlt")
    from urllib.parse import unquote
    t = TEXTE[_sprache(request)]
    name = unquote(request.headers.get("x-dateiname") or "")
    grenze = int(einstellungen.werte()["dokument_mb"]) * 1048576
    daten = bytearray()
    async for stueck in request.stream():
        daten += stueck
        if len(daten) > grenze:
            return JSONResponse({"ok": False, "meldung": t["dokument_zu_gross"].format(
                mb=einstellungen.werte()["dokument_mb"])}, 413)
    try:
        neu = dokumente.hochladen(b.mensch, name, bytes(daten))
    except speicher.Abgelehnt as exc:
        return JSONResponse({"ok": False, "meldung": f"{name}: {exc}"}, 400)
    return JSONResponse({"ok": True, "meldung": t["dokument_hochgeladen"].format(name=neu["name"])})


@router.get("/dokument/{dok}")
def dokument_holen(dok: str, request: Request) -> Response:
    b = _betrachter(request)
    try:
        uuid_ok = str(uuid.UUID(dok))
    except ValueError as exc:
        raise HTTPException(404) from exc
    d = dokumente.original(b.mensch, uuid_ok)
    if d is None:
        raise HTTPException(404)
    from urllib.parse import quote
    return Response(bytes(d["original"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(d['name'])}",
                             "X-Content-Type-Options": "nosniff"})


@router.get("/export")
def export(request: Request) -> Response:
    b = _betrachter(request)
    s = request.query_params.get("s") or None
    daten = speicher.ausfuhr(b.mensch, s)
    datei = "gedaechtnis" + (f"-{s[:20]}" if s else "")
    if request.query_params.get("format") == "md":
        zeilen = [f"# Gedächtnis von {b.name}", ""]
        for teil in daten:
            si = teil["sitzung"]
            name = ("Hauptgedächtnis" if si["kennung"] == speicher.HAUPT
                    else si["eigener_titel"] or si["titel"] or si["kennung"])
            zeilen += [f"## {name}", "",
                       f"- Sitzung: `{si['kennung']}`", f"- Verzeichnis: `{si['verzeichnis']}`",
                       f"- Arbeitsplatz: {si['arbeitsplatz_name']}", ""]
            for e in teil["eintraege"]:
                marke = " (angeheftet)" if e["angeheftet"] else ""
                zeilen.append(f"- **{e['art']}**{marke}, {e['geaendert']:%Y-%m-%d}: {e['inhalt']}")
            zeilen.append("")
        return Response("\n".join(zeilen), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{datei}.md"'})
    return Response(json.dumps(daten, default=str, ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{datei}.json"'})
