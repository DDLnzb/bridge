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
    """Boolsche Umgebungsvariable. Nur "true" (case-insensitiv) ist wahr.

    Bewusst dieselbe Semantik wie vorher - ein Umstellen auf "1"/"yes" waere
    eine stille Verhaltensaenderung fuer Deployments, die auf "true" setzen.
    """
    return raw.lower() == "true"


# Eine Quelle der Wahrheit fuer Feld <-> Umgebungsvariable <-> Default.
# `LOG_LEVEL` heisst in der Umgebung `BRIDGE_LOG_LEVEL` - die Namen sind
# historisch verschieden und duerfen nicht vereinheitlicht werden, ohne alle
# Deployments anzufassen.
ENV_SOURCES: dict[str, tuple[str, Callable[[str], Any], Any]] = {
    # Der Bridge bringt die API-URL selbst mit - sie ist kein Eingabefeld der
    # Oberflaeche. Per `DDLNZB_API_URL` ueberschreibbar, z. B. mit
    # `http://api:8000` im Docker-Netzwerk oder `http://localhost:8000` lokal.
    "DDLNZB_API_URL": ("DDLNZB_API_URL", str, "https://api.petsground.de"),
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

    def __post_init__(self):
        self._apply_env()
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
        """Alle Felder aus der Umgebung neu aufloesen.

        Eine leere Umgebungsvariable gilt als "nicht gesetzt" - sonst wuerde
        etwa `DDLNZB_API_URL=""` die Bridge auf eine leere URL laufen lassen.
        """
        for field_name, (env_name, cast, default) in ENV_SOURCES.items():
            raw = os.getenv(env_name)
            value = default if raw is None or raw == "" else cast(raw)
            setattr(self, field_name, value)

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Gespeicherte Overrides auf das Objekt legen.

        Unbekannte Schluessel werden ignoriert, leere Werte ebenfalls - ein
        leerer Wert bedeutet "zurueck auf die Umgebungsvariable".
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
        """Umgebung neu lesen und die gespeicherten Overrides darueber legen.

        Das ist der Eintrittspunkt fuer die Web-Oberflaeche: nach dem Speichern
        gilt die neue Konfiguration sofort, ohne Neustart.
        """
        self._apply_env()
        self.apply_overrides(settings_store.load())


config = BridgeConfig()
