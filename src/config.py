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
    DDLNZB_API_URL: str = "https://api.ddlnzb.com"
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
