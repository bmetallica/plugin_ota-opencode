"""Die Ansicht, die OTA einbettet: opencode · Erinnerungen in der Seitenleiste
und im ⋯-Menü eines Arbeitsplatzes.

Wer schaut, steht in den von OTA signierten Kopfzeilen (zugang.py). Jeder
sieht nur sein Eigenes — Admins genauso. Änderungen gehen als POST mit der
Kopfzeile `X-OTA-Ansicht`; die kann eine fremde Seite ohne CORS nicht
setzen (Schutz gegen CSRF, zusätzlich zum SameSite-Cookie von OTA).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import einstellungen, speicher, zugang
from .texte import TEXTE

router = APIRouter(prefix="/ansicht")
_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent / "vorlagen"),
                     autoescape=select_autoescape(["html"]))
_STATISCH = Path(__file__).parent / "statisch"


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
    gruppen: dict[str, list[dict[str, Any]]] = {}
    for s in liste:
        gruppen.setdefault(s["arbeitsplatz_name"] or t["ohne_arbeitsplatz"], []).append(s)
    w = einstellungen.werte()
    html = _jinja.get_template("ansicht.html").render(
        t=t, sprache=sprache, thema="hell" if request.query_params.get("thema") == "hell" else "dunkel",
        gruppen=gruppen, aktuell=aktuell, eintraege=eintraege, alle=alle,
        haupt=haupt, mit_haupt=mit_haupt, haupt_kennung=speicher.HAUPT,
        haupt_anzahl=speicher.haupt_anzahl(b.mensch) if mit_haupt else 0,
        zugriffe=zugriffe, gelesen_von=gelesen_von,
        vorschau=[speicher.zeile_fuer(e) for e in vorschau], arten=speicher.ARTEN,
        arbeitsplatz=arbeitsplatz, name=b.name, kurz=speicher.kurz,
        basis="../" if arbeitsplatz is not None else "",
        ablauf=w["ablauf_tage"], laenge=speicher.LAENGE,
        query=lambda **k: _query(request, **k), datum=_datum)
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
                    headers={"Cache-Control": "max-age=300"})


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
        if was == "alles_loeschen":
            if str(body.get("bestaetigung") or "").strip() != b.name:
                return JSONResponse({"ok": False, "meldung": t["bestaetigung_falsch"]}, 400)
            n = speicher.alles_loeschen(b.mensch)
            return JSONResponse({"ok": True, "meldung": t["geloescht_n"].format(n=n), "weg": True})
    except speicher.Abgelehnt as exc:
        return JSONResponse({"ok": False, "meldung": str(exc)}, 400)
    raise HTTPException(400, "Unbekannte Aktion")


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
