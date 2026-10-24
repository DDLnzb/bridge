# DDLnzb Bridge

A local service that presents itself to Radarr/Sonarr/Lidarr/LazyLibrarian as a
**SABnzbd-compatible download client**. It accepts fake NZBs, fetches the real
links from the DDLnzb API and hands them over to My JDownloader.

It speaks **no** Usenet protocol and downloads **no** NZB files.

## Features

- SABnzbd-compatible API on port 6789
- JDownloader integration via the My JDownloader API
- Web UI at `/ui` for setup and configuration
- Prometheus metrics at `/metrics` (protected)

## How it works

1. The DDLnzb API hands the *arr client a **fake NZB** — an NZB file that contains
   nothing but the release ID, no real segments.
2. The bridge decodes the release ID from the NZB and fetches the real links
   from the DDLnzb API via `GET /api/v1/links/{release_id}`.
3. The bridge passes the links to JDownloader and monitors the progress until
   Radarr/Sonarr sees the finished download in the history.

The direction is one-way: **the bridge asks the API, never the other way
round.** There is no callback, no webhook, no `BRIDGE_URL`. The bridge host must
be able to reach the API.

## Requirements

- A **DDLnzb API key** from the portal (Profile → API Keys). The same key is
  used for the Radarr indexer.
- A **My JDownloader** account with a registered device. Without JDownloader the
  bridge only puts the links into the queue and starts no download.

## Quick start (Docker)

```bash
docker run -d \
  --name ddlnzb-bridge \
  -p 6789:6789 \
  -v /path/to/downloads:/downloads \
  -v /path/to/data:/app/data \
  ghcr.io/ddlnzb/bridge:latest
```

That is all that is required to start the container. **Everything else is
configured in the web UI** at `http://<host>:6789/ui` — API key, JDownloader
access, download target folder and log level.

### First-time setup

As long as no API key is stored, `/ui` shows the setup form. It asks for the
**setup token**, which is printed to the container log on startup:

```bash
docker logs ddlnzb-bridge
```

The token is the one labelled `Setup-Token` / `setup token` in that output.

Without this token nobody can take over the bridge — not even from the LAN. The
token can also be set explicitly via `DDLNZB_SETUP_TOKEN`.

**Login.** After the setup is done, the DDLnzb API key is the password. It is
not stored in plain text but kept as a session cookie (HttpOnly,
SameSite=Strict).

### Image architectures

The image is **multi-arch** and carries both platforms from the same build:

| Platform | Architecture |
| --- | --- |
| `linux/amd64` | x86_64 |
| `linux/arm64` | ARM64 |

A `docker pull ghcr.io/ddlnzb/bridge:latest` picks the matching
variant automatically; an explicit `IMAGE_TAG` works as well because it points
at the same manifest. If a variant is missing, the pull fails with
`no matching manifest for linux/<arch>`.


## Web UI

`http://<host>:6789/ui` allows the complete configuration of the bridge:
API key, JDownloader access, download target folder, log level. On top of that
it offers connection tests and the current operating state (API, JDownloader,
queue).

## Running with Compose

When the bridge runs as part of a Docker Compose deployment, it reads its
credentials from a `.env` file (`env_file`) and reaches the API over the
Compose network as `http://api:8000` — inside that network the API is called
`api`; `localhost` would point at the container itself.

```bash
docker compose up -d bridge
```

The service binds to `0.0.0.0:6789` by default. To keep it local, set
`BRIDGE_BIND=127.0.0.1` in the host's `.env` file.

## Configuration

Everything below is optional — the web UI covers all of it. The variables are
only needed for unattended setups or to override a value permanently.

| Variable | Description | Required |
|----------|-------------|----------|
| `DDLNZB_API_URL` | Base URL of the DDLnzb API. Default `https://ddlnzb.pages.dev/api/app`; `http://api:8000` inside the Compose network | No |
| `DDLNZB_API_KEY` | API key from the portal. The bridge rejects **every** SABnzbd call without a key | No (via web UI) |
| `MYJD_EMAIL` | My JDownloader email | No (via web UI) |
| `MYJD_PASSWORD` | My JDownloader password | No (via web UI) |
| `MYJD_DEVICE_NAME` | Device name in `my.jdownloader.org` (exact match) | No (via web UI) |
| `MYJD_APP_KEY` | App key, default `DDLnzb` | No |
| `DOWNLOAD_DIR` | Target folder for the downloads | No |
| `EXTRACT_ARCHIVES` | Extract archives (default `true`) | No |
| `DELETE_ARCHIVES` | Delete archives after extraction (default `true`) | No |
| `BRIDGE_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`) | No |
| `DDLNZB_DATA_DIR` | Location of the queue and the settings file | No |
| `DDLNZB_SETTINGS_FILE` | Path of the settings file, default `<DDLNZB_DATA_DIR>/settings.json` | No |
| `DDLNZB_STATE_FILE` | Path of the state file (queue + history) | No |
| `DDLNZB_SETUP_TOKEN` | Setup token for the initial configuration instead of the log entry | No |

### Precedence of the configuration sources

```
settings file  >  environment variable  >  default
```

The web UI writes to the settings file (`DDLNZB_DATA_DIR`). What is stored
there applies immediately — without a restart — and wins over the environment
variable. To pin a value permanently via the environment, set it there **and**
do not change it in the UI afterwards.

`BRIDGE_HOST` / `BRIDGE_PORT` only apply to local runs (default
`127.0.0.1:8080`). In the container the image starts uvicorn via `CMD` with
`--host 0.0.0.0 --port 6789` — the Dockerfile takes precedence. The SABnzbd
port is therefore always **6789**.

## Configuration in Radarr/Sonarr

Radarr needs **two** entries: an indexer for searching and a download client
for the handover.

### 1. Indexer (Newznab) — for searching

`Settings → Indexers → Add → Newznab`

| Field | Value |
| --- | --- |
| URL | `https://ddlnzb.pages.dev` — Radarr appends the `/api` path itself |
| API Key | your DDLnzb API key |
| Categories | 2000–2060 (the default fits) |
| API Path | `/api` (default, do not change) |

⚠️ Radarr/Sonarr use a **different** URL than the bridge: the *arr apps append
the `/api` path themselves, so enter `https://ddlnzb.pages.dev` **without**
`/api` (Radarr turns it into `https://ddlnzb.pages.dev/api`). The bridge talks
to the app API and needs `https://ddlnzb.pages.dev/api/app`.

### 2. Download client (SABnzbd) — for the handover

`Settings → Download Clients → Add → SABnzbd`

| Field | Value |
| --- | --- |
| Host | host of the bridge — not `localhost` when Radarr runs in Docker (`bridge` or `host.docker.internal`) |
| Port | `6789` |
| API Key | the bridge key (the bridge's `DDLNZB_API_KEY`) |
| Movie Category | `movies` (or `tv` for Sonarr) |
| URL Base | leave empty |

The API key in the download client is the **bridge** key, not the portal key —
the bridge checks it itself before it asks the API.

## Categories

The bridge maps the SABnzbd category onto a JDownloader folder. The name is
handed over to JDownloader — it has to exist as a folder there, otherwise the
download ends up in JDownloader's default directory:

| SABnzbd category | Target folder |
| --- | --- |
| `movies`, `radarr` | `Movies` |
| `tv`, `sonarr` | `TV` |
| `music`, `lidarr` | `Music` |
| `books`, `lazy_librarian` | `Books` |
| `software` | `Software` |
| `whisparr` | `Adult` |

## Local development

When started outside of Docker the application loads the file `bridge/.env`
automatically. Put all required keys there and then start:

```bash
cd bridge
poetry install
poetry run uvicorn app.main:app --reload
```

No further steps are needed — the variables are available immediately.
