"""
DDLnzb Bridge - JDownloader Integration

My JDownloader API wrapper using the official myjdapi client.
"""

import asyncio
import logging
from typing import Any

from myjdapi import Myjdapi

from app.config import config

logger = logging.getLogger("ddlnzb.bridge.jdownloader")


class MyJDownloaderAPI:
    """Async wrapper around myjdapi."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        device_name: str | None = None,
    ):
        self.email = email or config.MYJD_EMAIL
        self.password = password or config.MYJD_PASSWORD
        self.device_name = device_name or config.MYJD_DEVICE_NAME

        self._client = Myjdapi()
        self._client.set_app_key(config.MYJD_APP_KEY)
        self._device = None

        self.device_id: str | None = None
        self.last_error: str | None = None

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def connect(self) -> bool:
        """Connect to My JDownloader API."""
        if not self.email or not self.password:
            self.last_error = "Email and password required"
            raise ValueError(self.last_error)

        logger.debug(
            "Connecting to MyJDownloader appkey=%s email_configured=%s device=%s",
            config.MYJD_APP_KEY,
            bool(self.email),
            self.device_name,
        )
        try:
            await self._run(self._client.connect, self.email, self.password)
        except Exception as exc:
            self.last_error = f"MyJD login failed: {exc}"
            logger.error(self.last_error)
            raise RuntimeError(self.last_error) from exc

        self.last_error = None
        return True

    async def get_devices(self) -> dict[str, list[dict[str, Any]]]:
        """Get list of connected devices."""
        await self._run(self._client.update_devices)
        devices = await self._run(self._client.list_devices) or []
        return {"list": devices}

    async def get_device(self, name: str | None = None) -> dict[str, Any] | None:
        """Get device by name and cache it."""
        await self._run(self._client.update_devices)
        device_name = name or self.device_name
        try:
            device = await self._run(self._client.get_device, device_name)
        except Exception:
            return None

        self._device = device
        self.device_id = getattr(device, "device_id", None)

        return {
            "name": getattr(device, "name", ""),
            "id": getattr(device, "device_id", ""),
            "type": getattr(device, "device_type", ""),
        }

    async def add_links(
        self,
        links: list[str],
        package_name: str | None = None,
        destination_folder: str | None = None,
        extract_password: str | None = None,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        """Add links to JDownloader."""
        if not self._device:
            device = await self.get_device()
            if not device:
                raise ValueError(f"Device '{self.device_name}' not found")

        params = {
            "autostart": auto_start,
            "links": "\n".join(links),
            "packageName": package_name,
            "extractPassword": extract_password,
            "destinationFolder": destination_folder,
            "priority": "DEFAULT",
            "downloadPassword": None,
            "overwritePackagizerRules": False,
        }

        return await self._run(self._device.linkgrabber.add_links, [params])

    async def get_packages(self) -> list[dict[str, Any]]:
        """Get download packages."""
        if not self._device:
            await self.get_device()
        return await self._run(self._device.downloads.query_packages)

    async def get_links(self, package_ids: list[int] | None = None) -> list[dict[str, Any]]:
        """Get download links."""
        if not self._device:
            await self.get_device()
        if package_ids:
            params = [{"packageUUIDs": package_ids}]
            return await self._run(self._device.downloads.query_links, params)
        return await self._run(self._device.downloads.query_links)

    async def start_downloads(self) -> bool:
        """Start all downloads."""
        if not self._device:
            await self.get_device()
        await self._run(self._device.downloadcontroller.start_downloads)
        return True

    async def stop_downloads(self) -> bool:
        """Stop all downloads."""
        if not self._device:
            await self.get_device()
        await self._run(self._device.downloadcontroller.stop_downloads)
        return True

    async def disconnect(self) -> bool:
        """Disconnect from API."""
        try:
            await self._run(self._client.disconnect)
        except Exception:
            pass

        self._device = None
        self.device_id = None
        return True

    async def debug_snapshot(self) -> dict[str, Any]:
        """Return diagnostic information about the current JD connection."""
        snapshot: dict[str, Any] = {
            "email_configured": bool(self.email),
            "device_name": self.device_name,
            "device_id_cached": self.device_id,
            "last_error": self.last_error,
        }
        try:
            devices = await self.get_devices()
        except Exception as exc:  # pragma: no cover - debug helper
            snapshot["device_list_error"] = str(exc)
        else:
            snapshot["available_devices"] = [
                device.get("name") for device in devices.get("list", [])
            ]
        return snapshot


# Global instance
jdownloader = MyJDownloaderAPI()
