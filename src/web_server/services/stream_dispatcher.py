"""Раздача кадров из Kafka подписчикам по camera_id: размеченных для MJPEG и исходных для браузера."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
import msgpack

from collections import defaultdict
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Set

from confluent_kafka import Consumer

from ..config import settings


def annotated_jpeg(payload: Dict[str, Any]) -> Optional[bytes]:
    return payload.get("annotated_frame")


def live_frame(payload: Dict[str, Any]) -> Optional[bytes]:
    """
    Кадр raw-video-frames для браузера: [uint32 BE длина заголовка][JSON-заголовок][JPEG].

    Args:
        payload (Dict[str, Any]): Сообщение VideoMessage.

    Returns:
        Optional[bytes]: Бинарный кадр или None без JPEG.
    """
    jpeg = payload.get("frame_data")
    if not jpeg:
        return None
    header = json.dumps({
        "pts_ms": payload.get("pts_ms"),
        "seq": payload.get("seq"),
        "frame_id": payload.get("frame_id"),
        "capture_wall_ts": payload.get("capture_wall_ts") or payload.get("timestamp"),
        "width": payload.get("processed_width"),
        "height": payload.get("processed_height"),
    }).encode("utf-8")
    return struct.pack(">I", len(header)) + header + jpeg


class StreamDispatcher:
    """
    Кадры топика -> очереди подписчиков по camera_id.

    extract превращает сообщение в байты для подписчика; при переполнении очереди выбрасывается старый кадр,
    кадры камер без подписчиков не распаковываются.
    """

    def __init__(self, topic: str, extract: Callable[[Dict[str, Any]], Optional[bytes]],
                 name: str = "StreamDispatcher", queue_size: int = 4) -> None:
        self._topic      = topic
        self._extract    = extract
        self._name       = name
        self._queue_size = queue_size
        self._log        = logging.getLogger(name)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True
        )
        self._thread.start()
        self._log.info(f"{self._name} started")

    async def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        for queues in self._subscribers.values():
            for q in queues:
                self._loop and self._loop.call_soon_threadsafe(q.put_nowait, None)
        self._log.info(f"{self._name} stopped")

    def subscribe(self, camera_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[camera_id].add(q)
        return q

    def unsubscribe(self, camera_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(camera_id, set()).discard(queue)
        if camera_id in self._subscribers and not self._subscribers[camera_id]:
            del self._subscribers[camera_id]

    def _run(self) -> None:
        consumer = Consumer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP,
            "group.id": f"webserver-{self._name.lower()}-{id(self)}",
            "auto.offset.reset": "latest",
            "fetch.message.max.bytes": 10 * 1024 * 1024,
        })
        consumer.subscribe([self._topic])
        self._log.info(f"Subscribed to {self._topic}")

        try:
            while not self._stop.is_set():
                msg = consumer.poll(0.2)
                if msg is None or msg.error():
                    continue
                cam_id = msg.key().decode("utf-8", "replace") if msg.key() else None
                if cam_id is not None and cam_id not in self._subscribers:
                    continue
                try:
                    payload = msgpack.unpackb(msg.value(), raw=False)
                    cam_id  = payload.get("camera_id") or cam_id
                    data    = self._extract(payload)
                    if not cam_id or not data:
                        continue
                except Exception as e:
                    self._log.warning(f"Bad message in {self._topic}: {e}")
                    continue

                queues = self._subscribers.get(cam_id)
                if not queues or self._loop is None:
                    continue
                for q in list(queues):
                    self._loop.call_soon_threadsafe(self._safe_put, q, data)
        finally:
            consumer.close()

    @staticmethod
    def _safe_put(queue: asyncio.Queue, item: bytes) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(item)


stream_dispatcher = StreamDispatcher(settings.TOPIC_ANNOTATED, annotated_jpeg, name="StreamDispatcher")
live_dispatcher   = StreamDispatcher(settings.TOPIC_RAW, live_frame, name="LiveDispatcher", queue_size=8)
