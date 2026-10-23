"""Приём камеры и микрофона браузера по WebSocket в источник сессии."""

import logging

from fastapi import APIRouter
from fastapi import WebSocket
from starlette.concurrency import run_in_threadpool

from src.video_producing.sources import browser_source
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/capture", tags=["capture"])
log    = logging.getLogger("capture")

KIND_VIDEO = 1
KIND_AUDIO = 2
HEADER     = 5

_connected: set = set()


@router.websocket("/{session_id}")
async def capture_ws(websocket: WebSocket, session_id: str):
    """
    Медиа браузера: [uint8 тип][uint32 BE pts_ms][данные]; тип 1 — JPEG кадра, 2 — PCM s16le 16 кГц моно.

    Отключение браузера завершает сессию, повторное подключение к той же сессии не принимается.
    """
    source = browser_source.get(session_id)
    if source is None or session_id in _connected:
        await websocket.close(code=1008, reason="Browser session not found or already connected")
        return

    await websocket.accept()
    _connected.add(session_id)
    frames = chunks = 0
    log.info(f"[{session_id}] browser connected")
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
        _connected.discard(session_id)
        log.info(f"[{session_id}] browser disconnected: {frames} frames, {chunks} audio chunks, "
                 f"dropped video={source.dropped_video} audio={source.dropped_audio}")
        producer = session_manager.detach(session_id)
        if producer is not None:
            await run_in_threadpool(session_manager.shutdown_producer, producer, session_id)
