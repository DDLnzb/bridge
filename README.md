# DDLnzb Bridge

Lokaler Dienst, der sich als **SABnzbd-kompatibler Download-Client** gegenüber
Radarr/Sonarr/Lidarr/LazyLibrarian ausgibt. Er nimmt Fake-NZBs entgegen, holt
die echten Links von der DDLnzb-API und reicht sie an My JDownloader weiter.

Er spricht **kein** Usenet-Protokoll und lädt **keine** NZBs herunter.

## Features

- SABnzbd-kompatible API auf Port 6789
- JDownloader-Anbindung über die My-JDownloader-API
- Web-Oberfläche `/ui` zur Einrichtung und Konfiguration
- Prometheus-Metriken unter `/metrics` (geschützt)

## Funktionsweise

1. Das Backend liefert dem *arr-Client eine **Fake-NZB** — eine NZB-Datei, die nur die Release-ID enthält, keine echten Segmente.
2. Die Bridge dekodiert die Release-ID aus der NZB und holt die echten Links über `GET /api/v1/links/{release_id}` bei der DDLnzb-API.
3. Die Bridge reicht die Links an JDownloader weiter und überwacht den Fortschritt, bis Radarr/Sonarr den fertigen Download im History-Status sieht.

Die Aufrufrichtung ist einseitig: **die Bridge fragt die API, niemals umgekehrt.** Es gibt keinen Callback, keinen Webhook, kein `BRIDGE_URL`. Der Bridge-Host muss die API erreichen können.

## Voraussetzungen

- Ein **DDLnzb-API-Key** aus dem Portal (Profil → API Keys). Derselbe Key wird auch im Radarr-Indexer gebraucht.
- Ein **My JDownloader**-Account mit einem registrierten Gerät. Ohne JDownloader legt die Bridge die Links nur in der Queue ab und startet keinen Download.

## Schnellstart (Docker)

```bash
docker run -d \
  --name ddlnzb-bridge \
  -p 6789:6789 \
  -e DDLNZB_API_URL=https://ddlnzb.pages.dev/api/app \
  -e DDLNZB_API_KEY=your-api-key \
  -e MYJD_EMAIL=your-jdownloader-email \
  -e MYJD_PASSWORD=your-jdownloader-password \
  -e MYJD_DEVICE_NAME=your-device-name \
  -v /path/to/downloads:/downloads \
  -v /path/to/data:/app/data \
  ghcr.io/weedo078/ddlnzb-bridge:latest
```

Die Web-Oberfläche ist danach unter `http://<host>:6789/ui` erreichbar.

`DDLNZB_API_KEY` und `DDLNZB_API_URL` dürfen nicht leer sein: ohne Key lehnt die Bridge **jeden** SABnzbd-Aufruf ab (`Bridge API key not configured`), ohne API-URL erreicht sie die API nicht.

### Image-Architekturen

Das Image ist **Multi-Arch** und trägt beide Plattformen im selben Tag:

| Plattform | Host |
| --- | --- |
| `linux/amd64` | x86_64 — der Unraid-Produktionshost |
| `linux/arm64` | ARM64 — der Dev-Host `ddlnzb-dev` |

Ein `docker pull ghcr.io/weedo078/ddlnzb-bridge:latest` wählt die passende Variante selbst; ein expliziter `IMAGE_TAG` funktioniert genauso, weil er auf dasselbe Manifest zeigt. Fehlt eine Variante, schlägt der Pull mit `no matching manifest for linux/<arch>` fehl.

> **Nur die Bridge ist mehrarchitekturfähig.** Die Images `ddlnzb-api` und `ddlnzb-web` werden ausschließlich für `linux/arm64` gebaut. Wer den kompletten Stack auf einem x86_64-Host betreiben will, braucht für diese beiden Images noch eine amd64-Variante.
## Web-Oberfläche

`http://<host>:6789/ui` erlaubt die vollständige Konfiguration des Bridge:
API-Key, JDownloader-Zugang, Download-Zielordner, Log-Stufe. Dazu kommen
Verbindungstests und der aktuelle Betriebszustand (API, JDownloader, Queue).

**Ersteinrichtung.** Solange kein `DDLNZB_API_KEY` gesetzt ist, zeigt `/ui`
das Setup-Formular. Es verlangt den **Setup-Token**, der beim Start im
Container-Log steht:

```bash
docker logs ddlnzb-bridge | grep "Setup-Token"
```

Ohne diesen Token kann niemand den Bridge übernehmen — auch nicht aus dem LAN.
Alternativ lässt sich der Token per `DDLNZB_SETUP_TOKEN` vorgeben.

**Anmeldung.** Danach genügt der `DDLNZB_API_KEY` als Passwort. Er wird nicht
gespeichert, sondern als Session-Cookie (HttpOnly, SameSite=Strict) abgelegt.

## Betrieb mit Compose

Der Bridge-Service ist in `docker-compose.prod.yml` enthalten. Er liest seine Zugangsdaten aus `bridge/.env` (`env_file`) und setzt `DDLNZB_API_URL=http://api:8000` — im Compose-Netz heißt die API `api`; `localhost` zeigt aus dem Container auf den Container selbst.

```bash
docker compose up -d bridge
```

Der Service bindet standardmäßig auf `0.0.0.0:6789`. Wer ihn nur lokal braucht, setzt `BRIDGE_BIND=127.0.0.1` in der `.env` des Hosts.

## Konfiguration

| Variable | Beschreibung | Erforderlich |
|----------|--------------|--------------|
| `DDLNZB_API_URL` | DDLnzb-API-URL. Default `https://ddlnzb.pages.dev/api/app`; im Compose-Netzwerk `http://api:8000` | Nein |
| `DDLNZB_API_KEY` | API-Key aus dem Portal. **Pflicht** — ohne Key lehnt der Bridge jeden Aufruf ab | Ja |
| `MYJD_EMAIL` | My-JDownloader-E-Mail | Für Downloads |
| `MYJD_PASSWORD` | My-JDownloader-Passwort | Für Downloads |
| `MYJD_DEVICE_NAME` | Gerätename in `my.jdownloader.org` (exakt) | Für Downloads |
| `MYJD_APP_KEY` | App-Key, Default `DDLnzb` | Nein |
| `DOWNLOAD_DIR` | Zielordner für die Downloads | Nein |
| `EXTRACT_ARCHIVES` | Archive entpacken (Default `true`) | Nein |
| `DELETE_ARCHIVES` | Archive nach dem Entpacken löschen (Default `true`) | Nein |
| `BRIDGE_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (Default `INFO`) | Nein |
| `DDLNZB_DATA_DIR` | Ablage der Queue und der Settings-Datei | Nein |
| `DDLNZB_SETTINGS_FILE` | Pfad der Settings-Datei, Default `<DDLNZB_DATA_DIR>/settings.json` | Nein |
| `DDLNZB_STATE_FILE` | Pfad der State-Datei (Queue + Historie) | Nein |
| `DDLNZB_SETUP_TOKEN` | Setup-Token für die Ersteinrichtung statt des Log-Eintrags | Nein |

### Vorrag der Konfigurationsquellen

```
Settings-Datei  >  Umgebungsvariable  >  Default
```

Die Web-Oberfläche schreibt in die Settings-Datei (`DDLNZB_DATA_DIR`). Was dort
steht, gilt sofort — ohne Neustart — und gewinnt gegen die Umgebungsvariable.
Wer einen Wert dauerhaft über die Umgebung festlegen will, muss ihn dort
setzen **und** die Oberfläche danach nicht mehr ändern.

`BRIDGE_HOST` / `BRIDGE_PORT` gelten nur für lokale Läufe (Default `127.0.0.1:8080`). Im Container startet das Image uvicorn per `CMD` mit `--host 0.0.0.0 --port 6789` — der SABnzbd-Port ist deshalb immer **6789**.
## Konfiguration in Radarr/Sonarr

Radarr braucht **zwei** Einträge: einen Indexer für die Suche und einen Download-Client für die Übergabe.

### 1. Indexer (Newznab) — für die Suche

`Settings → Indexers → Add → Newznab`

| Feld | Wert |
| --- | --- |
| URL | `https://ddlnzb.pages.dev/api/app` — die App-API der Pages Function |
| API Key | dein DDLnzb-API-Key |
| Categories | 2000–2060 (Default passt) |
| API Path | `/api` (Default, nicht ändern) |

⚠️ Für Radarr/Sonarr gilt eine **andere** URL als für die Bridge: Die *arrs hängen den `/api`-Pfad selbst an, dort gehört `https://ddlnzb.pages.dev` **ohne** `/api` eingetragen (Radarr ergibt damit `https://ddlnzb.pages.dev/api`). Die Bridge spricht die App-API an und braucht `https://ddlnzb.pages.dev/api/app`.

### 2. Download-Client (SABnzbd) — für die Übergabe

`Settings → Download Clients → Add → SABnzbd`

| Feld | Wert |
| --- | --- |
| Host | Host der Bridge — nicht `localhost`, wenn Radarr in Docker läuft (`bridge` bzw. `host.docker.internal`) |
| Port | `6789` |
| API Key | der Bridge-Key (`DDLNZB_API_KEY` der Bridge) |
| Movie Category | `movies` (bzw. `tv` für Sonarr) |
| URL Base | leer |

Der API Key im Download-Client ist der **Bridge**-Key, nicht der Portal-Key — die Bridge prüft ihn selbst, bevor sie die API fragt.

## Kategorien

Die Bridge bildet die SABnzbd-Kategorie auf einen JDownloader-Ordner ab. Der Name wird an JDownloader übergeben — er muss dort als Ordner existieren, sonst landet der Download im JDownloader-Standardverzeichnis:

| SABnzbd-Kategorie | Zielordner |
| --- | --- |
| `movies`, `radarr` | `Movies` |
| `tv`, `sonarr` | `TV` |
| `music`, `lidarr` | `Music` |
| `books`, `lazy_librarian` | `Books` |
| `software` | `Software` |
| `whisparr` | `Adult` |

## Umgebungsvariablen

| Variable | Beschreibung | Pflicht |
| --- | --- | --- |
| `DDLNZB_API_URL` | Basis-URL der DDLnzb-API | Ja |
| `DDLNZB_API_KEY` | API-Key aus dem Portal | Ja |
| `MYJD_EMAIL` | My-JDownloader-E-Mail | Ja |
| `MYJD_PASSWORD` | My-JDownloader-Passwort | Ja |
| `MYJD_DEVICE_NAME` | Name des JDownloader-Geräts | Ja |
| `MYJD_APP_KEY` | My-JDownloader-App-Key | Nein (Default `DDLnzb`) |
| `DOWNLOAD_DIR` | Zielverzeichnis für Links ohne JDownloader | Nein (Default `./downloads`) |
| `BRIDGE_LOG_LEVEL` | Log-Level | Nein (Default `INFO`) |
| `BRIDGE_HOST` / `BRIDGE_PORT` | Bind-Adresse für lokale Läufe | Nein (Default `127.0.0.1:8080`) |
| `EXTRACT_ARCHIVES` / `DELETE_ARCHIVES` | Archive entpacken bzw. löschen | Nein (Default `true`) |
| `DDLNZB_STATE_FILE` / `DDLNZB_DATA_DIR` | Ablage der Queue und History | Nein |

⚠️ **Im Container gelten `BRIDGE_HOST` und `BRIDGE_PORT` nicht.** Das Image startet uvicorn per `CMD` mit `--host 0.0.0.0 --port 6789` — der Dockerfile-Vorrang. Der SABnzbd-Port ist deshalb immer **6789**, lokal `BRIDGE_PORT` (Default 8080).

## Lokale Entwicklung

Beim lokalen Start außerhalb von Docker lädt die Anwendung automatisch die
Datei `bridge/.env`. Lege dort alle benötigten Schlüssel ab und starte
anschließend:

```bash
cd bridge
poetry install
poetry run uvicorn app.main:app --reload
```

Weitere Schritte sind nicht nötig – die Variablen stehen sofort zur Verfügung.
