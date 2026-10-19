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

**`app/` ist der produktive Pfad.** Das `CMD` im Dockerfile zeigt auf `uvicorn app.main:app` und damit auf `app/`. Belege dafür, dass das die gewollte Zielrichtung ist:

- `bridge/.env` und `bridge/.env.example` pflegen ausschließlich `app/`-Variablen (`MYJD_*`, `DDLNZB_API_KEY`). Keine einzige `src/`-Variable ist dort gesetzt.
- `README.md` startet lokal mit `poetry run uvicorn app.main:app`.
- Alle gemeldeten Bridge-Defekte betreffen `app/`-Dateien.

`src/` bleibt erhalten, wird aber nicht gestartet. Ob `src/` ganz entfällt, ist weiterhin offen — das ist eine eigene Entscheidung und nicht Teil der CMD-Korrektur. `Dockerfile` kopiert beide Verzeichnisse (`COPY app ./app`, `COPY src ./src`); das ist Absicht, damit der Stub nicht unbeabsichtigt mitverschwindet.

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

⚠️ Die Testabdeckung ist weiterhin dünn. `tests/test_smoke.py` ist ein Platzhalter (`assert True`); `tests/test_state_restore.py` deckt den Queue-/Historie-Restore über Import hinweg ab. Für alles andere — JDownloader-Anbindung, Cloud-Link-Fetch, Hintergrund-Tasks — gibt es weiterhin keine Tests. Die CI-Jobs für den Bridge führen nur Lint aus. Tests zu ergänzen ist laut `../plan/BRIDGE_COMPLETION_PLAN.md` noch offen.

Für Tests gilt: sie starten nur aus der `bridge/`-Wurzel, weil die Module absolut als `from app.…` importiert werden. **Dabei `python -m pytest` verwenden, nicht das `pytest`-Skript.** Das Skript legt das aktuelle Verzeichnis nicht auf `sys.path`; legt es eine alte Installation hinein (z. B. eine `.pth`-Datei eines früheren `poetry install` auf ein fremdes Verzeichnis), importiert pytest `app.*` von dort statt aus dem Working Tree. Das trifft Tests stillschweigend falsch — sie laufen gegen einen anderen Stand und schlagen dann für Gründe fehl, die im Working Tree nicht existieren. Gegenprobe bei Verdacht:

```bash
python -c "import app.sabnzbd_api as m; print(m.__file__)"
```

Der Pfad muss im Working Tree liegen. Wer den State-Pfad im Test braucht, setzt `DDLNZB_STATE_FILE` **vor** dem Import und lädt `app.config` und `app.sabnzbd_api` neu — `STATE_PATH` wird beim Modulimport aus der Config gebildet und sonst nicht neu gelesen.

`requirements.txt` ist veraltet (enthält `pycryptodome`, das im Code nicht verwendet wird, und lässt `myjdapi` sowie `pydantic-settings` weg). Das Dockerfile nutzt `poetry install`, nicht `requirements.txt` — die Datei ist tot und irreführend.

## Child DOX Index

| Child | Scope |
| --- | --- |
| [`app/AGENTS.md`](app/AGENTS.md) | Produktive Implementierung mit SABnzbd-API und JDownloader |

`src/` ist bewusst **ohne** eigenes Doc: Es ist ein Stub mit abweichendem Config-Vertrag und wird vom Image nicht gestartet. Ob er entfernt wird, ist offen.
