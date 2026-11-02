# Memory for opencode — an extension for OpenTerminalApps

*[Deutsch](README.md)*

A long-term memory for [opencode](https://opencode.ai), **per opencode session**, as a standalone
Docker application next to [OpenTerminalApps (OTA)](https://github.com/bmetallica/openterminalapps).
OTA pairs it, turns it on for groups, gives every workspace its own token and shows the memories
under **My account → Memory** and in a workspace tile's **⋯** menu.

- **Every session sees only its own memory.** Subagents and forks share that of their root session.
  OTA's opencode plugin `ota-sitzung` fills in the session, not the model.
- **Survives** opencode summarising a long history and the session being continued days later
  (`opencode -c`).
- **Five tools over MCP**, briefly described for local models: `memory_recall`, `memory_search`,
  `memory_save`, `memory_update`, `memory_forget`.
- **Search**: word search, optionally together with embeddings from an OpenAI-compatible server
  (vLLM, llama.cpp, also behind llama-swap). Prefixes for models like nomic-embed-text can be set.
- **Privacy**:
  - Separation between people via Row Level Security in Postgres.
  - No read access for admins.
  - Credentials are refused when saving.
  - Export and deletion by the person themselves; deleting the account in OTA removes everything.
  - Expiry after a period without activity.
- **No port on the host.** The service is reachable only through OTA's Traefik.

Requires OTA from 2026-11-02 with opencode turned on (manual chapter 38).

## Installing

On the OTA host:

```bash
cd /opt
git clone https://github.com/bmetallica/plugin_ota-opencode.git
cd plugin_ota-opencode
./setup.sh                      # .env with database password and pairing key
docker compose up -d --build
```

Then in OTA under **Settings → Extensions → Pair an extension**:

1. Enter **address** `http://ota-gedaechtnis:8080` and the **pairing key** from `setup.sh`.
2. **Configure**:
   - the embedding server, e.g. `http://192.168.1.5:8080/v1` with `nomic-embed-text` and the
     prefixes `search_document: ` / `search_query: `, or vLLM with `BAAI/bge-m3` without prefixes;
   - **Test**.
3. Choose groups and flip **On**.

From the next start or resume of a workspace opencode has its memory there. Everything else is in
OTA's manual,
[chapter 39](https://github.com/bmetallica/openterminalapps/blob/main/docs/wiki/en/39-erweiterungen.md).

## Operation

| Command | What |
|---|---|
| `make update` | `git pull` and rebuild; the data stays in the volume `ota_gedaechtnis_daten` |
| `make logs` | log, without contents and without tokens |
| `make test` | the tests, with their own database, without OTA |

OTA's backup run takes care of the **backup** (via `/ota/v1/sicherung`). Restore:

```bash
docker exec -i ota-gedaechtnis-db pg_restore -U gedaechtnis -d gedaechtnis --clean --if-exists < gedaechtnis.dump
docker restart ota-gedaechtnis
```

**Removing:** unpair in OTA, then `docker compose down`; only
`docker volume rm ota_gedaechtnis_daten` deletes the data.

**Network:** service and database are in an internal network with a fixed range
(`GEDAECHTNIS_NETZ`, default `10.98.254.0/28`). Otherwise Docker would at some point hand out
192.168.x, which can cover the LAN where the embedding server is.

## Layout

```
gedaechtnis/
  haupt.py          FastAPI: /mcp, /ota/v1, /ansicht
  mcp_dienst.py     the tools (official MCP SDK, Streamable HTTP, stateless)
  ota.py            the contract with OTA (interface 1)
  zugang.py         workspace tokens, OTA's signatures
  speicher.py       saving, searching, changing; separation per session
  einbettung.py     embeddings (OpenAI-compatible)
  filter.py         what must not go in
  pflege.py         nightly: expiry, catching up on embeddings
  ansicht.py        the view OTA embeds
  schema.sql        Postgres with pgvector and Row Level Security
```

## License

Apache-2.0, like OTA. See [LICENSE](LICENSE).
