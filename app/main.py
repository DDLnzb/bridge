"""
DDLnzb Bridge - Main Application

Local bridge that acts as SABnzbd for *arr apps and sends downloads to JDownloader.
"""

import logging
import secrets
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.download_handler import download_handler
from app.heartbeat import jd_heartbeat
from app.jdownloader import jdownloader
from app.persistence import queue_persistence
from app.sabnzbd_api import QueueItem, download_queue
from app.sabnzbd_api import router as sabnzbd_router

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
if config.LOG_LEVEL.upper() == "DEBUG":
    logging.getLogger("uvicorn.access").setLevel(logging.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print(f"Starting DDLnzb Bridge on {config.BRIDGE_HOST}:{config.BRIDGE_PORT}")
    logger = logging.getLogger("ddlnzb.bridge")
    if not config.DDLNZB_API_KEY:
        logger.warning("DDLnzb API key not configured")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{config.DDLNZB_API_URL}/health")
        if response.status_code == 200:
            logger.info("DDLnzb API reachable: %s", config.DDLNZB_API_URL)
        else:
            logger.error(
                "DDLnzb API unreachable (status %s): %s",
                response.status_code,
                config.DDLNZB_API_URL,
            )
    except Exception as exc:
        logger.error("DDLnzb API connection failed: %s (%s)", config.DDLNZB_API_URL, exc)

    # Connect to JDownloader if credentials are configured
    if config.MYJD_EMAIL and config.MYJD_PASSWORD:
        try:
            await jdownloader.connect()
            device = await jdownloader.get_device()
            if device:
                print(f"Connected to JDownloader device: {device.get('name')}")
            else:
                print(f"JDownloader device '{config.MYJD_DEVICE_NAME}' not found")
        except Exception as e:
            print(f"Failed to connect to JDownloader: {e}")
    else:
        print("JDownloader credentials not configured")

    # Load persisted queue
    try:
        saved_jobs = queue_persistence.load()
        if saved_jobs:
            for job in saved_jobs:
                if job.get("status") in ("Queued", "Downloading"):
                    nzo_id = job.get("id")
                    if nzo_id and nzo_id not in download_queue:
                        item = QueueItem(
                            nzo_id=nzo_id,
                            filename=job.get("filename", ""),
                            category=job.get("category", ""),
                            payload=job.get("payload", ""),
                        )
                        item.status = "Queued"  # Reset to queued for retry
                        item.links = job.get("links", [])
                        download_queue[nzo_id] = item
            logger.info("Restored %d pending jobs from persistence", len(download_queue))
    except Exception as e:
        logger.warning("Failed to load persisted queue: %s", e)

    # Start download handler loop
    try:
        await download_handler.start()
    except Exception as e:
        print(f"Failed to start download handler: {e}")

    # Start JDownloader heartbeat
    try:
        await jd_heartbeat.start()
    except Exception as e:
        logger.warning("Failed to start JD heartbeat: %s", e)

    yield

    # Cleanup
    await jd_heartbeat.stop()
    await download_handler.stop()

    # Save queue state before shutdown
    try:
        queue_persistence.save(download_queue)
    except Exception as e:
        logger.warning("Failed to save queue on shutdown: %s", e)

    await jdownloader.disconnect()
    print("DDLnzb Bridge stopped")


app = FastAPI(
    title="DDLnzb Bridge",
    description="Local bridge for DDLnzb - SABnzbd-compatible API for *arr apps",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include SABnzbd-compatible API
app.include_router(sabnzbd_router)


async def require_bridge_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    apikey: str | None = Query(None),
) -> None:
    """Bridge-Key-Prüfung für alle Routen außer `/health`.

    Deckt sich mit der Prüfung in `sabnzbd_api.py` und schließt bei fehlender
    Konfiguration: ist `DDLNZB_API_KEY` leer, ist kein Aufruf gültig. Sonst
    ließe sich ein Bridge ohne Key durch die Hintertür abschalten.
    """
    presented = x_api_key or apikey or ""
    if not config.DDLNZB_API_KEY or not secrets.compare_digest(presented, config.DDLNZB_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/", dependencies=[Depends(require_bridge_key)])
async def root():
    """Root endpoint."""
    return {
        "name": "DDLnzb Bridge",
        "version": "1.0.0",
        "status": "running",
        "jdownloader_connected": jdownloader.device_id is not None,
    }


@app.get("/health")
async def health():
    """Health check endpoint.

    Bewusst ohne Auth: Load-Balancer und Docker-HEALTHCHECK müssen den Endpunkt
    ohne Zugangsdaten erreichen können. Hier steht auch nichts außer `status`.
    """
    return {"status": "healthy"}


@app.get("/status", dependencies=[Depends(require_bridge_key)])
async def status():
    """Detailed status endpoint."""
    return {
        "name": "DDLnzb Bridge",
        "version": "1.0.0",
        "jdownloader": {
            "connected": jdownloader.device_id is not None,
            "device_id": jdownloader.device_id,
            "device_name": config.MYJD_DEVICE_NAME,
        },
        "heartbeat": jd_heartbeat.status,
        "queue": {
            "total": len(download_queue),
            "active": sum(
                1
                for item in download_queue.values()
                if getattr(item, "status", "") in ("Queued", "Downloading")
            ),
        },
    }


@app.post("/heartbeat/reconnect", dependencies=[Depends(require_bridge_key)])
async def force_reconnect():
    """Force JDownloader reconnect."""
    success = await jd_heartbeat.force_reconnect()
    return {"success": success, "status": jd_heartbeat.status}


def main():
    """Run the bridge server."""
    uvicorn.run(
        "app.main:app",
        host=config.BRIDGE_HOST,
        port=config.BRIDGE_PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
