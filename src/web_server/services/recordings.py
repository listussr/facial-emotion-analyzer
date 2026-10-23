"""Клиент хранилища записей сессий (создаётся при первом обращении) и удаление записи целиком."""

import threading

from typing import Optional

from src.session_archive import S3Settings
from src.session_archive import S3Storage
from .db import get_cursor

_storage: Optional[S3Storage] = None
_lock     = threading.Lock()


def storage() -> S3Storage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = S3Storage(S3Settings.from_env())
        return _storage


def delete_recording_data(session_id: str) -> int:
    """
    Удаление видео и аналитики записи из хранилища и строки из session_recordings.

    Args:
        session_id (str): Идентификатор сессии.

    Returns:
        int: Сколько объектов удалено из хранилища.
    """
    deleted = storage().delete_prefix(f"sessions/{session_id}/")
    with get_cursor() as cur:
        cur.execute("DELETE FROM session_recordings WHERE camera_id = %s", (session_id,))
    return deleted
