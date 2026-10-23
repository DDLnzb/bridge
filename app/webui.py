"""
DDLnzb Bridge - Web-Oberflaeche fuer die Einrichtung

Eine kleine, serverseitig gerenderte Oberflaeche, ueber die sich der Bridge
konfigurieren laesst: API-Key, JDownloader-Zugang, Download-Zielordner und
Log-Stufe. Sie ersetzt kein Container-Deployment - sie macht die Konfiguration
aber bequem, ohne die `.env` auf dem Host zu oeffnen.

## Zugang

Der Bridge hat genau einen Schluessel: `DDLNZB_API_KEY`. Derselbe Wert gilt
fuer die SABnzbd-API der *arr-Apps und fuer diese Oberflaeche.

- **Ersteinrichtung** (`/ui`): solange kein Key konfiguriert ist, zeigt die
  Seite das Setup-Formular. Es verlangt den Setup-Token aus dem Container-Log
  - ohne Token kann niemand die Bridge uebernehmen, auch nicht aus dem LAN.
- **Anmeldung**: danach wird der Key als Passwort eingegeben. Er wird nicht
  gespeichert, sondern als session cookie abgelegt. Das Cookie enthaelt nur
  eine HMAC-Ableitung des Keys, nicht den Key selbst.

## Schutzmechanismen

- Session-Cookie: `HttpOnly`, `SameSite=Strict`, `Path=/ui`. `SameSite=Strict`
  ist zugleich der CSRF-Schutz - ein fremder Origin kann kein Cookie mitschicken.
- Der Vergleich des Keys laeuft ueber `secrets.compare_digest`, nie mit `==`.
- Geheimnisse werden nie zurueck in die Oberflaeche gespiegelt: ein gesetztes
  Passwort erscheint als Platzhalter, nicht als Wert.
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
from app.jdownloader import jdownloader
from app.sabnzbd_api import download_queue
from app.webui_templates import dashboard_page, login_page, setup_page

logger = logging.getLogger("ddlnzb.bridge.webui")

router = APIRouter(prefix="/ui")

SESSION_COOKIE = "ddlnzb_ui"
SESSION_TTL = 7 * 24 * 60 * 60

_STARTED_AT = datetime.now(timezone.utc)

# Setup-Token: gilt nur, solange kein API-Key konfiguriert ist. Wird pro
# Prozess neu erzeugt - ein Neustart invalidiert einen alten Token.
_SETUP_TOKEN: str | None = None

# Letzte Ergebnisse der Verbindungstests (prozesslokal, nur Anzeige).
_LAST_TEST: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------- Auth


def setup_token() -> str:
    """Setup-Token liefern und beim ersten Aufruf erzeugen.

    Ein Token aus `DDLNZB_SETUP_TOKEN` gewinnt - damit laesst sich die
    Ersteinrichtung in automatisierten Deployments ohne Log-Lesen steuern.
    """
    global _SETUP_TOKEN
    if _SETUP_TOKEN is None:
        _SETUP_TOKEN = os.getenv("DDLNZB_SETUP_TOKEN") or secrets.token_urlsafe(12)
        logger.warning(
            "Bridge ist nicht konfiguriert - Setup-Token: %s "
            "(Einrichtung ueber http://<host>:6789/ui)",
            _SETUP_TOKEN,
        )
    return _SETUP_TOKEN


def _session_token(api_key: str) -> str:
    """Session-Cookie-Wert: HMAC des Keys, nicht der Key selbst."""
    return hmac.new(
        api_key.encode("utf-8"), b"ddlnzb-bridge-ui-session-v1", hashlib.sha256
    ).hexdigest()


def _session_valid(request: Request) -> bool:
    """Traegt der Request ein gueltiges Session-Cookie?"""
    api_key = config.DDLNZB_API_KEY
    if not api_key:
        return False
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return False
    return secrets.compare_digest(cookie, _session_token(api_key))


def _presented_key(request: Request) -> str:
    """Direkt mitgeschickter Key (Header oder Query) - fuer Maschinenclients."""
    return request.headers.get("X-API-Key") or request.query_params.get("apikey") or ""


async def require_ui_auth(request: Request) -> None:
    """Zugriffsschutz fuer die schreibenden Endpunkte.

    Zwei Wege: das Session-Cookie der Oberflaeche oder der Key direkt im
    Header. Beide werden zeitkonstant verglichen. Ohne konfigurierten Key ist
    **kein** Aufruf gueltig - eine leere Konfiguration schliesst.
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
    """Ergebnis eines Verbindungstests fuer die Anzeige merken."""
    _LAST_TEST[kind] = {
        "ok": ok,
        "detail": detail,
        "at": datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S"),
    }


def _test_badge(kind: str) -> tuple[str, str]:
    """(Label, Badge-Klasse) fuer den zuletzt gemessenen Zustand."""
    result = _LAST_TEST.get(kind)
    if not result:
        return "unbekannt", ""
    if result["ok"]:
        return "erreichbar", "ok"
    return "nicht erreichbar", "err"


def _view(request: Request) -> dict[str, Any]:
    """Datenbasis fuer die Hauptseite."""
    stored = settings_store.load()
    queued = sum(1 for item in download_queue.values() if getattr(item, "status", "") == "Queued")
    downloading = sum(
        1 for item in download_queue.values() if getattr(item, "status", "") == "Downloading"
    )

    api_label, api_kind = _test_badge("api")
    jd_connected = jdownloader.device_id is not None
    jd_label, jd_kind = ("verbunden", "ok") if jd_connected else ("nicht verbunden", "warn")

    mapping = config.CATEGORY_MAPPING or {}
    categories = ", ".join(f"{key} → {value}" for key, value in list(mapping.items())[:3])

    return {
        "flash": request.query_params.get("flash"),
        "api_url": config.DDLNZB_API_URL,
        "api_key_set": bool(config.DDLNZB_API_KEY),
        "api_reachable": api_label == "erreichbar",
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
        "config_source": "Settings-Datei" if stored else "Umgebung",
        "settings_path": str(settings_store.settings_path()),
        "started_at": _STARTED_AT.astimezone().strftime("%d.%m.%Y %H:%M:%S"),
        "version": "1.0.0",
        "category_summary": f"{categories} (+{max(len(mapping) - 3, 0)} weitere)"
        if len(mapping) > 3
        else categories,
    }


# ------------------------------------------------------------------ Routen


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Startseite: Setup, Anmeldung oder Dashboard."""
    if not config.DDLNZB_API_KEY:
        return HTMLResponse(setup_page())
    if not _session_valid(request):
        return HTMLResponse(login_page())
    return HTMLResponse(dashboard_page(_view(request)))


@router.post("/login")
async def login(request: Request):
    """Anmeldung mit dem Bridge-Key."""
    form = await _form_values(request)
    presented = str(form.get("key") or "")
    expected = config.DDLNZB_API_KEY

    if not expected:
        return RedirectResponse("/ui", status_code=303)

    if not secrets.compare_digest(presented, expected):
        return HTMLResponse(login_page("Ungueltiger API-Key."), status_code=401)

    response = RedirectResponse("/ui", status_code=303)
    _set_session(response, expected)
    return response


@router.post("/logout")
async def logout():
    """Session beenden."""
    response = RedirectResponse("/ui", status_code=303)
    _clear_session(response)
    return response


@router.post("/setup")
async def setup(request: Request):
    """Ersteinrichtung: Setup-Token pruefen und die Grundkonfiguration speichern."""
    global _SETUP_TOKEN

    if config.DDLNZB_API_KEY:
        # Schon konfiguriert - die Ersteinrichtung ist erledigt.
        return RedirectResponse("/ui", status_code=303)

    form = await _form_values(request)
    presented = str(form.get("setup_token") or "")
    expected = setup_token()

    if not presented or not secrets.compare_digest(presented, expected):
        return HTMLResponse(setup_page("Ungueltiger Setup-Token."), status_code=401)

    api_key = str(form.get("api_key") or "").strip()
    if not api_key:
        return HTMLResponse(setup_page("Der DDLnzb-API-Key ist Pflicht."), status_code=400)

    values: dict[str, Any] = {"DDLNZB_API_KEY": api_key}
    for form_name, field in settings_store.FORM_FIELDS.items():
        if field == "DDLNZB_API_KEY":
            continue
        raw = str(form.get(form_name) or "").strip()
        if raw:
            values[field] = raw

    settings_store.save(values)
    config.reload()

    # Der Token ist verbraucht - eine zweite Nutzung ist damit ausgeschlossen.
    _SETUP_TOKEN = None

    response = RedirectResponse("/ui", status_code=303)
    _set_session(response, config.DDLNZB_API_KEY)
    logger.info("Ersteinrichtung abgeschlossen, %d Werte gespeichert", len(values))
    return response


@router.post("/settings", dependencies=[Depends(require_ui_auth)])
async def save_settings(request: Request):
    """Konfiguration aus dem Formular uebernehmen."""
    form = await _form_values(request)
    values = _collect_values(form)

    try:
        settings_store.save(values)
    except OSError as exc:
        logger.error("Settings konnten nicht gespeichert werden: %s", exc)
        return RedirectResponse("/ui?flash=error", status_code=303)

    config.reload()

    # Neue Zugangsdaten sofort uebernehmen: der alte myjdapi-Client haelt
    # die alten Daten und wuerde sonst bis zum Neustart weiterverwenden.
    if any(key in values for key in ("MYJD_EMAIL", "MYJD_PASSWORD", "MYJD_DEVICE_NAME")):
        await jdownloader.reconfigure()

    response = RedirectResponse("/ui?flash=saved", status_code=303)
    if config.DDLNZB_API_KEY:
        _set_session(response, config.DDLNZB_API_KEY)
    return response


@router.post("/reset", dependencies=[Depends(require_ui_auth)])
async def reset_settings():
    """Alle Overrides loeschen - es gelten wieder die Werte aus der Umgebung."""
    settings_store.clear()
    config.reload()
    return RedirectResponse("/ui?flash=reset", status_code=303)


@router.post("/test", dependencies=[Depends(require_ui_auth)])
async def test_connections():
    """Erreichbarkeit von API und JDownloader pruefen."""
    await _check_api()
    await _check_jdownloader()
    return _LAST_TEST


@router.post("/reconnect", dependencies=[Depends(require_ui_auth)])
async def reconnect():
    """JDownloader mit den aktuellen Zugangsdaten neu verbinden."""
    ok = await jdownloader.reconfigure()
    detail = (
        "verbunden"
        if ok
        else "keine Verbindung - Zugangsdaten oder Geraete-Name pruefen"
    )
    record_test("jdownloader", ok, detail)
    return {"ok": ok, "detail": detail}


# --------------------------------------------------------------- Hilfsdinge


def _collect_values(form: dict[str, str]) -> dict[str, Any]:
    """Formularfelder in Settings-Werte uebersetzen.

    Leere Textfelder werden **nicht** uebernommen: ein leeres Feld bedeutet
    "unveraendert lassen", nicht "loeschen". Nur der Reset-Knopf leert die
    Konfiguration. Checkboxen werden immer uebernommen - ein abgesetzter
    Haken ist ein Wert, kein leeres Feld.
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
    """Formularwerte auslesen - ohne `python-multipart`.

    Die Bridge kommt bewusst ohne diese Abhaengigkeit aus (sie liest auch beim
    `mode=addfile` den Roh-Body). Hier wird deshalb der Body selbst
    ausgewertet: `application/x-www-form-urlencoded` ueber `parse_qs`, JSON
    ueber `json.loads`.
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
    """Health-Endpoint der DDLnzb-API abfragen."""
    url = f"{config.DDLNZB_API_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
        ok = response.status_code == 200
        detail = f"HTTP {response.status_code} von {url}"
    except Exception as exc:
        ok = False
        detail = f"{url} nicht erreichbar ({exc})"
    record_test("api", ok, detail)
    return ok


async def _check_jdownloader() -> bool:
    """JDownloader-Status abfragen."""
    if not config.MYJD_EMAIL or not config.MYJD_PASSWORD:
        record_test("jdownloader", False, "Keine JDownloader-Zugangsdaten konfiguriert")
        return False
    try:
        devices = await jdownloader.get_devices()
        names = [device.get("name", "") for device in devices.get("list", [])]
        ok = bool(names)
        detail = ", ".join(names) if names else "Keine Geraete im JDownloader-Account"
    except Exception as exc:
        ok = False
        detail = f"JDownloader nicht erreichbar ({exc})"
    record_test("jdownloader", ok, detail)
    return ok
