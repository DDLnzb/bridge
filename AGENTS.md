# Bridge — AGENTS.md

## Purpose

Lokaler Dienst, der sich gegenüber Radarr/Sonarr/Lidarr/LazyLibrarian als **SABnzbd-kompatibler Download-Client** ausgibt. Er empfängt Fake-NZBs vom Backend, dekodiert die darin enthaltenen Payloads, holt die echten Links von der DDLnzb-API und reicht sie an My JDownloader weiter. Ohne JDownloader legt er die Links in der Queue ab.

Er spricht **kein** Usenet-Protokoll und lädt **keine** NZBs herunter.

## Ownership

- `app/` — produktive Implementierung (FastAPI, SABnzbd-API, JDownloader-Anbindung)
- `src/` — Stub/Parallelimplementierung, siehe unten
- `Dockerfile`, `pyproject.toml`, `README.md`
- `.env`, `.env.example`

## Local Contracts

### `app/` und `src/` sind zwei verschiedene Systeme

Das ist der wichtigste Punkt in diesem Verzeichnis.

| | `app/` | `src/` |
| --- | --- | --- |
| Umfang | SABnzbd-API, Queue, JDownloader, Cloud-Link-Fetch | ~108 LOC, `queue`/`history` als `# TODO` |
| Config | `BridgeConfig` (Dataclass), liest `bridge/.env` | `pydantic_settings.BaseSettings`, `env_file=".env"` |
| Variablen | `MYJD_EMAIL`, `MYJD_PASSWORD`, `MYJD_APP_KEY`, `DDLNZB_API_KEY` | `JD_EMAIL`, `JD_PASSWORD`, `JD_DEVICE`, `DDLNZB_BRIDGE_TOKEN` |

Beide Configs sind **disjunkt** — dieselbe Umgebungsvariable wirkt nicht auf beide.

⚠️ Das `CMD` im Dockerfile lautet `uvicorn src.main:app --host 0.0.0.0 --port 6789` und zeigt damit auf den **Stub**. Das in `docker-compose.prod.yml` referenzierte Image `ghcr.io/weedo078/ddlnzb-bridge` dürfte damit auf `src/` laufen, während `README.md` und `.env.example` die `app/`-Variante beschreiben. Bevor in einem dieser Pfade etwas „repariert" wird, klären, welcher Pfad tatsächlich produktiv ist.

### Aufrufrichtung ist einseitig

Der Bridge fragt die API, niemals umgekehrt. Es gibt kein `BRIDGE_URL`, keinen Callback, keine Webhook-Zustellung an den Bridge. Das Backend kennt den Bridge nicht.

Konsequenz für das Deployment: der Bridge-Host muss die API erreichen können. Port-Bindings der API auf `127.0.0.1` allein reichen nicht, wenn der Bridge auf einem anderen Host läuft.

### Auth gegen die API

Der Bridge ruft `GET {DDLNZB_API_URL}/api/v1/links/{release_id}` mit Header `X-API-Key`. Jeder Aufruf zählt gegen das **tägliche Download-Limit** des User-Tiers. Der Bridge darf deshalb nicht aggressiv pollen.

Der `by-source`-Pfad in `download_handler.py` (mit `source_url`/`hoster`-Param) existiert im Backend **nicht** — er ist toter Code.

### Payload-Format

Das Backend erzeugt in `../backend/app/scrapers/utils/payload.py` einen base64url-kodierten String:

```
title|source|mirror|size_mb|password|imdb_id
```

An Position 2 setzt das Backend `str(release.id)` — also die **Release-UUID**, keine URL.

⚠️ Der Bridge dekodiert das **zweimal**, in `app/sabnzbd_api.py` (`decode_payload`) und `app/download_handler.py` (`_parse_payload`). Die Kopien können auseinanderlaufen. Änderungen am Format müssen an beiden Stellen nachgezogen werden.

### Ports

Der SABnzbd-Port ist **6789** (in `docker-compose.prod.yml` als `6789:6789` veröffentlicht). `BRIDGE_PORT` hat dagegen den Default `8080`, den das Dockerfile per `--port 6789` überschreibt. Die `.env.example` beschreibt `BRIDGE_PORT` — dem Dockerfile-Vorrang geben.

## Work Guidance

- Alle `app/`-Module importieren absolut als `from app.…`. Start daher nur aus `bridge/`-Wurzel oder mit `PYTHONPATH=bridge`. CI nutzt `working-directory: bridge`.
- `myjdapi` ist ein **synchroner** Client. Die Bridge kapselt ihn mit `asyncio.to_thread`. Jede neue JDownloader-Methode muss in diesem Muster bleiben, sonst blockiert der Event-Loop.
- `CATEGORY_MAPPING` ist in `BridgeConfig.__post_init__` verdrahtet. Der Kommentar in `.env.example`, der einen JSON-String-Override beschreibt, ist **nicht implementiert** — der Beispielwert täuscht.
- Zwei Persistenzmechanismen laufen parallel: `sabnzbd_api._persist_state()` schreibt die komplette Queue+History in `STATE_PATH`, `persistence.QueuePersistence` schreibt nur die Queue nach `DATA_DIR/queue.json` (mit atomarem Schreiben via `.tmp` + `replace()`). Beide werden im Lifespan unabhängig genutzt.

## Verification

```bash
cd bridge && poetry run ruff check .
poetry run pytest -v
```

⚠️ `tests/test_smoke.py` ist ein Platzhalter (`assert True`). Es existiert **keine** echte Testabdeckung — die CI-Jobs für den Bridge führen nur Lint aus. Tests zu ergänzen ist laut `../plan/BRIDGE_COMPLETION_PLAN.md` noch offen.

`requirements.txt` ist veraltet (enthält `pycryptodome`, das im Code nicht verwendet wird, und lässt `myjdapi` sowie `pydantic-settings` weg). Das Dockerfile nutzt `poetry install`, nicht `requirements.txt` — die Datei ist tot und irreführend.

## Child DOX Index

| Child | Scope |
| --- | --- |
| [`app/AGENTS.md`](app/AGENTS.md) | Produktive Implementierung mit SABnzbd-API und JDownloader |

`src/` ist bewusst **ohne** eigenes Doc: Es ist ein Stub mit abweichendem Config-Vertrag, und die Entscheidung, ob er entfernt oder produktiv gemacht wird, ist offen. Der Widerspruch ist in diesem Doc festgehalten.