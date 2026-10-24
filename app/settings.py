"""
DDLnzb Bridge - Persistent runtime configuration

The bridge reads its configuration from environment variables and `bridge/.env`
so far. Both are only effective when the process starts - a change requires a
restart of the container.

This module adds a third layer: a JSON file with overrides that can be written
at runtime via the web UI. It lives in `DDLNZB_DATA_DIR` (default:
`<bridge>/data/settings.json`) and therefore under `/app/data` in the Docker
image - that path must be mounted, otherwise the configuration is lost when the
container is replaced.

The precedence is deliberately:

    settings file  >  environment variable  >  default

The environment variable thus remains the source for deployment-specific
settings (e.g. `DDLNZB_API_URL` from the compose file), while the web UI can
override the values at runtime.

⚠️ **Secrets are stored in plain text in the file.** This is the same decision
as for the versioned `.env` files in the repo: the bridge runs locally on the
operator's host. The file is therefore created with `0600`.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ddlnzb.bridge.settings")

BASE_DIR = Path(__file__).resolve().parent.parent

SETTINGS_VERSION = 1

# All fields configurable via the web UI and their type.
# "secret" means: the value is never mirrored back into the web UI.
#
# The keys are the **field names** from `app.config.BridgeConfig` - not the
# environment variable names. The two differ for `LOG_LEVEL` (environment:
# `BRIDGE_LOG_LEVEL`), and mixing them would silently resolve to nothing on
# reload.
#
# `DDLNZB_API_URL` is deliberately **not** in here: the bridge ships the API
# URL itself (default `https://ddlnzb.pages.dev/api/app`). It is a deployment
# setting and is set via the environment, not via the web UI - otherwise a
# click in the web UI could redirect the bridge to any host.
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

# Form field -> settings key. The form uses short names, the settings file the
# field names of the config.
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

# Values the bridge must have available, otherwise it cannot serve a single
# call (an empty configuration closes everything - see `app/main.py`,
# `require_bridge_key`).
REQUIRED_FIELDS = ("DDLNZB_API_KEY",)


def settings_path() -> Path:
    """Path of the settings file.

    `DDLNZB_SETTINGS_FILE` wins, then `DDLNZB_DATA_DIR`, then the default
    `bridge/data/`. Deliberately without importing `app.config` - that would
    create a cycle, because `config` loads the settings on reload.
    """
    explicit = os.getenv("DDLNZB_SETTINGS_FILE")
    if explicit:
        return Path(explicit).expanduser()
    data_dir = os.getenv("DDLNZB_DATA_DIR") or str(BASE_DIR / "data")
    return Path(data_dir).expanduser() / "settings.json"


def load() -> dict[str, Any]:
    """Load the stored overrides.

    A broken file is no reason to refuse to start: the bridge then keeps
    running with the values from the environment. The error is logged, not
    raised.
    """
    path = settings_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Settings file not readable (%s): %s", path, exc)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Settings file has unexpected format: %s", path)
        return {}

    values = raw.get("values")
    if not isinstance(values, dict):
        return {}

    known = {key: value for key, value in values.items() if key in FIELD_TYPES}
    unknown = set(values) - set(known)
    if unknown:
        logger.warning("Unknown settings keys ignored: %s", sorted(unknown))
    return known


def save(values: dict[str, Any]) -> dict[str, Any]:
    """Write the overrides and atomically replace the file.

    An empty value means "not set": the entry is removed, so that the
    environment variable applies again. The return value is the effective
    stored state, not what was sent.
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
        "Settings saved (%s): %s",
        path,
        ", ".join(sorted(current)) or "none",
    )
    return current


def clear() -> None:
    """Remove all overrides - the environment variables apply again."""
    path = settings_path()
    if path.exists():
        path.unlink()
        logger.info("Settings reset: %s", path)


def _restrict_permissions(path: Path) -> None:
    """Tighten the file to `0600`. Best effort - no reason to crash."""
    try:
        path.chmod(0o600)
    except OSError as exc:  # pragma: no cover - depends on the file system
        logger.debug("Could not set file permissions (%s): %s", path, exc)
