"""
DDLnzb Bridge - SABnzbd-compatible API

Implements SABnzbd API endpoints for compatibility mit *arr apps.
"""

import base64
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import config

logger = logging.getLogger("ddlnzb.bridge")
STATE_PATH = Path(config.STATE_FILE).expanduser()

router = APIRouter()

# In-memory queue and history
download_queue: dict[str, "QueueItem"] = {}
download_history: dict[str, "HistoryItem"] = {}


def _serialize_queue_item(item: "QueueItem") -> dict:
    data = asdict(item)
    data["added_at"] = item.added_at.isoformat()
    return data


def _serialize_history_item(item: "HistoryItem") -> dict:
    data = asdict(item)
    data["completed_at"] = item.completed_at.isoformat()
    return data


def _deserialize_queue_item(data: dict) -> "QueueItem":
    data = data.copy()
    if data.get("added_at"):
        data["added_at"] = datetime.fromisoformat(data["added_at"])
    return QueueItem(**data)


def _deserialize_history_item(data: dict) -> "HistoryItem":
    data = data.copy()
    if data.get("completed_at"):
        data["completed_at"] = datetime.fromisoformat(data["completed_at"])
    return HistoryItem(**data)


def _persist_state() -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "queue": [_serialize_queue_item(item) for item in download_queue.values()],
            "history": [_serialize_history_item(item) for item in download_history.values()],
        }
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist bridge state: %s", exc)


def _load_state() -> None:
    if not STATE_PATH.exists():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        download_queue.clear()
        for item in data.get("queue", []):
            qi = _deserialize_queue_item(item)
            download_queue[qi.nzo_id] = qi
        download_history.clear()
        for item in data.get("history", []):
            hi = _deserialize_history_item(item)
            download_history[hi.nzo_id] = hi
        logger.info(
            "Restored bridge state: %s queue items, %s history entries",
            len(download_queue),
            len(download_history),
        )
    except Exception as exc:
        logger.warning("Failed to load bridge state: %s", exc)


@dataclass
class QueueItem:
    """Item in der Download-Queue."""

    nzo_id: str
    filename: str
    category: str
    status: str = "Queued"
    percentage: int = 0
    size: str = "0 MB"
    sizeleft: str = "0 MB"
    eta: str = "unknown"
    timeleft: str = "0:00:00"
    added_at: datetime = field(default_factory=datetime.utcnow)

    # DDLnzb specific
    release_id: str | None = None
    links: list[str] = field(default_factory=list)
    payload: str | None = None


@dataclass
class HistoryItem:
    """Item in der Download-Historie."""

    nzo_id: str
    name: str
    category: str
    status: str = "Completed"
    size: str = "0 MB"
    completed_at: datetime = field(default_factory=datetime.utcnow)
    storage: str = ""

    # DDLnzb spezifisch
    release_id: str | None = None
    payload: str | None = None
    links: list[str] = field(default_factory=list)


# Muss nach QueueItem und HistoryItem stehen: _deserialize_queue_item und
# _deserialize_history_item erzeugen Instanzen dieser beiden Klassen. Ein früherer
# Aufruf läuft in einen NameError, der vom except-Handler in _load_state()
# geschluckt wird - die Queue wäre dann stillschweigend leer.
_load_state()


@dataclass
class PayloadInfo:
    """Dekodierte Payload-Informationen."""

    title: str
    source_url: str
    mirror: str | None
    size_bytes: int
    password: str | None
    imdb_id: str | None


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

    # Auth check using configured bridge key
    if not config.DDLNZB_API_KEY:
        return _error_response("Bridge API key not configured", output)

    if apikey != config.DDLNZB_API_KEY:
        return _error_response("Invalid API key", output)

    if mode == "auth":
        return _json_or_text({"auth": "ok"}, output)

    elif mode == "version":
        return _json_or_text({"version": "4.0.0-ddlnzb"}, output)

    elif mode == "get_config":
        categories = [
            {"name": name, "dir": target}
            for name, target in (config.CATEGORY_MAPPING or {}).items()
        ]
        return _json_or_text(
            {
                "config": {
                    "misc": {
                        "complete_dir": config.DOWNLOAD_DIR,
                    },
                    "categories": categories,
                }
            },
            output,
        )

    elif mode == "get_cats":
        categories = list(config.CATEGORY_MAPPING.keys())
        return _json_or_text({"categories": ["*"] + categories}, output)

    elif mode == "queue":
        return _get_queue(output)

    elif mode == "history":
        return _get_history(output)

    elif mode == "addurl":
        return await _add_url(name, nzbname, cat, output)

    elif mode == "addfile":
        return await _add_file(request, nzbname, cat, output)

    elif mode == "ddlnzb_links":
        return _get_links_response(nzo_id, output)

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
        slots.append(
            {
                "nzo_id": item.nzo_id,
                "filename": item.filename,
                "cat": item.category,
                "status": item.status,
                "percentage": str(item.percentage),
                "size": item.size,
                "sizeleft": item.sizeleft,
                "eta": item.eta,
                "timeleft": item.timeleft,
                "links": item.links,
            }
        )

    return _json_or_text(
        {
            "queue": {
                "status": "Downloading" if slots else "Idle",
                "speedlimit": "0",
                "speed": "0 KB/s",
                "sizeleft": "0 MB",
                "timeleft": "0:00:00",
                "noofslots": len(slots),
                "slots": slots,
            }
        },
        output,
    )


def _get_history(output: str):
    """Get download history."""
    slots = []
    for item in list(download_history.values())[-50:]:  # Last 50 items
        slots.append(
            {
                "nzo_id": item.nzo_id,
                "name": item.name,
                "category": item.category,
                "status": item.status,
                "size": item.size,
                "completed": int(item.completed_at.timestamp()),
                "storage": item.storage,
                "links": item.links,
            }
        )

    return _json_or_text(
        {
            "history": {
                "noofslots": len(slots),
                "slots": slots,
            }
        },
        output,
    )


def _get_fullstatus(output: str):
    """Return combined status for SABnzbd clients."""
    queue = _get_queue("json").body
    history = _get_history("json").body
    try:
        queue_data = json.loads(queue)
        history_data = json.loads(history)
    except Exception:
        queue_data = {"queue": {"noofslots": 0, "slots": []}}
        history_data = {"history": {"noofslots": 0, "slots": []}}

    payload = {
        "status": {"state": "Downloading" if queue_data.get("queue", {}).get("slots") else "Idle"},
        "queue": queue_data.get("queue", {}),
        "history": history_data.get("history", {}),
    }
    return _json_or_text(payload, output)


async def _add_url(url: str, nzbname: str, category: str, output: str):
    """
    Add a download by URL.

    The URL is expected to be a DDLnzb "fake NZB" URL containing
    the release ID in the payload.
    """
    if not url:
        return _error_response("No URL provided", output)

    payload = _extract_payload_from_url(url)
    if not payload:
        return _error_response("Invalid DDLnzb URL payload", output)

    item = _enqueue_payload(payload, nzbname, category)

    return _json_or_text(
        {
            "status": True,
            "nzo_ids": [item.nzo_id],
        },
        output,
    )


async def _add_file(request: Request, nzbname: str, category: str, output: str):
    """Add NZB file upload (mode=addfile)."""
    body = await request.body()
    if not body:
        return _error_response("NZB file missing", output)

    payload = _extract_payload_from_nzb(body)
    if not payload:
        return _error_response("NZB payload missing", output)

    item = _enqueue_payload(payload, nzbname, category)

    return _json_or_text(
        {
            "status": True,
            "nzo_ids": [item.nzo_id],
        },
        output,
    )


def _delete_item(nzo_id: str, output: str):
    """Delete item from queue."""
    if nzo_id in download_queue:
        del download_queue[nzo_id]
        _persist_state()
        return _json_or_text({"status": True}, output)
    return _error_response(f"Item not found: {nzo_id}", output)


def _retry_item(nzo_id: str, output: str):
    """Download erneut versuchen."""
    if nzo_id in download_history:
        item = download_history[nzo_id]
        # Zurück in die Queue schieben
        queue_item = QueueItem(
            nzo_id=item.nzo_id,
            filename=item.name,
            category=item.category,
            release_id=item.release_id,
            payload=item.payload,
        )
        download_queue[nzo_id] = queue_item
        del download_history[nzo_id]
        _persist_state()
        return _json_or_text({"status": True}, output)
    return _error_response(f"Item not found: {nzo_id}", output)


def _get_links_response(nzo_id: str | None, output: str):
    """Return stored links for an NZO entry (fallback for manual downloads)."""
    if not nzo_id:
        return _error_response("nzo_id missing", output)
    if nzo_id in download_queue:
        item = download_queue[nzo_id]
        return _json_or_text(
            {
                "status": "queue",
                "nzo_id": nzo_id,
                "links": item.links,
            },
            output,
        )
    if nzo_id in download_history:
        item = download_history[nzo_id]
        return _json_or_text(
            {
                "status": "history",
                "nzo_id": nzo_id,
                "links": item.links,
            },
            output,
        )
    return _error_response(f"nzo_id not found: {nzo_id}", output)


# Helper functions for queue management
def complete_download(nzo_id: str, storage_path: str):
    """Download abschließen und in History verschieben."""
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
            payload=item.payload,
        )
        download_history[nzo_id] = history_item
        del download_queue[nzo_id]
        _persist_state()


def fail_download(nzo_id: str, error: str):
    """Download als fehlgeschlagen markieren."""
    if nzo_id in download_queue:
        item = download_queue[nzo_id]
        history_item = HistoryItem(
            nzo_id=item.nzo_id,
            name=item.filename,
            category=item.category,
            status=f"Failed: {error}",
            size=item.size,
            release_id=item.release_id,
            payload=item.payload,
            links=item.links,
        )
        download_history[nzo_id] = history_item
        del download_queue[nzo_id]


def update_progress(nzo_id: str, percentage: int, status: str = None):
    """Download-Fortschritt aktualisieren."""
    if nzo_id in download_queue:
        download_queue[nzo_id].percentage = percentage
        if status:
            download_queue[nzo_id].status = status


def mark_manual_download(nzo_id: str, message: str):
    """Move item to history with manual-download status."""
    if nzo_id in download_queue:
        item = download_queue[nzo_id]
        history_item = HistoryItem(
            nzo_id=item.nzo_id,
            name=item.filename,
            category=item.category,
            status=message,
            size=item.size,
            storage="",
            release_id=item.release_id,
            payload=item.payload,
            links=item.links,
        )
        download_history[nzo_id] = history_item
        del download_queue[nzo_id]


def _enqueue_payload(payload: str, nzbname: str | None, category: str | None) -> QueueItem:
    """Create queue item inkl. Payload und Metadaten."""
    info = decode_payload(payload)
    nzo_id = f"SABnzbd_nzo_{uuid.uuid4().hex[:12]}"
    filename = nzbname or info.title if info else (nzbname or nzo_id)
    size_str = "0 MB"
    if info and info.size_bytes:
        size_str = f"{info.size_bytes / (1024 * 1024):.2f} MB"
    item = QueueItem(
        nzo_id=nzo_id,
        filename=filename,
        category=category or "default",
        status="Queued",
        size=size_str,
        sizeleft=size_str,
        release_id=info.source_url if info else None,
        payload=payload,
    )
    download_queue[nzo_id] = item
    _persist_state()
    return item


def _extract_payload_from_url(url: str) -> str | None:
    """Read payload query parameter from Fake-NZB URL."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        payloads = params.get("payload") or params.get("id")
        if payloads:
            return payloads[0]
    except Exception:
        return None
    return None


def _extract_payload_from_nzb(data: bytes) -> str | None:
    """Extract payload marker aus Fake-NZB Datei."""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    match = re.search(r'payload="([A-Za-z0-9_\-=]+)"', text)
    if match:
        return match.group(1)
    match = re.search(r"<payload>(.*?)</payload>", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def decode_payload(payload_str: str) -> PayloadInfo | None:
    """Decode Base64 Payload -> PayloadInfo."""
    if not payload_str:
        return None
    try:
        decoded = base64.urlsafe_b64decode(payload_str).decode("utf-8")
        parts = decoded.split("|")
        if len(parts) >= 6:
            size_raw = parts[3]
            if size_raw:
                if "." in size_raw:
                    size = int(float(size_raw) * 1024 * 1024)
                else:
                    size = int(size_raw)
            else:
                size = 0
            return PayloadInfo(
                title=parts[0],
                source_url=parts[1],
                mirror=parts[2] or None,
                size_bytes=size,
                password=parts[4] or None,
                imdb_id=parts[5] or None,
            )
    except Exception:
        return None
    return None
