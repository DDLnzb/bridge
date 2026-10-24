"""
DDLnzb Bridge - Persistente Laufzeit-Konfiguration

Der Bridge liest seine Konfiguration bisher aus Umgebungsvariablen und der
`bridge/.env`. Beides ist nur beim Start des Prozesses wirksam - eine
Aenderung erfordert einen Neustart des Containers.

Dieses Modul ergaenzt einen dritten Layer: eine JSON-Datei mit Overrides, die
zur Laufzeit ueber die Web-Oberflaeche geschrieben werden kann. Sie liegt in
`DDLNZB_DATA_DIR` (Standard: `<bridge>/data/settings.json`) und ist damit im
Docker-Image unter `/app/data` - dieser Pfad muss gemountet werden, sonst
geht die Konfiguration beim Container-Wechsel verloren.

Der Vorrang ist bewusst:

    Settings-Datei  >  Umgebungsvariable  >  Default

Die Umgebungsvariable bleibt damit die Quelle fuer Deployment-spezifische
Festlegungen (z. B. `DDLNZB_API_URL` aus dem Compose-File), waehrend die
Oberflaeche die Werte zur Laufzeit ueberschreiben kann.

⚠️ **Secrets liegen im Klartext in der Datei.** Das ist dieselbe Entscheidung
wie bei den versionierten `.env`-Dateien im Repo: der Bridge laeuft lokal auf
dem Betreiber-Host. Die Datei wird deshalb mit `0600` angelegt.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ddlnzb.bridge.settings")

BASE_DIR = Path(__file__).resolve().parent.parent

SETTINGS_VERSION = 1

# Alle ueber die Oberflaeche konfigurierbaren Felder und ihr Typ.
# "secret" heisst: der Wert wird nie zurueck in die Oberflaeche gespiegelt.
#
# Die Schluessel sind die **Feldnamen** aus `app.config.BridgeConfig` - nicht
# die Namen der Umgebungsvariablen. Die beiden unterscheiden sich bei
# `LOG_LEVEL` (Umgebung: `BRIDGE_LOG_LEVEL`), und ein Mix aus beidem wuerde
# beim Reload stillschweigend ins Leere laufen.
#
# `DDLNZB_API_URL` steht hier bewusst **nicht** drin: die API-URL bringt der
# Bridge selbst mit (Default `https://ddlnzb.pages.dev/api/app`). Sie ist eine
# Deployment-Festlegung und wird ueber die Umgebung gesetzt, nicht ueber die
# Oberflaeche - sonst koennte ein Klick in der Web-UI den Bridge auf einen
# beliebigen Host umbiegen.
FIELD_TYPES: dict[str, str] = {
    "DDLNZB_API_KEY": "secret",
    "MYJD_EMAIL": "str",
    "MYJD_PASSWORD": "secret",
    "MYJD_DEVICE_NAME": "str",
    "MYJD_APP_KEY": "str",
    "DOWNLOAD_DIR": "str",
    "EXTRACT_ARCHIVES": "bool",
    "DELETE_ARCHIVES": "bool",
    "LOG_LEVEL": "str",
}

# Formularfeld -> Settings-Schluessel. Das Formular benutzt kurze Namen, die
# Settings-Datei die Feldnamen der Config.
FORM_FIELDS: dict[str, str] = {
    "api_key": "DDLNZB_API_KEY",
    "jd_email": "MYJD_EMAIL",
    "jd_password": "MYJD_PASSWORD",
    "jd_device": "MYJD_DEVICE_NAME",
    "jd_app_key": "MYJD_APP_KEY",
    "download_dir": "DOWNLOAD_DIR",
    "extract_archives": "EXTRACT_ARCHIVES",
    "delete_archives": "DELETE_ARCHIVES",
    "log_level": "LOG_LEVEL",
}

SECRET_FIELDS = frozenset(name for name, kind in FIELD_TYPES.items() if kind == "secret")

# Werte, die der Bridge zwingend zur Verfuegung stehen muessen, sonst kann sie
# keinen einzigen Aufruf bedienen (leere Konfiguration schliesst - siehe
# `app/main.py`, `require_bridge_key`).
REQUIRED_FIELDS = ("DDLNZB_API_KEY",)


def settings_path() -> Path:
    """Pfad der Settings-Datei.

    `DDLNZB_SETTINGS_FILE` gewinnt, danach `DDLNZB_DATA_DIR`, danach der
    Standard `bridge/data/`. Bewusst ohne Import von `app.config` - sonst
    entstuende ein Zyklus, weil `config` die Settings beim Reload laedt.
    """
    explicit = os.getenv("DDLNZB_SETTINGS_FILE")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = os.getenv("DDLNZB_DATA_DIR") or str(BASE_DIR / "data")
    return Path(data_dir).expanduser() / "settings.json"


def load() -> dict[str, Any]:
    """Gespeicherte Overrides laden.

    Eine kaputte Datei ist kein Grund, den Start zu verweigern: der Bridge
    laeuft dann mit den Werten aus der Umgebung weiter. Der Fehler wird
    geloggt, nicht geworfen.
    """
    path = settings_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Settings-Datei nicht lesbar (%s): %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Settings-Datei hat unerwartetes Format: %s", path)
        return {}

    values = raw.get("values")
    if not isinstance(values, dict):
        return {}

    known = {key: value for key, value in values.items() if key in FIELD_TYPES}
    unknown = set(values) - set(known)
    if unknown:
        logger.warning("Unbekannte Settings-Schluessel ignoriert: %s", sorted(unknown))
    return known


def save(values: dict[str, Any]) -> dict[str, Any]:
    """Overrides schreiben und die Datei atomar ersetzen.

    Leerer Wert heisst "nicht gesetzt": der Eintrag wird entfernt, damit die
    Umgebungsvariable wieder greift. Die Rueckgabe ist der effective
    Speicherzustand, nicht das, was gesendet wurde.
    """
    path = settings_path()
    current = load()

    for key, value in values.items():
        if key not in FIELD_TYPES:
            continue
        if value is None or value == "":
            current.pop(key, None)
        else:
            current[key] = value

    payload = {"version": SETTINGS_VERSION, "values": current}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    _restrict_permissions(path)

    logger.info(
        "Settings gespeichert (%s): %s",
        path,
        ", ".join(sorted(current)) or "keine",
    )
    return current


def clear() -> None:
    """Alle Overrides entfernen - die Umgebungsvariablen gelten wieder."""
    path = settings_path()
    if path.exists():
        path.unlink()
        logger.info("Settings zurueckgesetzt: %s", path)


def _restrict_permissions(path: Path) -> None:
    """Datei auf `0600` ziehen. Best effort - kein Grund fuer einen Absturz."""
    try:
        path.chmod(0o600)
    except OSError as exc:  # pragma: no cover - je nach Dateisystem
        logger.debug("Konnte Dateirechte nicht setzen (%s): %s", path, exc)
