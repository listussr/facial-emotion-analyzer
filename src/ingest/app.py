"""HTTP-часть сервиса приёма: камера и микрофон браузера по WebSocket, проверка живости."""

import logging
import threading

from contextlib import asynccontextmanager
from typing import Any
from typing import Callable
from typing import Optional

from fastapi import FastAPI
from fastapi import WebSocket
from starlette.concurrency import run_in_threadpool

from src.video_producing.sources import browser_source
from .service import IngestService

log = logging.getLogger("capture")

KIND_VIDEO   = 1
KIND_AUDIO   = 2
HEADER       = 5
RELAY_HEADER = "x-affectra-relay"
OWNER_WAIT_S = 3.0


def _default_connect(url: str, relay_from: str):
    import websockets

    return websockets.connect(url, max_size=None, additional_headers={RELAY_HEADER: relay_from})


def create_app(service: IngestService, run_loop: bool = True, status_store=None,
               connect: Callable[[str, str], Any] = _default_connect) -> FastAPI:
    """
    Приложение узла приёма.

    WebSocket сессии с другого узла пересылается узлу-владельцу из session-status: при нескольких узлах за одним
    адресом браузер может попасть на любой.

    Args:
        service      (IngestService)     : Сервис продюсеров.
        run_loop     (bool, optional)     : Запускать цикл команд в фоновом потоке. Defaults to True.
        status_store (optional)           : SessionStatusStore для поиска владельца; None — без пересылки.
        connect      (Callable, optional) : Клиент WebSocket к узлу-владельцу (url, ingest_id отправителя).

    Returns:
        FastAPI: Приложение.
    """
    connected: set = set()
    loop_thread: Optional[threading.Thread] = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal loop_thread
        if run_loop:
            loop_thread = threading.Thread(target=service.process, name="ingest-loop", daemon=True)
            loop_thread.start()
        try:
            yield
        finally:
            await run_in_threadpool(service.close)
            if status_store is not None:
                status_store.stop()

    app = FastAPI(title="Affectra ingest", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "ingest_id": service.ingest_id, "address": service.address}

    def owner_of(session_id: str) -> Optional[str]:
        if status_store is None:
            return None
        status = status_store.wait_for(session_id, OWNER_WAIT_S, predicate=lambda s: s.active)
        if status is None or not status.active or status.ingest_id == service.ingest_id:
            return None
        return status.ingest_address or None

    async def relay(websocket: WebSocket, session_id: str, address: str) -> None:
        await websocket.accept()
        sent = 0
        try:
            async with connect(f"ws://{address}/api/capture/{session_id}", service.ingest_id) as upstream:
                log.info(f"[{session_id}] browser relayed to {address}")
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if msg.get("bytes"):
                        await upstream.send(msg["bytes"])
                        sent += 1
        except Exception as e:
            log.warning(f"[{session_id}] relay to {address} closed: {e}")
            try:
                await websocket.close(code=1011, reason="Capture relay closed")
            except Exception:
                pass
        finally:
            log.info(f"[{session_id}] relay closed: {sent} messages")

    @app.websocket("/api/capture/{session_id}")
    async def capture_ws(websocket: WebSocket, session_id: str):
        """
        Медиа браузера: [uint8 тип][uint32 BE pts_ms][данные]; тип 1 — JPEG кадра, 2 — PCM s16le 16 кГц моно.

        Отключение браузера завершает сессию, повторное подключение к той же сессии не принимается.
        """
        source = browser_source.get(session_id)
        if source is None and RELAY_HEADER not in websocket.headers:
            address = await run_in_threadpool(owner_of, session_id)
            if address is not None:
                await relay(websocket, session_id, address)
                return
        if source is None or session_id in connected:
            await websocket.close(code=1008, reason="Browser session not found or already connected")
            return

        await websocket.accept()
        connected.add(session_id)
        frames = chunks = 0
        log.info(f"[{session_id}] browser connected"
                 + (f" via {websocket.headers[RELAY_HEADER]}" if RELAY_HEADER in websocket.headers else ""))
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if not data or len(data) <= HEADER:
                    continue
                pts = int.from_bytes(data[1:HEADER], "big")
                if data[0] == KIND_VIDEO:
                    frames += source.push_video(pts, data[HEADER:])
                elif data[0] == KIND_AUDIO:
                    chunks += await run_in_threadpool(source.push_audio, pts, data[HEADER:])
        except Exception as e:
            log.warning(f"[{session_id}] browser capture failed: {e}")
        finally:
            connected.discard(session_id)
            log.info(f"[{session_id}] browser disconnected: {frames} frames, {chunks} audio chunks, "
                     f"dropped video={source.dropped_video} audio={source.dropped_audio}")
            service.stop(session_id)

    return app
