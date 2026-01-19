"""
DDLnzb Bridge - Main Application

Local bridge that acts as SABnzbd for *arr apps and sends downloads to JDownloader.
"""

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.sabnzbd_api import router as sabnzbd_router
from app.jdownloader import jdownloader
from app.download_handler import download_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print(f"Starting DDLnzb Bridge on {config.BRIDGE_HOST}:{config.BRIDGE_PORT}")
    
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
    
    # Start download handler loop
    try:
        await download_handler.start()
    except Exception as e:
        print(f"Failed to start download handler: {e}")
    
    yield
    
    # Cleanup
    await download_handler.stop()
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


@app.get("/")
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
    """Health check endpoint."""
    return {"status": "healthy"}


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
