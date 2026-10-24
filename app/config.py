"""
DDLnzb Bridge - Configuration
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app import settings as settings_store

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

# Load .env automatically for local runs; do nothing if file is missing
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def _as_bool(raw: str) -> bool:
    """Boolean environment variable. Only "true" (case-insensitive) is truthy.

    Deliberately the same semantics as before - switching to "1"/"yes" would be
    a silent behavior change for deployments that set "true".
    """
    return raw.lower() == "true"


def _as_list(raw: str) -> tuple[str, ...]:
    """Comma separated list of values.

    Used for the CORS allowlist: `BRIDGE_CORS_ORIGINS="https://a, https://b"`.
    A tuple instead of a list, so that the default cannot be mutated through
    the config object.
    """
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# Single source of truth for field <-> environment variable <-> default.
# `LOG_LEVEL` is `BRIDGE_LOG_LEVEL` in the environment - the names differ
# historically and must not be unified without touching every deployment.
ENV_SOURCES: dict[str, tuple[str, Callable[[str], Any], Any]] = {
    # The bridge ships the API URL itself - it is not an input field of the
    # web UI. Overridable via `DDLNZB_API_URL`, e.g. with `http://api:8000`
    # in the Docker network or `http://localhost:8000` locally.
    "DDLNZB_API_URL": ("DDLNZB_API_URL", str, "https://ddlnzb.pages.dev/api/app"),
    "DDLNZB_API_KEY": ("DDLNZB_API_KEY", str, ""),
    "BRIDGE_HOST": ("BRIDGE_HOST", str, "127.0.0.1"),
    "BRIDGE_PORT": ("BRIDGE_PORT", int, 8080),
    "LOG_LEVEL": ("BRIDGE_LOG_LEVEL", str, "INFO"),
    "MYJD_EMAIL": ("MYJD_EMAIL", str, ""),
    "MYJD_PASSWORD": ("MYJD_PASSWORD", str, ""),
    "MYJD_DEVICE_NAME": ("MYJD_DEVICE_NAME", str, ""),
    "MYJD_APP_KEY": ("MYJD_APP_KEY", str, "DDLnzb"),
    "DOWNLOAD_DIR": ("DOWNLOAD_DIR", str, "./downloads"),
    "EXTRACT_ARCHIVES": ("EXTRACT_ARCHIVES", _as_bool, True),
    "DELETE_ARCHIVES": ("DELETE_ARCHIVES", _as_bool, True),
    "STATE_FILE": ("DDLNZB_STATE_FILE", str, "./ddlnzb_bridge_state.json"),
    "DATA_DIR": ("DDLNZB_DATA_DIR", str, str(BASE_DIR / "data")),
    # Explicit CORS allowlist (comma separated). Empty by default: the bridge
    # needs no CORS at all - the *arr apps are not browsers and the web UI is
    # served from the same origin. See `app/main.py`.
    "CORS_ORIGINS": ("BRIDGE_CORS_ORIGINS", _as_list, ()),
}


@dataclass
class BridgeConfig:
    """Bridge configuration."""

    # DDLnzb Cloud API
    DDLNZB_API_URL: str = ""
    DDLNZB_API_KEY: str = ""

    # Bridge Server
    BRIDGE_HOST: str = "127.0.0.1"
    BRIDGE_PORT: int = 8080
    LOG_LEVEL: str = "INFO"

    # JDownloader
    MYJD_EMAIL: str = ""
    MYJD_PASSWORD: str = ""
    MYJD_DEVICE_NAME: str = ""
    MYJD_APP_KEY: str = "DDLnzb"

    # Download Settings
    DOWNLOAD_DIR: str = "./downloads"
    EXTRACT_ARCHIVES: bool = True
    DELETE_ARCHIVES: bool = True

    # Category Mapping (SABnzbd category -> JDownloader folder)
    CATEGORY_MAPPING: dict = None

    # Persistence
    STATE_FILE: str = "./ddlnzb_bridge_state.json"
    DATA_DIR: str = ""

    # CORS allowlist - empty by default, see `ENV_SOURCES`.
    CORS_ORIGINS: tuple[str, ...] = ()

    def __post_init__(self):
        self._apply_env()
        # The settings file is applied **here**, at import time - not only when
        # the web UI writes it. Without this the configuration entered via the
        # UI was lost with every restart of the process (#715): the file was
        # only read by `reload()`, which the UI routes call.
        self.apply_overrides(settings_store.load())
        if self.CATEGORY_MAPPING is None:
            self.CATEGORY_MAPPING = {
                "movies": "Movies",
                "tv": "TV",
                "music": "Music",
                "books": "Books",
                "software": "Software",
                "radarr": "Movies",
                "sonarr": "TV",
                "lidarr": "Music",
                "lazy_librarian": "Books",
                "whisparr": "Adult",
            }

    def _apply_env(self) -> None:
        """Re-resolve all fields from the environment.

        An empty environment variable counts as "not set" - otherwise
        `DDLNZB_API_URL=""` would run the bridge against an empty URL.
        """
        for field_name, (env_name, cast, default) in ENV_SOURCES.items():
            raw = os.getenv(env_name)
            value = default if raw is None or raw == "" else cast(raw)
            setattr(self, field_name, value)

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Apply the stored overrides onto the object.

        Unknown keys are ignored, empty values as well - an empty value means
        "fall back to the environment variable".
        """
        for key, value in overrides.items():
            if key not in ENV_SOURCES:
                continue
            if value is None or value == "":
                continue
            if key == "EXTRACT_ARCHIVES" or key == "DELETE_ARCHIVES":
                value = bool(value)
            setattr(self, key, value)

    def reload(self) -> None:
        """Re-read the environment and layer the stored overrides on top.

        This is the common entry point for the **startup** and for the web UI:
        after saving, the new configuration takes effect immediately, without a
        restart - and at startup the same precedence applies
        (`settings file > environment > default`).
        """
        self._apply_env()
        self.apply_overrides(settings_store.load())


config = BridgeConfig()
