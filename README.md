# Gedächtnis für opencode — eine Erweiterung für OpenTerminalApps

*[English](README.en.md)*

Ein Langzeitgedächtnis für [opencode](https://opencode.ai), **je opencode-Sitzung**, als
eigenständige Docker-Anwendung neben [OpenTerminalApps (OTA)](https://github.com/bmetallica/openterminalapps).
OTA koppelt sie, schaltet sie für Gruppen ein, gibt jedem Arbeitsplatz ein eigenes Token und zeigt
die Erinnerungen unter **opencode · Erinnerungen** in der Seitenleiste und im Menü **⋯** der Kachel eines Arbeitsplatzes.

- **Jede Sitzung sieht nur ihr eigenes Gedächtnis.** Unteragenten und Forks teilen das ihrer
  Wurzelsitzung. Welche Sitzung es ist, setzt OTAs opencode-Plugin `ota-sitzung` ein, nicht das
  Modell.
- **Zugriff auf andere Sitzungen**, vom Menschen in der Ansicht vergeben: Sitzung B liest dann
  das Gedächtnis von Sitzung A mit, nur lesend. So geht es mit dem Wissen aus A in B weiter.
- **Hauptgedächtnis** je Mensch: Einträge, die alle seine Sitzungen mitlesen. Nur er pflegt es in
  OTA; opencode liest es nur. Angeheftetes kommt bei jedem Abruf mit, der Rest über die Suche.
- **Überdauert**, wenn opencode einen langen Verlauf zusammenfasst, und wenn die Sitzung Tage
  später fortgesetzt wird (`opencode -c`).
- **Fünf Werkzeuge über MCP**, knapp beschrieben für lokale Modelle: `memory_recall`,
  `memory_search`, `memory_save`, `memory_update`, `memory_forget`.
- **Suche**: Wortsuche, auf Wunsch zusammen mit Embeddings von einem OpenAI-kompatiblen Server
  (vLLM, llama.cpp, auch hinter llama-swap). Präfixe für Modelle wie nomic-embed-text sind
  einstellbar.
- **Datenschutz**:
  - Trennung zwischen Menschen per Row Level Security in Postgres.
  - Kein Lesezugriff für Admins.
  - Zugangsdaten werden beim Speichern abgewiesen.
  - Export und Löschen durch den Menschen selbst; beim Löschen des Kontos in OTA ist alles weg.
  - Ablauf nach einer Frist ohne Aktivität.
- **Ohne Port auf dem Host.** Erreichbar ist der Dienst nur über OTAs Traefik.

Voraussetzung ist OTA ab 2026-11-02 mit eingeschaltetem opencode (Handbuch Kapitel 38).

## Installieren

Auf dem OTA-Host:

```bash
cd /opt
git clone https://github.com/bmetallica/plugin_ota-opencode.git
cd plugin_ota-opencode
./setup.sh                      # .env mit Datenbank-Passwort und Kopplungsschlüssel
docker compose up -d --build
```

Dann in OTA unter **Einstellungen → Erweiterungen → Erweiterung koppeln**:

1. **Adresse** `http://ota-gedaechtnis:8080` und den **Kopplungsschlüssel** aus `setup.sh` eintragen.
2. **Einstellen**:
   - den Embedding-Server, z. B. `http://192.168.1.5:8080/v1` mit `nomic-embed-text` und den
     Präfixen `search_document: ` / `search_query: `, oder vLLM mit `BAAI/bge-m3` ohne Präfixe;
   - **Testen**.
3. Gruppen wählen und **Eingeschaltet** umlegen.

Ab dem nächsten Start oder Fortsetzen eines Arbeitsplatzes hat opencode dort sein Gedächtnis.
Alles Weitere steht in OTAs Handbuch,
[Kapitel 39](https://github.com/bmetallica/openterminalapps/blob/main/docs/wiki/39-erweiterungen.md).

## Betrieb

| Befehl | Was |
|---|---|
| `make update` | `git pull` und neu bauen; die Daten bleiben im Volume `ota_gedaechtnis_daten` |
| `make logs` | Protokoll, ohne Inhalte und ohne Tokens |
| `make test` | die Tests, mit eigener Datenbank, ohne OTA |

Die **Sicherung** übernimmt OTAs Sicherungslauf (über `/ota/v1/sicherung`). Wiederherstellen:

```bash
docker exec -i ota-gedaechtnis-db pg_restore -U gedaechtnis -d gedaechtnis --clean --if-exists < gedaechtnis.dump
docker restart ota-gedaechtnis
```

**Entfernen:** in OTA entkoppeln, dann `docker compose down`; erst
`docker volume rm ota_gedaechtnis_daten` löscht die Daten.

**Ort der Datenbank:** Standard ist das Volume `ota_gedaechtnis_daten`. Auf eine SSD zieht sie
OTAs Skript im laufenden Betrieb um: `sudo scripts/daten-umziehen.sh --gedaechtnis /ssd/ota/gedaechtnis`
(OTA-Handbuch, Kapitel 40). Es trägt `GEDAECHTNIS_DB_DIR` hier in `.env` ein.

**Netz:** Dienst und Datenbank hängen in einem internen Netz mit festem Bereich
(`GEDAECHTNIS_NETZ`, Vorgabe `10.98.254.0/28`). Docker vergäbe sonst irgendwann 192.168.x, und das
kann das LAN überdecken, in dem der Embedding-Server steht.

## Aufbau

```
gedaechtnis/
  haupt.py          FastAPI: /mcp, /ota/v1, /ansicht
  mcp_dienst.py     die Werkzeuge (offizielles MCP-SDK, Streamable HTTP, zustandslos)
  ota.py            der Vertrag mit OTA (Schnittstelle 1)
  zugang.py         Tokens der Arbeitsplätze, Signaturen von OTA
  speicher.py       Speichern, Suchen, Ändern; Trennung je Sitzung
  einbettung.py     Embeddings (OpenAI-kompatibel)
  filter.py         was nicht hinein darf
  pflege.py         nächtlich: Ablauf, Nachholen der Embeddings
  ansicht.py        die Ansicht, die OTA einbettet
  schema.sql        Postgres mit pgvector und Row Level Security
```

## Lizenz

Apache-2.0, wie OTA. Siehe [LICENSE](LICENSE).
