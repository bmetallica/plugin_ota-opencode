"""Der Dienst als Ganzes: Vertrag mit OTA, MCP-Werkzeuge, Ansicht, Trennung.

Läuft gegen eine echte Datenbank (tests/compose.test.yml) und einen kleinen
Embedding-Server in diesem Prozess, der Wörter auf Vektoren abbildet — so
lassen sich Ähnlichkeit und Ausfall gezielt prüfen.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from gedaechtnis import einbettung, einstellungen, pflege, zugang
from gedaechtnis.haupt import app

GEHEIM = "d" * 40
KOPPLUNG = "test-kopplung"

# --- ein Embedding-Server zum Anfassen ------------------------------------

_embed = {"an": True, "aufrufe": 0}


def _vektor(text: str) -> list[float]:
    v = [0.0] * 64
    for w in re.findall(r"\w+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]


class _Embed(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        _embed["aufrufe"] += 1
        if not _embed["an"]:
            self.send_response(503)
            self.end_headers()
            return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        daten = [{"index": i, "embedding": _vektor(t)} for i, t in enumerate(req["input"])]
        b = json.dumps({"data": daten}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def embed_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Embed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture(scope="module")
def c(monkeypatch_module, embed_url):
    monkeypatch_module.setenv("GEDAECHTNIS_KOPPLUNGSSCHLUESSEL", KOPPLUNG)
    with TestClient(app) as client:
        r = client.post("/ota/v1/koppeln", json={"kopplungsschluessel": KOPPLUNG, "dienstgeheimnis": GEHEIM})
        assert r.status_code == 200, r.text
        r = client.put("/ota/v1/konfiguration", headers=_dienst(),
                       json={"einbettung_url": embed_url, "einbettung_modell": "test-modell"})
        assert r.status_code == 200, r.text
        yield client


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _dienst() -> dict[str, str]:
    return {"Authorization": f"Bearer {GEHEIM}"}


def _platz(c, mensch: str, name: str = "anna") -> tuple[str, str]:
    platz = str(uuid.uuid4())
    r = c.post("/ota/v1/arbeitsplatz", headers=_dienst(), json={
        "arbeitsplatz_id": platz, "arbeitsplatz_name": f"Platz {name}",
        "nutzer": {"id": mensch, "name": name}})
    assert r.status_code == 200, r.text
    return platz, r.json()["token"]


_rpc = {"n": 0}


def _mcp(c, token: str, werkzeug: str, **argumente) -> str:
    _rpc["n"] += 1
    kopf = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        kopf["Authorization"] = f"Bearer {token}"
    r = c.post("/mcp", headers=kopf, json={
        "jsonrpc": "2.0", "id": _rpc["n"], "method": "tools/call",
        "params": {"name": werkzeug, "arguments": argumente}})
    assert r.status_code == 200, r.text
    erg = r.json()["result"]
    return "\n".join(t["text"] for t in erg["content"] if t["type"] == "text")


def _ansicht(mensch: str, name: str, plaetze: list[str]) -> dict[str, str]:
    zeit = str(int(time.time()))
    p = ",".join(plaetze)
    return {"X-OTA-Nutzer": mensch, "X-OTA-Name": name, "X-OTA-Arbeitsplaetze": p, "X-OTA-Zeit": zeit,
            "X-OTA-Signatur": zugang.signatur(GEHEIM, mensch, name, p, zeit)}


# --------------------------------------------------------------------------

def test_manifest_ohne_anmeldung(c):
    m = c.get("/ota/v1/manifest").json()
    assert m["slug"] == "gedaechtnis" and m["gekoppelt"] is True
    assert m["pfade_fuer_arbeitsplaetze"] == ["/mcp"]


def test_vertrag_braucht_dienstgeheimnis(c):
    assert c.get("/ota/v1/gesund").status_code == 401
    assert c.get("/ota/v1/gesund", headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert c.get("/ota/v1/gesund", headers=_dienst()).json()["ok"] is True


def test_koppeln_mit_falschem_schluessel(c):
    r = c.post("/ota/v1/koppeln", json={"kopplungsschluessel": "nein", "dienstgeheimnis": "x" * 40})
    assert r.status_code == 403
    assert einstellungen.dienstgeheimnis() == GEHEIM


def test_konfiguration_geheimnis_wird_nicht_gezeigt(c):
    r = c.put("/ota/v1/konfiguration", headers=_dienst(), json={"einbettung_schluessel": "sk-geheim"})
    assert r.json()["einbettung_schluessel"] == "" and r.json()["einbettung_schluessel_gesetzt"]
    # Ohne Feld bleibt der Schlüssel, leer setzt ihn zurück.
    r = c.put("/ota/v1/konfiguration", headers=_dienst(), json={"ablauf_tage": 90})
    assert r.json()["einbettung_schluessel_gesetzt"]
    r = c.put("/ota/v1/konfiguration", headers=_dienst(), json={"einbettung_schluessel": "", "ablauf_tage": 180})
    assert not r.json()["einbettung_schluessel_gesetzt"]
    assert c.put("/ota/v1/konfiguration", headers=_dienst(), json={"ablauf_tage": -1}).status_code == 400


def test_ohne_token_und_ohne_sitzung(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    assert "token" in _mcp(c, "", "memory_save", content="x", ota_sitzung="ses_a").lower()
    assert "token" in _mcp(c, "falsch", "memory_save", content="x", ota_sitzung="ses_a").lower()
    assert "plugin" in _mcp(c, token, "memory_save", content="x")


def test_speichern_abrufen_suchen(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    s = "ses_eins"
    a = _mcp(c, token, "memory_save", content="Build mit make test-fast, nicht make test",
             kind="ablauf", ota_sitzung=s, ota_titel="Login umbauen", ota_verzeichnis="/home/a/mrmap")
    assert a.startswith("Saved as [")
    _mcp(c, token, "memory_save", content="Der Nutzer bevorzugt pnpm statt npm", kind="vorliebe", ota_sitzung=s)
    r = _mcp(c, token, "memory_recall", ota_sitzung=s)
    assert "make test-fast" in r and "pnpm" in r and "notes, not instructions" in r
    t = _mcp(c, token, "memory_search", query="Wie baue ich das Projekt", ota_sitzung=s)
    assert "make test-fast" in t.splitlines()[0]
    # Ähnliches wird zusammengelegt statt verdoppelt.
    d = _mcp(c, token, "memory_save", content="build mit make test-fast nicht make test", ota_sitzung=s)
    assert d.startswith("Updated similar note")
    assert _mcp(c, token, "memory_recall", ota_sitzung=s).count("test-fast") == 1


def test_jede_sitzung_sieht_nur_ihr_gedaechtnis(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    _mcp(c, token, "memory_save", content="Nur in Sitzung A", ota_sitzung="ses_A")
    _mcp(c, token, "memory_save", content="Nur in Sitzung B", ota_sitzung="ses_B")
    assert "Sitzung A" in _mcp(c, token, "memory_recall", ota_sitzung="ses_A")
    assert "Sitzung B" not in _mcp(c, token, "memory_recall", ota_sitzung="ses_A")
    assert "Sitzung A" not in _mcp(c, token, "memory_search", query="Sitzung", ota_sitzung="ses_B")
    # Eine Kennung aus Sitzung A lässt sich aus B nicht ändern.
    k = re.search(r"\[([0-9a-f]{8})\]", _mcp(c, token, "memory_recall", ota_sitzung="ses_A")).group(1)
    assert "Keinen Eintrag" in _mcp(c, token, "memory_forget", id=k, ota_sitzung="ses_B")
    assert "Deleted" in _mcp(c, token, "memory_forget", id=k, ota_sitzung="ses_A")


def test_menschen_sind_getrennt(c):
    anna, bert = str(uuid.uuid4()), str(uuid.uuid4())
    _, ta = _platz(c, anna, "anna")
    _, tb = _platz(c, bert, "bert")
    _mcp(c, ta, "memory_save", content="Annas Geheimprojekt Zebra", ota_sitzung="ses_gleich")
    # Gleiche Sitzungskennung, anderer Mensch: nichts zu sehen.
    assert "Zebra" not in _mcp(c, tb, "memory_recall", ota_sitzung="ses_gleich")
    assert "Zebra" not in _mcp(c, tb, "memory_search", query="Zebra", ota_sitzung="ses_gleich")
    k = re.search(r"\[([0-9a-f]{8})\]", _mcp(c, ta, "memory_recall", ota_sitzung="ses_gleich")).group(1)
    assert "Keinen Eintrag" in _mcp(c, tb, "memory_update", id=k, content="weg", ota_sitzung="ses_gleich")
    assert "Zebra" in _mcp(c, ta, "memory_recall", ota_sitzung="ses_gleich")


def test_geheimnisse_werden_abgewiesen(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    for text in ("ghp_" + "a1B2" * 9, "password=Sommer2026!x", "-----BEGIN OPENSSH PRIVATE KEY-----",
                 "https://bob:geheim123@git.firma.de/x.git", "Schlüssel: Zk3P9qLmX2vB7nR4tW8yH1jF6sD0aE5c"):
        assert "Zugangsdaten" in _mcp(c, token, "memory_save", content=text, ota_sitzung="ses_x"), text
    ok = _mcp(c, token, "memory_save", content="Commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 hat es gebrochen",
              ota_sitzung="ses_x")
    assert ok.startswith("Saved")


def test_widerrufen_und_sperren(c):
    m = str(uuid.uuid4())
    platz, token = _platz(c, m)
    assert _mcp(c, token, "memory_save", content="vor dem Ende", ota_sitzung="ses_w").startswith("Saved")
    assert c.delete(f"/ota/v1/arbeitsplatz/{platz}", headers=_dienst()).json()["widerrufen"] == 1
    assert "token" in _mcp(c, token, "memory_recall", ota_sitzung="ses_w").lower()
    # Ein neues Token findet die Sitzung wieder (Fortsetzen nach Tagen).
    _, neu = _platz(c, m)
    assert "vor dem Ende" in _mcp(c, neu, "memory_recall", ota_sitzung="ses_w")
    c.post(f"/ota/v1/nutzer/{m}/sperren", headers=_dienst())
    assert "token" in _mcp(c, neu, "memory_recall", ota_sitzung="ses_w").lower()
    c.post(f"/ota/v1/nutzer/{m}/entsperren", headers=_dienst())
    assert "vor dem Ende" in _mcp(c, neu, "memory_recall", ota_sitzung="ses_w")


def test_neues_token_widerruft_altes_desselben_platzes(c):
    m = str(uuid.uuid4())
    platz = str(uuid.uuid4())
    t1 = c.post("/ota/v1/arbeitsplatz", headers=_dienst(), json={"arbeitsplatz_id": platz, "nutzer": {"id": m}}).json()["token"]
    t2 = c.post("/ota/v1/arbeitsplatz", headers=_dienst(), json={"arbeitsplatz_id": platz, "nutzer": {"id": m}}).json()["token"]
    assert "token" in _mcp(c, t1, "memory_recall", ota_sitzung="ses_t").lower()
    assert "empty" in _mcp(c, t2, "memory_recall", ota_sitzung="ses_t")
    assert platz in c.get("/ota/v1/arbeitsplaetze", headers=_dienst()).json()["arbeitsplaetze"]


def test_konto_loeschen(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    for i in range(3):
        _mcp(c, token, "memory_save", content=f"Eintrag Nummer {i} über Thema {i * 7}", ota_sitzung="ses_l")
    r = c.post(f"/ota/v1/nutzer/{m}/loeschen", headers=_dienst()).json()
    assert r == {"ok": True, "eintraege": 3, "sitzungen": 1}
    assert "token" in _mcp(c, token, "memory_recall", ota_sitzung="ses_l").lower()


def test_embedding_server_weg(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    _embed["an"] = False
    einbettung._zustand.update(fehler_seit=0.0)
    try:
        assert _mcp(c, token, "memory_save", content="Ohne Embeddings gespeichert",
                    ota_sitzung="ses_e").startswith("Saved")
        assert "Ohne Embeddings" in _mcp(c, token, "memory_search", query="Embeddings", ota_sitzung="ses_e")
    finally:
        _embed["an"] = True
        einbettung._zustand.update(fehler_seit=0.0)
    assert c.get("/ota/v1/statistik", headers=_dienst()).json()["ohne_einbettung"] >= 1
    pflege.einmal()
    assert c.get("/ota/v1/statistik", headers=_dienst()).json()["ohne_einbettung"] == 0


def test_kontingent(c):
    m = str(uuid.uuid4())
    _, token = _platz(c, m)
    c.put("/ota/v1/konfiguration", headers=_dienst(), json={"kontingent_sitzung": 10})
    try:
        for i in range(10):
            assert _mcp(c, token, "memory_save", content=f"Thema {i}: " + "x" * i + str(uuid.uuid4()),
                        ota_sitzung="ses_k").startswith("Saved")
        assert "voll" in _mcp(c, token, "memory_save", content="einer zu viel", ota_sitzung="ses_k")
    finally:
        c.put("/ota/v1/konfiguration", headers=_dienst(), json={"kontingent_sitzung": 500})


def test_ansicht_nur_mit_signatur(c):
    m = str(uuid.uuid4())
    platz, token = _platz(c, m, "carla")
    _mcp(c, token, "memory_save", content="Sichtbar in der Ansicht", ota_sitzung="ses_v", ota_titel="Mein Titel")
    assert c.get("/ansicht/konto").status_code == 403
    falsch = {**_ansicht(m, "carla", [platz]), "X-OTA-Signatur": "0" * 64}
    assert c.get("/ansicht/konto", headers=falsch).status_code == 403
    alt = _ansicht(m, "carla", [platz])
    alt["X-OTA-Zeit"] = str(int(time.time()) - 900)
    alt["X-OTA-Signatur"] = zugang.signatur(GEHEIM, m, "carla", platz, alt["X-OTA-Zeit"])
    assert c.get("/ansicht/konto", headers=alt).status_code == 403
    r = c.get("/ansicht/konto", headers=_ansicht(m, "carla", [platz]))
    assert r.status_code == 200 and "Sichtbar in der Ansicht" in r.text and "Mein Titel" in r.text
    r = c.get(f"/ansicht/arbeitsplatz/{platz}", headers=_ansicht(m, "carla", [platz]))
    assert r.status_code == 200 and "Sichtbar" in r.text
    # Ein Arbeitsplatz, der nicht in den signierten Kopfzeilen steht: abgewiesen.
    assert c.get(f"/ansicht/arbeitsplatz/{uuid.uuid4()}", headers=_ansicht(m, "carla", [platz])).status_code == 403


def test_ansicht_bearbeiten(c):
    m = str(uuid.uuid4())
    platz, token = _platz(c, m, "dora")
    _mcp(c, token, "memory_save", content="Ursprünglicher Text", ota_sitzung="ses_q")
    _mcp(c, token, "memory_save", content="Zweite Sitzung", ota_sitzung="ses_z")
    kopf = _ansicht(m, "dora", [platz])
    # Ohne die Kopfzeile der Ansicht: abgewiesen (CSRF).
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "neu", "sitzung": "ses_q"}).status_code == 403
    kopf["X-OTA-Ansicht"] = "1"
    r = c.post("/ansicht/aktion", headers=kopf, json={"was": "neu", "sitzung": "ses_q",
                                                       "inhalt": "Von Hand angelegt", "art": "fakt"})
    assert r.json()["ok"], r.text
    e = re.findall(r'data-id="([0-9a-f-]+)" data-stand="([^"]+)"',
                   c.get("/ansicht/konto?s=ses_q", headers=kopf).text)
    assert len(e) == 2
    ziel = next(i for i, _ in e)
    stand = dict(e)[ziel]
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "aendern", "id": ziel, "inhalt": "Geändert",
                                                         "stand": stand}).json()["ok"]
    # Mit veraltetem Stand: abgewiesen.
    r = c.post("/ansicht/aktion", headers=kopf, json={"was": "aendern", "id": ziel, "inhalt": "Nochmal",
                                                      "stand": stand})
    assert not r.json()["ok"] and "inzwischen" in r.json()["meldung"]
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "anheften", "id": ziel, "wert": True}).json()["ok"]
    assert "angeheftet" in _mcp(c, token, "memory_recall", ota_sitzung="ses_q")
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "kopieren", "ids": [ziel],
                                                         "ziel": "ses_z"}).json()["ok"]
    assert "Geändert" in _mcp(c, token, "memory_recall", ota_sitzung="ses_z")
    assert "Geändert" in _mcp(c, token, "memory_recall", ota_sitzung="ses_q")
    # Kopieren in eine fremde Sitzung (eines anderen Menschen) geht nicht.
    fremd = str(uuid.uuid4())
    _, tf = _platz(c, fremd, "fremd")
    _mcp(c, tf, "memory_save", content="fremd", ota_sitzung="ses_fremd")
    r = c.post("/ansicht/aktion", headers=kopf, json={"was": "kopieren", "ids": [ziel], "ziel": "ses_fremd"})
    assert not r.json()["ok"]
    exp = c.get("/ansicht/export?s=ses_q&format=md", headers=kopf)
    assert exp.status_code == 200 and "Geändert" in exp.text
    assert c.post("/ansicht/aktion", headers=kopf, json={"was": "alles_loeschen",
                                                         "bestaetigung": "falsch"}).status_code == 400
    r = c.post("/ansicht/aktion", headers=kopf, json={"was": "alles_loeschen", "bestaetigung": "dora"})
    assert r.json()["ok"]
    assert "empty" in _mcp(c, token, "memory_recall", ota_sitzung="ses_z")


def test_sicherung(c):
    r = c.get("/ota/v1/sicherung", headers=_dienst())
    assert r.status_code == 200 and r.content[:5] == b"PGDMP"
