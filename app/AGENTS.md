# Bridge `app/` — AGENTS.md

Production implementation. For the counterpart `../src/AGENTS.md` see the parent doc.

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

  ⚠️ **Behavior tests do not distinguish this.** Both comparisons deliver the same answer, the difference lies only in the runtime. `tests/test_api_dispatch_auth.py` therefore observes the *call* of `compare_digest` via a dummy — confirmed with a mutation test that the remaining ten tests stay green on a return to `!=`. Anyone switching the check must maintain the spy, otherwise the protection is ineffective.

`mode=auth` returns `{"auth": "ok"}` — **no** key hash is compared, only equality.

### Missing configuration is not an abort

If `DDLNZB_API_KEY` is missing, the lifespan only logs `logger.warning("DDLnzb API key not configured")` and starts anyway. The process therefore starts, but is functionally unusable: `/api` and all routes except `/health` reject every call. Changing this to fail-fast is a deliberate decision, not a bug fix.

Since 23.10.2026 there is a way out: the web UI `/ui` shows the setup form in this state and accepts the API key there (with the setup token from the log). The process therefore still starts, but can be brought **via the UI** into a working state without touching the `.env`.

### The config is changeable at runtime

`BridgeConfig` is a module global that is read in many places **at call time** (`config.DDLNZB_API_KEY` in `sabnzbd_api.py` and `main.py`, `config.DOWNLOAD_DIR` in `download_handler.py`). Therefore a change takes effect immediately, without a restart.

Two prerequisites for this:

1. **`ENV_SOURCES` in `config.py` is the only source of truth** for field name, env name and default. The dataclass fields therefore no longer have their own defaults — `__post_init__` resolves them from the environment. Anyone adding a field must enter it there; otherwise the value never takes effect.
2. **`config.reload()`** reads the environment anew and layers the overrides of the settings file on top. This is the only entry point for the UI.

Exceptions that need a restart: `LOG_LEVEL` (only effective in the `logging.basicConfig` in the lifespan) and `STATE_FILE`/`DATA_DIR` (the paths are formed in module constants at module import time, among others `sabnzbd_api.STATE_PATH`).

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

### CORS is configured unsafely

`main.py` sets `allow_origins=["*"]` **together with** `allow_credentials=True`. This combination is unsafe. The bridge listens on `127.0.0.1` by default, but on `0.0.0.0` in the Docker image.

### Time limits are hardcoded

- Cloud calls: timeout 30s, 3 attempts, exponential backoff
- JDownloader monitoring: 5s polling active / 300s idle, `max_duration = 30*60` (30 minutes)
- Heartbeat: `HEARTBEAT_INTERVAL = 60`, `MAX_RECONNECT_ATTEMPTS = 3`, backoff `[5, 15, 30]`. Pings only when there are active downloads in the queue

### Error handling in the download loop

`_fetch_links_from_cloud()` catches `httpx.RequestError` so that the retry is not broken. Unexpected errors abort the retry. Keep both in mind when changing it — catching too broadly hides errors, catching too narrowly wastes repetitions.

The `release_id` is encoded into the path segment with `quote(..., safe="")`. It comes from a base64 payload and is interpolated into `GET /api/v1/links/{release_id}` without validation. The former `by-source` branch (with the `source_url`/`hoster` parameter) has been removed — this path does not exist in the backend, see `../AGENTS.md`.

### The sleep follows the package state, not the poll success

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
| `test_api_dispatch_auth.py` | Key check at the `/api` dispatch, including the `compare_digest` call |
| `test_queue_transitions.py` | `_move_to_history`, persistence across a restart, `mode=retry` |
| `test_cloud_links.py` | URL construction from `release_id`, progress calculation |
| `test_state_restore.py` | State restore across module import, roundtrip |
| `test_monitor_intervals.py` | Sleep choice in `_monitor_jdownloader` (5s/300s, error branch, disappearing package) |
| `test_webui.py` | UI: setup token, login, session, saving, reset, secrets |
| `test_smoke.py` | Placeholder (`assert True`) |

`test_monitor_intervals.py` fills `app.sabnzbd_api.download_queue` **directly** and replaces neither `asyncio.sleep` nor the time source of the handler permanently — see the `fresh_import()` warning in the parent doc. The 30-minute deadline is set via a context manager that replaces `asyncio.get_event_loop()` only during the handler call: a dummy on `loop.time` alone is not enough, because the event loop calls `self.time()` itself and the count would be off by one iteration.

Still **without** tests: JDownloader connection, heartbeat, `queue_persistence`.

## Child DOX Index

No lower bound.
