"""
DDLnzb Bridge - SABnzbd-compatible API

Implements SABnzbd API endpoints for compatibility with the *arr apps.
"""

import base64
import json
import logging
import re
import secrets
import uuid
from dataclasses import asdict, dataclass, field, fields
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

# --- SABnzbd status vocabulary --------------------------------------------

#: The values from `SabnzbdDownloadStatus` (Sonarr/Radarr,
#: `src/NzbDrone.Core/Download/Clients/Sabnzbd/SabnzbdDownloadStatus.cs`).
#:
#: Newtonsoft aborts the deserialization of the **entire** queue or history
#: response as soon as a slot carries a value outside this vocabulary - not
#: just that one entry. The download client is then completely dead for the
#: *arr app (#659).
SAB_STATUS_QUEUED = "Queued"
SAB_STATUS_DOWNLOADING = "Downloading"
SAB_STATUS_COMPLETED = "Completed"
SAB_STATUS_FAILED = "Failed"

SAB_STATUSES = frozenset(
    {
        "Grabbing",
        "Queued",
        "Paused",
        "Checking",
        "Downloading",
        "QuickCheck",
        "Verifying",
        "Repairing",
        "Fetching",
        "Extracting",
        "Moving",
        "Running",
        "Completed",
        "Failed",
        "Deleted",
        "Propagating",
    }
)

#: The progress texts of the download handler -> SABnzbd status.
#:
#: `download_handler.py` writes free texts in several places
#: ("Fetching links...", "Downloading via JDownloader..."). The texts
#: themselves are preserved - in the `message` field of the slot. The `status`
#: now only carries a value from `SAB_STATUSES`.
#:
#: "Starting download..." is deliberately **not** mapped to "Queued": the
#: download handler only accepts entries with the status "Queued". A "Queued"
#: there would pick the entry up again immediately.
_STATUS_TEXT_MAP = {
    "Starting download...": SAB_STATUS_DOWNLOADING,
    "Fetching links...": SAB_STATUS_DOWNLOADING,
    "Getting download links...": SAB_STATUS_DOWNLOADING,
    "Sending to JDownloader...": SAB_STATUS_DOWNLOADING,
    "Downloading via JDownloader...": SAB_STATUS_DOWNLOADING,
    "Downloading...": SAB_STATUS_DOWNLOADING,
    "Links ready": SAB_STATUS_COMPLETED,
}


def _sab_status(text: str | None) -> str:
    """Convert free text into a SABnzbd status.

    The slot must always carry a value from `SAB_STATUSES`. An unknown text
    lands in "Downloading" and is logged - the original text is not lost, it
    is in the `message` field of the slot.
    """
    if not text:
        return SAB_STATUS_QUEUED
    if text in SAB_STATUSES:
        return text
    bekannt = _STATUS_TEXT_MAP.get(text)
    if bekannt is not None:
        return bekannt
    logger.warning(
        "Unknown bridge status %r - reporting it as %s. Update `app/AGENTS.md`.",
        text,
        SAB_STATUS_DOWNLOADING,
    )
    return SAB_STATUS_DOWNLOADING


def _format_size(size_bytes: int) -> str:
    """Bytes as text in the SABnzbd format ("1500.00 MB")."""
    if not size_bytes:
        return "0 MB"
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _bytes_aus_groessen_text(text: str | None) -> int:
    """Bytes from a size text like "1500.00 MB".

    Only for state files from an older bridge version: those stored the
    text, not the bytes.
    """
    if not text:
        return 0
    zahl, _, einheit = text.partition(" ")
    try:
        wert = float(zahl)
    except ValueError:
        return 0
    einheit = einheit.strip().upper()
    if einheit == "GB":
        return int(wert * 1024 * 1024 * 1024)
    if einheit == "KB":
        return int(wert * 1024)
    return int(wert * 1024 * 1024)


def _bekannte_felder(klasse: type, data: dict) -> dict:
    """Only pass through the fields the class knows.

    A state file from a newer version (or a rollback to an older one) must not
    cost the whole state: `_load_state()` catches the error and then keeps an
    empty queue - without any error message.
    """
    erlaubt = {f.name for f in fields(klasse)}
    return {k: v for k, v in data.items() if k in erlaubt}


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
    if not data.get("size_bytes"):
        # State file from an older version: it stored the text.
        data["size_bytes"] = _bytes_aus_groessen_text(data.get("size"))
    return QueueItem(**_bekannte_felder(QueueItem, data))


def _deserialize_history_item(data: dict) -> "HistoryItem":
    data = data.copy()
    if data.get("completed_at"):
        data["completed_at"] = datetime.fromisoformat(data["completed_at"])
    if not data.get("size_bytes"):
        data["size_bytes"] = _bytes_aus_groessen_text(data.get("size"))
    return HistoryItem(**_bekannte_felder(HistoryItem, data))


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
    """Item in the download queue."""

    nzo_id: str
    filename: str
    category: str
    status: str = SAB_STATUS_QUEUED
    percentage: int = 0
    eta: str = "unknown"
    timeleft: str = "0:00:00"
    added_at: datetime = field(default_factory=datetime.utcnow)

    #: Size in bytes. The **only** source for the size output - `size`/`sizeleft`
    #: (text) and `mb`/`mbleft` (number) are derived from it. Two separately
    #: maintained values are the reason why `sizeleft` permanently stayed at the
    #: initial value (#658).
    size_bytes: int = 0
    sizeleft_bytes: int = 0

    #: The detailed progress text ("Fetching links..."). `status` carries
    #: exclusively a value from `SAB_STATUSES` (#659).
    message: str = ""

    # DDLnzb specific
    release_id: str | None = None
    links: list[str] = field(default_factory=list)
    payload: str | None = None


@dataclass
class HistoryItem:
    """Item in the download history."""

    nzo_id: str
    name: str
    category: str
    status: str = SAB_STATUS_COMPLETED
    completed_at: datetime = field(default_factory=datetime.utcnow)
    storage: str = ""

    size_bytes: int = 0

    #: Detail text about the status - the error text on `Failed`, the plain
    #: text for "links without JDownloader" (#659).
    message: str = ""

    # DDLnzb specific
    release_id: str | None = None
    payload: str | None = None
    links: list[str] = field(default_factory=list)


# Must come after QueueItem and HistoryItem: _deserialize_queue_item and
# _deserialize_history_item create instances of these two classes. An earlier
# call runs into a NameError, which is swallowed by the except handler in
# _load_state() - the queue would then be silently empty.
_load_state()


@dataclass
class PayloadInfo:
    """Decoded payload information."""

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
    value: str = Query(None),
    del_files: str = Query(None),
    archive: str = Query(None),
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

    The nzo_id is carried by the SABnzbd client in the parameter `value`
    (`mode=delete&value=<nzo_id>`). Sonarr/Radarr additionally send
    `name=delete` with `mode=queue` and `mode=history` to remove the entry
    from the respective list. `nzo_id` remains supported as the bridge-native
    form (#657).
    """

    # Auth check using the configured bridge key
    if not config.DDLNZB_API_KEY:
        return _error_response("Bridge API key not configured", output)

    if not secrets.compare_digest(apikey or "", config.DDLNZB_API_KEY):
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
        # Sonarr/Radarr remove a queue entry with `mode=queue&name=delete`.
        # The plain call `mode=queue` remains the listing - an empty `name`
        # must not empty the queue (#657).
        if name == "delete":
            return _delete_item(value or nzo_id, output)
        return _get_queue(output)

    elif mode == "history":
        if name == "delete":
            return _delete_history_item(value or nzo_id, output)
        return _get_history(output)

    elif mode == "addurl":
        return await _add_url(name, nzbname, cat, output)

    elif mode == "addfile":
        return await _add_file(request, nzbname, cat, output)

    elif mode == "ddlnzb_links":
        return _get_links_response(nzo_id, output)

    elif mode == "delete":
        return _delete_item(value or nzo_id, output)

    elif mode == "retry":
        return _retry_item(value or nzo_id, output)

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


def _queue_slot(item: "QueueItem") -> dict:
    """A queue slot in the SABnzbd format.

    Sonarr/Radarr compute `TotalSize = mb * 1024 * 1024` and
    `RemainingSize = mbleft * 1024 * 1024`. Without the fields `mb` and
    `mbleft` they show every size as 0 MB (#658).
    """
    return {
        "nzo_id": item.nzo_id,
        "filename": item.filename,
        "cat": item.category,
        "status": item.status,
        "message": item.message,
        "percentage": str(item.percentage),
        "size": _format_size(item.size_bytes),
        "sizeleft": _format_size(item.sizeleft_bytes),
        "mb": round(item.size_bytes / (1024 * 1024), 2),
        "mbleft": round(item.sizeleft_bytes / (1024 * 1024), 2),
        "eta": item.eta,
        "timeleft": item.timeleft,
        "links": item.links,
    }


def _history_slot(item: "HistoryItem") -> dict:
    """A history slot in the SABnzbd format.

    Sonarr/Radarr read the size from `bytes` (byte count) - without this field
    `TotalSize` is 0 (#658). The error text is in `fail_message`, which the
    client takes over as `Message`.
    """
    return {
        "nzo_id": item.nzo_id,
        "name": item.name,
        "category": item.category,
        "status": item.status,
        "fail_message": item.message,
        "size": _format_size(item.size_bytes),
        "bytes": item.size_bytes,
        "completed": int(item.completed_at.timestamp()),
        "storage": item.storage,
        "links": item.links,
    }


def _get_queue(output: str):
    """Get current download queue."""
    slots = [_queue_slot(item) for item in download_queue.values()]

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
    slots = [_history_slot(item) for item in list(download_history.values())[-50:]]  # Last 50 items

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


def _delete_history_item(nzo_id: str, output: str):
    """Remove an entry from the history.

    Sonarr/Radarr delete history entries via `mode=history&name=delete` with
    the nzo_id in `value`. `mode=delete` only knows the queue - a history
    entry there would be an "Item not found" (#657).
    """
    if nzo_id in download_history:
        del download_history[nzo_id]
        _persist_state()
        return _json_or_text({"status": True}, output)
    return _error_response(f"Item not found: {nzo_id}", output)


def _retry_item(nzo_id: str, output: str):
    """Retry the download."""
    if nzo_id in download_history:
        item = download_history[nzo_id]
        # Completed downloads must not be queued again: the package no longer
        # exists in the JDownloader extraction folder, the retry would fail or
        # produce a second file.
        if item.status == SAB_STATUS_COMPLETED:
            return _error_response(f"Item already completed: {nzo_id}", output)
        # Push back into the queue. Take links, size and sizeleft along - a
        # retry without a link list starts the job without a usable source.
        queue_item = QueueItem(
            nzo_id=item.nzo_id,
            filename=item.name,
            category=item.category,
            status=SAB_STATUS_QUEUED,
            size_bytes=item.size_bytes,
            sizeleft_bytes=item.size_bytes,
            release_id=item.release_id,
            payload=item.payload,
            links=list(item.links),
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
def _move_to_history(
    item: QueueItem, *, status: str, storage: str = "", message: str = ""
) -> HistoryItem:
    """Move the queue entry into the history and persist the state.

    The only transition point from `download_queue` to `download_history`.
    Persisting is deliberately placed here: three separate call sites used to
    do it differently, whereby `fail_download` and `mark_manual_download`
    lost their entry on every restart.

    `status` must be a value from `SAB_STATUSES`, `message` carries the
    detailed text (#659).
    """
    history_item = HistoryItem(
        nzo_id=item.nzo_id,
        name=item.filename,
        category=item.category,
        status=status,
        message=message,
        size_bytes=item.size_bytes,
        storage=storage,
        release_id=item.release_id,
        payload=item.payload,
        links=item.links,
    )
    download_history[item.nzo_id] = history_item
    del download_queue[item.nzo_id]
    _persist_state()
    return history_item


def complete_download(nzo_id: str, storage_path: str = "", message: str = ""):
    """Complete the download and move it into the history.

    `storage_path` is the storage location of the finished file, `message` the
    plain text about the process (e.g. "Links available (3 URLs)").
    """
    if nzo_id in download_queue:
        _move_to_history(
            download_queue[nzo_id],
            status=SAB_STATUS_COMPLETED,
            storage=storage_path,
            message=message,
        )


def fail_download(nzo_id: str, error: str):
    """Mark the download as failed.

    The status is `Failed`, the error text is in `message` and is delivered as
    `fail_message` - Sonarr/Radarr take it over as `Message` (#659).
    """
    if nzo_id in download_queue:
        _move_to_history(
            download_queue[nzo_id],
            status=SAB_STATUS_FAILED,
            message=error,
        )


def update_progress(
    nzo_id: str,
    percentage: int,
    status: str | None = None,
    *,
    bytes_loaded: int | None = None,
    bytes_total: int | None = None,
) -> None:
    """Update the download progress.

    `status` is the free plain text ("Fetching links...", "Downloading via
    JDownloader..."). The slot carries the SABnzbd-compatible status, the text
    moves into `message` (#659).

    `bytes_loaded`/`bytes_total` come from JDownloader. If they are set, the
    remaining size is the actual remainder; otherwise it is derived from the
    progress (#658).
    """
    if nzo_id not in download_queue:
        return
    item = download_queue[nzo_id]
    item.percentage = percentage
    if status:
        item.status = _sab_status(status)
        item.message = status
    _set_sizeleft(item, bytes_loaded=bytes_loaded, bytes_total=bytes_total)


def set_item_size(item: QueueItem, size_bytes: int) -> None:
    """Set the size of a queue entry.

    The download handler knows the size from the payload. Without it `mb` and
    `mbleft` stay 0 and Sonarr/Radarr show 0 MB (#658).
    """
    item.size_bytes = size_bytes
    item.sizeleft_bytes = size_bytes


def _set_sizeleft(
    item: QueueItem, *, bytes_loaded: int | None = None, bytes_total: int | None = None
) -> None:
    """Write the remaining size of the entry.

    Sonarr/Radarr compute `RemainingSize = mbleft * 1024 * 1024`. If `sizeleft`
    stays at the initial value, the remaining display is permanently wrong -
    even while the download is running (#658).
    """
    if bytes_total and bytes_loaded is not None and bytes_total > 0:
        rest = max(0, bytes_total - bytes_loaded)
    else:
        rest = max(0, item.size_bytes - round(item.size_bytes * item.percentage / 100))
    item.sizeleft_bytes = rest


def mark_manual_download(nzo_id: str, message: str):
    """Move item to history with manual-download status."""
    if nzo_id in download_queue:
        _move_to_history(
            download_queue[nzo_id],
            status=SAB_STATUS_COMPLETED,
            message=message,
        )


def _enqueue_payload(payload: str, nzbname: str | None, category: str | None) -> QueueItem:
    """Create a queue item including payload and metadata."""
    info = decode_payload(payload)
    nzo_id = f"SABnzbd_nzo_{uuid.uuid4().hex[:12]}"
    filename = nzbname or info.title if info else (nzbname or nzo_id)
    size_bytes = info.size_bytes if info and info.size_bytes else 0
    item = QueueItem(
        nzo_id=nzo_id,
        filename=filename,
        category=category or "default",
        status=SAB_STATUS_QUEUED,
        size_bytes=size_bytes,
        sizeleft_bytes=size_bytes,
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
    """Extract the payload marker from a fake NZB file."""
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
