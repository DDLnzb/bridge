# Bridge `app/` — AGENTS.md

Produktive Implementierung. Zum Gegenstück `../src/AGENTS.md` siehe das Parent-Doc.

## Purpose

SABnzbd-kompatible API-Oberfläche, Queue-Verwaltung, JDownloader-Anbindung und das Polling der DDLnzb-Cloud-API.

## Local Contracts

### Prometheus-Metriken des Bridge

`metrics.py` definiert vier Metriken mit dem Präfix `ddlnzb_bridge_`: HTTP-Requests
(Label `service="bridge"`), Request-Dauer, Requests in flight und die
Queue-Tiefe (`state` = `total`/`queued`/`downloading`).

Die Zähler werden über die HTTP-Middleware in `main.py` gefüllt, die
Queue-Tiefe über den Endpunkt `GET /metrics` — dort werden die Gauges vor dem
Export frisch gesetzt, weil sie sonst nur den Stand des letzten Scrape zeigen.
Die Alert-Regeln in `monitoring/prometheus/rules/ddlnzb-alerts.yml` greifen auf
genau diese Namen zu.

Das `endpoint`-Label trägt das **Route-Template** (`_route_template()`), nicht
den rohen Pfad — sonst erzeugt jeder Link-Titel und jeder Scanner-Treffer eine
eigene Serie und die TSDB wächst ohne Grenzen. Ohne Route-Treffer landet der
Request in `unmatched`.

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
| `main.py`, Dependency `require_bridge_key` | Header `X-API-Key`, Query-Param `apikey` **oder** `Authorization: Bearer <key>` | `HTTPException(401, "Invalid API key")` |

`require_bridge_key` hängt an `@app.get("/")`, `@app.get("/status")`, `@app.get("/metrics")` und `@app.post("/heartbeat/reconnect")`. **Neue Routen in `main.py` brauchen diese Dependency ausdrücklich** — FastAPI schützt nichts automatisch. `/health` ist die einzige bewusst offene Route: Docker-HEALTHCHECK und Load-Balancer müssen sie ohne Zugangsdaten erreichen.

Der **Bearer**-Weg existiert für Prometheus: der kann keinen freien Header setzen, sondern nur `bearer_token_file`. Ohne diesen Weg wäre ein geschütztes `/metrics` nicht abfragbar. Er darf nicht entfernt werden, solange der Monitoring-Stack den Bridge über `bridge:6789/metrics` scrapt.

Zwei Eigenschaften, die bei Änderungen nicht verloren gehen dürfen:

- **Leere Konfiguration schließt.** Ist `DDLNZB_API_KEY` leer, ist kein Aufruf gültig (`not config.DDLNZB_API_KEY or ...`). Andernfalls ließe sich eine Bridge ohne Key abschalten.
- **`secrets.compare_digest`, nicht `==`.** Zeitkonstanter Vergleich. Stand 2026-10-19 stimmte das nur für `main.py`; der Dispatch in `sabnzbd_api.py` verglich mit `!=` (#478).

  ⚠️ **Verhaltenstests unterscheiden das nicht.** Beide Vergleiche liefern dieselbe Antwort, der Unterschied liegt nur in der Laufzeit. `tests/test_api_dispatch_auth.py` beobachtet deshalb den *Aufruf* von `compare_digest` über eine Attrappe — mit einem Mutationstest bestätigt, dass die übrigen zehn Tests bei der Rückkehr zu `!=` grün bleiben. Wer die Prüfung umstellt, muss den Spy mitpflegen, sonst ist die Absicherung wirkungslos.

`mode=auth` gibt `{"auth": "ok"}` zurück — es wird **kein** Key-Hash verglichen, nur Gleichheit.

### Fehlende Konfiguration ist kein Abbruch

Fehlt `DDLNZB_API_KEY`, loggt der Lifespan nur `logger.warning("DDLnzb API key not configured")` und startet weiter. Der Prozess startet also, ist aber funktional unbrauchbar: `/api` und alle Routen außer `/health` lehnen jeden Aufruf ab. Das ist als offener Punkt in `../plan/BRIDGE_COMPLETION_PLAN.md` (AP1) dokumentiert. Wer das zum Fail-Fast ändert, löst damit auch die offene Aufgabe auf — das ist eine bewusste Entscheidung, kein Bugfix.

Seit dem 23.10.2026 gibt es dafür einen Ausweg: die Web-Oberfläche `/ui` zeigt in diesem Zustand das Setup-Formular und nimmt dort den API-Key entgegen (mit Setup-Token aus dem Log). Der Prozess startet also weiter, ist aber **über die Oberfläche** in einen arbeitsfähigen Zustand bringbar, ohne die `.env` zu anfassen.

### Die Config ist zur Laufzeit änderbar

`BridgeConfig` ist ein Modul-Global, das an vielen Stellen **beim Aufruf** gelesen wird (`config.DDLNZB_API_KEY` in `sabnzbd_api.py` und `main.py`, `config.DOWNLOAD_DIR` in `download_handler.py`). Deshalb wirkt eine Änderung sofort, ohne Neustart.

Zwei Voraussetzungen dafür:

1. **`ENV_SOURCES` in `config.py` ist die einzige Quelle der Wahrheit** für Feldname, Env-Name und Default. Die Dataclass-Felder haben darum keine eigenen Defaults mehr — `__post_init__` löst sie aus der Umgebung auf. Wer ein Feld ergänzt, muss es dort eintragen; sonst greift der Wert nie.
2. **`config.reload()`** liest die Umgebung neu und legt die Overrides der Settings-Datei darüber. Das ist der einzige Einstiegspunkt für die Oberfläche.

Ausnahmen, die einen Neustart brauchen: `LOG_LEVEL` (wirkt nur beim `logging.basicConfig` im Lifespan) und `STATE_FILE`/`DATA_DIR` (die Pfade werden beim Modulimport in Modulkonstanten gebildet, u. a. `sabnzbd_api.STATE_PATH`).

### Die Web-Oberfläche (`webui.py`)

| Datei | Inhalt |
| --- | --- |
| `webui.py` | Router, Auth, Setup-Flow, Speichern, Verbindungstests |
| `webui_templates.py` | Markup, serverseitig in Python gebaut |
| `static/bridge.css` | Farbtokens aus `frontend/src/app/globals.css` |
| `static/bridge.js` | Passwort-Anzeige, Ladezustände, Dirty-Indikator |

**Der Auth der Oberfläche ist ein eigener Weg** und nicht `require_bridge_key`: Session-Cookie (`HttpOnly`, `SameSite=Strict`, `Path=/ui`) mit einer HMAC-Ableitung des Keys, alternativ der Key direkt im Header. Der Cookie enthält den Key **nicht**, sondern nur `HMAC(key, "ddlnzb-bridge-ui-session-v1")`.

Warum ein eigener Weg: `require_bridge_key` kennt nur Header und Query-Param. Ein Browser kann keinen Header setzen, ohne JavaScript — der Login wäre über die SABnzbd-Query-Param-Variante in der Adresszeile gelandet und der Key stünde im Verlauf. Der Cookie umgeht das.

**`SameSite=Strict` ist der CSRF-Schutz.** Es gibt kein CSRF-Token: ein fremder Origin kann das Cookie nicht mitschicken, ein Cross-Site-POST kommt also ohne Session an und wird mit 401 abgewiesen.

**Keine neue Abhängigkeit.** Das HTML wird in Python gebaut (keine Template-Engine), die Formulare werden aus dem Roh-Body gelesen — `python-multipart` bleibt außen vor, so wie `mode=addfile` im SABnzbd-Dispatcher es auch ohne auskommt. Wer hier ein Formular ergänzt, muss `webui._form_values()` benutzen, nicht `await request.form()`.

**Neue Felder brauchen drei Stellen:** `settings.FIELD_TYPES` (Typ), `settings.FORM_FIELDS` (Formularname → Feldname) und das Formular in `webui_templates.py`. Die Schlüssel in `FIELD_TYPES` sind die **Feldnamen** aus `BridgeConfig`, nicht die Env-Namen — für `LOG_LEVEL` sind die beiden verschieden (`BRIDGE_LOG_LEVEL`), und ein Mix aus beidem liefe beim Reload ins Leere.

⚠️ **`DDLNZB_API_URL` steht bewusst nicht in `FIELD_TYPES`.** Die API-URL bringt der Bridge mit; sie ist nur per Env überschreibbar. Ein Klick in der Oberfläche darf den Bridge nicht auf einen beliebigen Host umbiegen.

**Geheimnisse werden nie zurückgespiegelt.** Ein gesetztes Passwort erscheint als Platzhalter, nicht als Wert. Im Formular bedeutet ein leeres Feld „unverändert lassen", nicht „löschen" — nur der Reset-Knopf (`POST /ui/reset`) leert die Konfiguration. Abgesetzte Checkboxen sind dagegen ein Wert (`false`), kein leeres Feld.

**`jdownloader.reconfigure()`** ist der einzige Weg, neue Zugangsdaten zu übernehmen: der `myjdapi`-Client hält die alten Werte bis zum Neustart. Die Methode verbindet neu und liefert `True` nur, wenn das konfigurierte Gerät auch gefunden wurde.

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

### Der Warteschlaf folgt dem Paketzustand, nicht dem Poll-Erfolg

`_monitor_jdownloader()` wählt zwischen `active_interval` (5s) und `idle_interval` (300s) über das Flag `matched`. Das ist ein **Zustand des Monitorings**, kein Ergebnis des einzelnen Poll-Aufrufs, und lebt deshalb außerhalb der `while`-Schleife.

Die Regel dahinter: **ein Poll-Fehler sagt nichts über den Paketzustand.** Bis zum 2026-10-19 schlief der `except`-Zweig immer 300s, auch während ein Download lief — ein einzelner ausgesetzter JDownloader frozeerte die Fortschrittsanzeige für fünf Minuten und verbrauchte den Rest der 30-Minuten-Frist (#477). Beide Zweige wählen den Abstand jetzt aus demselben `matched`.

`matched` wird nur zurückgesetzt, wenn `get_packages()` **erfolgreich** war und das Paket darin nicht mehr enthalten war. Ein Fehler beim Abruf darf den Zustand nicht löschen. Wer das Flag am Anfang jeder Iteration zurücksetzt, baut genau den alten Fehler in anderer Form — die letzte bekannte Paketsicht geht dann beim ersten Poll-Fehler verloren.

### Division im Fortschritt

`_monitor_jdownloader()` rechnet `(bytesLoaded / bytesTotal) * 100`. JDownloader meldet `bytesTotal: 0`, solange die Gesamtgröße des Pakets nicht feststeht — der Wert ist also **normal**, nicht nur ein Ausnahmefall. Ohne den `if bytes_total > 0`-Schutz entsteht eine `ZeroDivisionError`, die vom breiten `except Exception` unten im Loop geschluckt wird; anschließend wartet der Handler bis zum nächsten Poll, bei laufendem Download also 5s statt 300s.

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
| `test_api_dispatch_auth.py` | Key-Prüfung am `/api`-Dispatch, inkl. `compare_digest`-Aufruf |
| `test_queue_transitions.py` | `_move_to_history`, Persistenz über Neustart, `mode=retry` |
| `test_cloud_links.py` | URL-Bildung aus `release_id`, Fortschrittsberechnung |
| `test_state_restore.py` | State-Restore über Modulimport, Roundtrip |
| `test_monitor_intervals.py` | Warteschlaf-Wahl in `_monitor_jdownloader` (5s/300s, Fehlerzweig, Verschwinden des Pakets) |
| `test_webui.py` | Oberfläche: Setup-Token, Anmeldung, Session, Speichern, Reset, Geheimnisse |
| `test_smoke.py` | Platzhalter (`assert True`) |

`test_monitor_intervals.py` füllt `app.sabnzbd_api.download_queue` **direkt** und ersetzt weder `asyncio.sleep` noch die Zeitquelle des Handlers dauerhaft — siehe die `fresh_import()`-Warnung im Parent-Doc. Die 30-Minuten-Frist wird über einen Kontextmanager gestellt, der `asyncio.get_event_loop()` nur während des Handler-Aufrufs ersetzt: eine Attrappe auf `loop.time` allein genügt nicht, weil der Event-Loop `self.time()` selbst aufruft und die Zählung um einen Durchlauf verschöbe.

Weiterhin **ohne** Tests: JDownloader-Verbindung, Heartbeat, `queue_persistence`.

## Child DOX Index

Keine Untergrenze.