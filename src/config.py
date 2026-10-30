"""
DDLnzb Bridge - Configuration
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bridge settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # DDLnzb Cloud Connection
    # Der Default zeigt bewusst auf den **live** erreichbaren Host. Er stand
    # vorher auf `https://api.ddlnzb.com` - eine Domain ohne jeden DNS-Record,
    # die also nie aufloesen konnte (#879). Wer den Stub startet, sprach damit
    # ins Leere; der Fehler fiel nur als "keine Verbindung" auf, ohne die
    # Ursache zu nennen.
    #
    # `app/config.py` nutzt denselben Wert - beide Pfade muessen zusammenpassen.
    DDLNZB_API_URL: str = "https://ddlnzb.pages.dev/api/app"
    DDLNZB_BRIDGE_TOKEN: str = ""

    # JDownloader Credentials
    JD_EMAIL: str = ""
    JD_PASSWORD: str = ""
    JD_DEVICE: str = ""

    # Download Paths
    DOWNLOAD_PATH_MOVIES: str = "/downloads/ddlnzb/movies"
    DOWNLOAD_PATH_TV: str = "/downloads/ddlnzb/tv"
    DOWNLOAD_PATH_DOCS: str = "/downloads/ddlnzb/docs"


settings = Settings()
