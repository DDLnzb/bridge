"""
DDLnzb Bridge - JDownloader Heartbeat

Background task that maintains JDownloader connection when downloads are active.
"""

import asyncio
import logging
from datetime import datetime

from app.config import config
from app.jdownloader import jdownloader
from app.sabnzbd_api import download_queue

logger = logging.getLogger("ddlnzb.bridge.heartbeat")

# Heartbeat interval in seconds (only when downloads active)
HEARTBEAT_INTERVAL = 60

# Reconnect settings
MAX_RECONNECT_ATTEMPTS = 3
RECONNECT_BACKOFF = [5, 15, 30]  # seconds


class JDownloaderHeartbeat:
    """Manages JDownloader connection health."""

    def __init__(self):
        self.running = False
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0
        self._last_success: datetime | None = None
        self._last_error: str | None = None

    @property
    def is_healthy(self) -> bool:
        """Check if JDownloader connection is healthy."""
        return self._consecutive_failures == 0 and jdownloader.device_id is not None

    def mark_connected(self) -> None:
        """The connection works again - clear the error state (#681).

        The web UI reconnects JDownloader (`POST /ui/reconnect`) but did not
        touch the heartbeat state: a `last_error` from an earlier attempt
        stayed in `/status` and `is_healthy` stayed `False`. The heartbeat
        only cleans up in `_ping()`, and that runs **only with active
        downloads** - without a download the stale error stood forever.
        """
        self._consecutive_failures = 0
        self._last_error = None
        self._last_success = datetime.utcnow()

    def mark_failed(self, message: str) -> None:
        """Note the **current** error - not an older one (#681).

        The counter stays untouched on purpose: it belongs to the ping
        attempts of the heartbeat. A manual reconnect that would push it
        over `MAX_RECONNECT_ATTEMPTS` would stop the automatic reconnect
        for good ("manual intervention required").
        """
        self._last_error = message

    @property
    def status(self) -> dict:
        """Get heartbeat status."""
        return {
            "running": self.running,
            "healthy": self.is_healthy,
            "consecutive_failures": self._consecutive_failures,
            "last_success": self._last_success.isoformat() if self._last_success else None,
            "last_error": self._last_error,
            "device_id": jdownloader.device_id,
        }

    def _has_active_downloads(self) -> bool:
        """Check if there are active downloads in the queue."""
        if not download_queue:
            return False
        for item in download_queue.values():
            status = getattr(item, "status", "")
            if status in ("Queued", "Downloading", "Extracting"):
                return True
        return False

    async def start(self):
        """Start the heartbeat loop."""
        if self.running:
            logger.debug("Heartbeat already running")
            return

        self.running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info("JDownloader heartbeat started (interval=%ds)", HEARTBEAT_INTERVAL)

    async def stop(self):
        """Stop the heartbeat loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("JDownloader heartbeat stopped")

    async def _heartbeat_loop(self):
        """Main heartbeat loop."""
        while self.running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                # Only ping if there are active downloads
                if not self._has_active_downloads():
                    logger.debug("No active downloads, skipping heartbeat")
                    continue

                # Perform heartbeat check
                success = await self._ping()
                if success:
                    self.mark_connected()
                    logger.debug("JDownloader heartbeat OK")
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "JDownloader heartbeat failed (attempt %d/%d)",
                        self._consecutive_failures,
                        MAX_RECONNECT_ATTEMPTS,
                    )

                    # Try to reconnect
                    if self._consecutive_failures <= MAX_RECONNECT_ATTEMPTS:
                        await self._reconnect()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Heartbeat loop error: %s", exc)
                self._last_error = str(exc)
                await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _ping(self) -> bool:
        """Ping JDownloader to check connection."""
        try:
            if not jdownloader.device_id:
                # Not connected yet
                return False

            # Try to get devices - this validates the connection
            devices = await jdownloader.get_devices()
            device_list = devices.get("list", [])

            # Check if our device is still in the list
            for device in device_list:
                if device.get("id") == jdownloader.device_id:
                    return True
                if device.get("name") == config.MYJD_DEVICE_NAME:
                    return True

            logger.warning("Device %s not found in device list", config.MYJD_DEVICE_NAME)
            return False

        except Exception as exc:
            self._last_error = str(exc)
            logger.debug("Ping failed: %s", exc)
            return False

    async def _reconnect(self) -> bool:
        """Attempt to reconnect to JDownloader."""
        attempt = self._consecutive_failures
        if attempt > MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "Max reconnect attempts (%d) exceeded, manual intervention required",
                MAX_RECONNECT_ATTEMPTS,
            )
            return False

        backoff = RECONNECT_BACKOFF[min(attempt - 1, len(RECONNECT_BACKOFF) - 1)]
        logger.info("Attempting reconnect in %ds (attempt %d)", backoff, attempt)
        await asyncio.sleep(backoff)

        try:
            # Disconnect first
            await jdownloader.disconnect()

            # Reconnect
            await jdownloader.connect()
            device = await jdownloader.get_device()

            if device:
                logger.info("Reconnected to JDownloader device: %s", device.get("name"))
                self.mark_connected()
                return True
            else:
                self._last_error = f"Device '{config.MYJD_DEVICE_NAME}' not found after reconnect"
                logger.warning(self._last_error)
                return False

        except Exception as exc:
            self._last_error = str(exc)
            logger.error("Reconnect failed: %s", exc)
            return False

    async def force_reconnect(self) -> bool:
        """Force a reconnect attempt (for manual trigger)."""
        logger.info("Forcing JDownloader reconnect")
        self._consecutive_failures = 1
        return await self._reconnect()


# Global instance
jd_heartbeat = JDownloaderHeartbeat()
