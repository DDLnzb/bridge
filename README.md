# DDLnzb Bridge

Lokaler Dienst, der sich gegenüber Radarr/Sonarr/Lidarr/LazyLibrarian als **SABnzbd-kompatibler Download-Client** ausgibt. Er spricht kein Usenet-Protokoll und lädt keine NZBs herunter.

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
  -e DDLNZB_API_URL=https://api.petsground.de \
  -e DDLNZB_API_KEY=your-portal-api-key \
  -e MYJD_EMAIL=your-jdownloader-email \
  -e MYJD_PASSWORD=your-jdownloader-password \
  -e MYJD_DEVICE_NAME=your-device-name \
  -v /pfad/zum/downloads:/downloads \
  ghcr.io/weedo078/ddlnzb-bridge:latest
```

`DDLNZB_API_KEY` und `DDLNZB_API_URL` dürfen nicht leer sein: ohne Key lehnt die Bridge **jeden** SABnzbd-Aufruf ab (`Bridge API key not configured`), ohne API-URL erreicht sie die API nicht.

## Betrieb mit Compose

Der Bridge-Service ist in `docker-compose.prod.yml` enthalten. Er liest seine Zugangsdaten aus `bridge/.env` (`env_file`) und setzt `DDLNZB_API_URL=http://api:8000` — im Compose-Netz heißt die API `api`; `localhost` zeigt aus dem Container auf den Container selbst.

```bash
docker compose up -d bridge
```

Der Service bindet standardmäßig auf `0.0.0.0:6789`. Wer ihn nur lokal braucht, setzt `BRIDGE_BIND=127.0.0.1` in der `.env` des Hosts.

## Konfiguration in Radarr/Sonarr

Radarr braucht **zwei** Einträge: einen Indexer für die Suche und einen Download-Client für die Übergabe.

### 1. Indexer (Newznab) — für die Suche

`Settings → Indexers → Add → Newznab`

| Feld | Wert |
| --- | --- |
| URL | `https://api.petsground.de` — **ohne** `/api` |
| API Key | dein DDLnzb-API-Key |
| Categories | 2000–2060 (Default passt) |
| API Path | `/api` (Default, nicht ändern) |

⚠️ Radarr hängt den `/api`-Pfad selbst an die URL. Ein eingetragenes `https://api.petsground.de/api` ergibt `…/api/api` und damit einen 404.

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

Beim lokalen Start außerhalb von Docker lädt die Anwendung automatisch die Datei `bridge/.env`. Lege dort alle benötigten Schlüssel ab (siehe Tabelle oben) und starte anschließend zum Beispiel mit `poetry run uvicorn app.main:app --reload`. Weitere Schritte sind nicht nötig – die Variablen stehen sofort zur Verfügung.

```bash
cd bridge
poetry install
poetry run uvicorn app.main:app --reload
```

`/health` ist bewusst ohne Auth erreichbar (Docker-HEALTHCHECK). Alle anderen Routen verlangen den Key per `X-API-Key`, `apikey`-Query-Param oder `Authorization: Bearer`.
