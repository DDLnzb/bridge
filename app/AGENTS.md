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

Der Endpunkt vergleicht den Query-Param `apikey` mit `config.DDLNZB_API_KEY` und liefert bei Abweichung `{"status": false, "error": "Invalid API key"}`. `mode=auth` gibt `{"auth": "ok"}` zurück — es wird **kein** Key-Hash verglichen, nur Gleichheit.

### Fehlende Konfiguration ist kein Abbruch

Fehlt `DDLNZB_API_KEY`, loggt der Lifespan nur `logger.warning("DDLnzb API key not configured")` und startet weiter. Das ist als offener Punkt in `../plan/BRIDGE_COMPLETION_PLAN.md` (AP1) dokumentiert. Wer das zum Fail-Fast ändert, löst damit auch die offene Aufgabe auf — das ist eine bewusste Entscheidung, kein Bugfix.

### CORS ist unsicher konfiguriert

`main.py` setzt `allow_origins=["*"]` **zusammen mit** `allow_credentials=True`. Diese Kombination ist unsicher. Der Bridge lauscht standardmäßig auf `127.0.0.1`, im Docker-Image aber auf `0.0.0.0`.

### Zeitlimits sind hart kodiert

- Cloud-Calls: Timeout 30s, 3 Versuche, exponentielles Backoff
- JDownloader-Monitoring: 5s Polling aktiv / 300s idle, `max_duration = 30*60` (30 Minuten)
- Heartbeat: `HEARTBEAT_INTERVAL = 60`, `MAX_RECONNECT_ATTEMPTS = 3`, Backoff `[5, 15, 30]`. Pingt nur, wenn aktive Downloads in der Queue sind

### Fehlerbehandlung im Download-Loop

`_fetch_links_from_cloud()` fängt `httpx.RequestError` ab, um den Retry nicht zu brechen. Unerwartete Fehler brechen den Retry ab. Bei Änderungen beides im Blick behalten — zu breites Fangen verdeckt Fehler, zu schmales verschwendet Wiederholungen.

## Verification

```bash
cd bridge && poetry run ruff check app/
```

Für echte Verbindungsdiagnose: `jd_test.py` (manueller JDownloader-Connect-Smoke, ruft `asyncio.run()` beim Import auf — nicht pytest-fähig). Automatisierte Tests existieren nicht; siehe Parent-Doc.

## Child DOX Index

Keine Untergrenze.