# Bridge — AGENTS.md

## Purpose

A local service that presents itself to Radarr/Sonarr/Lidarr/LazyLibrarian as a
**SABnzbd-compatible download client**. It receives fake NZBs from the backend,
decodes the payloads they carry, fetches the real links from the DDLnzb API and
passes them on to My JDownloader. Without JDownloader it puts the links into the
queue.

It speaks **no** Usenet protocol and downloads **no** NZB files.

The service is configured completely via `http://<host>:6789/ui` — the setup
token is in the container log as long as no API key is stored yet.

## Ownership

- `app/` — production implementation (FastAPI, SABnzbd API, JDownloader integration)
- `Dockerfile`, `pyproject.toml`, `README.md` — the README documents the config contract of `app/`
- `.env`, `.env.example`
- `docs/` — screenshots of the web UI (image files only, no documentation)

### This directory is mirrored into a public repository

The content of `bridge/` is mirrored via CI into the public repository
`DDLnzb/bridge` (job `sync-bridge-public`, executed through
`scripts/sync-bridge-public.sh`). There, the repository's own CI builds the
image `ghcr.io/ddlnzb/bridge` (linux/amd64 + linux/arm64).

Consequences for changes here:

- **The shipped code is written in English.** The directory is copied verbatim
  into a publicly readable repository. Comments, docstrings, the web UI labels
  and the documentation are all in English — a German sentence in a public
  repository is a bug, not a style question. The **test names** are an
  exception: they are German throughout the suite, and they stay that way.
- **`.env` must not end up in the mirror.** The file contains real credentials
  (`MYJD_PASSWORD`, `MYJD_APP_KEY`, `DDLNZB_API_KEY`) and is neither versioned
  nor contained in the history of the mirrored repository — the sync filters it
  out of *every* snapshot. Anyone who needs a credentials file here creates it as
  `.env.example`.
- **`tests/test_deployment_contract.py` needs subjects that do not exist here.**
  It checks the compose file of the production stack and the versioned
  `.env` — neither is part of this repository, so it skips itself here.
- **Changes here produce a sync.** The sync transfers **one snapshot commit**
  based on the current `main` of the mirrored repository, opens a PR and merges
  it via rebase-merge. A direct push to `main` is forbidden there by a ruleset —
  the sync works the same way: branch, PR, merge.

## Local Contracts

### There is exactly one implementation

Until 30.10.2026 this directory held **two** trees: `app/` (the production
implementation) and `src/` (a ~108 LOC stub with its own SABnzbd API, a
disjoint config contract and `queue`/`history` as `# TODO`). The stub was never
started — the `CMD` of the image points to `uvicorn app.main:app` — but it
shipped in the image and carried a **default API URL for a host that does not
resolve** (see #870). That URL was a trap: a reader could not tell which of
the two defaults was the live one.

Both are gone (#879). `src/` was deleted, the `Dockerfile` copies only `app/`,
and `pyproject.toml` packages `app` instead of `src`.

Consequences:

- **One implementation, one config contract, one default.** `ENV_SOURCES` in
  `app/config.py` is the single source of truth for every variable and its
  default. The API URL exists in exactly one Python file.
- **The default points at the live host**: `https://ddlnzb.pages.dev/api/app`.
  `tests/test_default_urls.py` holds that contract: it fails if a host without
  a DNS record appears in a shipped file, if the default URL appears in a
  second Python file, or if `src/` comes back.
- **A new default URL belongs in `ENV_SOURCES`** — nowhere else. The web UI
  labels and the README point at the variable, not at the value.

### The call direction is one-way

The bridge asks the API, never the other way round. There is no `BRIDGE_URL`, no
callback, no webhook delivery to the bridge. The backend does not know the
bridge.

Consequence for the deployment: the bridge host must be able to reach the API.
Binding the API to `127.0.0.1` alone is not enough when the bridge runs on a
different host.

### Authentication against the API

The bridge calls `GET {DDLNZB_API_URL}/api/v1/links/{release_id}` with the
header `X-API-Key`. Every call counts against the **daily download limit** of the
user tier. The bridge must therefore not poll aggressively.

The `release_id` is encoded into the path segment with `quote(..., safe="")` — it
comes from a base64 payload and is checked as a UUID **before** it is
interpolated. A payload that yields no UUID is discarded.

### The settings file counts at startup

The bridge has three configuration layers: `settings file > environment > default`.
The file is written by the web UI (`DDLNZB_DATA_DIR/settings.json`, mounted to
`/app/data` in the image).

The image runs as UID:GID `99:100` (`nobody:users`) to match Unraid's default
appdata ownership, and creates `/app/data` owned by that identity so a new
Docker named volume is writable. Bind-mounted host directories must be
writable by UID 99 and GID 100; existing mounts retain their prior ownership.

Until 24.10.2026 the file was only read by the **write** routes of that UI: after
a restart the bridge started with the environment alone — the key was empty, a
setup token went into the log and every *arr call was answered with 401, although
the file was mounted and the documentation promised the opposite (#715). The file
is now applied in `BridgeConfig.__post_init__` and again in `config.reload()` at
the start of the lifespan.

The CORS allowlist (`BRIDGE_CORS_ORIGINS`) is the one setting that the settings
file cannot reach — it is an environment-only value, so a web form can never
widen the list of browser origins that may talk to the bridge.

### Payload format

The backend generates a base64url-encoded string in
`../backend/app/scrapers/utils/payload.py`. The format is **JSON**:

```text
title|source|mirror|size_mb|password|imdb_id
```

`release_id` is the **release UUID** — not a URL. The bridge resolves exactly
this release (`GET /api/v1/links/{release_id}`).

⚠️ **Until 24.10.2026 it was a `|` format** (`title|source|mirror|size_mb|
password|imdb_id`). A `|` in the release title — which occurs regularly —
shifted every following field by one position. The title thus decided which
release the bridge resolves and with which password it unpacks it (Issue #729).
The JSON format has no delimiter.

**The legacy format is only read, and only when it is unambiguous:** exactly
six fields **and** a UUID in field 1. A title with `|` produces seven or more
fields and is rejected — exactly that ambiguity was the finding. A payload
that is neither JSON nor a valid legacy payload is **not** guessed: the
download aborts with an error instead of loading a wrong release.

⚠️ **There is exactly one decoder.** `app/sabnzbd_api.py` (`decode_payload`)
and `app/download_handler.py` call the **same** function. Until 24.10.2026 the
decode logic existed twice; the copies could drift apart. Whoever changes the
format changes **one** place.

The payload is in the NZB document twice: in the `<meta type="ddlnzb_payload">`
and in the `<payload>` element. The bridge reads the meta first and falls back
to the element — **not** to an arbitrary `<payload>` anywhere: the file comes
from outside, a foreign NZB could smuggle in such an element.

### Three payload formats, one decoder

`decode_payload()` reads **three** formats, and it is the only place that
knows them:

| Format | Condition |
| --- | --- |
| **JSON (version 2)** | the current state; `v` must be `PAYLOAD_VERSION` |
| **Legacy pipe format** | only with exactly `LEGACY_FIELD_COUNT` fields **and** a UUID in field 1 |
| **Download token** | `<base64url(payload)>.<base64url(signature)>` with `v == DOWNLOAD_TOKEN_VERSION` |

The release ID is checked as a UUID in **all** formats — the backend knows no
other IDs, and the API call carries the ID in the path.

**The download token** comes from the NZB link in the Newznab feed. Since #694
that link carries no API key any more, but a short-lived signed token
(`backend/app/services/download_token.py`, TTL 2 h). The token carries the
**release ID only** — no title, no password, no size (#789).

The bridge reads the token **without verifying the signature**: it does not
know the secret of the backend and does not need it — it resolves the release
through its own service authorization (`GET /api/v1/links/{release_id}` with
`X-API-Key`). The token gives it the release ID alone.

⚠️ **The missing signature check is not an authorization bypass.** The
boundary of this hop is the **API key of the bridge**: every mode of the
dispatch is gated by `secrets.compare_digest(apikey, config.DDLNZB_API_KEY)`,
and that same key resolves the release against the backend, where
`GET /api/v1/links/{release_id}` selects the release by UUID **without an
ownership check**. Whoever could forge a token already holds a key that
resolves any release directly — a signature check here would grant no further
permission. The signature is verified where it protects something: the
backend checks the token in `/api/download`, and only that path hands out the
NZB file. Copying the signing secret into the bridge would put it on a second
host — and this directory is mirrored into a public repository.

⚠️ **The dot must be detected before the base64 decode.** `.` is not a
base64url character, and `urlsafe_b64decode` **silently discards** characters
outside the alphabet — a decode first would build a nonsense string out of
body and signature. The token branch therefore runs before the base64 branch.

**A payload that cannot be decoded is not queued.** `mode=addurl` and
`mode=addfile` answer with `"Unreadable DDLnzb payload"` and the queue stays
empty. Before #789 the entry went into the queue without a release ID and the
download ended later with "Invalid payload".

### Title, password and size come from the link endpoint

The download token carries the release ID only. Title, password and size stand
in the answer of `GET /api/v1/links/{release_id}` — and the bridge takes them
from there when the payload does not carry them itself
(`app/download_handler.py::_fetch_release_data`).

The bridge **does not fetch the URL** of the NZB link. It resolves the
release through the API with its own service key — that keeps this path free
of a request against an arbitrary host.

### The heartbeat state follows the manual reconnect

`POST /ui/reconnect` reconnects JDownloader with the current credentials. It
also updates the heartbeat state, in both directions (#681):

- **success** → `jd_heartbeat.mark_connected()`: the error of an earlier
  attempt is cleared, the counter of failed pings goes back to zero.
- **failure** → `jd_heartbeat.mark_failed(detail)`: the **current** reason is
  taken over — the JDownloader error text, "credentials are not configured"
  or "device not found" — instead of a generic hint.

Before that the state was only cleaned up in `_ping()`, and `_ping()` runs
**only with active downloads** in the queue. Without a download a stale error
stood in `/status` forever and `is_healthy` stayed `False` although the
connection worked.

⚠️ **A manual reconnect does not count as a failed ping.** `mark_failed()`
leaves the counter untouched on purpose — it belongs to the ping attempts of
the heartbeat. A counter that the manual reconnect pushes over
`MAX_RECONNECT_ATTEMPTS` would stop the automatic reconnect for good
("manual intervention required").

### Image architectures

The image is **multi-architecture**: its tag (`:latest` or a commit tag like
`:<sha12>`) points to a manifest list with `linux/arm64` **and** `linux/amd64`.

**Where it is built:** in the **public repository `DDLnzb/bridge`**, not in this
monorepo. The CI of that repo builds both platforms on GitHub-hosted runners —
the arm64 branch natively on `ubuntu-24.04-arm`, the amd64 branch natively on
`ubuntu-latest`. After the push it checks the manifest itself — if one of the
two platforms is missing, the job goes red.

The monorepo keeps the **code** as the source of truth and mirrors it into that
repo (`sync-bridge-public`); the job `docker-bridge` in this repo's `ci.yml` was
removed on 25.10.2026 because it built the same image a second time. The image
name is `ghcr.io/ddlnzb/bridge` (not `ghcr.io/weedo078/ddlnzb-bridge`).
`docker-compose.prod.yml` pulls it with `BRIDGE_IMAGE_TAG` (default `:latest`),
because the Monorepo version tag (`v1.1.x`) does not exist in that repo.

⚠️ **The manifest list must not be flattened.** A `docker pull` + `tag` + `push`
on an ARM64 host pulls only the arm64 variant and loses the amd64 variant. The
alias in the `release` job of this repo therefore touches only `ddlnzb-web`
(single-platform); the bridge is not aliased here at all.

### The image build needs no HTTP

The `Dockerfile` rewrites the Debian repositories to **HTTPS** before
`apt-get update` runs (`sed -i 's|http://deb.debian.org|https://deb.debian.org|g'`
on `/etc/apt/sources.list` and `/etc/apt/sources.list.d/debian.sources`).

Reason (#850): the ops runner (`ddlnzb-ec2-ops`) cannot reach outbound traffic
on **port 80** — the TCP connect to `deb.debian.org:80` times out, while port
443 works. Without the rewrite the build fails at `apt-get update` with exit
code 100, and with it the whole chain `auto-tag` → `docker-backend` → `release`
→ `deploy-coolify`: a tag is created, but no release and no deploy to `ddlnzb-dev`
happen.

⚠️ **A green `docker-backend` on `main` proves nothing here.** As long as the
cached layer of the `apt` step is hit, the step never runs at all — the failure
only becomes visible when that layer has to be rebuilt. Check a changed package
list once **without cache** (`docker build --no-cache`), otherwise nothing was
verified.

`ca-certificates` is already part of the `python:3.11-slim` base image and `apt`
speaks HTTPS natively — no extra certificate package is needed. The contract is
written down in `backend/tests/test_dockerfile_apt_sources.py` and covers the
backend Dockerfile the same way.

### Ports

The SABnzbd port is **6789** (published as `6789:6789` in
`docker-compose.prod.yml`). `BRIDGE_PORT` on the other hand has the default
`8080`, which the Dockerfile overrides with `--port 6789`. The `.env.example`
describes `BRIDGE_PORT` — the Dockerfile takes precedence.

The web UI hangs on the **same** port under the prefix `/ui`; the static files
live under `/ui/static`. There is no second port.

## Work Guidance

- All `app/` modules import absolutely as `from app.…`. Therefore start only
  from the `bridge/` root or with `PYTHONPATH=bridge`. CI uses
  `working-directory: bridge`.
- `myjdapi` is a **synchronous** client. The bridge wraps it with
  `asyncio.to_thread`. Every new JDownloader method has to stay within that
  pattern, otherwise the event loop blocks.
- `CATEGORY_MAPPING` is wired in `BridgeConfig.__post_init__`. The comment in
  `.env.example` describing a JSON string override is **not implemented** — the
  example value misleads.
- Two persistence mechanisms run in parallel: `sabnzbd_api._persist_state()`
  writes the complete queue + history into `STATE_PATH`, `persistence.QueuePersistence`
  writes only the queue to `DATA_DIR/queue.json` (with atomic write via `.tmp` +
  `replace()`). Both are used independently in the lifespan.
- The state file keeps the size in **bytes** (`size_bytes`, `sizeleft_bytes`),
  not as text. Older files with `size`/`sizeleft` are converted on load via
  `_bytes_aus_groessen_text()`. Unknown fields in the file are discarded
  (`_bekannte_felder()`) — otherwise `_load_state()` swallows the error and the
  queue is **silently empty** after a restart.

## Verification

```bash
cd bridge && poetry run ruff check .
poetry run python -m pytest tests/ -v
```

⚠️ `tests/test_smoke.py` is a placeholder (`assert True`). The `bridge` job in
`ci.yml` runs `poetry run pytest -v` — so the tests do run: 17 test files with
142 test cases (as of 30.10.2026). Covered are auth, API
dispatch, cloud links, queue transitions, state restore, the poll intervals of
`_monitor_jdownloader` (with a stubbed JDownloader, not against a real
connection), the payload format (`tests/test_payload.py`: JSON v2, legacy
format, UUID check of the release ID — see „Payload format") and the web UI
(`tests/test_webui.py`: initial setup with setup token, login and session,
saving the configuration, reset, protection of secrets) plus the SABnzbd
compatibility (`tests/test_sabnzbd_compat.py`: `value`/`name=delete`, size as
numbers, status vocabulary).

Two files came with the security review of 24.10.2026: `test_settings_startup.py`
(the settings file is applied when the process starts — the key is effective
without any UI write, no setup token is generated, the file wins over the
environment) and `test_cors_policy.py` (a foreign origin receives no CORS
header, its preflight is refused, a configured origin is allowed).

`tests/test_deployment_contract.py` pins the deployment against the compose
file of the production stack and the versioned `bridge/.env`. Since 24.10.2026
it also holds the rule that **`environment` wins over `env_file`**: the API key
must not appear in the `environment` section of the bridge service, because an
empty entry there overwrites the key from `bridge/.env` with an empty string
(#716). The bridge then starts without a key and rejects every SABnzbd call.

Four files came with the batch of 30.10.2026:

- `test_default_urls.py` — one implementation, one default: `src/` must not
  exist, no host without a DNS record may appear in a shipped file, the API
  default may appear in exactly one Python file, and the live host must
  resolve (#879).
- `test_addurl_token.py` — the download token of the NZB link: the queue entry
  gets the release ID, an unreadable payload is refused instead of queued, and
  title/password/size are taken from the link endpoint (#789).
- `test_ui_reconnect.py` — the manual reconnect updates the heartbeat state in
  both directions, and a failed reconnect does not count as a failed ping
  (#681).
- `test_jdownloader_reconfigure.py` — `reconfigure()` names the **current**
  reason in every failure branch: missing credentials, a failed login, a
  missing device. A branch that returned `False` without setting `last_error`
  would leave the error of an earlier attempt standing in the status (#681).

Not tested is **`app/heartbeat.py::_heartbeat_loop`** — the heartbeat client.
The two tests with "heartbeat" in their name (`test_bridge_auth.py`) only check
that `/heartbeat/reconnect` answers with 401 without a valid key; the loop
itself runs in no test. The queue persistence is untested as well. A green
bridge job says nothing about these paths.

`tests/conftest.py` provides `state_file` (state file in `tmp_path`, sets
`DDLNZB_STATE_FILE`) and `fresh_import` (reloads `app.config` and
`app.sabnzbd_api` so that `STATE_PATH` is read again). New tests that touch the
state use these fixtures instead of building their own.

⚠️ **Not every test may use `fresh_import()`.** `download_handler._monitor_jdownloader()`
reaches the `download_queue` through a module reference. `fresh_import()` creates
a new dict that the handler does not see — the entry is missing, the loop
returns immediately and the test would be silently green. Such tests populate
`app.sabnzbd_api.download_queue` directly and then verify that `get_packages`
was really called.

⚠️ **Tests that go through the HTTP API must not import `app.sabnzbd_api` at
test time.** `fresh_import()` removes the module from `sys.modules` and imports
it again — but the router in `app.main` holds the **old** module instance. An
`import app.sabnzbd_api` **inside** the test function then yields a different
module: the test fills a queue the router does not see and the responses come
back empty. The import therefore belongs at **module level** of the test file —
then it binds the instance that `app.main` also uses. This affects every test
that goes through `/api` (`tests/test_sabnzbd_compat.py`).

For tests: they only start from the `bridge/` root, because the modules are
imported absolutely as `from app.…`. **Use `python -m pytest` for this, not the
`pytest` script.** The script does not put the current directory on `sys.path`;
if it puts an old installation there (e.g. a `.pth` file from an earlier
`poetry install` into a foreign directory), pytest imports `app.*` from there
instead of the working tree. That silently tests the wrong code and then fails
for reasons that do not exist in the working tree. Counter-check in case of
doubt:

```bash
python -c "import app.sabnzbd_api as m; print(m.__file__)"
```

The path must point into the working tree.

`pyproject.toml` and `poetry.lock` are the only dependency source of the
bridge. A `requirements.txt` existed until 2026-10-19, was read by no build
path and differed from the lockfile; it has been removed. **Do not create a
second declaration** — Dependabot would scan both and produce duplicate PRs, of
which only one changes the installed version.

`prometheus-client` (>=0.24.1,<0.27.0) has been a bridge dependency since
PR #210: `app/metrics.py` exports `ddlnzb_bridge_*`, and the monitoring stack
scrapes the bridge via `GET /metrics`.

## Child DOX Index

| Child | Scope |
| --- | --- |
| [`app/AGENTS.md`](app/AGENTS.md) | Production implementation with SABnzbd API and JDownloader |
