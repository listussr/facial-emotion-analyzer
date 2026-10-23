"""Клиент хранилища записей сессий; создаётся при первом обращении."""

import threading

from typing import Optional

from src.session_archive import S3Settings
from src.session_archive import S3Storage

_storage: Optional[S3Storage] = None
_lock     = threading.Lock()


def storage() -> S3Storage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = S3Storage(S3Settings.from_env())
        return _storage
