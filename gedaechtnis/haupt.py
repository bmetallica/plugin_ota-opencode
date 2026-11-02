"""Der Dienst: MCP unter /mcp, der Vertrag mit OTA unter /ota/v1, die
Ansicht unter /ansicht. Ein Prozess, ein Port (8080, nur im Netz
ota_erweiterungen)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from fastapi import FastAPI

from . import ansicht, db, mcp_dienst, ota, pflege

logging.basicConfig(level=os.environ.get("GEDAECHTNIS_LOG", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

mcp_app = mcp_dienst.asgi_app()


@contextlib.asynccontextmanager
async def _leben(_: FastAPI):
    await asyncio.to_thread(db.starten)
    aufgabe = asyncio.create_task(pflege.schleife())
    # Der Sitzungsverwalter von MCP braucht seinen eigenen Lebenszyklus.
    async with mcp_dienst.server.session_manager.run():
        yield
    aufgabe.cancel()
    await asyncio.to_thread(db.stoppen)


app = FastAPI(title="Gedächtnis für opencode", lifespan=_leben, docs_url=None, redoc_url=None,
              openapi_url=None)
app.include_router(ota.router)
app.include_router(ansicht.router)


@app.get("/gesund")
def gesund() -> dict[str, str]:
    """Für den Healthcheck des Containers — ohne Anmeldung, ohne Inhalt."""
    return {"ok": "ja"}


app.mount("/", mcp_app)
