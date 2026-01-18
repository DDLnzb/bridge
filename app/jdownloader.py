"""
DDLnzb Bridge - JDownloader Integration

My JDownloader API client for adding links and monitoring downloads.
"""

import asyncio
import hashlib
import hmac
import json
import time
from base64 import b64decode, b64encode
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from app.config import config


class MyJDownloaderAPI:
    """My JDownloader API client."""
    
    API_URL = "https://api.jdownloader.org"
    APP_KEY = "DDLnzb"
    
    def __init__(self, email: str = None, password: str = None, device_name: str = None):
        self.email = email or config.MYJD_EMAIL
        self.password = password or config.MYJD_PASSWORD
        self.device_name = device_name or config.MYJD_DEVICE_NAME
        
        self.session_token: Optional[str] = None
        self.regain_token: Optional[str] = None
        self.device_id: Optional[str] = None
        self.device_secret: Optional[bytes] = None
        self.server_encryption_token: Optional[bytes] = None
        self.device_encryption_token: Optional[bytes] = None
        
        self._request_id = int(time.time() * 1000)
    
    def _get_request_id(self) -> int:
        """Get unique request ID."""
        self._request_id += 1
        return self._request_id
    
    def _secret(self, email: str, password: str, domain: str) -> bytes:
        """Generate secret from credentials."""
        return hashlib.sha256(
            f"{email.lower()}{password}{domain}".encode()
        ).digest()
    
    def _encrypt(self, data: str, key: bytes) -> str:
        """Encrypt data with AES."""
        iv = key[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(pad(data.encode(), AES.block_size))
        return b64encode(encrypted).decode()
    
    def _decrypt(self, data: str, key: bytes) -> str:
        """Decrypt data with AES."""
        iv = key[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = unpad(cipher.decrypt(b64decode(data)), AES.block_size)
        return decrypted.decode()
    
    def _sign(self, data: str, key: bytes) -> str:
        """Sign data with HMAC-SHA256."""
        return hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
    
    async def connect(self) -> bool:
        """Connect to My JDownloader API."""
        if not self.email or not self.password:
            raise ValueError("Email and password required")
        
        login_secret = self._secret(self.email, self.password, "server")
        device_secret = self._secret(self.email, self.password, "device")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_URL}/my/connect",
                params={
                    "email": self.email,
                    "appkey": self.APP_KEY,
                },
                data=self._encrypt(
                    json.dumps({"email": self.email, "appkey": self.APP_KEY}),
                    login_secret
                ),
            )
            
            if response.status_code != 200:
                return False
            
            data = json.loads(self._decrypt(response.text, login_secret))
            
            self.session_token = data.get("sessiontoken")
            self.regain_token = data.get("regaintoken")
            
            # Generate encryption tokens
            self.server_encryption_token = hashlib.sha256(
                login_secret + bytes.fromhex(self.session_token)
            ).digest()
            
            self.device_secret = device_secret
            
            return True
    
    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get list of connected devices."""
        return await self._api_call("/my/listdevices")
    
    async def get_device(self, name: str = None) -> Optional[Dict[str, Any]]:
        """Get device by name."""
        name = name or self.device_name
        devices = await self.get_devices()
        
        for device in devices.get("list", []):
            if device.get("name") == name:
                self.device_id = device.get("id")
                self.device_encryption_token = hashlib.sha256(
                    self.device_secret + bytes.fromhex(self.session_token)
                ).digest()
                return device
        
        return None
    
    async def add_links(
        self,
        links: List[str],
        package_name: str = None,
        destination_folder: str = None,
        extract_password: str = None,
        auto_start: bool = True,
    ) -> Dict[str, Any]:
        """
        Add links to JDownloader.
        
        Args:
            links: List of download URLs
            package_name: Name for the package
            destination_folder: Download destination
            extract_password: Archive password
            auto_start: Start download automatically
        """
        if not self.device_id:
            device = await self.get_device()
            if not device:
                raise ValueError(f"Device '{self.device_name}' not found")
        
        params = {
            "links": "\n".join(links),
            "autostart": auto_start,
        }
        
        if package_name:
            params["packageName"] = package_name
        if destination_folder:
            params["destinationFolder"] = destination_folder
        if extract_password:
            params["extractPassword"] = extract_password
        
        return await self._device_call(
            "/linkgrabberv2/addLinks",
            [params]
        )
    
    async def get_packages(self) -> List[Dict[str, Any]]:
        """Get download packages."""
        if not self.device_id:
            await self.get_device()
        
        return await self._device_call(
            "/downloadsV2/queryPackages",
            [{}]
        )
    
    async def get_links(self, package_ids: List[int] = None) -> List[Dict[str, Any]]:
        """Get download links."""
        if not self.device_id:
            await self.get_device()
        
        params = {}
        if package_ids:
            params["packageUUIDs"] = package_ids
        
        return await self._device_call(
            "/downloadsV2/queryLinks",
            [params]
        )
    
    async def start_downloads(self) -> bool:
        """Start all downloads."""
        if not self.device_id:
            await self.get_device()
        
        await self._device_call("/downloadcontroller/start", [])
        return True
    
    async def stop_downloads(self) -> bool:
        """Stop all downloads."""
        if not self.device_id:
            await self.get_device()
        
        await self._device_call("/downloadcontroller/stop", [])
        return True
    
    async def _api_call(self, path: str, params: dict = None) -> Dict[str, Any]:
        """Make API call to My JDownloader."""
        if not self.session_token:
            raise ValueError("Not connected")
        
        query = f"sessiontoken={self.session_token}&rid={self._get_request_id()}"
        if params:
            for k, v in params.items():
                query += f"&{k}={quote(str(v))}"
        
        signature = self._sign(query, self.server_encryption_token)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_URL}{path}?{query}&signature={signature}",
            )
            
            if response.status_code != 200:
                raise Exception(f"API error: {response.status_code}")
            
            return json.loads(
                self._decrypt(response.text, self.server_encryption_token)
            )
    
    async def _device_call(self, path: str, params: list = None) -> Any:
        """Make API call to device."""
        if not self.device_id or not self.device_encryption_token:
            raise ValueError("Device not connected")
        
        rid = self._get_request_id()
        
        data = {
            "url": path,
            "params": params or [],
            "rid": rid,
            "apiVer": 1,
        }
        
        encrypted_data = self._encrypt(
            json.dumps(data),
            self.device_encryption_token
        )
        
        query = f"sessiontoken={self.session_token}&rid={rid}"
        signature = self._sign(query, self.server_encryption_token)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_URL}/t_{self.session_token}_{self.device_id}{path}?{query}&signature={signature}",
                data=encrypted_data,
            )
            
            if response.status_code != 200:
                raise Exception(f"Device API error: {response.status_code}")
            
            result = json.loads(
                self._decrypt(response.text, self.device_encryption_token)
            )
            
            return result.get("data")
    
    async def disconnect(self) -> bool:
        """Disconnect from API."""
        if self.session_token:
            try:
                await self._api_call("/my/disconnect")
            except Exception:
                pass
            
            self.session_token = None
            self.device_id = None
        
        return True


# Global instance
jdownloader = MyJDownloaderAPI()
