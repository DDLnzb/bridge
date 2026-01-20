"""
DDLnzb Bridge - Download Handler

Handles the download flow: fetch links from cloud, send to JDownloader.
"""

import asyncio
import base64
import logging
from typing import Optional

import httpx

from app.config import config
from app.jdownloader import jdownloader
from app.sabnzbd_api import complete_download, fail_download, update_progress, download_queue


logger = logging.getLogger("ddlnzb.bridge")


class DownloadHandler:
    """Handles download processing."""
    
    def __init__(self):
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
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
                    if item.status == "Queued":
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
            release_info = self._parse_payload(payload_source)
            if not release_info:
                fail_download(nzo_id, "Invalid payload")
                logger.warning("Invalid payload for %s", nzo_id)
                return
            
            title, source_url, mirror, size, password, imdb_id = release_info
            if size and size > 0:
                size_mb = f"{size / (1024 * 1024):.2f} MB"
                item.size = size_mb
                item.sizeleft = size_mb
            item.release_id = source_url
            logger.debug(
                "Parsed payload for %s: title=%s mirror=%s size=%s",
                nzo_id,
                title,
                mirror,
                size,
            )
            
            update_progress(nzo_id, 10, "Getting download links...")
            
            # Fetch actual download links from DDLnzb Cloud
            links = await self._fetch_links_from_cloud(source_url, mirror, item.release_id)
            if not links:
                fail_download(nzo_id, "No links available")
                logger.warning("No links available for %s (%s)", nzo_id, title)
                return
            
            update_progress(nzo_id, 20, "Sending to JDownloader...")
            logger.debug("Fetched %s links for %s", len(links), nzo_id)
            
            # Determine destination folder based on category
            dest_folder = config.CATEGORY_MAPPING.get(
                item.category,
                config.DOWNLOAD_DIR
            )
            
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
                complete_download(nzo_id, f"Links available ({len(links)} URLs)")
                
        except Exception as e:
            logger.exception("Processing error for %s: %s", nzo_id, e)
            fail_download(nzo_id, str(e))
    
    def _parse_payload(self, payload_str: str) -> Optional[tuple]:
        """Parse DDLnzb payload."""
        try:
            # Payload format: title|source|mirror|size|password|imdb_id
            decoded = base64.urlsafe_b64decode(payload_str).decode('utf-8')
            parts = decoded.split("|")
            
            if len(parts) >= 6:
                return (
                    parts[0],  # title
                    parts[1],  # source_url
                    parts[2] or None,  # mirror
                    int(parts[3]) if parts[3] else 0,  # size
                    parts[4] or None,  # password
                    parts[5] or None,  # imdb_id
                )
            return None
        except Exception:
            return None
    
    async def _fetch_links_from_cloud(
        self,
        source_url: str,
        mirror: Optional[str] = None,
        release_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> list[str]:
        """Fetch download links from DDLnzb Cloud API with retries."""
        backoff = 1
        endpoint = "by-source"
        params: dict[str, str] = {}
        if release_id:
            endpoint = release_id
        else:
            params["source_url"] = source_url
            if mirror and mirror != "*":
                params["hoster"] = mirror
        
        headers = {}
        if config.DDLNZB_API_KEY:
            headers["X-API-Key"] = config.DDLNZB_API_KEY
        
        for attempt in range(1, max_retries + 1):
            try:
                url = f"{config.DDLNZB_API_URL}/api/v1/links/{endpoint}"
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        params=params or None,
                        headers=headers,
                        timeout=30,
                    )
                
                if response.status_code == 200:
                    data = response.json()
                    links = [link["url"] for link in data.get("links", []) if link.get("url")]
                    if links:
                        logger.debug(
                            "Received %s links from cloud (release=%s attempt=%s)",
                            len(links),
                            release_id or source_url,
                            attempt,
                        )
                        return links
                    logger.warning(
                        "Cloud returned no links for release %s",
                        release_id or source_url,
                    )
                    return []
                elif response.status_code in (404, 410):
                    logger.warning("Release not found for %s", release_id or source_url)
                    return []
                else:
                    logger.error(
                        "Cloud error %s for %s: %s",
                        response.status_code,
                        release_id or source_url,
                        response.text,
                    )
            except httpx.RequestError as e:
                logger.warning(
                    "Cloud request failed (attempt %s) for %s: %s",
                    attempt,
                    release_id or source_url,
                    e,
                )
            except Exception as e:
                logger.exception(
                    "Unexpected cloud error (attempt %s) for %s: %s",
                    attempt,
                    release_id or source_url,
                    e,
                )
            await asyncio.sleep(backoff)
            backoff *= 2
        return []
    
    async def _monitor_jdownloader(self, nzo_id: str, package_name: str):
        """Monitor JDownloader download progress."""
        max_checks = 360  # 30 minutes max
        check_interval = 5
        
        for _ in range(max_checks):
            if nzo_id not in download_queue:
                return  # Download was cancelled
            
            try:
                packages = await jdownloader.get_packages()
                
                for pkg in packages or []:
                    if package_name.lower() in pkg.get("name", "").lower():
                        # Calculate progress
                        bytes_loaded = pkg.get("bytesLoaded", 0)
                        bytes_total = pkg.get("bytesTotal", 1)
                        progress = int((bytes_loaded / bytes_total) * 100)
                        
                        update_progress(nzo_id, 20 + int(progress * 0.8), "Downloading...")
                        
                        # Check if complete
                        if pkg.get("finished", False):
                            complete_download(
                                nzo_id,
                                pkg.get("saveTo", config.DOWNLOAD_DIR)
                            )
                            return
                        
                        break
                
            except Exception as e:
                print(f"JDownloader monitoring error: {e}")
            
            await asyncio.sleep(check_interval)
        
        # Timeout
        fail_download(nzo_id, "Download timeout")


# Global instance
download_handler = DownloadHandler()
