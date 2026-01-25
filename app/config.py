"""
DDLnzb Bridge - Configuration
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

# Load .env automatically for local runs; do nothing if file is missing
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


@dataclass
class BridgeConfig:
    """Bridge configuration."""
    
    # DDLnzb Cloud API
    DDLNZB_API_URL: str = os.getenv("DDLNZB_API_URL", "http://localhost:8000")
    DDLNZB_API_KEY: str = os.getenv("DDLNZB_API_KEY", "")
    
    # Bridge Server
    BRIDGE_HOST: str = os.getenv("BRIDGE_HOST", "127.0.0.1")
    BRIDGE_PORT: int = int(os.getenv("BRIDGE_PORT", "8080"))
    LOG_LEVEL: str = os.getenv("BRIDGE_LOG_LEVEL", "INFO")
    
    # JDownloader
    MYJD_EMAIL: str = os.getenv("MYJD_EMAIL", "")
    MYJD_PASSWORD: str = os.getenv("MYJD_PASSWORD", "")
    MYJD_DEVICE_NAME: str = os.getenv("MYJD_DEVICE_NAME", "")
    MYJD_APP_KEY: str = os.getenv("MYJD_APP_KEY", "DDLnzb")
    
    # Download Settings
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "./downloads")
    EXTRACT_ARCHIVES: bool = os.getenv("EXTRACT_ARCHIVES", "true").lower() == "true"
    DELETE_ARCHIVES: bool = os.getenv("DELETE_ARCHIVES", "true").lower() == "true"
    
    # Category Mapping (SABnzbd category -> JDownloader folder)
    CATEGORY_MAPPING: dict = None
    
    # Persistence
    STATE_FILE: str = os.getenv("DDLNZB_STATE_FILE", "./ddlnzb_bridge_state.json")
    
    def __post_init__(self):
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


config = BridgeConfig()
