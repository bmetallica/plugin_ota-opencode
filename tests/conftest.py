"""Gemeinsam für alle Tests: ein Dienst (einmal gestartet — der MCP-Teil
lässt sich nur einmal starten), ein Embedding-Server zum Anfassen, Helfer."""
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

from gedaechtnis import zugang
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


@pytest.fixture(scope="session")
def embed_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Embed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


@pytest.fixture(scope="session")
def c(monkeypatch_sitzung, embed_url):
    monkeypatch_sitzung.setenv("GEDAECHTNIS_KOPPLUNGSSCHLUESSEL", KOPPLUNG)
    with TestClient(app) as client:
        r = client.post("/ota/v1/koppeln", json={"kopplungsschluessel": KOPPLUNG, "dienstgeheimnis": GEHEIM})
        assert r.status_code == 200, r.text
        r = client.put("/ota/v1/konfiguration", headers=_dienst(),
                       json={"einbettung_url": embed_url, "einbettung_modell": "test-modell"})
        assert r.status_code == 200, r.text
        yield client


@pytest.fixture(scope="session")
def monkeypatch_sitzung():
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


