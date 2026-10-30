# Bridge `app/` — AGENTS.md

Production implementation — the **only** one. The former second tree `src/`
was removed on 30.10.2026 (#879); see the parent doc.

## Purpose

SABnzbd-compatible API surface, queue management, JDownloader integration and the polling of the DDLnzb cloud API.

## Local Contracts

### Prometheus metrics of the bridge

`metrics.py` defines four metrics with the prefix `ddlnzb_bridge_`: HTTP requests
(label `service="bridge"`), request duration, requests in flight and the queue
depth (`state` = `total`/`queued`/`downloading`).

The counters are filled via the HTTP middleware in `main.py`, the queue depth via
the endpoint `GET /metrics` — there the gauges are set fresh before the export,
because otherwise they only show the state of the last scrape. The alert rules in
`monitoring/prometheus/rules/ddlnzb-alerts.yml` use exactly these names.

The `endpoint` label carries the **route template** (`_route_template()`), not
the raw path — otherwise every link title and every scanner hit produces its own
series and the TSDB grows without bounds. Requests without a route match end up
in `unmatched`.

### Global instances instead of dependency injection

Four module globals that are created once at import time: `jdownloader`, `download_handler`, `jd_heartbeat`, `queue_persistence`, plus the queue lists `download_queue` and `download_history` in `sabnzbd_api.py`.

The lifespan in `main.py` initializes them in a fixed order: health check of the API → JDownloader connect → state restore → start of the background tasks. Anyone adding a module that needs one of these instances must respect the order.

### Dispatch via the `mode` parameter

`sabnzbd_api.py` uses **one** endpoint (`GET /api` and `POST /api`) with dispatch via `mode=`. Supported modes: `auth`, `version`, `get_cats`, `get_config`, `queue`, `history`, `addurl`, `addfile`, `ddlnzb_links`, `delete`, `retry`, `fullstatus`.

Add new modes here, do not put them alongside as their own route — otherwise the *arr clients do not understand the endpoint.

### API key check

There are **two** separate checks, both against `config.DDLNZB_API_KEY`:

| Location | Form | Behavior on mismatch |
| --- | --- | --- |
| `sabnzbd_api.py`, dispatch at the `/api` endpoint | query parameter `apikey` | `{"status": false, "error": "Invalid API key"}` |
| `main.py`, dependency `require_bridge_key` | header `X-API-Key`, query parameter `apikey` **or** `Authorization: Bearer <key>` | `HTTPException(401, "Invalid API key")` |

`require_bridge_key` is attached to `@app.get("/")`, `@app.get("/status")`, `@app.get("/metrics")` and `@app.post("/heartbeat/reconnect")`. **New routes in `main.py` need this dependency explicitly** — FastAPI protects nothing automatically. `/health` is the only deliberately open route: the Docker HEALTHCHECK and the load balancer must be able to reach it without credentials.

The **bearer** path exists for Prometheus: it cannot set a free-form header, only `bearer_token_file`. Without this path a protected `/metrics` could not be queried. It must not be removed as long as the monitoring stack scrapes the bridge via `bridge:6789/metrics`.

Two properties that must not get lost in changes:

- **An empty configuration closes everything.** If `DDLNZB_API_KEY` is empty, no call is valid (`not config.DDLNZB_API_KEY or ...`). Otherwise a bridge without a key could be switched off.
- **`secrets.compare_digest`, not `==`.** Constant-time comparison. As of 2026-10-19 that was only true for `main.py`; the dispatch in `sabnzbd_api.py` compared with `!=` (#478).

  ⚠️ **Behavior tests do not distinguish this.** Both comparisons deliver the same answer, the difference lies only in the runtime. `tests/test_api_dispatch_auth.py` therefore observes the *call* of `compare_digest` via a dummy — confirmed with a mutation test that the remaining eleven tests stay green on a return to `!=`. Anyone switching the check must maintain the spy, otherwise the protection is ineffective.

`mode=auth` returns `{"auth": "ok"}` — **no** key hash is compared, only equality.

**The key gate stands in front of the mode dispatch, not inside it.** That is what makes the unsigned download token harmless: the token names a release, the key authorizes the call. A mode that accepted a token without a key would turn it into a bearer credential. `tests/test_api_dispatch_auth.py` pins that for `mode=addurl` — the mode the *arr apps use for the NZB link.

### Missing configuration is not an abort

If `DDLNZB_API_KEY` is missing, the lifespan only logs `logger.warning("DDLnzb API key not configured")` and starts anyway. The process therefore starts, but is functionally unusable: `/api` and all routes except `/health` reject every call. Changing this to fail-fast is a deliberate decision, not a bug fix.

Since 23.10.2026 there is a way out: the web UI `/ui` shows the setup form in this state and accepts the API key there (with the setup token from the log). The process therefore still starts, but can be brought **via the UI** into a working state without touching the `.env`.

Since 24.10.2026 the settings file is applied **at startup** as well (see below): a bridge whose key was entered in the UI does not lose that key with the next restart - it comes back from `data/settings.json`, and no setup token is generated.

### The config is changeable at runtime

`BridgeConfig` is a module global that is read in many places **at call time** (`config.DDLNZB_API_KEY` in `sabnzbd_api.py` and `main.py`, `config.DOWNLOAD_DIR` in `download_handler.py`). Therefore a change takes effect immediately, without a restart.

Two prerequisites for this:

1. **`ENV_SOURCES` in `config.py` is the only source of truth** for field name, env name and default. The dataclass fields therefore no longer have their own defaults — `__post_init__` resolves them from the environment. Anyone adding a field must enter it there; otherwise the value never takes effect.
2. **`config.reload()`** reads the environment anew and layers the overrides of the settings file on top. It is the common entry point for the **startup** (the lifespan calls it as its first statement) and for the **web UI** (after a save).

Exceptions that need a restart: `LOG_LEVEL` (only effective in the `logging.basicConfig` in the lifespan) and `STATE_FILE`/`DATA_DIR` (the paths are formed in module constants at module import time, among others `sabnzbd_api.STATE_PATH`).

### The settings file applies at startup, not only after a UI write

Until 24.10.2026 the settings file was read **only** by the write routes of the web UI. After a restart of the container the bridge started with the environment alone: the API key was empty, a setup token went into the log and every call of the *arr apps was answered with 401 — although the file was mounted and the documentation promised the opposite (#715).

The file is applied in two places:

| Place | When | Why |
| --- | --- | --- |
| `BridgeConfig.__post_init__` | at import time | a freshly created config already carries the file |
| `config.reload()` | as the first statement of the lifespan | the **first** decision that depends on the configuration — the health check of the API, the connection to JDownloader and the setup token — sees the file |

`settings.load()` filters the content to `settings.FIELD_TYPES`: a broken file is logged and ignored, it never prevents the start. The precedence stays `settings file > environment > default`.

⚠️ **`CORS_ORIGINS` is environment-only.** The settings file only carries the fields from `FIELD_TYPES` — the CORS allowlist is not among them, so it can only be set via `BRIDGE_CORS_ORIGINS`. That is deliberate: the allowlist decides which browser origins may talk to the bridge at all, and a web form must not be able to widen it.

### The web UI (`webui.py`)

| File | Content |
| --- | --- |
| `webui.py` | Router, auth, setup flow, saving, connection tests |
| `webui_templates.py` | Markup, built server-side in Python |
| `static/bridge.css` | Color tokens from `frontend/src/app/globals.css` |
| `static/bridge.js` | Password display, loading states, dirty indicator |

**The auth of the UI is its own path** and not `require_bridge_key`: session cookie (`HttpOnly`, `SameSite=Strict`, `Path=/ui`) with an HMAC derivation of the key, alternatively the key directly in the header. The cookie does **not** contain the key, only `HMAC(key, "ddlnzb-bridge-ui-session-v1")`.

Why its own path: `require_bridge_key` only knows header and query parameter. A browser cannot set a header without JavaScript — the login would have ended up in the address bar via the SABnzbd query parameter variant and the key would be in the history. The cookie avoids that.

**`SameSite=Strict` is the CSRF protection.** There is no CSRF token: a foreign origin cannot send the cookie along, so a cross-site POST arrives without a session and is rejected with 401.

**No new dependency.** The HTML is built in Python (no template engine), the forms are read from the raw body — `python-multipart` stays out, just like `mode=addfile` in the SABnzbd dispatcher does without it. Anyone adding a form here must use `webui._form_values()`, not `await request.form()`.

**New fields need three places:** `settings.FIELD_TYPES` (type), `settings.FORM_FIELDS` (form name → field name) and the form in `webui_templates.py`. The keys in `FIELD_TYPES` are the **field names** from `BridgeConfig`, not the env names — for `LOG_LEVEL` the two differ (`BRIDGE_LOG_LEVEL`), and mixing them would resolve to nothing on reload.

⚠️ **`DDLNZB_API_URL` is deliberately not in `FIELD_TYPES`.** The bridge ships the API URL; it is only overridable via env. A click in the UI must not redirect the bridge to an arbitrary host.

**Secrets are never mirrored back.** A set password appears as a placeholder, not as a value. In the form an empty field means "leave unchanged", not "delete" — only the reset button (`POST /ui/reset`) empties the configuration. Unchecked checkboxes on the other hand are a value (`false`), not an empty field.

**`jdownloader.reconfigure()`** is the only way to take over new credentials: the `myjdapi` client keeps the old values until the restart. The method reconnects and returns `True` only if the configured device was found as well.

### One transition point queue → history

`_move_to_history()` in `sabnzbd_api.py` is the **only** way on which an entry moves from `download_queue` to `download_history`. `complete_download()`, `fail_download()` and `mark_manual_download()` are thin wrappers around it.

Two properties are deliberately in it:

- **Persisting.** Before the merge the three call sites did it differently — `fail_download` and `mark_manual_download` lost their entry on every restart. New transition: call `_move_to_history`, do not write into both dicts by hand.
- **Take the links along.** `links=item.links` goes into the `HistoryItem`. Without them `mode=retry` cannot queue the job again.

`mode=retry` rejects entries with `status == "Completed"`: the package no longer exists in the JDownloader extraction folder.

### `status` carries only the SABnzbd vocabulary, the text is in `message`

The `status` of every queue and history slot is a value from `SAB_STATUSES` — the
set from Sonarr's `SabnzbdDownloadStatus`
(`src/NzbDrone.Core/Download/Clients/Sabnzbd/SabnzbdDownloadStatus.cs`).

**Newtonsoft aborts the deserialization of the entire queue or history response
as soon as a slot carries a foreign value** — not just that one entry. The
download client is then completely dead for the *arr app (#659).** Until
23.10.2026 the slot carried free texts like `"Fetching links..."` or
`"Failed: Hoster down"`.

The division of labor follows from that:

- `status` — **only** a value from `SAB_STATUSES`.
- `message` (queue) respectively `fail_message` (history) — the detailed text.
- `_sab_status(text)` translates the free texts of the download handler
  (`_STATUS_TEXT_MAP`). Unknown texts land in `Downloading` and are logged; the
  original text is not lost, it is in `message`.

**"Starting download..." must not become "Queued".** The download handler only
accepts entries with the status `Queued` — a `Queued` at this point would pick
the entry up again immediately.

### The size is kept in bytes, not as text

`QueueItem.size_bytes` / `sizeleft_bytes` and `HistoryItem.size_bytes` are the
**only** source. `size`, `sizeleft`, `mb`, `mbleft` and `bytes` are derived from
them.

Sonarr/Radarr compute `TotalSize = mb * 1024 * 1024` (queue) and read the
history size from `bytes`. Without these fields they show every file as 0 MB
(#658). Two separately maintained values (text **and** bytes) are the reason why
`sizeleft` permanently stayed at the initial value: there was no place where the
remaining size was written.

`set_item_size()` and `_set_sizeleft()` are the only places that change the
size. `_set_sizeleft()` takes the real byte counts from JDownloader
(`bytes_loaded`/`bytes_total`); if they are unknown, the remaining size is
derived from the progress.

### The nzo_id comes in the parameter `value`, deletion happens with `name=delete`

Sonarr/Radarr call the dispatch like this:

| Call | Effect |
| --- | --- |
| `mode=queue&name=delete&value=<nzo_id>` | Remove the entry from the queue |
| `mode=history&name=delete&value=<nzo_id>` | Remove the entry from the history |
| `mode=delete&value=<nzo_id>` | Remove the entry from the queue |
| `mode=retry&value=<nzo_id>` | Queue the failed download again |

`value` is the standard SABnzbd name; `nzo_id` remains supported as the
bridge-native form. **Without `value` the bridge does not know the nzo_id** —
until 23.10.2026 `value` was not declared, a `name=delete` ran into the void
(#657).

Two traps:

- `mode=queue` **without** `name=delete` remains the listing. An empty `name`
  must not empty the queue.
- `mode=delete` only knows the **queue**. A history entry is deleted via
  `mode=history&name=delete`.

### CORS: an explicit allowlist, no reflection of the request origin

`main.py` registers `CORSMiddleware` with `allow_origins=list(config.CORS_ORIGINS)`, `allow_credentials=False` and a restricted method and header list.

Until 24.10.2026 it was `allow_origins=["*"]` **together with** `allow_credentials=True`. That combination is unsafe in Starlette: the origin of the request is **reflected** — no allowlist takes place. The only thing that still protected the web UI was the `SameSite=Strict` attribute of its session cookie (#720).

The bridge needs **no** CORS at all: the *arr apps are not browsers, and the web UI is served from the same origin as the API. The default is therefore an **empty** allowlist — then a foreign origin receives neither `access-control-allow-origin` nor `access-control-allow-credentials`, and its preflight ends with 400.

`BRIDGE_CORS_ORIGINS` (comma separated) is the explicit way to open the list for a deployment that operates the UI from another origin. The field `CORS_ORIGINS` is in `ENV_SOURCES` with the default `()`.

⚠️ **The allowlist is read when the module builds the app.** A change of the environment therefore needs a restart of the process — and a test that changes it has to reload `app.main` afterwards.

### Time limits are hardcoded

- Cloud calls: timeout 30s, 3 attempts, exponential backoff
- JDownloader monitoring: 5s polling active / 300s idle, `max_duration = 30*60` (30 minutes)
- Heartbeat: `HEARTBEAT_INTERVAL = 60`, `MAX_RECONNECT_ATTEMPTS = 3`, backoff `[5, 15, 30]`. Pings only when there are active downloads in the queue

### Error handling in the download loop

`_fetch_release_data()` catches `httpx.RequestError` so that the retry is not broken. Unexpected errors abort the retry. Keep both in mind when changing it — catching too broadly hides errors, catching too narrowly wastes repetitions.

The `release_id` is encoded into the path segment with `quote(..., safe="")`. It comes from a base64 payload and is checked as a UUID **before** it is interpolated into `GET /api/v1/links/{release_id}`. The former `by-source` branch (with the `source_url`/`hoster` parameter) has been removed — this path does not exist in the backend, see `../AGENTS.md`.

### The link endpoint delivers more than links

`_fetch_release_data()` returns the **complete** answer of
`GET /api/v1/links/{release_id}` — not only the list of URLs. The answer
carries `title`, `password` and `size_bytes` as well, and the bridge needs
them when the payload is the **download token** of the NZB link: that token
carries the release ID only (#789).

The precedence is: what the payload carries wins, the answer of the endpoint
fills the gap:

```text
title      = info.title     or release["title"]      or item.filename
password   = info.password  or release["password"]
size_bytes = info.size_bytes or release["size_bytes"]
```

A payload that cannot be decoded is **not** queued — `mode=addurl` and
`mode=addfile` answer with `"Unreadable DDLnzb payload"` and the queue stays
empty. Before that the entry went into the queue without a release ID and the
download ended later with "Invalid payload".

### The download token is read, not verified

`_decode_download_token()` in `sabnzbd_api.py` reads the signed token of the
NZB link (`<base64url(payload)>.<base64url(signature)>`) and returns the
release ID out of it. **The signature is not checked** — the bridge does not
know the secret of the backend and does not need it: it authorizes itself
against the API with its own service key.

⚠️ **The missing signature check is not an authorization bypass.** The
boundary of this hop is the **API key of the bridge**, not the token:

| Fact | Where |
| --- | --- |
| Every mode of the dispatch — `addurl` included — is gated by `secrets.compare_digest(apikey, config.DDLNZB_API_KEY)` | `sabnzbd_api.py` |
| The bridge resolves the release with that **same** key (`X-API-Key`) | `download_handler.py` |
| `GET /api/v1/links/{release_id}` selects the release by UUID **without an ownership check** — the only limit is the daily quota of the key's user | `backend/app/api/links.py` |

A caller who could forge a token therefore **already holds a key that
resolves any release directly**. A signature check in the bridge would grant
no further permission.

The signature is verified where it protects something: the backend checks the
token in `/api/download` (`verify_download_token()`), and only that path hands
out the NZB file. Copying the signing secret into the bridge would put it on a
second host — and this directory is mirrored into a public repository.

⚠️ **The dot is detected before the base64 decode.** `.` is not a base64url
character, and `urlsafe_b64decode` **silently discards** characters outside the
alphabet. A decode first would build a nonsense string out of body and
signature — the release ID would never be found and the download would end
with "Invalid payload".

### The heartbeat state follows the manual reconnect

`POST /ui/reconnect` in `webui.py` updates the heartbeat state via
`jd_heartbeat.mark_connected()` / `jd_heartbeat.mark_failed(detail)` (#681).
Before that the state was only cleaned up in `_ping()`, and `_ping()` runs
**only with active downloads** — without a download a stale error stood in
`/status` forever.

- `mark_connected()` clears the error and the counter of failed pings.
- `mark_failed(message)` takes over the **current** reason — the JDownloader
  error text, "credentials are not configured" or "device not found" — and
  leaves the counter untouched on purpose: it belongs to the ping attempts, and
  a counter pushed over `MAX_RECONNECT_ATTEMPTS` would stop the automatic
  reconnect for good.

`jdownloader.reconfigure()` sets `last_error` itself in every failure branch;
the web UI prefers that text over its generic hint.

### One decoder, two callers (#729)

`decode_payload()` in `sabnzbd_api.py` is the **only** decoder of the payload
format. `download_handler.py` imports it (`from app.sabnzbd_api import
decode_payload`) and calls it — there is no second copy.

Until 24.10.2026 the decode logic existed twice (`decode_payload` and
`_parse_payload`); the copies could drift apart without a test noticing.
Whoever changes the format changes **one** place.

Three formats are read (the token is described above):

| Format | Condition |
| --- | --- |
| JSON (`{"v":2,…}`) | the current state; `v` must be `PAYLOAD_VERSION` |
| Legacy (`title\|source\|…`) | only with exactly `LEGACY_FIELD_COUNT` fields **and** a UUID in field 1 |
| Download token | `<base64url(payload)>.<base64url(signature)>` with `v == DOWNLOAD_TOKEN_VERSION` |

A payload that is none of them yields `None` — the download aborts instead of
loading a wrong release. **Nothing is guessed.**

The release ID is checked as a UUID in **all** formats (`_is_uuid()`): the
backend knows no other IDs, and the ID travels in the path of
`GET /api/v1/links/{release_id}`.

⚠️ **The title determines nothing any more.** In the legacy format a `|` in
the release title shifted the fields — and thereby determined which release
was loaded and with which password it was unpacked. That was the finding
#729.

### Division in the sleep

`_monitor_jdownloader()` chooses between `active_interval` (5s) and `idle_interval` (300s) via the flag `matched`. That is a **state of the monitoring**, not the result of the single poll call, and therefore lives outside the `while` loop.

The rule behind it: **a poll error says nothing about the package state.** Until 2026-10-19 the `except` branch always slept 300s, even while a download was running — a single unresponsive JDownloader froze the progress display for five minutes and consumed the rest of the 30-minute deadline (#477). Both branches now choose the interval from the same `matched`.

`matched` is only reset when `get_packages()` was **successful** and the package was no longer contained in it. An error during the fetch must not delete the state. Anyone resetting the flag at the start of every iteration builds exactly the old error in another form — the last known view of the package is then lost at the first poll error.

### Division in the progress

`_monitor_jdownloader()` computes `(bytesLoaded / bytesTotal) * 100`. JDownloader reports `bytesTotal: 0` as long as the total size of the package is not known — the value is therefore **normal**, not just an edge case. Without the `if bytes_total > 0` guard a `ZeroDivisionError` arises, which is swallowed by the broad `except Exception` further down in the loop; the handler then waits until the next poll, so with a running download 5s instead of 300s.

## Verification

```bash
cd bridge && poetry run ruff check app/
poetry run python -m pytest tests/ -q
```

⚠️ **`python -m pytest`, not the `pytest` script** — see the sys.path trap in the parent doc. Counter-check:

```bash
python -c "import app.sabnzbd_api as m; print(m.__file__)"
```

For a real connection diagnostic: `jd_test.py` (manual JDownloader connect smoke, calls `asyncio.run()` at import — not pytest-capable).

Covered by `tests/`:

| File | Area |
| --- | --- |
| `test_bridge_auth.py` | `require_bridge_key`: all routes except `/health` |
| `test_api_dispatch_auth.py` | Key check at the `/api` dispatch, including the `compare_digest` call and the gate in front of `mode=addurl` |
| `test_queue_transitions.py` | `_move_to_history`, persistence across a restart, `mode=retry` |
| `test_cloud_links.py` | URL construction from `release_id`, progress calculation |
| `test_state_restore.py` | State restore across module import, roundtrip |
| `test_monitor_intervals.py` | Sleep choice in `_monitor_jdownloader` (5s/300s, error branch, disappearing package) |
| `test_webui.py` | UI: setup token, login, session, saving, reset, secrets |
| `test_settings_startup.py` | The settings file is applied at startup: key effective without a UI write, no setup token, precedence against the environment, broken file |
| `test_cors_policy.py` | CORS policy: a foreign origin gets no header, a preflight is refused, a configured origin is allowed |
| `test_default_urls.py` | One implementation, one default: no `src/`, no host without DNS in a shipped file, the API default in exactly one Python file, the live host resolves |
| `test_addurl_token.py` | The download token of the NZB link: release ID in the queue entry, unreadable payload refused, title/password/size from the link endpoint |
| `test_ui_reconnect.py` | The manual reconnect updates the heartbeat state in both directions; a failed reconnect does not count as a failed ping |
| `test_jdownloader_reconfigure.py` | `reconfigure()` names the current reason in every failure branch: missing credentials, failed login, missing device |
| `test_smoke.py` | Placeholder (`assert True`) |

`test_monitor_intervals.py` fills `app.sabnzbd_api.download_queue` **directly** and replaces neither `asyncio.sleep` nor the time source of the handler permanently — see the `fresh_import()` warning in the parent doc. The 30-minute deadline is set via a context manager that replaces `asyncio.get_event_loop()` only during the handler call: a dummy on `loop.time` alone is not enough, because the event loop calls `self.time()` itself and the count would be off by one iteration.

Still **without** tests: JDownloader connection, the heartbeat loop itself, `queue_persistence`.

## Child DOX Index

No lower bound.
