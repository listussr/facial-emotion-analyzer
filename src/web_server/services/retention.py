"""Автоочистка хранилища: записи и загрузки старше заданного срока удаляются фоновым потоком."""

import logging
import threading
import time

from datetime import datetime
from datetime import timezone
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from .db import get_cursor
from .recordings import delete_recording_data

_log = logging.getLogger("retention")

DAY_S = 86_400


def recordings_older_than(cutoff: float) -> List[str]:
    """Готовые и неудавшиеся записи, законченные раньше cutoff; идущие и рендерящиеся не трогаются."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT camera_id FROM session_recordings WHERE status IN ('ready', 'failed') "
            "AND COALESCE(finished_at, started_at) < %s",
            (datetime.fromtimestamp(cutoff, tz=timezone.utc),),
        )
        return [r["camera_id"] for r in cur.fetchall()]


class RetentionService:
    """
    Удаление записей старше recordings_days и загрузок старше uploads_days (0 — хранить всегда).

    Проверка идёт раз в interval_s, первая — через first_delay_s после старта веб-сервера.
    """

    def __init__(self, recordings_days: float, uploads_days: float, interval_s: float = 3600.0,
                 first_delay_s: float = 60.0,
                 find_recordings: Callable[[float], List[str]] = recordings_older_than,
                 delete_recording: Callable[[str], int] = delete_recording_data,
                 delete_uploads: Optional[Callable[[float], List[str]]] = None):
        self.recordings_days  = recordings_days
        self.uploads_days     = uploads_days
        self.interval_s       = interval_s
        self.first_delay_s    = first_delay_s
        self._find_recordings = find_recordings
        self._delete_record   = delete_recording
        self._delete_uploads  = delete_uploads
        self._stop            = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return self.recordings_days > 0 or self.uploads_days > 0

    def run_once(self, now: Optional[float] = None) -> Dict[str, List[str]]:
        """
        Одна проверка.

        Args:
            now (float, optional): Текущее время. Defaults to None.

        Returns:
            Dict[str, List[str]]: Удалённые записи (recordings) и загрузки (uploads).
        """
        now = time.time() if now is None else now
        out: Dict[str, List[str]] = {"recordings": [], "uploads": []}
        if self.recordings_days > 0:
            for session_id in self._find_recordings(now - self.recordings_days * DAY_S):
                try:
                    self._delete_record(session_id)
                    out["recordings"].append(session_id)
                except Exception as e:
                    _log.warning(f"Recording {session_id} not deleted: {e}")
        if self.uploads_days > 0:
            if self._delete_uploads is None:
                from ..routes.uploads import delete_uploads_older_than
                self._delete_uploads = delete_uploads_older_than
            out["uploads"] = self._delete_uploads(now - self.uploads_days * DAY_S)
        if out["recordings"] or out["uploads"]:
            _log.info(f"Retention removed {len(out['recordings'])} recordings and {len(out['uploads'])} uploads")
        return out

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        _log.info(f"Retention: recordings {self.recordings_days or '∞'} days, uploads {self.uploads_days or '∞'} days")
        self._thread = threading.Thread(target=self._run, name="retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if self._stop.wait(self.first_delay_s):
            return
        while True:
            try:
                self.run_once()
            except Exception:
                _log.exception("Retention check failed")
            if self._stop.wait(self.interval_s):
                return
