"""
Запись результатов слияния в БД.

Итоговые эмоции трека идут в emotion_timeseries (modality fused) только для лиц с UUID, голос сессии —
в session_audio_timeseries. Запись — в одном фоновом потоке со своим соединением: psycopg2-соединение
не делится между потоками, а обработка кадров не ждёт БД.
"""

import logging
import time
import uuid

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET

_log = logging.getLogger("fusion.recorder")


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


@dataclass
class _Track:
    face_id: str
    started_at: float
    last_seen: float
    samples: List[Dict[str, Any]] = field(default_factory=list)
    fusion_seq: Any = None


@dataclass
class _Voice:
    started_at: float
    windows: List[Dict[str, Any]] = field(default_factory=list)


class FusionRecorder:
    """
    Накопление и запись слитых таймсерий.

    Трек пишется, пропав дольше track_timeout_s; голос сессии — при освобождении камеры.
    Без handler_factory ничего не пишется, записи копятся в sink — для тестов.
    """

    def __init__(self, handler_factory: Optional[Callable[[], Any]], track_timeout_s: float = 3.0):
        self.track_timeout_s = track_timeout_s
        self._factory        = handler_factory
        self._handler        = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fusion-db") \
            if handler_factory is not None else None
        self._tracks: Dict[Tuple[str, int], _Track] = {}
        self._voice: Dict[str, _Voice] = {}
        self.written_tracks       = 0
        self.written_voice        = 0
        self.skipped_unidentified = 0
        self.sink: List[Tuple[str, Dict[str, Any]]] = []

    def record_frame(self, fused: Dict[str, Any], now: Optional[float] = None) -> None:
        """
        Отсчёты треков кадра; итог меняется раз в окно, поэтому отсчёт пишется только на новое слияние.

        Args:
            fused (Dict[str, Any])  : Слитый результат кадра.
            now   (float, optional) : Текущее время. Defaults to None.
        """
        now       = time.time() if now is None else now
        camera_id = fused["camera_id"]
        t         = fused.get("capture_wall_ts") or now
        for f in fused.get("faces") or []:
            if not f.get("emotion") or not f.get("emotion_scores"):
                continue
            key = (camera_id, f.get("track_id"))
            tr  = self._tracks.get(key)
            if tr is None:
                tr = self._tracks[key] = _Track(face_id=f.get("face_id"), started_at=t, last_seen=now)
            tr.face_id   = f.get("face_id") or tr.face_id
            tr.last_seen = now
            seq          = f.get("fusion_seq")
            if seq is not None and seq == tr.fusion_seq:
                continue
            tr.fusion_seq = seq
            tr.samples.append({
                "t": t, "pts_ms": fused.get("pts_ms"),
                "label": f["emotion"], "scores": f["emotion_scores"],
                "face_label": f.get("face_emotion"), "voice_label": f.get("voice_emotion"),
                "fusion_used": f.get("fusion_used"),
            })

    def record_voice(self, window: Dict[str, Any], now: Optional[float] = None) -> None:
        if not window.get("is_speech"):
            return
        now = time.time() if now is None else now
        v   = self._voice.setdefault(window["camera_id"], _Voice(started_at=window.get("capture_wall_ts") or now))
        v.windows.append({k: window.get(k) for k in (
            "window_seq", "pts_start_ms", "pts_end_ms", "speech_prob", "label", "scores",
            "smoothed_label", "smoothed_scores", "model")} | {"t": window.get("capture_wall_ts")})

    def flush_inactive(self, now: Optional[float] = None) -> int:
        """
        Запись треков, пропавших дольше track_timeout_s.

        Args:
            now (float, optional): Текущее время. Defaults to None.

        Returns:
            int: Число записанных треков.
        """
        now  = time.time() if now is None else now
        done = [k for k, tr in self._tracks.items() if now - tr.last_seen > self.track_timeout_s]
        for k in done:
            self._write_track(k, self._tracks.pop(k), now)
        return len(done)

    def flush_camera(self, camera_id: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for k in [k for k in self._tracks if k[0] == camera_id]:
            self._write_track(k, self._tracks.pop(k), now)
        voice = self._voice.pop(camera_id, None)
        if voice is not None and voice.windows:
            payload = {
                "camera_id": camera_id, "label_set": LABEL_SET, "labels": list(EMOTION_LABELS),
                "started_at": voice.started_at, "ended_at": now, "windows": voice.windows,
            }
            self._submit("insert_session_audio", camera_id, payload)
            self.written_voice += 1

    def close(self, now: Optional[float] = None) -> None:
        for camera_id in {k[0] for k in self._tracks} | set(self._voice):
            self.flush_camera(camera_id, now)
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            if self._handler is not None:
                try:
                    self._handler.close()
                except Exception:
                    pass

    def _write_track(self, key: Tuple[str, int], tr: _Track, now: float) -> None:
        if not tr.samples:
            return
        if not _is_uuid(tr.face_id):
            self.skipped_unidentified += 1
            return
        payload = {
            "camera_id": key[0], "track_id": key[1], "modality": "fused",
            "label_set": LABEL_SET, "labels": list(EMOTION_LABELS),
            "started_at": tr.started_at, "ended_at": now, "samples": tr.samples,
        }
        self._submit("insert_emotion_timeseries", tr.face_id, payload)
        self.written_tracks += 1

    def _submit(self, method: str, key: str, payload: Dict[str, Any]) -> None:
        self.sink.append((method, payload))
        if self._executor is not None:
            self._executor.submit(self._write, method, key, payload)

    def _write(self, method: str, key: str, payload: Dict[str, Any]) -> None:
        try:
            if self._handler is None:
                self._handler = self._factory()
            getattr(self._handler, method)(key, payload)
        except Exception:
            _log.exception(f"Failed to write {method} for {key}")
            self._handler = None
