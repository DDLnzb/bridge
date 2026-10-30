"""
DDLnzb Bridge - Web UI for the setup

A small, server-rendered UI via which the bridge can be configured: API key,
JDownloader access, download target folder and log level. It does not replace
a container deployment - but it makes the configuration convenient without
opening the `.env` on the host.

## Access

The bridge has exactly one key: `DDLNZB_API_KEY`. The same value applies to the
SABnzbd API of the *arr apps and to this UI.

- **Initial setup** (`/ui`): as long as no key is configured, the page shows
  the setup form. It requires the setup token from the container log - without
  the token nobody can take over the bridge, not even from the LAN.
- **Login**: afterwards the key is entered as a password. It is not stored but
  kept as a session cookie. The cookie contains only an HMAC derivation of the
  key, not the key itself.

## Protection mechanisms

- Session cookie: `HttpOnly`, `SameSite=Strict`, `Path=/ui`. `SameSite=Strict`
  is at the same time the CSRF protection - a foreign origin cannot send the
  cookie along.
- The key comparison runs through `secrets.compare_digest`, never with `==`.
- Secrets are never mirrored back into the UI: a set password appears as a
  placeholder, not as a value.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import settings as settings_store
from app.config import config
from app.heartbeat import jd_heartbeat
from app.jdownloader import jdownloader
from app.sabnzbd_api import download_queue
from app.webui_templates import dashboard_page, login_page, setup_page

logger = logging.getLogger("ddlnzb.bridge.webui")

router = APIRouter(prefix="/ui")

SESSION_COOKIE = "ddlnzb_ui"
SESSION_TTL = 7 * 24 * 60 * 60

_STARTED_AT = datetime.now(timezone.utc)

# Setup token: valid only as long as no API key is configured. Generated anew
# per process - a restart invalidates an old token.
_SETUP_TOKEN: str | None = None

# Latest results of the connection tests (process-local, display only).
_LAST_TEST: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------- Auth


def setup_token() -> str:
    """Return the setup token and generate it on the first call.

    A token from `DDLNZB_SETUP_TOKEN` wins - that way the initial setup can be
    steered in automated deployments without reading the log.
    """
    global _SETUP_TOKEN
    if _SETUP_TOKEN is None:
        _SETUP_TOKEN = os.getenv("DDLNZB_SETUP_TOKEN") or secrets.token_urlsafe(12)
        logger.warning(
            "Bridge is not configured - setup token: %s (setup via http://<host>:6789/ui)",
            _SETUP_TOKEN,
        )
    return _SETUP_TOKEN


def _session_token(api_key: str) -> str:
    """Session cookie value: HMAC of the key, not the key itself."""
    return hmac.new(
        api_key.encode("utf-8"), b"ddlnzb-bridge-ui-session-v1", hashlib.sha256
    ).hexdigest()


def _session_valid(request: Request) -> bool:
    """Does the request carry a valid session cookie?"""
    api_key = config.DDLNZB_API_KEY
    if not api_key:
        return False
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return False
    return secrets.compare_digest(cookie, _session_token(api_key))


def _presented_key(request: Request) -> str:
    """Key sent directly (header or query) - for machine clients."""
    return request.headers.get("X-API-Key") or request.query_params.get("apikey") or ""


async def require_ui_auth(request: Request) -> None:
    """Access protection for the writing endpoints.

    Two ways: the session cookie of the web UI or the key directly in the
    header. Both are compared in constant time. Without a configured key
    **no** call is valid - an empty configuration closes everything.
    """
    api_key = config.DDLNZB_API_KEY
    if not api_key:
        raise HTTPException(status_code=401, detail="Bridge API key not configured")

    if _session_valid(request):
        return

    presented = _presented_key(request)
    if presented and secrets.compare_digest(presented, api_key):
        return

    raise HTTPException(status_code=401, detail="Invalid API key")


def _set_session(response, api_key: str) -> None:
    """Session-Cookie setzen."""
    response.set_cookie(
        SESSION_COOKIE,
        _session_token(api_key),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="strict",
        path="/ui",
    )


def _clear_session(response) -> None:
    """Session-Cookie entfernen."""
    response.delete_cookie(SESSION_COOKIE, path="/ui")


# ------------------------------------------------------------------- Status


def record_test(kind: str, ok: bool, detail: str) -> None:
    """Remember the result of a connection test for the display."""
    _LAST_TEST[kind] = {
        "ok": ok,
        "detail": detail,
        "at": datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S"),
    }


def _test_badge(kind: str) -> tuple[str, str]:
    """(Label, badge class) for the last measured state."""
    result = _LAST_TEST.get(kind)
    if not result:
        return "unknown", ""
    if result["ok"]:
        return "reachable", "ok"
    return "not reachable", "err"


def _view(request: Request) -> dict[str, Any]:
    """Data basis for the main page."""
    stored = settings_store.load()
    queued = sum(1 for item in download_queue.values() if getattr(item, "status", "") == "Queued")
    downloading = sum(
        1 for item in download_queue.values() if getattr(item, "status", "") == "Downloading"
    )

    api_label, api_kind = _test_badge("api")
    jd_connected = jdownloader.device_id is not None
    jd_label, jd_kind = ("connected", "ok") if jd_connected else ("not connected", "warn")

    mapping = config.CATEGORY_MAPPING or {}
    categories = ", ".join(f"{key} → {value}" for key, value in list(mapping.items())[:3])

    return {
        "flash": request.query_params.get("flash"),
        "api_url": config.DDLNZB_API_URL,
        "api_key_set": bool(config.DDLNZB_API_KEY),
        "api_reachable": api_label == "reachable",
        "api_label": api_label,
        "api_kind": api_kind,
        "jd_email": config.MYJD_EMAIL,
        "jd_password_set": bool(config.MYJD_PASSWORD),
        "jd_device": config.MYJD_DEVICE_NAME,
        "jd_device_name": jdownloader.connected_device or "",
        "jd_connected": jd_connected,
        "jd_label": jd_label,
        "jd_kind": jd_kind,
        "jd_app_key": config.MYJD_APP_KEY,
        "download_dir": config.DOWNLOAD_DIR,
        "extract_archives": config.EXTRACT_ARCHIVES,
        "delete_archives": config.DELETE_ARCHIVES,
        "log_level": config.LOG_LEVEL,
        "queue_total": len(download_queue),
        "queue_queued": queued,
        "queue_downloading": downloading,
        "config_source": "settings file" if stored else "environment",
        "settings_path": str(settings_store.settings_path()),
        "started_at": _STARTED_AT.astimezone().strftime("%d.%m.%Y %H:%M:%S"),
        "version": "1.0.0",
        "category_summary": f"{categories} (+{max(len(mapping) - 3, 0)} more)"
        if len(mapping) > 3
        else categories,
    }


# ------------------------------------------------------------------ Routen


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Start page: setup, login or dashboard."""
    if not config.DDLNZB_API_KEY:
        return HTMLResponse(setup_page())
    if not _session_valid(request):
        return HTMLResponse(login_page())
    return HTMLResponse(dashboard_page(_view(request)))


@router.post("/login")
async def login(request: Request):
    """Login with the bridge key."""
    form = await _form_values(request)
    presented = str(form.get("key") or "")
    expected = config.DDLNZB_API_KEY

    if not expected:
        return RedirectResponse("/ui", status_code=303)

    if not secrets.compare_digest(presented, expected):
        return HTMLResponse(login_page("Invalid API key."), status_code=401)

    response = RedirectResponse("/ui", status_code=303)
    _set_session(response, expected)
    return response


@router.post("/logout")
async def logout():
    """End the session."""
    response = RedirectResponse("/ui", status_code=303)
    _clear_session(response)
    return response


@router.post("/setup")
async def setup(request: Request):
    """Initial setup: check the setup token and store the basic configuration."""
    global _SETUP_TOKEN

    if config.DDLNZB_API_KEY:
        # Already configured - the initial setup is done.
        return RedirectResponse("/ui", status_code=303)

    form = await _form_values(request)
    presented = str(form.get("setup_token") or "")
    expected = setup_token()

    if not presented or not secrets.compare_digest(presented, expected):
        return HTMLResponse(setup_page("Invalid setup token."), status_code=401)

    api_key = str(form.get("api_key") or "").strip()
    if not api_key:
        return HTMLResponse(setup_page("The DDLnzb API key is required."), status_code=400)

    values: dict[str, Any] = {"DDLNZB_API_KEY": api_key}
    for form_name, field in settings_store.FORM_FIELDS.items():
        if field == "DDLNZB_API_KEY":
            continue
        raw = str(form.get(form_name) or "").strip()
        if raw:
            values[field] = raw

    settings_store.save(values)
    config.reload()

    # The token is used up - a second use is thereby excluded.
    _SETUP_TOKEN = None

    response = RedirectResponse("/ui", status_code=303)
    _set_session(response, config.DDLNZB_API_KEY)
    logger.info("Initial setup completed, %d values saved", len(values))
    return response


@router.post("/settings", dependencies=[Depends(require_ui_auth)])
async def save_settings(request: Request):
    """Take over the configuration from the form."""
    form = await _form_values(request)
    values = _collect_values(form)

    try:
        settings_store.save(values)
    except OSError as exc:
        logger.error("Settings could not be saved: %s", exc)
        return RedirectResponse("/ui?flash=error", status_code=303)

    config.reload()

    # Apply new credentials immediately: the old myjdapi client keeps the old
    # data and would otherwise use it until the restart.
    if any(key in values for key in ("MYJD_EMAIL", "MYJD_PASSWORD", "MYJD_DEVICE_NAME")):
        await jdownloader.reconfigure()

    response = RedirectResponse("/ui?flash=saved", status_code=303)
    if config.DDLNZB_API_KEY:
        _set_session(response, config.DDLNZB_API_KEY)
    return response


@router.post("/reset", dependencies=[Depends(require_ui_auth)])
async def reset_settings():
    """Delete all overrides - the values from the environment apply again."""
    settings_store.clear()
    config.reload()
    return RedirectResponse("/ui?flash=reset", status_code=303)


@router.post("/test", dependencies=[Depends(require_ui_auth)])
async def test_connections():
    """Check the reachability of API and JDownloader."""
    await _check_api()
    await _check_jdownloader()
    return _LAST_TEST


@router.post("/reconnect", dependencies=[Depends(require_ui_auth)])
async def reconnect():
    """Reconnect JDownloader with the current credentials.

    The heartbeat state is updated with the result (#681): a successful
    reconnect clears the error of an earlier attempt, a failed one carries
    the **current** reason instead of a generic hint.
    """
    ok = await jdownloader.reconfigure()
    if ok:
        jd_heartbeat.mark_connected()
        detail = "connected"
    else:
        detail = jdownloader.last_error or (
            "no connection - check the credentials or the device name"
        )
        jd_heartbeat.mark_failed(detail)
    record_test("jdownloader", ok, detail)
    return {"ok": ok, "detail": detail}


# --------------------------------------------------------------- Helpers


def _collect_values(form: dict[str, str]) -> dict[str, Any]:
    """Translate form fields into settings values.

    Empty text fields are **not** taken over: an empty field means "leave
    unchanged", not "delete". Only the reset button empties the configuration.
    Checkboxes are always taken over - an unchecked box is a value, not an
    empty field.
    """
    values: dict[str, Any] = {}
    for form_name, field in settings_store.FORM_FIELDS.items():
        kind = settings_store.FIELD_TYPES.get(field)
        if kind == "bool":
            values[field] = bool(form.get(form_name))
            continue
        raw = form.get(form_name)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            values[field] = value
    return values


async def _form_values(request: Request) -> dict[str, str]:
    """Read the form values - without `python-multipart`.

    The bridge deliberately does without that dependency (it also reads the
    raw body with `mode=addfile`). The body itself is therefore evaluated
    here: `application/x-www-form-urlencoded` via `parse_qs`, JSON via
    `json.loads`.
    """
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if not body:
        return {}

    if "application/json" in content_type:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if value is not None}

    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


async def _check_api() -> bool:
    """Query the health endpoint of the DDLnzb API."""
    url = f"{config.DDLNZB_API_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
        ok = response.status_code == 200
        detail = f"HTTP {response.status_code} von {url}"
    except Exception as exc:
        ok = False
        detail = f"{url} not reachable ({exc})"
    record_test("api", ok, detail)
    return ok


async def _check_jdownloader() -> bool:
    """JDownloader-Status abfragen."""
    if not config.MYJD_EMAIL or not config.MYJD_PASSWORD:
        record_test("jdownloader", False, "No JDownloader credentials configured")
        return False
    try:
        devices = await jdownloader.get_devices()
        names = [device.get("name", "") for device in devices.get("list", [])]
        ok = bool(names)
        detail = ", ".join(names) if names else "No devices in the JDownloader account"
    except Exception as exc:
        ok = False
        detail = f"JDownloader not reachable ({exc})"
    record_test("jdownloader", ok, detail)
    return ok
