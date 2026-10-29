"""Ключи итоговых записей сессий в объектном хранилище."""

from typing import Dict

from src.object_storage import S3Settings
from src.object_storage import S3Storage

__all__ = ["S3Settings", "S3Storage", "recording_keys"]


def recording_keys(camera_id: str) -> Dict[str, str]:
    """
    Ключи объектов записи сессии.

    Args:
        camera_id (str): Идентификатор сессии.

    Returns:
        Dict[str, str]: Ключи video, analytics и series.
    """
    return {"video": f"sessions/{camera_id}/recording.mp4", "analytics": f"sessions/{camera_id}/analytics.json",
            "series": f"sessions/{camera_id}/series.json"}
