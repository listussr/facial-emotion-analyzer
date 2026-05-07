from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from confluent_kafka import Consumer

from ..config import settings


class EventsDispatcher:
    def __init__(self) -> None:
        self._log = logging.getLogger("EventsDispatcher")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="EventsDispatcher", daemon=True
        )
        self._thread.start()
        self._log.info("Events dispatcher started")

    async def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        for queues in self._subscribers.values():
            for q in queues:
                self._loop and self._loop.call_soon_threadsafe(q.put_nowait, None)
        self._log.info("Events dispatcher stopped")

    def subscribe(self, camera_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subscribers[camera_id].add(q)
        return q

    def unsubscribe(self, camera_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(camera_id, set()).discard(queue)
        if camera_id in self._subscribers and not self._subscribers[camera_id]:
            del self._subscribers[camera_id]

    def _run(self) -> None:
        consumer = Consumer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP,
            "group.id": f"webserver-events-{id(self)}",
            "auto.offset.reset": "latest",
            "fetch.message.max.bytes": 10 * 1024 * 1024,
        })
        consumer.subscribe([settings.TOPIC_ANALYTICS])
        self._log.info(f"Subscribed to {settings.TOPIC_ANALYTICS}")

        try:
            while not self._stop.is_set():
                msg = consumer.poll(0.2)
                if msg is None or msg.error():
                    continue
                try:
                    payload: Dict[str, Any] = json.loads(msg.value().decode("utf-8"))
                    cam_id = payload.get("camera_id")
                    if not cam_id:
                        continue
                except Exception as e:
                    self._log.warning(f"Bad analytics msg: {e}")
                    continue

                queues = self._subscribers.get(cam_id)
                if not queues or self._loop is None:
                    continue
                for q in list(queues):
                    self._loop.call_soon_threadsafe(self._safe_put, q, payload)

        finally:
            consumer.close()

    @staticmethod
    def _safe_put(queue: asyncio.Queue, item: Any) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(item)


events_dispatcher = EventsDispatcher()
