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

## Schnellstart (Docker)

```bash
docker run -d \
  --name ddlnzb-bridge \
  -p 6789:6789 \
  -e DDLNZB_API_URL=https://api.petsground.de \
  -e DDLNZB_API_KEY=your-api-key \
  -e MYJD_EMAIL=your-jdownloader-email \
  -e MYJD_PASSWORD=your-jdownloader-password \
  -e MYJD_DEVICE_NAME=your-device-name \
  -v /path/to/downloads:/downloads \
  -v /path/to/data:/app/data \
  ghcr.io/weedo078/ddlnzb-bridge:latest
```

Die Web-Oberfläche ist danach unter `http://<host>:6789/ui` erreichbar.

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

## Konfiguration

| Variable | Beschreibung | Erforderlich |
|----------|--------------|--------------|
| `DDLNZB_API_URL` | DDLnzb-API-URL. Default `https://api.petsground.de`; im Compose-Netzwerk `http://api:8000` | Nein |
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

## Konfiguration in Radarr/Sonarr

1. Settings → Download Clients
2. „SABnzbd“ als Client-typ wählen
3. Einstellen:
   - Host: `localhost` (oder der Docker-Host)
   - Port: `6789`
   - API-Key: dein `DDLNZB_API_KEY`
   - Kategorie: `movies` (Radarr) bzw. `tv` (Sonarr)

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
