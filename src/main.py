"""
DDLnzb Bridge - SABnzbd-compatible API for JDownloader Integration
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    print("Starting DDLnzb Bridge")
    print(f"API URL: {settings.DDLNZB_API_URL}")
    yield
    print("Shutting down DDLnzb Bridge")


app = FastAPI(
    title="DDLnzb Bridge",
    description="SABnzbd-compatible API for JDownloader Integration",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "name": "DDLnzb Bridge",
        "version": "0.1.0",
        "sabnzbd_api": "/api",
    }


# SABnzbd-compatible API endpoints
@app.get("/api")
@app.post("/api")
async def sabnzbd_api(
    mode: str = "version",
    apikey: str = "",
) -> dict:
    """
    SABnzbd-compatible API endpoint.

    Supported modes:
    - auth: Authentication check
    - version: Get version
    - get_cats: Get categories
    - get_config: Get configuration
    - queue: Get download queue
    - history: Get download history
    - fullstatus: Get full status
    """
    if mode == "auth":
        return {"auth": "apikey"}

    if mode == "version":
        return {"version": "3.7.1"}

    if mode == "get_cats":
        return {"categories": ["*", "movies", "tv", "docs"]}

    if mode == "get_config":
        return {
            "config": {
                "misc": {
                    "complete_dir": settings.DOWNLOAD_PATH_MOVIES,
                },
                "categories": [
                    {"name": "movies", "dir": settings.DOWNLOAD_PATH_MOVIES},
                    {"name": "tv", "dir": settings.DOWNLOAD_PATH_TV},
                    {"name": "docs", "dir": settings.DOWNLOAD_PATH_DOCS},
                ],
            }
        }

    if mode == "queue":
        # TODO: Get actual queue from JDownloader
        return {"queue": {"slots": []}}

    if mode == "history":
        # TODO: Get actual history from JDownloader
        return {"history": {"slots": []}}

    if mode == "fullstatus":
        return {"status": {"state": "idle"}}

    return {"error": f"Unknown mode: {mode}"}
