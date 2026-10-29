"""Источник данных разбора: series.json из хранилища записей и метаданные сессии из представления reader.sessions."""

import json
import logging

from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple

from src.session_archive import recording_keys

_log = logging.getLogger("affectra_mcp.data")


class RecordingSource:
    """
    Чтение данных одной сессии с правами только на чтение.

    Args:
        storage    : S3Storage с ключом reader (чтение бакета записей).
        db_connect : Фабрика соединения psycopg2 под ролью affectra_reader; None — без метаданных.
    """

    def __init__(self, storage, db_connect: Optional[Callable[[], Any]] = None):
        self.storage    = storage
        self.db_connect = db_connect

    def series(self, session_id: str) -> Dict[str, Any]:
        """
        Документ series.json сессии.

        Raises:
            FileNotFoundError: У записи нет series.json (соберите scripts/build_series.py).
        """
        key = recording_keys(session_id)["series"]
        try:
            return json.loads(self.storage.get(key)["Body"].read())
        except Exception as e:
            raise FileNotFoundError(f"{key}: {e}") from e

    def meta(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Имя, тип и время сессии из reader.sessions; None, если БД недоступна или записи нет."""
        if self.db_connect is None:
            return None
        try:
            conn = self.db_connect()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT camera_id, name, kind, status, started_at, finished_at, duration_sec "
                                "FROM reader.sessions WHERE camera_id = %s", (session_id,))
                    row = cur.fetchone()
            finally:
                conn.close()
        except Exception as e:
            _log.warning(f"session meta unavailable: {e}")
            return None
        if row is None:
            return None
        keys = ("camera_id", "name", "kind", "status", "started_at", "finished_at", "duration_sec")
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(keys, row)}

    def load(self, session_id: str) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        return self.series(session_id), self.meta(session_id)
