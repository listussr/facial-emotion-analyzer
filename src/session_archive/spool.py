"""Спул сессии на диск: JPEG-кадры, PCM звука и слитая аналитика до рендера итоговой записи."""

import json
import shutil
import struct
import time

from pathlib import Path
from typing import Any
from typing import Dict
from typing import Iterator
from typing import Optional
from typing import Tuple

_FRAME_HEADER = struct.Struct(">qI")

ANALYTICS_FACE_FIELDS = ("track_id", "face_id", "bbox", "emotion", "emotion_scores", "face_emotion",
                         "face_scores", "voice_emotion", "voice_scores", "fusion_used", "predicted")


def compact_analytics(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Поля слитой аналитики, нужные для рендера и итогового JSON.

    Args:
        message (Dict[str, Any]): Сообщение fused-analytics.

    Returns:
        Dict[str, Any]: Сокращённая запись кадра.
    """
    faces = [{k: f.get(k) for k in ANALYTICS_FACE_FIELDS} for f in message.get("faces") or []]
    voice = message.get("scene_voice")
    return {
        "pts_ms": message.get("pts_ms"),
        "faces": faces,
        "voice": {"label": voice.get("label"), "scores": voice.get("scores")} if voice else None,
    }


class SessionSpool:
    """
    Файлы одной сессии в каталоге спула.

    Звук пишется сплошным PCM от первого чанка: разрывы больше чанка заполняются тишиной,
    поэтому позиция в файле однозначно задаёт время. После max_duration_s медиа не пишется.
    """

    def __init__(self, root: Path, camera_id: str, max_duration_s: float = 1800.0):
        self.camera_id      = camera_id
        self.dir            = root / camera_id
        self.max_duration_s = max_duration_s
        self.dir.mkdir(parents=True, exist_ok=True)

        self.created_at   = time.time()
        self.last_seen    = self.created_at
        self.ended_at: Optional[float] = None
        self.end_event: Optional[Dict[str, Any]] = None
        self.start_event: Optional[Dict[str, Any]] = None
        self.truncated    = False

        self.frames          = 0
        self.first_video_pts: Optional[int] = None
        self.last_video_pts: Optional[int] = None
        self.last_analytics_pts: Optional[int] = None

        self.audio_rate: Optional[int] = None
        self.audio_channels: Optional[int] = None
        self.first_audio_pts: Optional[int] = None
        self._audio_samples = 0

        self._video     = open(self.dir / "video.bin", "ab")
        self._audio     = open(self.dir / "audio.pcm", "ab")
        self._analytics = open(self.dir / "analytics.jsonl", "a", encoding="utf-8")

    def _over_limit(self, pts_ms: int) -> bool:
        start = min(p for p in (self.first_video_pts, self.first_audio_pts, pts_ms) if p is not None)
        if pts_ms - start > self.max_duration_s * 1000:
            self.truncated = True
            return True
        return False

    def add_frame(self, pts_ms: int, jpeg: bytes) -> None:
        self.last_seen = time.time()
        if self._over_limit(pts_ms) or (self.last_video_pts is not None and pts_ms <= self.last_video_pts):
            return
        self._video.write(_FRAME_HEADER.pack(pts_ms, len(jpeg)))
        self._video.write(jpeg)
        if self.first_video_pts is None:
            self.first_video_pts = pts_ms
        self.last_video_pts = pts_ms
        self.frames        += 1

    def add_audio(self, pts_ms: int, pcm: bytes, sample_rate: int, channels: int) -> None:
        self.last_seen = time.time()
        if self._over_limit(pts_ms):
            return
        if self.audio_rate is None:
            self.audio_rate, self.audio_channels, self.first_audio_pts = sample_rate, channels, pts_ms
        if sample_rate != self.audio_rate or channels != self.audio_channels:
            return
        frame_bytes = 2 * channels
        expected    = self.first_audio_pts + self._audio_samples * 1000 // sample_rate
        chunk       = len(pcm) // frame_bytes
        gap         = (pts_ms - expected) * sample_rate // 1000
        if gap > chunk:
            self._audio.write(b"\x00" * gap * frame_bytes)
            self._audio_samples += gap
        self._audio.write(pcm)
        self._audio_samples += chunk

    def add_analytics(self, message: Dict[str, Any]) -> None:
        self.last_seen = time.time()
        record         = compact_analytics(message)
        self._analytics.write(json.dumps(record, ensure_ascii=False) + "\n")
        pts = record["pts_ms"]
        if pts is not None and (self.last_analytics_pts is None or pts > self.last_analytics_pts):
            self.last_analytics_pts = pts

    def end(self, event: Dict[str, Any]) -> None:
        self.ended_at  = time.time()
        self.end_event = event

    def analytics_caught_up(self) -> bool:
        """Слитая аналитика дошла до последнего кадра: дальше её ждать незачем."""
        if self.last_video_pts is None:
            return True
        return self.last_analytics_pts is not None and self.last_analytics_pts >= self.last_video_pts

    def close_files(self) -> None:
        for f in (self._video, self._audio, self._analytics):
            if not f.closed:
                f.close()

    def remove(self) -> None:
        self.close_files()
        shutil.rmtree(self.dir, ignore_errors=True)

    def iter_frames(self) -> Iterator[Tuple[int, bytes]]:
        with open(self.dir / "video.bin", "rb") as f:
            while True:
                header = f.read(_FRAME_HEADER.size)
                if len(header) < _FRAME_HEADER.size:
                    return
                pts_ms, size = _FRAME_HEADER.unpack(header)
                data         = f.read(size)
                if len(data) < size:
                    return
                yield pts_ms, data

    def iter_analytics(self) -> Iterator[Dict[str, Any]]:
        with open(self.dir / "analytics.jsonl", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    @property
    def audio_path(self) -> Path:
        return self.dir / "audio.pcm"

    @property
    def audio_samples(self) -> int:
        return self._audio_samples
