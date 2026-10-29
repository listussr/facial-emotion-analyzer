"""Сервис архивации: сырые кадры, звук и слитая аналитика сессии -> итоговая запись в S3."""

import json
import logging
import tempfile
import threading
import time
import msgpack

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from .render import build_analytics_document
from .render import render_recording
from .series import build_series_from_document
from .spool import SessionSpool
from .storage import recording_keys

_log = logging.getLogger("archive")


def _utc(ts: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


class ArchiveService:
    """
    Архивация сессий.

    Медиа складывается в спул без перекодирования. После stream_end запись ждёт, пока слитая аналитика
    дойдёт до последнего кадра (не дольше analytics_grace_s), затем рендерится в одном фоновом потоке,
    загружается в хранилище, а статус пишется в session_recordings. Сессия без сообщений дольше
    idle_timeout_s завершается так же — на случай упавшего продюсера.
    """

    def __init__(self, spool_root: Path, storage, db_factory: Optional[Callable[[], Any]] = None,
                 kafka_settings: Optional[Dict[str, Any]] = None, analytics_grace_s: float = 15.0,
                 idle_timeout_s: float = 120.0, max_duration_s: float = 1800.0, render_threads: int = 2):
        self.spool_root        = Path(spool_root)
        self.storage           = storage
        self.analytics_grace_s = analytics_grace_s
        self.idle_timeout_s    = idle_timeout_s
        self.max_duration_s    = max_duration_s
        self.render_threads    = render_threads
        self.spool_root.mkdir(parents=True, exist_ok=True)

        self._spools: Dict[str, SessionSpool] = {}
        self._finished: Dict[str, float] = {}
        self._executor   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-render")
        self._db_factory = db_factory
        self._db         = None
        self._db_lock    = threading.Lock()

        self._consumer = None
        self._topics: Dict[str, str] = {}
        if kafka_settings is not None:
            from confluent_kafka import Consumer

            self._topics = {k: kafka_settings[k] for k in ("video_topic", "audio_topic", "analytics_topic", "events_topic")}
            self._consumer = Consumer({
                "bootstrap.servers": kafka_settings["bootstrap_servers"],
                "group.id": kafka_settings.get("group_id", "archiver-v1"),
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
                "fetch.message.max.bytes": 10 * 1024 * 1024,
            })
            self._consumer.subscribe(list(self._topics.values()))

    def _record(self, camera_id: str, **fields: Any) -> None:
        if self._db_factory is None:
            return
        with self._db_lock:
            for attempt in range(2):
                try:
                    if self._db is None:
                        self._db = self._db_factory()
                    self._db.upsert_recording(camera_id, **fields)
                    return
                except Exception as e:
                    _log.warning(f"[{camera_id}] recording status not saved ({e}), attempt {attempt + 1}")
                    try:
                        if self._db is not None:
                            self._db.close()
                    except Exception:
                        pass
                    self._db = None

    def _spool(self, camera_id: str) -> Optional[SessionSpool]:
        if camera_id in self._finished:
            return None
        spool = self._spools.get(camera_id)
        if spool is None:
            spool                     = SessionSpool(self.spool_root, camera_id, self.max_duration_s)
            self._spools[camera_id]   = spool
            _log.info(f"[{camera_id}] recording started")
            self._record(camera_id, status="recording", started_at=_utc(spool.created_at))
        return spool

    def handle_video(self, message: Dict[str, Any]) -> None:
        spool = self._spool(message["camera_id"])
        if spool is not None:
            spool.add_frame(int(message["pts_ms"]), message["frame_data"])

    def handle_audio(self, message: Dict[str, Any]) -> None:
        spool = self._spool(message["camera_id"])
        if spool is not None:
            spool.add_audio(int(message["pts_ms"]), message["audio_data"], int(message["sample_rate"]),
                            int(message["channels"]))

    def handle_analytics(self, message: Dict[str, Any]) -> None:
        spool = self._spools.get(message.get("camera_id"))
        if spool is not None:
            spool.add_analytics(message)

    def handle_event(self, event: Dict[str, Any]) -> None:
        camera_id = event.get("camera_id")
        if event.get("event") == "stream_start":
            spool = self._spool(camera_id)
            if spool is not None:
                spool.start_event = event
        elif event.get("event") == "stream_end" and camera_id in self._spools:
            self._spools[camera_id].end(event)
            _log.info(f"[{camera_id}] stream_end ({event.get('reason')}), waiting for analytics")

    def due(self, now: Optional[float] = None) -> List[str]:
        """
        Сессии, готовые к рендеру.

        Args:
            now (float, optional): Текущее время. Defaults to None.

        Returns:
            List[str]: Идентификаторы сессий.
        """
        now = time.time() if now is None else now
        out = []
        for camera_id, s in self._spools.items():
            if s.ended_at is not None and (s.analytics_caught_up() or now - s.ended_at > self.analytics_grace_s):
                out.append(camera_id)
            elif s.ended_at is None and now - s.last_seen > self.idle_timeout_s:
                out.append(camera_id)
        return out

    def sweep(self, now: Optional[float] = None) -> List[str]:
        now  = time.time() if now is None else now
        done = self.due(now)
        for camera_id in done:
            spool                     = self._spools.pop(camera_id)
            self._finished[camera_id] = now
            spool.close_files()
            self._executor.submit(self.finalize, spool)
        for camera_id in [c for c, t in self._finished.items() if now - t > 600]:
            del self._finished[camera_id]
        return done

    def finalize(self, spool: SessionSpool) -> None:
        """
        Рендер, загрузка в хранилище и итоговый статус; спул удаляется в любом случае.

        Args:
            spool (SessionSpool): Закрытый спул сессии.
        """
        camera_id = spool.camera_id
        keys      = recording_keys(camera_id)
        self._record(camera_id, status="rendering", finished_at=_utc(spool.ended_at or spool.last_seen))
        try:
            with tempfile.TemporaryDirectory(dir=self.spool_root) as tmp:
                video_path  = Path(tmp) / "recording.mp4"
                result      = render_recording(spool, video_path, threads=self.render_threads)
                document    = build_analytics_document(spool, result, session=spool.start_event)
                json_path   = Path(tmp) / "analytics.json"
                json_path.write_text(json.dumps(document, ensure_ascii=False, default=str), encoding="utf-8")
                series_path = Path(tmp) / "series.json"
                series_path.write_text(json.dumps(build_series_from_document(document), ensure_ascii=False),
                                       encoding="utf-8")

                self.storage.upload_file(video_path, keys["video"], "video/mp4")
                self.storage.upload_file(json_path, keys["analytics"], "application/json")
                self.storage.upload_file(series_path, keys["series"], "application/json")
            self._record(camera_id, status="ready", video_key=keys["video"], analytics_key=keys["analytics"],
                         frames=result.frames, duration_sec=result.duration_sec, size_bytes=result.size_bytes,
                         has_audio=result.has_audio, truncated=spool.truncated, summary=document["summary"],
                         error=None)
            _log.info(f"[{camera_id}] recording ready: {keys['video']}")
        except Exception as e:
            _log.exception(f"[{camera_id}] recording failed")
            self._record(camera_id, status="failed", error=str(e)[:500])
        finally:
            spool.remove()

    def process(self, sweep_every_s: float = 1.0) -> None:
        if self._consumer is None:
            raise RuntimeError("ArchiveService created without Kafka settings")
        _log.info(f"Archive service started: topics={self._topics}, spool={self.spool_root}")
        handlers = {
            self._topics["video_topic"]: lambda raw: self.handle_video(msgpack.unpackb(raw, raw=False)),
            self._topics["audio_topic"]: lambda raw: self.handle_audio(msgpack.unpackb(raw, raw=False)),
            self._topics["analytics_topic"]: lambda raw: self.handle_analytics(json.loads(raw)),
            self._topics["events_topic"]: lambda raw: self.handle_event(msgpack.unpackb(raw, raw=False)),
        }
        last_sweep = time.time()
        try:
            while True:
                for msg in self._consumer.consume(num_messages=200, timeout=0.5):
                    if msg.error():
                        _log.warning(f"Kafka error: {msg.error()}")
                        continue
                    try:
                        handlers[msg.topic()](msg.value())
                    except Exception:
                        _log.exception(f"Archive failed on message from {msg.topic()}")
                now = time.time()
                if now - last_sweep >= sweep_every_s:
                    self.sweep(now)
                    last_sweep = now
        except KeyboardInterrupt:
            _log.info("Archive service interrupted")
        finally:
            self.close()

    def close(self) -> None:
        for spool in self._spools.values():
            spool.close_files()
        self._executor.shutdown(wait=True)
        if self._consumer is not None:
            self._consumer.close()
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass
