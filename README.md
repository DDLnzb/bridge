# DDLnzb Bridge

Local Docker container that acts as a SABnzbd-compatible download client for Radarr/Sonarr/LazyLibrarian.

## Features

- SABnzbd-compatible API on port 6789
- JDownloader integration via My JDownloader API
- Receives Fake-NZBs from *arr apps
- Fetches real links from DDLnzb Cloud
- Sends links to JDownloader

## Quick Start

```bash
docker run -d \
  --name ddlnzb-bridge \
  -p 6789:6789 \
  -e DDLNZB_API_URL=https://api.ddlnzb.com \
  -e DDLNZB_BRIDGE_TOKEN=your-token \
  -e JD_EMAIL=your-jdownloader-email \
  -e JD_PASSWORD=your-jdownloader-password \
  -e JD_DEVICE=your-device-name \
  -v /path/to/downloads:/downloads \
  ddlnzb/bridge:latest
```

## Configuration in Radarr/Sonarr

1. Go to Settings → Download Clients
2. Add new SABnzbd client
3. Configure:
   - Host: `localhost` (or your Docker host)
   - Port: `6789`
   - API Key: Your DDLnzb API key
   - Category: `movies` (for Radarr) or `tv` (for Sonarr)

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DDLNZB_API_URL` | DDLnzb Cloud API URL | Yes |
| `DDLNZB_BRIDGE_TOKEN` | Your API token from DDLnzb Portal | Yes |
| `JD_EMAIL` | My JDownloader email | Yes |
| `JD_PASSWORD` | My JDownloader password | Yes |
| `JD_DEVICE` | JDownloader device name | Yes |
| `DOWNLOAD_PATH_MOVIES` | Download path for movies | No |
| `DOWNLOAD_PATH_TV` | Download path for TV shows | No |
| `DOWNLOAD_PATH_DOCS` | Download path for documents | No |
