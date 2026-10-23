# Bridge — AGENTS.md

## Purpose

Lokaler Dienst, der sich gegenüber Radarr/Sonarr/Lidarr/LazyLibrarian als **SABnzbd-kompatibler Download-Client** ausgibt. Er empfängt Fake-NZBs vom Backend, dekodiert die darin enthaltenen Payloads, holt die echten Links von der DDLnzb-API und reicht sie an My JDownloader weiter. Ohne JDownloader legt er die Links in der Queue ab.

Er spricht **kein** Usenet-Protokoll und lädt **keine** NZBs herunter.

Über `http://<host>:6789/ui` lässt sich der Dienst vollständig konfigurieren — der Setup-Token steht im Container-Log, sobald noch kein API-Key gesetzt ist.

## Ownership

- `app/` — produktive Implementierung (FastAPI, SABnzbd-API, JDownloader-Anbindung)
- `src/` — Stub/Parallelimplementierung, siehe unten
- `Dockerfile`, `pyproject.toml`, `README.md`
- `.env`, `.env.example`
- `docs/` — Screenshots der Web-Oberfläche (nur Bilddateien, keine Dokumentation)

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

Die `release_id` wird mit `quote(..., safe="")` ins Pfadsegment kodiert — sie stammt aus einem Base64-Payload und wird ungeprüft interpoliert.

### Payload-Format

Das Backend erzeugt in `../backend/app/scrapers/utils/payload.py` einen base64url-kodierten String:

```text
title|source|mirror|size_mb|password|imdb_id
```

An Position 2 setzt das Backend `str(release.id)` — also die **Release-UUID**, keine URL.

⚠️ Der Bridge dekodiert das **zweimal**, in `app/sabnzbd_api.py` (`decode_payload`) und `app/download_handler.py` (`_parse_payload`). Die Kopien können auseinanderlaufen. Änderungen am Format müssen an beiden Stellen nachgezogen werden.

### Ports

Der SABnzbd-Port ist **6789** (in `docker-compose.prod.yml` als `6789:6789` veröffentlicht). `BRIDGE_PORT` hat dagegen den Default `8080`, den das Dockerfile per `--port 6789` überschreibt. Die `.env.example` beschreibt `BRIDGE_PORT` — dem Dockerfile-Vorrang geben.

Die Web-Oberfläche hängt am **selben** Port unter dem Präfix `/ui`; die statischen Dateien liegen unter `/ui/static`. Es gibt keinen zweiten Port.

### Drei Konfigurationsquellen, ein Vorrang

```
Settings-Datei  >  Umgebungsvariable  >  Default
```

- **Default** — die Felddefaults in `app/config.py`.
- **Umgebung** — `bridge/.env` (lokal) bzw. die Container-Umgebung. `ENV_SOURCES` in `app/config.py` ist die einzige Stelle, die Feldnamen, Env-Namen und Defaults zusammenführt; `LOG_LEVEL` heißt in der Umgebung `BRIDGE_LOG_LEVEL`, die Namen dürfen nicht vereinheitlicht werden.
- **Settings-Datei** — `app/settings.py`, standardmäßig `<DDLNZB_DATA_DIR>/settings.json`. Wird ausschließlich über die Web-Oberfläche geschrieben, gilt sofort (ohne Neustart) und **gewinnt gegen die Umgebung**.

⚠️ **Der Vorrag ist eine bewusste Entscheidung und überraschend:** ein in der Oberfläche gespeicherter Wert überschreibt eine Umgebungsvariable. Wer einen Wert dauerhaft festnageln will, muss ihn dort setzen und die Oberfläche danach nicht mehr ändern. Der API-Key ist die einzige Ausnahme mit praktischem Nutzen — er wird fast immer zuerst in der Oberfläche gesetzt.

Die Datei enthält die Secrets im Klartext (Modus `0600`). Das entspricht der Entscheidung, `.env`-Dateien bewusst im Repo zu versionieren: der Bridge läuft lokal auf dem Betreiber-Host.

⚠️ **Der Pfad muss gemountet sein.** Ohne Volume auf `DDLNZB_DATA_DIR` (`/app/data` im Image) ist die Konfiguration nach jedem Container-Wechsel weg — der Bridge fällt dann auf die Umgebung zurück. `docker-compose.prod.yml` mountet dafür `bridge_data`.

### Die Web-Oberfläche `/ui`

`app/webui.py` liefert die Seiten, `app/webui_templates.py` das Markup, `app/static/` CSS und JS. Es gibt **keine** Template-Engine und keine neue Dependency — das HTML wird in Python gebaut, die Formulare werden aus dem Roh-Body gelesen (`python-multipart` bleibt bewusst außen vor, so wie es `mode=addfile` im SABnzbd-Dispatcher auch ohne auskommt).

Drei Zustände, alle unter demselben Pfad:

| Zustand | Bedingung | Seite |
| --- | --- | --- |
| Ersteinrichtung | `DDLNZB_API_KEY` ist leer | Setup-Formular mit Token-Pflicht |
| Anmeldung | Key gesetzt, keine Session | Login-Formular |
| Dashboard | Session gültig | Konfiguration + Status |

Auth: Session-Cookie (`HttpOnly`, `SameSite=Strict`, `Path=/ui`) mit einer HMAC-Ableitung des Keys — der Key selbst liegt **nicht** im Cookie. `SameSite=Strict` ist zugleich der CSRF-Schutz. Maschinenclients kommen über `X-API-Key` oder `?apikey=` herein.

Der **Setup-Token** schützt die Ersteinrichtung: ohne Key wäre der Bridge sonst aus dem LAN übernehmbar. Er wird beim Start erzeugt und im Log ausgegeben (`docker logs ddlnzb-bridge | grep "Setup-Token"`), ist pro Prozess einmalig und wird nach der Nutzung verworfen. `DDLNZB_SETUP_TOKEN` überschreibt ihn.

⚠️ **Die API-URL ist kein Eingabefeld.** Sie bringt der Bridge mit (Default `https://api.petsground.de`) und ist nur per `DDLNZB_API_URL` überschreibbar. Ein Klick in der Oberfläche darf den Bridge nicht auf einen beliebigen Host umbiegen — deshalb steht sie in `settings.FIELD_TYPES` nicht drin.

Die Oberfläche ist im Stil der DDLnzb-Website gehalten: die Farbtokens stammen aus `frontend/src/app/globals.css`, die Website selbst ist immer hell (kein Theme-Toggle). Änderungen an den Farbwerten dort sind hier nachzuziehen.

## Work Guidance

- Alle `app/`-Module importieren absolut als `from app.…`. Start daher nur aus `bridge/`-Wurzel oder mit `PYTHONPATH=bridge`. CI nutzt `working-directory: bridge`.
- `myjdapi` ist ein **synchroner** Client. Die Bridge kapselt ihn mit `asyncio.to_thread`. Jede neue JDownloader-Methode muss in diesem Muster bleiben, sonst blockiert der Event-Loop.
- `CATEGORY_MAPPING` ist in `BridgeConfig.__post_init__` verdrahtet. Der Kommentar in `.env.example`, der einen JSON-String-Override beschreibt, ist **nicht implementiert** — der Beispielwert täuscht.
- Zwei Persistenzmechanismen laufen parallel: `sabnzbd_api._persist_state()` schreibt die komplette Queue+History in `STATE_PATH`, `persistence.QueuePersistence` schreibt nur die Queue nach `DATA_DIR/queue.json` (mit atomarem Schreiben via `.tmp` + `replace()`). Beide werden im Lifespan unabhängig genutzt.

## Verification

```bash
cd bridge && poetry run ruff check .
poetry run python -m pytest tests/ -v
```

⚠️ `tests/test_smoke.py` ist ein Platzhalter (`assert True`). Der `bridge`-Job in `ci.yml` führt `poetry run pytest -v` aus — die Tests laufen also: 8 Testdateien mit 70 Testfunktionen (Stand 2026-10-23). Abgedeckt sind Auth, API-Dispatch, Cloud-Links, Queue-Transitions, State-Restore, die Poll-Intervalle von `_monitor_jdownloader` (mit gestubbtem JDownloader, nicht gegen eine echte Verbindung) und die Web-Oberfläche (`tests/test_webui.py`: Ersteinrichtung mit Setup-Token, Anmeldung und Session, Speichern der Konfiguration, Reset, Schutz der Geheimnisse).

Nicht getestet ist **`app/heartbeat.py::_heartbeat_loop`** — der Heartbeat-Client. Die zwei Tests mit „heartbeat" im Namen (`test_bridge_auth.py`) prüfen ausschließlich, dass `/heartbeat/reconnect` ohne gültigen Key mit 401 antwortet; die Schleife selbst läuft in keinem Test. Ebenso ungetestet bleibt die Queue-Persistenz. Ein grüner Bridge-Job sagt nichts über diese Pfade aus. Tests zu ergänzen ist laut `../plan/BRIDGE_COMPLETION_PLAN.md` noch offen.

`tests/conftest.py` liefert `state_file` (State-Datei im `tmp_path`, setzt `DDLNZB_STATE_FILE`) und `fresh_import` (lädt `app.config` und `app.sabnzbd_api` neu, damit `STATE_PATH` neu gelesen wird). Neue Tests, die den Zustand anfassen, nehmen diese Fixtures statt eigene zu bauen.

⚠️ **Nicht jeden Test darf `fresh_import()` benutzen.** `download_handler._monitor_jdownloader()` greift über eine Modul-Referenz auf `download_queue` zu. `fresh_import()` erzeugt ein neues Dict, das der Handler nicht sieht — der Eintrag fehlt, die Schleife kehrt sofort zurück und der Test wäre stillschweigend grün. Solche Tests befüllen `app.sabnzbd_api.download_queue` direkt und prüfen anschließend, dass `get_packages` wirklich aufgerufen wurde.

Für Tests gilt: sie starten nur aus der `bridge/`-Wurzel, weil die Module absolut als `from app.…` importiert werden. **Dabei `python -m pytest` verwenden, nicht das `pytest`-Skript.** Das Skript legt das aktuelle Verzeichnis nicht auf `sys.path`; legt es eine alte Installation hinein (z. B. eine `.pth`-Datei eines früheren `poetry install` auf ein fremdes Verzeichnis), importiert pytest `app.*` von dort statt aus dem Working Tree. Das trifft Tests stillschweigend falsch — sie laufen gegen einen anderen Stand und schlagen dann für Gründe fehl, die im Working Tree nicht existieren. Gegenprobe bei Verdacht:

```bash
python -c "import app.sabnzbd_api as m; print(m.__file__)"
```

Der Pfad muss im Working Tree liegen.

`pyproject.toml` und `poetry.lock` sind die einzige Abhängigkeitsquelle der Bridge. Ein `requirements.txt` gab es bis 2026-10-19, wurde von keinem Build-Pfad gelesen und wich vom Lockfile ab; es ist entfernt. **Keine zweite Deklaration anlegen** — Dependabot würde beide scannen und doppelte PRs erzeugen, von denen nur einer die installierte Version ändert.

`prometheus-client` (>=0.24.1,<0.27.0) ist seit PR #210 eine Bridge-Abhängigkeit: `app/metrics.py` exportiert `ddlnzb_bridge_*`, und der Monitoring-Stack scrapt den Bridge über `GET /metrics`.

## Child DOX Index

| Child | Scope |
| --- | --- |
| [`app/AGENTS.md`](app/AGENTS.md) | Produktive Implementierung mit SABnzbd-API und JDownloader |

`src/` ist bewusst **ohne** eigenes Doc: Es ist ein Stub mit abweichendem Config-Vertrag und wird vom Image nicht gestartet. Ob er entfernt wird, ist offen.
