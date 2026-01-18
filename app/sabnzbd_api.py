"""
DDLnzb Bridge - SABnzbd-compatible API

Implements SABnzbd API endpoints for compatibility with *arr apps.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import config

router = APIRouter()

# In-memory queue and history
download_queue: Dict[str, "QueueItem"] = {}
download_history: Dict[str, "HistoryItem"] = {}


@dataclass
class QueueItem:
    """Item in the download queue."""
    nzo_id: str
    filename: str
    category: str
    status: str = "Downloading"
    percentage: int = 0
    size: str = "0 MB"
    sizeleft: str = "0 MB"
    eta: str = "unknown"
    timeleft: str = "0:00:00"
    added_at: datetime = field(default_factory=datetime.utcnow)
    
    # DDLnzb specific
    release_id: Optional[str] = None
    links: List[str] = field(default_factory=list)


@dataclass
class HistoryItem:
    """Item in download history."""
    nzo_id: str
    name: str
    category: str
    status: str = "Completed"
    size: str = "0 MB"
    completed_at: datetime = field(default_factory=datetime.utcnow)
    storage: str = ""
    
    # DDLnzb specific
    release_id: Optional[str] = None


@router.get("/api")
@router.post("/api")
async def sabnzbd_api(
    request: Request,
    mode: str = Query(None),
    output: str = Query("json"),
    apikey: str = Query(None),
    name: str = Query(None),
    nzbname: str = Query(None),
    cat: str = Query(None),
    nzo_id: str = Query(None),
):
    """
    SABnzbd-compatible API endpoint.
    
    Supported modes:
    - auth: Check API key
    - version: Get version
    - get_cats: Get categories
    - queue: Get queue status
    - history: Get history
    - addurl: Add download by URL
    - delete: Delete from queue
    - retry: Retry failed download
    """
    
    # Auth check (simplified - in production verify against stored key)
    # if apikey != config.DDLNZB_API_KEY:
    #     return _error_response("Invalid API key", output)
    
    if mode == "auth":
        return _json_or_text({"auth": "ok"}, output)
    
    elif mode == "version":
        return _json_or_text({"version": "4.0.0-ddlnzb"}, output)
    
    elif mode == "get_config":
        return _json_or_text({
            "config": {
                "misc": {
                    "complete_dir": config.DOWNLOAD_DIR,
                }
            }
        }, output)
    
    elif mode == "get_cats":
        categories = list(config.CATEGORY_MAPPING.keys())
        return _json_or_text({"categories": ["*"] + categories}, output)
    
    elif mode == "queue":
        return _get_queue(output)
    
    elif mode == "history":
        return _get_history(output)
    
    elif mode == "addurl":
        return await _add_url(name, nzbname, cat, output)
    
    elif mode == "delete":
        return _delete_item(nzo_id, output)
    
    elif mode == "retry":
        return _retry_item(nzo_id, output)
    
    elif mode == "fullstatus":
        return _get_fullstatus(output)
    
    else:
        return _error_response(f"Unknown mode: {mode}", output)


def _json_or_text(data: dict, output: str):
    """Return JSON or plain text response."""
    if output == "json":
        return JSONResponse(data)
    return PlainTextResponse(json.dumps(data))


def _error_response(message: str, output: str):
    """Return error response."""
    return _json_or_text({"status": False, "error": message}, output)


def _get_queue(output: str):
    """Get current download queue."""
    slots = []
    for item in download_queue.values():
        slots.append({
            "nzo_id": item.nzo_id,
            "filename": item.filename,
            "cat": item.category,
            "status": item.status,
            "percentage": str(item.percentage),
            "size": item.size,
            "sizeleft": item.sizeleft,
            "eta": item.eta,
            "timeleft": item.timeleft,
        })
    
    return _json_or_text({
        "queue": {
            "status": "Downloading" if slots else "Idle",
            "speedlimit": "0",
            "speed": "0 KB/s",
            "sizeleft": "0 MB",
            "timeleft": "0:00:00",
            "noofslots": len(slots),
            "slots": slots,
        }
    }, output)


def _get_history(output: str):
    """Get download history."""
    slots = []
    for item in list(download_history.values())[-50:]:  # Last 50 items
        slots.append({
            "nzo_id": item.nzo_id,
            "name": item.name,
            "category": item.category,
            "status": item.status,
            "size": item.size,
            "completed": int(item.completed_at.timestamp()),
            "storage": item.storage,
        })
    
    return _json_or_text({
        "history": {
            "noofslots": len(slots),
            "slots": slots,
        }
    }, output)


async def _add_url(url: str, nzbname: str, category: str, output: str):
    """
    Add a download by URL.
    
    The URL is expected to be a DDLnzb "fake NZB" URL containing
    the release ID in the payload.
    """
    if not url:
        return _error_response("No URL provided", output)
    
    # Generate unique ID
    nzo_id = f"SABnzbd_nzo_{uuid.uuid4().hex[:12]}"
    
    # Parse the DDLnzb URL to extract release ID
    release_id = None
    if "ddlnzb" in url.lower() or "payload=" in url:
        # Extract payload from URL
        try:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if "payload" in params:
                import base64
                payload = base64.urlsafe_b64decode(params["payload"][0]).decode()
                # Payload format: title|source|mirror|size|password|imdb_id
                parts = payload.split("|")
                if len(parts) >= 1:
                    nzbname = nzbname or parts[0]
        except Exception:
            pass
    
    # Create queue item
    item = QueueItem(
        nzo_id=nzo_id,
        filename=nzbname or url.split("/")[-1],
        category=category or "default",
        release_id=release_id,
    )
    download_queue[nzo_id] = item
    
    # TODO: Trigger actual download via JDownloader
    # This will be implemented in the JDownloader integration
    
    return _json_or_text({
        "status": True,
        "nzo_ids": [nzo_id],
    }, output)


def _delete_item(nzo_id: str, output: str):
    """Delete item from queue."""
    if nzo_id in download_queue:
        del download_queue[nzo_id]
        return _json_or_text({"status": True}, output)
    return _error_response(f"Item not found: {nzo_id}", output)


def _retry_item(nzo_id: str, output: str):
    """Retry a failed download."""
    if nzo_id in download_history:
        item = download_history[nzo_id]
        # Move back to queue
        queue_item = QueueItem(
            nzo_id=item.nzo_id,
            filename=item.name,
            category=item.category,
            release_id=item.release_id,
        )
        download_queue[nzo_id] = queue_item
        del download_history[nzo_id]
        return _json_or_text({"status": True}, output)
    return _error_response(f"Item not found: {nzo_id}", output)


def _get_fullstatus(output: str):
    """Get full status including queue and history."""
    queue_data = _get_queue("json").body
    history_data = _get_history("json").body
    
    return _json_or_text({
        "status": {
            "state": "DOWNLOADING" if download_queue else "IDLE",
            "speed": "0",
            "queue": json.loads(queue_data)["queue"],
            "history": json.loads(history_data)["history"],
        }
    }, output)


# Helper functions for queue management
def complete_download(nzo_id: str, storage_path: str):
    """Mark a download as complete and move to history."""
    if nzo_id in download_queue:
        item = download_queue[nzo_id]
        history_item = HistoryItem(
            nzo_id=item.nzo_id,
            name=item.filename,
            category=item.category,
            status="Completed",
            size=item.size,
            storage=storage_path,
            release_id=item.release_id,
        )
        download_history[nzo_id] = history_item
        del download_queue[nzo_id]


def fail_download(nzo_id: str, error: str):
    """Mark a download as failed."""
    if nzo_id in download_queue:
        item = download_queue[nzo_id]
        history_item = HistoryItem(
            nzo_id=item.nzo_id,
            name=item.filename,
            category=item.category,
            status=f"Failed: {error}",
            size=item.size,
            release_id=item.release_id,
        )
        download_history[nzo_id] = history_item
        del download_queue[nzo_id]


def update_progress(nzo_id: str, percentage: int, status: str = None):
    """Update download progress."""
    if nzo_id in download_queue:
        download_queue[nzo_id].percentage = percentage
        if status:
            download_queue[nzo_id].status = status
