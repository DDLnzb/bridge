"""
DDLnzb Bridge - JSON Persistence

Persists download queue to JSON file for recovery after restart.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import config

logger = logging.getLogger("ddlnzb.bridge.persistence")

# Default persistence file path
DEFAULT_QUEUE_FILE = Path(config.DATA_DIR) / "queue.json"
SCHEMA_VERSION = 1


class QueuePersistence:
    """JSON-based queue persistence."""

    def __init__(self, file_path: Path | str | None = None):
        self.file_path = Path(file_path) if file_path else DEFAULT_QUEUE_FILE
        self._ensure_data_dir()

    def _ensure_data_dir(self) -> None:
        """Ensure data directory exists."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, queue: dict[str, Any]) -> bool:
        """Save queue to JSON file.

        Args:
            queue: Dictionary of nzo_id -> QueueItem (or dict representation)

        Returns:
            True if saved successfully, False otherwise.
        """
        try:
            jobs = []
            for nzo_id, item in queue.items():
                job = {
                    "id": nzo_id,
                    "status": getattr(item, "status", item.get("status", "Unknown")),
                    "filename": getattr(item, "filename", item.get("filename", "")),
                    "category": getattr(item, "category", item.get("category", "")),
                    "payload": getattr(item, "payload", item.get("payload", "")),
                    "size": getattr(item, "size", item.get("size", "")),
                    "links": getattr(item, "links", item.get("links", [])),
                    "release_id": getattr(item, "release_id", item.get("release_id", "")),
                    "created_at": getattr(
                        item, "created_at", item.get("created_at", datetime.utcnow().isoformat())
                    ),
                    "updated_at": datetime.utcnow().isoformat(),
                }
                jobs.append(job)

            data = {
                "version": SCHEMA_VERSION,
                "saved_at": datetime.utcnow().isoformat(),
                "jobs": jobs,
            }

            # Write atomically via temp file
            temp_path = self.file_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            temp_path.replace(self.file_path)

            logger.debug("Saved %d jobs to %s", len(jobs), self.file_path)
            return True

        except Exception as exc:
            logger.error("Failed to save queue: %s", exc)
            return False

    def load(self) -> list[dict[str, Any]]:
        """Load queue from JSON file.

        Returns:
            List of job dictionaries, empty list if file doesn't exist or is invalid.
        """
        if not self.file_path.exists():
            logger.debug("No queue file found at %s", self.file_path)
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            version = data.get("version", 0)
            if version != SCHEMA_VERSION:
                logger.warning(
                    "Queue file version mismatch: expected %d, got %d",
                    SCHEMA_VERSION,
                    version,
                )
                # For now, try to load anyway - future migrations can be added here

            jobs = data.get("jobs", [])
            logger.info("Loaded %d jobs from %s", len(jobs), self.file_path)
            return jobs

        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON in queue file: %s", exc)
            return []
        except Exception as exc:
            logger.error("Failed to load queue: %s", exc)
            return []

    def clear(self) -> bool:
        """Delete the queue file."""
        try:
            if self.file_path.exists():
                self.file_path.unlink()
                logger.info("Cleared queue file %s", self.file_path)
            return True
        except Exception as exc:
            logger.error("Failed to clear queue file: %s", exc)
            return False


# Global instance
queue_persistence = QueuePersistence()
