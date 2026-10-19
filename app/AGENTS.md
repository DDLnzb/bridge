# Bridge `app/` — AGENTS.md

Produktive Implementierung. Zum Gegenstück `../src/AGENTS.md` siehe das Parent-Doc.

## Purpose

SABnzbd-kompatible API-Oberfläche, Queue-Verwaltung, JDownloader-Anbindung und das Polling der DDLnzb-Cloud-API.

## Local Contracts

### Globale Instanzen statt Dependency Injection

Vier Modul-Globals, die einmal beim Import erzeugt werden: `jdownloader`, `download_handler`, `jd_heartbeat`, `queue_persistence`, dazu die Queue-Listen `download_queue` und `download_history` in `sabnzbd_api.py`.

Der Lifespan in `main.py` initialisiert sie in fester Reihenfolge: Health-Check der API → JDownloader-Connect → State-Restore → Start der Hintergrund-Tasks. Wer ein Modul hinzufügt, das eine dieser Instanzen benötigt, muss die Reihenfolge beachten.

### Dispatch über den `mode`-Parameter

`sabnzbd_api.py` nutzt **einen** Endpunkt (`GET /api` und `POST /api`) mit Dispatch über `mode=`. Unterstützte Modi: `auth`, `version`, `get_cats`, `get_config`, `queue`, `history`, `addurl`, `addfile`, `ddlnzb_links`, `delete`, `retry`, `fullstatus`.

Neue Modi hier ergänzen, nicht als eigene Route danebenlegen — sonst verstehen die *arr-Clients den Endpunkt nicht.

### API-Key-Prüfung

Es gibt **zwei** getrennte Prüfungen, beide gegen `config.DDLNZB_API_KEY`:

| Ort | Form | Verhalten bei Abweichung |
| --- | --- | --- |
| `sabnzbd_api.py`, Dispatch am `/api`-Endpunkt | Query-Param `apikey` | `{"status": false, "error": "Invalid API key"}` |
| `main.py`, Dependency `require_bridge_key` | Header `X-API-Key` **oder** Query-Param `apikey` | `HTTPException(401, "Invalid API key")` |

`require_bridge_key` hängt an `@app.get("/")`, `@app.get("/status")` und `@app.post("/heartbeat/reconnect")`. **Neue Routen in `main.py` brauchen diese Dependency ausdrücklich** — FastAPI schützt nichts automatisch. `/health` ist die einzige bewusst offene Route: Docker-HEALTHCHECK und Load-Balancer müssen sie ohne Zugangsdaten erreichen.

Zwei Eigenschaften, die bei Änderungen nicht verloren gehen dürfen:

- **Leere Konfiguration schließt.** Ist `DDLNZB_API_KEY` leer, ist kein Aufruf gültig (`not config.DDLNZB_API_KEY or ...`). Andernfalls ließe sich eine Bridge ohne Key abschalten.
- **`secrets.compare_digest`, nicht `==`.** Zeitkonstanter Vergleich.

`mode=auth` gibt `{"auth": "ok"}` zurück — es wird **kein** Key-Hash verglichen, nur Gleichheit.

### Fehlende Konfiguration ist kein Abbruch

Fehlt `DDLNZB_API_KEY`, loggt der Lifespan nur `logger.warning("DDLnzb API key not configured")` und startet weiter. Der Prozess startet also, ist aber funktional unbrauchbar: `/api` und alle Routen außer `/health` lehnen jeden Aufruf ab. Das ist als offener Punkt in `../plan/BRIDGE_COMPLETION_PLAN.md` (AP1) dokumentiert. Wer das zum Fail-Fast ändert, löst damit auch die offene Aufgabe auf — das ist eine bewusste Entscheidung, kein Bugfix.

### Ein Übergangspunkt Queue → Historie

`_move_to_history()` in `sabnzbd_api.py` ist der **einzige** Weg, auf dem ein Eintrag aus `download_queue` nach `download_history` wandert. `complete_download()`, `fail_download()` und `mark_manual_download()` sind dünne Wrapper darum.

Zwei Eigenschaften stecken bewusst darin:

- **Persistieren.** Vor der Zusammenführung haben die drei Aufrufstellen es unterschiedlich gemacht — `fail_download` und `mark_manual_download` haben ihren Eintrag bei jedem Neustart verloren. Neuer Übergang: `_move_to_history` aufrufen, nicht von Hand in beide Dicts schreiben.
- **Links mitnehmen.** `links=item.links` geht in die `HistoryItem`. Ohne sie kann `mode=retry` den Auftrag nicht wieder einreihen.

`mode=retry` weist Einträge mit `status == "Completed"` ab: das Paket existiert im JDownloader-Entpackungsordner nicht mehr.

### CORS ist unsicher konfiguriert

`main.py` setzt `allow_origins=["*"]` **zusammen mit** `allow_credentials=True`. Diese Kombination ist unsicher. Der Bridge lauscht standardmäßig auf `127.0.0.1`, im Docker-Image aber auf `0.0.0.0`.

### Zeitlimits sind hart kodiert

- Cloud-Calls: Timeout 30s, 3 Versuche, exponentielles Backoff
- JDownloader-Monitoring: 5s Polling aktiv / 300s idle, `max_duration = 30*60` (30 Minuten)
- Heartbeat: `HEARTBEAT_INTERVAL = 60`, `MAX_RECONNECT_ATTEMPTS = 3`, Backoff `[5, 15, 30]`. Pingt nur, wenn aktive Downloads in der Queue sind

### Fehlerbehandlung im Download-Loop

`_fetch_links_from_cloud()` fängt `httpx.RequestError` ab, um den Retry nicht zu brechen. Unerwartete Fehler brechen den Retry ab. Bei Änderungen beides im Blick behalten — zu breites Fangen verdeckt Fehler, zu schmales verschwendet Wiederholungen.

Die `release_id` wird mit `quote(..., safe="")` ins Pfadsegment kodiert. Sie stammt aus einem Base64-Payload und wird ungeprüft in `GET /api/v1/links/{release_id}` interpoliert. Der frühere `by-source`-Zweig (mit `source_url`/`hoster`-Param) ist entfernt — im Backend existiert dieser Pfad nicht, siehe `../AGENTS.md`.

### Division im Fortschritt

`_monitor_jdownloader()` rechnet `(bytesLoaded / bytesTotal) * 100`. JDownloader meldet `bytesTotal: 0`, solange die Gesamtgröße des Pakets nicht feststeht — der Wert ist also **normal**, nicht nur ein Ausnahmefall. Ohne den `if bytes_total > 0`-Schutz entsteht eine `ZeroDivisionError`, die vom breiten `except Exception` unten im Loop geschluckt wird; anschließend schläft der Handler `idle_interval` (300s) und verliert den Fortschritt für fünf Minuten.

## Verification

```bash
cd bridge && poetry run ruff check app/
poetry run python -m pytest tests/ -q
```

⚠️ **`python -m pytest`, nicht das `pytest`-Skript** — siehe die sys.path-Falle im Parent-Doc. Gegenprobe:

```bash
python -c "import app.sabnzbd_api as m; print(m.__file__)"
```

Für echte Verbindungsdiagnose: `jd_test.py` (manueller JDownloader-Connect-Smoke, ruft `asyncio.run()` beim Import auf — nicht pytest-fähig).

Abgedeckt von `tests/`:

| Datei | Bereich |
| --- | --- |
| `test_bridge_auth.py` | `require_bridge_key`: alle Routen außer `/health` |
| `test_queue_transitions.py` | `_move_to_history`, Persistenz über Neustart, `mode=retry` |
| `test_cloud_links.py` | URL-Bildung aus `release_id`, Fortschrittsberechnung |
| `test_state_restore.py` | State-Restore über Modulimport, Roundtrip |
| `test_smoke.py` | Platzhalter (`assert True`) |

Weiterhin **ohne** Tests: JDownloader-Verbindung, Heartbeat, `queue_persistence`.

## Child DOX Index

Keine Untergrenze.