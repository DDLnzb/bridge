"""
DDLnzb Bridge - Download Handler

Handles the download flow: fetch links from cloud, send to JDownloader.
"""

import asyncio
import logging
from urllib.parse import quote

import httpx

from app.config import config
from app.jdownloader import jdownloader
from app.sabnzbd_api import (
    SAB_STATUS_QUEUED,
    complete_download,
    decode_payload,
    download_queue,
    fail_download,
    set_item_size,
    update_progress,
)

logger = logging.getLogger("ddlnzb.bridge")


class DownloadHandler:
    """Handles download processing."""

    def __init__(self):
        self.running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the download handler."""
        if self.running:
            logger.debug("Download handler already running")
            return

        self.running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Download handler loop started")

    async def stop(self):
        """Stop the download handler."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Download handler loop stopped")

    async def _process_loop(self):
        """Main processing loop."""
        while self.running:
            try:
                # Process pending downloads
                for nzo_id, item in list(download_queue.items()):
                    if item.status == SAB_STATUS_QUEUED:
                        logger.debug("Starting processing for %s (%s)", nzo_id, item.filename)
                        update_progress(nzo_id, 0, "Starting download...")
                        await self._process_download(nzo_id, item)

                await asyncio.sleep(5)
            except Exception as e:
                logger.exception("Download handler error: %s", e)
                await asyncio.sleep(10)

    async def _process_download(self, nzo_id: str, item):
        """Process a single download."""
        try:
            update_progress(nzo_id, 5, "Fetching links...")

            # Parse payload to get release info
            payload_source = item.payload or item.filename
            info = decode_payload(payload_source)
            if not info:
                fail_download(nzo_id, "Invalid payload")
                logger.warning("Invalid payload for %s", nzo_id)
                return

            # Das Payload traegt die Release-UUID - nicht die Portal-URL.
            # Der Decoder ist der einzige Ort, der das Format kennt; er
            # prueft die ID auch als UUID, bevor sie in einen Pfad wandert.
            title = info.title
            release_id = info.release_id
            password = info.password
            if info.size_bytes and info.size_bytes > 0:
                set_item_size(item, info.size_bytes)
            item.release_id = release_id
            logger.debug(
                "Parsed payload for %s: title=%s mirror=%s size=%s",
                nzo_id,
                title,
                info.mirror,
                info.size_bytes,
            )

            update_progress(nzo_id, 10, "Getting download links...")

            # Fetch actual download links from DDLnzb Cloud
            links = await self._fetch_links_from_cloud(item.release_id)
            if not links:
                fail_download(nzo_id, "No links available")
                logger.warning("No links available for %s (%s)", nzo_id, title)
                return

            update_progress(nzo_id, 20, "Sending to JDownloader...")
            logger.debug("Fetched %s links for %s", len(links), nzo_id)

            # Determine destination folder based on category
            dest_folder = config.CATEGORY_MAPPING.get(item.category, config.DOWNLOAD_DIR)

            # Send to JDownloader
            if jdownloader.device_id:
                await jdownloader.add_links(
                    links=links,
                    package_name=title,
                    destination_folder=dest_folder,
                    extract_password=password,
                    auto_start=True,
                )

                update_progress(nzo_id, 50, "Downloading via JDownloader...")
                logger.info("Sent package %s to JDownloader", title)

                # Monitor JDownloader progress
                await self._monitor_jdownloader(nzo_id, title)
            else:
                # No JDownloader - just store links for manual usage
                item.links = links
                update_progress(nzo_id, 100, "Links ready")
                logger.info("Stored links for %s; no JDownloader configured", nzo_id)
                # No JDownloader, so there is no storage location - the text
                # belongs into the plain-text field, not into `storage` (#659).
                complete_download(nzo_id, "", f"Links available ({len(links)} URLs)")

        except Exception as e:
            logger.exception("Processing error for %s: %s", nzo_id, e)
            fail_download(nzo_id, str(e))

    async def _fetch_links_from_cloud(
        self,
        release_id: str | None,
        max_retries: int = 3,
    ) -> list[str]:
        """Fetch download links from the DDLnzb Cloud API with retries.

        `release_id` is the release UUID. The backend knows exactly one
        endpoint: `GET /api/v1/links/{release_id}` - there is no `by-source`
        path, the former mirror filter was dead code.
        """
        if not release_id:
            logger.error("Payload contains no release ID - cloud fetch skipped")
            return []

        backoff = 1
        # Encode the path segment: the ID comes from a base64 payload and is
        # interpolated into the path without validation.
        endpoint = quote(release_id, safe="")

        headers = {}
        if config.DDLNZB_API_KEY:
            headers["X-API-Key"] = config.DDLNZB_API_KEY

        logger.info(
            "Connecting to DDLnzb API for links: base_url=%s endpoint=%s",
            config.DDLNZB_API_URL,
            endpoint,
        )
        logger.debug(
            "DDLnzb API request details: url=%s api_key_configured=%s",
            f"{config.DDLNZB_API_URL}/api/v1/links/{endpoint}",
            bool(config.DDLNZB_API_KEY),
        )

        for attempt in range(1, max_retries + 1):
            try:
                url = f"{config.DDLNZB_API_URL}/api/v1/links/{endpoint}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        headers=headers,
                        timeout=30,
                    )

                if response.status_code == 200:
                    data = response.json()
                    links = [link["url"] for link in data.get("links", []) if link.get("url")]
                    if links:
                        logger.info(
                            "DDLnzb API returned %s links for %s",
                            len(links),
                            release_id,
                        )
                        logger.debug(
                            "Received %s links from cloud (release=%s attempt=%s)",
                            len(links),
                            release_id,
                            attempt,
                        )
                        return links
                    logger.warning(
                        "Cloud returned no links for release %s",
                        release_id,
                    )
                    return []
                elif response.status_code in (404, 410):
                    logger.warning("Release not found for %s", release_id)
                    return []
                else:
                    logger.error(
                        "Cloud error %s for %s: %s",
                        response.status_code,
                        release_id,
                        response.text,
                    )
            except httpx.RequestError as e:
                logger.warning(
                    "Cloud request failed (attempt %s) for %s: %s",
                    attempt,
                    release_id,
                    e,
                )
            except Exception as e:
                logger.exception(
                    "Unexpected cloud error (attempt %s) for %s: %s",
                    attempt,
                    release_id,
                    e,
                )
            await asyncio.sleep(backoff)
            backoff *= 2
        logger.error(
            "DDLnzb API unreachable after %s attempts: %s",
            max_retries,
            config.DDLNZB_API_URL,
        )
        return []

    async def _monitor_jdownloader(self, nzo_id: str, package_name: str):
        """Monitor JDownloader download progress."""
        max_duration = 30 * 60  # 30 minutes max
        active_interval = 5
        idle_interval = 300  # 5 minutes
        deadline = asyncio.get_event_loop().time() + max_duration

        # `matched` is a state of the **monitoring**, not of the single poll
        # call, and therefore lives outside the loop.
        #
        # Until 2026-10-19 the `except` branch always slept 300 s, even while
        # a download was running. A single unresponsive JDownloader froze the
        # progress display for five minutes and burned the rest of the
        # 30-minute deadline (#477).
        #
        # The state is only reset when JDownloader answered **successfully**
        # and the package was no longer contained in it. A failure during the
        # fetch says nothing about the package state - that is the difference
        # that justifies the 300 s here.
        matched = False

        while asyncio.get_event_loop().time() < deadline:
            if nzo_id not in download_queue:
                return  # Download was cancelled

            try:
                packages = await jdownloader.get_packages()
                # The query succeeded: the state is now known.
                matched = False
                for pkg in packages or []:
                    if package_name.lower() in pkg.get("name", "").lower():
                        matched = True
                        # Calculate progress. JDownloader reports bytesTotal = 0
                        # as long as the total size of the package is not known -
                        # progress then stays at 0 instead of dividing by zero
                        # and breaking the monitoring loop.
                        bytes_loaded = pkg.get("bytesLoaded") or 0
                        bytes_total = pkg.get("bytesTotal") or 0
                        if bytes_total > 0:
                            progress = min(100, max(0, int((bytes_loaded / bytes_total) * 100)))
                        else:
                            progress = 0

                        # Pass the real bytes along: `update_progress` derives
                        # the remaining size (`sizeleft`) from them. Without
                        # them `sizeleft` would stay at the initial value
                        # (#658).
                        update_progress(
                            nzo_id,
                            20 + int(progress * 0.8),
                            "Downloading...",
                            bytes_loaded=bytes_loaded,
                            bytes_total=bytes_total,
                        )

                        # Check if complete
                        if pkg.get("finished", False):
                            complete_download(nzo_id, pkg.get("saveTo", config.DOWNLOAD_DIR))
                            return

                        break

                await asyncio.sleep(active_interval if matched else idle_interval)
            except Exception as e:
                logger.warning("JDownloader monitoring error: %s", e)
                # `matched` deliberately sits outside the `try` - see the
                # explanation at the loop head. A poll error during a running
                # download must not extend the wait to 300 s.
                await asyncio.sleep(active_interval if matched else idle_interval)

        # Timeout
        fail_download(nzo_id, "Download timeout")


# Global instance
download_handler = DownloadHandler()
