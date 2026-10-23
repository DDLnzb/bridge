"""
DDLnzb Bridge - Main Application

Local bridge that acts as SABnzbd for *arr apps and sends downloads to JDownloader.
"""

import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import webui
from app.config import config
from app.download_handler import download_handler
from app.heartbeat import jd_heartbeat
from app.jdownloader import jdownloader
from app.metrics import (
    bridge_http_request_duration_seconds,
    bridge_http_requests_in_flight,
    bridge_http_requests_total,
    bridge_queue_depth,
    metrics_payload,
)
from app.persistence import queue_persistence
from app.sabnzbd_api import QueueItem, download_queue
from app.sabnzbd_api import router as sabnzbd_router
from app.webui import router as webui_router

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
            webui.record_test("api", True, f"HTTP {response.status_code}")
        else:
            logger.error(
                "DDLnzb API unreachable (status %s): %s",
                response.status_code,
                config.DDLNZB_API_URL,
            )
            webui.record_test("api", False, f"HTTP {response.status_code}")
    except Exception as exc:
        logger.error("DDLnzb API connection failed: %s (%s)", config.DDLNZB_API_URL, exc)
        webui.record_test("api", False, str(exc))

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

    # Ohne API-Key ist der Bridge funktional unbrauchbar - dann braucht es den
    # Setup-Token fuer die Ersteinrichtung ueber /ui. Das Token steht im Log.
    if not config.DDLNZB_API_KEY:
        webui.setup_token()

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


def _route_template(request: Request) -> str:
    """Route-Template statt des rohen Pfads als Label-Wert.

    Der rohe Pfad ist unbeschaenkt in der Kardi­nalitaet: jeder Link-Titel
    und jeder Scanner-Treffer erzeugt eine eigene Serie. Das Template
    (`/api`) haelt das Label bounded.

    Aufgeloest wird das Template erst **nach** `call_next`, weil Starlette die
    Route erst im Routing-Schritt setzt. Requests ohne Treffer landen in
    `unmatched`.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template or "unmatched"


@app.middleware("http")
async def prometheus_metrics(request: Request, call_next):
    service = "bridge"
    method = request.method
    start_time = time.perf_counter()

    bridge_http_requests_in_flight.labels(service=service).inc()
    try:
        response = await call_next(request)
        endpoint = _route_template(request)
        status_code = str(response.status_code)
        duration = time.perf_counter() - start_time
        bridge_http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            service=service,
        ).inc()
        bridge_http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
            service=service,
        ).observe(duration)
        return response
    except Exception:
        duration = time.perf_counter() - start_time
        endpoint = _route_template(request)
        bridge_http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code="500",
            service=service,
        ).inc()
        bridge_http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
            service=service,
        ).observe(duration)
        raise
    finally:
        bridge_http_requests_in_flight.labels(service=service).dec()


# Include SABnzbd-compatible API
app.include_router(sabnzbd_router)

# Web-Oberflaeche fuer die Einrichtung (siehe app/webui.py)
app.include_router(webui_router)

# Statische Dateien der Oberflaeche (CSS/JS). Der Mount muss unter /ui liegen,
# weil die Routen dort mit Prefix /ui registriert sind.
app.mount(
    "/ui/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="ui-static",
)


async def require_bridge_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    apikey: str | None = Query(None),
    authorization: str | None = Header(None),
) -> None:
    """Bridge-Key-Prüfung für alle Routen außer `/health`.

    Deckt sich mit der Prüfung in `sabnzbd_api.py` und schließt bei fehlender
    Konfiguration: ist `DDLNZB_API_KEY` leer, ist kein Aufruf gültig. Sonst
    ließe sich ein Bridge ohne Key durch die Hintertür abschalten.

    Drei Wege werden akzeptiert:
    - `X-API-Key: <key>` (Header) — der Weg der *arr-Apps und des Backends
    - `?apikey=<key>` (Query) — SABnzbd-Kompatibilität
    - `Authorization: Bearer <key>` — Prometheus kann nur einen Bearer-Token
      mitschicken (`bearer_token_file`), keine freien Header. Ohne diesen Weg
      wäre ein geschütztes `/metrics` nicht abfragbar.
    """
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    presented = x_api_key or apikey or bearer
    if not config.DDLNZB_API_KEY or not secrets.compare_digest(
        presented or "", config.DDLNZB_API_KEY
    ):
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


@app.get("/metrics", dependencies=[Depends(require_bridge_key)])
async def metrics() -> Response:
    """Prometheus metrics endpoint.

    Geschuetzt wie `/status`: der Bridge-Port 6789 ist im Prod-Compose
    veroeffentlicht, und die Metriken verraten Queue-Tiefe und
    Request-Muster. Prometheus liefert den Key als Bearer-Token
    (`bearer_token_file` in `monitoring/prometheus/prometheus.yml`).
    """
    queued = sum(1 for item in download_queue.values() if getattr(item, "status", "") == "Queued")
    downloading = sum(
        1 for item in download_queue.values() if getattr(item, "status", "") == "Downloading"
    )
    bridge_queue_depth.labels(state="total").set(len(download_queue))
    bridge_queue_depth.labels(state="queued").set(queued)
    bridge_queue_depth.labels(state="downloading").set(downloading)

    payload, content_type = metrics_payload()
    return Response(content=payload, media_type=content_type)


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
