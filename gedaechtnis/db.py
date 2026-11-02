"""Datenbankzugang: ein Pool, das Schema und die Trennung je Mensch.

Jede Anfrage läuft in einer Transaktion, die vorher festlegt, für wen sie
gilt (`SET LOCAL ota.mensch`). Die Row Level Security in schema.sql lässt
dann nur dessen Zeilen durch. `system=True` ist der ausdrückliche Weg für
Pflege, Kontolöschung und Zahlen — er steht nur in Modulen, die das
brauchen, nie in den Werkzeugen oder der Ansicht.
"""
from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("gedaechtnis.db")

_pool: ConnectionPool | None = None


def adresse() -> str:
    return os.environ.get("GEDAECHTNIS_DB", "postgresql://gedaechtnis@db/gedaechtnis")


def starten() -> None:
    global _pool
    if _pool is not None:
        return
    _pool = ConnectionPool(adresse(), min_size=1, max_size=10, open=False,
                           kwargs={"row_factory": dict_row, "autocommit": False})
    _pool.open(wait=True, timeout=60)
    _schema()


def stoppen() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def verbindung(mensch: str | None = None, system: bool = False) -> Iterator[psycopg.Connection]:
    """Eine Transaktion für genau einen Menschen — oder für das System."""
    if _pool is None:
        raise RuntimeError("Datenbank nicht gestartet")
    with _pool.connection() as conn:
        with conn.transaction():
            if mensch:
                conn.execute("SELECT set_config('ota.mensch', %s, true)", (str(mensch),))
            if system:
                conn.execute("SELECT set_config('ota.system', 'an', true)")
            yield conn


_ABSCHNITT = re.compile(r"^-- @abschnitt (\d+)\s*$", re.M)


def _schema() -> None:
    """Spielt die Abschnitte aus schema.sql ein, die noch fehlen."""
    text = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    teile = _ABSCHNITT.split(text)
    abschnitte = {int(teile[i]): teile[i + 1] for i in range(1, len(teile), 2)}
    with verbindung(system=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_stand (nummer integer NOT NULL)")
        # Zwei Container gleichzeitig (Neustart während eines Updates) dürfen
        # nicht beide einspielen.
        conn.execute("SELECT pg_advisory_xact_lock(4711)")
        zeile = conn.execute("SELECT max(nummer) AS n FROM schema_stand").fetchone()
        stand = (zeile or {}).get("n") or 0
        for nummer in sorted(abschnitte):
            if nummer <= stand:
                continue
            log.info("Schema: Abschnitt %d", nummer)
            conn.execute(abschnitte[nummer])
            conn.execute("INSERT INTO schema_stand (nummer) VALUES (%s)", (nummer,))
